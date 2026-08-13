"""Frozen realism oracle for the final targeted B3 actual-generation round.

The three rows are embedded to keep the regression independent of ignored
report artifacts. The later blind non-leading adjudication supersedes the
initial human read for two prompts that compel personal sacrifice and outcome
liability. The compact prompts still do not cross the soft overload threshold.
"""

from __future__ import annotations

import json

import pytest

from app.services.question_realism import evaluate_question_realism


TARGETED_B3_FINAL_REALISM_CASES: list[dict[str, object]] = json.loads(
    r"""
[
    {
        "case_id":  "closing_report_rules",
        "type":  "인바스켓면접",
        "question":  "오늘 결재가 필요한 결산 초안, 내일 회신 기한인 증빙 보완 요청, 경영진 보고자료가 동시에 도착했지만 본인은 최종 승인 권한이 없습니다. 문서별 마감과 결재 권한을 고려해 처리 순서와 처리 주체를 하나의 원칙으로 결정하고, 확정값과 잠정값의 구분, 본문과 주석의 배치, 근거 자료 연결이 표시된 검토보고서 한 장을 제시해 주세요.",
        "follow_ups":  [
                           "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상급자 보고 중 그 방식을 택한 근거와 뒤로 미룬 문서에서 발생할 수 있는 누락 위험을 설명해 주세요.",
                           "앞서 제시한 검토보고서에서 잠정값으로 분류한 항목 하나를 골라, 어떤 근거가 보완되면 확정값으로 바꾸고 본문 또는 주석의 위치를 어떻게 조정할지 말씀해 주세요.",
                           "근거 자료가 기한 안에 도착하지 않을 때 결재 문서에 남겨야 할 최소 표시와 승인 요청 범위를 설명해 주세요."
                       ],
        "question_source":  "codex_cli"
    },
    {
        "case_id":  "objective_evaluation",
        "type":  "토론면접",
        "question":  "[토론과제] 시범사업의 수치 성과는 목표에 미달했지만 현업은 초기 이용자 정착과 장기 효과를 이유로 계속 추진을 요구하고 있습니다. 한쪽은 동일한 정량 기준을 예외 없이 적용해 낮은 등급을 부여해야 한다는 입장이고, 다른 쪽은 현장 여건을 반영해 등급을 조정해야 한다는 입장입니다. 확인할 사실을 토대로 적용할 공동 평가 원칙 하나를 결정하되, 합의가 어렵다면 남은 쟁점과 결정권자에게 넘길 경계를 정하고, 본인은 일정 지연이나 현업 반발을 감수하면서 어떤 등급 처리를 직접 보류·수정·확정할지와 그 결과에 질 책임이 함께 드러나는 평가원칙안 한 장을 제시해 주세요.",
        "follow_ups":  [
                           "방금 상대 입장에서 수용한 근거와 수용하지 않은 주장을 각각 짚고, 그 경계를 가른 자료의 신뢰도 또는 비교 가능성을 설명해 주세요.",
                           "앞서 정한 등급 처리로 불이익을 받는 부서가 이의를 제기한다면, 본인이 남긴 판단 근거 중 무엇으로 결정을 방어하고 어떤 오류가 확인될 때 책임지고 수정하겠습니까?",
                           "공동 원칙의 적용 대상, 허용할 예외, 다음 평가에서 동일하게 확인할 기준과 후속 점검 책임자를 정해 말씀해 주세요."
                       ],
        "question_source":  "codex_cli"
    },
    {
        "case_id":  "visualization_data_accuracy",
        "type":  "창의적 문제해결력면접",
        "question":  "부서 집계표와 업무시스템 추출값의 불일치가 매월 반복되지만 게시 일정은 오늘이고, 검증에 투입할 수 있는 인원은 한 명뿐입니다. 두 출처의 갱신 시점과 중복 집계 가능성을 가르는 최소 검증을 근거로 게시·수정·보류 중 하나를 결정하고, 본인이 게시 지연이나 담당 부서 반발을 감수해 직접 취할 조치와 오류 발생 시 질 책임이 결속된 검증 판단 기록 한 장을 제시해 주세요. 기록에는 원인 가설, 최소 검증 방법, 중단 기준을 표시해 주세요.",
        "follow_ups":  [
                           "방금 제시한 원인 가설을 반박할 수 있는 자료는 무엇이며, 그 자료가 확인되면 선택한 게시·수정·보류 결정을 어떻게 바꾸겠습니까?",
                           "앞서 정한 최소 검증에서 어떤 관찰 결과가 나오면 조사를 중단하고 결론을 내릴지, 그 기준이 출처 간 불일치를 실제로 구별하는 이유와 함께 설명해 주세요.",
                           "같은 오류의 재발을 줄이기 위해 원자료에 추가할 확인 항목 하나와 변경 이력을 남길 책임 주체를 제시해 주세요."
                       ],
        "question_source":  "codex_cli"
    }
]
"""
)


BLIND_RULE_NEGATIVE_CASE_IDS = frozenset(
    {"objective_evaluation", "visualization_data_accuracy"}
)


@pytest.mark.parametrize(
    "item",
    TARGETED_B3_FINAL_REALISM_CASES,
    ids=lambda item: str(item["case_id"]),
)
def test_targeted_b3_final_actual_generation_matches_human_realism_oracle(
    item: dict[str, object],
) -> None:
    result = evaluate_question_realism(item)
    expected_pass = item["case_id"] not in BLIND_RULE_NEGATIVE_CASE_IDS

    assert result["policy_version"] == "field-realism-v3.14"
    assert result["passed"] is expected_pass, (item["case_id"], result["issues"])
    assert result["checks"]["no_prescribed_answer"] is expected_pass
    assert result["checks"]["no_generic_template_scaffolding"] is True
    assert result["checks"]["concrete_scenario"] is True
    assert result["metrics"]["scenario_signals"]["concrete_fact"] is True
    assert result["metrics"]["scenario_signals"]["dilemma"] is True
    assert result["metrics"]["adaptive_follow_up_count"] >= 2
    assert result["metrics"]["required_adaptive_follow_up_count"] == 2
    assert result["metrics"]["overload_warning"] is False


def test_targeted_b3_final_actual_generation_oracle_is_complete() -> None:
    assert len(TARGETED_B3_FINAL_REALISM_CASES) == 3
    assert {item["case_id"] for item in TARGETED_B3_FINAL_REALISM_CASES} == {
        "closing_report_rules",
        "objective_evaluation",
        "visualization_data_accuracy",
    }
