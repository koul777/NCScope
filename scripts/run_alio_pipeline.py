"""Continuously collect JOB-ALIO documents and drive audited interviews.

One cycle is deliberately split into two subprocess boundaries:

1. collect/update public postings, attachments, parses, and exact NCS matches;
2. reconcile eligible runs, generate through NCScope, then ask Claude to review.

The worker keeps a single-instance lock, writes sanitized cycle summaries, and
enables the orchestrator's persisted retry backoff. It never stores provider
keys or raw provider responses in its operational journal.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(".local/alio_corpus")
DEFAULT_GENERATOR = "app.services.alio_live_adapters:generate_with_ncscope"
DEFAULT_REVIEWER = "app.services.alio_live_adapters:review_with_claude"
JOURNAL_NAME = "pipeline_runs.jsonl"
JOURNAL_MAX_BYTES = 10 * 1024 * 1024
JOURNAL_BACKUP_COUNT = 5
LOCK_SCHEMA_VERSION = "ncscope-alio-pipeline-lock.v2"
EVENT_SCHEMA_VERSION = "ncscope-alio-pipeline-event.v1"

_COLLECTOR_SUMMARY_KEYS = (
    "postings",
    "documents",
    "document_statuses",
    "parsed_kinds",
    "job_descriptions_with_detail_candidates",
    "ncs_match_statuses",
    "verified_document_ncs_units",
    "ncs_matching",
    "profile_training_documents",
    "profile_classes",
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "review_token",
    "secret",
    "token",
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)(api[_ -]?key|authorization|password|review[_ -]?token|secret)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _bounded_text(value: Any, maximum: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    for pattern in _SECRET_TEXT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    if len(text) <= maximum:
        return text
    marker = "...[truncated]"
    return text[: max(0, maximum - len(marker))] + marker


def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
    """Return a small, secret-resistant value suitable for operations logs."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _bounded_text(value)
    if depth >= 5:
        return "[depth-limited]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:60]:
            key = _bounded_text(raw_key, 80)
            normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                continue
            result[key] = _bounded_json_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:60]]
    return _bounded_text(type(value).__name__, 80)


def _compact_collector_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _bounded_json_value(summary.get(key))
        for key in _COLLECTOR_SUMMARY_KEYS
        if key in summary
    }


def _safe_reason(value: Any) -> str:
    """Keep an operational error code and safe retry attributes, never raw text."""

    parts = [part.strip() for part in str(value or "").split("|") if part.strip()]
    if not parts:
        return ""
    safe = [_bounded_text(parts[0], 160)]
    for part in parts[1:]:
        if re.fullmatch(r"(?i)(retryable=(?:true|false)|http_status=\d{3})", part):
            safe.append(part.casefold())
    return "|".join(safe)


def _load_dotenv(path: Path) -> None:
    """Load missing process variables without ever returning or logging them."""

    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or name in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name] = value


def _last_json_object(output: str) -> dict[str, Any]:
    lines = str(output or "").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].lstrip().startswith("{"):
            continue
        try:
            value = json.loads("\n".join(lines[index:]))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    elapsed_seconds: float
    summary: dict[str, Any]
    timed_out: bool = False


def _run_command(command: list[str], *, timeout_seconds: int) -> CommandResult:
    started = time.monotonic()
    child_env = dict(os.environ)
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, timeout_seconds),
            check=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            exit_code=124,
            elapsed_seconds=round(time.monotonic() - started, 3),
            summary={"ok": False, "error": "command_timeout"},
            timed_out=True,
        )
    return CommandResult(
        exit_code=int(completed.returncode),
        elapsed_seconds=round(time.monotonic() - started, 3),
        summary=_last_json_object(completed.stdout),
    )


