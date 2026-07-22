from __future__ import annotations

from app.services.kordoc_parser import _loads_kordoc_json, structure_job_description, structure_job_notice


def test_structure_job_description_extracts_detail_from_html_table() -> None:
    markdown = """
<table>
<tr><td rowspan="5">분류체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>사업관리</td><td>사업관리</td><td>프로젝트관리</td><td>프로젝트관리</td></tr>
<tr><td rowspan="2">정보통신</td><td rowspan="2">정보기술</td><td>정보기술전략·계획</td><td>정보기술전략</td></tr>
<tr><td>정보기술기획</td></tr>
<tr><td>기관주요업무</td><td colspan="4">ICT R&D 기술기획</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "프로젝트관리",
        "정보기술전략",
        "정보기술기획",
    ]


def test_loads_kordoc_json_recovers_after_stdout_warning() -> None:
    raw = 'Warning: Required "glyf" table is not found -- trying to recover.\n{"success": true, "markdown": "ok"}'

    result = _loads_kordoc_json(raw)

    assert result == {"success": True, "markdown": "ok"}


def test_structure_job_notice_extracts_duty_and_evaluation_windows() -> None:
    markdown = """
## 채용분야
경영기획 담당자 1명

## 담당업무
- 경영계획 수립 및 사업성과 분석
- 예산 운영 지원과 대내외 보고자료 작성

## 면접전형 평가항목
- 문제해결능력
- 의사소통능력
- 청렴성 및 조직적합도

## 기타사항
최종합격자는 임용 후 배치 예정
"""

    result = structure_job_notice({"markdown": markdown}, filename="notice.txt")

    assert "경영계획 수립" in result["fields"]["duty_text"]
    assert "문제해결능력" in result["fields"]["evaluation_text"]
    assert "기타사항" not in result["fields"]["evaluation_text"]


def test_structure_job_description_filters_detail_label_noise() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 세분류 | 원자력발전설비운영 |
| 능력단위 | 원자력 발전설비 운전 |
| 주요사업 | 원자력 발전 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["원자력발전설비운영"]


def test_structure_job_description_cleans_detail_candidate_punctuation() -> None:
    markdown = """
| 세분류 | 영상의학 (특화분류) | 임상병리 (특화분류) | 간호업무 보조/ | 재원환자 관리, |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "영상의학",
        "임상병리",
        "간호업무 보조",
        "재원환자 관리",
    ]
