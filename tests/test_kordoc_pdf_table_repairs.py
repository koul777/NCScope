from copy import deepcopy

import pytest

from app.services.kordoc_parser import _split_ability_unit_entries, structure_job_description


def _cell(text, **spans):
    return {"text": text, "colSpan": 1, "rowSpan": 1, **spans}


def _detached_classification(details):
    headers = [
        ("소분류(직무명,Job)", 235), ("세분류", 324),
        ("구분\t대분류", 54), ("중분류", 178), ("직무정의", 448),
    ]
    blocks = [
        {"type": "paragraph", "text": label, "pageNumber": 1,
         "bbox": {"x": x, "y": 708, "width": 60, "height": 6}}
        for label, x in headers
    ]
    rows = [[_cell(s) for s in ["자체기준", "내부", "내부", "자체업무", "내부직무", "업무 설명"]]]
    for index, detail in enumerate(details):
        rows.append([
            _cell("NCS기준" if not index else "", rowSpan=len(details) if not index else 1),
            _cell("02. 경영·회계·사무" if not index else "", rowSpan=len(details) if not index else 1),
            _cell("01. 기획사무"), _cell("01. 경영기획"), _cell(detail), _cell(""),
        ])
    blocks.append({
        "type": "table", "pageNumber": 1,
        "bbox": {"x": 40, "y": 634, "width": 510, "height": 70},
        "table": {"cols": 6, "rows": len(rows), "cells": rows, "hasHeader": True},
    })
    return {"blocks": blocks, "markdown": "\n\n".join(label for label, _ in headers)}


@pytest.mark.parametrize("details,expected", [
    (["01. 경영기획", "05. 물류기획", "06. 운송관리"], ["경영기획", "물류기획", "운송관리"]),
    (["02. 경영평가", "01. 인사"], ["경영평가", "인사"]),
    (["00. 산업안전관리 공통직무", "01. 산업보건관리", "02. 근로자작업환경관리"],
     ["산업안전관리 공통직무", "산업보건관리", "근로자작업환경관리"]),
    (["01. 경영기획"], ["경영기획"]),
])
def test_detached_pdf_hierarchy_header_recovers_only_ncs_rows(details, expected):
    parsed = _detached_classification(details)
    original = deepcopy(parsed)
    result = structure_job_description(parsed)
    assert result["fields"]["ncs_detail_candidates"] == expected
    positioned = [item for item in result["fields"]["positioned_items"] if item["section"] == "ncs_detail"]
    assert [item["text"] for item in positioned] == expected
    assert all(item["value_cell"]["column"] == 4 for item in positioned)
    assert all(item["page"] == 1 and item["label"] == "세분류" for item in positioned)
    assert parsed == original


@pytest.mark.parametrize("damage", ["other_page", "missing_header", "distant_header", "no_ncs_marker", "missing_page"])
def test_detached_pdf_header_requires_complete_local_ncs_evidence(damage):
    parsed = _detached_classification(["01. 경영기획"])
    if damage == "other_page":
        parsed["blocks"][1]["pageNumber"] = 2
    elif damage == "missing_header":
        parsed["blocks"].pop(3)
    elif damage == "distant_header":
        parsed["blocks"][1]["bbox"]["y"] = 770
    elif damage == "missing_page":
        for block in parsed["blocks"]:
            block.pop("pageNumber")
    else:
        parsed["blocks"][-1]["table"]["cells"][1][0]["text"] = "자체기준"
    assert structure_job_description(parsed)["fields"]["ncs_detail_candidates"] == []


