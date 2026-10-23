from __future__ import annotations

import pytest

from app.services.kordoc_parser import structure_job_description
from app.services.ncs_mcp_client import classify_official_ability_unit_names


def _merged_detail_table(
    detail_cell: str,
    adjacent_cell: str = "02. 사무행정",
    *,
    context: str = "분류체계",
    units: str = "문서관리, 비품관리",
) -> str:
    return f"""
<table>
<tr><th rowspan="4">{context}</th><th>대분류</th><td colspan="4">02. 경영·회계·사무</td></tr>
<tr><td>소분류</td><td colspan="2">01. 총무</td><td colspan="2">03. 일반사무</td></tr>
<tr><td colspan="3">{detail_cell}</td><td colspan="2">{adjacent_cell}</td></tr>
<tr><td>능력단위</td><td colspan="4">{units}</td></tr>
</table>
"""


def _fields(markdown: str) -> dict:
    return structure_job_description({"markdown": markdown}, filename="scope.pdf")["fields"]


def test_merged_detail_label_restores_unique_exact_ability_scope() -> None:
    fields = _fields(_merged_detail_table("세분류 01. 총무"))

    assert fields["ncs_detail_candidates"] == ["총무", "사무행정"]
    states = classify_official_ability_unit_names(
        ["문서관리", "비품관리"],
        selected_detail_names=fields["ncs_detail_candidates"],
    )
    assert [row["mappingState"] for row in states] == ["official_exact_source_scoped"] * 2
    assert states[0]["resolvedUnitCodes"] == ["0202030202_22v3"]
    assert states[1]["resolvedUnitCodes"] == ["0202010104_25v3"]
    assert all(row["automaticSemanticMappingAllowed"] is False for row in states)


def test_merged_detail_label_keeps_all_numbered_official_names_and_adjacent_cell() -> None:
    fields = _fields(_merged_detail_table("세분류 01. 총무, 01. 인사"))

    assert fields["ncs_detail_candidates"] == ["총무", "인사", "사무행정"]
    assert all(row["snippet"] for row in fields["ncs_detail_candidate_evidence"])


def test_merged_detail_label_does_not_override_real_unit_scope_conflict() -> None:
    fields = _fields(_merged_detail_table("세분류 02. 사무행정", adjacent_cell=""))

    assert fields["ncs_detail_candidates"] == ["사무행정"]
    state = classify_official_ability_unit_names(
        ["비품관리"], selected_detail_names=fields["ncs_detail_candidates"]
    )[0]
    assert state["mappingState"] == "official_exact_scope_conflict"
    assert state["resolvedUnitCodes"] == []


@pytest.mark.parametrize(
    ("detail_cell", "adjacent_cell", "context"),
    [
        ("세분류 01. 총무", "02. 사무행정", "참고자료"),
        ("세분류 01. 총무 지원", "", "분류체계"),
        ("세분류 01. 총무, 02. 기관자체분류", "", "분류체계"),
        ("세분류 01. 총무", "현재 NCS에 Mapping 가능한 직무가 없어 별도 분석", "분류체계"),
        ("세분류 관련 업무는 01. 총무", "", "분류체계"),
        ("세분류 01. 문서관리", "", "분류체계"),
        ("세분류 01. 한식·중식조리", "", "분류체계"),
        ("세분류 01. 총무 등", "", "분류체계"),
        ("세분류 01. 총무 ○ 비품관리", "", "분류체계"),
    ],
)
def test_merged_detail_recovery_requires_classification_and_exact_labels(
    detail_cell: str, adjacent_cell: str, context: str
) -> None:
    fields = _fields(_merged_detail_table(detail_cell, adjacent_cell, context=context))

    assert fields["ncs_detail_candidates"] == []


def test_missing_source_detail_keeps_common_official_unit_ambiguous() -> None:
    state = classify_official_ability_unit_names(["문서관리"], selected_detail_names=[])[0]

    assert state["mappingState"] == "official_exact_detail_ambiguous"
    assert state["resolvedUnitCodes"] == []
    assert state["reviewRequired"] is True


def test_multiple_source_details_keep_common_official_unit_ambiguous() -> None:
    state = classify_official_ability_unit_names(
        ["문서관리"], selected_detail_names=["사무행정", "화학물질분석"]
    )[0]

    assert state["mappingState"] == "official_exact_detail_ambiguous"
    assert state["resolvedUnitCodes"] == []
    assert state["reviewRequired"] is True