def _collector_command(args: argparse.Namespace, *, today: date) -> list[str]:
    start = today - timedelta(days=max(0, int(args.lookback_days)))
    command = [
        sys.executable,
        str(ROOT / "scripts" / "collect_alio_documents.py"),
        "--start-date",
        start.isoformat(),
        "--end-date",
        today.isoformat(),
        "--window-days",
        str(max(1, int(args.window_days))),
        "--limit",
        str(max(0, int(args.collector_limit))),
        "--page-limit-per-window",
        str(max(1, int(args.page_limit_per_window))),
        "--delay",
        str(max(0.0, float(args.request_delay))),
        "--retry-errors",
        "--match-ncs",
        "--out-dir",
        str(args.out_dir.resolve()),
    ]
    if args.keep_files:
        command.append("--keep-files")
    return command


def _orchestrator_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_alio_interview_orchestrator.py"),
        "prepare" if args.prepare_only else "run-once",
        "--database",
        str((args.out_dir / "alio_corpus.sqlite3").resolve()),
        "--limit",
        str(max(1, int(args.orchestrator_limit))),
    ]
    if not args.prepare_only:
        command.extend(
            [
                "--generator",
                str(args.generator),
                "--reviewer",
                str(args.reviewer),
                "--respect-retry-backoff",
            ]
        )
    return command


def _cycle_status(
    collector: CommandResult,
    orchestrator: CommandResult | None,
) -> str:
    if collector.exit_code != 0 or orchestrator is None or orchestrator.exit_code != 0:
        return "failed"
    summary = orchestrator.summary
    if _bounded_text(summary.get("circuit_breaker"), 160):
        return "degraded"
    try:
        posting_count = int(summary.get("posting_count") or 0)
        processed_count = int(summary.get("processed_posting_count") or 0)
    except (TypeError, ValueError, OverflowError):
        return "degraded"
    if posting_count > processed_count:
        return "degraded"
    runs = summary.get("runs")
    if isinstance(runs, list) and any(
        isinstance(row, dict)
        and str(row.get("state") or "")
        in {"failed", "waiting_documents", "waiting_ncs_mcp", "needs_human_ncs_review"}
        for row in runs
    ):
        return "degraded"
    return "ok"


