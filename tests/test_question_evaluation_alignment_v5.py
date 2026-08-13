from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


# Frozen, report-independent copy of the final targeted three-question run
# from the real recruitment notice and NCS job-description PDFs.
FINAL_TARGETED_B3_ORACLE: tuple[dict[str, Any], ...] = tuple(
    json.loads(
        r"""[
  {
    "case_id": "closing_report_rules",
    "type": "인바스켓면접",
    "question": "오늘 결재가 필요한 결산 초안, 내일 회신 기한인 증빙 보완 요청, 경영진 보고자료가 동시에 도착했지만 본인은 최종 승인 권한이 없습니다. 문서별 마감과 결재 권한을 고려해 처리 순서와 처리 주체를 하나의 원칙으로 결정하고, 확정값과 잠정값의 구분, 본문과 주석의 배치, 근거 자료 연결이 표시된 검토보고서 한 장을 제시해 주세요.",
    "follow_ups": [
      "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상급자 보고 중 그 방식을 택한 근거와 뒤로 미룬 문서에서 발생할 수 있는 누락 위험을 설명해 주세요.",
      "앞서 제시한 검토보고서에서 잠정값으로 분류한 항목 하나를 골라, 어떤 근거가 보완되면 확정값으로 바꾸고 본문 또는 주석의 위치를 어떻게 조정할지 말씀해 주세요.",
      "근거 자료가 기한 안에 도착하지 않을 때 결재 문서에 남겨야 할 최소 표시와 승인 요청 범위를 설명해 주세요."
    ],
    "evaluation_points": [
      "서로 다른 마감과 권한을 반영한 처리 순서 및 담당 주체의 결정",
      "확정값과 잠정값을 구별한 근거의 명확성",
      "본문과 주석의 배치가 정보의 확실성 수준과 일치하는지 여부",
      "각 판단을 확인 가능한 근거 자료와 연결하고 미확인 위험을 표시하는 방식"
    ]
  },
  {
    "case_id": "objective_evaluation",
    "type": "토론면접",
    "question": "[토론과제] 시범사업의 수치 성과는 목표에 미달했지만 현업은 초기 이용자 정착과 장기 효과를 이유로 계속 추진을 요구하고 있습니다. 한쪽은 동일한 정량 기준을 예외 없이 적용해 낮은 등급을 부여해야 한다는 입장이고, 다른 쪽은 현장 여건을 반영해 등급을 조정해야 한다는 입장입니다. 확인할 사실을 토대로 적용할 공동 평가 원칙 하나를 결정하되, 합의가 어렵다면 남은 쟁점과 결정권자에게 넘길 경계를 정하고, 본인은 일정 지연이나 현업 반발을 감수하면서 어떤 등급 처리를 직접 보류·수정·확정할지와 그 결과에 질 책임이 함께 드러나는 평가원칙안 한 장을 제시해 주세요.",
    "follow_ups": [
      "방금 상대 입장에서 수용한 근거와 수용하지 않은 주장을 각각 짚고, 그 경계를 가른 자료의 신뢰도 또는 비교 가능성을 설명해 주세요.",
      "앞서 정한 등급 처리로 불이익을 받는 부서가 이의를 제기한다면, 본인이 남긴 판단 근거 중 무엇으로 결정을 방어하고 어떤 오류가 확인될 때 책임지고 수정하겠습니까?",
      "공동 원칙의 적용 대상, 허용할 예외, 다음 평가에서 동일하게 확인할 기준과 후속 점검 책임자를 정해 말씀해 주세요."
    ],
    "evaluation_points": [
      "두 입장의 주장을 검증하기 위해 확인할 사실과 자료의 적절성",
      "동일 조건의 일관된 적용과 현장 차이 반영 사이의 수용 경계",
      "일정 지연이나 관계 부서 반발을 감수한 본인의 직접적인 등급 처리 선택",
      "결정 근거와 수정 조건 및 결과 책임이 평가원칙안에 명확히 기록되는지 여부"
    ]
  },
  {
    "case_id": "visualization_data_accuracy",
    "type": "창의적 문제해결력면접",
    "question": "부서 집계표와 업무시스템 추출값의 불일치가 매월 반복되지만 게시 일정은 오늘이고, 검증에 투입할 수 있는 인원은 한 명뿐입니다. 두 출처의 갱신 시점과 중복 집계 가능성을 가르는 최소 검증을 근거로 게시·수정·보류 중 하나를 결정하고, 본인이 게시 지연이나 담당 부서 반발을 감수해 직접 취할 조치와 오류 발생 시 질 책임이 결속된 검증 판단 기록 한 장을 제시해 주세요. 기록에는 원인 가설, 최소 검증 방법, 중단 기준을 표시해 주세요.",
    "follow_ups": [
      "방금 제시한 원인 가설을 반박할 수 있는 자료는 무엇이며, 그 자료가 확인되면 선택한 게시·수정·보류 결정을 어떻게 바꾸겠습니까?",
      "앞서 정한 최소 검증에서 어떤 관찰 결과가 나오면 조사를 중단하고 결론을 내릴지, 그 기준이 출처 간 불일치를 실제로 구별하는 이유와 함께 설명해 주세요.",
      "같은 오류의 재발을 줄이기 위해 원자료에 추가할 확인 항목 하나와 변경 이력을 남길 책임 주체를 제시해 주세요."
    ],
    "evaluation_points": [
      "갱신 시점 또는 중복 집계를 구별할 수 있는 반증 가능한 원인 가설",
      "제한된 인력으로 출처 간 차이를 판별하는 최소 검증 방법",
      "중단 기준에 근거한 게시·수정·보류 결정과 본인의 직접 조치",
      "게시 지연이나 부서 반발을 감수하고 판단 결과 및 오류 책임을 기록하는 방식"
    ]
  }
]"""
    )
)


