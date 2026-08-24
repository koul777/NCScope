from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_stored_jd_holdout.py"
SPEC = importlib.util.spec_from_file_location("stored_jd_holdout_score", SCRIPT)
assert SPEC and SPEC.loader
scorer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scorer)


def _reference(record: dict) -> dict:
    records = [record]
    return {
        "reviewer_type": "agent_source_evidence_review",
        "is_human_reviewed": False,
        "is_gold": False,
        "blind_completed_before_observed_comparison": True,
        "record_count": 1,
        "records_sha256": scorer._canonical_sha256(records),
        "reference_id": "test",
        "records": records,
    }


def _record() -> dict:
    # First 32 bits are divisible by five, so this is in the deterministic holdout.
    return {
        "sha256": "0" * 64,
        "document_state": "explicit_detail",
        "expected_detail_labels": ["사무행정"],
        "expected_detail_codes": [{"code": "02020302"}],
        "expected_ability_units_by_detail": {"사무행정": ["문서 작성"]},
        "expected_ability_unit_codes": [{"code": "0202030201_22v3"}],
    }


def _row() -> dict:
    return {
        "sha256": "0" * 64,
        "status": "mcp_exact",
        "declared_no_mapping": "False",
        "details": "사무행정",
        "detail_mapping_states": json.dumps(
            [
                {
                    "sourceName": "사무행정",
                    "mappingState": "official_current_exact",
                    "officialDetailCodes": ["02020302"],
                }
            ]
        ),
        "ability_mapping_states": json.dumps(
            [
                {
                    "sourceName": "문서 작성",
                    "mappingState": "official_exact_source_scoped",
                    "resolvedUnitCodes": ["0202030201_22v3"],
                }
            ]
        ),
        "ability_source_scope_mapping": json.dumps({"사무행정": ["문서 작성"]}),
        "ksa_available_codes": "0202030201_22v3",
    }


THRESHOLDS = {
    "detail_code_pct": 90.0,
    "detail_document_exact_pct": 80.0,
    "ability_scope_pct": 95.0,
    "ability_code_pct": 80.0,
    "ksa_pct": 100.0,
}


def test_release_thresholds_match_policy() -> None:
    assert scorer.DEFAULT_RELEASE_THRESHOLDS == THRESHOLDS


def test_strict_cli_requires_frozen_selection_manifest(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "reference.json",
            "benchmark.csv",
            "--require-selection-manifest",
        ],
    )

    with pytest.raises(SystemExit) as error:
        scorer.main()

    assert error.value.code == 2
    assert "requires --selection-manifest-json" in capsys.readouterr().err


def test_score_rows_passes_only_independent_expected_sets() -> None:
    result = scorer.score_rows(
        _reference(_record()),
        [_row()],
        thresholds=THRESHOLDS,
    )

    assert result["metric_validity"] is True
    assert result["passed"] is True
    assert result["release_acceptance"] is True
    assert result["is_gold_accuracy"] is False
    assert result["metrics"]["ability_pairs"]["recall_pct"] == 100.0


def test_score_rows_fails_missing_code_and_ksa_without_denominator_exclusion() -> None:
    row = _row()
    row["ability_mapping_states"] = "[]"
    row["ksa_available_codes"] = ""

    result = scorer.score_rows(
        _reference(_record()),
        [row],
        thresholds=THRESHOLDS,
    )

    assert result["passed"] is False
    assert result["metrics"]["ability_codes"]["false_negative"] == 1
    assert result["metrics"]["ksa_codes"]["false_negative"] == 1
    assert result["checks"]["ability_code_recall"] is False
    assert result["checks"]["ksa_recall"] is False


def test_score_rows_fails_tampered_reference_hash() -> None:
    reference = _reference(_record())
    reference["records_sha256"] = "f" * 64

    result = scorer.score_rows(reference, [_row()], thresholds=THRESHOLDS)

    assert result["metric_validity"] is False
    assert result["release_acceptance"] is False
    assert "reference records_sha256 mismatch" in result["integrity_failures"]


def test_holdout_normalization_treats_middle_dot_variants_as_formatting() -> None:
    assert scorer._norm("방역·소독관리") == scorer._norm("방역ᆞ소독관리")
    assert scorer._norm("승객 승･하차지원") == scorer._norm("승객 승ㆍ하차지원")


def test_frozen_selection_manifest_replaces_legacy_modulo_integrity_split() -> None:
    record = _record()
    record["sha256"] = "1" * 64
    row = _row()
    row["sha256"] = record["sha256"]
    selection_records = [{"sha256": record["sha256"], "suffix": ".pdf"}]
    manifest = {
        "selection_method": "seeded_sha256_by_suffix_without_parser_output",
        "record_count": 1,
        "records_sha256": scorer._canonical_sha256(selection_records),
        "records": selection_records,
    }
    selected, failures, records_hash = scorer._validate_selection_manifest(manifest)

    result = scorer.score_rows(
        _reference(record),
        [row],
        thresholds=THRESHOLDS,
        selected_sha256s=selected,
        selection_integrity_failures=failures,
        selection_records_sha256=records_hash,
    )

    assert result["metric_validity"] is True
    assert result["passed"] is True
    assert result["selection_manifest_records_sha256"] == records_hash


def test_frozen_selection_manifest_rejects_reference_set_drift() -> None:
    selected = {"1" * 64}
    result = scorer.score_rows(
        _reference(_record()),
        [_row()],
        thresholds=THRESHOLDS,
        selected_sha256s=selected,
    )

    assert result["metric_validity"] is False
    assert "reference sha256 set does not match selection manifest" in result[
        "integrity_failures"
    ]
