"""R11-B3: preserve knowledge heads and grammatical template connections."""

import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS
from app.services.jd_strategy import _build_ncs_code_template_fallback_question
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row


def _row(factor, kind="지식"):
    return {
        "unit_code": "0202030201_17v2", "unit_name": "문서작성", "sub_name": "사무행정",
        "element_name": "자료 분석하기", "ksa_type": kind, "factor_name": factor, "ksa_no": "1",
    }


def _fallback(row, method):
    return _build_ncs_code_template_fallback_question(
        unit={"compeUnitName": row["unit_name"], "ncsClCd": row["unit_code"]},
        comp_name=row["unit_name"], ncs_code=row["unit_code"],
        ksa_terms=[row["factor_name"]], evidence_terms=[row["factor_name"]],
        evidence_rows=[evidence_row(row)], index=0, method_override=method,
        job_context_text="문서 작성과 자료 분석 업무",
    )


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize("builder", [build_template_question, _fallback])
@pytest.mark.parametrize("factor", ["지원현황", "산업 동향", "회계 개념", "전기 원리", "근로 법령", "문서 보안",
                                    "교육과정의 선택과 이수에 대한 내용", "의료정보 작성과 보관에 관한 내용"])
def test_all_template_paths_avoid_repeated_content_relation(method, builder, factor):
    row = _row(factor)
    item = builder(row, method)
    visible = "\n".join([item["question"], *item["follow_ups"]])
    assert not re.search(r"관한 내용에 관한|관한 내용에 대한|대한 내용에 관한", visible)
    assert item["question_focus_surface"] in item["question"]
    assert item["question_focus"] == factor
    assert item["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))


@pytest.mark.parametrize("head", ["방법", "개념", "원리", "종류", "법령", "법규", "규정", "제도", "현황", "동향", "이해", "특성", "구조"])
@pytest.mark.parametrize("prefix", [
    "지리정보시스템(GIS, Geographic Information System)를 통한 지리공간데이터 및",
    "전국 지역별 공공기관 사업 부서에서 사용하는 여러 통계 자료와 관련 보고서를 종합하여 비교하고 지역 및 대상 집단별로 분류하여 정리한 조사 결과에 따른",
])
def test_long_knowledge_preserves_its_terminal_head(prefix, head):
    surface, source = public_task_object(
        factor_name=f"{prefix} 인구통계 분석 {head}", ksa_type="지식", element_name="자료 분석하기",
    )
    assert source == "factor_repair"
    assert f"인구통계 분석 {head}" in surface
    assert len(surface) <= 80
    assert surface.count("(") == surface.count(")")


@pytest.mark.parametrize("factor", ["열분해 조업후 분석", "운행전 점검", "작업시 점검", "운전중 점검", "문서등 관리"])
def test_attached_dependent_nouns_are_not_action_objects(factor):
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")
    assert (surface, source) == (f"{factor} 작업", "factor_repair")
    assert not re.search(r"(?:관련|전|후|시|중|등)(?:을|를) \S+하는 작업", surface)


@pytest.mark.parametrize(("factor", "expected"), [
    ("안전 관리", "안전을 관리하는 작업"), ("비중 분석", "비중을 분석하는 작업"),
    ("하중 계산", "하중을 계산하는 작업"), ("작업 지시 분석", "작업 지시를 분석하는 작업"),
    ("의견 제시 분석", "의견 제시 분석 작업"), ("운전실표시등 해석", "운전실표시등을 해석하는 작업"),
])
def test_dependent_suffix_exceptions_keep_noun_objects(factor, expected):
    assert public_task_object(factor_name=factor, ksa_type="기술")[0] == expected


@pytest.mark.parametrize(("factor", "kind"), [("지원현황", "지식"), ("문서 분석", "기술"), ("정확성 유지 태도", "태도")])
def test_fallback_knowledge_method_uses_matching_particles(factor, kind):
    item = _fallback(_row(factor, kind), "직무지식면접")
    visible = "\n".join([item["question"], *item["follow_ups"]])
    assert not re.search(r"내용가|작업를|행동를", visible)


@pytest.mark.parametrize(("factor", "kind"), [("지원현황", "지식"), ("문서 분석", "기술")])
def test_fallback_presentation_uses_matching_particles(factor, kind):
    item = _fallback(_row(factor, kind), "발표면접")
    assert not re.search(r"내용를|작업를", item["question"])


@pytest.mark.parametrize("factor", [
    "업무 규정: " + "대상별 자료와 부서별 자료 및 통계 수치 " * 8 + "조사 결과",
    "사업 관리(통합, 범위, 일정, 원가, 품질, 인적자원, 의사소통, 리스크, 조달 및 이해관계자)에 대한 일반적 지식",
])
def test_unsafe_knowledge_compaction_uses_official_element(factor):
    surface, source = public_task_object(factor_name=factor, ksa_type="지식", element_name="자료 분석하기")
    assert (surface, source) == ("자료 분석에 관한 내용", "element_name")
    assert "일반적에 관한" not in surface


@pytest.mark.parametrize("factor", [
    "기구가 조립된 후 제대로 특성을 나타내는지 검증하는 방법",
    "장비를 구성하는 다양한 기구 유닛이 제대로 조립되어 장비에 요구되는 양산 특성을 발휘할 수 있는지 검증할 수 있는 방법",
])
def test_explicit_check_keeps_a_source_method_head(factor):
    surface, source = public_task_object(factor_name=factor, ksa_type="지식", element_name="자료 분석하기")
    assert source == "factor_repair"
    assert "방법" in surface


def test_knowledge_gloss_removal_does_not_leave_a_second_type_suffix():
    factor = "로봇 시스템에 대한 전기적 기계적 특성에 관한 지식 (로봇 기구학, 로봇 공학, 제어이론, 신뢰성 시험방법, 기계설계, 생산공학 등)"
    surface, source = public_task_object(factor_name=factor, ksa_type="지식", element_name="로봇 설계하기")
    assert (surface, source) == ("로봇 시스템에 대한 전기적 기계적 특성에 관한 내용", "factor_repair")
    assert "지식에 관한 내용" not in surface


@pytest.mark.parametrize(("noun", "source_particle", "expected_particle"), [
    ("지리정보시스템", "를", "을"), ("통계자료", "을", "를"),
])
def test_shortened_gloss_reconnects_particle_to_remaining_noun(noun, source_particle, expected_particle):
    factor = f"{noun}(Geographic Information System, Detailed Statistical Information){source_particle} 통한 인구통계 분석 방법"
    surface, source = public_task_object(factor_name=factor, ksa_type="지식")
    assert source == "factor_repair"
    assert surface == f"{noun}{expected_particle} 통한 인구통계 분석 방법에 관한 내용"
    assert f"{noun}{source_particle}" not in surface
