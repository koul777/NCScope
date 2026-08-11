from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from pathlib import Path

from scripts.run_question_quality_loop import Stage, build_stages, run_stage, write_cycle_report, write_state


def _args(**overrides):
    values = {
        "skip_official": False,
        "skip_alio": False,
        "skip_feedback_eval": False,
        "model_eval": False,
        "official_collection": "all",
        "official_limit": 4,
        "alio_limit": 4,
        "alio_min_evaluated_doc_rate": 0.5,
        "alio_min_template_ready_rate": 1.0,
        "questions_per_doc": 7,
        "test_timeout_seconds": 300,
        "official_timeout_seconds": 1800,
        "alio_timeout_seconds": 900,
        "live_http_base_url": "",
        "live_http_timeout_seconds": 120,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_stages_keeps_model_evaluation_opt_in() -> None:
    template_stages = build_stages(_args())
    model_stages = build_stages(_args(model_eval=True))

    assert any("template" in stage.command for stage in template_stages)
    assert any("model" in stage.command for stage in model_stages)
    assert template_stages[0].name == "quality-regression-tests"
    alio_stage = next(stage for stage in template_stages if stage.name == "alio-template-quality-benchmark")
    threshold_index = alio_stage.command.index("--min-evaluated-doc-rate")
    assert alio_stage.command[threshold_index + 1] == "0.5"
    ready_index = alio_stage.command.index("--min-template-ready-rate")
    assert alio_stage.command[ready_index + 1] == "1.0"


def test_build_stages_can_run_fast_local_gate_only() -> None:
    stages = build_stages(_args(skip_official=True, skip_alio=True))

    assert [stage.name for stage in stages] == ["quality-regression-tests", "feedback-eval-regression"]
    command = stages[0].command
    assert "tests/test_question_quality_runtime_orchestrator.py" in command
    assert "tests/test_question_history_context.py" in command
    assert "tests/test_frontend_question_history_contract.py" in command


def test_build_stages_uses_unique_pytest_temp_directory_per_run() -> None:
    first = build_stages(_args(skip_official=True, skip_alio=True))[0].command
    second = build_stages(_args(skip_official=True, skip_alio=True))[0].command
    first_temp = next(value for value in first if value.startswith("--basetemp="))
    second_temp = next(value for value in second if value.startswith("--basetemp="))

    assert first_temp.startswith("--basetemp=.tmp/pytest-question-quality-loop-")
    assert first_temp != second_temp


def test_build_stages_adds_live_http_review_gate_only_when_configured() -> None:
    without_live = build_stages(_args(skip_official=True, skip_alio=True))
    with_live = build_stages(
        _args(
            skip_official=True,
            skip_alio=True,
            live_http_base_url="http://127.0.0.1:8015",
            live_http_timeout_seconds=45,
        )
    )

    assert all(stage.name != "live-http-review-smoke" for stage in without_live)
    live_stage = next(stage for stage in with_live if stage.name == "live-http-review-smoke")
    assert "http://127.0.0.1:8015" in live_stage.command
    assert live_stage.timeout_seconds == 135


def test_stage_and_report_capture_failure_without_shell(tmp_path: Path) -> None:
    stage = Stage(
        name="expected-failure",
        command=(sys.executable, "-c", "import sys; print('evidence'); sys.exit(3)"),
        timeout_seconds=30,
    )

    result = run_stage(stage, tmp_path)
    report = write_cycle_report(tmp_path, 1, [result])

    assert result.status == "failed"
    assert result.returncode == 3
    assert "evidence" in (tmp_path / "expected-failure.log").read_text(encoding="utf-8")
    assert "failed" in report.read_text(encoding="utf-8")


def test_state_write_is_valid_json(tmp_path: Path) -> None:
    write_state(tmp_path, {"success": True, "cycle": 2})

    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {
        "success": True,
        "cycle": 2,
    }
    assert list(tmp_path.glob(".state-*.tmp")) == []


def test_concurrent_state_writes_leave_one_valid_atomic_snapshot(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda cycle: write_state(tmp_path, {"success": True, "cycle": cycle}), range(40)))

    payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["cycle"] in range(40)
    assert list(tmp_path.glob(".state-*.tmp")) == []
