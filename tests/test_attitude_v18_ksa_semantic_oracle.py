from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class NeutralAttitudeCase:
    case_id: str
    method: str
    factor: str
    question: str
    paraphrase: str
    generic_intention: str
    rationale: str


# Frozen independently of runtime reports.  These prompts deliberately leave
# the defensible response open: attitude evidence is the candidate's selected
# trade-off, executable action, and checkable consequence/record.  None asks
# the candidate to claim personal sacrifice or unilateral liability.
NEUTRAL_ATTITUDE_CASES = (
    NeutralAttitudeCase(
        "numeric_accuracy",
        "상황면접",
        "수리적 정확도를 확보하려는 자세",
        "성과 집계 마감 직전 원자료와 집계표 수치가 불일치합니다. 예정대로 제출·"
        "잠정 표시 후 제출·검증 완료 뒤 제출 중 어느 처리를 선택할지 정하고, "
        "원자료 값을 어떤 순서로 대조·검산할지 설명해 주세요. 처리 근거·처리 "
        "상태·수정 전후 값을 담은 검증 내역을 작성해 주세요.",
        "보고 기한에 원자료와 취합 수치가 맞지 않습니다. 즉시 제출·잠정 제출·"
        "정정 뒤 제출 가운데 하나를 결정하고, 집계값을 어떻게 재집계해 확인할지 "
        "말씀해 주세요. 판단 이유·승인 상태·변경 전후 값을 담은 정정 기록을 "
        "제시해 주세요.",
        "성과 집계 마감 직전 원자료와 집계표 수치가 불일치합니다. 항상 정확성을 "
        "중요하게 생각하며 관련 부서와 원만히 협의하겠다는 다짐을 설명해 주세요.",
        "제출 여부를 열어 둔 채 원자료 검산 조치와 수정 결과를 같은 기록으로 "
        "확인하게 하므로 자기희생 문구 없이도 정확성 태도가 관찰된다.",
    ),
    NeutralAttitudeCase(
        "measurement_framework",
        "창의적 문제해결력면접",
        "성과측정 기준 수립을 위한 체계적 사고",
        "성과 집계에서 같은 활동이 부서마다 누락되거나 중복되고 공시 마감은 "
        "임박했습니다. 기존 기준 유지·일부 수정·재설계 중 어느 기준을 채택할지 "
        "결정해 주세요. 선택한 기준을 표본에 어떻게 시험·적용할지와 관찰 결과·"
        "변경 조건을 설명하고, 측정 차원·포함·제외·집계 기간·시험 절차가 담긴 "
        "측정정의서를 작성해 주세요.",
        "공동사업 실적의 누락과 중복이 반복되지만 즉시 집계하라는 요구가 있습니다. "
        "현 원칙 유지·보완·교체 중 하나를 선택하고, 그 집계 원칙을 자료에 어떻게 "
        "검증해 반영할지와 확인 결과·재검토 조건을 설명해 주세요. 측정 단위·산입·"
        "미산입·관찰 기간을 적은 집계원칙표를 제시해 주세요.",
        "성과 집계에 누락과 중복이 있고 즉시 공시하라는 요구가 있습니다. 체계적인 "
        "사고로 공정한 측정 기준을 마련하겠다는 원칙만 설명해 주세요.",
        "기준 유지·수정·재설계를 열어 두고 선택한 기준의 시험 적용과 관찰 결과에 "
        "따른 변경 조건을 요구해 태도를 행동과 환류로 측정한다.",
    ),
    NeutralAttitudeCase(
        "resource_allocation_fairness",
        "발표면접",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "세 사업이 늘어난 인력·예산을 모두 요구하지만 총량은 동결되어 있습니다. "
        "현 배분 유지·공통 비율 조정·단계 지원 중 어느 방식을 선택할지 정하고, "
        "공통 기준으로 사업별 예산과 인력을 어떻게 배분할지 설명해 주세요. 공통 "
        "기준·사업별 배정량·성과 확인 지표·재조정 조건을 담은 배분안 한 장을 "
        "발표해 주세요.",
        "여러 과제가 추가 예산과 인력을 요청하지만 가용 자원은 고정돼 있습니다. "
        "기존 배정 유지·차등 조정·순차 지원 중 한 방식을 결정하고, 과제별 지원량을 "
        "어떻게 배정할지 발표해 주세요. 배분 원칙·조정량·적용 기간·성과 지표가 "
        "보이는 지원조정표에 예외 사유도 함께 제시해 주세요.",
        "세 사업이 예산과 인력을 요구하지만 자원은 부족합니다. 모든 부서에 공정하고 "
        "합리적으로 대응하겠다는 의지만 발표해 주세요.",
        "희소 자원에서 지원 방식을 고르고 사업별 배정안을 제안하며 성과 지표와 "
        "재조정 조건으로 그 선택의 귀결을 점검하게 한다.",
    ),
    NeutralAttitudeCase(
        "objective_evaluation",
        "토론면접",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 시범사업 평가에서 정량 수치와 현장 맥락을 근거로 두 부서가 "
        "서로 다른 등급을 주장합니다. 확인된 수치와 현장 근거를 공통 기준에 어느 "
        "범위까지 반영할지 정한 뒤, 등급을 유지·조정·보류 중 어떻게 처리할지 "
        "선택하고 적용 절차를 설명해 주세요. 판정 근거·적용 범위·후속 자료의 "
        "재검토 조건이 담긴 평가기록을 제시해 토론해 주세요.",
        "[토론과제] 정량 성과와 지역 운영 여건을 근거로 평가 입장이 충돌합니다. "
        "확인된 수치와 질적 근거를 공동 원칙에 인정할 경계를 정하고, 판정 결과를 "
        "확정·수정·이송 중 어떻게 처리할지 결정해 주세요. 적용 근거·예외 범위·"
        "추가 자료의 변경 조건을 담은 판정원칙안을 제시해 토론해 주세요.",
        "[토론과제] 정량 평가와 현장 입장이 충돌합니다. 어느 쪽에도 치우치지 않고 "
        "항상 객관적으로 토론하겠다는 원칙을 설명해 주세요.",
        "상충 근거의 인정 경계를 먼저 세우고 등급 처리와 재검토 조건을 선택하게 해 "
        "권한을 과장하거나 특정 결론을 강요하지 않는다.",
    ),
    NeutralAttitudeCase(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "데이터의 정확성을 추구하는 태도",
        "내일 공개할 성과 대시보드에서 원천 시스템별 값이 서로 다르고 검증 인력은 "
        "한 명입니다. 예정 공개·잠정 표시·일부 보류·수정 중 어느 처리를 선택할지 "
        "결정하고, 선택한 데이터 항목을 원자료와 어떻게 대조·재집계할지 설명해 "
        "주세요. 대상 항목·원인 가설·최소 검증·중단 기준·확인 결과·변경 조건을 "
        "담은 정합성 판단표를 작성해 주세요.",
        "게시 기한 전 원천별 집계값이 불일치하고 확인 인력이 부족합니다. 갱신 시점 "
        "차이를 원인 가설로 두고 전체 공개·"
        "잠정 게시·오류 항목 보류 가운데 하나를 선택한 뒤, 해당 수치를 어떤 출처와 "
        "검증해 수정할지 설명해 주세요. 비교 대상·간이 검증·판정 경계·관찰 결과·"
        "재검토 조건이 담긴 대조시험표를 제시해 주세요.",
        "공개할 데이터 값이 서로 다르고 검증 인력은 부족합니다. 데이터는 언제나 "
        "정확해야 한다는 신념과 성실하게 검토하겠다는 의지만 설명해 주세요.",
        "공개·잠정·보류 중 선택과 실제 대조·재집계 절차를 묻고 확인 결과와 변경 "
        "조건을 기록하게 하므로 단순한 문제해결 의도와 구별된다.",
    ),
    NeutralAttitudeCase(
        "data_use_ethics",
        "상황면접",
        "기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        "분석 마감이 오늘인데 제공받은 데이터의 수집 목적과 접근 권한, 공유 범위가 "
        "요청 용도와 맞지 않는 부분이 있습니다. 그대로 사용·범위 축소 후 사용·승인 "
        "전 보류·사용 거절 중 어느 처리를 선택할지 결정하고, 선택한 데이터에 범위 "
        "축소·접근 차단·승인 요청 중 어떤 조치를 실행할지 설명해 주세요. 수집 목적·"
        "접근 주체·허용 범위·승인 상태·후속 확인을 담은 접근 검토 대장을 작성해 "
        "주세요.",
        "업무 기한은 임박했지만 자료의 용도와 열람 권한 및 공유 대상이 불명확합니다. "
        "제한 사용·익명 처리·승인까지 보류·거절 중 하나를 판단하고, 해당 자료를 "
        "익명 처리하거나 공유 중단할 실행 절차를 설명해 주세요. 목적·권한·제한 "
        "범위·변경 내역·재검토 조건을 담은 접근검토기록을 제시해 주세요.",
        "마감이 임박했지만 데이터의 목적과 접근 권한이 불분명합니다. 늘 윤리 의식을 "
        "가지고 신중하게 사용하겠다는 일반 원칙만 설명해 주세요.",
        "허용·제한·보류·거절을 열어 두고 선택한 이용 조치와 승인 상태 또는 후속 "
        "확인을 한 기록에 남겨 윤리 태도를 관찰한다.",
    ),
)


