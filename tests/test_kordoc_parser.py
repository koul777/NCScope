from __future__ import annotations

from app.services.kordoc_parser import _loads_kordoc_json, structure_job_description


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


def test_structure_job_description_extracts_notice_supplement_aliases() -> None:
    markdown = """
담당 예정 업무: 예산 집행 및 사업 일정 관리
공통자격: 관련 분야 실무경력 3년 이상
가산점: 공공기관 사업관리 경험
평가요소: 문제해결능력, 의사소통능력
"""

    result = structure_job_description({"markdown": markdown}, filename="notice.pdf")
    fields = result["fields"]

    assert fields["duties"] == ["예산 집행 및 사업 일정 관리"]
    assert fields["qualifications"] == ["관련 분야 실무경력 3년 이상"]
    assert fields["preferences"] == ["공공기관 사업관리 경험"]
    assert fields["evaluation"] == ["문제해결능력, 의사소통능력"]


def test_notice_evaluation_ignores_document_and_written_exam_sections() -> None:
    markdown = """
### 전형 방법
서류전형
평가 대상: 입사지원자 전원
전형 사항(평가 기준)
항목|비고
응시 요건의 적합성 | 채용 기준의 적합성, 블라인드 채용 기준의 위배 여부 등을 심사함.
직무 수행
요건의 적합성 | 교육, 경력, 자격 요건 등이 채용 분야와 관련성이 있는지 여부를 심사함.
의사소통 및
문제 해결 능력 | 구성원과 원만한 의사소통 역량, 문제 파악 및 해결 방안 제시 능력 등을 심사함.
발전가능성 | 직무역량 개발 계획과 잠재력 등을 심사함.
필기전형
응시 대상: 서류전형 합격자
전형 사항(평가 기준)
직업기초능력평가(NCS) 의사소통능력 15문항
자원관리능력 15문항
문제해결능력 15문항
논술 보고서 작성 능력 1문항
나. 인적성 검사
"""

    result = structure_job_description({"markdown": markdown}, filename="notice.pdf")

    assert result["fields"]["evaluation"] == []


def test_notice_evaluation_keeps_interview_section_only() -> None:
    markdown = """
### 전형 방법
서류전형
전형 사항(평가 기준)
응시 요건의 적합성 | 채용 기준의 적합성
필기전형
직업기초능력평가(NCS) 의사소통능력 15문항
면접전형
전형 사항(평가 기준)
직무역량 | 직무수행에 필요한 지식과 경험
의사소통능력 | 조직 내 협업과 소통 역량
발전가능성 | 직무역량 개발 계획과 잠재력
### 전형 일정
서류 전형: 2025년 11월 초순 예정
필기 전형: 2025년 11월 22일 예정
면접 전형: 2025년 12월 초순 예정
임용 시기: 2026년 1월 1일
최종 합격자에게 개별 통지함.
"""

    result = structure_job_description({"markdown": markdown}, filename="notice.pdf")

    assert result["fields"]["evaluation"] == [
        "직무역량 | 직무수행에 필요한 지식과 경험",
        "의사소통능력 | 조직 내 협업과 소통 역량",
        "발전가능성 | 직무역량 개발 계획과 잠재력",
    ]


def test_notice_evaluation_stops_before_schedule_and_attachment_rows() -> None:
    markdown = """
면접전형
구분 | 전형방법
평가방법(배점)
◦【인성면접】40점
◦【실무면접】30점
※ 세부 전형별 면접위원 평균점수가 40% 이하인 경우 결격 처리
1부.
건설기술관련 가점 인정 학과 범위 1부.
채용서류 반환 청구서 1부. 끝.
면접심사 합격자 발표 | 2026.8.5.(수) 15:00 | 문자 개별통보
면접심사 (2차) | ▪ 응시원서 및 직무수행계획서 등을 바탕으로 한 직무역량 면접
"""

    result = structure_job_description({"markdown": markdown}, filename="notice.pdf")

    assert result["fields"]["evaluation"] == [
        "◦【인성면접】40점",
        "◦【실무면접】30점",
        "※ 세부 전형별 면접위원 평균점수가 40% 이하인 경우 결격 처리",
    ]
