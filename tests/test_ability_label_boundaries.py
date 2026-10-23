"""Minimal source fragments for R1-A; no benchmark/holdout documents."""

import pytest

from app.services.kordoc_parser import (
    _split_ability_unit_entries,
    _split_numbered_ability_units,
    structure_job_description,
)
from app.main import _lock_units_to_reviewed_ability_units
import app.main as main


@pytest.mark.parametrize("separator", [" ", "\n"])
def test_full_unit_codes_separate_adjacent_ability_labels(separator):
    rows = _split_ability_unit_entries(
        "1002020101. 주거용건물 유지관리"
        + separator
        + "1002020103. 주택 안전관리"
    )
    assert [row["text"] for row in rows] == ["주거용건물 유지관리", "주택 안전관리"]
    # A full code must not become a source ordinal or rewrite the source name.
    assert [row["ordinal"] for row in rows] == ["", ""]


def test_full_unit_codes_keep_nonofficial_source_name():
    rows = _split_ability_unit_entries(
        "0202030201. 문서작성 0202030205. 사무행정회계처리"
    )
    assert [row["text"] for row in rows] == ["문서작성", "사무행정회계처리"]


@pytest.mark.parametrize("source", [
    "NCS 0202030201. 문서작성 NCS 0202030202. 문서관리",
    "0202030201_22v3. 문서작성 0202030202_22v3. 문서관리",
])
def test_full_unit_code_prefix_and_version_are_not_labels_or_ordinals(source):
    rows = _split_ability_unit_entries(source)
    assert [row["text"] for row in rows] == ["문서작성", "문서관리"]
    assert [row["ordinal"] for row in rows] == ["", ""]


def test_pdf_inverted_exclamation_bullets_do_not_leak_into_labels():
    rows = _split_ability_unit_entries(
        "¡ (방송콘텐츠제작) 14. 제작계획, 20. 제작편집\n"
        "¡ (영상촬영) 04. 촬영장비 준비, 07. 촬영",
        declared_details=["방송콘텐츠제작", "영상촬영"],
    )
    assert [row["text"] for row in rows] == ["제작계획", "제작편집", "촬영장비 준비", "촬영"]
    assert [row["detail_hint"] for row in rows] == [
        "방송콘텐츠제작", "방송콘텐츠제작", "영상촬영", "영상촬영"
    ]
    assert [row["ordinal"] for row in rows] == ["14", "20", "04", "07"]


def test_pdf_private_use_square_bullets_are_only_removed_at_item_boundaries():
    rows = _split_ability_unit_entries("\uf0fa 환자 교육 및 관리\n\uf0fa 의료\uf09e진료준비")
    assert [row["text"] for row in rows] == ["환자 교육 및 관리", "의료\uf09e진료준비"]


@pytest.mark.parametrize("source,official,code", [
    ("보수‧보강 시공관리", "보수·보강 시공 관리", "1401030108_25v3"),
    ("승객 승･하차지원", "승객 승ㆍ하차지원", "0901010103_15v1"),
    ("승객 승하차 지원", "승객 승ㆍ하차지원", "0901010103_15v1"),
    ("소화기구･수계소화설비점검", "소화기구·수계소화설비점검", "0502010412_23v3"),
])
def test_exact_unit_lock_accepts_equivalent_korean_middle_dot_glyphs(source, official, code):
    # These spellings/codes were verified against the local official NCS MCP.
    unit = {"compeUnitName": official, "ncsClCd": code, "evidence_id": "official-unit"}
    locked, missing = _lock_units_to_reviewed_ability_units([unit], [source])
    assert missing == []
    assert [row["ncsClCd"] for row in locked] == [code]
    assert locked[0]["evidence_id"] == "official-unit"
    assert locked[0]["requiredAbilityUnitName"] == source


def test_middle_dot_normalization_does_not_relax_names_or_code_ambiguity():
    official = [
        {"compeUnitName": "회의 운영", "ncsClCd": "0202030206_22v4"},
        {"compeUnitName": "승객 승ㆍ하차지원", "ncsClCd": "0901010103_15v1"},
        {"compeUnitName": "승객 승·하차지원", "ncsClCd": "0901010203_15v1"},
    ]
    source = ["회의운영·지원", "환자대응관리", "환자서비스관리", "승객 승･하차지원"]
    locked, missing = _lock_units_to_reviewed_ability_units(official, source)
    assert locked == []
    assert missing == source


