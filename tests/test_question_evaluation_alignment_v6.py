from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)
from tests.test_final_v16_realism_oracle import FINAL_V16_REALISM_CASES


# The candidate-visible prompts are imported from another report-independent,
# frozen source fixture.  The human-audited scoring criteria are frozen here;
# no dated report is read at test runtime.
_EVALUATION_POINTS: dict[str, tuple[str, str, str, str]] = {
    "indicator_definition": (
        "집계 목적에 맞춰 포함 대상과 제외 대상을 구분하는 논리",
        "동일 참여자의 중복 여부를 판별하는 기준",
        "예외 적용 사유와 오류 위험을 구체적으로 설명하는 능력",
        "적용 기준과 변경 흔적을 확인할 수 있는 기준 정리표의 완결성",
    ),
    "research_plan_review": (
        "서로 다른 문서의 참여기관 정보를 대응시켜 불일치를 식별하는 능력",
        "마감 압박 속에서도 제출 가능 여부를 근거 있게 판정하는 능력",
        "누락된 역할과 비용 근거를 연결하여 구체적인 수정을 요구하는 능력",
        "수정본의 문서 간 일치와 누락 해소를 확인하는 방법",
    ),
    "numeric_accuracy": (
        "실제 불일치의 대상과 발견 경위를 구체적으로 설명하는 정도",
        "단위·기간·합계 관계를 대조하고 직접 검산한 행동",
        "압박 속에서 정확한 결과를 위해 비용을 감수한 본인의 선택",
        "수정 전후 값과 근거 및 승인 흔적으로 결과 책임을 입증하는 정도",
    ),
    "plan_actual_gap": (
        "계획과 집행 및 성과의 차이를 동일 기간과 단위로 비교하는 능력",
        "여러 이상 징후 중 핵심 차이를 선별하는 판단",
        "수치 변화와 민원 기록을 연결해 원인을 설명하는 근거성",
        "계획값·실적값·원인 근거를 명확히 보여 주는 분석표의 전달력",
    ),
    "research_fund_rule": (
        "사업·기간·지출 유형을 기준으로 서로 다른 근거의 적용 범위를 구별한다.",
        "예외의 성립 조건과 적용되지 않는 경계를 조건부로 설명한다.",
        "보완 또는 반려 결론과 그 근거를 하나의 검토기록에 연결한다.",
        "재제출 서류와 승인·증빙 기록을 대조해 오류 해소 여부를 확인한다.",
    ),
    "agency_negotiation": (
        "양측의 일정상 이익과 검증 책임을 분리해 확인할 사실을 제시한다.",
        "요구의 수용 범위와 불수용 경계를 구체적인 교환 조건으로 조정한다.",
        "합의 내용 또는 미합의 쟁점과 상위 결정 이송 기준을 협의기록에 남긴다.",
        "적용 범위·예외·이행 확인 기준과 담당 주체를 명확히 정한다.",
    ),
    "personal_data_protection": (
        "각 요청의 목적, 승인 권한, 필요한 정보 범위를 구분한다.",
        "마감과 정보 노출 위험을 함께 고려해 처리 순서를 정한다.",
        "직접처리·보고·보류 결정을 건별 담당 주체 및 사유와 연결해 기록한다.",
        "회신 범위와 사전 확인 항목을 제시해 오발송·과다 제공 위험을 통제한다.",
    ),
    "measurement_framework": (
        "일정 지연 또는 관계 부서 반발이라는 비용을 감수하는 선택을 명확히 밝힌다.",
        "선택한 보류 또는 수정 조치를 본인이 직접 반영하고 결과 책임의 범위를 기록한다.",
        "측정 차원·포함 및 제외 경계·관찰 기간이 서로 일관된 기준을 제시한다.",
        "반증 가능성과 제한된 자원을 고려한 소규모 검증 및 중단 조건을 설명한다.",
    ),
    "closing_report_rules": (
        "마감과 승인 권한을 함께 고려하여 문서별 처리 순서와 주체를 구분한다",
        "선택한 우선순위로 발생할 수 있는 누락 또는 오보고 위험을 구체적으로 설명한다",
        "같은 검토기록 안에서 확정값과 잠정값을 구별하고 본문과 주석의 배치를 정한다",
        "기재한 금액이나 판단을 확인 가능한 증빙과 연결하고 승인 불가 시 반영 범위를 제시한다",
    ),
    "review_report_writing": (
        "마감 압박과 자료 불일치가 있었던 실제 상황 및 본인의 역할을 구체적으로 밝힌다",
        "판정에 사용한 원자료와 선택 이유를 설명한다",
        "계획값·실적값·차이 사유가 연결된 검토표에서 본인의 작성 흔적을 구분한다",
        "승인·정정·의사결정 등 산출물의 실제 활용 결과와 확인 근거를 제시한다",
    ),
    "civil_form_process": (
        "충돌한 기록 중 첫 확인 대상을 정하고 그 선택 근거를 설명한다",
        "근거 유무에 따라 기재·공란 유지·제출 보류의 조건을 구분한다",
        "수정 서식에 필수 항목, 확인 출처와 보완 상태를 식별 가능하게 표시한다",
        "기존 민원 안내와 달라지는 내용을 변경 흔적 및 후속 확인과 연결한다",
    ),
    "resource_allocation_fairness": (
        "제한된 총자원 안에서 모든 사업에 일관되게 적용할 구별 가능한 기준을 제시한다",
        "부서 반발이나 핵심 일정 지연이라는 비용을 감수하는 하나의 배분을 직접 결정한다",
        "배분안에 공통 기준, 사업별 배분량과 본인의 책임 범위를 명확히 기록한다",
        "반대 자료나 필수 의무를 근거로 결정의 수정 경계와 결과 확인 기준을 설명한다",
    ),
    "objective_evaluation": (
        "상충하는 정량 자료와 현장 사실을 구분하여 확인할 근거를 제시한다",
        "일정 지연이나 부서 반발을 감수하는 일관된 기준 선택을 설명한다",
        "선택한 기준을 적용하기 위한 본인의 직접 조치와 결과 책임을 명시한다",
        "평가원칙안에 적용 범위와 예외 또는 이송 조건을 구체적으로 기록한다",
    ),
    "visualization_data_accuracy": (
        "반복 불일치를 설명하는 검증 가능한 가설을 특정한다",
        "제한된 인력 아래 보류 또는 수정 범위와 최소 검증을 서로 연결한다",
        "공개 지연이라는 비용을 감수한 직접 행동과 결과 책임을 명확히 한다",
        "판단 기록에 대상 항목·검증 가설·중단 기준을 판별 가능하게 제시한다",
    ),
    "data_use_ethics": (
        "자료의 수집 목적·접근권한·공유 대상 가운데 실제 충돌 지점을 구체화한다",
        "편의 포기나 반발을 감수하고 사용 제한 또는 거절을 선택한 이유를 설명한다",
        "본인이 직접 취한 조치와 그 결과에 대한 책임을 실제 변화나 기록으로 입증한다",
        "승인·반려 기록에 허용 범위와 판단 근거를 구분하여 남긴다",
    ),
    "document_register": (
        "문서별 마감과 권한 차이를 반영하여 처리 순서와 주체를 결정한다",
        "접수번호가 없거나 승인 권한이 없는 문서를 임의 처리하지 않고 상태를 구분한다",
        "정정 전후 기록을 연결하여 변경 사유와 처리 경위를 추적 가능하게 한다",
        "접수대장 처리안에 접수 시각·담당자·상태·보류 또는 정정 이력을 정확히 기재한다",
    ),
}


