from __future__ import annotations

from scripts.simulate_question_review_concurrency import run_simulation


def test_review_concurrency_simulation_preserves_one_active_decision() -> None:
    result = run_simulation(
        rounds=3,
        parallel_reviews=4,
        mixed_operations=4,
        reconnect_every=1,
    )

    assert result["status"] == "passed"
    assert result["failure_count"] == 0
    assert result["total_mutations"] == 24
    assert result["reconnect_count"] == 2
    assert all(result["invariants"].values())
    assert all(case["active_reviews"] == 1 for case in result["cases"])
    assert all(case["final_decision"] == case["expected_decision"] for case in result["cases"])
