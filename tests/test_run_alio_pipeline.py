from __future__ import annotations

import json
import os
import uuid
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_alio_pipeline as pipeline


def _args(tmp_path: Path, **overrides) -> Namespace:
    values = {
        "out_dir": tmp_path,
        "lookback_days": 7,
        "window_days": 7,
        "collector_limit": 25,
        "page_limit_per_window": 10,
        "request_delay": 0.0,
        "keep_files": False,
        "orchestrator_limit": 20,
        "generator": pipeline.DEFAULT_GENERATOR,
        "reviewer": pipeline.DEFAULT_REVIEWER,
        "prepare_only": False,
        "collector_timeout_sec": 60,
        "orchestrator_timeout_sec": 60,
    }
    values.update(overrides)
    return Namespace(**values)


def test_last_json_object_reads_single_line_and_pretty_tail() -> None:
    assert pipeline._last_json_object('noise\n{"ok": true}\n') == {"ok": True}
    assert pipeline._last_json_object(
        'discovered=1\n{\n  "documents": 2,\n  "postings": 1\n}\n'
    ) == {"documents": 2, "postings": 1}
    assert pipeline._last_json_object("not-json") == {}


def test_subprocess_boundary_forces_utf8_without_logging_environment(
    monkeypatch,
) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    result = pipeline._run_command(["python", "child.py"], timeout_seconds=30)

    assert result.summary == {"ok": True}
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"


def test_pipeline_commands_enable_exact_matching_claude_and_retry_backoff(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    collector = pipeline._collector_command(args, today=pipeline.date(2026, 10, 18))
    orchestrator = pipeline._orchestrator_command(args)

    assert "--match-ncs" in collector
    assert "--retry-errors" in collector
    assert collector[collector.index("--start-date") + 1] == "2026-10-11"
    assert (
        orchestrator[orchestrator.index("--generator") + 1]
        == pipeline.DEFAULT_GENERATOR
    )
    assert (
        orchestrator[orchestrator.index("--reviewer") + 1] == pipeline.DEFAULT_REVIEWER
    )
    assert "--respect-retry-backoff" in orchestrator

    prepare = pipeline._orchestrator_command(_args(tmp_path, prepare_only=True))
    assert "prepare" in prepare
    assert "--generator" not in prepare
    assert "--reviewer" not in prepare
    assert "--respect-retry-backoff" not in prepare


def test_cycle_runs_existing_backlog_even_when_collection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "alio_corpus.sqlite3").write_bytes(b"db")
    results = iter(
        [
            pipeline.CommandResult(1, 1.5, {"ok": False, "error": "network"}),
            pipeline.CommandResult(
                0,
                2.5,
                {"ok": True, "runs": [{"state": "ready_automated"}]},
            ),
        ]
    )
    commands: list[list[str]] = []

    def fake_run(command, *, timeout_seconds):
        commands.append(command)
        assert timeout_seconds == 60
        return next(results)

    monkeypatch.setattr(pipeline, "_run_command", fake_run)
    result = pipeline.run_cycle(_args(tmp_path), 3)

    assert len(commands) == 2
    assert result["cycle"] == 3
    assert result["status"] == "failed"
    assert result["orchestrator"]["summary"]["actionable_runs"] == []
    assert result["orchestrator"]["summary"]["omitted_run_count"] == 1


def test_cycle_marks_fail_closed_runs_degraded_and_journals_sanitized_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "alio_corpus.sqlite3").write_bytes(b"db")
    results = iter(
        [
            pipeline.CommandResult(0, 1.0, {"postings": 2}),
            pipeline.CommandResult(
                0,
                1.0,
                {
                    "ok": True,
                    "runs": [
                        {
                            "state": "failed",
                            "reason": "openrouter_rate_limited|retryable=true",
                        }
                    ],
                },
            ),
        ]
    )
    monkeypatch.setattr(
        pipeline,
        "_run_command",
        lambda _command, *, timeout_seconds: next(results),
    )
    args = _args(tmp_path)
    result = pipeline.run_cycle(args, 1)
    pipeline._write_cycle(args, result)

    assert result["status"] == "degraded"
    latest = json.loads((tmp_path / "pipeline_latest.json").read_text(encoding="utf-8"))
    assert latest == result
    journal = (tmp_path / "pipeline_runs.jsonl").read_text(encoding="utf-8")
    assert "openrouter_rate_limited" in journal
    assert "api_key" not in journal.casefold()


