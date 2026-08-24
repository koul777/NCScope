from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_stored_jd_holdout_reference.py"
SPEC = importlib.util.spec_from_file_location("stored_jd_holdout_reference", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _record(digest: str) -> dict:
    return {
        "sha256": digest,
        "document_state": "explicit_detail",
        "expected_detail_labels": ["사무행정"],
        "expected_detail_codes": [{"source_label": "사무행정", "code": "02020302"}],
        "non_current_or_custom_detail_labels": [],
        "expected_ability_units_by_detail": {"사무행정": ["문서 작성"]},
        "expected_ability_unit_codes": [
            {
                "source_detail_label": "사무행정",
                "source_unit_label": "문서 작성",
                "code": "0202030201_22v3",
            }
        ],
        "confidence": 0.99,
        "unresolved_reason": None,
    }


def _batch(records: list[dict]) -> dict:
    digest = builder.canonical_sha256(records)
    return {
        "reviewer_type": "agent_source_evidence_review",
        "blind_completed_before_observed_comparison": True,
        "blind_records": records,
        "observed_comparison": {"blind_records_sha256": digest},
    }


def test_build_reference_strips_observed_output_and_source_evidence() -> None:
    record = _record("0" * 64)
    record["source_file"] = "private-name.pdf"
    record["evidence"] = [{"value": "source text"}]

    result = builder.build_reference([("a", _batch([record]))], expected_record_count=1)

    assert result["is_gold"] is False
    assert result["is_human_reviewed"] is False
    assert result["records"][0]["sha256"] == "0" * 64
    assert "source_file" not in result["records"][0]
    assert "evidence" not in result["records"][0]
    assert result["records_sha256"] == builder.canonical_sha256(result["records"])


def test_build_reference_rejects_post_blind_mutation() -> None:
    payload = _batch([_record("0" * 64)])
    payload["blind_records"][0]["expected_detail_labels"] = ["총무"]

    with pytest.raises(ValueError, match="hash mismatch"):
        builder.build_reference([("a", payload)])


def test_build_reference_applies_auditable_source_exact_adjudication() -> None:
    record = _record("0" * 64)
    record["expected_detail_labels"] = ["임상병리사"]
    record["expected_detail_codes"] = []
    record["non_current_or_custom_detail_labels"] = [
        {"label": "임상병리사", "classification": "source_legacy_or_non_current"}
    ]
    record["expected_ability_units_by_detail"] = {"임상병리사": ["혈액은행"]}
    record["expected_ability_unit_codes"] = []
    adjudication = {
        "reviewer_type": "agent_source_evidence_cross_adjudication",
        "source_exact_corrections_only": True,
        "corrections": [
            {
                "operation": "replace_ability_unit_label",
                "sha256": "0" * 64,
                "detail_label": "임상병리사",
                "from": "혈액은행",
                "to": "혈핵은행",
                "source_evidence": "○ 혈핵은행 – 혈액형 검사",
            }
        ],
    }

    result = builder.build_reference(
        [("a", _batch([record]))],
        expected_record_count=1,
        adjudications=[("source_exact", adjudication)],
    )

    assert result["records"][0]["expected_ability_units_by_detail"] == {
        "임상병리사": ["혈핵은행"]
    }
    assert result["reference_tier"].endswith("cross_adjudicated")
    assert result["source_adjudications"][0]["correction_count"] == 1


def test_build_reference_rejects_unscoped_or_inconsistent_code_labels() -> None:
    unscoped = _record("0" * 64)
    unscoped["expected_ability_units_by_detail"] = {"__unscoped__": ["문서 작성"]}
    with pytest.raises(ValueError, match="scope is not a declared detail"):
        builder.build_reference([("a", _batch([unscoped]))])

    inconsistent = _record("1" * 64)
    inconsistent["expected_ability_unit_codes"][0]["source_unit_label"] = "자료 관리"
    with pytest.raises(ValueError, match="unit is not in scoped units"):
        builder.build_reference([("a", _batch([inconsistent]))])
