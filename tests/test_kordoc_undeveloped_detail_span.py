"""Kordoc 4.24.1 splits an NCS hierarchy whose middle columns are declared
``(현재 NCS 미개발 분야)`` into real grid rows (303961 dev case).

4.9.1 flattened the whole hierarchy into one cell, so the document read as
"no NCS mapping". 4.24.1 keeps the columns: the declaration spans the columns
whose 세분류 cells hold the institution's own job names, e.g. ``(약제 관리)``.
Those names are source-declared non-NCS labels, not official 세분류 candidates
that failed to match. Official names in the other columns stay unchanged.
"""

from __future__ import annotations

import pytest

from app.services import kordoc_parser
from app.services.kordoc_parser import structure_job_description

# 4.24.1 shape: the 세분류 label spans two rows; the declaration spans seven
# columns in the first row and the own job names sit under it in the second.
_SPLIT_DETAIL_ROW = """
<table>
<tr><th rowspan="2">채용분야</th><th colspan="2">직종</th><th colspan="3">직무</th><th colspan="3">직급</th><th colspan="2">전형</th></tr>
<tr><td colspan="2">심사직</td><td colspan="3">약사</td><td colspan="3">4급</td><td colspan="2">일반</td></tr>
<tr><td rowspan="5">NCS<br>분류체계</td><td>대분류</td><td>02. 경영·<br>회계·사무</td><td colspan="7">06. 보건·의료</td><td>20. 정보통신</td></tr>
<tr><td>중분류</td><td>01. 기획사무</td><td colspan="7">01. 보건</td><td>01. 정보기술</td></tr>
<tr><td>소분류</td><td>03. 마케팅</td><td colspan="7">03. 약무</td><td>07. 인공지능</td></tr>
<tr><td rowspan="2">세분류</td><td rowspan="2">03. 통계조사</td><td colspan="7">(현재 NCS 미개발 분야)</td><td rowspan="2">07. 생성형AI엔지니어링</td></tr>
<tr><td>(약제 관리)</td><td>(약제 기준)</td><td colspan="2">(신약 등재)</td><td>(약가 산정)</td><td colspan="2">(약제 평가)</td></tr>
<tr><td rowspan="2">요건</td><td>연령</td><td colspan="9">만 60세 미만</td></tr>
<tr><td>전공</td><td colspan="9">약학</td></tr>
</table>
"""

# 4.24.1 shape: the declaration spans 대·중·소분류 rows and the own job names
# are the 세분류 cells right under it.
_DECLARATION_ABOVE_DETAIL_ROW = """
<table>
<tr><th rowspan="2">채용분야</th><th colspan="2">직종</th><th colspan="3">직무</th><th colspan="3">직급</th></tr>
<tr><td colspan="2">연구직</td><td colspan="3">연구</td><td colspan="3">연구원</td></tr>
<tr><td rowspan="4">NCS<br>분류체계</td><td>대분류</td><td>02. 경영·<br>회계·사무</td><td colspan="2">20. 정보통신</td><td colspan="3" rowspan="3">(현재 NCS 미개발 분야)</td></tr>
<tr><td>중분류</td><td>01. 기획<br>사무</td><td colspan="2">01. 정보기술</td></tr>
<tr><td>소분류</td><td>03. 마케팅</td><td colspan="2">07. 인공<br>지능</td></tr>
<tr><td>세분류</td><td>03. 통계<br>조사</td><td>05. 빅데이터<br>분석</td><td>07. 빅데이터<br>기획</td><td>(건강보험<br>제도연구)</td><td>(심사·평가<br>제도연구)</td><td>(보건의료<br>제도연구)</td></tr>
<tr><td rowspan="1">요건</td><td>연령</td><td colspan="6">만 60세 미만</td></tr>
</table>
"""


def _fields(markdown: str) -> dict:
    return structure_job_description({"markdown": markdown}, filename="jd.pdf")["fields"]


def _states(fields: dict) -> dict[str, str]:
    return {
        row["detail"]: row["mapping_state"]
        for row in fields["ncs_detail_candidate_evidence"]
    }


