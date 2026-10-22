from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import ai_question_quality_review as review_service
from app.services import alio_live_adapters, provider_config


def _review_response(*, valid: bool = True) -> dict[str, Any]:
    if valid:
        body: Any = {
            "reviews": [
                {
                    "index": 1,
                    "scores": {
                        dimension: 4
                        for dimension in review_service.AI_QUALITY_DIMENSIONS
                    },
                    "reason_codes": [],
                    "regeneration_guidance_codes": [],
                }
            ]
        }
    else:
        body = {"review": {"score": 4, "provider_debug": "must-not-leak"}}
    return {
        "choices": [
            {"message": {"content": json.dumps(body, ensure_ascii=False)}}
        ]
    }


def _review(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "questions": [
            {
                "type": "경험면접",
                "ncsClCd": "0202030203_25v1",
                "question_evidence_id": "ksa-evidence-1",
                "question": "업무 자료의 오류를 발견하고 수정한 경험을 설명해 주세요.",
                "follow_ups": [
                    "어떤 근거로 오류라고 판단했습니까?",
                    "직접 취한 조치는 무엇입니까?",
                    "수정 결과를 어떻게 확인했습니까?",
                ],
                "evaluation_points": [
                    "오류 식별 근거",
                    "본인의 구체적 조치",
                    "수정 결과의 검증",
                    "업무 기준의 적용",
                ],
            }
        ],
        "ncs_matches": [
            {
                "ncsClCd": "0202030203_25v1",
                "compeUnitName": "사무행정 업무관리",
                "compeUnitDef": "업무 자료를 기준에 따라 점검하고 관리한다.",
            }
        ],
        "ncs_ksa": [
            {
                "evidence_id": "ksa-evidence-1",
                "ncsClCd": "0202030203_25v1",
                "compeUnitName": "사무행정 업무관리",
                "compeUnitDef": "업무 자료를 기준에 따라 점검하고 관리한다.",
                "factorName": "업무 자료의 오류를 확인하는 능력",
                "ksaTypeName": "기술",
            }
        ],
        "interview_methods": ["경험면접"],
        "job_context": {"duties": "행정 자료 작성과 검증"},
        "provider": "openrouter_api",
        "api_key_override": "sk-or-test-request-key",
    }
    kwargs.update(overrides)
    return review_service.review_interview_questions_with_ai(**kwargs)


def test_openrouter_invalid_shape_uses_one_strict_recovery_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_model = "openai/gpt-oss-120b:free"
    recovery_model = "openai/gpt-oss-20b:free"
    monkeypatch.setattr(provider_config, "OPENROUTER_DEFAULT_MODEL", primary_model)
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", recovery_model)
    calls: list[dict[str, Any]] = []
    responses = [_review_response(valid=False), _review_response(valid=True)]

    def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(review_service, "post_chat_completions_with_retries", fake_post)

    result = _review()

    assert len(calls) == 2
    assert calls[0]["payload"]["model"] == primary_model
    assert calls[0]["payload"]["response_format"]["type"] == "json_schema"
    assert calls[0]["payload"]["response_format"]["json_schema"]["strict"] is True
    assert calls[0]["payload"]["provider"]["require_parameters"] is True
    assert calls[1]["payload"]["model"] == recovery_model
    assert calls[1]["payload"]["response_format"] == {"type": "json_object"}
    assert calls[1]["max_attempts"] == 1
    prompt = calls[0]["payload"]["messages"][1]["content"]
    assert "STRICT_OUTPUT_CONTRACT" in prompt
    assert "exactly 1 objects" in prompt
    assert result["status"] == "passed"
    assert result["model"] == recovery_model
    assert result["format_recovery_used"] is True
    assert result["format_recovery_reason"] == "ai_quality_review_invalid_shape"
    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert "sk-or-test-request-key" not in serialized
    assert "provider_debug" not in serialized


def test_openrouter_recovery_keeps_strict_parser_and_safe_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", "openai/gpt-oss-20b:free")
    call_count = 0

    def invalid_post(**_kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return _review_response(valid=False)

    monkeypatch.setattr(review_service, "post_chat_completions_with_retries", invalid_post)

    with pytest.raises(review_service.AIQuestionQualityReviewError) as captured:
        _review()

    assert call_count == 2
    assert captured.value.code == "ai_quality_review_invalid_shape_after_recovery"
    assert "provider_debug" not in str(captured.value)
    classified = alio_live_adapters._classified_quality_review_error(captured.value)
    assert classified is not None
    assert str(classified) == (
        "ai_quality_review_invalid_shape_after_recovery|retryable=true"
    )


def test_openrouter_transport_recovery_failure_does_not_add_a_third_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", "openai/gpt-oss-20b:free")
    response = _review_response(valid=False)
    response["_ncscope_openrouter_timeout_recovery_used"] = True
    calls = 0

    def recovered_but_invalid(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return response

    monkeypatch.setattr(
        review_service,
        "post_chat_completions_with_retries",
        recovered_but_invalid,
    )

    with pytest.raises(review_service.AIQuestionQualityReviewError) as captured:
        _review()

    assert calls == 1
    assert captured.value.code == "ai_quality_review_invalid_shape_after_recovery"


def test_openrouter_invalid_shape_without_recovery_model_keeps_primary_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_RECOVERY_MODEL", raising=False)
    monkeypatch.setattr(
        review_service,
        "post_chat_completions_with_retries",
        lambda **_kwargs: _review_response(valid=False),
    )

    with pytest.raises(review_service.AIQuestionQualityReviewError) as captured:
        _review()

    assert captured.value.code == "ai_quality_review_invalid_shape"