def test_operational_summary_bounds_actionable_rows() -> None:
    summary = pipeline._compact_orchestrator_summary(
        {
            "ok": True,
            "run_count": 70,
            "processed_posting_count": 1,
            "circuit_breaker": "openrouter_rate_limited|retryable=true",
            "runs": [
                {
                    "run_id": index,
                    "state": "failed",
                    "reason": "openrouter_rate_limited|retryable=true|ignored=value",
                }
                for index in range(70)
            ],
            "errors": [
                {
                    "posting_id": str(index),
                    "error": "RuntimeError",
                    "message": "api_key=sk-must-not-be-journaled",
                }
                for index in range(30)
            ],
        }
    )

    assert len(summary["actionable_runs"]) == 50
    assert summary["omitted_run_count"] == 20
    assert summary["retryable_failure_count"] == 70
    assert summary["processed_posting_count"] == 1
    assert summary["circuit_breaker"] == "openrouter_rate_limited|retryable=true"
    assert len(summary["errors"]) == 20
    assert summary["omitted_error_count"] == 10
    assert "sk-must-not-be-journaled" not in json.dumps(summary)


def test_circuit_breaker_or_partial_processing_marks_cycle_degraded() -> None:
    collector = pipeline.CommandResult(0, 1.0, {})
    orchestrator = pipeline.CommandResult(
        0,
        1.0,
        {
            "posting_count": 10,
            "processed_posting_count": 1,
            "circuit_breaker": "openrouter_rate_limited",
            "runs": [],
        },
    )

    assert pipeline._cycle_status(collector, orchestrator) == "degraded"


def test_cycle_writes_durable_stage_events_and_seals_interrupted_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "alio_corpus.sqlite3").write_bytes(b"db")
    results = iter(
        [
            pipeline.CommandResult(
                0,
                1.0,
                {
                    "postings": 2,
                    "documents": 3,
                    "api_key": "sk-collector-secret",
                    "unexpected": "not persisted",
                },
            ),
            pipeline.CommandResult(
                0,
                1.0,
                {
                    "ok": True,
                    "posting_count": 2,
                    "processed_posting_count": 1,
                    "circuit_breaker": "openrouter_rate_limited|retryable=true",
                    "runs": [],
                    "errors": [],
                },
            ),
        ]
    )
    monkeypatch.setattr(
        pipeline,
        "_run_command",
        lambda _command, *, timeout_seconds: next(results),
    )
    args = _args(tmp_path)

    result = pipeline.run_cycle(args, 1)
    pipeline._write_cycle(args, result)
    pipeline._append_journal_event(
        args,
        pipeline._cycle_event(
            "cycle_started",
            cycle_id="abandoned-cycle",
            cycle_number=2,
        ),
    )

    assert pipeline._seal_interrupted_cycles(args) == 1
    assert pipeline._seal_interrupted_cycles(args) == 0
    raw = (tmp_path / pipeline.JOURNAL_NAME).read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines()]
    assert [record["event"] for record in records[:4]] == [
        "cycle_started",
        "collector_finished",
        "orchestrator_finished",
        "cycle_committed",
    ]
    assert records[-1]["event"] == "cycle_interrupted"
    assert records[-1]["status"] == "interrupted"
    assert "sk-collector-secret" not in raw
    assert "unexpected" not in raw


