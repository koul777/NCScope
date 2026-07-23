from __future__ import annotations

from app.services.kordoc_parser import _loads_kordoc_json, structure_job_description, structure_job_notice


def _evidence_by_detail(result: dict) -> dict[str, dict]:
    return {
        row["detail"]: row
        for row in result["fields"].get("ncs_detail_candidate_evidence", [])
    }


def _assert_contextual_evidence_uses_source_snippet(result: dict) -> None:
    candidates = result["fields"]["ncs_detail_candidates"]
    evidence_rows = result["fields"]["ncs_detail_candidate_evidence"]

    assert len(evidence_rows) == len(candidates)
    for candidate, evidence in zip(candidates, evidence_rows):
        assert evidence["detail"] == candidate
        assert evidence["source"] == "contextual"
        assert evidence["snippet"]
        assert evidence["snippet"] != candidate


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


def test_structure_job_description_merges_detail_from_kordoc_table_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "구분"}, {"text": "내용"}],
                    [{"text": "NCS 세분류명"}, {"text": "사무행정"}],
                    [{"text": "담당업무"}, {"text": "문서 접수 및 보고자료 작성"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
    assert result["fields"]["ncs_detail_source"] == "explicit"
    assert result["fields"]["ncs_detail_candidate_evidence"][0]["detail"] == "사무행정"
    assert result["fields"]["ncs_detail_candidate_evidence"][0]["source"] == "kordoc"
    assert "사무행정" in result["fields"]["ncs_detail_candidate_evidence"][0]["snippet"]
    assert result["fields"]["duties"] == ["문서 접수 및 보고자료 작성"]


def test_structure_job_description_splits_numbered_detail_cells_from_kordoc_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "세분류명"}, {"text": "유원시설운영관리"}, {"text": "02.객실관리"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["유원시설운영관리", "객실관리"]


def test_structure_job_description_does_not_recover_no_mapping_from_kordoc_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "NCS 세분류명"}, {"text": "현재 NCS에 Mapping 가능한 직무가 없어 별도 분석"}],
                    [{"text": "중점 수행분야"}, {"text": "안전관리 및 사고예방"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"
    assert result["fields"]["ncs_detail_absence_declared_no_mapping"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is True


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
    assert result["fields"]["ncs_detail_source"] == "explicit"


def test_structure_job_description_extracts_detail_from_combined_specialized_header() -> None:
    markdown = """
| 항목 | 내용1 | 내용2 |
| --- | --- | --- |
| 소분류 세분류(특화분류) | 간호업무 보조 | 간호행정 보조 |
| 능력단위 | 환자 이송 지원 | 진료 행정 지원 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "간호업무 보조",
        "간호행정 보조",
    ]


def test_structure_job_description_filters_section_and_duty_sentence_noise() -> None:
    markdown = """
| 세분류 | 스포츠시설운영관리 | 개발 전 | 직무개요 | 세부직무및직무수행내용 | 02, 스포츠시설 운영관리 | 청소 및 환경미화 업무 ○ 잡역 등 부대업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["스포츠시설운영관리"]


def test_structure_job_description_expands_composite_cooking_detail_candidate() -> None:
    markdown = """
| 세분류 | 한식조리 | 일식· 복어・조리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "한식조리",
        "일식조리",
        "복어조리",
    ]

    evidence_by_detail = _evidence_by_detail(result)
    for candidate in result["fields"]["ncs_detail_candidates"]:
        evidence = evidence_by_detail[candidate]
        assert evidence["source"] in {"markdown", "kordoc"}
        assert evidence["snippet"]
        assert evidence["snippet"] != candidate


def test_structure_job_description_splits_comma_and_slash_detail_candidates() -> None:
    markdown = """
| 세분류 | 총무, 사무행정 | 한식조리/양식조리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "총무",
        "사무행정",
        "한식조리",
        "양식조리",
    ]


def test_structure_job_description_does_not_promote_small_category_when_detail_cell_is_blank() -> None:
    markdown = """
<table>
<tr><th rowspan="4">채용분야</th><th rowspan="4">보건직</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
<tr><td>06.보건·의료</td><td>02.의료</td><td>05.보건</td><td>-</td></tr>
<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>01.총무</td><td>01.총무</td></tr>
<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>03.일반사무</td><td>02.사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["총무", "사무행정"]


def test_structure_job_description_uses_detail_column_not_small_category_in_html_header_table() -> None:
    markdown = """
<table>
<tr><th rowspan="3">직무분야</th><th rowspan="3">간호조무</th><th colspan="4">NCS 분류체계</th></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>06.보건/의료</td><td>02.의료</td><td>05.간호조무</td><td>01.간호업무 보조<br>(특화분류)</td></tr>
<tr><td>주요사업</td><td colspan="5">환자 지원 업무</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["간호업무 보조"]
    assert "간호조무" not in result["fields"]["ncs_detail_candidates"]


def test_structure_job_description_reads_detail_from_classification_marker_row() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th></th><th rowspan="2">기간제직원수시채용</th><th>연구원</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
<tr><td>채용분야</td><td>분류체계</td><td>공공분야</td><td>공공정책연구개발</td><td colspan="2">문화〮관광정책</td></tr>
<tr><td>담당업무</td><td colspan="6">정책연구, 조사, 평가</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "kcti.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["문화〮관광정책"]
    assert result["fields"]["ncs_detail_source"] == "explicit"


def test_structure_job_description_stops_html_detail_backfill_at_required_ability_row() -> None:
    markdown = """
<table>
<tr><th rowspan="3">채용분야</th><th rowspan="3">기술직</th><th colspan="4">NCS 분류체계</th></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>14.건설</td><td>03.건설기계운전·정비</td><td>03.건설기계정비</td><td>01.건설기계정비</td></tr>
<tr><td>필요능력</td><td colspan="5">공사감독 및 안전관리 분야의 관련 법령 이해</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["건설기계정비"]


def test_structure_job_description_does_not_extract_when_table_declares_no_ncs_mapping() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="6">안전순찰원</th></tr>
<tr><td rowspan="2">NCS<br>분류<br>체계</td><td colspan="2">대분류</td><td>중분류</td><td colspan="2">소분류</td><td>세분류</td></tr>
<tr><td colspan="5">현재 NCS에 Mapping 가능한 직무(세분류)가 없어,<br>별도 분석을 통해 내용 도출</td><td>안전순찰원</td></tr>
<tr><td>중점<br>수행분야</td><td colspan="6">안전관리 및 사고예방</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_marks_undeveloped_ncs_classification_as_no_mapping() -> None:
    markdown = """
# 직무기술서 : 인증 및 환자안전
<table>
<tr><th rowspan="4">채용분야</th><th colspan="2" rowspan="4">인증 및 환자안전</th><th>대분류</th><th rowspan="4">현재 NCS 분류체계 미개발 분야</th></tr>
<tr><td>중분류</td></tr>
<tr><td>소분류</td></tr>
<tr><td>세분류</td></tr>
<tr><td>직무내용</td><td colspan="4">인증제도 운영 및 환자안전사고 보고 자료 관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_treats_inline_undeveloped_detail_as_no_mapping() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 | 세분류 |
| --- | --- | --- | --- | --- |
| 채용분야 | 연구직 | 연구 | 연구 | 연구(미개발) |
| 직무수행내용 | 연구과제 기획 및 수행 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_marks_blank_detail_cell_state() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 | 세분류 |
| --- | --- | --- | --- | --- |
| 채용분야 | 경영·회계·사무 | 총무ㆍ인사 | 일반사무 | - |
| 직무수행내용 | 자료 취합 및 문서 관리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_detail_cell_blank_or_dash"
    assert result["fields"]["ncs_detail_absence_saw_ncs_table"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is True
    assert result["fields"]["ncs_detail_absence_blank_or_dash_detail_cell"] is True


def test_structure_job_description_marks_filtered_detail_candidate_state() -> None:
    markdown = """
| NCS 분류체계 | 세분류 |
| --- | --- |
| 채용분야 | 자료 취합 및 문서 관리 업무를 수행하고 대내외 보고자료 작성 및 부대업무를 담당 |
| 직무수행내용 | 자료 취합 및 문서 관리 |
| 2026 | 1234 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_detail_candidate_filtered"
    filtered_reasons = result["fields"]["ncs_detail_absence_filtered_candidate_reason"].split("; ")
    assert "filtered_candidate_not_detail_like" in filtered_reasons
    assert "value_too_long" in filtered_reasons
    assert "filtered_candidate_reason=value_too_long" in result["fields"]["ncs_detail_absence_state"]


def test_structure_job_description_marks_ncs_table_without_detail_header() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 |
| --- | --- | --- | --- |
| 채용분야 | 사업관리 | 사업관리 | 프로젝트관리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_table_without_detail_header"
    assert result["fields"]["ncs_detail_absence_saw_ncs_table"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is False


def test_structure_job_description_marks_job_document_without_explicit_ncs_detail() -> None:
    markdown = """
# 직무소개서
| 채용분야 | 업무지원직 |
| --- | --- |
| 세부직무 | 배치부서 업무지원 |
| 업무내용 | 우편물 관리, 환자 안내, 환경관리, 공연 전시 업무보조 |
| 직무요건 | [지식] 병원 환경 관리에 대한 이해 [기술] 문서작성 및 사무기기 활용 |
"""

    result = structure_job_description({"markdown": markdown}, filename="job-intro.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "job_document_markers_without_ncs_classification"
    )
    assert "직무소개서" in result["fields"]["ncs_detail_absence_evidence"]


def test_structure_job_description_continues_after_no_ncs_mapping_row_for_later_explicit_detail() -> None:
    markdown = """
<table>
<tr><td rowspan="2">NCS<br>분류<br>체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td colspan="3">현재 NCS에 Mapping 가능한 직무(세분류)가 없어 별도 분석</td><td>안전순찰원</td></tr>
<tr><td>세분류</td><td colspan="4">사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]


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


def test_structure_job_description_infers_high_confidence_wastewater_detail_when_no_label() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 채용분야 | 수탁운영(하수도 시설운영 지원) |
| 근무예정부서 | 보령권지사 장항물재생센터 |
| 직무내용(세부업무) | 채수, 수질검사, 실험실 일지관리 등 수질실험실 운영을 위한 보조업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["하수처리시설운영관리"]
    assert result["fields"]["duties"] == [
        "채수, 수질검사, 실험실 일지관리 등 수질실험실 운영을 위한 보조업무"
    ]


def test_structure_job_description_does_not_infer_translation_as_ncs_detail() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 직무명 | 한국어-영어 통·번역사 |
| 직무내용(세부업무) | 한영 번역과 회의 시 통역 업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "translation_role_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "translation_role_markers_without_ncs_detail"
    )


def test_structure_job_description_marks_multi_role_healthcare_document_without_detail() -> None:
    markdown = """
# 채용 직무 설명자료
강원대학교병원 직종별 설명자료
간호직, 의료기술직, 약무직, 업무협력직, 임상교수, 임상병리, 영상의학, 의무기록 업무를 포함한다.
직무내용: 병원 내 여러 직종의 진료지원, 행정, 검사, 시설 업무를 통합 안내한다.
"""

    result = structure_job_description({"markdown": markdown}, filename="hospital.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "multi_role_healthcare_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "multi_role_healthcare_markers_without_ncs_detail"
    )
    assert "강원대학교병원" in result["fields"]["ncs_detail_absence_evidence"]


def test_structure_job_description_does_not_infer_ambiguous_power_plant_detail() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 공사명 | 영흥 5호기 계획예방정비공사 |
| 직무내용(세부업무) | 전기설비 정비 업무 보조 및 발전설비 유지보수 지원 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_infers_youngheung_thermal_power_detail_when_no_label() -> None:
    markdown = """
<table>
<tr><th>근무지</th><th colspan="2">○ 한전KPS 영흥사업처<br>- 주소 : 인천광역시 옹진군 영흥면</th></tr>
<tr><td>직무수행<br>내 용</td><td colspan="2">○ 2026년 영흥 5호기 계획예방정비공사 정비업무 보조<br>- 전기설비 정비 업무 보조</td></tr>
<tr><td>필요지식</td><td colspan="2">○ 발전설비에 대한 올바른 이해<br>○ 발전설비의 유지보수에 관한 기초 지식</td></tr>
<tr><td>필요기술</td><td colspan="2">○ 설비별, 기기별 정비 절차 이해<br>○ 안전 수칙 준수</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["화력발전설비운영"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_does_not_infer_power_detail_for_youngheung_office_assistant() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><td>사무보조</td></tr>
<tr><th>근무지</th><td>한전KPS 영흥사업처 총무부</td></tr>
<tr><th>직무수행 내용</th><td>5호기 계획예방정비공사 사무 업무 보조, 문서 작성, 전산 입력 지원</td></tr>
<tr><th>필요지식</th><td>사무업무에 대한 기본 지식</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="직무기술서(사무보조)_총무부.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "job_document_markers_without_ncs_classification"
    )


def test_structure_job_description_infers_old_water_pipe_detail_when_no_label() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="3">기술관리_건설사업</th></tr>
<tr><td>근무예정부서</td><td>모집인원</td><td>근무지역</td></tr>
<tr><td>의령2 노후상수관망정비사업소</td><td>1명</td><td>의령군</td></tr>
<tr><td>직무내용</td><td colspan="3">- 노후상수도 정비사업 공사감독, 안전관리 및 사업관리<br>- 노후상수도 정비사업 관련, 누수탐사‧복구 및 민원처리 업무 등</td></tr>
<tr><td>필요능력</td><td colspan="3">건설기술 분야의 공사감독 및 안전관리, 사업관리 분야의 관련 법령</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["상수관로시설운영관리"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_infers_health_education_and_industrial_health_when_no_label() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th>채용분야</th><td>의료보조(보건관리)</td></tr>
<tr><td>직무내용</td><td>
⚬ (보건교육) 개인과 집단의 질병예방 및 건강증진을 위하여 보건교육 요구도 진단 및 수립
⚬ (보건관리계획수립평가) 연간보건관리계획 수립
⚬ (사업장 건강증진) 산업안전보건법에 따른 건강진단 시행 및 사후관리
⚬ (작업환경측정 평가개선) 본사 부서 작업환경측정 지원
</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "health.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["보건교육", "산업보건관리"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_does_not_infer_broad_health_management_label_only() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th>채용분야</th><td>의료보조(보건관리)</td></tr>
<tr><td>직무내용</td><td>보건관리 업무 지원 및 자료 정리</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "health.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