FINAL_V16_ALIGNMENT_ORACLE: tuple[dict[str, Any], ...] = tuple(
    {
        "case_id": case["case_id"],
        "type": case["type"],
        "question": case["question"],
        "follow_ups": tuple(case["follow_ups"]),
        "evaluation_points": _EVALUATION_POINTS[str(case["case_id"])],
    }
    for case in FINAL_V16_REALISM_CASES
)

_CASES = {str(case["case_id"]): case for case in FINAL_V16_ALIGNMENT_ORACLE}


@pytest.mark.parametrize(
    "case",
    FINAL_V16_ALIGNMENT_ORACLE,
    ids=lambda case: str(case["case_id"]),
)
def test_final_v16_human_audited_evaluation_sets_pass(case: dict[str, Any]) -> None:
    item = {**case, "evaluation_points": list(case["evaluation_points"])}

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", (case["case_id"], result)
    assert result["metrics"]["matched_atom_count"] == result["metrics"][
        "point_atom_count"
    ]


@pytest.mark.parametrize(
    ("prompt_id", "criteria_id"),
    [
        (str(prompt["case_id"]), str(criteria["case_id"]))
        for prompt in FINAL_V16_ALIGNMENT_ORACLE
        for criteria in FINAL_V16_ALIGNMENT_ORACLE
        if prompt["case_id"] != criteria["case_id"]
    ],
)
def test_final_v16_all_ordered_cross_factor_swaps_fail(
    prompt_id: str,
    criteria_id: str,
) -> None:
    item = {
        **_CASES[prompt_id],
        "evaluation_points": list(_CASES[criteria_id]["evaluation_points"]),
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] != "pass", (prompt_id, criteria_id, result)


