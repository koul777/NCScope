"""Realism oracle for the final five editorial questions and v3.13 boundaries."""

from __future__ import annotations

import pytest

from app.services.question_realism import evaluate_question_realism


FINAL_EDITORIAL_REALISM_CASES: tuple[dict[str, object], ...] = (
    {
        "case_id": "numeric_accuracy",
        "type": "경험면접",
        "question": (
            "성과 보고 마감이 임박한 상황에서 부서 원자료와 집계표의 값이 서로 "
            "달랐던 실제 사례를 말씀해 주세요. 같은 경험이 없다면 학업·프로젝트·봉사에서 "
            "가장 가까운 사례도 좋습니다. 당시 본인의 역할, 직접 택한 대조 행동 한 가지, "
            "그 뒤 관찰된 결과를 설명해 주세요."
        ),
        "follow_ups": [
            "방금 말씀하신 자료를 수정했다면 무엇을 근거로 어느 값을 바꿨고, 수정하지 않았다면 그 이유와 다음 확인 조치는 무엇이었습니까?",
            "앞서 언급한 결과에서 변화를 확인했다면 어떤 기록으로 확인했고, 변화가 없었다면 어느 자료나 판단을 다시 점검했습니까?",
            "마감 전까지 불일치가 해소되지 않을 경우, 당시 권한 안에서 수치의 사용 범위와 승인 요청 시점을 어떻게 정하겠습니까?",
        ],
        "evaluation_points": [
            "불일치가 발생한 실제 맥락과 본인의 역할을 구체적으로 구분한다",
            "원자료의 출처와 집계 기준을 연결해 직접 수행한 대조 행동을 설명한다",
            "수정 여부와 관계없이 선택 근거 및 권한에 맞는 다음 조치를 제시한다",
            "변경 기록이나 재점검 결과처럼 관찰 가능한 결과 증거를 제시한다",
        ],
    },
    {
        "case_id": "plan_actual_gap",
        "type": "발표면접",
        "question": (
            "한 연구지원 사업의 분기별 계획·집행·성과 추이표에서 집행 수준은 계획과 "
            "비슷하지만 성과 건수는 계획보다 계속 낮게 나타납니다. 가장 중요한 차이 한 "
            "건의 성격을 판정하고, 계획 기준·실적 관측값·차이의 귀속 근거가 드러나는 "
            "분석표 한 장으로 발표해 주세요."
        ),
        "follow_ups": [
            "방금 차이의 근거로 사용한 항목 중 출처나 측정 기간이 다르다면 분석표의 판정을 어떻게 조정하겠습니까?",
            "앞서 제시한 판정과 반대되는 자료가 추가된다면 기존 설명을 유지하거나 변경할 기준은 무엇입니까?",
            "판정된 차이에 대응할 수 있는 두 가지 후속 조치를 비교할 때 어떤 조건을 우선 확인하겠습니까?",
        ],
        "evaluation_points": [
            "계획과 실적의 범위·기간·단위를 맞춰 비교한다",
            "가장 중요한 차이 한 건을 다른 변동과 구분해 판정한다",
            "분석표에서 관측값과 차이의 귀속 근거를 추적 가능하게 연결한다",
            "반대 자료나 기준 변경에 따라 판정을 수정할 조건을 설명한다",
        ],
    },
    {
        "case_id": "research_fund_rule",
        "type": "직무지식면접",
        "question": (
            "지원기관 지침에는 직접비의 세부 용도 변경 전에 기관 승인을 받도록 되어 "
            "있지만, 내부 업무지침에는 같은 비목 안의 변경을 담당 부서 확인만으로 처리할 "
            "수 있다고 적혀 있습니다. 연구자가 긴급 현장조사 비용을 먼저 집행했고 사전 "
            "승인 기록은 없습니다. 담당자 권한에서 적용할 근거와 처리 경계를 판단하고, "
            "적용 근거·예외 가능성·보완 요구가 담긴 검토 메모 한 장을 제시해 주세요."
        ),
        "follow_ups": [
            "방금 우선 적용한다고 한 근거가 해당 과제의 협약 조건에는 포함되지 않았다면 판단을 어떻게 바꾸겠습니까?",
            "앞서 언급한 예외를 인정한다면 필요한 사실이나 증빙이 빠졌을 때 보완을 요청할지 승인권자에게 이송할지 어떤 기준으로 나누겠습니까?",
            "유사 집행의 오류를 줄이기 위해 접수 단계에서 반드시 대조할 문서와 확인 기록을 설명해 주세요.",
        ],
        "evaluation_points": [
            "지원기관 조건과 내부 절차의 적용 범위 및 우선관계를 구별한다",
            "사후 집행 사실을 예외로 볼 수 있는 조건과 그 한계를 설명한다",
            "담당자 권한 안의 보완 요청·처리 보류·승인권자 이송 경계를 제시한다",
            "검토 메모에 판단 근거와 필요한 증빙을 서로 연결한다",
        ],
    },
    {
        "case_id": "review_report_writing",
        "type": "경험면접",
        "question": (
            "예산계획과 결산자료가 맞지 않거나 일부 증빙이 부족한 상태에서 의사결정자에게 "
            "검토 내용을 전달해야 했던 실제 사례를 소개해 주세요. 직접 같은 경험이 없다면 "
            "학업·프로젝트·인턴에서 가장 가까운 사례도 좋습니다. 당시 맡은 역할, 보고서에 "
            "직접 반영한 행동 한 가지, 활용 과정에서 관찰된 결과를 설명해 주세요."
        ),
        "follow_ups": [
            "방금 언급한 보고서를 작성했다면 확정된 내용과 확인 중인 내용을 어떻게 구분해 본문이나 주석에 배치했으며, 작성하지 않았다면 어떤 방식으로 판단 상태를 전달했습니까?",
            "앞서 말한 결과가 의사결정에 활용됐다면 무엇으로 확인했고, 활용되지 않았다면 보고 내용이나 증빙 연결에서 무엇을 다시 점검했습니까?",
            "원자료와 보고서의 설명이 다를 때 독자가 근거를 역추적할 수 있도록 어떤 참조 정보를 남기겠습니까?",
        ],
        "evaluation_points": [
            "자료 불일치 또는 증빙 부족이 있었던 실제 맥락과 본인의 역할을 밝힌다",
            "확정 내용과 확인 중인 내용을 독자가 오인하지 않도록 구분한 행동을 설명한다",
            "본문·주석과 원자료 증빙 사이의 추적 관계를 제시한다",
            "보고 내용의 활용 여부를 확인한 결과 또는 재점검 조치를 제시한다",
        ],
    },
    {
        "case_id": "resource_allocation_fairness",
        "type": "발표면접",
        "question": (
            "다음 분기 지원 자원 총량은 동결됐지만, 성과가 높은 연구지원 사업은 수요 "
            "증가를 이유로 확대를 요청하고 접근성이 낮은 이용자를 지원하는 사업은 서비스 "
            "공백 방지를 이유로 현 수준 유지를 요청하고 있습니다. 정확한 총량 수치는 "
            "제공되지 않습니다. 어느 사업을 상대적으로 우선할지 결정하고, 공통 기준·불리해질 "
            "수 있는 영향·조정 가능한 경계가 담긴 배분안 한 장으로 발표해 주세요."
        ),
        "follow_ups": [
            "방금 선택한 우선순위로 불리해지는 대상이나 효과를 빠뜨렸다면 이를 배분안에 어떻게 반영하겠습니까?",
            "앞서 적용한 공통 기준과 충돌하는 새 실적자료가 나온다면 어느 조건에서 배분안을 수정하거나 승인권자에게 이송하겠습니까?",
            "배분 결과가 예상과 달랐을 때 확인할 지표, 재검토 시점, 수정안을 결정할 담당 역할을 설명해 주세요.",
        ],
        "evaluation_points": [
            "서로 다른 사업에 일관되게 적용할 비교 기준을 제시한다",
            "상대적 우선순위와 그 선택으로 불리해질 수 있는 영향을 함께 설명한다",
            "자료 변화에 따른 조정 경계와 승인권자 이송 조건을 구분한다",
            "결과 확인 지표·재검토 시점·수정 담당 역할을 연결한다",
        ],
    },
)


