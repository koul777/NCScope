from __future__ import annotations

from typing import Any

import pytest

from app.main import (
    _adjust_generated_questions,
    _attach_ksa_evidence_to_strategy,
    _enrich_ai_authored_question_metadata,
    _planned_question_evidence_assignments,
    _raw_model_evidence_assignment_report,
)
from app.services.question_surface import stable_ksa_evidence_id


CODE = "0203010101_24v1"


def _unit() -> dict[str, Any]:
    return {
        "ncsClCd": CODE,
        "compeUnitName": "예산 편성",
        "compeUnitDef": "사업별 예산안을 검토하고 편성한다.",
        "ncsSubdCdnm": "예산",
        "ncsSclasCdnm": "재무관리",
        "matchedDetailName": "예산",
    }


def _evidence(factor: str, number: str) -> dict[str, str]:
    return {
        "ncsClCd": CODE,
        "compeUnitName": "예산 편성",
        "elementName": "사업별 예산 검토",
        "ksaTypeName": "기술",
        "factorName": factor,
        "factorNo": number,
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }


def _plan() -> dict[str, Any]:
    return {
        "question_sequence": [{"detail": "예산", "follow_up_count": 3}],
        "total_main_count": 1,
        "follow_up_count": 3,
    }


def test_server_manifest_owns_the_index_evidence_assignment() -> None:
    first = _evidence("예산 집행 자료 분석 능력", "1")
    second = _evidence("비용 효과 분석 능력", "2")

    runtime_plan, locks = _planned_question_evidence_assignments(
        question_plan=_plan(),
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first, second],
    )

    expected_id = stable_ksa_evidence_id(first)
    assert locks == [(1, expected_id)]
    assert runtime_plan["question_sequence"][0]["evidence_id"] == expected_id

    report = _raw_model_evidence_assignment_report(
        {
            "interview_questions": [
                {"question_evidence_id": stable_ksa_evidence_id(second)}
            ]
        },
        locks,
    )
    assert report["passed"] is False
    assert report["mismatched_indexes"] == [1]


def test_surface_only_rows_are_bound_to_planned_evidence_by_server_order() -> None:
    first = _evidence("예산 집행 자료 분석 능력", "1")
    runtime_plan, locks = _planned_question_evidence_assignments(
        question_plan=_plan(),
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first],
    )
    raw = {
        "interview_questions": [
            {
                "question": "실제 예산 자료를 검토한 경험을 설명해 주세요.",
                "follow_ups": ["어떤 근거를 먼저 확인했습니까?"],
                "evaluation_points": ["검토 근거", "수행 행동", "결과", "검증"],
            }
        ]
    }

    legacy_report = _raw_model_evidence_assignment_report(raw, locks)
    server_report = _raw_model_evidence_assignment_report(
        raw,
        locks,
        server_locked_by_order=True,
    )
    enriched = _enrich_ai_authored_question_metadata(
        raw,
        question_plan=runtime_plan,
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first],
        server_locked_by_order=True,
    )
    question = enriched["interview_questions"][0]

    assert legacy_report["passed"] is False
    assert server_report == {
        "policy": "planned-question-evidence-assignment-v1",
        "applicable": True,
        "passed": True,
        "expected_count": 1,
        "matched_count": 1,
        "mismatch_count": 0,
        "mismatched_indexes": [],
    }
    assert question["question_evidence_assignment_valid"] is True
    assert question["question_evidence_id"] == stable_ksa_evidence_id(first)
    assert question["question_focus"] == first["factorName"]


@pytest.mark.parametrize(
    ("rows", "locks", "expected_indexes"),
    [
        ([], [(1, "ksa_000000000000000000000000")], [1]),
        (
            [{"question": "one"}, {"question": "extra"}],
            [(1, "ksa_000000000000000000000000")],
            [2],
        ),
        ([{"question": "one"}], [], [1]),
        ([{"question": "one"}], [(2, "ksa_000000000000000000000000")], [1, 2]),
    ],
)
def test_server_order_lock_fails_closed_for_incomplete_manifest_or_row_count(
    rows: list[dict[str, Any]],
    locks: list[tuple[int, str]],
    expected_indexes: list[int],
) -> None:
    report = _raw_model_evidence_assignment_report(
        {"interview_questions": rows},
        locks,
        server_locked_by_order=True,
    )

    assert report["applicable"] is True
    assert report["passed"] is False
    assert report["mismatched_indexes"] == expected_indexes


