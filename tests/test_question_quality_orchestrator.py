from __future__ import annotations

import argparse
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
        "questions_per_doc": 7,
        "test_timeout_seconds": 300,
        "official_timeout_seconds": 1800,
        "alio_timeout_seconds": 900,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_stages_keeps_model_evaluation_opt_in() -> None:
    template_stages = build_stages(_args())
    model_stages = build_stages(_args(model_eval=True))

    assert any("template" in stage.command for stage in template_stages)
    assert any("model" in stage.command for stage in model_stages)
    assert template_stages[0].name == "quality-regression-tests"


def test_build_stages_can_run_fast_local_gate_only() -> None:
    stages = build_stages(_args(skip_official=True, skip_alio=True))

    assert [stage.name for stage in stages] == ["quality-regression-tests", "feedback-eval-regression"]


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
