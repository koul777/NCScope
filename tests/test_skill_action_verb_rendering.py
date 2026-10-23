"""Skill surfaces that end in an action noun must not get a second "수행" verb."""

from __future__ import annotations

import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row

# "작성을 수행", "적용을 직접 수행" stack two action verbs.  Surfaces that end
# in 절차 (for example "…확인 절차를 수행") are the documented exception.
_STACKED_ACTION_RE = re.compile(
    r"(?:작성|적용|활용|예측|분석|해석|조정|조율|의사결정|포착|개발)\s*['\"’]?\s*(?:을|를)\s*"
    r"(?:직접\s*|실제로\s*)?수행"
)
SKILL_FACTORS = (
    "보고서 작성 능력",
    "통계 방법 적용 능력",
    "예산프로그램 활용 능력",
    "제품설계, 공정설계, 생산설계 활용 능력",
    "분석 결과에 대한 통계적 해석 및 업무 적용 능력",
    "고객 데이터 분석 능력",
    "수요예측 기법",
)


def _row(factor: str) -> dict[str, str]:
    return {
        "major_code": "02",
        "major_name": "경영·회계·사무",
        "sub_name": "사무행정",
        "unit_code": "0202030201_25v3",
        "unit_name": "문서작성",
        "unit_definition": "요구사항을 파악하여 문서를 작성하는 능력이다.",
        "element_name": "문서 작성하기",
        "ksa_type": "기술",
        "factor_name": factor,
        "ksa_no": "1",
    }


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize("factor", SKILL_FACTORS)
def test_action_noun_skill_surface_is_not_followed_by_perform(method: str, factor: str) -> None:
    row = _row(factor)
    for variation_index in (0, 27):
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence_row(row)],
        )
        question = evaluated["interview_questions"][0]
        quality = evaluated["question_quality_report"]["items"][0]
        visible = "\n".join([question["question"], *question["follow_ups"]])

        assert not _STACKED_ACTION_RE.search(visible), visible
        assert quality["ready"] is True, (method, factor, quality.get("issues"))
        assert question["question_focus"] == factor
        assert question["ksa_evidence"][0]["factorName"] == factor
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))


def test_experience_tool_surface_does_not_ask_for_the_tool_twice() -> None:
    row = _row("예산프로그램 활용 능력")
    surface, _source = public_task_object(factor_name=row["factor_name"], ksa_type="기술")
    item = build_template_question(row, "경험면접", variation_index=0)

    assert "프로그램" in surface
    assert surface in item["question"]
    assert "그때 쓴 도구" not in item["question"]
    assert "행동의 순서와 만든 결과물, 결과를 확인한 방법을 설명해 주세요." in item["question"]


@pytest.mark.parametrize(
    ("factor", "particle"),
    (
        ("계약서의 내용 파악 능력", "을"),
        ("화면전환효과 선택 능력", "을"),
        ("식재료 선별 능력", "을"),
        ("무게중심 유지 능력", "를"),
        ("미생물 배양 능력", "을"),
        ("보고서 작성 능력", "을"),
    ),
)
def test_situational_action_noun_uses_haeya_without_extra_execution(factor: str, particle: str) -> None:
    row = _row(factor)
    for variation_index in (0, 27):
        item = build_template_question(row, "상황면접", variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence_row(row)],
        )
        question = evaluated["interview_questions"][0]
        surface = question["question_focus_surface"]

        assert f"{surface}{particle} 실제로 해야 하는 가운데" in question["question"]
        assert "실제로 실행해야" not in question["question"]
        assert question["question_focus"] == factor
        assert question["ksa_evidence"][0]["factorName"] == factor
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
        assert evaluated["question_quality_report"]["items"][0]["ready"] is True


@pytest.mark.parametrize("method", ("인바스켓면접", "창의적 문제해결력면접"))
def test_performance_task_uses_matching_concurrent_connective(method: str) -> None:
    row = _row("피교육생의 수준에 맞는 교육 수행능력")
    for variation_index in (0, 27):
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence_row(row)],
        )
        question = evaluated["interview_questions"][0]

        # R11: the in-basket sorts documents by the skill and the creative
        # task names what the problem needs; neither does the skill "while".
        expected = {
            "인바스켓면접": "문서마다 피교육생의 수준에 맞는 교육이 필요한지 판단해",
            "창의적 문제해결력면접": "피교육생의 수준에 맞는 교육 없이는 해결하기 어려운 복합 문제",
        }[method]
        assert expected in question["question"]
        assert "실제로 수행하며" not in question["question"]
        assert "실제로 수행해" not in question["question"]
        assert question["question_focus"] == row["factor_name"]
        assert question["ksa_evidence"][0]["factorName"] == row["factor_name"]
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
        assert evaluated["question_quality_report"]["items"][0]["ready"] is True
