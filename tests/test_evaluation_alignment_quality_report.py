from __future__ import annotations

from app.main import _attach_question_quality_report
from app.services.question_evaluation_alignment import EVALUATION_ELICITATION_POLICY


def test_quality_report_exposes_evaluation_alignment_as_shadow_signal() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "경험면접",
                "question_source": "model",
                "question": (
                    "부서별 집행표와 원장의 금액이 달랐던 경험에서 어떤 자료를 "
                    "대조하고 어떤 조치를 했으며 결과는 무엇이었습니까?"
                ),
                "follow_ups": [
                    "방금 고른 자료를 먼저 본 이유는 무엇입니까?",
                    "그 조치 뒤에도 차이가 남았다면 무엇을 확인했습니까?",
                    "최종 결과는 어떤 기록으로 남겼습니까?",
                ],
                "evaluation_points": [
                    "자료 대조",
                    "판단 근거",
                    "수정 조치",
                    "질문하지 않은 실행 책임자",
                ],
            }
        ]
    }

    result = _attach_question_quality_report(strategy)
    report = result["question_quality_report"]
    item = report["items"][0]

    assert report["evaluation_alignment_policy"] == EVALUATION_ELICITATION_POLICY
    assert report["evaluation_alignment_enforcement"] == "shadow"
    assert item["evaluation_alignment_decision"] == "fail"
    assert item["evaluation_alignment_issues"]
    assert report["summary"]["evaluation_alignment_fail_count"] == 1
    assert "evaluation_alignment" not in item["checks"]
    assert "evaluation_alignment" not in item["issues"]
