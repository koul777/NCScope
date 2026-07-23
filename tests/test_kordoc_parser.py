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


def test_structure_job_notice_prefers_interview_part_inside_selection_method() -> None:
    markdown = """
### 전형 방법

◦ 서류전형
- 평가 대상: 입사지원자 전원
- 전형 사항(평가 기준)
|항목|비고|
| --- | --- |
| 응시 요건의 적합성 | 채용 기준의 적합성, 블라인드 채용 기준의 위배 여부 등을 심사함. |
| 직무 수행 요건의 적합성 | 교육, 경력, 자격 요건 등이 채용 분야와 관련성이 있는지 여부를 심사함. |

◦ 필기전형
- 응시 대상: 서류전형 합격자(채용 예정 인원의 30배수 이내)
- 전형 사항(평가 기준)
- 가. 직업기초능력평가(NCS) 및 논술(1page보고서 작성)
|과 목|세부 내용|문항|비고|
| --- | --- | --- | --- |
| 직업기초능력평가(NCS) | 의사소통능력 | 15문항 | 60점 만점 |
| 직업기초능력평가(NCS) | 자원관리능력 | 15문항 | 60점 만점 |
- 나. 인적성 검사
인적성검사 결과는 필기전형 합격자의 면접전형 참고자료로만 활용함.
단, 인적성검사에 응시하지 않을 경우 면접전형 응시 불가

◦ 면접전형
- 응시 대상: 필기전형 합격자(채용 예정 인원의 5배수 이내)
- 면접 불참자는 불합격 처리함
- 전형 사항(평가 기준)
|항목|비고|
| --- | --- |
| 직무 역량 | 직무에 대한 이해도, 직무 수행에 필요한 전문지식, 창의력, 상황 대처 능력 등을 심사함. |
| 인성 및 자질 | 공직자로서 정신 자세, 인성, 태도, 표현력 등을 심사함. |

### 응시 원서 접수 및 전형 일정
- 면접 전형: 2025년 12월 초순 예정
"""

    result = structure_job_notice({"markdown": markdown}, filename="notice.txt")
    evaluation = result["fields"]["evaluation_text"]

    assert evaluation.startswith("◦ 면접전형")
    assert "직무 역량" in evaluation
    assert "인성 및 자질" in evaluation
    assert "서류전형" not in evaluation
    assert "직업기초능력평가" not in evaluation
    assert "응시 원서 접수" not in evaluation


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


def test_structure_job_description_cleans_html_table_detail_candidate_punctuation() -> None:
    markdown = """
<table>
<tr><td>세분류</td><td>영상의학 (특화분류)</td><td>임상병리 (특화분류)</td><td>간호업무 보조/</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "영상의학",
        "임상병리",
        "간호업무 보조",
    ]