def test_own_job_names_under_split_undeveloped_detail_row_are_self_developed() -> None:
    fields = _fields(_SPLIT_DETAIL_ROW)
    own = ["(약제 관리)", "(약제 기준)", "(신약 등재)", "(약가 산정)", "(약제 평가)"]

    assert set(own) <= set(fields["ncs_detail_candidates"])
    assert fields["ncs_self_developed_detail_candidates"] == own
    states = _states(fields)
    assert {name: states[name] for name in own} == {
        name: "source_declared_self_developed" for name in own
    }
    # Nothing outside the declared columns is turned into a non-NCS label.
    assert all(
        states[name] == "requires_official_validation"
        for name in fields["ncs_detail_candidates"]
        if name not in own
    )


def test_own_job_names_below_a_multi_row_undeveloped_declaration_are_self_developed() -> None:
    fields = _fields(_DECLARATION_ABOVE_DETAIL_ROW)
    own = ["(건강보험 제도연구)", "(심사·평가 제도연구)", "(보건의료 제도연구)"]

    assert fields["ncs_self_developed_detail_candidates"] == own
    states = _states(fields)
    official = [name for name in fields["ncs_detail_candidates"] if name not in own]
    assert official
    assert all(states[name] == "requires_official_validation" for name in official)


def test_parenthesized_detail_names_without_undeveloped_declaration_stay_official_candidates() -> None:
    markdown = _SPLIT_DETAIL_ROW.replace("(현재 NCS 미개발 분야)", "06. 약무")

    fields = _fields(markdown)

    assert fields["ncs_self_developed_detail_candidates"] == []
    assert all(
        state == "requires_official_validation" for state in _states(fields).values()
    )


@pytest.mark.parametrize("official_first", [True, False])
def test_same_detail_name_in_official_and_undeveloped_tables_keeps_official_candidate(official_first):
    own = _DECLARATION_ABOVE_DETAIL_ROW.replace("(건강보험<br>제도연구)", "사무행정")
    official = "<table><tr><td>세분류</td><td>02. 사무행정</td></tr></table>"
    fields = _fields(official + own if official_first else own + official)
    assert _states(fields)["사무행정"] == "requires_official_validation"
    assert "사무행정" not in fields["ncs_self_developed_detail_candidates"]


def test_same_detail_name_in_adjacent_official_column_is_not_self_developed():
    markdown = _DECLARATION_ABOVE_DETAIL_ROW.replace("03. 통계<br>조사", "02. 사무행정").replace(
        "(건강보험<br>제도연구)", "사무행정"
    )
    fields = _fields(markdown)
    assert _states(fields)["사무행정"] == "requires_official_validation"
    evidence = next(row for row in fields["ncs_detail_candidate_evidence"] if row["detail"] == "사무행정")
    assert {item["mapping_state"] for item in evidence["occurrences"]} == {
        "requires_official_validation", "source_declared_self_developed",
    }
    assert len({item["value_cell"]["column"] for item in evidence["occurrences"]}) == 2


@pytest.mark.parametrize("label", ["02. 사무행정", "02．사무행정", "02 사무행정"])
def test_numbered_detail_under_undeveloped_span_remains_validation_candidate(label):
    fields = _fields(_DECLARATION_ABOVE_DETAIL_ROW.replace("(건강보험<br>제도연구)", label))
    assert _states(fields)["사무행정"] == "requires_official_validation"
    assert "사무행정" not in fields["ncs_self_developed_detail_candidates"]


def test_undeveloped_span_stops_at_next_hierarchy_even_in_same_table():
    markdown = """<table>
<tr><td>대분류</td><td>NCS 미개발</td></tr>
<tr><td>세분류</td><td>자체업무</td></tr>
<tr><td>대분류</td><td>02. 경영·회계·사무</td></tr>
<tr><td>중분류</td><td>02. 총무·인사</td></tr>
<tr><td>소분류</td><td>03. 일반사무</td></tr>
<tr><td>세분류</td><td>사무행정</td></tr></table>"""
    fields = _fields(markdown)
    assert _states(fields)["자체업무"] == "source_declared_self_developed"
    assert _states(fields)["사무행정"] == "requires_official_validation"


def test_undeveloped_span_stops_at_a_new_detail_label():
    fields = _fields("""<table>
<tr><td>대분류</td><td>NCS 미개발</td></tr>
<tr><td>세분류</td><td>자체업무</td></tr>
<tr><td>세분류</td><td>사무행정</td></tr></table>""")
    assert _states(fields)["자체업무"] == "source_declared_self_developed"
    assert _states(fields)["사무행정"] == "requires_official_validation"


