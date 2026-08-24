from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_stored_jd_quality_gate.py"
)
SPEC = importlib.util.spec_from_file_location("check_stored_jd_quality_gate", SCRIPT_PATH)
assert SPEC and SPEC.loader
quality_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality_gate)


def _passing_summary() -> dict[str, object]:
    return {
        "files": 206,
        "unique_contents": 198,
        "metric_validity": {"valid": True, "invalid_reasons": []},
        "current_official_detail_recognition_pct": 90.0,
        "detail_mapping_state_coverage_pct": 100.0,
        "documents_all_current_official_details_exact_pct": 80.0,
        "ability_mapping_state_coverage_pct": 100.0,
        "ability_official_scope_candidate_pct": 95.0,
        "ability_official_code_candidate_pct": 80.0,
        "ksa_available_pct": 100.0,
        # Legacy raw-yield metrics remain diagnostic and do not gate acceptance.
        "parse_success_pct": 99.0,
        "official_validation_detail_exact_pct": 12.0,
        "documents_all_official_details_exact_pct": 11.0,
        "ability_scoped_pct": 10.0,
        "ability_end_to_end_pct": 9.0,
    }


def _passing_holdout_score() -> dict[str, object]:
    return {
        "evaluation_basis": "agent_source_evidence_review_holdout_not_human_gold",
        "is_human_reviewed": False,
        "is_gold_accuracy": False,
        "metric_validity": True,
        "passed": True,
        "release_acceptance": True,
        "record_count": 33,
        "reference_records_sha256": "a" * 64,
        "selection_manifest_records_sha256": "b" * 64,
        "release_acceptance_scope": "agent_reviewed_holdout_quality_gate",
        "thresholds": dict(quality_gate.HOLDOUT_POLICY_THRESHOLDS),
        "checks": {
            "detail_code_precision": True,
            "detail_code_recall": True,
            "detail_document_exact": True,
            "ability_scope_precision": True,
            "ability_scope_recall": True,
            "ability_code_precision": True,
            "ability_code_recall": True,
            "ksa_recall": True,
        },
        "integrity_failures": [],
    }


def test_quality_gate_passes_only_at_all_target_thresholds() -> None:
    result = quality_gate.evaluate_quality_gate(_passing_summary())

    assert quality_gate.CORE_OPERATIONAL_THRESHOLDS == {
        "current_official_detail_recognition_pct": 90.0,
        "detail_mapping_state_coverage_pct": 100.0,
        "documents_all_current_official_details_exact_pct": 80.0,
    }
    assert quality_gate.DOWNSTREAM_ADVISORY_THRESHOLDS == {
        "ability_mapping_state_coverage_pct": 100.0,
        "ability_official_scope_candidate_pct": 95.0,
        "ability_official_code_candidate_pct": 80.0,
        "ksa_available_pct": 100.0,
    }
    assert quality_gate.DEFAULT_THRESHOLDS == {
        **quality_gate.CORE_OPERATIONAL_THRESHOLDS,
        **quality_gate.DOWNSTREAM_ADVISORY_THRESHOLDS,
    }
    assert result["passed"] is True
    assert result["failures"] == []
    assert result["acceptance_scope"] == (
        "operational_quality_gate_not_gold_accuracy"
    )
    assert result["release_acceptance"] is False
    assert result["release_acceptance_reason"] == (
        "requires_valid_agent_reviewed_holdout_scorer"
    )
    assert result["release_failures"] == ["missing_agent_reviewed_holdout_score"]


def test_quality_gate_accepts_release_only_with_valid_holdout_score() -> None:
    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=_passing_holdout_score(),
    )

    assert result["passed"] is True
    assert result["release_acceptance"] is True
    assert result["release_failures"] == []


def test_quality_gate_rejects_invalid_or_incomplete_holdout_score() -> None:
    holdout = _passing_holdout_score()
    holdout["reference_records_sha256"] = "bad"
    holdout["checks"] = {"detail_code_recall": False}

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["passed"] is True
    assert result["release_acceptance"] is False
    assert set(result["release_failures"]) == {
        "invalid_holdout_reference_hash",
        "holdout_checks_not_all_true",
    }