def _item(
    case: NeutralAttitudeCase,
    *,
    question: str | None = None,
    source: str = "openai_api",
) -> dict[str, object]:
    return {
        "type": case.method,
        "question_focus": case.factor,
        "question_focus_type": "태도",
        "question_focus_surface": "내부 추적용 표면 힌트",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_attitude_v18_{case.case_id}",
        "question": question or case.question,
        "follow_ups": [
            "선택을 바꿀 반증이나 추가 자료는 무엇입니까?",
            "그 조치의 결과를 어느 시점에 무엇으로 확인하겠습니까?",
            "확인 결과가 예상과 다르면 기록과 처리를 어떻게 고치겠습니까?",
        ],
        "evaluation_points": [
            "상충 조건에서 하나의 처리 방식을 선택한다.",
            "선택을 실행 가능한 대상과 조치로 구체화한다.",
            "결과나 상태를 확인할 기준을 제시한다.",
            "선택·조치·확인을 추적 가능한 산출물에 연결한다.",
        ],
    }


def _case(case_id: str) -> NeutralAttitudeCase:
    return next(case for case in NEUTRAL_ATTITUDE_CASES if case.case_id == case_id)


def test_v18_neutral_attitude_oracle_is_frozen_and_non_leading() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(NEUTRAL_ATTITUDE_CASES) == 6
    assert len({case.case_id for case in NEUTRAL_ATTITUDE_CASES}) == 6
    assert all(len(case.rationale) >= 35 for case in NEUTRAL_ATTITUDE_CASES)
    for case in NEUTRAL_ATTITUDE_CASES:
        assert case.factor not in case.question
        assert all(
            forbidden not in case.question for forbidden in ("본인", "감수", "책임지")
        )


