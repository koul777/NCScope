import pytest

from app.services.kordoc_parser import _split_ability_unit_entries, structure_job_description


@pytest.mark.parametrize("surface", ["html", "html_wrapped", "native", "both"])
def test_ksa_only_incomplete_detail_names_are_diagnostics_without_guessing(surface):
    rows = [["세분류 명", "지식 명", "* NCS 참고"],
            ["지역사회", "업무 지식", ""], ["환경관리", "관련 법령", ""],
            ["사무행정", "문서관리 지식", ""], ["기관 자체분야", "업무 절차", ""]]
    parsed = {}
    if surface == "html_wrapped":
        rows[0] = ["세분류<br>명", "지식<br>명", "* NCS 참고"]
    if surface in {"html", "html_wrapped", "both"}:
        parsed["markdown"] = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows
        ) + "</table>"
    if surface in {"native", "both"}:
        parsed["blocks"] = [{"type": "table", "rows": rows}]
    fields = structure_job_description(parsed)["fields"]
    assert fields["ncs_detail_candidates"] == ["환경관리", "사무행정", "기관 자체분야"]
    assert "지역사회환경관리" not in fields["ncs_detail_candidates"]
    assert {d["text"] for d in fields["ncs_detail_candidate_diagnostics"]} == {"지역사회"}


@pytest.mark.parametrize("surface", ["html", "native", "both"])
def test_ability_row_detail_heading_is_not_an_extra_ability_unit(surface):
    rows = [["세분류", "03. 냉동공조유지보수관리", ""],
            ["능력단위", "냉동공조유지보수관리", "01. 유지보수계획, 02. 에너지관리"]]
    parsed = {}
    if surface in {"html", "both"}:
        parsed["markdown"] = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows
        ) + "</table>"
    if surface in {"native", "both"}:
        parsed["blocks"] = [{"type": "table", "rows": rows}]
    fields = structure_job_description(parsed)["fields"]
    assert fields["ability_units"] == ["유지보수계획", "에너지관리"]
    assert fields["ability_units_by_detail"] == {"냉동공조유지보수관리": ["유지보수계획", "에너지관리"]}


def test_same_name_detail_and_official_ability_stays_in_ability_list():
    fields = structure_job_description({"markdown": """<table>
<tr><td>세분류</td><td>고객관리</td></tr>
<tr><td>능력단위</td><td>고객관리</td><td>01. 고객관리 계획 수립</td></tr>
</table>"""})["fields"]
    assert "고객관리" in fields["ability_units"]


def test_source_declared_institution_ability_is_preserved_for_diagnosis():
    fields = structure_job_description({"markdown": """<table>
<tr><td>세분류</td><td>지반설계</td></tr>
<tr><td>능력단위</td><td>01. 지질재해 현장 기술지원</td></tr>
</table>"""})["fields"]
    assert fields["ability_units"] == ["지질재해 현장 기술지원"]


def test_ksa_rejoined_detail_does_not_remove_an_exact_official_cell():
    fields = structure_job_description({"markdown": """| 세분류 | 건설공사환경관리 |
<table><tr><td>세분류 명</td><td>지식 명</td></tr>
<tr><td>건설공사</td><td>업무 지식</td></tr>
<tr><td>환경관리</td><td>관련 법령</td></tr></table>"""})["fields"]
    assert fields["ncs_detail_candidates"] == ["건설공사환경관리", "환경관리"]


def test_ability_wrap_after_conjunction_remains_one_source_candidate():
    assert [e["text"] for e in _split_ability_unit_entries("프로그램 운영 지원 및\n사무보조")] == [
        "프로그램 운영 지원 및 사무보조",
    ]


def test_ksa_only_fragments_do_not_return_as_contextual_candidates():
    fields = structure_job_description({"markdown": """<table>
<tr><td>세분류 명</td><td>지식 명</td></tr>
<tr><td>지역사회</td><td>업무 지식</td></tr>
<tr><td>위생관리</td><td>관련 법령</td></tr></table>"""})["fields"]
    assert fields["ncs_detail_candidates"] == []
    assert {item["text"] for item in fields["ncs_detail_candidate_diagnostics"]} == {"지역사회", "위생관리"}


@pytest.mark.parametrize("boundary", ["\n\n", "\n• ", "\n01. "])
def test_conjunction_does_not_join_across_paragraph_bullet_or_number(boundary):
    assert len(_split_ability_unit_entries("프로그램 운영 지원 및" + boundary + "사무보조")) == 2
