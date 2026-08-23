from __future__ import annotations

import pytest

from app.main import _run_runtime_question_quality_orchestration


METHODS = (
    "경험면접",
    "상황면접",
    "발표면접",
    "토론면접",
    "인바스켓면접",
    "직무지식면접",
    "창의적 문제해결력면접",
)


@pytest.mark.parametrize("method", METHODS)
def test_every_supported_method_runs_the_same_quality_orchestration_and_surface_guard(
    method: str,
) -> None:
    """Keep method routing and public-source sanitization on one runtime path.

    This is intentionally a runtime contract test rather than a provider test:
    all providers (and the deterministic fallback) must pass through the same
    audit/repair/recheck pipeline before the question is returned to the UI.
    """

    question = {
        "type": method,
        "question_source": "openrouter_api",
        "ncsClCd": "2306010214_21v3",
        "competency": "전기안전관리",
        "ncs_detail": "전기안전관리",
        "question_focus": "전기작업 안전관리 절차",
        "question_focus_type": "지식",
        "question_focus_surface": "전기작업 안전관리 절차 적용과 검증",
        "question": (
            "공고·직무기술서상 국가유공자 지원요건을 검토하며 전기작업 안전관리 절차를 "
            "실제 업무에 적용한 상황에서 본인의 판단과 행동, 결과를 설명해 주세요."
        ),
        "follow_ups": [
            "당시 상황과 본인 역할을 설명해 주세요.",
            "판단 근거와 확인한 자료를 설명해 주세요.",
            "결과를 어떻게 확인하고 개선했는지 설명해 주세요.",
        ],
        "evaluation_points": [
            "상황과 역할",
            "판단 근거",
            "실행 행동",
            "결과와 개선",
        ],
    }
    ncs_ksa = [
        {
            "ncsClCd": "2306010214_21v3",
            "compeUnitName": "전기안전관리",
            "factorName": "전기작업 안전관리 절차",
            "ksaTypeName": "지식",
            "factorSource": "ncs-mcp",
            "evidence_id": "ksa_method_contract",
        }
    ]

    result = _run_runtime_question_quality_orchestration(
        {"interview_questions": [question]},
        question_plan={"total_main_count": 1, "follow_up_count": 3},
        ncs_ksa=ncs_ksa,
        avoid_questions=[],
    )

    item = result["interview_questions"][0]
    orchestration = result["question_quality_orchestration"]
    assert item["type"] == method
    assert orchestration["policy"].startswith("ncs_ksa_runtime_orchestration_")
    assert orchestration["question_count"] == 1
    assert orchestration["stages"][-1]["name"] == "full_quality_gate"
    assert isinstance(result.get("question_quality_report"), dict)

    public_text = " ".join(
        [
            str(item.get("question") or ""),
            *(str(value) for value in item.get("follow_ups") or []),
            *(str(value) for value in item.get("evaluation_points") or []),
        ]
    )
    assert "공고·직무기술서상" not in public_text
    assert "국가유공자" not in public_text

