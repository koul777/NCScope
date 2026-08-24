from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_stored_jd_gaps.py"
SPEC = importlib.util.spec_from_file_location("analyze_stored_jd_gaps", SCRIPT_PATH)
assert SPEC and SPEC.loader
gap_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gap_analysis)


def _catalog(
    *,
    details: list[dict] | None = None,
    units: list[dict] | None = None,
) -> dict:
    details = details or []
    units = units or []
    details_by_key: dict[str, list[dict]] = {}
    units_by_name_key: dict[str, list[dict]] = {}
    for row in details:
        details_by_key.setdefault(gap_analysis.normalize_key(row["name"]), []).append(row)
    for row in units:
        units_by_name_key.setdefault(gap_analysis.normalize_key(row["name"]), []).append(row)
    return {
        "details": details,
        "units": units,
        "details_by_key": details_by_key,
        "units_by_name_key": units_by_name_key,
    }


def test_normalize_key_folds_visual_middle_dot_variants() -> None:
    assert gap_analysis.normalize_key("회계․감사") == gap_analysis.normalize_key("회계·감사")
    assert gap_analysis.normalize_key("문화・예술행정") == gap_analysis.normalize_key("문화·예술행정")


def test_collect_gap_occurrences_groups_format_variants_and_tracks_files() -> None:
    rows = [
        {
            "filename": "a.pdf",
            "suffix": ".pdf",
            "detail_unmatched": "회계․감사; 임상간호",
        },
        {
            "filename": "b.hwp",
            "suffix": ".hwp",
            "detail_unmatched": "회계·감사",
        },
    ]

    grouped = gap_analysis.collect_gap_occurrences(rows)

    accounting = grouped[gap_analysis.normalize_key("회계·감사")]
    assert accounting["occurrences"] == 2
    assert accounting["files"] == ["a.pdf", "b.hwp"]
    assert grouped[gap_analysis.normalize_key("임상간호")]["occurrences"] == 1


def test_diagnose_detail_never_auto_accepts_semantic_suggestion(monkeypatch) -> None:
    monkeypatch.setattr(gap_analysis, "search_units_by_detail", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        gap_analysis,
        "suggest_units_by_text",
        lambda *_args, **_kwargs: [
            {
                "canonicalDetailName": "병원행정",
                "compeUnitName": "병원행정 기획",
                "ncsLclasCdnm": "보건·의료",
                "ncsMclasCdnm": "보건",
                "ncsSclasCdnm": "의료기술지원",
                "ncsSubdCdnm": "병원행정",
            }
        ],
    )

    result = gap_analysis.diagnose_detail("간호행정관리", suggestion_limit=5)

    assert result["automatic_official_code_allowed"] is False
    assert result["match_diagnostic"] == "specialized_healthcare_label_unserved_by_catalog"
    assert result["best_suggestion_name"] == "병원행정"


def test_diagnose_detail_routes_nonexact_semantic_suggestion_to_review(monkeypatch) -> None:
    monkeypatch.setattr(gap_analysis, "search_units_by_detail", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        gap_analysis,
        "suggest_units_by_text",
        lambda *_args, **_kwargs: [
            {
                "canonicalDetailName": "냉동공조설계",
                "compeUnitName": "냉동공조 설계도면 작성",
            }
        ],
    )

    result = gap_analysis.diagnose_detail(
        "공조냉동설계",
        suggestion_limit=5,
        catalog_index=_catalog(),
    )

    assert result["catalog_status"] == "official_detail_catalog_absent"
    assert result["suggestion_status"] == "semantic_suggestion_review_required"
    assert result["match_diagnostic"] == "semantic_suggestion_review_required"
    assert result["automatic_acceptance_basis"] == "none"
    assert result["automatic_official_code_allowed"] is False


def test_diagnose_detail_auto_accepts_only_unique_normalized_catalog_exact() -> None:
    catalog = _catalog(
        details=[{"code": "02030201", "name": "회계·감사"}],
    )

    result = gap_analysis.diagnose_detail(
        "회계․감사",
        suggestion_limit=5,
        catalog_index=catalog,
        query_mcp=False,
    )

    assert result["catalog_status"] == "official_detail_normalized_exact"
    assert result["match_diagnostic"] == "official_detail_normalized_exact"
    assert result["catalog_detail_codes"] == "02030201"
    assert result["catalog_detail_names"] == "회계·감사"
    assert result["automatic_acceptance_basis"] == "official_detail_normalized_exact"
    assert result["automatic_official_code_allowed"] is True


