from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class EditorialCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    paraphrase: str
    rationale: str


# Independent human oracle frozen in source.  It has no report path, loader,
# generated annotation, or case-specific production bypass.
EDITORIAL_V19_CASES = (
    EditorialCase(
        "numeric_accuracy",
        "경험면접",
        "태도",
        "수리적 정확도를 확보하려는 자세",
        "성과 보고 마감이 임박한 상황에서 부서 원자료와 집계표의 값이 서로 달랐던 "
        "실제 사례를 말씀해 주세요. 같은 경험이 없다면 학업·프로젝트·봉사에서 "
        "가장 가까운 사례도 좋습니다. 당시 본인의 역할, 직접 택한 대조 행동 한 "
        "가지, 그 뒤 관찰된 결과를 설명해 주세요.",
        "제출 기한 직전 원자료와 취합 수치에 차이가 생긴 실제 또는 가장 가까운 "
        "경험을 설명해 주세요. 당시 맡은 역할에서 본인이 검산을 선택해 직접 수행한 "
        "행동과 이후 확인된 결과를 말씀해 주세요.",
        "실제·근접 경험에서 지원자의 역할, 스스로 고른 정확성 행동, 확인 가능한 "
        "귀결을 연속해 묻기 때문에 희생을 강요하지 않고 태도를 관찰한다.",
    ),
    EditorialCase(
        "plan_actual_gap",
        "발표면접",
        "기술",
        "계획대비 실적 분석 능력",
        "한 연구지원 사업의 분기별 계획·집행·성과 추이표에서 집행 수준은 계획과 "
        "비슷하지만 성과 건수는 계획보다 계속 낮게 나타납니다. 가장 중요한 차이 한 "
        "건의 성격을 판정하고, 계획 기준·실적 관측값·차이의 귀속 근거가 드러나는 "
        "분석표 한 장으로 발표해 주세요.",
        "사업의 월별 계획·집행·성과 추이에서 집행은 목표와 비슷한데 성과는 차이가 "
        "납니다. 핵심 격차를 선별해 판정하고, 기준선·측정값·격차의 설명 근거가 "
        "연결된 비교 분석표로 발표해 주세요.",
        "계획과 관측 실적을 맞춰 핵심 차이를 판정하고 그 귀속 근거를 추적 가능한 "
        "분석표로 만들게 하므로 분석 기술이 직접 드러난다.",
    ),
    EditorialCase(
        "research_fund_rule",
        "직무지식면접",
        "지식",
        "부처별 연구개발사업 관리규정에 대한 지식",
        "지원기관 지침에는 직접비의 세부 용도 변경 전에 기관 승인을 받도록 되어 "
        "있지만, 내부 업무지침에는 같은 비목 안의 변경을 담당 부서 확인만으로 "
        "처리할 수 있다고 적혀 있습니다. 연구자가 긴급 현장조사 비용을 먼저 "
        "집행했고 사전 승인 기록은 없습니다. 담당자 권한에서 적용할 근거와 처리 "
        "경계를 판단하고, 적용 근거·예외 가능성·보완 요구가 담긴 검토 메모 한 장을 "
        "제시해 주세요.",
        "지원기관 기준은 연구비 변경 전에 승인을 요구하지만 기관 내부 지침은 같은 "
        "항목이면 부서 확인으로 가능하다고 합니다. 비용은 이미 집행됐고 승인 기록은 "
        "없습니다. 어느 근거를 적용해 인정 또는 보완할지 판단하고, 판단 근거·예외 "
        "조건·필요 증빙을 적은 적용 의견 기록을 제시해 주세요.",
        "서로 다른 권위의 승인 요건을 실제 선집행 사실에 적용해 담당 권한의 처리 "
        "경계를 판정하고 예외·보완 필드가 있는 독립 메모를 요구한다.",
    ),
    EditorialCase(
        "review_report_writing",
        "경험면접",
        "기술",
        "분석·검토보고서 작성 능력",
        "예산계획과 결산자료가 맞지 않거나 일부 증빙이 부족한 상태에서 "
        "의사결정자에게 검토 내용을 전달해야 했던 실제 사례를 소개해 주세요. 직접 "
        "같은 경험이 없다면 학업·프로젝트·인턴에서 가장 가까운 사례도 좋습니다. "
        "당시 맡은 역할, 보고서에 직접 반영한 행동 한 가지, 활용 과정에서 관찰된 "
        "결과를 설명해 주세요.",
        "예산계획과 결산내역이 불일치하거나 자료가 누락된 실제 또는 유사 프로젝트를 "
        "말씀해 주세요. 당시 본인의 역할에서 검토 내용을 보고서에 직접 작성한 "
        "행동과 의사결정에 사용된 뒤 확인된 결과를 설명해 주세요.",
        "실제·근접 상황에서 보고서 작성자의 역할과 직접 반영 행동, 산출물의 활용 "
        "결과를 묻기 때문에 단순한 작성 의향이 아닌 기술 증거를 얻는다.",
    ),
    EditorialCase(
        "resource_allocation_fairness",
        "발표면접",
        "태도",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "다음 분기 지원 자원 총량은 동결됐지만, 성과가 높은 연구지원 사업은 수요 "
        "증가를 이유로 확대를 요청하고 접근성이 낮은 이용자를 지원하는 사업은 "
        "서비스 공백 방지를 이유로 현 수준 유지를 요청하고 있습니다. 정확한 총량 "
        "수치는 제공되지 않습니다. 어느 사업을 상대적으로 우선할지 결정하고, 공통 "
        "기준·불리해질 수 있는 영향·조정 가능한 경계가 담긴 배분안 한 장으로 "
        "발표해 주세요.",
        "지원 자원은 고정됐지만 여러 사업이 확대를 요청합니다. 서비스 접근과 성과 "
        "근거를 비교해 어느 사업을 우선할지 선택하고, 공통 원칙·불리한 영향·변경 "
        "경계를 담은 지원 배분 계획으로 발표해 주세요.",
        "희소 자원에서 후보자가 상대 우선순위를 선택하고 공통 기준뿐 아니라 불리한 "
        "영향과 수정 경계를 회계하게 해 자기희생 없이 공정 태도를 관찰한다.",
    ),
)