@pytest.mark.parametrize("source", ["native", "markdown"])
def test_adjacent_table_cells_do_not_create_synthetic_ability_label(source):
    rows = [
        ["세분류", "응용SW엔지니어링", "IT시스템관리"],
        ["능력단위", "요구사항 확인, 프로그래밍 언어 활용", "응용SW 운영관리, NW 운영관리"],
    ]
    if source == "native":
        parsed = {"blocks": [{"type": "table", "pageNumber": 1, "rows": rows}]}
    else:
        parsed = {"markdown": "\n".join("| " + " | ".join(row) + " |" for row in rows)}
    result = structure_job_description(parsed)
    expected = {"요구사항 확인", "프로그래밍 언어 활용", "응용SW 운영관리", "NW 운영관리"}
    assert set(result["fields"]["ability_units"]) == expected
    assert {row["text"] for row in result["sections"]["ability_units"]} == expected


def test_layout_inferred_table_does_not_split_parenthetical_detail_heading():
    markdown = """
세분류: 건설공사품질관리, 건설공사환경관리, 유지관리
능력단위
○ (건설공사품질관리) 01. 품질관리 분석
| ○ ( 건설공사환경관리 | ) 1. 공사환경 특성 파악 | ,2. 환경관련 규정 검토 |
| --- | --- | --- |
| ○ ( 유지관리 | ) 1. 유지관리 계획 수립 ,2. | 유지관리 행정업무 수행 |
"""
    result = structure_job_description({"markdown": markdown})
    assert result["fields"]["ability_units_by_detail"] == {
        "건설공사품질관리": ["품질관리 분석"],
        "건설공사환경관리": ["공사환경 특성 파악", "환경관련 규정 검토"],
        "유지관리": ["유지관리 계획 수립", "유지관리 행정업무 수행"],
    }


def test_comma_delimited_official_label_preserves_layout_wrap_as_whitespace():
    rows = _split_ability_unit_entries(
        "경영평가관련 정보수집, 경영평가활동\n수행, 경영평가 결과보고, "
        "경영평가 모니\n터링, 경영평가 사후관리"
    )
    assert [row["text"] for row in rows] == [
        "경영평가관련 정보수집", "경영평가활동 수행", "경영평가 결과보고",
        "경영평가 모니 터링", "경영평가 사후관리",
    ]


def test_wrapped_labels_keep_source_cell_evidence():
    source = "경영평가활동\n수행, 경영평가 모니\n터링"
    result = structure_job_description({"blocks": [{"type": "table", "rows": [
        ["세분류", "경영평가"], ["능력단위", source],
    ]}]})
    units = [row for row in result["fields"]["positioned_items"] if row["section"] == "ability_units"]
    assert [row["text"] for row in units] == ["경영평가활동 수행", "경영평가 모니 터링"]
    assert all(row["raw_cell_text"] == source for row in units)
    assert all(row["scope"]["ncs_details"] == ["경영평가"] for row in units)


@pytest.mark.parametrize("text,expected", [
    ("회의운영·지원", ["회의운영·지원"]),
    ("환자대응관리, 환자서비스관리", ["환자대응관리", "환자서비스관리"]),
    ("문서작성, 문서관리\n자료관리, 회의운영", ["문서작성", "문서관리", "자료관리", "회의운영"]),
    ("기관 자체\n업무, 환자서비스관리", ["기관 자체", "업무", "환자서비스관리"]),
])
def test_source_names_and_unproven_line_boundaries_are_not_rewritten(text, expected):
    assert [row["text"] for row in _split_ability_unit_entries(text)] == expected


