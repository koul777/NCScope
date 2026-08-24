from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_detail_key(value: Any) -> str:
    """Return the strict formatting-insensitive key used by NCS detail lookup."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


# These aliases mirror the narrowly-scoped, evidence-backed aliases already used by
# app.services.ncs_mcp_client. Do not add a similarity-derived mapping here.
EXPLICIT_DETAIL_ALIASES_BY_KEY: dict[str, str] = {
    normalize_detail_key("건축감리"): "건축공사감리",
    normalize_detail_key("비서 (글로벌경영사무 지원)"): "비서",
    normalize_detail_key("외식운영관리 (02.식자재관리)"): "외식운영관리",
}


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
        "간호조무",
        "양의학치료",
        "임상간호",
        "양약조제",
        "물리치료",
        "작업치료",
        "임상 간호 업무",
        "임상병리 (감염관리)",
        "임상병리사",
        "외래",
        "병동 특수부서 간호 보조",
        "간호보조",
    )
}


# Corpus labels that are known to be institution task groupings rather than an
# asserted official NCS detail. Exact keys make this classification reproducible;
# wording similarity must never add a label to this set at runtime.
INSTITUTION_DEFINED_DETAIL_KEYS = {
    normalize_detail_key(name)
    for name in (
        "학사운영",
        "법무",
        "연구개발",
        "기업홍보",
        "연구사업관리",
        "정책개발",
        "연구기획",
        "자료수집 및 가공",
        "연구진행",
        "문화・관광・콘텐츠정책",
        "연구사업기획",
        "연구사업준비",
        "자료활용",
        "결과도출 및 정책화",
        "연구지원 및 행정",
        "영유아조기개입서비스",
        "RCY",
        "국제협력",
        "남북교류 등",
        "정책연구",
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
        "verified explicit alias: 건축감리 -> 건축공사감리; acceptance still requires the official target in the local catalog"
    ),
    normalize_detail_key("문화・관광정책"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage"
    ),
}


def explicit_detail_alias_target(value: Any) -> str:
    return EXPLICIT_DETAIL_ALIASES_BY_KEY.get(normalize_detail_key(value), "")


def is_healthcare_specialized_detail(value: Any) -> bool:
    return normalize_detail_key(value) in HEALTHCARE_SPECIALIZED_DETAIL_KEYS


def source_label_classification(
    value: Any,
    *,
    declared_self_developed: bool = False,
) -> dict[str, str]:
    """Classify only from explicit declarations or exact registries."""

    key = normalize_detail_key(value)
    if declared_self_developed or any(
        marker in key for marker in ("자체개발", "기관자체", "ncs미개발")
    ):
        return {
            "source_label_type": "declared_self_developed",
            "source_type_evidence": "benchmark_or_label_explicit_self_developed_declaration",
        }
    if key in INSTITUTION_DEFINED_DETAIL_KEYS:
        return {
            "source_label_type": "institution_defined_label",
            "source_type_evidence": "exact_corpus_institution_label_registry",
        }
    if key in HEALTHCARE_SPECIALIZED_DETAIL_KEYS:
        return {
            "source_label_type": "specialized_healthcare_label",
            "source_type_evidence": "exact_healthcare_label_registry",
        }
    return {
        "source_label_type": "ncs_like_unverified",
        "source_type_evidence": "no_explicit_source_type_evidence",
    }


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
    official_detail_matches: list[dict[str, Any]] | None = None,
    explicit_alias_matches: list[dict[str, Any]] | None = None,
    declared_self_developed: bool = False,
    catalog_checked: bool = False,
) -> dict[str, str]:
    """Return a deterministic gap lane without promoting fuzzy suggestions.

    ``official_detail_matches`` and ``explicit_alias_matches`` must come from an
    exact normalized lookup in the official detail catalog. Similarity-ranked or
    semantic results belong only in ``suggestions`` and can never reach an accept
    action through this function.
    """

    term = str(detail or "").strip()
    suggestions = [row for row in (suggestions or []) if isinstance(row, dict)]
    canonical_detail_matches = [
        row for row in (canonical_detail_matches or []) if isinstance(row, dict)
    ]
    unit_name_matches = [row for row in (unit_name_matches or []) if isinstance(row, dict)]
    official_detail_matches = [
        row for row in (official_detail_matches or []) if isinstance(row, dict)
    ]
    explicit_alias_matches = [
        row for row in (explicit_alias_matches or []) if isinstance(row, dict)
    ]
    source = source_label_classification(
        term,
        declared_self_developed=declared_self_developed,
    )
    if official_detail_matches:
        official_codes = {
            str(row.get("code") or "").strip()
            for row in official_detail_matches
            if str(row.get("code") or "").strip()
        }
        if len(official_codes) != 1:
            return {
                "match_diagnostic": "official_detail_normalized_ambiguous",
                "review_action": "manual_review_ambiguous_official_detail",
                "review_reason": (
                    "The normalized key maps to zero or multiple official detail codes; "
                    "automatic acceptance requires one unique official target."
                ),
            }
        return {
            "match_diagnostic": "official_detail_normalized_exact",
            "review_action": "accept_normalized_official_detail",
            "review_reason": (
                "The source label has a unique normalized exact match in the official detail catalog."
            ),
        }
    if explicit_alias_matches:
        alias_codes = {
            str(row.get("code") or "").strip()
            for row in explicit_alias_matches
            if str(row.get("code") or "").strip()
        }
        if len(alias_codes) != 1:
            return {
                "match_diagnostic": "verified_explicit_alias_target_ambiguous",
                "review_action": "manual_review_ambiguous_explicit_alias",
                "review_reason": (
                    "The explicit alias target is not one unique official detail code; "
                    "automatic acceptance is blocked."
                ),
            }
        return {
            "match_diagnostic": "verified_explicit_detail_alias",
            "review_action": "accept_verified_explicit_alias",
            "review_reason": (
                "A pre-existing explicit alias resolves to a unique target in the official detail catalog."
            ),
        }
    if is_healthcare_specialized_detail(term):
        if not catalog_checked:
            return {
                "match_diagnostic": "specialized_healthcare_label_unserved_by_mcp",
                "review_action": "manual_review_healthcare_specialized_label",
                "review_reason": (
                    "Healthcare specialized/NCS-like source label was extracted from the JD, "
                    "but current MCP serving DB returned no exact official units; do not "
                    "auto-alias without official catalog evidence."
                ),
            }
        return {
            "match_diagnostic": "specialized_healthcare_label_unserved_by_catalog",
            "review_action": "manual_review_healthcare_specialized_label",
            "review_reason": (
                "Healthcare specialized/NCS-like source label was extracted from the JD, "
                "but the official local detail catalog has no normalized exact match; "
                "do not auto-alias a neighboring healthcare unit."
            ),
        }
    if unit_name_matches:
        if not catalog_checked:
            return {
                "match_diagnostic": "unit_name_only",
                "review_action": "manual_review_unit_name",
                "review_reason": "No exact detail match; suggestion matched a capability unit name only.",
            }
        return {
            "match_diagnostic": "capability_unit_name_not_detail",
            "review_action": "manual_review_unit_name_as_detail",
            "review_reason": (
                "The label is an exact capability-unit name, not an official detail name; "
                "its parent detail requires human confirmation."
            ),
        }
    if catalog_checked and source["source_label_type"] in {
        "declared_self_developed",
        "institution_defined_label",
    }:
        return {
            "match_diagnostic": "self_developed_or_institution_label",
            "review_action": "manual_review_institution_classification",
            "review_reason": (
                "The exact source label is declared self-developed or is in the explicit "
                "institution-label registry; it must not be rewritten as an NCS detail automatically."
            ),
        }
    if canonical_detail_matches:
        if not catalog_checked:
            return {
                "match_diagnostic": "catalog_gap_verified_source_label",
                "review_action": "manual_review_canonical_detail",
                "review_reason": "Suggestion reports the same canonical detail, but exact detail search returned no official units.",
            }
        return {
            "match_diagnostic": "catalog_absent_canonical_suggestion",
            "review_action": "manual_review_canonical_detail",
            "review_reason": (
                "A suggestion reports the same normalized detail name, but the local official "
                "detail catalog has no exact record; keep it unresolved pending catalog review."
            ),
        }
    if normalize_detail_key(term) in MANUAL_REVIEW_SUGGESTIONS_BY_KEY:
        return {
            "match_diagnostic": "known_manual_review_catalog_gap",
            "review_action": "manual_review_known_catalog_gap",
            "review_reason": MANUAL_REVIEW_SUGGESTIONS_BY_KEY[normalize_detail_key(term)],
        }
    if suggestions:
        if not catalog_checked:
            return {
                "match_diagnostic": "semantic_suggestion_unverified",
                "review_action": "manual_review_semantic_suggestion",
                "review_reason": "No exact detail match; semantic suggestions require human confirmation.",
            }
        return {
            "match_diagnostic": "semantic_suggestion_review_required",
            "review_action": "manual_review_semantic_suggestion",
            "review_reason": (
                "The official detail catalog has no normalized exact match; semantic or "
                "similarity suggestions are review hints only."
            ),
        }
    if catalog_checked:
        return {
            "match_diagnostic": "official_detail_catalog_absent",
            "review_action": "manual_review_catalog_absence",
            "review_reason": (
                "No normalized exact detail, verified explicit alias, or exact capability-unit "
                "name was found in the local official catalogs."
            ),
        }
    return {
        "match_diagnostic": "catalog_gap_or_nonstandard_source_label",
        "review_action": "manual_review_no_match",
        "review_reason": "Current MCP index returned no exact units or semantic suggestions for this label.",
    }
