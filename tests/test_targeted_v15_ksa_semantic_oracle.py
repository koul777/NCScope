from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class TargetedV15Case:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    follow_ups: tuple[str, str, str]
    rationale: str


# Frozen from the fresh targeted generation after v14.  This fixture is
# deliberately report-independent so regenerating or relabeling an audit file
# cannot change the human oracle.  All three main questions are KSA-A.
TARGETED_V15_CASES = (
    TargetedV15Case(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "오늘 안에 결재할 결산 초안, 내일 오전까지 회신해야 하는 증빙 보완 요청, "
        "한 시간 뒤 회의에 쓰일 경영진 보고자료가 동시에 도착했습니다. 초안에는 "
        "증빙이 없는 금액이 포함되어 있고, 본인은 금액 확정 권한이 없습니다. 무엇을 "
        "먼저 누구에게 맡기거나 직접 처리할지 하나의 우선순위로 결정하고, 확정된 "
        "값과 잠정값의 본문·주석 배치 및 증빙 연결 상태가 함께 보이는 처리결정표를 "
        "제시해 주세요.",
        (
            "방금 1순위로 정한 문서와 처리 주체를 기준으로, 그 선택이 지연시키는 "
            "다른 업무와 누락 위험을 어떻게 통제하겠습니까?",
            "앞서 제시한 처리결정표에서 잠정값 또는 증빙 연결이 불분명하다면, 어떤 "
            "표시와 검토 흔적을 추가하겠습니까?",
            "결재권자가 부재한 채 보고 시각이 도래하면 어느 범위까지 작성하고 어떤 "
            "항목을 승인 대기 상태로 남기겠습니까?",
        ),
        "확정·잠정 값을 본문과 주석에 배치하고 증빙 연결까지 처리표에 적용한다.",
    ),
    TargetedV15Case(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 시범사업의 정량 성과는 목표에 미달했지만 현업은 장기 효과와 "
        "지역별 운영 여건을 반영해 계속사업으로 인정해 달라고 요구하고, 경영진은 "
        "일정 준수를 위해 현재 수치만으로 평가를 종결하라고 요구합니다. 한쪽은 모든 "
        "사업에 동일한 정량 기준을 적용하자는 입장이고, 다른 쪽은 현장 맥락에 따라 "
        "예외를 인정하자는 입장입니다. 일정 지연이나 관계 부서의 반발을 "
        "감수하더라도 본인이 직접 어떤 공통 판정 규칙을 제안하고 적용을 "
        "보류·수정할 것인지 결정한 뒤, 확인할 사실과 적용 범위가 담긴 공동 "
        "평가원칙안을 제시하고 그 결정의 결과를 본인이 어떻게 책임질지 토론해 "
        "주세요. 공동안에 이르지 못하면 남은 쟁점과 결정권자에게 넘길 기준도 밝히세요.",
        (
            "방금 수용한 상대 입장과 남겨 둔 예외를 기준으로, 어떤 자료가 확인되면 "
            "적용 범위를 넓히거나 줄이겠습니까?",
            "앞서 선택한 보류 또는 수정 때문에 일정이나 관계 부서에 발생할 비용을 "
            "본인이 어떤 행동으로 감당하고, 결과를 무엇으로 확인하겠습니까?",
            "공통 판정 규칙을 모든 시범사업에 적용할 수 없는 경우, 예외 승인 요건과 "
            "후속 확인 책임자를 어떻게 정하겠습니까?",
        ),
        "본인이 비용을 감수해 직접 보류·수정하고 그 판정 결과를 책임지도록 한다.",
    ),
    TargetedV15Case(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "같은 성과 항목이 사업관리시스템, 부서 제출파일, 전월 대시보드에서 반복적으로 "
        "다르게 나타나지만 공개 일정은 오늘이고 추가 투입 인력은 한 명뿐입니다. "
        "공개 지연과 담당 부서의 반발을 감수하더라도 본인이 직접 어느 데이터를 "
        "보류하거나 수정할지 결정하고, 검증되지 않은 항목·선택 근거·본인의 후속 "
        "책임이 담긴 정확성 판단기록을 제시해 주세요.",
        (
            "방금 보류하거나 수정하기로 한 항목의 불일치 원인에 대해 어떤 가설을 "
            "세웠으며, 그 가설을 뒤집을 수 있는 자료는 무엇입니까?",
            "앞서 제시한 원인 가설을 한 명의 인력으로 확인하려면 어떤 최소 검증을 "
            "수행하고, 어떤 관찰 결과에서 검증을 중단하거나 다른 가설로 전환하겠습니까?",
            "검증을 마친 뒤 같은 오류의 재발을 막기 위해 원자료에 남길 필수 관리 "
            "항목과 변경 이력을 어떻게 구성하겠습니까?",
        ),
        "공개 비용 속 본인이 직접 값을 보류·수정하고 후속 책임을 기록하게 한다.",
    ),
)


