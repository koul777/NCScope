from __future__ import annotations

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)
from tests.test_composite_v20_ksa_semantic_oracle import COMPOSITE_V20_CASES


FACTOR = "기업 내 데이터 수집 및 활용에 대한 윤리적 태도"
QUESTION = (
    "성과 현황을 도표로 정리해 공유해야 하는 촉박한 일정에서, 이용 목적이나 열람 "
    "범위가 불분명한 내부 자료를 사용하면 업무는 빨라지지만 부적절한 노출 우려가 "
    "있었던 실제 사례를 말씀해 주세요. 같은 경험이 없다면 학업·프로젝트·봉사에서 "
    "가장 가까운 사례로 답해도 좋습니다. 당시 본인의 역할, 선택한 행동 하나와 "
    "관찰된 결과는 무엇이었습니까?"
)
PARAPHRASE = (
    "보고 자료를 준비할 기한이 촉박한 때 이용 용도와 접근 범위가 불명확한 업무 "
    "자료를 쓰면 작업은 빨라지지만 정보 노출 위험이 있었던 경험을 말씀해 주세요. "
    "본인이 맡은 역할과 취한 행동, 그 뒤 확인한 결과는 무엇입니까?"
)
RATIONALE = (
    "업무 속도와 부적절한 노출 위험의 충돌에서 이용 목적·열람 경계를 식별하고, "
    "응답자가 고른 행동과 관찰 가능한 결과를 같은 경험 단위에서 확인한다."
)


def _item(
    question: str = QUESTION,
    *,
    source: str = "openai_api",
    factor: str = FACTOR,
    method: str = "경험면접",
) -> dict[str, object]:
    return {
        "type": method,
        "question_focus": factor,
        "question_focus_type": "태도",
        "question_focus_surface": "내부 추적용 표면 힌트",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": "ksa_data_use_v21_frozen",
        "question": question,
        "follow_ups": ["답변 근거를 더 설명해 주세요."] * 3,
        "evaluation_points": ["관찰 가능한 선택과 결과를 확인한다."] * 4,
    }


def test_v21_oracle_is_frozen_and_report_independent() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(RATIONALE) >= 40
    assert FACTOR not in QUESTION
    assert "감수" not in QUESTION


@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
@pytest.mark.parametrize("question", [QUESTION, PARAPHRASE], ids=["fresh", "paraphrase"])
def test_v21_accepts_observable_experience_relation(
    source: str,
    question: str,
) -> None:
    assert evaluate_ksa_measurement(_item(question, source=source))["passed"] is True


REMOVALS = (
    ("boundary", "이용 목적이나 열람 범위가 불분명한", "출처가 알려진"),
    (
        "choice-action",
        "당시 본인의 역할, 선택한 행동 하나와 관찰된 결과",
        "당시 상황에 대한 일반적인 생각",
    ),
    (
        "result",
        "선택한 행동 하나와 관찰된 결과",
        "윤리적으로 행동하겠다는 다짐",
    ),
)


@pytest.mark.parametrize(("dimension", "old", "new"), REMOVALS)
def test_v21_rejects_unique_relation_removals(
    dimension: str,
    old: str,
    new: str,
) -> None:
    assert old in QUESTION
    result = evaluate_ksa_measurement(_item(QUESTION.replace(old, new, 1)))
    assert result["passed"] is False, (dimension, result)


def test_v21_rejects_negated_action_and_result() -> None:
    old = "당시 본인의 역할, 선택한 행동 하나와 관찰된 결과는 무엇이었습니까?"
    new = "당시 행동을 선택하지 말고 결과도 확인하지 않은 이유만 말해 주세요."
    assert evaluate_ksa_measurement(_item(QUESTION.replace(old, new, 1)))["passed"] is False


@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_v21_rejects_keyword_salad(source: str) -> None:
    salad = "데이터 수집 활용 윤리 목적 열람 범위 선택 행동 결과 경험을 말해 주세요."
    assert evaluate_ksa_measurement(_item(salad, source=source))["passed"] is False


@pytest.mark.parametrize("other", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
def test_v21_rejects_cross_factor_swaps(other: object) -> None:
    other_question = getattr(other, "question")
    other_factor = getattr(other, "factor")
    other_method = getattr(other, "method")
    assert evaluate_ksa_measurement(_item(other_question))["passed"] is False
    assert (
        evaluate_ksa_measurement(
            _item(QUESTION, factor=other_factor, method=other_method)
        )["passed"]
        is False
    )