def test_quality_gate_rejects_missing_selection_manifest_and_relaxed_policy() -> None:
    holdout = _passing_holdout_score()
    holdout["selection_manifest_records_sha256"] = ""
    holdout["thresholds"] = {
        key: 1.0 for key in quality_gate.HOLDOUT_POLICY_THRESHOLDS
    }

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is False
    assert set(result["release_failures"]) == {
        "invalid_holdout_selection_manifest_hash",
        "holdout_threshold_policy_mismatch",
    }


def test_quality_gate_rejects_wrong_holdout_acceptance_scope() -> None:
    holdout = _passing_holdout_score()
    holdout["release_acceptance_scope"] = "development_only"

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is False
    assert result["release_failures"] == [
        "invalid_holdout_release_acceptance_scope"
    ]


def test_quality_gate_reports_downstream_metric_provenance_without_gating() -> None:
    for validity, reason in (
        (None, "missing_metric_validity"),
        (
            {
                "valid": False,
                "invalid_reasons": ["row: missing ability_mapping_states"],
            },
            "invalid_derived_metrics",
        ),
    ):
        summary = _passing_summary()
        if validity is None:
            summary.pop("metric_validity")
        else:
            summary["metric_validity"] = validity

        result = quality_gate.evaluate_quality_gate(summary)

        assert result["passed"] is True
        assert result["failures"] == []
        assert result["downstream_advisory"]["operational"]["passed"] is False
        failure = next(
            row
            for row in result["downstream_advisory"]["operational"]["failures"]
            if row["metric"] == "metric_validity"
        )
        assert failure["reason"] == reason


def test_quality_gate_reports_every_failed_metric_and_corpus_drift() -> None:
    summary = _passing_summary()
    summary["files"] = 205
    summary["ability_official_code_candidate_pct"] = 79.99
    summary["ksa_available_pct"] = 99.0

    result = quality_gate.evaluate_quality_gate(summary)

    assert result["passed"] is False
    assert {row["metric"] for row in result["failures"]} == {"files"}
    assert {
        row["metric"]
        for row in result["downstream_advisory"]["operational"]["failures"]
    } == {
        "ability_official_code_candidate_pct",
        "ksa_available_pct",
    }


def test_quality_gate_fails_closed_when_new_metrics_are_missing() -> None:
    legacy_summary = {
        "files": 206,
        "unique_contents": 198,
        "parse_success_pct": 100.0,
        "official_validation_detail_exact_pct": 100.0,
        "documents_all_official_details_exact_pct": 100.0,
        "ability_scoped_pct": 100.0,
        "ability_end_to_end_pct": 100.0,
        "ksa_available_pct": 100.0,
    }

    result = quality_gate.evaluate_quality_gate(legacy_summary)

    assert result["passed"] is False
    missing = {
        row["metric"]
        for row in result["failures"]
        if row.get("reason") == "missing_required_metric"
    }
    assert missing == set(quality_gate.CORE_OPERATIONAL_THRESHOLDS)
    downstream_missing = {
        row["metric"]
        for row in result["downstream_advisory"]["operational"]["failures"]
        if row.get("reason") == "missing_required_metric"
    }
    assert downstream_missing == set(quality_gate.DOWNSTREAM_ADVISORY_THRESHOLDS) - {
        "ksa_available_pct"
    }


def test_quality_gate_fails_closed_on_invalid_percentage() -> None:
    summary = _passing_summary()
    summary["detail_mapping_state_coverage_pct"] = "not-a-number"

    result = quality_gate.evaluate_quality_gate(summary)

    assert result["passed"] is False
    assert result["observed"]["detail_mapping_state_coverage_pct"] is None
    assert any(
        row["metric"] == "detail_mapping_state_coverage_pct"
        and row["reason"] == "invalid_percentage"
        for row in result["failures"]
    )