@pytest.mark.parametrize(
    "case",
    FINAL_TARGETED_B3_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_final_targeted_human_aligned_questions_pass(
    case: dict[str, Any],
) -> None:
    result = evaluate_evaluation_elicitation_alignment(case)

    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", (case["case_id"], result)
    assert (
        result["metrics"]["matched_atom_count"] == result["metrics"]["point_atom_count"]
    )


@pytest.mark.parametrize(
    "case",
    FINAL_TARGETED_B3_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_final_targeted_hidden_approval_line_remains_closed(
    case: dict[str, Any],
) -> None:
    item = {**case, "evaluation_points": list(case["evaluation_points"])}
    item["evaluation_points"][3] = "최종 산출물의 부서장 결재선과 승인 절차"

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case["case_id"], result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == "approval_process"
        for issue in result["issues"]
    )


@pytest.mark.parametrize(
    ("prompt_id", "criteria_id"),
    [
        (prompt["case_id"], criteria["case_id"])
        for prompt in FINAL_TARGETED_B3_ORACLE
        for criteria in FINAL_TARGETED_B3_ORACLE
        if prompt["case_id"] != criteria["case_id"]
    ],
)
def test_final_targeted_cross_case_criteria_swaps_fail(
    prompt_id: str,
    criteria_id: str,
) -> None:
    cases = {case["case_id"]: case for case in FINAL_TARGETED_B3_ORACLE}
    item = {
        **cases[prompt_id],
        "evaluation_points": list(cases[criteria_id]["evaluation_points"]),
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (prompt_id, criteria_id, result)


@pytest.mark.parametrize(
    "case_id",
    [
        "closing_report_rules",
        "objective_evaluation",
        "visualization_data_accuracy",
    ],
)
def test_final_targeted_unique_relation_removal_fails(case_id: str) -> None:
    cases = {case["case_id"]: case for case in FINAL_TARGETED_B3_ORACLE}
    item = {**cases[case_id], "follow_ups": list(cases[case_id]["follow_ups"])}
    if case_id == "closing_report_rules":
        item["question"] = item["question"].replace(
            "본문과 주석의 배치, ",
            "",
        )
        item["follow_ups"].pop(1)
    elif case_id == "objective_evaluation":
        item["question"] = item["question"].replace(
            (
                "한쪽은 동일한 정량 기준을 예외 없이 적용해 낮은 등급을 부여해야 "
                "한다는 입장이고, 다른 쪽은 현장 여건을 반영해 등급을 조정해야 "
                "한다는 입장입니다. "
            ),
            "",
        )
        item["follow_ups"].pop(2)
    else:
        item["follow_ups"].pop(0)

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case_id, result)


def test_evidence_inputs_are_not_limited_by_later_single_action_choice() -> None:
    case = next(
        case
        for case in FINAL_TARGETED_B3_ORACLE
        if case["case_id"] == "visualization_data_accuracy"
    )

    result = evaluate_evaluation_elicitation_alignment(case)

    assert result["decision"] == "pass", result
    assert not any(
        issue["code"] == "quantifier_scope_mismatch" for issue in result["issues"]
    )


def test_explicit_single_evidence_choice_cannot_cover_both_scored_inputs() -> None:
    item = {
        "type": "창의적 문제해결력면접",
        "question": (
            "갱신 시점과 중복 집계 중 하나만 확인해 원인 가설과 최소 검증을 제시하세요."
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