def test_detached_training_header_keeps_exact_code_name_evidence():
    parsed = _detached_classification(["01. 경영기획"])
    parsed["blocks"].append({
        "type": "paragraph", "text": "필요교육", "pageNumber": 1,
        "bbox": {"x": 448, "y": 326, "width": 23, "height": 6},
    })
    for index, (code, name) in enumerate([
        ("0201010101_22v3", "01.사업환경 분석"),
        ("0201010102_22v3", "02.경영방침수립"),
        ("0201010103_22v3", "03.기관 자체업무"),
    ]):
        parsed["blocks"].append({
            "type": "table", "pageNumber": 1,
            "bbox": {"x": 368, "y": 311 - 10 * index, "width": 182, "height": 10},
            "table": {"cols": 2, "cells": [[_cell(code), _cell(name)]]},
        })
    result = structure_job_description(parsed)
    assert result["fields"]["ability_units_by_detail"] == {
        "경영기획": ["사업환경 분석", "경영방침 수립"],
    }
    units = [item for item in result["fields"]["positioned_items"] if item["section"] == "ability_units"]
    assert [item["source_unit_code"] for item in units] == ["0201010101_22v3", "0201010102_22v3"]


@pytest.mark.parametrize("source,expected", [
    ("IT프로젝트 일정관리,\nIT프로젝트 변경관리,\nIT프로젝트 정보시스템\n구축관리",
     ["IT프로젝트 일정관리", "IT프로젝트 변경관리", "IT프로젝트 정보시스템 구축관리"]),
    ("사업환경 분석, 경영계획 수립, 경영실적\n분석",
     ["사업환경 분석", "경영계획 수립", "경영실적 분석"]),
])
def test_final_comma_list_item_wrap_has_two_complete_preceding_items(source, expected):
    assert [entry["text"] for entry in _split_ability_unit_entries(source)] == expected


def test_numbered_parenthetical_detail_in_table_is_scope_not_unit_prefix():
    result = structure_job_description({"markdown": """
<table>
<tr><td>세분류</td><td>기계안전관리</td><td>건설안전관리</td></tr>
<tr><td>능력단위</td><td colspan="2">(01. 기계안전관리)<br>
03. 기계작업공정 특성 분석, 14. 기계안전시설 관리 23. 기계안전점검,<br>
(03. 건설안전관리)<br>20. 건설현장 유해·위험요인관리, 21. 건설현장 안전사고 예방</td></tr>
</table>
"""})
    assert result["fields"]["ability_units_by_detail"] == {
        "기계안전관리": ["기계작업공정 특성 분석", "기계안전시설 관리", "기계안전점검"],
        "건설안전관리": ["건설현장 유해·위험요인관리", "건설현장 안전사고 예방"],
    }
    assert len(result["fields"]["ability_units"]) == 5


def test_parenthetical_ability_headings_do_not_turn_elements_into_unit_names():
    result = structure_job_description({"blocks": [{"type": "table", "rows": [
        ["세분류", "병원안내"],
        ["능력단위", "○ (고객응대관리) 고객진료정보 파악, 대기고객 관리하기, 고객 배웅하기\n"
         "○ (고객상담관리) 고객 유형별 매뉴얼에 따라 응대\n"
         "○ (진료서비스지원관리) 진료대기시간 관리하기, 진료 전 설명하기"],
    ]}]})
    assert result["fields"]["ability_units"] == ["고객응대관리", "고객상담관리", "진료서비스지원관리"]
    assert result["fields"]["ability_units_by_detail"] == {
        "병원안내": ["고객응대관리", "고객상담관리", "진료서비스지원관리"],
    }


@pytest.mark.parametrize("offset,expected", [(0, ["경영기획"]), (100, [])])
def test_hierarchy_header_detached_into_an_aligned_table(offset, expected):
    parsed = _detached_classification(["01. 경영기획"])
    parsed["blocks"] = [{
        "type": "table", "pageNumber": 1,
        "bbox": {"x": 40 + offset, "y": 707, "width": 510, "height": 73},
        "table": {"cols": 6, "cells": [
            [_cell("직무분류", colSpan=6), *[_cell("") for _ in range(5)]],
            [_cell(""), _cell("구분 대분류 중분류 소분류(직무명,Job) 세분류"),
             _cell(""), _cell(""), _cell("직무정의"), _cell("")],
        ]},
    }, parsed["blocks"][-1]]
    assert structure_job_description(parsed)["fields"]["ncs_detail_candidates"] == expected