def _item(
    case: EditorialCase,
    *,
    question: str | None = None,
    method: str | None = None,
    source: str = "openai_api",
) -> dict[str, object]:
    return {
        "type": method or case.method,
        "question_focus": case.factor,
        "question_focus_type": case.ksa_type,
        "question_focus_surface": "내부 추적용 표면 힌트",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_editorial_v19_{case.case_id}",
        "question": question or case.question,
        "follow_ups": [
            "방금 판단을 바꿀 추가 근거는 무엇입니까?",
            "그 행동의 결과를 어떤 자료로 확인했습니까?",
            "권한이나 자료가 부족하면 다음 조치를 어떻게 정하겠습니까?",
        ],
        "evaluation_points": [
            "직무상 충돌이나 불일치를 구체적으로 구분한다.",
            "대상과 판단 또는 행동을 연결한다.",
            "결과나 근거를 관찰 가능한 방식으로 제시한다.",
            "산출물 또는 경험 기록으로 답변을 검증할 수 있다.",
        ],
    }


def _case(case_id: str) -> EditorialCase:
    return next(case for case in EDITORIAL_V19_CASES if case.case_id == case_id)


def test_editorial_v19_oracle_is_frozen_and_independently_explained() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(EDITORIAL_V19_CASES) == 5
    assert len({case.case_id for case in EDITORIAL_V19_CASES}) == 5
    assert all(len(case.rationale) >= 45 for case in EDITORIAL_V19_CASES)
    assert all(case.factor not in case.question for case in EDITORIAL_V19_CASES)
    assert "감수" not in "".join(case.question for case in EDITORIAL_V19_CASES)