@pytest.mark.parametrize("table_format", ["native", "native_cells", "html", "markdown"])
@pytest.mark.parametrize("blank_line", ["\n\n", "\n \n", "\r\n\r\n"])
def test_structured_table_preserves_blank_ability_boundary(table_format, blank_line):
    source = f"경영평가활동{blank_line}수행, 경영평가 모니\n터링"
    rows = [["세분류", "경영평가"], ["능력단위", source]]
    if table_format == "native":
        parsed = {"blocks": [{"type": "table", "rows": rows}]}
    elif table_format == "native_cells":
        parsed = {"blocks": [{"type": "table", "table": {"cells": [
            [{"text": cell} for cell in row] for row in rows
        ]}}]}
    else:
        rows = [[cell.replace("\r\n", "\n").replace("\n", "<br>") for cell in row] for row in rows]
        if table_format == "html":
            parsed = {"markdown": "<table>" + "".join(
                "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                for row in rows
            ) + "</table>"}
        else:
            parsed = {"markdown": "\n".join("| " + " | ".join(row) + " |" for row in rows)}
    result = structure_job_description(parsed)
    expected = ["경영평가활동", "수행", "경영평가 모니 터링"]
    assert result["fields"]["ability_units"] == expected
    assert [row["text"] for row in result["sections"]["ability_units"]] == expected
    assert result["fields"]["ability_units_by_detail"] == {"경영평가": expected}
    positioned = [row for row in result["fields"]["positioned_items"] if row["section"] == "ability_units"]
    assert [row["text"] for row in positioned] == expected
    assert all("\n\n" in row["raw_cell_text"].replace("\r", "").replace(" ", "") for row in positioned)


def test_blank_line_preservation_does_not_change_other_table_sections():
    rows = [["세분류", "경영평가"], ["필요지식", "평가 계획\n\n평가 결과"]]
    compact_rows = [[label, value.replace("\n\n", "\n")] for label, value in rows]
    assert structure_job_description({"blocks": [{"type": "table", "rows": rows}]}) == structure_job_description(
        {"blocks": [{"type": "table", "rows": compact_rows}]}
    )


def test_html_single_line_wrap_keeps_existing_ability_text():
    result = structure_job_description({"markdown": (
        "<table><tr><td>세분류</td><td>경영평가</td></tr>"
        "<tr><td>능력단위</td><td>기관 자체<br>업무</td></tr></table>"
    )})
    assert result["fields"]["ability_units"] == ["기관 자체 업무"]
    assert [row["text"] for row in result["sections"]["ability_units"]] == ["기관 자체 업무"]


@pytest.mark.parametrize("source,expected", [
    ("경영평가활동\n수행, 경영평가 결과보고",
     ["경영평가활동", "수행", "경영평가 결과보고"]),
    ("경영평가관련 정보수집, 경영평가활동\n수행\n경영평가 결과보고",
     ["경영평가관련 정보수집", "경영평가활동", "수행", "경영평가 결과보고"]),
    ("경영평가관련 정보수집, 경영평가활동\n수행, 기관 자체\n업무",
     ["경영평가관련 정보수집", "경영평가활동", "수행", "기관 자체", "업무"]),
    ("경영평가활동\n\n수행, 경영평가 모니\n터링",
     ["경영평가활동", "수행", "경영평가 모니 터링"]),
])
def test_comma_cell_does_not_join_unproven_item_boundaries(source, expected):
    assert [row["text"] for row in _split_ability_unit_entries(source)] == expected


@pytest.mark.parametrize("source", [
    "고객번호 1234567890. 확인",
    "고객번호1234567890. 확인",
    "고객번호 0202030201. 확인",
    "고객번호 NCS 0202030201. 확인",
])
def test_ten_digit_number_inside_ability_name_is_not_a_code_marker(source):
    assert [row["text"] for row in _split_ability_unit_entries(source)] == [source]


def test_versioned_number_inside_ability_name_is_not_a_code_marker():
    source = "고객번호 1234567890_22v3. 확인"
    assert _split_numbered_ability_units(source) == [{"text": source, "ordinal": ""}]


@pytest.mark.parametrize("separator", [", ", "; ", "\n"])
def test_full_code_marker_after_explicit_item_boundary(separator):
    rows = _split_ability_unit_entries("기관 자체업무" + separator + "0202030201. 문서작성")
    assert [row["text"] for row in rows] == ["기관 자체업무", "문서작성"]


def test_inline_coded_list_keeps_numeric_customer_reference_in_name():
    rows = _split_ability_unit_entries(
        "0202030201. 문서작성 0202030202. 고객번호 1234567890. 확인"
    )
    assert [row["text"] for row in rows] == ["문서작성", "고객번호 1234567890. 확인"]