def test_server_order_lock_rejects_conflicting_provider_id_but_keeps_server_id() -> None:
    first = _evidence("예산 집행 자료 분석 능력", "1")
    second = _evidence("비용 효과 분석 능력", "2")
    runtime_plan, locks = _planned_question_evidence_assignments(
        question_plan=_plan(),
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first, second],
    )
    raw = {
        "interview_questions": [
            {
                "question": "예산 검토 경험을 설명해 주세요.",
                "follow_ups": ["근거는 무엇입니까?"],
                "evaluation_points": ["근거", "행동", "결과", "검증"],
                "question_evidence_id": stable_ksa_evidence_id(second),
            }
        ]
    }

    report = _raw_model_evidence_assignment_report(
        raw,
        locks,
        server_locked_by_order=True,
    )
    enriched = _enrich_ai_authored_question_metadata(
        raw,
        question_plan=runtime_plan,
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first, second],
        server_locked_by_order=True,
    )
    question = enriched["interview_questions"][0]

    assert report["passed"] is False
    assert report["mismatched_indexes"] == [1]
    assert question["question_evidence_assignment_valid"] is False
    assert question["question_evidence_id"] == stable_ksa_evidence_id(first)
    assert question["question_focus"] == first["factorName"]


@pytest.mark.parametrize(
    "raw_id",
    ["", "ksa_000000000000000000000000"],
)
def test_model_backed_evidence_enrichment_never_invents_or_replaces_id(
    raw_id: str,
) -> None:
    row = _evidence("예산 집행 자료 분석 능력", "1")
    question = {
        "question_source": "openai_api",
        "question": "부서별 집행표와 원장의 금액이 다를 때 무엇을 먼저 확인하겠습니까?",
        "ncsClCd": CODE,
        "question_evidence_id": raw_id,
        "question_evidence_required": True,
        "follow_ups": [],
        "evaluation_points": [],
    }

    result = _attach_ksa_evidence_to_strategy(
        {"interview_questions": [question]},
        [row],
    )
    enriched = result["interview_questions"][0]

    assert enriched.get("question_evidence_id", "") == raw_id
    assert not enriched.get("ksa_evidence")
    assert not enriched.get("evidence_ids")


def test_adjustment_uses_planned_evidence_but_marks_swapped_raw_id_invalid() -> None:
    first = _evidence("예산 집행 자료 분석 능력", "1")
    second = _evidence("비용 효과 분석 능력", "2")
    runtime_plan, _locks = _planned_question_evidence_assignments(
        question_plan=_plan(),
        interview_methods=["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first, second],
    )

    result = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question_source": "openai_api",
                    "question_evidence_id": stable_ksa_evidence_id(second),
                    "ncsClCd": CODE,
                    "question": (
                        "부서별 집행표와 회계 원장의 금액이 다른 채 마감이 하루 남았습니다. "
                        "어떤 자료를 먼저 대조하고 정정안을 어떻게 확정하겠습니까?"
                    ),
                    "follow_ups": [
                        "방금 선택한 자료에서 다른 불일치가 나오면 판단을 어떻게 바꾸겠습니까?",
                        "말씀한 정정안을 반대하는 부서에는 어떤 근거를 제시하겠습니까?",
                        "최종 정정 결과는 어떤 기록으로 남기겠습니까?",
                    ],
                    "evaluation_points": [
                        "대조 자료의 적절성",
                        "정정 판단의 근거",
                        "이해관계자 조정 행동",
                        "결과 기록의 검증 가능성",
                    ],
                }
            ]
        },
        runtime_plan,
        ["상황면접"],
        ncs_matches=[_unit()],
        ncs_ksa=[first, second],
    )
    question = result["interview_questions"][0]

    assert question["question_evidence_assignment_valid"] is False
    assert question["question_evidence_id"] == stable_ksa_evidence_id(first)
    assert question["question_focus"] == first["factorName"]
