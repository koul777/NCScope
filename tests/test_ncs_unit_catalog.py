from __future__ import annotations

import json
import re
from pathlib import Path

from app.services import ncs_mcp_client


ROOT = Path(__file__).resolve().parents[1]
UNIT_CATALOG_PATH = ROOT / "app" / "data" / "ncs_unit_catalog.json"
DETAIL_CATALOG_PATH = ROOT / "app" / "data" / "ncs_detail_catalog.json"


def test_lightweight_unit_catalog_covers_every_source_unit_code() -> None:
    unit_payload = json.loads(UNIT_CATALOG_PATH.read_text(encoding="utf-8"))
    detail_payload = json.loads(DETAIL_CATALOG_PATH.read_text(encoding="utf-8"))
    rows = unit_payload["units"]
    codes = [row["code"] for row in rows]
    valid_detail_codes = {row["code"] for row in detail_payload["details"]}

    assert unit_payload["schema_version"] == 1
    assert unit_payload["unit_count"] == len(rows)
    assert len(rows) == 13_282
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"\d{10}_[0-9A-Za-z]+", code) for code in codes)
    assert all(row["code"].startswith(row["detail_code"]) for row in rows)
    assert all(row["detail_code"] in valid_detail_codes for row in rows)


def test_exact_unit_catalog_lookup_is_normalized_but_not_semantic() -> None:
    ncs_mcp_client._official_units_by_name_key.cache_clear()

    rows = ncs_mcp_client.exact_official_units_by_name(["행사 지원 관리"])

    assert any(row["ncsClCd"] == "0202010102_25v3" for row in rows)
    assert all(row["compeUnitName"] == "행사지원관리" for row in rows)
    assert all(row["source"] == "ncs-unit-catalog-exact" for row in rows)
    assert ncs_mcp_client.exact_official_units_by_name(["기관 자체 행사 지원 업무"]) == []


def test_exact_unit_catalog_normalizes_legacy_hangul_middle_dot_variant() -> None:
    ncs_mcp_client._official_units_by_name_key.cache_clear()

    rows = ncs_mcp_client.exact_official_units_by_name(["승객 승･하차지원"])

    assert [row["ncsClCd"] for row in rows] == ["0901010103_15v1"]
    assert rows[0]["compeUnitName"] == "승객 승ㆍ하차지원"


def test_inactive_detail_units_are_not_available_as_current_official() -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()
    ncs_mcp_client._official_units_by_name_key.cache_clear()

    assert ncs_mcp_client.exact_official_units_by_name(["가동전 준비"]) == []


def test_remote_mcp_cannot_reintroduce_inactive_detail_units(mocker) -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()
    ncs_mcp_client._active_official_detail_codes.cache_clear()
    inactive_row = {
        "id": "1702000101_22v1",
        "text": "가동전 준비",
        "path": {
            "small": "석유·기초화학물",
            "sub": "석유화학공정운전",
        },
    }
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [inactive_row]},
    )

    assert ncs_mcp_client.search_units_by_detail(["석유화학공정운전"]) == []
    assert ncs_mcp_client.suggest_units_by_text(["가동전 준비"]) == []


def test_detail_catalog_classification_preserves_unmapped_and_self_developed() -> None:
    rows = ncs_mcp_client.classify_official_detail_names(
        ["총무", "기업홍보", "기관 자체개발"],
        self_developed_names=["기관 자체개발"],
    )

    assert rows[0]["mappingState"] == "official_current_exact"
    assert rows[0]["officialDetailCodes"] == ["02020101"]
    assert rows[1]["mappingState"] == "not_in_current_official_catalog"
    assert rows[1]["officialDetailCodes"] == []
    assert rows[2]["mappingState"] == "source_declared_self_developed"
    assert all(row["automaticSemanticMappingAllowed"] is False for row in rows)