def test_native_only_undeveloped_cell_does_not_taint_same_name_official_column():
    fields = structure_job_description({"blocks": [{"type": "table", "rows": [
        ["대분류", "02. 경영·회계·사무", "NCS 미개발"],
        ["세분류", "02. 사무행정", "사무행정"],
    ]}]})["fields"]
    assert _states(fields)["사무행정"] == "requires_official_validation"
    assert fields["ncs_self_developed_detail_candidates"] == []


@pytest.mark.parametrize("marker", ["NCS 미개발", "자체개발", "기관자체개발"])
@pytest.mark.parametrize("label", ["02. 사무행정", "사무행정"])
def test_adjacent_markdown_declaration_in_snippet_keeps_official_candidate(marker, label):
    fields = _fields(f"| 세분류 | {label} | {marker} |")

    assert "사무행정" in fields["ncs_detail_candidates"]
    evidence = next(row for row in fields["ncs_detail_candidate_evidence"] if row["detail"] == "사무행정")
    assert marker in evidence["snippet"]
    assert evidence["mapping_state"] == "requires_official_validation"
    assert "사무행정" not in fields["ncs_self_developed_detail_candidates"]
    assert all(item["raw_cell_text"] == label for item in evidence["occurrences"])
    assert all(item["value_cell"]["column"] == 1 for item in evidence["occurrences"])
    if marker != "NCS 미개발":
        assert _states(fields)[marker] == "source_declared_self_developed"


@pytest.mark.parametrize("source", ["html", "native"])
@pytest.mark.parametrize("marker", ["NCS 미개발", "자체개발"])
def test_positioned_detail_overrides_declaration_in_broad_evidence_snippet(source, marker):
    rows = [["세분류", "02. 사무행정", marker]]
    parsed = {"blocks": [{"type": "table", "rows": rows}]} if source == "native" else {}
    markdown = "" if source == "native" else (
        f"<table><tr><td>세분류</td><td>02. 사무행정</td><td>{marker}</td></tr></table>"
    )
    evidence = kordoc_parser._detail_candidate_evidence(
        ["사무행정"], {"ncs_detail": [{"text": f"사무행정 {marker}"}]}, markdown, "explicit",
    )

    kordoc_parser._mark_ncs_undeveloped_span_details(evidence, markdown, parsed)

    assert evidence[0]["snippet"] == f"사무행정 {marker}"
    assert evidence[0]["mapping_state"] == "requires_official_validation"
    assert evidence[0]["occurrences"][0]["value_cell"] == {
        "row": 0, "column": 1, "row_span": 1, "column_span": 1,
    }


