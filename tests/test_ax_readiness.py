from app.services.ax_readiness import AX_CRITERIA, assess_ax_readiness
import app.main as main


def _signals(*, evidence_groups=(), pilot_groups=()):
    values = {}
    for criterion_id, group, _title in AX_CRITERIA:
        values[f"{criterion_id}_evidence"] = group in evidence_groups
        values[f"{criterion_id}_pilot"] = group in pilot_groups
    return values


def test_ax_gate_does_not_average_over_a_missing_required_item() -> None:
    signals = _signals(evidence_groups={"ready", "enabled", "first"})
    signals["first_exception_evidence"] = False
    signals["native_loop_evidence"] = True

    result = assess_ax_readiness(signals)

    assert result["base_stage"] == "AI-Enabled"
    assert "first_exception" in result["missing_gates"]


def test_ax_pilot_does_not_count_as_operational_evidence() -> None:
    result = assess_ax_readiness(_signals(pilot_groups={"ready", "enabled", "first", "native"}))

    assert result["base_stage"] == "미착수"
    assert result["verified_count"] == 0
    assert all(item["status"] == "pilot" for item in result["items"])


def test_ax_all_operational_gates_reach_native() -> None:
    result = assess_ax_readiness(_signals(evidence_groups={"ready", "enabled", "first", "native"}))

    assert result["base_stage"] == "AI-Native"
    assert result["verified_count"] == 13
    assert result["all_required_gates"] is True


def test_ax_ops_endpoint_stays_enabled_when_first_gates_lack_operating_evidence(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(
        main,
        "question_quality_metrics",
        lambda: {
            "runs": 3,
            "reviews": 2,
            "escalation_runs": 0,
            "active_eval_cases": 0,
            "decisions": {"approve": 2},
        },
    )
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    result = main.ops_ax_readiness("test-admin-token")

    assert result["assessment"]["base_stage"] == "AI-Enabled"
    assert result["assessment"]["items"][3]["status"] == "pilot"
    assert result["disclaimer"]
