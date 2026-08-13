from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class TargetedV16Case:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    follow_ups: tuple[str, str, str]
    rationale: str


# Frozen independently from the third fresh targeted generation.  Runtime
# tests never import the report, and the human oracle is A for all three main
# questions.
TARGETED_V16_CASES = (
    TargetedV16Case(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "오늘 결재가 필요한 결산 초안, 내일 회신 기한인 증빙 보완 요청, 경영진 "
        "보고자료가 동시에 도착했지만 본인은 최종 승인 권한이 없습니다. 문서별 "
        "마감과 결재 권한을 고려해 처리 순서와 처리 주체를 하나의 원칙으로 결정하고, "
        "확정값과 잠정값의 구분, 본문과 주석의 배치, 근거 자료 연결이 표시된 "
        "검토보고서 한 장을 제시해 주세요.",
        (
            "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상급자 "
            "보고 중 그 방식을 택한 근거와 뒤로 미룬 문서에서 발생할 수 있는 누락 "
            "위험을 설명해 주세요.",
            "앞서 제시한 검토보고서에서 잠정값으로 분류한 항목 하나를 골라, 어떤 "
            "근거가 보완되면 확정값으로 바꾸고 본문 또는 주석의 위치를 어떻게 "
            "조정할지 말씀해 주세요.",
            "근거 자료가 기한 안에 도착하지 않을 때 결재 문서에 남겨야 할 최소 "
            "표시와 승인 요청 범위를 설명해 주세요.",
        ),
        "확정·잠정 구분과 본문·주석 배치, 근거 연결을 검토보고서에 직접 적용한다.",
    ),
    TargetedV16Case(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 시범사업의 수치 성과는 목표에 미달했지만 현업은 초기 이용자 "
        "정착과 장기 효과를 이유로 계속 추진을 요구하고 있습니다. 한쪽은 동일한 "
        "정량 기준을 예외 없이 적용해 낮은 등급을 부여해야 한다는 입장이고, 다른 "
        "쪽은 현장 여건을 반영해 등급을 조정해야 한다는 입장입니다. 확인할 사실을 "
        "토대로 적용할 공동 평가 원칙 하나를 결정하되, 합의가 어렵다면 남은 쟁점과 "
        "결정권자에게 넘길 경계를 정하고, 본인은 일정 지연이나 현업 반발을 "
        "감수하면서 어떤 등급 처리를 직접 보류·수정·확정할지와 그 결과에 질 책임이 "
        "함께 드러나는 평가원칙안 한 장을 제시해 주세요.",
        (
            "방금 상대 입장에서 수용한 근거와 수용하지 않은 주장을 각각 짚고, 그 "
            "경계를 가른 자료의 신뢰도 또는 비교 가능성을 설명해 주세요.",
            "앞서 정한 등급 처리로 불이익을 받는 부서가 이의를 제기한다면, 본인이 "
            "남긴 판단 근거 중 무엇으로 결정을 방어하고 어떤 오류가 확인될 때 "
            "책임지고 수정하겠습니까?",
            "공동 원칙의 적용 대상, 허용할 예외, 다음 평가에서 동일하게 확인할 "
            "기준과 후속 점검 책임자를 정해 말씀해 주세요.",
        ),
        "본인이 비용을 감수해 등급을 직접 보류·수정·확정하고 결과 책임을 진다.",
    ),
    TargetedV16Case(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "부서 집계표와 업무시스템 추출값의 불일치가 매월 반복되지만 게시 일정은 "
        "오늘이고, 검증에 투입할 수 있는 인원은 한 명뿐입니다. 두 출처의 갱신 "
        "시점과 중복 집계 가능성을 가르는 최소 검증을 근거로 게시·수정·보류 중 "
        "하나를 결정하고, 본인이 게시 지연이나 담당 부서 반발을 감수해 직접 취할 "
        "조치와 오류 발생 시 질 책임이 결속된 검증 판단 기록 한 장을 제시해 주세요. "
        "기록에는 원인 가설, 최소 검증 방법, 중단 기준을 표시해 주세요.",
        (
            "방금 제시한 원인 가설을 반박할 수 있는 자료는 무엇이며, 그 자료가 "
            "확인되면 선택한 게시·수정·보류 결정을 어떻게 바꾸겠습니까?",
            "앞서 정한 최소 검증에서 어떤 관찰 결과가 나오면 조사를 중단하고 결론을 "
            "내릴지, 그 기준이 출처 간 불일치를 실제로 구별하는 이유와 함께 설명해 "
            "주세요.",
            "같은 오류의 재발을 줄이기 위해 원자료에 추가할 확인 항목 하나와 변경 "
            "이력을 남길 책임 주체를 제시해 주세요.",
        ),
        "갱신·중복 원인의 최소검증과 게시 선택, 본인 직접 조치·오류 책임을 결속한다.",
    ),
)


def _case(case_id: str) -> TargetedV16Case:
    return next(case for case in TARGETED_V16_CASES if case.case_id == case_id)


def _item(
    case: TargetedV16Case,
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
        "question_evidence_id": f"ksa_targeted_v16_{case.case_id}",
        "question": question or case.question,
        "follow_ups": list(case.follow_ups),
    }


def test_targeted_v16_oracle_is_frozen_and_complete() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(TARGETED_V16_CASES) == 3
    assert len({case.case_id for case in TARGETED_V16_CASES}) == 3
    assert all(len(case.follow_ups) == 3 for case in TARGETED_V16_CASES)
    assert all(len(case.rationale) >= 20 for case in TARGETED_V16_CASES)


