from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


# Frozen, report-independent copy of the targeted three-question regeneration
# from the real recruitment notice and NCS job-description PDFs.
TARGETED_B3_ORACLE: tuple[dict[str, Any], ...] = tuple(
    json.loads(
        r"""[
  {
    "case_id": "closing_report_rules",
    "type": "인바스켓면접",
    "question": "오늘 안에 결재할 결산 초안, 내일 오전까지 회신해야 하는 증빙 보완 요청, 한 시간 뒤 회의에 쓰일 경영진 보고자료가 동시에 도착했습니다. 초안에는 증빙이 없는 금액이 포함되어 있고, 본인은 금액 확정 권한이 없습니다. 무엇을 먼저 누구에게 맡기거나 직접 처리할지 하나의 우선순위로 결정하고, 확정된 값과 잠정값의 본문·주석 배치 및 증빙 연결 상태가 함께 보이는 처리결정표를 제시해 주세요.",
    "follow_ups": [
      "방금 1순위로 정한 문서와 처리 주체를 기준으로, 그 선택이 지연시키는 다른 업무와 누락 위험을 어떻게 통제하겠습니까?",
      "앞서 제시한 처리결정표에서 잠정값 또는 증빙 연결이 불분명하다면, 어떤 표시와 검토 흔적을 추가하겠습니까?",
      "결재권자가 부재한 채 보고 시각이 도래하면 어느 범위까지 작성하고 어떤 항목을 승인 대기 상태로 남기겠습니까?"
    ],
    "evaluation_points": [
      "문서의 마감, 오류 영향, 권한을 근거로 처리 순서와 주체를 명확히 정한다",
      "선택으로 지연되는 업무와 누락 위험에 대한 통제 행동을 설명한다",
      "확정된 값과 잠정값을 구별하여 본문 또는 주석에 배치한다",
      "각 금액의 증빙 연결 여부와 승인 대기 범위를 처리결정표에 남긴다"
    ]
  },
  {
    "case_id": "objective_evaluation",
    "type": "토론면접",
    "question": "[토론과제] 시범사업의 정량 성과는 목표에 미달했지만 현업은 장기 효과와 지역별 운영 여건을 반영해 계속사업으로 인정해 달라고 요구하고, 경영진은 일정 준수를 위해 현재 수치만으로 평가를 종결하라고 요구합니다. 한쪽은 모든 사업에 동일한 정량 기준을 적용하자는 입장이고, 다른 쪽은 현장 맥락에 따라 예외를 인정하자는 입장입니다. 일정 지연이나 관계 부서의 반발을 감수하더라도 본인이 직접 어떤 공통 판정 규칙을 제안하고 적용을 보류·수정할 것인지 결정한 뒤, 확인할 사실과 적용 범위가 담긴 공동 평가원칙안을 제시하고 그 결정의 결과를 본인이 어떻게 책임질지 토론해 주세요. 공동안에 이르지 못하면 남은 쟁점과 결정권자에게 넘길 기준도 밝히세요.",
    "follow_ups": [
      "방금 수용한 상대 입장과 남겨 둔 예외를 기준으로, 어떤 자료가 확인되면 적용 범위를 넓히거나 줄이겠습니까?",
      "앞서 선택한 보류 또는 수정 때문에 일정이나 관계 부서에 발생할 비용을 본인이 어떤 행동으로 감당하고, 결과를 무엇으로 확인하겠습니까?",
      "공통 판정 규칙을 모든 시범사업에 적용할 수 없는 경우, 예외 승인 요건과 후속 확인 책임자를 어떻게 정하겠습니까?"
    ],
    "evaluation_points": [
      "정량 결과와 현장 맥락을 같은 기준 아래 비교하기 위해 확인할 사실을 구체화한다",
      "일정 지연이나 부서 반발을 감수하는 본인의 보류·수정 행동을 명확히 선택한다",
      "공통 판정 규칙의 적용 범위와 예외 경계를 공동 평가원칙안에 기록한다",
      "합의 실패 시 남은 쟁점, 이송 기준, 본인이 질 결과 책임을 설명한다"
    ]
  },
  {
    "case_id": "visualization_data_accuracy",
    "type": "창의적 문제해결력면접",
    "question": "같은 성과 항목이 사업관리시스템, 부서 제출파일, 전월 대시보드에서 반복적으로 다르게 나타나지만 공개 일정은 오늘이고 추가 투입 인력은 한 명뿐입니다. 공개 지연과 담당 부서의 반발을 감수하더라도 본인이 직접 어느 데이터를 보류하거나 수정할지 결정하고, 검증되지 않은 항목·선택 근거·본인의 후속 책임이 담긴 정확성 판단기록을 제시해 주세요.",
    "follow_ups": [
      "방금 보류하거나 수정하기로 한 항목의 불일치 원인에 대해 어떤 가설을 세웠으며, 그 가설을 뒤집을 수 있는 자료는 무엇입니까?",
      "앞서 제시한 원인 가설을 한 명의 인력으로 확인하려면 어떤 최소 검증을 수행하고, 어떤 관찰 결과에서 검증을 중단하거나 다른 가설로 전환하겠습니까?",
      "검증을 마친 뒤 같은 오류의 재발을 막기 위해 원자료에 남길 필수 관리 항목과 변경 이력을 어떻게 구성하겠습니까?"
    ],
    "evaluation_points": [
      "출처 간 불일치가 공개 결과에 미치는 영향을 근거로 보류 또는 수정 대상을 결정한다",
      "공개 지연과 부서 반발을 감수하는 본인의 직접 행동과 결과 책임을 판단기록에 남긴다",
      "선택한 원인 가설을 반증할 자료와 최소 검증 절차를 구체적으로 제시한다",
      "관찰 결과에 따른 중단·전환 기준과 원자료 변경 이력의 구성 방식을 설명한다"
    ]
  }
]"""
    )
)


@pytest.mark.parametrize(
    "case",
    TARGETED_B3_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_targeted_fresh_human_aligned_questions_pass(
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
    TARGETED_B3_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_targeted_fresh_questions_do_not_open_hidden_retention_role(
    case: dict[str, Any],
) -> None:
    item = {**case, "evaluation_points": list(case["evaluation_points"])}
    item["evaluation_points"][3] = "평가 기록의 법정 보존 기간과 폐기 시점"

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case["case_id"], result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == "record_retention"
        for issue in result["issues"]
    )


@pytest.mark.parametrize(
    ("prompt_id", "criteria_id"),
    [
        (prompt["case_id"], criteria["case_id"])
        for prompt in TARGETED_B3_ORACLE
        for criteria in TARGETED_B3_ORACLE
        if prompt["case_id"] != criteria["case_id"]
    ],
)
def test_targeted_fresh_cross_case_criteria_swaps_fail(
    prompt_id: str,
    criteria_id: str,
) -> None:
    cases = {case["case_id"]: case for case in TARGETED_B3_ORACLE}
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
def test_targeted_fresh_unique_elicitation_removal_fails(case_id: str) -> None:
    cases = {case["case_id"]: case for case in TARGETED_B3_ORACLE}
    item = {**cases[case_id], "follow_ups": list(cases[case_id]["follow_ups"])}
    if case_id == "closing_report_rules":
        item["follow_ups"].pop(0)
    elif case_id == "visualization_data_accuracy":
        item["follow_ups"].pop(1)
    else:
        item["question"] = item["question"].replace(
            "공동안에 이르지 못하면 남은 쟁점과 결정권자에게 넘길 기준도 밝히세요.",
            "",
        )

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case_id, result)