def test_detail_catalog_resolves_only_verified_ordinal_unit_decoration() -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()
    ncs_mcp_client._official_units_by_name_key.cache_clear()

    resolved = ncs_mcp_client.classify_official_detail_names(
        ["외식운영관리 (02.식자재관리)"]
    )[0]

    assert resolved["sourceName"] == "외식운영관리 (02.식자재관리)"
    assert resolved["mappingState"] == "official_current_exact"
    assert resolved["officialDetailNames"] == ["외식운영관리"]
    assert resolved["officialDetailCodes"] == ["13010301"]
    assert resolved["catalogExact"] is False
    assert resolved["resolvedCatalogExact"] is True
    assert resolved["matchMethod"] == (
        "official_detail_with_exact_ordinal_unit_decoration"
    )
    assert resolved["automaticSemanticMappingAllowed"] is False
    assert resolved["reviewRequired"] is True


def test_detail_catalog_does_not_strip_unverified_parenthetical_labels() -> None:
    values = [
        "비서 (글로벌경영사무 지원)",
        "사무행정(기록물)",
        "외식운영관리 (식자재관리)",
        "외식운영관리 (02.식자재관리, 03.위생관리)",
        "외식운영관리 ((02.식자재관리))",
        "외식운영관리 (03.식자재관리)",
    ]

    rows = ncs_mcp_client.classify_official_detail_names(values)

    assert [row["mappingState"] for row in rows] == [
        "not_in_current_official_catalog"
    ] * len(values)
    assert all(row["resolvedCatalogExact"] is False for row in rows)


def test_ability_catalog_classification_distinguishes_scope_states() -> None:
    scoped = ncs_mcp_client.classify_official_ability_unit_names(
        ["행사지원관리"],
        selected_detail_names=["총무"],
    )[0]
    derived = ncs_mcp_client.classify_official_ability_unit_names(
        ["행사지원관리"],
        selected_detail_names=[],
    )[0]
    conflict = ncs_mcp_client.classify_official_ability_unit_names(
        ["행사지원관리"],
        selected_detail_names=["사무행정"],
    )[0]
    unmapped = ncs_mcp_client.classify_official_ability_unit_names(
        ["기관 자체 행사 지원 업무"],
        selected_detail_names=["총무"],
    )[0]

    assert scoped["mappingState"] == "official_exact_source_scoped"
    assert scoped["resolvedUnitCodes"] == ["0202010102_25v3"]
    assert scoped["reviewRequired"] is False
    assert derived["mappingState"] == "official_exact_derived_scope_review_required"
    assert derived["resolvedUnitCodes"] == ["0202010102_25v3"]
    assert derived["reviewRequired"] is True
    assert conflict["mappingState"] == "official_exact_scope_conflict"
    assert conflict["resolvedUnitCodes"] == []
    assert unmapped["mappingState"] == "not_in_current_official_catalog"


def test_exact_unit_convergence_suggests_review_only_detail_alias() -> None:
    rows = ncs_mcp_client.derive_detail_candidates_from_exact_ability_scopes(
        {
            "문화유산보존": [
                "문화유산 보존 계획",
                "문화유산 분석 조사",
                "기관 자체 설명",
            ]
        }
    )

    assert len(rows) == 1
    assert rows[0]["officialDetailCode"] == "08010403"
    assert rows[0]["officialDetailName"] == "문화재보존"
    assert rows[0]["distinctExactUnitCount"] == 2
    assert rows[0]["automaticMappingAllowed"] is False
    assert rows[0]["reviewRequired"] is True


def test_exact_unit_convergence_rejects_single_or_mixed_detail_votes() -> None:
    assert (
        ncs_mcp_client.derive_detail_candidates_from_exact_ability_scopes(
            {"기관 자체분류": ["행사지원관리"]}
        )
        == []
    )
    assert (
        ncs_mcp_client.derive_detail_candidates_from_exact_ability_scopes(
            {"기관 자체분류": ["행사지원관리", "문서작성"]}
        )
        == []
    )
    assert (
        ncs_mcp_client.derive_detail_candidates_from_exact_ability_scopes(
            {"총무": ["행사지원관리", "비품관리"]}
        )
        == []
    )