def test_quality_gate_keeps_invalid_downstream_percentage_advisory() -> None:
    for invalid in (float("nan"), float("inf"), float("-inf")):
        summary = _passing_summary()
        summary["ability_official_scope_candidate_pct"] = invalid

        result = quality_gate.evaluate_quality_gate(summary)

        assert result["passed"] is True
        assert result["observed"]["ability_official_scope_candidate_pct"] is None
        assert any(
            row["metric"] == "ability_official_scope_candidate_pct"
            and row["reason"] == "invalid_percentage"
            for row in result["downstream_advisory"]["operational"]["failures"]
        )


def test_detail_mapping_state_coverage_requires_exactly_full_coverage() -> None:
    summary = _passing_summary()
    summary["detail_mapping_state_coverage_pct"] = 99.99

    result = quality_gate.evaluate_quality_gate(summary)

    assert result["passed"] is False
    assert any(
        row["metric"] == "detail_mapping_state_coverage_pct"
        and row["reason"] == "below_minimum"
        for row in result["failures"]
    )


def test_ability_mapping_state_coverage_is_downstream_advisory() -> None:
    summary = _passing_summary()
    summary["ability_mapping_state_coverage_pct"] = 99.99

    result = quality_gate.evaluate_quality_gate(
        summary,
        holdout_score=_passing_holdout_score(),
    )

    assert result["passed"] is True
    assert result["release_acceptance"] is True
    assert result["downstream_advisory"]["passed"] is False
    assert result["downstream_advisory"]["operational"]["failures"] == [
        {
            "metric": "ability_mapping_state_coverage_pct",
            "actual": 99.99,
            "minimum": 100.0,
            "reason": "below_minimum",
        }
    ]


def test_downstream_holdout_failures_do_not_block_detail_release_gate() -> None:
    holdout = _passing_holdout_score()
    holdout["passed"] = False
    holdout["release_acceptance"] = False
    holdout["checks"]["ability_scope_recall"] = False
    holdout["checks"]["ksa_recall"] = False

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is True
    assert result["release_failures"] == []
    assert result["downstream_advisory"]["passed"] is False
    assert result["downstream_advisory"]["holdout"]["checks"][
        "ability_scope_recall"
    ] is False


def test_downstream_holdout_threshold_policy_is_advisory() -> None:
    holdout = _passing_holdout_score()
    holdout["thresholds"]["ability_scope_pct"] = 1.0

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is True
    assert result["release_failures"] == []
    assert result["downstream_advisory"]["holdout"][
        "threshold_policy_matches"
    ] is False


def test_only_downstream_empty_denominators_are_advisory() -> None:
    holdout = _passing_holdout_score()
    holdout["metric_validity"] = False
    holdout["passed"] = False
    holdout["release_acceptance"] = False
    holdout["integrity_failures"] = [
        "empty evaluation denominator: ability_pairs",
        "empty evaluation denominator: ability_codes",
        "empty evaluation denominator: ksa_codes",
    ]

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is True
    assert result["release_failures"] == []
    assert result["downstream_advisory"]["holdout"]["integrity_failures"] == (
        holdout["integrity_failures"]
    )

    holdout["integrity_failures"].append("benchmark missing holdout deadbeef")
    rejected = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )
    assert rejected["release_acceptance"] is False
    assert rejected["release_failures"] == ["holdout_integrity_failures"]


def test_holdout_provenance_must_not_claim_human_gold() -> None:
    holdout = _passing_holdout_score()
    holdout["is_human_reviewed"] = True
    holdout["is_gold_accuracy"] = True

    result = quality_gate.evaluate_quality_gate(
        _passing_summary(),
        holdout_score=holdout,
    )

    assert result["release_acceptance"] is False
    assert set(result["release_failures"]) == {
        "invalid_holdout_is_human_reviewed",
        "invalid_holdout_is_gold_accuracy",
    }


def test_quality_gate_preserves_legacy_metrics_as_diagnostics() -> None:
    summary = _passing_summary()

    result = quality_gate.evaluate_quality_gate(summary)

    assert result["passed"] is True
    assert result["legacy_diagnostics"]["parse_success_pct"] == 99.0
    assert (
        result["legacy_diagnostics"]["official_validation_detail_exact_pct"]
        == 12.0
    )
    assert "official_validation_detail_exact_pct" not in result["thresholds"]