def _compact_orchestrator_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep operational evidence without duplicating the entire DB queue."""

    rows = summary.get("runs")
    runs = (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )
    actionable = [
        {
            key: (
                _safe_reason(row.get(key))
                if key == "reason"
                else _bounded_json_value(row.get(key))
            )
            for key in (
                "posting_id",
                "run_id",
                "state",
                "reason",
                "official_detail_code",
                "expected_unit_count",
                "verified_unit_count",
                "attempt_count",
                "review_attempt_count",
                "retry_status",
                "input_fingerprint",
                "generation_output_hash",
            )
        }
        for row in runs
        if str(row.get("state") or "") not in {"queued", "ready_automated"}
    ][:50]
    raw_errors = summary.get("errors")
    errors = raw_errors if isinstance(raw_errors, list) else []
    compact_errors = []
    for item in errors[:20]:
        if isinstance(item, dict):
            compact_errors.append(
                {
                    key: _bounded_text(item.get(key), 160)
                    for key in ("posting_id", "error")
                    if item.get(key) not in (None, "")
                }
            )
        else:
            compact_errors.append({"error": _bounded_text(type(item).__name__, 80)})
    compact = {
        key: _bounded_json_value(summary.get(key))
        for key in (
            "ok",
            "mode",
            "database",
            "posting_count",
            "processed_posting_count",
            "run_count",
            "generator_configured",
            "reviewer_configured",
            "state_counts",
        )
    }
    compact.update(
        {
            "circuit_breaker": _safe_reason(summary.get("circuit_breaker")),
            "errors": compact_errors,
            "omitted_error_count": max(0, len(errors) - len(compact_errors)),
            "retryable_failure_count": sum(
                1
                for row in runs
                if row.get("state") == "failed"
                and "retryable=true" in str(row.get("reason") or "")
            ),
            "actionable_runs": actionable,
            "omitted_run_count": max(0, len(runs) - len(actionable)),
        }
    )
    return compact


def _journal_path(out_dir: Path, generation: int = 0) -> Path:
    base = out_dir / JOURNAL_NAME
    return base if generation == 0 else out_dir / f"{JOURNAL_NAME}.{generation}"


def _rotate_journal(out_dir: Path) -> None:
    journal = _journal_path(out_dir)
    oldest = _journal_path(out_dir, JOURNAL_BACKUP_COUNT)
    if oldest.is_file():
        oldest.unlink()
    for generation in range(JOURNAL_BACKUP_COUNT - 1, 0, -1):
        source = _journal_path(out_dir, generation)
        if source.is_file():
            source.replace(_journal_path(out_dir, generation + 1))
    if journal.is_file():
        journal.replace(_journal_path(out_dir, 1))


def _append_journal_event(args: argparse.Namespace, event: dict[str, Any]) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    journal = _journal_path(args.out_dir)
    bounded = _bounded_json_value(event)
    encoded = (json.dumps(bounded, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if journal.is_file() and journal.stat().st_size + len(encoded) > JOURNAL_MAX_BYTES:
        _rotate_journal(args.out_dir)
    with journal.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _cycle_event(
    event: str,
    *,
    cycle_id: str,
    cycle_number: int,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event": event,
        "cycle_id": cycle_id,
        "cycle": cycle_number,
        "at": _utc_now(),
        **fields,
    }


def _journal_events(out_dir: Path) -> Iterator[dict[str, Any]]:
    paths = [
        _journal_path(out_dir, generation)
        for generation in range(JOURNAL_BACKUP_COUNT, 0, -1)
    ]
    paths.append(_journal_path(out_dir))
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    yield value


def _seal_interrupted_cycles(args: argparse.Namespace) -> int:
    open_cycles: dict[str, dict[str, Any]] = {}
    for event in _journal_events(args.out_dir):
        cycle_id = str(event.get("cycle_id") or "")
        event_name = str(event.get("event") or "")
        if not cycle_id:
            continue
        if event_name == "cycle_started":
            open_cycles[cycle_id] = event
        elif event_name in {"cycle_committed", "cycle_interrupted"}:
            open_cycles.pop(cycle_id, None)
    for cycle_id, started in open_cycles.items():
        _append_journal_event(
            args,
            _cycle_event(
                "cycle_interrupted",
                cycle_id=cycle_id,
                cycle_number=int(started.get("cycle") or 0),
                status="interrupted",
                reason="worker_restart_detected",
                started_at=_bounded_text(started.get("at"), 80),
            ),
        )
    return len(open_cycles)


def run_cycle(args: argparse.Namespace, cycle_number: int) -> dict[str, Any]:
    cycle_id = uuid.uuid4().hex
    started_at = _utc_now()
    _append_journal_event(
        args,
        _cycle_event(
            "cycle_started",
            cycle_id=cycle_id,
            cycle_number=cycle_number,
            started_at=started_at,
        ),
    )
    collector = _run_command(
        _collector_command(args, today=date.today()),
        timeout_seconds=int(args.collector_timeout_sec),
    )
    collector_summary = _compact_collector_summary(collector.summary)
    _append_journal_event(
        args,
        _cycle_event(
            "collector_finished",
            cycle_id=cycle_id,
            cycle_number=cycle_number,
            exit_code=collector.exit_code,
            elapsed_seconds=collector.elapsed_seconds,
            timed_out=collector.timed_out,
            summary=collector_summary,
        ),
    )
    database = args.out_dir / "alio_corpus.sqlite3"
    orchestrator = None
    if database.is_file():
        orchestrator = _run_command(
            _orchestrator_command(args),
            timeout_seconds=int(args.orchestrator_timeout_sec),
        )
    orchestrator_summary = (
        _compact_orchestrator_summary(orchestrator.summary) if orchestrator else None
    )
    _append_journal_event(
        args,
        _cycle_event(
            "orchestrator_finished",
            cycle_id=cycle_id,
            cycle_number=cycle_number,
            skipped=orchestrator is None,
            exit_code=None if orchestrator is None else orchestrator.exit_code,
            elapsed_seconds=None
            if orchestrator is None
            else orchestrator.elapsed_seconds,
            timed_out=None if orchestrator is None else orchestrator.timed_out,
            summary=orchestrator_summary,
        ),
    )
    status = _cycle_status(collector, orchestrator)
    return {
        "schema_version": "ncscope-alio-pipeline-cycle.v1",
        "cycle_id": cycle_id,
        "cycle": cycle_number,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "status": status,
        "collector": {
            "exit_code": collector.exit_code,
            "elapsed_seconds": collector.elapsed_seconds,
            "timed_out": collector.timed_out,
            "summary": collector_summary,
        },
        "orchestrator": None
        if orchestrator is None
        else {
            "exit_code": orchestrator.exit_code,
            "elapsed_seconds": orchestrator.elapsed_seconds,
            "timed_out": orchestrator.timed_out,
            "summary": orchestrator_summary,
        },
    }


def _write_cycle(args: argparse.Namespace, result: dict[str, Any]) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _append_journal_event(
        args,
        _cycle_event(
            "cycle_committed",
            cycle_id=str(result.get("cycle_id") or uuid.uuid4().hex),
            cycle_number=int(result.get("cycle") or 0),
            status=_bounded_text(result.get("status"), 40),
            result=result,
        ),
    )
    latest = args.out_dir / "pipeline_latest.json"
    temporary = args.out_dir / "pipeline_latest.json.tmp"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            _bounded_json_value(result),
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(latest)


class _WindowsFileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _process_start_identity(pid: int) -> tuple[bool | None, str]:
    """Return process liveness and a PID-reuse-resistant start identity."""

    if pid <= 0:
        return False, ""
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsFileTime),
            ctypes.POINTER(_WindowsFileTime),
            ctypes.POINTER(_WindowsFileTime),
            ctypes.POINTER(_WindowsFileTime),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, 0, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: the PID does not exist.
                return False, ""
            return None, ""
        try:
            created = _WindowsFileTime()
            exited = _WindowsFileTime()
            kernel = _WindowsFileTime()
            user = _WindowsFileTime()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None, ""
            ticks = (int(created.high) << 32) | int(created.low)
            return True, f"windows-filetime:{ticks}"
        finally:
            kernel32.CloseHandle(handle)

    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8")
        fields = stat[stat.rfind(")") + 2 :].split()
        start_ticks = fields[19]
        boot_id_path = Path("/proc/sys/kernel/random/boot_id")
        boot_id = boot_id_path.read_text(encoding="ascii").strip()
        return True, f"proc:{boot_id}:{start_ticks}"
    except FileNotFoundError:
        return False, ""
    except (IndexError, OSError, ValueError):
        return None, ""


def _read_lock_owner(owner_path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = owner_path.read_bytes()
        if not raw or len(raw) > 4096:
            raise ValueError
        owner = json.loads(raw.decode("utf-8"))
        pid = int(owner.get("pid"))
        identity = str(owner.get("process_start_identity") or "")
        instance_id = str(owner.get("instance_id") or "")
        parsed_uuid = uuid.UUID(instance_id)
        if (
            not isinstance(owner, dict)
            or owner.get("schema_version") != LOCK_SCHEMA_VERSION
            or pid <= 0
            or not identity
            or parsed_uuid.hex != instance_id.replace("-", "").casefold()
            or not str(owner.get("started_at") or "")
        ):
            raise ValueError
    except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as exc:
        raise RuntimeError("alio_pipeline_lock_owner_invalid") from exc
    return owner, raw


def _reclaim_stale_lock(out_dir: Path, lock_dir: Path) -> bool:
    reclaim_dir = out_dir / ".pipeline.lock.reclaiming"
    if reclaim_dir.exists():
        raise RuntimeError("alio_pipeline_lock_reclaim_in_progress")
    try:
        entries = list(lock_dir.iterdir())
    except FileNotFoundError:
        return True
    if len(entries) != 1 or entries[0].name != "owner.json" or not entries[0].is_file():
        raise RuntimeError("alio_pipeline_lock_owner_invalid")
    owner, raw = _read_lock_owner(entries[0])
    pid = int(owner["pid"])
    alive, current_identity = _process_start_identity(pid)
    if alive is None:
        raise RuntimeError("alio_pipeline_lock_owner_unverifiable")
    if alive and current_identity == owner["process_start_identity"]:
        raise RuntimeError("alio_pipeline_already_running")
    try:
        lock_dir.replace(reclaim_dir)
    except FileNotFoundError:
        return True
    except OSError as exc:
        code = (
            "alio_pipeline_lock_reclaim_in_progress"
            if reclaim_dir.exists()
            else "alio_pipeline_lock_reclaim_failed"
        )
        raise RuntimeError(code) from exc
    moved_owner = reclaim_dir / "owner.json"
    try:
        if moved_owner.read_bytes() != raw:
            raise RuntimeError("alio_pipeline_lock_owner_changed")
        moved_owner.unlink()
        reclaim_dir.rmdir()
    except OSError as exc:
        raise RuntimeError("alio_pipeline_lock_reclaim_failed") from exc
    return True


@contextmanager
def _single_instance_lock(out_dir: Path) -> Iterator[None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = out_dir / ".pipeline.lock"
    reclaim_dir = out_dir / ".pipeline.lock.reclaiming"
    if reclaim_dir.exists():
        raise RuntimeError("alio_pipeline_lock_reclaim_in_progress")
    for _attempt in range(3):
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            _reclaim_stale_lock(out_dir, lock_dir)
    else:
        raise RuntimeError("alio_pipeline_lock_acquisition_failed")
    alive, process_identity = _process_start_identity(os.getpid())
    if alive is not True or not process_identity:
        lock_dir.rmdir()
        raise RuntimeError("alio_pipeline_process_identity_unavailable")
    instance_id = uuid.uuid4().hex
    owner_payload = {
        "schema_version": LOCK_SCHEMA_VERSION,
        "pid": os.getpid(),
        "process_start_identity": process_identity,
        "instance_id": instance_id,
        "started_at": _utc_now(),
    }
    owner_path = lock_dir / "owner.json"
    try:
        with owner_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(owner_payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        if owner_path.is_file():
            try:
                current_owner, _raw = _read_lock_owner(owner_path)
            except RuntimeError:
                current_owner = {}
            if current_owner.get("instance_id") == instance_id:
                owner_path.unlink()
                lock_dir.rmdir()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("once", "loop"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--collector-limit", type=int, default=100)
    parser.add_argument("--page-limit-per-window", type=int, default=20)
    parser.add_argument("--request-delay", type=float, default=0.35)
    parser.add_argument("--keep-files", action="store_true")
    parser.add_argument("--orchestrator-limit", type=int, default=100)
    parser.add_argument("--generator", default=DEFAULT_GENERATOR)
    parser.add_argument("--reviewer", default=DEFAULT_REVIEWER)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Collect, match, and reconcile the queue without invoking model callbacks.",
    )
    parser.add_argument("--interval-sec", type=int, default=900)
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Loop limit for controlled runs; 0 means continue until interrupted.",
    )
    parser.add_argument("--collector-timeout-sec", type=int, default=3600)
    parser.add_argument("--orchestrator-timeout-sec", type=int, default=7200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out_dir = args.out_dir.resolve()
    _load_dotenv(ROOT / ".env")
    max_cycles = 1 if args.mode == "once" else max(0, int(args.max_cycles))
    try:
        with _single_instance_lock(args.out_dir):
            _seal_interrupted_cycles(args)
            cycle = 0
            last_status = "ok"
            while True:
                cycle += 1
                result = run_cycle(args, cycle)
                _write_cycle(args, result)
                last_status = str(result["status"])
                print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
                if max_cycles and cycle >= max_cycles:
                    break
                time.sleep(max(30, int(args.interval_sec)))
            return 0 if last_status in {"ok", "degraded"} else 1
    except RuntimeError as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True),
            flush=True,
        )
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"ok": True, "status": "interrupted"}), flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
