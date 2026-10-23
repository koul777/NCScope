"""Removing 수행 must leave a complete noun object, across all interview methods."""

from __future__ import annotations

import copy
import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row


PERFORMANCE_CASES = (
    ("계획 수립 및 수행 능력", "계획 수립 및 수행"),
    ("현장에서 수행 능력", "현장에서 수행"),
    ("정확한 수행 능력", "정확한 수행"),
    ("면담 수행 능력", "면담 수행"),
)


def _row(factor: str) -> dict[str, str]:
    return {
        "major_code": "04",
        "major_name": "교육·자연·사회과학",
        "sub_name": "이러닝시스템개발",
        "unit_code": "0403020109_16v3",
        "unit_name": "학습시스템 이전",
        "unit_definition": "교육계획을 수립하여 교육하고 교육목표 달성 여부를 파악하는 능력이다.",
        "element_name": "학습시스템 사용자 교육하기",
        "ksa_type": "기술",
        "factor_name": factor,
        "ksa_no": "2",
    }


@pytest.mark.parametrize(
    "prefix",
    (
        "교육계획 수립 및", "계획 수립 또는", "계획 수립 혹은", "계획 수립·",
        "계획 수립ㆍ", "계획 수립․", "계획 수립,", "계획 수립/",
        "교육계획과", "교육자료와", "교육현장에서", "교육현장으로", "교육자료로",
        "교육계획의", "교육현장에", "교육현장은",
        "정확한", "계획된", "안정적인", "진행하는", "준비된", "지속되는",
        "준비할", "준비했던", "준비 중인",
    ),
)
@pytest.mark.parametrize("suffix", ("능력", "기술"))
def test_performance_keeps_noun_after_connective_particle_or_modifier(
    prefix: str, suffix: str,
) -> None:
    factor = f"{prefix} 수행 {suffix}"
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")

    assert surface == f"{prefix} 수행"
    assert source == "factor_repair"
    assert "검증" not in surface
    assert "절차" not in surface


@pytest.mark.parametrize(
    ("factor", "expected"),
    PERFORMANCE_CASES + (
        ("검사수행 능력", "검사 수행"),
        ("교육계획 수립 수행 능력", "교육계획 수립"),
        ("피교육생의 수준에 맞는 교육 수행능력", "피교육생의 수준에 맞는 교육"),
        ("교육계획 수립 수행", "교육계획 수립"),
    ),
)
def test_performance_preserves_short_task_and_complete_noun(
    factor: str, expected: str,
) -> None:
    surface, source = public_task_object(
        factor_name=factor, ksa_type="기술", element_name="학습시스템 사용자 교육하기",
    )

    assert surface == expected
    assert source == "factor_repair"
    assert "절차" not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "expected"), PERFORMANCE_CASES)
def test_performance_every_method_keeps_complete_surface_and_evidence(
    method: str, factor: str, expected: str,
) -> None:
    row = _row(factor)
    evidence = evidence_row(row)
    for variation_index in (0, 27):
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence],
        )
        question = evaluated["interview_questions"][0]
        quality = evaluated["question_quality_report"]["items"][0]
        visible = "\n".join([question["question"], *question["follow_ups"]])

        assert question["question_focus_surface"] == expected
        assert expected in question["question"]
        assert not re.search(r"(?:및|에서|정확한)(?:을|를)", visible), visible
        assert not re.search(r"수행(?:을|를)\s*(?:직접\s*|실제로\s*)?수행", visible), visible
        assert "수행·검증 절차" not in visible
        assert question["question_focus"] == factor
        assert question["ksa_evidence"][0]["factorName"] == factor
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence)
        assert quality["ready"] is True, quality.get("issues")
        assert quality["score"] == 1.0

        second_pass = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [copy.deepcopy(question)], "question_plan_used": {"total_main_count": 1}},
            [evidence],
        )["interview_questions"][0]
        for key in ("question", "follow_ups", "question_focus_surface", "question_evidence_id"):
            assert second_pass[key] == question[key], (method, factor, key)