@pytest.mark.parametrize("unrelated_table", ["", "\n| 항목 | 내용 |\n| 안내 | 기관 소개 |"])
def test_plain_undeveloped_declaration_keeps_no_mapping_diagnostic(unrelated_table):
    declaration = "NCS 미개발 분야: 기관 고유 직무"
    fields = _fields(declaration + unrelated_table)

    assert fields["ncs_detail_candidates"] == []
    assert fields["ncs_self_developed_detail_candidates"] == []
    assert fields["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"
    assert fields["ncs_detail_absence_declared_no_mapping"] is True
    assert declaration in fields["ncs_detail_absence_evidence"]


@pytest.mark.parametrize("layout", [
    "plain", "plain_with_unrelated_table", "markdown", "html", "native", "table_and_plain",
])
def test_explicit_self_developed_detail_keeps_its_own_cell_declaration(layout):
    detail = "(자체개발) 기관 고유 직무"
    inputs = {
        "plain": {"markdown": f"NCS 세분류: {detail}"},
        "plain_with_unrelated_table": {
            "markdown": f"NCS 세분류: {detail}\n| 항목 | 내용 |\n| 안내 | 기관 소개 |",
        },
        "markdown": {"markdown": f"| 세분류 | {detail} |"},
        "html": {"markdown": f"<table><tr><td>세분류</td><td>{detail}</td></tr></table>"},
        "native": {"blocks": [{"type": "table", "rows": [["세분류", detail]]}]},
        "table_and_plain": {"markdown": f"| 세분류 | {detail} |\n\nNCS 세분류: {detail}"},
    }
    fields = structure_job_description(inputs[layout])["fields"]

    assert fields["ncs_detail_candidates"] == [detail]
    assert fields["ncs_self_developed_detail_candidates"] == [detail]
    assert _states(fields)[detail] == "source_declared_self_developed"


@pytest.mark.parametrize("layout", ["html", "markdown"])
@pytest.mark.parametrize("marker", ["NCS 미개발", "자체 개발"])
def test_horizontal_parent_declaration_stays_scoped_to_its_detail_row(layout, marker):
    rows = [
        ["대분류", "중분류", "소분류", "세분류"],
        ["기관분야", "기관업무", f"기관직무 ({marker})", "01. 기관고유업무"],
        ["02. 경영·회계·사무", "02. 총무·인사", "03. 일반사무", "02. 사무행정"],
    ]
    if layout == "html":
        parsed = {"markdown": "<table>" + "".join(
            "<tr>" + "".join(f"<td>{text}</td>" for text in row) + "</tr>" for row in rows
        ) + "</table>"}
    else:
        parsed = {"markdown": "\n".join("| " + " | ".join(row) + " |" for row in rows)}
    fields = structure_job_description(parsed)["fields"]

    assert _states(fields)["기관고유업무"] == "source_declared_self_developed"
    assert fields["ncs_self_developed_detail_candidates"] == ["기관고유업무"]
    assert _states(fields)["사무행정"] == "requires_official_validation"


@pytest.mark.parametrize("marker", ["NCS 미개발", "자체 개발"])
def test_native_horizontal_parent_declaration_stays_scoped_to_its_detail_row(marker):
    parsed = {"blocks": [{"type": "table", "rows": [
        ["대분류", "중분류", "소분류", "세분류"],
        ["기관분야", "기관업무", f"기관직무 ({marker})", "01. 기관고유업무"],
        ["02. 경영·회계·사무", "02. 총무·인사", "03. 일반사무", "02. 사무행정"],
    ]}]}
    evidence = kordoc_parser._detail_candidate_evidence(
        ["기관고유업무", "사무행정"], {}, "", "explicit",
    )

    kordoc_parser._mark_ncs_undeveloped_span_details(evidence, "", parsed)

    assert {row["detail"]: row["mapping_state"] for row in evidence} == {
        "기관고유업무": "source_declared_self_developed",
        "사무행정": "requires_official_validation",
    }


def test_horizontal_merged_parent_declaration_keeps_all_own_detail_candidates():
    fields = _fields("""<table>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td colspan="3">자체 개발</td><td>01. 기관고유업무, 기관지원업무</td></tr>
<tr><td>02. 경영·회계·사무</td><td>02. 총무·인사</td><td>03. 일반사무</td><td>02. 사무행정</td></tr>
</table>""")

    assert fields["ncs_self_developed_detail_candidates"] == ["기관고유업무", "기관지원업무"]
    assert _states(fields)["사무행정"] == "requires_official_validation"


def test_own_cell_undeveloped_declaration_survives_a_clean_candidate_snippet():
    fields = _fields("""<table>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>기관분야</td><td>기관업무</td><td>기관직무</td><td>기관고유업무<br>(NCS 미개발 분야)</td></tr>
</table>""")

    assert fields["ncs_detail_candidates"] == ["기관고유업무"]
    assert fields["ncs_self_developed_detail_candidates"] == ["기관고유업무"]


def test_horizontal_parent_declaration_does_not_taint_same_name_in_official_row():
    fields = _fields("""<table>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td colspan="3">자체 개발</td><td>사무행정</td></tr>
<tr><td>02. 경영·회계·사무</td><td>02. 총무·인사</td><td>03. 일반사무</td><td>02. 사무행정</td></tr>
</table>""")

    assert fields["ncs_detail_candidates"] == ["사무행정"]
    assert fields["ncs_self_developed_detail_candidates"] == []
    assert _states(fields)["사무행정"] == "requires_official_validation"
    evidence = fields["ncs_detail_candidate_evidence"][0]
    assert {item["mapping_state"] for item in evidence["occurrences"]} == {
        "requires_official_validation", "source_declared_self_developed",
    }
