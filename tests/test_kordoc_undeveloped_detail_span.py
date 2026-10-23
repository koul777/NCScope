"""Kordoc 4.24.1 splits an NCS hierarchy whose middle columns are declared
``(현재 NCS 미개발 분야)`` into real grid rows (303961 dev case).

4.9.1 flattened the whole hierarchy into one cell, so the document read as
"no NCS mapping". 4.24.1 keeps the columns: the declaration spans the columns
whose 세분류 cells hold the institution's own job names, e.g. ``(약제 관리)``.
Those names are source-declared non-NCS labels, not official 세분류 candidates
that failed to match. Official names in the other columns stay unchanged.
"""

from __future__ import annotations

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
