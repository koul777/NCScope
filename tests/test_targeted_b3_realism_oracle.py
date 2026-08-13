"""Frozen realism oracle for the targeted B3 actual-generation round.

Rows are embedded so this regression remains independent of ignored report
artifacts. Human review marked all three realistic, with at least two
answer-linked probes. Only the dense six-output discussion warrants the soft
overload warning.
"""

from __future__ import annotations

import json

import pytest

from app.services.question_realism import evaluate_question_realism


TARGETED_B3_REALISM_CASES: list[dict[str, object]] = json.loads(
    r"""
[
    {
        "case_id":  "closing_report_rules",
        "type":  "인바스켓면접",
        "question":  "오늘 안에 결재할 결산 초안, 내일 오전까지 회신해야 하는 증빙 보완 요청, 한 시간 뒤 회의에 쓰일 경영진 보고자료가 동시에 도착했습니다. 초안에는 증빙이 없는 금액이 포함되어 있고, 본인은 금액 확정 권한이 없습니다. 무엇을 먼저 누구에게 맡기거나 직접 처리할지 하나의 우선순위로 결정하고, 확정된 값과 잠정값의 본문·주석 배치 및 증빙 연결 상태가 함께 보이는 처리결정표를 제시해 주세요.",
        "follow_ups":  [
                           "방금 1순위로 정한 문서와 처리 주체를 기준으로, 그 선택이 지연시키는 다른 업무와 누락 위험을 어떻게 통제하겠습니까?",
                           "앞서 제시한 처리결정표에서 잠정값 또는 증빙 연결이 불분명하다면, 어떤 표시와 검토 흔적을 추가하겠습니까?",
                           "결재권자가 부재한 채 보고 시각이 도래하면 어느 범위까지 작성하고 어떤 항목을 승인 대기 상태로 남기겠습니까?"
                       ],
        "question_source":  "codex_cli"
    },
    {
        "case_id":  "objective_evaluation",
        "type":  "토론면접",
        "question":  "[토론과제] 시범사업의 정량 성과는 목표에 미달했지만 현업은 장기 효과와 지역별 운영 여건을 반영해 계속사업으로 인정해 달라고 요구하고, 경영진은 일정 준수를 위해 현재 수치만으로 평가를 종결하라고 요구합니다. 한쪽은 모든 사업에 동일한 정량 기준을 적용하자는 입장이고, 다른 쪽은 현장 맥락에 따라 예외를 인정하자는 입장입니다. 일정 지연이나 관계 부서의 반발을 감수하더라도 본인이 직접 어떤 공통 판정 규칙을 제안하고 적용을 보류·수정할 것인지 결정한 뒤, 확인할 사실과 적용 범위가 담긴 공동 평가원칙안을 제시하고 그 결정의 결과를 본인이 어떻게 책임질지 토론해 주세요. 공동안에 이르지 못하면 남은 쟁점과 결정권자에게 넘길 기준도 밝히세요.",
        "follow_ups":  [
                           "방금 수용한 상대 입장과 남겨 둔 예외를 기준으로, 어떤 자료가 확인되면 적용 범위를 넓히거나 줄이겠습니까?",
                           "앞서 선택한 보류 또는 수정 때문에 일정이나 관계 부서에 발생할 비용을 본인이 어떤 행동으로 감당하고, 결과를 무엇으로 확인하겠습니까?",
                           "공통 판정 규칙을 모든 시범사업에 적용할 수 없는 경우, 예외 승인 요건과 후속 확인 책임자를 어떻게 정하겠습니까?"
                       ],
        "question_source":  "codex_cli"
    },
    {
        "case_id":  "visualization_data_accuracy",
        "type":  "창의적 문제해결력면접",
        "question":  "같은 성과 항목이 사업관리시스템, 부서 제출파일, 전월 대시보드에서 반복적으로 다르게 나타나지만 공개 일정은 오늘이고 추가 투입 인력은 한 명뿐입니다. 공개 지연과 담당 부서의 반발을 감수하더라도 본인이 직접 어느 데이터를 보류하거나 수정할지 결정하고, 검증되지 않은 항목·선택 근거·본인의 후속 책임이 담긴 정확성 판단기록을 제시해 주세요.",
        "follow_ups":  [
                           "방금 보류하거나 수정하기로 한 항목의 불일치 원인에 대해 어떤 가설을 세웠으며, 그 가설을 뒤집을 수 있는 자료는 무엇입니까?",
                           "앞서 제시한 원인 가설을 한 명의 인력으로 확인하려면 어떤 최소 검증을 수행하고, 어떤 관찰 결과에서 검증을 중단하거나 다른 가설로 전환하겠습니까?",
                           "검증을 마친 뒤 같은 오류의 재발을 막기 위해 원자료에 남길 필수 관리 항목과 변경 이력을 어떻게 구성하겠습니까?"
                       ],
        "question_source":  "codex_cli"
    }
]
"""
)


@pytest.mark.parametrize(
    "item",
    TARGETED_B3_REALISM_CASES,
    ids=lambda item: str(item["case_id"]),
)
def test_targeted_b3_actual_generation_matches_human_realism_oracle(
    item: dict[str, object],
) -> None:
    result = evaluate_question_realism(item)

    assert result["policy_version"] == "field-realism-v3.14"
    assert result["passed"] is True, (item["case_id"], result["issues"])
    assert result["checks"]["no_generic_template_scaffolding"] is True
    assert result["checks"]["concrete_scenario"] is True
    assert result["metrics"]["scenario_signals"]["concrete_fact"] is True
    assert result["metrics"]["scenario_signals"]["dilemma"] is True
    assert result["metrics"]["adaptive_follow_up_count"] >= 2
    assert result["metrics"]["required_adaptive_follow_up_count"] == 2

    expected_overload = item["case_id"] == "objective_evaluation"
    assert result["metrics"]["overload_warning"] is expected_overload
    if expected_overload:
        assert result["metrics"]["response_demand_length"] >= 150
        assert result["metrics"]["demand_family_count"] >= 2
        assert result["metrics"]["independent_output_count"] >= 4


def test_targeted_b3_actual_generation_oracle_is_complete() -> None:
    assert len(TARGETED_B3_REALISM_CASES) == 3
    assert {item["case_id"] for item in TARGETED_B3_REALISM_CASES} == {
        "closing_report_rules",
        "objective_evaluation",
        "visualization_data_accuracy",
    }
