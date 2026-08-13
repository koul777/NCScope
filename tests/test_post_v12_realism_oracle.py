from __future__ import annotations

import pytest

from app.services.question_realism import evaluate_question_realism


# Frozen from the latest-code, real-PDF Codex run on 2026-08-14.  The report is
# deliberately not read by this test: these eight rows are the out-of-sample
# false negatives that v3.5 exposed after the earlier corpora had been fixed.
LATEST_REALISM_FIXTURES = (
    (
        "plan_actual_gap",
        "발표면접",
        "세 사업의 월별 계획·집행·성과 추이표와 관련 민원 요약을 검토한 결과, 한 사업은 집행액이 계획에 근접하지만 성과가 두 달 연속 크게 낮아졌습니다. 이 사업에서 가장 중요한 차이의 원인을 하나로 판정하고, 계획값·실적값·원인 근거가 보이는 분석표 한 장으로 발표해 주세요.",
        [
            "발표에서 원인 근거로 선택한 수치가 단순한 시기 차이가 아니라는 점을 어떤 자료와 대조해 확인하시겠습니까?",
            "앞서 판정한 원인과 반대되는 자료가 추가로 확인된다면 분석표의 어느 부분을 어떻게 수정하시겠습니까?",
            "판정한 원인에 대응할 수 있는 조정 대안 두 가지를 비교하고, 제한된 예산에서 먼저 시행할 하나와 그 이유를 설명해 주세요.",
        ],
    ),
    (
        "research_fund_rule",
        "직무지식면접",
        "연구책임자가 제출한 회의비 집행서에는 사업 공통지침상 인정되는 증빙은 갖춰져 있지만, 지원기관의 해당 사업 안내에서는 참석자와 연구과제의 관련성을 추가로 확인하도록 정하고 있습니다. 공통지침과 사업별 안내의 목적·적용 대상·특례 여부를 따져 이 건의 인정, 보완 또는 반려 중 하나를 판단하고, 적용 근거·판정·요구 증빙이 담긴 검토기록 한 건을 제시해 주세요.",
        [
            "방금 적용 우선순위를 정할 때 언급한 근거가 이 과제에는 적용되지 않는다는 사실이 확인되면, 어떤 조건을 확인해 판정을 바꾸시겠습니까?",
            "앞서 제시한 검토기록에서 연구책임자가 가장 이의를 제기할 부분은 무엇이며, 그 오류나 누락을 어떤 원자료와 대조해 확인하시겠습니까?",
            "같은 유형의 집행서류가 다시 접수될 때 검토 편차를 줄이기 위한 완료 확인 기록에는 무엇을 남기겠습니까?",
        ],
    ),
    (
        "agency_negotiation",
        "토론면접",
        "[토론과제] 공동 연구사업의 중간보고가 임박한 가운데 주관기관은 일정 준수를 위해 핵심 결과만 먼저 제출하자는 입장이고, 검증기관은 원자료와 산출 과정까지 함께 받아야 검토를 시작할 수 있다는 입장입니다. 양측 주장의 근거를 확인한 뒤 이번 제출에서 수용할 경계를 결정하고, 합의 범위·유보 사항·책임 주체가 담긴 협의기록을 제시하세요. 합의가 어렵다면 남은 쟁점과 결정권자에게 넘길 기준을 기록하세요.",
        [
            "방금 수용한 상대 측 요구와 받아들이지 않은 요구를 나눈 기준은 무엇이며, 그 기준을 확인하려면 어떤 일정·자료·검증 사실이 필요합니까?",
            "앞서 남겨 둔 유보 사항이 상대 기관의 필수 조건이라고 확인되면, 어디까지 양보하고 어느 지점부터 결정권자에게 넘기시겠습니까?",
            "협의 결과의 적용 대상과 예외, 이행 책임자 및 후속 확인 시점을 어떻게 정해야 같은 갈등이 반복되지 않겠습니까?",
        ],
    ),
    (
        "personal_data_protection",
        "인바스켓면접",
        "오전 중 세 건이 동시에 도착했습니다. 한 시간 안에 보내 달라는 외부 연구자의 참여자 명단 요청에는 연락처까지 포함되어 있고 이용 목적과 제공 근거가 적혀 있지 않습니다. 오늘 결재가 필요한 인사 현황 보고서에는 다른 직원의 평가자료가 잘못 첨부되었으며, 오후까지 처리해야 하는 당사자의 정보 정정 요청은 담당자가 부재 중입니다. 업무 목적에 필요한 최소 항목만 권한 있는 사람에게 제공하고, 근거가 불명확한 제공은 보류한다는 원칙에 따라 처리 순서와 처리 주체를 결정한 뒤, 순서·주체·보류 사유가 담긴 처리대장을 제시해 주세요.",
        [
            "방금 1순위로 둔 건과 그 처리 주체를 선택한 이유를 목적의 명확성, 제공 근거, 피해 가능성 중 어느 기준으로 설명하시겠습니까?",
            "앞서 보류하거나 위임한 요청에서 권한 또는 이용 목적이 추가로 확인되면, 제공 항목과 열람 범위를 어떻게 다시 정하시겠습니까?",
            "긴급한 외부 요청이라도 본인 동의 없이 처리할 수 있는 별도 근거가 제시된다면, 적용 대상과 최소 제공 범위를 어떤 기록으로 확인하겠습니까?",
        ],
    ),
    (
        "review_report_writing",
        "경험면접",
        "예산계획표와 결산 원장의 실적이 서로 맞지 않았지만 보고 마감은 임박했던 실제 사례를 말씀해 주십시오. 본인이 가장 중요하다고 판정한 차이 한 건, 그 판단을 위해 취한 행동, 그리고 계획값·실적값·차이 근거가 드러나도록 직접 작성한 검토보고서가 어떻게 활용되었는지 결과 증거와 함께 설명해 주십시오.",
        [
            "방금 중요하다고 판단한 차이에 대해, 어떤 원자료를 서로 대조했으며 다른 차이보다 먼저 보고할 만하다고 본 근거는 무엇이었습니까?",
            "앞서 언급한 검토보고서에서 본인이 직접 작성하거나 수정한 부분을 짚고, 잠정값·증빙 부족·담당자 의견을 어떻게 구분해 독자가 오해하지 않게 했는지 설명해 주십시오.",
            "직접 경험이 없다면, 유사한 자료 불일치 상황을 가정하여 보고서의 핵심 표와 품질 확인 방법을 제시해 주십시오.",
        ],
    ),
    (
        "civil_form_process",
        "상황면접",
        "관계기관 제출 서식에는 접수일과 처리 결과가 필수인데 접수일이 비어 있고, 내부 민원 기록에는 날짜가 있으나 같은 건임을 확인할 식별정보 일부가 다릅니다. 제출 마감은 오늘이고 담당 기관에는 한 차례만 확인할 수 있습니다. 내부 기록의 날짜를 서식에 반영할 수 있는지 먼저 판단하고, 항목별 출처·미확정 항목·수정 이력이 표시된 보완 서식 한 부를 제시해 주십시오.",
        [
            "방금 내린 첫 판단에서 두 기록을 같은 민원으로 본 근거가 부족하다면, 담당 기관에 무엇을 우선 확인하고 답변에 따라 서식을 어떻게 바꾸겠습니까?",
            "앞서 제시한 보완 서식에 누락되었거나 출처가 불분명한 항목이 있다면, 임의 기입을 막으면서 마감을 관리할 표시와 회신 기록을 어떻게 남기겠습니까?",
            "담당 기관의 답변이 마감 전에 오지 않을 때 제출·보완 예정 통지·기한 조정 요청 중 하나를 고르는 기준은 무엇입니까?",
        ],
    ),
    (
        "resource_allocation_fairness",
        "발표면접",
        "다음 분기 총인력과 예산이 동결된 가운데, 연구지원 세 사업이 모두 증액을 요구하고 있습니다. 한 사업은 최근 실적이 급감했지만 의무 지원 대상이 많고, 다른 사업은 실적이 높으나 특정 부서만 이용하며, 나머지 사업은 신규 과제라 비교 실적이 없습니다. 성과가 높은 사업에 몰아 달라는 경영진의 요구와 최소 서비스 유지를 요구하는 현장 의견이 충돌할 때 배분을 하나로 결정하고, 공통 기준·사업별 조정량·예외 사유가 보이는 배분안 한 장을 발표해 주십시오.",
        [
            "방금 적용한 공통 기준 때문에 가장 큰 감축을 받는 사업의 반발이 예상됩니다. 그 기준을 유지하며 본인이 감수할 불이익과 결정 결과에 대한 책임을 어떻게 설명하겠습니까?",
            "앞서 선택한 배분안과 비교해 탈락시킨 대안 하나를 제시하고, 어느 조건이 바뀌면 그 대안으로 전환할지 설명해 주십시오.",
            "배분 후 성과를 확인하려면 측정 대상의 포함·제외 범위와 관찰 기간을 어떻게 정하며, 누가 후속 점검을 맡아야 합니까?",
        ],
    ),
    (
        "objective_evaluation",
        "토론면접",
        "[토론과제] 신규 문화사업의 성과를 판정하려는데, 기획부서는 모든 사업에 정량 증빙을 엄격히 적용해야 한다고 주장하고 현업부서는 참여자 특성과 장기 효과를 반영하지 않으면 성과가 왜곡된다고 맞섭니다. 두 입장을 검토한 뒤 어느 조건에서 어떤 근거를 우선할지 하나로 판단하고, 확인할 사실과 적용 범위가 담긴 공동 평가 원칙안 한 장을 제시하십시오. 합의가 어렵다면 남은 쟁점과 결정권자에게 넘길 기준을 원칙안에 표시하십시오.",
        [
            "방금 말씀하신 원칙을 정하기 전에 반드시 확인해야 할 자료나 사실은 무엇이며, 그 사실이 달라지면 판단을 어떻게 바꾸겠습니까?",
            "앞서 일부 수용한 상대 측 주장과 남겨 둔 예외를 기준으로, 수용할 수 없는 경계와 그로 인해 감수할 일정 지연 또는 내부 반발을 설명해 주십시오.",
            "공통 원칙의 적용 대상과 제외 대상을 어떻게 구분하고, 적용 결과를 누가 어떤 기록으로 점검해야 합니까?",
        ],
    ),
)