@pytest.mark.parametrize("surface", ["html", "native", "both"])
def test_fragmented_ksa_detail_column_requires_independent_complete_label(surface):
    rows = [
        ["세분류 명", "지식 명", "", "* NCS 참고"],
        ["지역사회", "원료 선택 방법", "", ""],
        ["위생관리", "포장과 보관 기술", "", ""],
        ["식품가공", "미생물의 생장조건", "", ""],
        ["연구개발", "예방 대책", "", ""],
        ["기관 자체분야", "기관의 업무 절차", "", ""],
    ]
    parsed = {"markdown": "| NCS 세분류 | 능력단위명칭 |\n| --- | --- |\n"
              "| 지역사회위생관리 | 식품위생관리 |\n| 식품가공연구개발 | 공정설정 |\n"}
    if surface in {"html", "both"}:
        parsed["markdown"] += "<table>" + "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
        ) + "</table>"
    if surface in {"native", "both"}:
        parsed["blocks"] = [{"type": "table", "rows": rows}]
    result = structure_job_description(parsed)
    assert result["fields"]["ncs_detail_candidates"] == [
        "지역사회위생관리", "식품가공연구개발", "기관 자체분야",
    ]


def test_fragmented_ksa_column_keeps_independently_declared_fragments():
    parsed = {"markdown": """
| NCS 세분류 | 능력단위명칭 |
| --- | --- |
| 지역사회위생관리 | 식품위생관리 |
| 위생관리 | 기관 자체업무 |
<table><tr><td>세분류 명</td><td>기술 명 * NCS 참고</td></tr>
<tr><td>지역사회</td><td>원료 선택</td></tr>
<tr><td>위생관리</td><td>보관 기술</td></tr></table>
"""}
    assert structure_job_description(parsed)["fields"]["ncs_detail_candidates"] == [
        "지역사회위생관리", "위생관리",
    ]


def test_fragmented_ksa_column_does_not_guess_similar_complete_name():
    parsed = {"markdown": """
| 세분류 | 지역사회위생관리 |
<table><tr><td>세분류 명</td><td>지식 명 * NCS 참고</td></tr>
<tr><td>지역사회</td><td>원료 선택</td></tr>
<tr><td>환경관리</td><td>보관 기술</td></tr></table>
"""}
    assert structure_job_description(parsed)["fields"]["ncs_detail_candidates"] == [
        "지역사회위생관리", "지역사회", "환경관리",
    ]


@pytest.mark.parametrize("source", [
    "○ (병원안내) 고객응대관리\n○ (병원안내) 고객상담관리",
    "○ (기관 자체분야) 고객응대관리\n○ (기관 자체분야) 고객상담관리",
])
def test_parenthetical_detail_or_unknown_subgroup_is_not_an_ability_heading(source):
    entries = _split_ability_unit_entries(source)
    assert [entry["text"] for entry in entries] == ["고객응대관리", "고객상담관리"]


@pytest.mark.parametrize("source", [
    "사업환경 분석, 경영계획 수립, 경영실적\n\n분석",
    "사업환경 분석, 경영계획 수립, 경영실적\n• 분석",
])
def test_final_comma_item_does_not_cross_paragraph_or_bullet_boundary(source):
    assert [entry["text"] for entry in _split_ability_unit_entries(source)] == [
        "사업환경 분석", "경영계획 수립", "경영실적", "분석",
    ]


def test_ksa_fragments_cannot_join_across_an_empty_table_row():
    parsed = {"markdown": """
| 세분류 | 지역사회위생관리 |
<table><tr><td>세분류 명</td><td>지식 명</td></tr>
<tr><td>지역사회</td><td>원료 선택</td></tr>
<tr><td></td><td></td></tr>
<tr><td>위생관리</td><td>보관 기술</td></tr></table>
"""}
    assert structure_job_description(parsed)["fields"]["ncs_detail_candidates"] == [
        "지역사회위생관리", "지역사회", "위생관리",
    ]