def test_code_shaped_customer_number_inside_coded_item_is_preserved():
    rows = _split_ability_unit_entries(
        "0202030201. 문서작성 고객번호 0202030205. 확인"
    )
    assert [row["text"] for row in rows] == ["문서작성 고객번호 0202030205. 확인"]


def test_explicit_bullet_line_cannot_be_joined_as_a_wrapped_label():
    rows = _split_ability_unit_entries("경영평가활동\n• 수행, 경영평가 모니\n터링")
    assert [row["text"] for row in rows] == ["경영평가활동", "수행", "경영평가 모니 터링"]


@pytest.mark.parametrize("second_code", ["0901010104_15v1", "0901010203_15v1"])
@pytest.mark.parametrize("source", ["승객 승ㆍ하차지원", "승객 승·하차지원", "승객 승･하차지원"])
def test_code_recovery_rejects_normalized_base_code_collision(second_code, source):
    units = [
        {"compeUnitName": "승객 승ㆍ하차지원", "ncsClCd": "0901010103_15v1"},
        {"compeUnitName": "승객 승·하차지원", "ncsClCd": second_code},
    ]
    for candidates in (units, units[:1], units[1:]):
        recovered, missing = main._recover_code_scoped_reviewed_ability_units(
            units, [source], candidates,
        )
        assert recovered == []
        assert missing == [source]


def test_ordinal_recovery_cannot_select_one_base_of_normalized_name_collision():
    source = "승객 승ㆍ하차지원"
    units = [
        {"compeUnitName": source, "ncsClCd": "0901010103_15v1"},
        {"compeUnitName": "승객 승·하차지원", "ncsClCd": "0901010104_15v1"},
    ]
    ordinals = main._reviewed_ability_unit_ordinals({"positioned_items": [
        {"section": "ability_units", "text": source, "ability_unit_ordinal": "03"},
    ]})
    recovered, missing = main._recover_ordinal_scoped_reviewed_ability_units(
        units, [source], ordinals,
    )
    assert recovered == []
    assert missing == [source]


def test_scope_inference_rejects_same_detail_normalized_base_code_collision():
    source = "승객 승･하차지원"
    scoped, missing = main._scope_reviewed_ability_units_by_exact_detail_membership(
        {"철도운전운영": [
            {"compeUnitName": "승객 승ㆍ하차지원", "ncsClCd": "0901010103_15v1"},
            {"compeUnitName": "승객 승·하차지원", "ncsClCd": "0901010104_15v1"},
        ]}, [source],
    )
    assert scoped == {}
    assert missing == [source]


def test_ability_name_and_ordinal_keys_share_middle_dot_normalization():
    source = "승객 승ㆍ하차지원"
    equivalent = "승객 승･하차지원"
    assert main._reviewed_ability_unit_names([source, equivalent]) == [source]
    ordinals = main._reviewed_ability_unit_ordinals({"positioned_items": [
        {"section": "ability_units", "text": source, "ability_unit_ordinal": "03"},
        {"section": "ability_units", "text": equivalent, "ability_unit_ordinal": "04"},
    ]})
    assert ordinals == {main._norm_ability_coverage_key(source): ["03", "04"]}


@pytest.mark.parametrize("route", ["ordinal", "code"])
def test_recovery_accepts_equivalent_dot_and_versions_of_one_base(route):
    source = "승객 승･하차지원"
    units = [
        {"compeUnitName": "승객 승ㆍ하차지원", "ncsClCd": "0901010103_15v1", "evidence_id": "v1"},
        {"compeUnitName": "승객 승·하차지원", "ncsClCd": "0901010103_22v2", "evidence_id": "v2"},
    ]
    if route == "ordinal":
        recovered, missing = main._recover_ordinal_scoped_reviewed_ability_units(
            units, [source], {main._norm_ability_coverage_key(source): ["03"]},
        )
    else:
        recovered, missing = main._recover_code_scoped_reviewed_ability_units(
            units, [source], units,
        )
    assert missing == []
    assert [row["ncsClCd"] for row in recovered] == [row["ncsClCd"] for row in units]
    assert [row["evidence_id"] for row in recovered] == ["v1", "v2"]
