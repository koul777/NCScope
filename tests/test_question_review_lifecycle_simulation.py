from __future__ import annotations

from scripts.simulate_question_review_lifecycle import run_simulation


def test_review_lifecycle_simulation_rejects_invalid_payloads_without_state_mutation() -> None:
    result = run_simulation(cycles=3, reconnect_every=1)

    assert result["status"] == "passed"
    assert result["failure_count"] == 0
    assert result["reconnect_count"] == 2
    assert all(result["invariants"].values())
    assert all(case["rejected_statuses"] == [422, 422, 409] for case in result["cases"])
    assert all(case["http_statuses"] == [200, 200, 200, 200, 200, 200] for case in result["cases"])
    assert all(case["response_errors"] == [] for case in result["cases"])
