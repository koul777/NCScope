from __future__ import annotations

import pytest

from app.services.question_realism import evaluate_question_realism


@pytest.mark.parametrize(
    "item",
    [
        {
            "type": "경험면접",
            "question": (
                "월말 수치 오류를 스스로 찾아낸 실제 경험을 말씀해 주십시오. "
                "부서 반대를 감내하고 어떤 정정을 시행했는지 설명해 주십시오."
            ),
        },
        {
            "type": "창의적 문제해결력면접",
            "question": (
                "공시 마감과 검증 요구가 충돌합니다. 지원자가 보고 지연을 떠안고 "
                "기준 하나를 선택해 직접 시행하며 그 결과를 책임지는 방안을 제시하십시오."
            ),
        },
        {
            "type": "발표면접",
            "question": (
                "세 사업의 예산이 부족합니다. 책임까지 지원자가 떠안겠다는 전제에서 "
                "사업별 감축안을 발표하십시오."
            ),
        },
        {
            "type": "경험면접",
            "question": (
                "접근 권한이 부적절해 자료 이용 범위를 축소하거나 이용을 중단했던 "
                "실제 사례와 그로 인한 업무 지연을 설명해 주십시오."
            ),
            "follow_ups": [
                "직접 경험이 없다면 유사한 상황에서 허용 범위를 설명해 주십시오."
            ],
        },
        {
            "type": "발표면접",
            "question": (
                "집행액 증가와 성과 하락이 나타났고 민원도 동일 분기에 증가했습니다. "
                "가장 중요한 원인을 한 가지로 특정해 발표하십시오."
            ),
        },
        {
            "type": "발표면접",
            "question": (
                "동결된 예산을 세 사업에 배분할 기준과 조정량을 발표하십시오."
            ),
            "follow_ups": [
                "발표에서 지원자가 감수하겠다고 한 손실이 발생하면 결정을 유지할 경계는 무엇입니까?"
            ],
        },
        {
            "type": "창의적 문제해결력면접",
            "question": (
                "오늘 게시할 수치가 두 시스템에서 다릅니다. 게시 여부와 최소 검증을 정하십시오."
            ),
            "evaluation_points": [
                "게시 지연 비용을 감수한 직접 조치와 결과 책임을 명확히 제시한다"
            ],
        },
    ],
    ids=[
        "costly_discovery_without_fallback",
        "cost_direct_action_personal_liability",
        "personal_liability_precondition",
        "one_sided_ethical_experience",
        "cooccurrence_forced_as_single_cause",
        "followup_presumes_accepted_personal_cost",
        "scoring_key_rewards_personal_sacrifice",
    ],
)
def test_leading_answer_mutations_are_blocked(item: dict[str, object]) -> None:
    result = evaluate_question_realism(item)

    assert result["policy_version"] == "field-realism-v3.14"
    assert result["checks"]["no_prescribed_answer"] is False
    assert "candidate_answer_prescribed" in result["issue_codes"]
    assert result["metrics"]["leading_or_assumptive_exposure_count"] >= 1


@pytest.mark.parametrize(
    "item",
    [
        {
            "type": "상황면접",
            "question": (
                "공시 마감과 검증 요구가 충돌합니다. 게시·부분 게시·보류 가운데 어떤 "
                "조치를 택할지, 판단 근거와 권한 밖 조치의 보고 경계를 설명하십시오."
            ),
        },
        {
            "type": "경험면접",
            "question": (
                "월말 수치 오류를 스스로 찾아 부서 반대를 감내하고 정정한 경험을 "
                "말씀해 주십시오."
            ),
            "follow_ups": [
                "직접 경험이 없다면 유사한 불일치 상황에서 선택할 조치를 설명해 주십시오."
            ],
        },
        {
            "type": "경험면접",
            "question": (
                "접근 권한이 불분명한 자료를 두고 사용·제한·보류 중 무엇을 선택했는지, "
                "또는 같은 상황이라면 무엇을 선택할지 설명해 주십시오."
            ),
        },
        {
            "type": "발표면접",
            "question": (
                "집행액 증가와 성과 하락이 나타났고 민원도 같은 달에 늘었습니다. "
                "가능한 원인 가설과 이를 반증할 자료를 발표하십시오."
            ),
        },
        {
            "type": "토론면접",
            "question": (
                "일정 준수와 검증 완결성 중 어떤 조건을 우선할지 토론하고, 결정 권한과 "
                "결과 책임을 맡을 주체를 정하십시오."
            ),
        },
        {
            "type": "발표면접",
            "question": "동결된 예산의 사업별 배분 기준과 조정량을 발표하십시오.",
            "follow_ups": [
                "앞서 선택한 배분으로 지연이 생긴다면 어떤 자료로 유지 또는 수정을 판단하겠습니까?"
            ],
            "evaluation_points": [
                "권한 범위에서 직접 할 조치와 상급자에게 보고할 조치를 구분한다"
            ],
        },
    ],
    ids=[
        "neutral_tradeoff_and_escalation",
        "specific_experience_with_fallback",
        "open_ethical_choice",
        "correlation_framed_as_testable_hypothesis",
        "organizational_responsibility_owner",
        "conditional_consequence_and_bounded_action",
    ],
)
def test_neutral_dilemma_mutations_remain_allowed(item: dict[str, object]) -> None:
    result = evaluate_question_realism(item)

    assert result["checks"]["no_prescribed_answer"] is True, result["issues"]
    assert "candidate_answer_prescribed" not in result["issue_codes"]
    assert result["metrics"]["leading_or_assumptive_exposure_count"] == 0
