from __future__ import annotations

import pytest

import app.main as main
from app.services.question_precision_grounding import (
    evaluate_question_precision_grounding,
)


@pytest.mark.parametrize(
    "question",
    [
        "첨부된 표를 바탕으로 ROI를 정확히 산출",
        "원자료를 토대로 정확한 손익분기점",
        "첨부 계약서상 위약금 액수",
        "재무표를 참고해 전년 대비 증감률",
    ],
)
def test_unregistered_source_precision_paraphrase_fails_public_boundary(
    question: str,
) -> None:
    item = {"type": "발표면접", "question": question}
    precision = evaluate_question_precision_grounding(item)

    assert precision["passed"] is False
    assert precision["metrics"]["precision_demand_count"] == 1
    assert precision["demands"][0]["disposition"] == "reject"
    assert main._public_questions_precision_grounded({"questions": [item]}) is False


def test_evaluation_point_numeric_allocation_gap_fails_public_boundary() -> None:
    item = {
        "type": "발표면접",
        "question": (
            "예산과 인력이 동결된 상황에서 세 사업의 지원 방식과 우선순위를 "
            "결정해 주세요."
        ),
        "evaluation_points": [
            "공통 기준의 일관성을 설명한다.",
            "사업별 예산과 인력의 배분량을 수치화한다.",
        ],
    }

    precision = evaluate_question_precision_grounding(item)

    assert precision["passed"] is False
    assert precision["demands"][0]["location"] == "evaluation_points[1]"
    assert precision["demands"][0]["kind"] == "quantified_allocation"
    assert main._public_questions_precision_grounded({"questions": [item]}) is False