def _first_sentence(text: str) -> str:
    return f"{text.split('. ', 1)[0]}."


def _remove_unique_elicitation(case: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(case)
    item["evaluation_points"] = list(item["evaluation_points"])
    item["follow_ups"] = list(item["follow_ups"])
    case_id = str(item["case_id"])

    if case_id == "indicator_definition":
        item["follow_ups"].pop(1)
    elif case_id == "research_plan_review":
        item["question"] = item["question"].replace(
            "한 기관의 역할과 예산 근거도 빠져 있습니다. ", ""
        )
        item["follow_ups"].pop(1)
    elif case_id == "numeric_accuracy":
        item["follow_ups"].pop(0)
    elif case_id == "plan_actual_gap":
        item["question"] = _first_sentence(item["question"])
        item["follow_ups"] = [item["follow_ups"][0], item["follow_ups"][2]]
    elif case_id == "research_fund_rule":
        item["question"] = item["question"].replace(
            "세 근거의 적용 대상과 효력 범위를 따져 ", ""
        ).replace("적용 근거·", "")
        item["follow_ups"] = [item["follow_ups"][2]]
    elif case_id == "agency_negotiation":
        item["follow_ups"].pop(2)
    elif case_id == "personal_data_protection":
        item["follow_ups"] = item["follow_ups"][:1]
    elif case_id == "measurement_framework":
        item["follow_ups"].pop(1)
    elif case_id == "closing_report_rules":
        item["follow_ups"].pop(0)
    elif case_id == "review_report_writing":
        item["follow_ups"].pop(0)
    elif case_id == "civil_form_process":
        item["follow_ups"] = item["follow_ups"][:1]
    elif case_id == "resource_allocation_fairness":
        item["question"] = _first_sentence(item["question"])
        item["follow_ups"] = [item["follow_ups"][0], item["follow_ups"][2]]
    elif case_id == "objective_evaluation":
        item["question"] = item["question"].replace("두 입장의 근거를 확인한 뒤 ", "")
        item["follow_ups"].pop(0)
    elif case_id == "visualization_data_accuracy":
        item["question"] = _first_sentence(item["question"])
        item["follow_ups"] = [item["follow_ups"][2]]
    elif case_id == "data_use_ethics":
        item["follow_ups"].pop(0)
    elif case_id == "document_register":
        item["question"] = item["question"].replace("·보류 또는 정정 이력", "")
        item["follow_ups"].pop(1)
    else:  # pragma: no cover - fixture completeness guard
        raise AssertionError(f"missing removal probe for {case_id}")
    return item


@pytest.mark.parametrize(
    "case",
    FINAL_V16_ALIGNMENT_ORACLE,
    ids=lambda case: str(case["case_id"]),
)
def test_final_v16_unique_elicitation_removal_is_non_passing(
    case: dict[str, Any],
) -> None:
    result = evaluate_evaluation_elicitation_alignment(
        _remove_unique_elicitation(case)
    )

    assert result["decision"] != "pass", (case["case_id"], result)


_HIDDEN_CRITERIA = (
    ("개선안의 실행 책임자", "execution_owner"),
    ("검토서의 부서장 결재선과 승인 절차", "approval_process"),
    ("처리 기록의 법정 보존 기간과 폐기 시점", "record_retention"),
    ("재발 방지를 위한 전 직원 교육", "prevention_training"),
)


@pytest.mark.parametrize(
    ("case", "criterion", "family"),
    [
        (case, criterion, family)
        for case in FINAL_V16_ALIGNMENT_ORACLE
        for criterion, family in _HIDDEN_CRITERIA
    ],
    ids=lambda value: str(value.get("case_id")) if isinstance(value, dict) else str(value),
)
def test_final_v16_hidden_role_insertions_remain_closed(
    case: dict[str, Any],
    criterion: str,
    family: str,
) -> None:
    item = {**case, "evaluation_points": list(case["evaluation_points"])}
    item["evaluation_points"][3] = criterion

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case["case_id"], family, result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == family
        for issue in result["issues"]
    )


