from __future__ import annotations

import re
from typing import Any


def normalize_detail_key(value: Any) -> str:
    return re.sub(r"[\s\-\_/|(),.·・〮‧･ㆍ•∙⋅]+", "", str(value or "")).lower()


HEALTHCARE_SPECIALIZED_DETAIL_KEYS = {
    normalize_detail_key(name)
    for name in (
        "간호업무 보조",
        "간호행정 보조",
        "재원환자 관리",
        "응급 환자 관리",
        "간호수행",
        "간호행정관리",
        "영상의학",
        "임상병리",
    )
}


MANUAL_REVIEW_SUGGESTIONS_BY_KEY: dict[str, str] = {
    normalize_detail_key("간호업무 보조"): (
        "manual-review-only: nearby 요양지원 units include "
        "0601010801_23v3 진료지원보조, 0601010802_23v3 물품전달, "
        "0601010803_23v3 환자이송지원, 0601010808_23v3 사고예방지원; "
        "do not count as exact coverage without human selection"
    ),
    normalize_detail_key("간호행정 보조"): (
        "manual-review-only: no exact local NCS hit; broad 병원행정 candidates are too weak for automatic coverage"
    ),
    normalize_detail_key("재원환자 관리"): (
        "false friend: element-level 재원환자 관리하기 belongs to 0601020110_16v2 진료비관리 under 병원행정; "
        "keep unresolved in clinical nursing context"
    ),
    normalize_detail_key("응급 환자 관리"): (
        "manual-review-only: source-like 0602020000_17v1 is not available in local MCP; "
        "응급환자 searches return rescue/industrial units, not nursing"
    ),
    normalize_detail_key("영상의학"): (
        "manual-review-only: no exact local/public NCS unit hit for human radiology context"
    ),
    normalize_detail_key("임상병리"): (
        "false friend: public NCS search returns animal/nonclinical pathology hits, not human clinical laboratory context"
    ),
    normalize_detail_key("간호조무"): (
        "manual-review-only: no exact local/public NCS hit; nearby 요양지원 or 병원행정 units require human selection"
    ),
    normalize_detail_key("간호수행"): (
        "manual-review-only: no exact local/public NCS hit for nursing-performance label"
    ),
    normalize_detail_key("간호행정관리"): (
        "manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage"
    ),
    normalize_detail_key("유지관리"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage; "
        "do not borrow broad maintenance suggestions automatically"
    ),
    normalize_detail_key("건축감리"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage"
    ),
    normalize_detail_key("문화・관광정책"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage"
    ),
}


def is_healthcare_specialized_detail(value: Any) -> bool:
    return normalize_detail_key(value) in HEALTHCARE_SPECIALIZED_DETAIL_KEYS


def manual_review_suggestions(details: list[str]) -> str:
    suggestions: list[str] = []
    for detail in details:
        term = str(detail or "").strip()
        suggestion = MANUAL_REVIEW_SUGGESTIONS_BY_KEY.get(normalize_detail_key(term))
        if suggestion:
            suggestions.append(f"{term}: {suggestion}")
    return " | ".join(suggestions)


def classify_unmatched_detail_gap(
    detail: Any,
    *,
    suggestions: list[dict[str, Any]] | None = None,
    canonical_detail_matches: list[dict[str, Any]] | None = None,
    unit_name_matches: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    term = str(detail or "").strip()
    suggestions = [row for row in (suggestions or []) if isinstance(row, dict)]
    canonical_detail_matches = [
        row for row in (canonical_detail_matches or []) if isinstance(row, dict)
    ]
    unit_name_matches = [row for row in (unit_name_matches or []) if isinstance(row, dict)]
    if is_healthcare_specialized_detail(term):
        return {
            "match_diagnostic": "specialized_healthcare_label_unserved_by_mcp",
            "review_action": "manual_review_healthcare_specialized_label",
            "review_reason": (
                "Healthcare specialized/NCS-like source label was extracted from the JD, "
                "but current MCP serving DB returned no exact official units; do not auto-alias without official catalog evidence."
            ),
        }
    if canonical_detail_matches:
        return {
            "match_diagnostic": "catalog_gap_verified_source_label",
            "review_action": "manual_review_canonical_detail",
            "review_reason": "Suggestion reports the same canonical detail, but exact detail search returned no official units.",
        }
    if unit_name_matches:
        return {
            "match_diagnostic": "unit_name_only",
            "review_action": "manual_review_unit_name",
            "review_reason": "No exact detail match; suggestion matched a capability unit name only.",
        }
    if suggestions:
        return {
            "match_diagnostic": "semantic_suggestion_unverified",
            "review_action": "manual_review_semantic_suggestion",
            "review_reason": "No exact detail match; semantic suggestions require human confirmation.",
        }
    return {
        "match_diagnostic": "catalog_gap_or_nonstandard_source_label",
        "review_action": "manual_review_no_match",
        "review_reason": "Current MCP index returned no exact units or semantic suggestions for this label.",
    }