@pytest.mark.parametrize("case", TARGETED_V16_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_targeted_v16_a_cases_pass_every_provider(
    case: TargetedV16Case,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert case.factor not in case.question
    assert result["passed"] is True, (case.rationale, result)
    assert all(result["checks"].values()), result


CLOSING_FIELD_REMOVALS = {
    "confirmed": ("확정값과 ", ""),
    "provisional": ("잠정값의 구분, ", ""),
    "body": ("본문과 ", ""),
    "notes": ("주석의 배치, ", ""),
    "evidence_link": ("근거 자료 연결이 표시된 ", ""),
}


@pytest.mark.parametrize(
    "removed_fields",
    tuple(combinations(CLOSING_FIELD_REMOVALS, 2)),
    ids=lambda fields: "without-" + "-and-".join(fields),
)
def test_v16_closing_rejects_every_two_field_removal(
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
    ("dimension", "old", "new"),
    (
        (
            "cost",
            "일정 지연이나 현업 반발을 감수하면서 ",
            "",
        ),
        (
            "direct-grade-action",
            "어떤 등급 처리를 직접 보류·수정·확정할지",
            "어떤 등급인지 검토할지",
        ),
        (
            "result-responsibility",
            "와 그 결과에 질 책임이 함께 드러나는",
            "가 기록된",
        ),
    ),
)
def test_v16_objective_requires_cost_direct_action_and_responsibility(
    dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case("objective_evaluation")
    assert old in case.question
    mutated = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    if dimension == "cost":
        assert result["passed"] is True, (dimension, mutated, result)
    else:
        assert result["passed"] is False, (dimension, mutated, result)


@pytest.mark.parametrize(
    ("dimension", "old", "new"),
    (
        (
            "own-direct-action",
            "본인이 게시 지연이나 담당 부서 반발을 감수해 직접 취할 조치와 ",
            "검토 과정과 ",
        ),
        (
            "error-responsibility",
            "오류 발생 시 질 책임이 결속된 ",
            "",
        ),
    ),
)
def test_v16_accuracy_requires_own_action_and_error_responsibility(
    dimension: str,
    old: str,
    new: str,
) -> None:
    case = _case("visualization_data_accuracy")
    assert old in case.question
    mutated = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=mutated))

    assert result["passed"] is False, (dimension, mutated, result)


PARAPHRASES = (
    (
        "closing_report_rules",
        "승인자가 없는 결산 초안에 근거 자료가 부족하고 보고 기한도 임박했습니다. "
        "문서 순서를 정한 뒤 승인 완료 수치와 검토 중 수치를 본표와 각주에 구분해 "
        "놓고 각 값에 증빙을 연결한 보고 판정 대장을 작성해 주세요.",
    ),
    (
        "objective_evaluation",
        "[토론과제] 수치 성과는 낮지만 현장은 장기 효과를 이유로 등급 완화를 "
        "요구합니다. 공통 판정 원칙과 적용 경계를 정하고, 지연 비용을 감당하면서 "
        "스스로 등급 적용을 중단하거나 조정한 뒤 결과 책임을 직접 밝힌 판정원칙안을 "
        "제시해 주세요.",
    ),
    (
        "visualization_data_accuracy",
        "집계 파일과 시스템 값이 매번 다르고 게시 기한도 임박했습니다. 업데이트 "
        "시각과 중복 산입을 나누는 표본 점검으로 공개·정정·사용 중단 중 하나를 "
        "선택하고, 지연을 감수해 직접 실행할 조치와 오류 책임, 비교 대상·검사 방법·"
        "중단 조건을 담은 검증 판정표를 작성해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    PARAPHRASES,
    ids=[case[0] for case in PARAPHRASES],
)
def test_v16_accepts_morphological_and_artifact_paraphrases(
    case_id: str,
    question: str,
) -> None:
    case = _case(case_id)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert case.factor not in question
    assert result["passed"] is True, result


@pytest.mark.parametrize("target", TARGETED_V16_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", TARGETED_V16_CASES, ids=lambda case: case.case_id)
def test_targeted_v16_rejects_cross_domain_substitution(
    target: TargetedV16Case,
    source: TargetedV16Case,
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
            "검토보고서 한 장을 제시해 주세요",
            "검토보고서를 작성하지 말고 구두로 답해 주세요",
        ),
        (
            "objective_evaluation",
            "평가원칙안 한 장을 제시해 주세요",
            "평가원칙안은 작성하지 말고 답해 주세요",
        ),
        (
            "visualization_data_accuracy",
            "검증 판단 기록 한 장을 제시해 주세요",
            "검증 판단 기록은 작성하지 말고 답해 주세요",
        ),
    ),
)
def test_targeted_v16_rejects_negated_artifact_production(
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
            "결산 값 잠정 확정 본문 주석 근거 연결 검토보고서 제시해 주세요.",
        ),
        (
            "objective_evaluation",
            "수치 현장 공동 원칙 경계 본인 지연 등급 보류 수정 책임 제시해 주세요.",
        ),
        (
            "visualization_data_accuracy",
            "집계 불일치 갱신 중복 검증 게시 수정 보류 직접 책임 기록 제시해 주세요.",
        ),
    ),
)
def test_targeted_v16_rejects_keyword_salad(case_id: str, salad: str) -> None:
    result = evaluate_ksa_measurement(_item(_case(case_id), question=salad))

    assert result["passed"] is False, result