def test_negated_requirements_do_not_become_positive_demands() -> None:
    item = {
        "type": "상황면접",
        "question": (
            "실행 책임자, 부서장 결재선, 기록 보존 기간, 직원 교육은 답변하지 "
            "마십시오. 대신 자료 불일치와 판단 기준만 설명하십시오."
        ),
        "follow_ups": [],
        "evaluation_points": [
            "개선안의 실행 책임자",
            "검토서의 부서장 결재선과 승인 절차",
            "처리 기록의 법정 보존 기간과 폐기 시점",
            "재발 방지를 위한 전 직원 교육",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", result
    assert result["metrics"]["matched_atom_count"] == 0


def test_premise_only_semantic_terms_are_not_candidate_demands() -> None:
    item = {
        "type": "상황면접",
        "question": (
            "기존 문서에 측정 차원, 단위·기간·합계, 보류 또는 수정 선택, "
            "재제출 서류가 적혀 있는 상황입니다. 표지 색상 하나를 골라 "
            "설명해 주세요."
        ),
        "follow_ups": [],
        "evaluation_points": [
            "측정 차원",
            "단위·기간·합계 관계",
            "보류 또는 수정 선택",
            "재제출 서류 확인",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", result


def test_keyword_insertion_is_not_substantive_elicitation() -> None:
    item = {
        "type": "직무지식면접",
        "question": (
            "포함 대상, 중복 여부, 예외 사유, 오류 위험, 변경 흔적을 키워드로 "
            "넣어 답변하십시오."
        ),
        "follow_ups": [],
        "evaluation_points": [
            "포함 대상과 제외 대상",
            "동일 참여자의 중복 여부",
            "예외 적용 사유와 오류 위험",
            "적용 기준과 변경 흔적",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", result
    assert result["checks"]["has_prompt_demands"] is False


def test_explicit_single_choice_still_fails_compound_scoring_scope() -> None:
    item = {
        "type": "창의적 문제해결력면접",
        "question": (
            "갱신 시점과 중복 집계 중 하나만 확인해 원인 가설과 최소 검증을 "
            "제시하세요."
        ),
        "follow_ups": [],
        "evaluation_points": [
            "갱신 시점",
            "중복 집계",
            "원인 가설",
            "갱신 시점과 중복 집계",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", result
    assert any(
        issue["code"] == "quantifier_scope_mismatch" for issue in result["issues"]
    )


def test_alignment_does_not_invent_self_sacrifice_or_personal_liability() -> None:
    case = deepcopy(_CASES["indicator_definition"])
    case["evaluation_points"] = list(case["evaluation_points"])

    baseline = evaluate_evaluation_elicitation_alignment(case)
    assert baseline["decision"] == "pass", baseline

    case["evaluation_points"][3] = (
        "지원자의 금전적 희생과 법적 결과 책임을 개인적으로 부담하는 방식"
    )
    injected = evaluate_evaluation_elicitation_alignment(case)

    assert injected["decision"] == "fail", injected
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        for issue in injected["issues"]
    )


def test_final_v16_fixture_is_complete_and_report_independent() -> None:
    assert len(FINAL_V16_ALIGNMENT_ORACLE) == 16
    assert len(_CASES) == 16
    assert set(_CASES) == set(_EVALUATION_POINTS)
    assert all(len(case["evaluation_points"]) == 4 for case in _CASES.values())
