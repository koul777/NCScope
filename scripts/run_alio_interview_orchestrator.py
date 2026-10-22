"""Reconcile and drive ALIO interview-generation work from the corpus DB.

The command intentionally has no built-in placeholder question generator.
Model adapters are explicit ``module:function`` callbacks; without one,
eligible work remains queued and generated work remains awaiting Claude.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import alio_interview_orchestrator as orchestrator  # noqa: E402


DEFAULT_DATABASE = Path(".local/alio_corpus/alio_corpus.sqlite3")
DEFAULT_LIVE_GENERATOR = "app.services.alio_live_adapters:generate_with_ncscope"

_SHARED_INFRASTRUCTURE_FAILURE_PREFIXES = (
    "openrouter_",
    "openai_api_authentication_failed",
    "openai_http_",
    "openai_request_",
    "openai_network_",
    "ai_quality_review_network_failed",
    "ai_quality_review_http_",
    "ai_quality_review_timeout",
    "ai_quality_review_provider_failed",
    "ai_quality_review_key_unavailable",
    "claude_cli_unavailable",
    "claude_review_timeout",
    "claude_review_process_failed",
    "claude_review_cli_failed_exit_",
    "claude_ncs_mcp_stdio_unavailable",
    "ncscope_generation_official_ksa_fetch_failed",
    "ncscope_alio_generation_",
)


def _load_callable(spec: str, role: str) -> Callable[[dict[str, Any]], Any] | None:
    value = str(spec or "").strip()
    if not value:
        return None
    if ":" not in value:
        raise ValueError(f"{role} must use module:function syntax")
    module_name, attribute_path = value.split(":", 1)
    if not module_name or not attribute_path:
        raise ValueError(f"{role} must use module:function syntax")
    target: Any = importlib.import_module(module_name)
    for part in attribute_path.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{role} is not callable: {value}")
    return target


def _posting_ids(
    connection: sqlite3.Connection,
    requested: list[str],
    limit: int,
) -> list[str]:
    if requested:
        seen: set[str] = set()
        return [
            value
            for raw in requested
            if (value := str(raw or "").strip()) and not (value in seen or seen.add(value))
        ]
    rows = connection.execute(
        "SELECT posting_id FROM postings ORDER BY discovered_at DESC,posting_id DESC LIMIT ?",
        (max(1, limit),),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _summary_row(item: dict[str, Any]) -> dict[str, Any]:
    bundle = item.get("bundle") if isinstance(item.get("bundle"), dict) else {}
    match = bundle.get("ncs_match") if isinstance(bundle.get("ncs_match"), dict) else {}
    orchestration = (
        bundle.get("orchestration")
        if isinstance(bundle.get("orchestration"), dict)
        else {}
    )
    units = bundle.get("verified_units")
    retry_status = (
        orchestrator._unattended_retry_status(
            item,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        if str(item.get("state") or "") == "failed"
        or (
            str(item.get("state") or "") == "awaiting_claude"
            and str(item.get("failure_stage") or "").startswith("review_claimed:")
        )
        else ""
    )
    return {
        "posting_id": str(item.get("posting_id") or bundle.get("posting", {}).get("posting_id") or ""),
        "run_id": item.get("id"),
        "document_id": item.get("document_id"),
        "match_id": item.get("match_id"),
        "state": str(item.get("state") or ""),
        "reason": str(item.get("last_error") or orchestration.get("reason") or ""),
        "input_fingerprint": str(item.get("input_fingerprint") or ""),
        "generation_output_hash": str(item.get("generation_output_hash") or ""),
        "attempt_count": int(item.get("attempt_count") or 0),
        "review_attempt_count": int(item.get("review_attempt_count") or 0),
        "retry_status": retry_status,
        "official_detail_code": str(match.get("official_detail_code") or ""),
        "official_detail_name": str(match.get("official_detail_name") or ""),
        "expected_unit_count": int(match.get("expected_unit_count") or 0),
        "verified_unit_count": len(units) if isinstance(units, list) else 0,
    }


def _shared_failure_signature(result: dict[str, Any]) -> tuple[str, int, str]:
    """Return the stable fields that prove whether a failure changed this pass."""

    return (
        _shared_infrastructure_code(result.get("last_error")),
        int(result.get("attempt_count") or 0),
        str(result.get("updated_at") or ""),
    )


def _shared_failure_snapshot(
    connection: sqlite3.Connection,
    posting_id: str,
) -> dict[int, tuple[str, int, str]]:
    """Capture existing shared failures so parked rows do not reopen a batch."""

    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interview_runs'"
    ).fetchone()
    if table is None:
        return {}
    rows = connection.execute(
        """
        SELECT id,last_error,attempt_count,updated_at
        FROM interview_runs
        WHERE posting_id=? AND is_current=1 AND state='failed'
        """,
        (posting_id,),
    ).fetchall()
    snapshot: dict[int, tuple[str, int, str]] = {}
    for run_id, last_error, attempt_count, updated_at in rows:
        signature = _shared_failure_signature(
            {
                "last_error": last_error,
                "attempt_count": attempt_count,
                "updated_at": updated_at,
            }
        )
        if signature[0]:
            snapshot[int(run_id)] = signature
    return snapshot


def _shared_infrastructure_failure(
    results: list[dict[str, Any]],
    previous: dict[int, tuple[str, int, str]] | None = None,
) -> str:
    """Stop after one newly observed provider/MCP outage, never an old parked one."""

    for result in results:
        if str(result.get("state") or "") != "failed":
            continue
        signature = _shared_failure_signature(result)
        code = signature[0]
        if code:
            run_id = int(result.get("id") or 0)
            if previous is not None and previous.get(run_id) == signature:
                continue
            return code
    return ""


def _shared_infrastructure_code(error: Any) -> str:
    code = str(error or "").split("|", 1)[0].strip().casefold()
    return code if code.startswith(_SHARED_INFRASTRUCTURE_FAILURE_PREFIXES) else ""


def _shared_failure_preflight(
    connection: sqlite3.Connection,
    posting_ids: list[str],
) -> tuple[list[str], str]:
    """Apply shared-failure cooldown only inside the requested posting scope.

    Terminal parked/exhausted rows are left for audit but do not block other
    work. A transient shared outage still blocks the scoped batch while its
    cooldown is active; once due, that posting is tried first so at most one
    fresh outage is fanned out in a cycle.
    """

    if not posting_ids:
        return posting_ids, ""
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interview_runs'"
    ).fetchone()
    if table is None:
        return posting_ids, ""
    scoped_posting_ids = list(dict.fromkeys(posting_ids))
    placeholders = ",".join("?" for _ in scoped_posting_ids)
    rows = connection.execute(
        f"""
        SELECT posting_id,state,failure_stage,last_error,attempt_count,updated_at,bundle_json
        FROM interview_runs
        WHERE is_current=1 AND state='failed'
          AND posting_id IN ({placeholders})
        ORDER BY updated_at,id
        """,
        tuple(scoped_posting_ids),
    ).fetchall()
    now = orchestrator._utc_now()
    current_profile = orchestrator._generation_profile()
    due_postings: list[str] = []
    for (
        posting_id,
        state,
        failure_stage,
        last_error,
        attempt_count,
        updated_at,
        bundle_json,
    ) in rows:
        code = _shared_infrastructure_code(last_error)
        if not code:
            continue
        try:
            bundle = json.loads(str(bundle_json or "{}"))
        except (TypeError, ValueError):
            bundle = {}
        if not isinstance(bundle, dict) or bundle.get("generation_profile") != current_profile:
            continue
        run = {
            "state": state,
            "failure_stage": failure_stage,
            "last_error": last_error,
            "attempt_count": attempt_count,
            "updated_at": updated_at,
        }
        retry_status = orchestrator._unattended_retry_status(run, now)
        if retry_status in {"parked", "exhausted"}:
            continue
        if retry_status == "cooldown":
            return posting_ids, code
        if retry_status == "due":
            due_postings.append(str(posting_id))
    if not due_postings:
        return posting_ids, ""
    priority = list(dict.fromkeys(due_postings))
    priority_set = set(priority)
    return [*priority, *(item for item in posting_ids if item not in priority_set)], ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("dry-run", "prepare", "run-once"),
        help="Preview only, persist queue state, or drive each current run once.",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--posting-id",
        action="append",
        default=[],
        help="ALIO posting id; repeat to process more than one posting.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Newest posting limit when --posting-id is omitted (default: 100).",
    )
    parser.add_argument(
        "--generator",
        default="",
        metavar="MODULE:FUNCTION",
        help="Explicit generation callback used only by run-once.",
    )
    parser.add_argument(
        "--reviewer",
        default="",
        metavar="MODULE:FUNCTION",
        help="Explicit Claude review callback used only by run-once.",
    )
    parser.add_argument(
        "--respect-retry-backoff",
        action="store_true",
        help=(
            "For unattended run-once workers, retry only failures explicitly "
            "marked retryable after exponential backoff."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = args.database.resolve()
    if not database.is_file():
        print(
            json.dumps(
                {"ok": False, "error": "database_not_found", "database": str(database)},
                ensure_ascii=False,
            )
        )
        return 2
    if args.mode != "run-once" and (args.generator or args.reviewer):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "callbacks_are_only_valid_for_run_once",
                    "mode": args.mode,
                },
                ensure_ascii=False,
            )
        )
        return 2
    if (
        args.mode == "run-once"
        and args.generator == DEFAULT_LIVE_GENERATOR
        and orchestrator._generation_profile().get("provider") == "adapter_default"
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "generation_provider_not_configured",
                    "hint": "set INTERVIEW_GENERATION_PROVIDER before live generation",
                },
                ensure_ascii=False,
            )
        )
        return 2

    try:
        generator = _load_callable(args.generator, "generator")
        reviewer = _load_callable(args.reviewer, "reviewer")
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            posting_ids = _posting_ids(connection, args.posting_id, args.limit)
            preflight_circuit_breaker = ""
            if args.mode == "run-once" and args.respect_retry_backoff:
                posting_ids, preflight_circuit_breaker = _shared_failure_preflight(
                    connection,
                    posting_ids,
                )
            rows: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            processed_posting_count = 0
            circuit_breaker = preflight_circuit_breaker
            for posting_id in (posting_ids if not circuit_breaker else []):
                processed_posting_count += 1
                try:
                    if args.mode == "dry-run":
                        results = orchestrator.preview_posting(connection, posting_id)
                    elif args.mode == "prepare":
                        results = orchestrator.prepare_posting(connection, posting_id)
                    else:
                        orchestrator.prepare_posting(connection, posting_id)
                        previous_shared_failures = _shared_failure_snapshot(
                            connection,
                            posting_id,
                        )
                        results = orchestrator.drive_posting(
                            connection,
                            posting_id,
                            generator=generator,
                            reviewer=reviewer,
                            respect_retry_backoff=bool(args.respect_retry_backoff),
                            stop_predicate=(
                                lambda result: bool(
                                    _shared_infrastructure_failure(
                                        [result],
                                        previous_shared_failures,
                                    )
                                )
                                if args.respect_retry_backoff
                                else False
                            ),
                        )
                    rows.extend(_summary_row(result) for result in results)
                    if args.mode == "run-once":
                        circuit_breaker = _shared_infrastructure_failure(
                            results,
                            previous_shared_failures
                            if args.respect_retry_backoff
                            else None,
                        )
                        if circuit_breaker:
                            break
                except Exception as exc:
                    errors.append(
                        {
                            "posting_id": posting_id,
                            "error": type(exc).__name__,
                            "message": str(exc)[:1000],
                        }
                    )
        finally:
            connection.close()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc)[:1000],
                    "database": str(database),
                },
                ensure_ascii=False,
            )
        )
        return 1

    states = Counter(row["state"] for row in rows)
    print(
        json.dumps(
            {
                "ok": not errors,
                "mode": args.mode,
                "database": str(database),
                "posting_count": len(posting_ids),
                "processed_posting_count": processed_posting_count,
                "run_count": len(rows),
                "generator_configured": generator is not None,
                "reviewer_configured": reviewer is not None,
                "state_counts": dict(sorted(states.items())),
                "runs": rows,
                "errors": errors,
                "circuit_breaker": circuit_breaker,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
