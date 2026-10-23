"""Chair review: source meaning and grammar survive all seven templates."""

import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _with_josa
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row


KNOWLEDGE = (
    "지원현황", "산업 동향", "회계 개념", "전기 원리", "근로 법령", "장비 종류",
    "문서 분류 기준", "문서 보안 지식",
)
SKILLS = (
    "외국어 의사소통 능력", "도면에 대한 이해 능력", "의견발표 기술", "약효증대 기술",
    "상처 치료(드레싱) 기술", "프로세스 수행하는 능력", "프로세스를 정확하게 수행하는 능력",
    "프로젝트 이행을 실행에 옮길 수 있는 능력", "고객을 도울 수 있는 능력",
)


def _row(factor, kind):
    return {
        "unit_code": "0202030201_17v2", "unit_name": "문서작성", "sub_name": "사무행정",
        "element_name": "자료 분석하기", "ksa_type": kind, "factor_name": factor, "ksa_no": "1",
    }


@pytest.mark.parametrize("factor", KNOWLEDGE)
def test_knowledge_names_source_content_without_inventing_a_standard(factor):
    surface, source = public_task_object(factor_name=factor, ksa_type="지식")
    assert source == "factor_repair"
    assert surface.endswith("내용")
    for word in ("확인", "검증", "점검", "절차", "적용", "기준", "관련"):
        if word not in factor:
            assert word not in surface
    for word in ("현황", "동향", "개념", "원리", "법령", "종류"):
        if word in factor:
            assert word in surface


@pytest.mark.parametrize("factor", (
    "영업비밀 관련 관리", "운행 전 점검", "작업 후 점검", "운전 시 점검", "운전 중 점검",
    "기관 간 관리", "조직 내 관리", "조직 외 관리", "문서 등 관리", "현장처치 매뉴얼 작성 운영",
    "현장처치 매뉴얼 작성·운영", "자료 수집 관리", "영업비밀관련 관리", "자료수집 관리",
    "주·보조기관 길들이기 운전 관리", "일정 계획 수립", "예산 계획 수립", "현장처치 매뉴얼작성 운영",
))
def test_source_action_does_not_make_a_modifier_or_action_into_an_object(factor):
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")
    assert (surface, source) == (f"{factor} 작업", "factor_repair")
    assert not re.search(r"(?:관련|전|후|시|중|간|내|외|등|작성|수집)(?:을|를)", surface)


@pytest.mark.parametrize("factor", (
    "프로젝트 이행을 실행에 옮길 수 있는 능력", "고객을 도울 수 있는 능력",
))
def test_non_hada_capability_uses_the_official_element(factor):
    assert public_task_object(
        factor_name=factor, ksa_type="기술", element_name="자료 분석하기",
    ) == ("자료 분석", "element_name")


def test_knowledge_keeps_laws_and_regulations_distinct():
    law, _ = public_task_object(factor_name="위원회 운영 관련 법령", ksa_type="지식")
    rule, _ = public_task_object(factor_name="위원회 운영 규정", ksa_type="지식")
    assert law == "위원회 운영 법령에 관한 내용"
    assert rule == "위원회 운영 규정에 관한 내용"
    assert law != rule


def test_knowledge_drops_a_trailing_relation_before_the_content_head():
    surface, source = public_task_object(
        factor_name="시장 환경 동향 관련에 대한 지식", ksa_type="지식",
    )
    assert (surface, source) == ("시장 환경 동향에 관한 내용", "factor_repair")
    assert "관련에 관한" not in surface


@pytest.mark.parametrize(("text", "expected"), (
    ("상처 치료(드레싱)", "상처 치료(드레싱)를"),
    ("공정(프로세스)", "공정(프로세스)을"),
    ("상처 치료(드레싱)(Dressing)", "상처 치료(드레싱)(Dressing)를"),
))
def test_parenthetical_gloss_does_not_choose_the_object_particle(text, expected):
    assert _with_josa(text, "을", "를") == expected


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "kind"), [(f, "지식") for f in KNOWLEDGE] + [(f, "기술") for f in SKILLS])
def test_chair_review_all_methods_keep_evidence_and_avoid_broken_predicates(method, factor, kind):
    row = _row(factor, kind)
    item = build_template_question(row, method)
    visible = "\n".join([item["question"], *item["follow_ups"]])
    assert item["question_focus_surface"] in item["question"]
    assert item["question_focus"] == factor
    assert item["ksa_evidence"] == [evidence_row(row)]
    assert item["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
    for pattern in (
        r"적용(?:할)? 기준을 적용", r"수 있는 작업", r"수행\S* 작업을 (?:직접 |실제로 )?수행",
        r"(?:이해|의사소통|의견발표|증대)를?을? (?:직접 |실제로 )?수행", r"치료\(드레싱\)을",
    ):
        assert not re.search(pattern, visible), visible
