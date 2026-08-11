from __future__ import annotations

from scripts.simulate_question_regeneration import FOCUSES, run_simulation, write_report
from scripts.simulate_question_review_lifecycle import run_simulation as run_lifecycle_simulation
from app.services.question_quality_orchestrator import RUNTIME_QUESTION_ORCHESTRATION_POLICY


def test_repeated_generation_simulation_covers_methods_and_ksa_without_failures() -> None:
    result = run_simulation(cycles=4, history_window=200)

    assert result["status"] == "passed"
    assert result["runtime_policy"] == RUNTIME_QUESTION_ORCHESTRATION_POLICY
    assert result["case_count"] == 7 * len(FOCUSES)
    assert result["total_cycles"] == 7 * len(FOCUSES) * 4
    assert result["failure_count"] == 0
    assert result["total_repairs"] >= result["case_count"]
    assert all(result["invariants"].values())
    assert all(case["generated_count"] == 4 for case in result["cases"])
    assert all(case["unique_count"] == 4 for case in result["cases"])


def test_regeneration_report_exposes_representative_questions_for_human_review(tmp_path) -> None:
    result = run_simulation(cycles=3, history_window=20, methods=["경험면접"], focus_types=["기술"])

    _json_path, markdown_path = write_report(result, tmp_path)
    report = markdown_path.read_text(encoding="utf-8")

    assert "## 사람이 확인할 대표 문항" in report
    assert "<summary>경험면접 / 기술</summary>" in report
    assert result["cases"][0]["first_question"] in report
    assert result["cases"][0]["last_question"] in report
    assert "경험이 있으십니까" not in report


def test_review_lifecycle_simulation_survives_decision_changes_rollbacks_and_reconnects() -> None:
    result = run_lifecycle_simulation(cycles=6, reconnect_every=2)

    assert result["status"] == "passed"
    assert result["cycles"] == 6
    assert result["reconnect_count"] == 2
    assert result["unique_question_count"] == 6
    assert result["failure_count"] == 0
    assert all(result["invariants"].values())
    assert all(case["http_statuses"] == [200, 200, 200, 200, 200, 200] for case in result["cases"])
    assert all(case["rejected_statuses"] == [422, 422, 409] for case in result["cases"])
    assert all(case["restored_verdict"] == case["first_verdict"] for case in result["cases"])