def _item(
    case: TargetedV15Case,
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
        "question_evidence_id": f"ksa_targeted_v15_{case.case_id}",
        "question": question or case.question,
        "follow_ups": list(case.follow_ups),
    }


def _case(case_id: str) -> TargetedV15Case:
    return next(case for case in TARGETED_V15_CASES if case.case_id == case_id)


def test_targeted_v15_oracle_is_complete_and_explained() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(TARGETED_V15_CASES) == 3
    assert len({case.case_id for case in TARGETED_V15_CASES}) == 3
    assert all(len(case.follow_ups) == 3 for case in TARGETED_V15_CASES)
    assert all(len(case.rationale) >= 20 for case in TARGETED_V15_CASES)


@pytest.mark.parametrize("case", TARGETED_V15_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_targeted_v15_a_cases_pass_every_provider(
    case: TargetedV15Case,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert case.factor not in case.question
    assert result["passed"] is True, (case.rationale, result)
    assert all(result["checks"].values()), result


CLOSING_FIELD_REMOVALS = {
    "confirmed": ("확정된 값과 ", ""),
    "provisional": ("잠정값의 ", ""),
    "body": ("본문·", ""),
    "notes": ("주석 ", ""),
    "evidence_link": (" 및 증빙 연결 상태", ""),
}


@pytest.mark.parametrize(
    "removed_fields",
    tuple(combinations(CLOSING_FIELD_REMOVALS, 2)),
    ids=lambda fields: "without-" + "-and-".join(fields),
)
def test_closing_requires_at_least_four_bound_report_fields(
    removed_fields: tuple[str, str],
) -> None:
    case = _case("closing_report_rules")
    mutated = case.question
    for field in removed_fields:
        old, new = CLOSING_FIELD_REMOVALS[field]
        assert old in mutated
        mutated = mutated.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    assert result["passed"] is False, (removed_fields, mutated, result)


@pytest.mark.parametrize(
    ("missing_dimension", "old", "new"),
    (
        (
            "own-cost",
            "일정 지연이나 관계 부서의 반발을 감수하더라도 ",
            "",
        ),
        (
            "direct-action",
            "본인이 직접 어떤 공통 판정 규칙을 제안하고 적용을 보류·수정할 것인지 결정한 뒤, ",
            "어떤 공통 판정 규칙을 제안할지 검토한 뒤, ",
        ),
        (
            "outcome-responsibility",
            "하고 그 결정의 결과를 본인이 어떻게 책임질지 토론해 주세요",
            "해 주세요",
        ),
    ),
)
def test_objective_attitude_requires_each_personal_commitment_dimension(
    missing_dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case("objective_evaluation")
    assert old in case.question
    mutated = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    if missing_dimension == "own-cost":
        assert result["passed"] is True, (missing_dimension, mutated, result)
    else:
        assert result["passed"] is False, (missing_dimension, mutated, result)


@pytest.mark.parametrize(
    ("missing_dimension", "old", "new"),
    (
        (
            "own-direct-action",
            "본인이 직접 어느 데이터를 보류하거나 수정할지 결정하고, ",
            "어느 데이터가 불일치하는지 검토하고, ",
        ),
        (
            "outcome-responsibility",
            "·본인의 후속 책임",
            "",
        ),
    ),
)
def test_accuracy_attitude_requires_own_action_and_responsibility(
    missing_dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case("visualization_data_accuracy")
    assert old in case.question
    mutated = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    assert result["passed"] is False, (missing_dimension, mutated, result)


PARAPHRASES = (
    (
        "closing_report_rules",
        "결재 기한이 임박한 결산 초안에 근거가 없는 금액이 있고 승인권자도 부재합니다. "
        "우선순위를 정한 뒤 승인 완료값과 검토 중 값을 본표·각주에 구분 배치하고 근거 연결을 함께 담은 "
        "보고 판정 대장을 작성해 주세요.",
    ),
    (
        "objective_evaluation",
        "[토론과제] 성과 평가의 정량 결과는 낮지만 현장은 장기 효과와 표본 편중을 이유로 예외를 "
        "요구합니다. 지연 비용을 감수하더라도 스스로 적용을 중단하거나 조정할 범위를 "
        "결정하고, 공통 평가 기준으로 확인할 수치와 예외 범위를 담은 공동 판정원칙안을 제시한 뒤 결과 "
        "책임을 직접 설명하세요.",
    ),
    (
        "visualization_data_accuracy",
        "업무시스템과 제출 파일의 같은 수치가 다르지만 공개 기한은 오늘입니다. 공개 "
        "지연의 부담을 감수하고 스스로 오류 항목을 사용 중단하거나 수정할지 결정한 "
        "뒤, 미확인 항목·판단 근거·결과 책임을 담은 검증 판정표를 작성해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    PARAPHRASES,
    ids=[case[0] for case in PARAPHRASES],
)
def test_v15_accepts_semantic_paraphrases_with_new_artifact_names(
    case_id: str,
    question: str,
) -> None:
    case = _case(case_id)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert case.factor not in question
    assert result["passed"] is True, result


@pytest.mark.parametrize("target", TARGETED_V15_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", TARGETED_V15_CASES, ids=lambda case: case.case_id)
def test_targeted_v15_rejects_cross_domain_substitution(
    target: TargetedV15Case,
    source: TargetedV15Case,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity is covered by the positive oracle")

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["passed"] is False, result


@pytest.mark.parametrize(
    ("case_id", "old", "negated"),
    (
        (
            "closing_report_rules",
            "처리결정표를 제시해 주세요",
            "처리결정표를 작성하지 말고 구두로 답해 주세요",
        ),
        (
            "objective_evaluation",
            "공동 평가원칙안을 제시하고",
            "공동 평가원칙안은 작성하지 말고",
        ),
        (
            "visualization_data_accuracy",
            "정확성 판단기록을 제시해 주세요",
            "정확성 판단기록을 작성하지 말고 답해 주세요",
        ),
    ),
)
def test_targeted_v15_rejects_negated_artifact_production(
    case_id: str,
    old: str,
    negated: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    mutated = case.question.replace(old, negated, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    assert result["passed"] is False, result


@pytest.mark.parametrize(
    ("case_id", "salad"),
    (
        (
            "closing_report_rules",
            "결산 초안 확정 잠정 본문 주석 증빙 연결 처리결정표 제시해 주세요.",
        ),
        (
            "objective_evaluation",
            "정량 현장 공통 기준 본인 지연 보류 수정 책임 평가원칙안 토론해 주세요.",
        ),
        (
            "visualization_data_accuracy",
            "시스템 불일치 공개 지연 본인 직접 보류 수정 책임 판단기록 제시해 주세요.",
        ),
    ),
)
def test_targeted_v15_rejects_keyword_salad(case_id: str, salad: str) -> None:
    result = evaluate_ksa_measurement(_item(_case(case_id), question=salad))

    assert result["passed"] is False, result