@pytest.mark.parametrize(
    ("case_id", "method", "question", "follow_ups"),
    LATEST_REALISM_FIXTURES,
    ids=[row[0] for row in LATEST_REALISM_FIXTURES],
)
def test_latest_real_pdf_realism_oracle_separates_one_prescribed_answer(
    case_id: str,
    method: str,
    question: str,
    follow_ups: list[str],
) -> None:
    result = evaluate_question_realism(
        {
            "type": method,
            "question": question,
            "follow_ups": follow_ups,
            "question_source": "openai_api",
        }
    )

    assert result["policy_version"] == "field-realism-v3.14"
    if case_id == "personal_data_protection":
        assert result["passed"] is False
        assert result["issue_codes"] == ["candidate_answer_prescribed"]
    else:
        assert result["passed"] is True, result
        assert result["issue_codes"] == []
    assert result["metrics"]["adaptive_follow_up_count"] >= 2


@pytest.mark.parametrize(
    "follow_up",
    [
        "방금 적용한 기준을 다시 설명해 주세요.",
        "앞서 판단한 내용을 한 번 더 말씀해 주세요.",
        "앞서 수용한 상대 측 주장을 요약해 주세요.",
    ],
)
def test_answer_ownership_without_a_new_branch_is_not_adaptive(
    follow_up: str,
) -> None:
    result = evaluate_question_realism(
        "마감 전에 상충한 자료를 조정해 결론을 낸 경험을 말씀해 주세요.",
        method="경험면접",
        follow_ups=[follow_up],
        question_source="openai_api",
    )

    assert result["checks"]["answer_adaptive_follow_ups"] is False
    assert result["metrics"]["adaptive_follow_up_count"] == 0