@pytest.mark.parametrize(
    "item",
    FINAL_EDITORIAL_REALISM_CASES,
    ids=lambda item: str(item["case_id"]),
)
def test_final_editorial_questions_match_blind_human_realism(
    item: dict[str, object],
) -> None:
    result = evaluate_question_realism({**item, "question_source": "codex_cli"})

    assert result["policy_version"] == "field-realism-v3.14"
    assert result["passed"] is True, (item["case_id"], result["issues"])
    assert result["checks"]["no_prescribed_answer"] is True
    assert result["metrics"]["adaptive_follow_up_count"] >= 2
    assert result["metrics"]["overload_warning"] is False
    if item["type"] in {"발표면접", "상황면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"}:
        assert result["checks"]["concrete_scenario"] is True


@pytest.mark.parametrize(
    "follow_up",
    [
        "방금 차이의 근거로 사용한 항목 중 출처나 측정 기간이 다르다면 분석표의 판정을 어떻게 조정하겠습니까?",
        "앞서 언급한 예외를 인정한다면 필요한 사실이나 증빙이 빠졌을 때 보완을 요청할지 승인권자에게 이송할지 어떤 기준으로 나누겠습니까?",
        "앞서 말한 결과가 의사결정에 활용됐다면 무엇으로 확인했고, 활용되지 않았다면 보고 내용이나 증빙 연결에서 무엇을 다시 점검했습니까?",
    ],
)
def test_answer_owned_conditional_branch_is_adaptive(follow_up: str) -> None:
    result = evaluate_question_realism(
        "업무 자료를 검토해 판단한 경험을 말씀해 주세요.",
        method="경험면접",
        follow_ups=[follow_up],
        question_source="openai_api",
    )

    assert result["metrics"]["adaptive_follow_up_count"] == 1