def test_journal_rotates_at_bound_and_keeps_five_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "JOURNAL_MAX_BYTES", 300)
    args = _args(tmp_path)

    for index in range(15):
        pipeline._append_journal_event(
            args,
            pipeline._cycle_event(
                "cycle_started",
                cycle_id=f"cycle-{index}",
                cycle_number=index,
                detail="x" * 120,
            ),
        )

    assert (tmp_path / pipeline.JOURNAL_NAME).is_file()
    assert all(
        (tmp_path / f"{pipeline.JOURNAL_NAME}.{generation}").is_file()
        for generation in range(1, 6)
    )
    assert not (tmp_path / f"{pipeline.JOURNAL_NAME}.6").exists()


def _write_lock_owner(path: Path, *, pid: int, identity: str) -> None:
    path.mkdir()
    (path / "owner.json").write_text(
        json.dumps(
            {
                "schema_version": pipeline.LOCK_SCHEMA_VERSION,
                "pid": pid,
                "process_start_identity": identity,
                "instance_id": uuid.uuid4().hex,
                "started_at": "2026-10-18T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("owner_alive", "observed_identity"),
    [(False, ""), (True, "reused-pid-start")],
)
def test_single_instance_lock_reclaims_only_verifiably_stale_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_alive: bool,
    observed_identity: str,
) -> None:
    stale_pid = 424242
    _write_lock_owner(
        tmp_path / ".pipeline.lock",
        pid=stale_pid,
        identity="old-process-start",
    )

    def inspect(pid: int) -> tuple[bool | None, str]:
        if pid == stale_pid:
            return owner_alive, observed_identity
        assert pid == os.getpid()
        return True, "current-process-start"

    monkeypatch.setattr(pipeline, "_process_start_identity", inspect)

    with pipeline._single_instance_lock(tmp_path):
        owner = json.loads(
            (tmp_path / ".pipeline.lock" / "owner.json").read_text(encoding="utf-8")
        )
        assert owner["pid"] == os.getpid()
        assert owner["process_start_identity"] == "current-process-start"

    assert not (tmp_path / ".pipeline.lock").exists()
    assert not (tmp_path / ".pipeline.lock.reclaiming").exists()


def test_single_instance_lock_fails_closed_for_corrupt_owner(tmp_path: Path) -> None:
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="alio_pipeline_lock_owner_invalid"):
        with pipeline._single_instance_lock(tmp_path):
            pass

    assert lock_dir.is_dir()


def test_single_instance_lock_rejects_overlap_and_cleans_up(tmp_path: Path) -> None:
    with pipeline._single_instance_lock(tmp_path):
        assert (tmp_path / ".pipeline.lock" / "owner.json").is_file()
        with pytest.raises(RuntimeError, match="alio_pipeline_already_running"):
            with pipeline._single_instance_lock(tmp_path):
                pass

    assert not (tmp_path / ".pipeline.lock").exists()


def test_main_loop_runs_exactly_31_cycles_and_sleeps_only_between_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cycles: list[int] = []
    committed: list[int] = []
    sleeps: list[int] = []

    def fake_cycle(_args: Namespace, cycle: int) -> dict[str, object]:
        cycles.append(cycle)
        return {"cycle": cycle, "cycle_id": f"cycle-{cycle}", "status": "ok"}

    monkeypatch.setattr(pipeline, "_load_dotenv", lambda _path: None)
    monkeypatch.setattr(pipeline, "_single_instance_lock", lambda _path: nullcontext())
    monkeypatch.setattr(pipeline, "_seal_interrupted_cycles", lambda _args: 0)
    monkeypatch.setattr(pipeline, "run_cycle", fake_cycle)
    monkeypatch.setattr(
        pipeline,
        "_write_cycle",
        lambda _args, result: committed.append(int(result["cycle"])),
    )
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: sleeps.append(seconds))

    exit_code = pipeline.main(
        [
            "loop",
            "--out-dir",
            str(tmp_path),
            "--max-cycles",
            "31",
            "--interval-sec",
            "1",
        ]
    )

    assert exit_code == 0
    assert cycles == list(range(1, 32))
    assert committed == cycles
    assert sleeps == [30] * 30