@pytest.mark.parametrize("case", EDITORIAL_V19_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_editorial_v19_human_a_passes_all_providers(
    case: EditorialCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert result["passed"] is True, (case.rationale, result)
    assert all(result["checks"].values()), result


@pytest.mark.parametrize("case", EDITORIAL_V19_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_editorial_v19_accepts_relational_paraphrases(
    case: EditorialCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(
        _item(case, question=case.paraphrase, source=source)
    )

    assert result["passed"] is True, (case.rationale, result)


@pytest.mark.parametrize("target", EDITORIAL_V19_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", EDITORIAL_V19_CASES, ids=lambda case: case.case_id)
def test_editorial_v19_rejects_all_cross_factor_substitutions(
    target: EditorialCase,
    source: EditorialCase,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity is covered by the positive oracle")

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


REMOVALS = (
    (
        "numeric_accuracy",
        "accuracy-action",
        "직접 택한 대조 행동 한 가지, 그 뒤 관찰된 결과",
        "관련해 가졌던 일반 의견",
    ),
    (
        "numeric_accuracy",
        "mismatch",
        "값이 서로 달랐던",
        "값이 이미 일치했던",
    ),
    (
        "plan_actual_gap",
        "judgment-relation",
        "가장 중요한 차이 한 건의 성격을 판정하고",
        "전체 자료를 읽고",
    ),
    (
        "plan_actual_gap",
        "attribution-field",
        "차이의 귀속 근거",
        "일반 의견",
    ),
    (
        "research_fund_rule",
        "opposed-authority",
        "내부 업무지침에는 같은 비목 안의 변경을 담당 부서 확인만으로 처리할 수 있다고 적혀 있습니다.",
        "동일한 승인 요건이 반복 안내되어 있습니다.",
    ),
    (
        "research_fund_rule",
        "operational-memo-fields",
        "적용 근거·예외 가능성·보완 요구",
        "검토 제목",
    ),
    (
        "review_report_writing",
        "direct-report-action",
        "보고서에 직접 반영한 행동 한 가지",
        "보고서에 대한 일반적인 생각",
    ),
    (
        "review_report_writing",
        "observed-use-result",
        "활용 과정에서 관찰된 결과",
        "작성 원칙",
    ),
    (
        "resource_allocation_fairness",
        "scarcity",
        "지원 자원 총량은 동결됐지만",
        "지원 자원은 모든 요청을 충족할 만큼 충분하고",
    ),
    (
        "resource_allocation_fairness",
        "priority-choice",
        "어느 사업을 상대적으로 우선할지 결정하고",
        "각 사업의 설명을 요약하고",
    ),
    (
        "resource_allocation_fairness",
        "consequence-boundary",
        "불리해질 수 있는 영향·조정 가능한 경계",
        "사업 명칭",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "dimension", "old", "new"),
    REMOVALS,
    ids=lambda value: str(value),
)
def test_editorial_v19_rejects_unique_relation_removals(
    case_id: str,
    dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    result = evaluate_ksa_measurement(
        _item(case, question=case.question.replace(old, new, 1))
    )

    assert result["passed"] is False, (dimension, result)


NEGATIONS = (
    ("numeric_accuracy", "직접 택한 대조 행동", "직접 택했지만 대조하지 않은 행동"),
    ("plan_actual_gap", "차이 한 건의 성격을 판정하고", "차이 한 건을 판정하지 말고"),
    ("research_fund_rule", "처리 경계를 판단하고", "처리 경계를 판단하지 말고"),
    ("review_report_writing", "보고서에 직접 반영한 행동", "보고서에 직접 반영하지 않은 내용"),
    ("resource_allocation_fairness", "우선할지 결정하고", "우선할지 결정하지 말고"),
)


@pytest.mark.parametrize(("case_id", "old", "new"), NEGATIONS)
def test_editorial_v19_does_not_count_negated_actions(
    case_id: str,
    old: str,
    new: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    result = evaluate_ksa_measurement(
        _item(case, question=case.question.replace(old, new, 1))
    )

    assert result["passed"] is False, result


@pytest.mark.parametrize("case", EDITORIAL_V19_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_editorial_v19_rejects_raw_label_keyword_salad(
    case: EditorialCase,
    source: str,
) -> None:
    salad = (
        f"{case.factor} 상황 선택 판단 행동 결과 분석표 검토 메모 배분안 "
        "제시해 주세요."
    )
    result = evaluate_ksa_measurement(_item(case, question=salad, source=source))

    assert result["passed"] is False, result
