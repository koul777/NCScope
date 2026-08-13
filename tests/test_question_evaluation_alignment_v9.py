from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


# Report-independent candidate-visible freeze of the fresh ethics item.
FRESH_DATA_USE_ETHICS: dict[str, Any] = {
    "type": "경험면접",
    "question": (
        "성과 현황을 도표로 정리해 공유해야 하는 촉박한 일정에서, 이용 목적이나 "
        "열람 범위가 불분명한 내부 자료를 사용하면 업무는 빨라지지만 부적절한 노출 "
        "우려가 있었던 실제 사례를 말씀해 주세요. 같은 경험이 없다면 학업·프로젝트·"
        "봉사에서 가장 가까운 사례로 답해도 좋습니다. 당시 본인의 역할, 선택한 행동 "
        "하나와 관찰된 결과는 무엇이었습니까?"
    ),
    "follow_ups": [
        (
            "방금 말씀하신 선택을 할 때 자료의 이용 목적, 접근 가능 대상, 공유 범위 "
            "가운데 무엇을 근거로 삼았으며, 답변에 그 근거가 없다면 당시 무엇을 "
            "추가로 확인할 수 있었습니까?"
        ),
        (
            "앞서 언급한 결과에서 자료의 사용 범위나 표현 방식을 조정했다면 무엇을 "
            "바꿨고 어떻게 확인했는지, 조정하지 않았다면 그 이유와 권한자에게 요청한 "
            "다음 조치를 말씀해 주세요."
        ),
        (
            "같은 상황에서 업무 일정과 자료 이용의 적정성이 다시 충돌한다면, 본인의 "
            "권한 안에서 어떤 조건에 따라 사용·제한·이송 여부를 정하겠습니까?"
        ),
    ],
    "evaluation_points": [
        "자료 이용 목적과 실제 업무 필요성 사이의 연관성을 구체적으로 설명하는가",
        "열람 대상과 공유 범위를 구분하여 선택 근거를 제시하는가",
        "본인의 역할과 권한 안에서 수행한 행동을 명확히 밝히는가",
        (
            "선택 결과를 확인한 근거와 필요시 수정하거나 권한자에게 이송할 조건을 "
            "설명하는가"
        ),
    ],
}


def test_fresh_data_use_ethics_human_alignment_passes_v9() -> None:
    result = evaluate_evaluation_elicitation_alignment(FRESH_DATA_USE_ETHICS)
    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["policy"] == "evaluation-elicitation-alignment-v9"
    assert result["decision"] == "pass", result
    assert result["metrics"]["matched_atom_count"] == 8


def test_fresh_data_use_loses_alignment_when_unique_purpose_probe_is_removed() -> None:
    item = deepcopy(FRESH_DATA_USE_ETHICS)
    item["follow_ups"].pop(0)
    result = evaluate_evaluation_elicitation_alignment(item)
    assert result["decision"] != "pass", result
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == "document_purpose"
        for issue in result["issues"]
    )


def test_fresh_data_use_rejects_unrelated_cross_factor() -> None:
    item = deepcopy(FRESH_DATA_USE_ETHICS)
    item["evaluation_points"][1] = (
        "계획값·실적값·차이 근거가 대응되는 분석표를 구성하는가"
    )
    assert evaluate_evaluation_elicitation_alignment(item)["decision"] == "fail"


_HIDDEN_CRITERIA = (
    ("개선안의 실행 책임자", "execution_owner"),
    ("검토서의 부서장 결재선과 승인 절차", "approval_process"),
    ("처리 기록의 법정 보존 기간과 폐기 시점", "record_retention"),
    ("재발 방지를 위한 전 직원 교육", "prevention_training"),
)


@pytest.mark.parametrize(("criterion", "family"), _HIDDEN_CRITERIA)
def test_fresh_data_use_hidden_roles_remain_closed(
    criterion: str, family: str
) -> None:
    item = deepcopy(FRESH_DATA_USE_ETHICS)
    item["evaluation_points"][3] = criterion
    result = evaluate_evaluation_elicitation_alignment(item)
    assert result["decision"] == "fail", (family, result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == family
        for issue in result["issues"]
    )


def test_v9_negation_and_explicit_single_selection_do_not_open_scope() -> None:
    negated = {
        "type": "경험면접",
        "question": (
            "열람 대상과 공유 범위는 설명하지 마십시오. 대신 선택한 행동만 설명해 주세요."
        ),
        "follow_ups": [],
        "evaluation_points": [
            "열람 대상",
            "공유 범위",
            "열람 대상과 공유 범위의 구분",
            "선택 근거",
        ],
    }
    assert evaluate_evaluation_elicitation_alignment(negated)["decision"] == "fail"

    single = deepcopy(negated)
    single["question"] = "열람 대상과 공유 범위 중 하나만 선택해 설명해 주세요."
    result = evaluate_evaluation_elicitation_alignment(single)
    assert result["decision"] == "fail", result
    assert any(issue["code"] == "quantifier_scope_mismatch" for issue in result["issues"])