@pytest.mark.parametrize("case", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_v18_accepts_neutral_choice_action_consequence_across_providers(
    case: NeutralAttitudeCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert result["passed"] is True, (case.rationale, result)
    assert all(result["checks"].values()), result


@pytest.mark.parametrize("case", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_v18_accepts_artifact_and_action_paraphrases(
    case: NeutralAttitudeCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(
        _item(case, question=case.paraphrase, source=source)
    )

    assert result["passed"] is True, (case.rationale, result)


@pytest.mark.parametrize("case", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
def test_v18_rejects_generic_attitude_intentions(case: NeutralAttitudeCase) -> None:
    result = evaluate_ksa_measurement(_item(case, question=case.generic_intention))

    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize("target", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
def test_v18_rejects_all_cross_factor_substitutions(
    target: NeutralAttitudeCase,
    source: NeutralAttitudeCase,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity is covered by the positive oracle")

    result = evaluate_ksa_measurement(_item(target, question=source.question))

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


REMOVALS = (
    (
        "numeric_accuracy",
        "choice",
        "예정대로 제출·잠정 표시 후 제출·검증 완료 뒤 제출 중 어느 처리를 선택할지 정하고, ",
        "관계 부서의 의견을 들은 뒤 ",
    ),
    (
        "numeric_accuracy",
        "action",
        "원자료 값을 어떤 순서로 대조·검산할지",
        "관련 원칙을 어떻게 설명할지",
    ),
    (
        "numeric_accuracy",
        "consequence",
        "처리 근거·처리 상태·수정 전후 값을",
        "관련 설명을",
    ),
    (
        "measurement_framework",
        "choice",
        "기존 기준 유지·일부 수정·재설계 중 어느 기준을 채택할지 결정해 주세요. 선택한 기준을",
        "기존 자료를 검토해 주세요. 해당 기준을",
    ),
    (
        "measurement_framework",
        "action",
        "선택한 기준을 표본에 어떻게 시험·적용할지",
        "선택한 기준의 취지를 어떻게 설명할지",
    ),
    (
        "measurement_framework",
        "consequence",
        "관찰 결과·변경 조건을 설명하고, ",
        "검토 의견을 설명하고, ",
    ),
    (
        "resource_allocation_fairness",
        "choice",
        "현 배분 유지·공통 비율 조정·단계 지원 중 어느 방식을 선택할지 정하고, ",
        "각 부서의 의견을 요약하고, ",
    ),
    (
        "resource_allocation_fairness",
        "action",
        "공통 기준으로 사업별 예산과 인력을 어떻게 배분할지 설명해 주세요. 공통 기준·사업별 배정량",
        "공통 원칙을 어떻게 설명할지 말씀해 주세요. 회의 주제",
    ),
    (
        "resource_allocation_fairness",
        "consequence",
        "성과 확인 지표·재조정 조건",
        "검토 의견",
    ),
    (
        "objective_evaluation",
        "choice",
        "등급을 유지·조정·보류 중 어떻게 처리할지 선택하고 ",
        "양측 의견을 정리하고 ",
    ),
    (
        "objective_evaluation",
        "action",
        "등급을 유지·조정·보류 중 어떻게 처리할지 선택하고 적용 절차를",
        "어느 입장을 선호할지 선택하고 토론 쟁점을",
    ),
    (
        "objective_evaluation",
        "consequence",
        "후속 자료의 재검토 조건",
        "참여자 의견",
    ),
    (
        "visualization_data_accuracy",
        "choice",
        "예정 공개·잠정 표시·일부 보류·수정 중 어느 처리를 선택할지 결정하고, ",
        "관련 부서의 설명을 들은 뒤 ",
    ),
    (
        "visualization_data_accuracy",
        "action",
        "선택한 데이터 항목을 원자료와 어떻게 대조·재집계할지",
        "관련 데이터 원칙을 어떻게 설명할지",
    ),
    (
        "visualization_data_accuracy",
        "consequence",
        "중단 기준·확인 결과·변경 조건",
        "판정 경계",
    ),
    (
        "data_use_ethics",
        "choice",
        "그대로 사용·범위 축소 후 사용·승인 전 보류·사용 거절 중 어느 처리를 선택할지 결정하고, 선택한 데이터에",
        "관련 규정을 확인하고, 해당 데이터에",
    ),
    (
        "data_use_ethics",
        "action",
        "선택한 데이터에 범위 축소·접근 차단·승인 요청 중 어떤 조치를 실행할지",
        "선택한 데이터의 일반 원칙을 어떻게 설명할지",
    ),
    (
        "data_use_ethics",
        "consequence",
        "승인 상태·후속 확인",
        "검토 의견",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "dimension", "old", "new"),
    REMOVALS,
    ids=lambda value: str(value),
)
def test_v18_rejects_choice_action_or_consequence_removal(
    case_id: str,
    dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    mutated = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    assert result["checks"]["ksa_type_operationalized"] is False, (
        dimension,
        result,
    )
    assert result["passed"] is False, (dimension, result)


NEGATED_ACTIONS = (
    ("numeric_accuracy", "대조·검산할지", "대조하거나 검산하지 않을지"),
    ("measurement_framework", "시험·적용할지", "시험하거나 적용하지 않을지"),
    ("resource_allocation_fairness", "배분할지", "배분하지 않을지"),
    ("objective_evaluation", "처리할지", "처리하지 않을지"),
    ("visualization_data_accuracy", "대조·재집계할지", "대조하거나 재집계하지 않을지"),
    (
        "data_use_ethics",
        "범위 축소·접근 차단·승인 요청 중 어떤 조치를 실행할지",
        "범위 축소도 접근 차단도 승인 요청도 실행하지 않을지",
    ),
)


@pytest.mark.parametrize(("case_id", "old", "new"), NEGATED_ACTIONS)
def test_v18_does_not_count_negated_actions(
    case_id: str,
    old: str,
    new: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    result = evaluate_ksa_measurement(
        _item(case, question=case.question.replace(old, new, 1))
    )

    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize("case", NEUTRAL_ATTITUDE_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_v18_rejects_attitude_keyword_salad(
    case: NeutralAttitudeCase,
    source: str,
) -> None:
    salad = (
        f"{case.factor} 선택 행동 결과 확인 기록 기준 적용 검증 수정 보류 "
        "제시해 주세요."
    )
    result = evaluate_ksa_measurement(_item(case, question=salad, source=source))

    assert result["passed"] is False, result
