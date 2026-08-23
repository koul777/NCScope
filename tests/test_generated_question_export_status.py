from __future__ import annotations

from app.main import _normalize_generated_questions


def test_degraded_fallback_question_is_not_exported_as_ready() -> None:
    rows = _normalize_generated_questions(
        [
            {
                "question": "해당 직무에서 자료를 확인한 경험을 말씀해 주세요.",
                "type": "경험면접",
                "question_source": "server_ksa_fallback",
                "human_review_required": True,
                "degraded": True,
            }
        ],
        expected_count=1,
    )

    assert rows[0]["question"]
    assert rows[0]["review_required"] is True
    assert rows[0]["ready"] is False


def test_model_question_without_review_flags_remains_ready() -> None:
    rows = _normalize_generated_questions(
        [
            {
                "question": "실제 경험에서 맡은 역할과 결과를 설명해 주세요.",
                "type": "경험면접",
                "question_source": "openrouter_api",
            }
        ],
        expected_count=1,
    )

    assert rows[0]["ready"] is True
    assert "review_required" not in rows[0]
