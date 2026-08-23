from __future__ import annotations

from app.main import (
    _build_generated_question_text_payload,
    _build_generated_questions_payload,
    _extract_all_generated_question_items,
    _extract_generated_question_items,
    _normalize_generated_questions,
)


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


def test_main_strategy_exports_keep_degraded_fallback_in_review_state() -> None:
    strategy = {
        "question_plan": {"total_main_count": 1},
        "interview_questions": [
            {
                "question": "서버가 구성한 검토용 질문입니다.",
                "type": "경험면접",
                "question_source": "server_ksa_fallback",
                "degraded": True,
                "human_review_required": True,
            }
        ],
    }

    preview = _build_generated_questions_payload(
        strategy,
        strategy["question_plan"],
        include_all=False,
    )
    all_rows = _extract_all_generated_question_items(strategy)
    text_rows, _ = _build_generated_question_text_payload(strategy)

    for rows in (preview, all_rows, text_rows):
        assert rows[0]["ready"] is False
        assert rows[0]["review_required"] is True


def test_main_strategy_exports_keep_template_fallback_in_review_state() -> None:
    strategy = {
        "interview_questions": [
            {
                "question": "템플릿 기반 검토용 질문입니다.",
                "question_source": "template_fallback",
            }
        ]
    }

    assert _extract_generated_question_items(strategy)[0]["ready"] is False
    assert _extract_generated_question_items(strategy)[0]["review_required"] is True
