"""Candidate-facing KSA surfaces use behaviour and check wording.

The official factor name and evidence id stay unchanged; only the public
surface and the templates that render it are pinned here.
"""

from __future__ import annotations

import pytest

from app.main import (
    SUPPORTED_INTERVIEW_METHODS,
    _attach_ksa_evidence_to_strategy,
    _followups_for_method,
    _question_for_method,
)
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row

ATTITUDE_FACTORS = (
    "원활한 커뮤니케이션을 하고자 하는 적극적인 태도",
    "정확성 유지",
    "정확성을 우선하려는 태도",
    "도면 숙지 의지",
    "입사예정자의 조직적응을 적극적으로 도와주고자 하는 태도",
    "프로젝트 목표를 달성하고자 하는 책임감 있는 태도",
    "고객과 원활하게 소통하려는 태도",
)
KNOWLEDGE_FACTORS = (
    "자국의 대외정책, 공적개발원조정책, 국별협력전략",
    "문서 보안 법규 지식",
    "전략적 인적자원관리 지식",
    "설계도면과 현장여건 검토 지식",
    "승인된 변경에 대한 지식",
)


@pytest.mark.parametrize("factor", ATTITUDE_FACTORS)
def test_attitude_surface_names_the_behaviour_not_a_behaviour_rule(factor: str) -> None:
    surface, _source = public_task_object(factor_name=factor, ksa_type="태도")

    assert surface.endswith("행동"), surface
    assert "행동 기준" not in surface
    assert not surface.endswith(("태도", "의지", "자세"))


@pytest.mark.parametrize("factor", KNOWLEDGE_FACTORS)
def test_knowledge_surface_does_not_add_a_judgment_criterion_noun_pile(factor: str) -> None:
    surface, _source = public_task_object(factor_name=factor, ksa_type="지식")

    assert "판단 기준" not in surface, surface
    assert "확인·판단" not in surface


def _row(factor: str, ksa_type: str) -> dict[str, str]:
    return {
        "major_code": "01",
        "major_name": "사업관리",
        "sub_name": "공적개발원조",
        "unit_code": "0101010104_17v2",
        "unit_name": "공적개발원조사업 총괄운영관리",
        "unit_definition": "공적개발원조사업을 총괄 운영하는 능력이다.",
        "element_name": "사업 운영 관리하기",
        "ksa_type": ksa_type,
        "factor_name": factor,
        "ksa_no": "1",
    }


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(
    ("ksa_type", "factor"),
    [
        ("태도", "원활한 커뮤니케이션을 하고자 하는 적극적인 태도"),
        ("태도", "정확성 유지"),
        ("지식", "자국의 대외정책, 공적개발원조정책, 국별협력전략"),
        ("지식", "문서 보안 법규 지식"),
    ],
)
def test_every_method_renders_behaviour_and_check_surfaces_and_keeps_evidence(
    method: str,
    ksa_type: str,
    factor: str,
) -> None:
    row = _row(factor, ksa_type)
    for variation_index in (0, 5, 27):
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence_row(row)],
        )
        question = evaluated["interview_questions"][0]
        quality = evaluated["question_quality_report"]["items"][0]
        visible = "\n".join([question["question"], *question["follow_ups"]])

        # Official KSA evidence is unchanged; only the public surface moved.
        assert question["question_focus"] == factor
        assert question["ksa_evidence"][0]["factorName"] == factor
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
        assert quality["ready"] is True, (method, ksa_type, quality.get("issues"))
        if ksa_type == "태도":
            assert "행동 기준" not in visible
            assert "행동 기준을 지키" not in visible
            assert "행동이 드러난 본인의 실제 행동" not in visible
        else:
            assert "확인·판단 기준" not in visible
            assert "적용·판단 기준" not in visible


def test_experience_knowledge_question_says_judgment_once() -> None:
    question = _question_for_method(
        "경험면접",
        "공적개발원조사업 개발전략수립",
        "자국의 대외정책, 공적개발원조정책, 국별협력전략",
        "공적개발원조",
        "",
        "지식",
    )

    assert "판단 근거로 삼았고" in question
    assert question.count("판단") == 1


def test_attitude_templates_ask_to_practise_the_behaviour() -> None:
    focus = "정확성 유지"
    debate = _question_for_method("토론면접", "문서관리", focus, "", "", "태도")
    situation = _question_for_method("상황면접", "문서관리", focus, "", "", "태도")
    experience_follow_ups = _followups_for_method("경험면접", "문서관리", focus, 3, focus_type="태도")

    assert "정확성 준수 행동을 실천해야 하는 가운데" in debate
    assert "정확성 준수 행동을 실천하기 어려운 조건" in situation
    assert "정확성 준수 행동을 보이느라 포기하거나 감수한 것" in experience_follow_ups[1]
    for text in (debate, situation, *experience_follow_ups):
        assert "행동 기준" not in text
        assert "행동을 지키" not in text