def test_diagnose_detail_auto_accepts_only_preexisting_alias_with_catalog_target() -> None:
    catalog = _catalog(
        details=[{"code": "14030103", "name": "건축공사감리"}],
    )

    result = gap_analysis.diagnose_detail(
        "건축감리",
        suggestion_limit=5,
        catalog_index=catalog,
        query_mcp=False,
    )

    assert result["catalog_status"] == "verified_explicit_detail_alias"
    assert result["explicit_alias_target"] == "건축공사감리"
    assert result["match_diagnostic"] == "verified_explicit_detail_alias"
    assert result["automatic_official_code_allowed"] is True


def test_diagnose_detail_blocks_ambiguous_normalized_catalog_key() -> None:
    catalog = _catalog(
        details=[
            {"code": "11111111", "name": "가·나"},
            {"code": "22222222", "name": "가 나"},
        ],
    )

    result = gap_analysis.diagnose_detail(
        "가-나",
        suggestion_limit=5,
        catalog_index=catalog,
        query_mcp=False,
    )

    assert result["match_diagnostic"] == "official_detail_normalized_ambiguous"
    assert result["automatic_acceptance_basis"] == "none"
    assert result["automatic_official_code_allowed"] is False


def test_diagnose_detail_identifies_exact_unit_name_without_promoting_parent() -> None:
    catalog = _catalog(
        units=[
            {
                "code": "0601020118_22v3",
                "name": "병원시설관리",
                "detail_name": "병원행정",
            }
        ],
    )

    result = gap_analysis.diagnose_detail(
        "병원시설관리",
        suggestion_limit=5,
        catalog_index=catalog,
        query_mcp=False,
    )

    assert result["catalog_status"] == "capability_unit_name_exact_only"
    assert result["match_diagnostic"] == "capability_unit_name_not_detail"
    assert result["catalog_unit_parent_details"] == "병원행정"
    assert result["automatic_official_code_allowed"] is False


def test_diagnose_detail_keeps_institution_label_in_manual_review() -> None:
    result = gap_analysis.diagnose_detail(
        "학사운영",
        suggestion_limit=5,
        catalog_index=_catalog(),
        query_mcp=False,
    )

    assert result["source_label_type"] == "institution_defined_label"
    assert result["match_diagnostic"] == "self_developed_or_institution_label"
    assert result["automatic_official_code_allowed"] is False


def test_collect_gap_occurrences_preserves_explicit_self_developed_evidence() -> None:
    rows = [
        {
            "filename": "declared.pdf",
            "suffix": ".pdf",
            "detail_unmatched": "기관특화업무",
            "self_developed_details": "기관특화업무",
        }
    ]

    grouped = gap_analysis.collect_gap_occurrences(rows)
    item = grouped[gap_analysis.normalize_key("기관특화업무")]

    assert item["declared_self_developed"] is True
    assert item["self_developed_evidence_files"] == ["declared.pdf"]


def test_current_catalog_and_benchmark_baseline_is_reproducible() -> None:
    catalog = gap_analysis.load_catalog_index()
    if not gap_analysis.DEFAULT_BENCHMARK_PATH.is_file():
        pytest.skip("local stored-JD benchmark is intentionally excluded from Git")
    with gap_analysis.DEFAULT_BENCHMARK_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        grouped = gap_analysis.collect_gap_occurrences(list(csv.DictReader(handle)))

    assert catalog["detail_catalog_count"] == 1094
    assert catalog["unit_catalog_count"] == 13282
    assert len(grouped) == 52
    assert sum(int(item["occurrences"]) for item in grouped.values()) == 84

    diagnosed = []
    for item in grouped.values():
        result = gap_analysis.diagnose_detail(
            item["detail"],
            suggestion_limit=1,
            catalog_index=catalog,
            declared_self_developed=bool(item["declared_self_developed"]),
            query_mcp=False,
        )
        diagnosed.append({**item, **result})
    summary = gap_analysis._summary(diagnosed)

    assert summary["catalog_status_unique_counts"] == {
        "official_detail_catalog_absent": 46,
        "capability_unit_name_exact_only": 6,
    }
    assert summary["diagnostic_unique_counts"] == {
        "specialized_healthcare_label_unserved_by_catalog": 16,
        "self_developed_or_institution_label": 20,
        "known_manual_review_catalog_gap": 1,
        "official_detail_catalog_absent": 9,
        "capability_unit_name_not_detail": 6,
    }
    assert summary["current_exact_resolved_unique"] == 0
    assert summary["manual_review_unique"] == 52