@pytest.mark.parametrize(
    "follow_up",
    [
        "출처나 측정 기간이 다르다면 분석표의 판정을 어떻게 조정하겠습니까?",
        "방금 차이의 근거로 사용한 항목을 다시 설명해 주세요.",
        "필요한 사실이나 증빙이 빠졌을 때 보완과 이송 기준은 무엇입니까?",
        "앞서 언급한 예외를 다시 설명해 주세요.",
        "결과가 활용됐다면 확인 기록은 무엇입니까?",
        "앞서 말한 결과를 한 번 더 설명해 주세요.",
        "판정된 차이에 대응할 두 가지 후속 조치를 비교해 주세요.",
    ],
    ids=[
        "gap_owner_removed",
        "gap_relation_removed",
        "exception_owner_removed",
        "exception_relation_removed",
        "result_owner_removed",
        "result_relation_removed",
        "generic_next_step",
    ],
)
def test_owner_or_relational_branch_removal_is_not_adaptive(
    follow_up: str,
) -> None:
    result = evaluate_question_realism(
        "업무 자료를 검토해 판단한 경험을 말씀해 주세요.",
        method="경험면접",
        follow_ups=[follow_up],
        question_source="openai_api",
    )

    assert result["metrics"]["adaptive_follow_up_count"] == 0
    assert result["checks"]["answer_adaptive_follow_ups"] is False


def test_generic_relative_allocation_without_two_positions_is_not_concrete() -> None:
    result = evaluate_question_realism(
        {
            "type": "발표면접",
            "question": (
                "다음 분기 지원 자원 총량은 동결됐습니다. 사업별 배분안을 발표해 주세요."
            ),
            "follow_ups": [],
            "question_source": "openai_api",
        }
    )

    assert result["checks"]["concrete_scenario"] is False


def test_relative_requests_without_candidate_tradeoff_are_not_a_dilemma() -> None:
    result = evaluate_question_realism(
        {
            "type": "발표면접",
            "question": (
                "다음 분기 자원 총량은 동결됐습니다. 한 사업은 확대를 요청하고 다른 "
                "사업은 현 수준 유지를 요청하고 있습니다. 현황을 요약해 주세요."
            ),
            "follow_ups": [],
            "question_source": "openai_api",
        }
    )

    assert result["metrics"]["scenario_signals"]["dilemma"] is False
    assert result["checks"]["concrete_scenario"] is False


@pytest.mark.parametrize(
    "question",
    [
        (
            "기관 승인 권한이 없지만 지원자가 직접 승인해 집행하고, 발생한 손실을 "
            "본인이 책임지는 조치를 제시하십시오."
        ),
        (
            "집행 증가와 성과 하락이 나타났고 민원도 같은 달에 증가했습니다. "
            "민원을 원인 한 가지로 판정해 발표하십시오."
        ),
    ],
    ids=["forced_authority_and_liability", "cooccurrence_as_cause"],
)
def test_forced_authority_or_causal_leading_remains_blocked(question: str) -> None:
    result = evaluate_question_realism(
        {
            "type": "발표면접",
            "question": question,
            "follow_ups": [],
            "question_source": "openai_api",
        }
    )

    assert result["checks"]["no_prescribed_answer"] is False
    assert "candidate_answer_prescribed" in result["issue_codes"]


def test_final_editorial_oracle_is_complete() -> None:
    assert len(FINAL_EDITORIAL_REALISM_CASES) == 5
    assert len({item["case_id"] for item in FINAL_EDITORIAL_REALISM_CASES}) == 5
