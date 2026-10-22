"""Detail candidates must come from detail values, not from prose around them."""

from __future__ import annotations

import pytest

from app.services import kordoc_parser
from app.services.kordoc_parser import structure_job_description


def _details(markdown: str) -> list[str]:
    result = structure_job_description(
        {"markdown": markdown, "blocks": []},
        filename="job.pdf",
    )
    return result["fields"].get("ncs_detail_candidates") or []


def test_note_sentence_using_detail_label_as_a_word_is_not_a_candidate() -> None:
    # An undeveloped-job note names the reference hierarchy in prose:
    # ``... 03. 교통운영/감리 (세분류)의 하위 요소를 참고하여 제시하였습니다.``
    markdown = """<table>
<tr><th>채용분야</th><th colspan="6">교통시스템(정책)(4급)</th></tr>
<tr><td rowspan="2">NCS<br>분류체계</td><td colspan="2">분류코드</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td colspan="6">[NCS 미개발 직무]<br>○ '연구분야'는 현 NCS 개발단계상 미개발 된 직무로, 14. 건설(대분류) → 06. 도시․교통(중분류)→<br>02. 교통계획․설계 (소분류) → 01. 교통계획, 02. 교통설계, 03. 교통운영/감리 (세분류)의 하위 요소를<br>참고하여 제시하였습니다.</td></tr>
<tr><td>직업기초능력</td><td colspan="6">○ 의사소통능력, 수리능력</td></tr>
</table>
"""

    details = _details(markdown)

    assert not any("하위 요소" in detail or "제시하였습니다" in detail for detail in details)


def test_no_mapping_prose_in_detail_cell_is_not_a_candidate() -> None:
    # ``NCS 미개발 직무(세분류)로 별도 분석을 통해 내용 도출`` declares that no
    # detail exists; the words after ``(세분류)`` are not a detail name.
    markdown = """<table>
<tr><td rowspan="4">NCS분류<br>체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td rowspan="2">02.경영•회계•사무</td><td>01.기획사무</td><td>02. 홍보•광고</td><td>01. PR</td></tr>
<tr><td>02.총무•인사</td><td>03. 일반사무</td><td>02.사무행정</td></tr>
<tr><td>05.법률•경찰•소방•교도•국방</td><td>01. 법률</td><td>01. 법무</td><td>NCS 미개발 직무(세분류)로 별도 분석을 통해 내용 도출</td></tr>
<tr><td>전형 방법</td><td colspan="4">□ 서류전형 → 면접시험 → 임용</td></tr>
</table>
"""

    result = structure_job_description(
        {"markdown": markdown, "blocks": []},
        filename="job.hwp",
    )
    fields = result["fields"]
    details = fields.get("ncs_detail_candidates") or []
    self_developed = fields.get("ncs_self_developed_detail_candidates") or []

    assert not any("별도 분석" in value for value in [*details, *self_developed])
    # The real detail cells of the same table are unaffected.
    assert "PR" in details
    assert "사무행정" in details


@pytest.mark.parametrize(
    "value",
    [
        "의 하위 요소를 참고하여 제시하였습니다.",
        "로 별도 분석을 통해 내용 도출",
        "을 참고하여 작성함",
        "해당 직무를 수행합니다",
    ],
)
def test_sentence_fragments_are_not_detail_names(value: str) -> None:
    assert kordoc_parser._is_detail_sentence_fragment(value) is True


@pytest.mark.parametrize(
    "value",
    [
        # Source-declared labels written in a detail cell stay candidates even
        # when they are not current official names; they are shown as
        # unmatched diagnostics downstream.
        "양의학치료",
        "임상 간호 업무",
        "결과도출 및 정책화",
        "운영지원팀",
        "이러닝운영",
        "도시계획",
        "로봇설계",
        "의료기기관리",
        "남북교류 등",
    ],
)
def test_declared_detail_labels_still_look_like_detail_names(value: str) -> None:
    assert kordoc_parser._looks_like_detail_candidate(value) is True
    assert kordoc_parser._is_detail_sentence_fragment(value) is False


@pytest.mark.parametrize(
    "markdown",
    [
        "| 구분 | 대분류 | 중분류 | 소분류 | 세분류 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| NCS | 02.경영·회계·사무 | 02.총무·인사 | 03.일반사무 | 02.사무행정, 위 직무를 수행합니다 |\n",
        "세분류: 사무행정, 위 직무를 수행합니다\n",
    ],
)
def test_prose_after_a_real_detail_drops_only_the_prose(markdown: str) -> None:
    # The fragment rule applies to split items, so a detail cell that also
    # carries a prose sentence keeps its detail name.
    assert _details(markdown) == ["사무행정"]


def test_flattened_hierarchy_detail_after_label_is_still_read() -> None:
    markdown = """<table>
<tr><td>국가NCS</td><td>(대분류)11.경비청소 - (중분류)02.청소 - (소분류)01.청소 - (세분류)01.환경미화</td></tr>
</table>
"""

    assert _details(markdown) == ["환경미화"]