def test_two_actors_wanting_the_same_full_evidence_are_not_a_dilemma() -> None:
    result = evaluate_question_realism(
        {
            "type": "토론면접",
            "question": (
                "[토론과제] 주관기관은 원자료와 산출 과정을 먼저 제출하자는 입장이고, "
                "검증기관도 원자료와 산출 과정을 함께 받아 검토하자는 입장입니다. "
                "검토 순서를 토론해 주세요."
            ),
            "follow_ups": [],
            "question_source": "openai_api",
        }
    )

    assert result["metrics"]["scenario_signals"]["dilemma"] is False
    assert result["checks"]["no_generic_template_scaffolding"] is False


@pytest.mark.parametrize(
    "question",
    [
        (
            "외부기관의 참여자 명단 요청에는 이용 목적과 제공 근거가 없고, 내부 "
            "보고서에는 다른 직원 자료가 잘못 첨부되어 있습니다. 어떤 원칙과 "
            "처리 경계를 적용할지 결정해 주세요."
        ),
        (
            "내부 지침에 적힌 개인정보 보호 원칙과 요청 목적을 함께 검토해 제공 "
            "가능 여부를 판단해 주세요."
        ),
    ],
)
def test_fact_pattern_or_named_principle_does_not_itself_prescribe_answer(
    question: str,
) -> None:
    result = evaluate_question_realism(
        {
            "type": "인바스켓면접",
            "question": question,
            "follow_ups": [],
            "question_source": "openai_api",
        }
    )

    assert result["checks"]["no_prescribed_answer"] is True
    assert "candidate_answer_prescribed" not in result["issue_codes"]
