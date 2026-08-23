from __future__ import annotations

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import jd_strategy, openai_http, question_generation
from app.services.provider_config import (
    GenerationCredentialError,
    OPENROUTER_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_MODEL,
    detect_generation_provider_from_key,
    openrouter_reasoning_effort,
    prepare_chat_payload,
    provider_base_url,
    resolve_generation_credential,
    resolve_generation_model,
)


def test_openrouter_reasoning_policy_promotes_complex_methods_without_changing_request_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENROUTER_HIGH_RISK_REASONING_EFFORT", "high")
    effort, reason = openrouter_reasoning_effort(
        interview_methods=["발표면접"],
        target_count=1,
        follow_up_count=3,
        stage="primary",
    )

    assert effort == "high"
    assert reason == "high_risk_interview_method"


def test_openrouter_reasoning_policy_promotes_quality_retry_and_keeps_standard_medium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    standard, standard_reason = openrouter_reasoning_effort(
        interview_methods=["경험면접"],
        target_count=1,
        follow_up_count=3,
        stage="primary",
    )
    retry, retry_reason = openrouter_reasoning_effort(
        interview_methods=["경험면접"],
        target_count=1,
        follow_up_count=3,
        stage="quality_retry",
    )

    assert (standard, standard_reason) == ("medium", "standard_generation")
    assert (retry, retry_reason) == ("high", "quality_retry")


def test_openrouter_internal_reasoning_effort_overrides_primary_deployment_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    prepared = prepare_chat_payload(
        {
            "model": OPENROUTER_DEFAULT_MODEL,
            "messages": [],
            "reasoning_effort": "high",
            "_openrouter_internal_reasoning_effort": "high",
        },
        "openrouter_api",
    )

    assert prepared["reasoning_effort"] == "high"
    assert "_openrouter_internal_reasoning_effort" not in prepared


def _candidate(slot: int, variant: int) -> dict:
    scenarios = (
        "월별 지출 증빙과 원장 합계가 맞지 않는 사건",
        "긴급 구매 요청과 승인 권한이 충돌한 사건",
        "성과 보고 자료의 기준 시점이 부서마다 다른 사건",
    )
    return {
        "type": "상황면접" if slot % 2 == 0 else "직무지식면접",
        "competency": f"예산 검토 {slot}",
        "ncsClCd": "0201010101",
        "question": (
            f"후보 {variant}에서 {scenarios[slot % len(scenarios)]}의 핵심 판단과 "
            "검토 기록 산출물을 설명해 주세요."
        ),
        "follow_ups": [
            "방금 선택한 근거 중 가장 중요한 것은 무엇입니까?",
            "앞서 설명한 결과가 달랐다면 무엇을 수정하겠습니까?",
            "검토 기록에 반드시 남길 항목은 무엇입니까?",
        ],
        "evaluation_points": [
            "근거 식별",
            "대안 비교",
            "권한 확인",
            "결과 검증",
        ],
        "question_focus": f"예산 근거 {slot}",
        "question_focus_surface": f"예산 검토 {slot}",
        "ksa_refs": [f"예산 근거 {slot}"],
        "question_evidence_id": f"ksa_{slot:024x}",
        "question_task_frame": {
            "scenario_frame": "자료 불일치",
            "difficulty": "심화",
            "constraint_axis": "마감",
        },
    }


def test_generic_key_detection_and_provider_mismatch_are_fail_closed() -> None:
    assert detect_generation_provider_from_key("sk-or-v1-test") == "openrouter_api"
    assert detect_generation_provider_from_key("sk-proj-test") == "openai_api"

    resolved = resolve_generation_credential(
        generation_api_key="sk-or-v1-test",
        requested_provider="openrouter",
    )
    assert resolved.provider == "openrouter_api"
    assert resolved.api_key == "sk-or-v1-test"

    with pytest.raises(GenerationCredentialError, match="generation_provider_key_mismatch"):
        resolve_generation_credential(
            generation_api_key="sk-or-v1-test",
            requested_provider="openai_api",
        )
    with pytest.raises(GenerationCredentialError, match="generation_api_key_ambiguous"):
        resolve_generation_credential(
            generation_api_key="sk-or-v1-test",
            openai_api_key="sk-proj-test",
        )


def test_openrouter_model_url_and_payload_are_fixed_and_capability_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://admin-openai.example/v1")
    assert provider_base_url("openrouter") == OPENROUTER_DEFAULT_BASE_URL
    assert resolve_generation_model(
        provider="openrouter",
        explicit_model="user-controlled-model",
    ) == OPENROUTER_DEFAULT_MODEL

    prepared = prepare_chat_payload(
        {
            "model": "gpt-5.6-sol",
            "n": 3,
            "temperature": 0.8,
            "max_completion_tokens": 12_000,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "questions", "schema": {}},
            },
        },
        "openrouter",
    )
    assert prepared["model"] == OPENROUTER_DEFAULT_MODEL
    assert prepared["reasoning_effort"] == "max"
    assert prepared["max_tokens"] == 12_000
    assert prepared["response_format"] == {"type": "json_object"}
    assert "n" not in prepared
    assert "temperature" not in prepared
    assert "max_completion_tokens" not in prepared

    recovered = prepare_chat_payload(
        {
            "model": "ignored",
            "reasoning_effort": "max",
            "_openrouter_internal_recovery_effort": "medium",
        },
        "openrouter",
    )
    assert recovered["reasoning_effort"] == "medium"
    assert "_openrouter_internal_recovery_effort" not in recovered


def test_openrouter_primary_reasoning_effort_can_be_lowered_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")

    prepared = prepare_chat_payload(
        {
            "model": "ignored",
            "messages": [],
        },
        "openrouter",
    )

    assert prepared["reasoning_effort"] == "medium"


def test_http_client_routes_openrouter_key_only_to_openrouter_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"choices": []}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict) -> _Response:
            del json
            calls.append((url, dict(headers)))
            return _Response()

    monkeypatch.setattr(openai_http.httpx, "Client", _Client)
    result = openai_http.post_chat_completions_with_retries(
        payload={"model": OPENROUTER_DEFAULT_MODEL, "messages": []},
        api_key="sk-or-v1-secret",
        max_attempts=1,
        provider="openrouter",
    )

    assert result == {"choices": []}
    assert calls[0][0] == f"{OPENROUTER_DEFAULT_BASE_URL}/chat/completions"
    assert calls[0][1]["Authorization"] == "Bearer sk-or-v1-secret"
    assert "api.openai.com" not in calls[0][0]

    with pytest.raises(RuntimeError, match="generation_provider_key_mismatch"):
        openai_http.post_chat_completions_with_retries(
            payload={"model": "gpt-test", "messages": []},
            api_key="sk-or-v1-must-not-route-to-openai",
            max_attempts=1,
            provider="openai_api",
        )
    with pytest.raises(RuntimeError, match="generation_provider_key_mismatch"):
        openai_http.post_chat_completions_with_retries(
            payload={"model": OPENROUTER_DEFAULT_MODEL, "messages": []},
            api_key="sk-proj-must-not-route-to-openrouter",
            max_attempts=1,
            provider="openrouter_api",
        )
    assert len(calls) == 1


def test_openrouter_timeout_can_use_explicit_effort_rescue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "{}"}}]}

    class _TimeoutThenSuccessClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, headers: dict[str, str], json: dict) -> _Response:
            del headers
            calls.append(dict(json))
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow Ox Alpha response")
            return _Response()

    monkeypatch.setenv("OPENROUTER_FALLBACK_REASONING_EFFORT", "high")
    monkeypatch.setattr(openai_http.httpx, "Client", _TimeoutThenSuccessClient)
    result = openai_http.post_chat_completions_with_retries(
        payload={
            "model": OPENROUTER_DEFAULT_MODEL,
            "messages": [],
            "reasoning_effort": "max",
            "max_tokens": 12000,
        },
        api_key="sk-or-v1-secret",
        timeout_sec=30,
        max_attempts=1,
        provider="openrouter_api",
    )

    assert result["choices"]
    assert result["_ncscope_openrouter_timeout_recovery_used"] is True
    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "max"
    assert calls[1]["reasoning_effort"] == "high"


def test_openrouter_timeout_can_fail_over_to_server_owned_free_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "{}"}}]}

    class _TimeoutThenSuccessClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, headers: dict[str, str], json: dict) -> _Response:
            del headers
            calls.append(dict(json))
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow Ox Alpha response")
            return _Response()

    monkeypatch.setenv("OPENROUTER_FALLBACK_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(openai_http.httpx, "Client", _TimeoutThenSuccessClient)

    result = openai_http.post_chat_completions_with_retries(
        payload={
            "model": OPENROUTER_DEFAULT_MODEL,
            "messages": [],
            "reasoning_effort": "max",
            "response_format": {"type": "json_object"},
        },
        api_key="sk-or-v1-secret",
        timeout_sec=8,
        max_attempts=1,
        provider="openrouter_api",
    )

    assert result["_ncscope_openrouter_timeout_recovery_used"] is True
    assert calls[0]["model"] == OPENROUTER_DEFAULT_MODEL
    assert calls[0]["reasoning_effort"] == "max"
    assert calls[1]["model"] == "openai/gpt-oss-20b"
    assert "reasoning_effort" not in calls[1]
    assert calls[1]["response_format"] == {"type": "json_object"}


def test_openrouter_slim_payload_uses_configured_recovery_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", "openai/gpt-oss-20b")

    prepared = prepare_chat_payload(
        {
            "model": OPENROUTER_DEFAULT_MODEL,
            "messages": [],
            "reasoning_effort": "medium",
            "response_format": {"type": "json_object"},
            "_openrouter_internal_recovery_model": "configured",
            "_openrouter_internal_recovery_effort": "medium",
        },
        "openrouter_api",
    )

    assert prepared["model"] == "openai/gpt-oss-20b"
    assert "reasoning_effort" not in prepared
    assert "response_format" not in prepared
    assert not any(key.startswith("_openrouter_internal") for key in prepared)


def test_openrouter_uses_opted_in_environment_key_when_request_key_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_key = "sk-or-v1-server-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", server_key)
    monkeypatch.setenv("OPENROUTER_ALLOW_SERVER_KEY", "true")
    assert main.settings.resolve_openrouter_key("") == server_key
    assert main.settings.openrouter_key_source("") == "server_env"
    resolved = resolve_generation_credential(requested_provider="openrouter_api")
    assert resolved.provider == "openrouter_api"
    assert resolved.api_key == ""
    provider, model, key = main._resolve_request_generation(provider="openrouter_api")
    assert (provider, model, key) == (
        "openrouter_api",
        OPENROUTER_DEFAULT_MODEL,
        server_key,
    )
    request_key = "sk-or-v1-request-override"
    assert main.settings.resolve_openrouter_key(request_key) == request_key
    assert main.settings.openrouter_key_source(request_key) == "request"


def test_main_generic_contract_auto_detects_and_rejects_mismatch() -> None:
    provider, model, key = main._resolve_request_generation(
        generation_api_key="sk-or-v1-test",
        provider="openrouter",
    )
    assert (provider, model, key) == (
        "openrouter_api",
        OPENROUTER_DEFAULT_MODEL,
        "sk-or-v1-test",
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main._resolve_request_generation(
            generation_api_key="sk-or-v1-test",
            provider="openai_api",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "generation_provider_key_mismatch"
    assert "sk-or-v1-test" not in str(exc_info.value.detail)


def test_status_accepts_openrouter_alias_and_returns_canonical_provider() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status?provider=openrouter")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openrouter_api"
    assert payload["default_model"] == OPENROUTER_DEFAULT_MODEL
    assert payload["status"] == "key_required"
    assert payload["credential_configured"] is False


@pytest.mark.parametrize(
    ("reason", "status_code", "public_code"),
    [
        ("openrouter_http_401", 401, "openrouter_api_authentication_failed"),
        ("openrouter_http_429", 429, "openrouter_api_usage_limit_reached"),
        ("openrouter_request_timeout", 504, "openrouter_api_timeout"),
        ("openrouter_network_unreachable", 503, "openrouter_api_unreachable"),
        ("openrouter_http_503", 502, "openrouter_api_upstream_unavailable"),
    ],
)
def test_openrouter_failures_have_distinct_sanitized_public_codes(
    reason: str,
    status_code: int,
    public_code: str,
) -> None:
    error = main._institution_api_provider_http_error(
        RuntimeError(reason),
        provider="openrouter",
    )
    assert error.status_code == status_code
    assert error.detail["code"] == public_code
    assert error.detail["provider"] == "openrouter_api"
    assert reason not in str(error.detail)


def test_json_endpoint_rejects_provider_key_mismatch_without_reflecting_key() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-personalized",
            json={
                "generation_provider": "openai_api",
                "generation_api_key": "sk-or-v1-do-not-reflect",
                "ncs_code": "0201010101",
            },
        )
    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["code"] == "generation_provider_key_mismatch"
    assert payload["detail"]["provider"] == "openai_api"
    assert "sk-or-v1-do-not-reflect" not in response.text


def test_multipart_endpoint_rejects_provider_key_mismatch_before_processing() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("jd.txt", b"job description", "text/plain")},
            data={
                "generation_provider": "openai_api",
                "generation_api_key": "sk-or-v1-do-not-reflect",
            },
        )
    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["code"] == "generation_provider_key_mismatch"
    assert "sk-or-v1-do-not-reflect" not in response.text


def test_ncs_code_endpoint_never_returns_success_after_history_filters_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = {"question": "This exact generated question must be filtered."}
    monkeypatch.setattr(main, "_require_ncs_mcp_url", lambda: None)
    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        lambda **_kwargs: {"main_questions": [dict(question)]},
    )
    monkeypatch.setattr(main, "_require_institution_api_question_output", lambda *_: None)
    monkeypatch.setattr(main, "_require_official_ksa_result", lambda *_: None)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "generation_provider": "openrouter_api",
                "generation_api_key": "sk-or-v1-test",
                "ncs_code": "0201010101",
                "target_count": 1,
                "avoid_questions": [question["question"]],
            },
        )

    assert response.status_code == 502
    payload = response.json()
    assert payload["detail"]["code"] == "openrouter_api_generation_failed"
    assert payload["detail"]["provider"] == "openrouter_api"


def test_openrouter_strategy_uses_three_parallel_single_choice_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    lock = threading.Lock()
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "3")

    def unexpected_models_probe(**_kwargs):
        raise AssertionError("OpenRouter must not perform a redundant models probe")

    monkeypatch.setattr(jd_strategy, "_check_openai_connectivity", unexpected_models_probe)

    def fake_chat(**kwargs):
        with lock:
            calls.append(kwargs)
            variant = len(calls)
        content = {
            "interview_questions": [
                _candidate(slot=slot, variant=variant)
                for slot in range(2)
            ],
            "ncs_link": [],
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)
    result = jd_strategy.build_strategy_with_openai(
        jd_text="예산 검토",
        notice_text="",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=2,
        max_model_requests=2,
    )

    assert len(calls) == 3
    assert all(call["provider"] == "openrouter_api" for call in calls)
    assert all(call["max_attempts"] == 1 for call in calls)
    assert all(30 <= float(call["timeout_sec"]) <= 110 for call in calls)
    for call in calls:
        payload = call["payload"]
        assert payload["model"] == OPENROUTER_DEFAULT_MODEL
        assert payload["reasoning_effort"] == "max"
        assert payload["response_format"] == {"type": "json_object"}
        assert "n" not in payload
        assert "max_completion_tokens" not in payload
    assert result["generation_provider"] == "openrouter_api"
    assert result["provider_generation_model"] == OPENROUTER_DEFAULT_MODEL
    assert result["provider_generation_request_count"] == 3
    assert result["provider_candidate_variant_count"] == 3
    assert result["provider_candidate_variant_received_count"] == 3
    assert len(result["interview_questions"]) == 2
    assert all(
        row["question_source"] == "openrouter_api"
        for row in result["interview_questions"]
    )


def test_openrouter_strategy_promotes_presentation_to_high_without_extra_candidate_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENROUTER_HIGH_RISK_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "1")

    def fake_chat(**kwargs):
        calls.append(kwargs)
        content = {
            "interview_questions": [_candidate(slot=0, variant=1)],
            "ncs_link": [],
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)
    result = jd_strategy.build_strategy_with_openai(
        jd_text="발표 자료 분석",
        notice_text="공공기관 채용",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=1,
        interview_methods=["발표면접"],
        max_model_requests=1,
    )

    assert len(calls) == 1
    assert calls[0]["payload"]["reasoning_effort"] == "high"
    assert result["provider_reasoning_effort"] == "high"
    assert result["provider_reasoning_stage"] == "primary"
    assert result["provider_reasoning_reason"] == "high_risk_interview_method"


def test_openrouter_quality_retry_uses_high_profile_only_after_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENROUTER_QUALITY_RETRY_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENROUTER_INVALID_OUTPUT_RETRY_TIMEOUT_SEC", "15")
    monkeypatch.setenv("OPENROUTER_RECOVERY_MODEL", "")
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "1")

    def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"interview_questions": ['},
                    }
                ]
            }
        content = {
            "interview_questions": [_candidate(slot=0, variant=2)],
            "ncs_link": [],
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)
    result = jd_strategy.build_strategy_with_openai(
        jd_text="자료 오류",
        notice_text="공공기관 채용",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=1,
        interview_methods=["경험면접"],
        max_model_requests=2,
    )

    assert [call["payload"]["reasoning_effort"] for call in calls] == [
        "medium",
        "high",
    ]
    assert result["provider_reasoning_effort"] == "high"
    assert result["provider_reasoning_stage"] == "quality_retry"
    assert result["provider_reasoning_reason"] == "quality_retry"


def test_openrouter_invalid_max_output_uses_one_bounded_medium_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "1")
    monkeypatch.setenv("OPENROUTER_TIMEOUT_SEC", "15")
    monkeypatch.setenv("OPENROUTER_FALLBACK_TIMEOUT_SEC", "65")
    monkeypatch.setenv("OPENROUTER_FALLBACK_REASONING_EFFORT", "medium")

    def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"interview_questions": ['},
                    }
                ]
            }
        content = {
            "interview_questions": [_candidate(slot=0, variant=2)],
            "ncs_link": [],
        }
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(content, ensure_ascii=False)},
                }
            ]
        }

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)
    result = jd_strategy.build_strategy_with_openai(
        jd_text="Electrical facility maintenance",
        notice_text="Public institution hiring notice",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=1,
        max_model_requests=2,
    )

    assert [call["timeout_sec"] for call in calls] == [15.0, 65.0]
    assert [call["payload"]["reasoning_effort"] for call in calls] == [
        "max",
        "medium",
    ]
    assert result["provider_generation_request_count"] == 2
    assert result["question_generation_policy"].endswith("slim_retry")
    assert len(result["interview_questions"]) == 1
    assert "error" not in result


def test_openrouter_timeout_does_not_start_another_semantic_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "1")

    def timed_out(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("openrouter_request_timeout")

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", timed_out)
    result = jd_strategy.build_strategy_with_openai(
        jd_text="Electrical facility maintenance",
        notice_text="Public institution hiring notice",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=1,
        max_model_requests=2,
    )

    assert len(calls) == 1
    assert result["provider_generation_request_count"] == 1
    assert result["interview_questions"] == []
    assert result["error"] == "model_generation_failed: openrouter_request_timeout"


def test_openrouter_invalid_timeout_recovery_does_not_start_third_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "1")

    def invalid_recovery(**kwargs):
        calls.append(kwargs)
        return {
            "_ncscope_openrouter_timeout_recovery_used": True,
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"interview_questions": ['},
                }
            ],
        }

    monkeypatch.setattr(
        jd_strategy,
        "post_chat_completions_with_retries",
        invalid_recovery,
    )
    result = jd_strategy.build_strategy_with_openai(
        jd_text="Electrical facility maintenance",
        notice_text="Public institution hiring notice",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
        target_count_override=1,
        max_model_requests=2,
    )

    assert len(calls) == 1
    assert result["provider_generation_request_count"] == 1
    assert result["interview_questions"] == []
    assert result["error"] == "model_generation_failed: model_response_truncated"


@pytest.mark.parametrize(
    ("content", "expected_question"),
    [
        (
            "```json\n{\"interview_questions\":[{\"question\":\"펜스 복구 질문\"}]}\n```",
            "펜스 복구 질문",
        ),
        (
            "설명 뒤 JSON {\"questions\":[{\"question\":\"별칭 복구 질문\"}]} 끝",
            "별칭 복구 질문",
        ),
        (
            [{"type": "text", "text": '[{"question":"파트 복구 질문"}]'}],
            "파트 복구 질문",
        ),
    ],
)
def test_openrouter_strategy_decoder_recovers_safe_json_wrappers(
    content,
    expected_question: str,
) -> None:
    decoded = jd_strategy._decode_strategy_model_content(content)

    assert decoded["interview_questions"][0]["question"] == expected_question


def test_openrouter_auxiliary_generation_keeps_valid_candidates_after_one_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    lock = threading.Lock()
    monkeypatch.setenv("OPENAI_QUESTION_CANDIDATE_MULTIPLIER", "3")
    monkeypatch.setenv("OPENAI_QUESTION_VARIANT_ATTEMPTS", "3")

    def fake_chat(**kwargs):
        with lock:
            calls.append(kwargs)
            variant = len(calls)
        if variant == 1:
            raise httpx.ConnectError("upstream unavailable")
        content = {
            "interview_questions": [_candidate(slot=0, variant=variant)]
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(
        question_generation,
        "post_chat_completions_with_retries",
        fake_chat,
    )
    selected = question_generation._generate_questions_with_openai_from_ncs(
        ncs_matches=[],
        ncs_ksa=[],
        target_count=1,
        api_key_override="sk-or-v1-test",
        generation_provider="openrouter",
    )

    assert len(calls) == 3
    assert all(30 <= float(call["timeout_sec"]) <= 110 for call in calls)
    assert len(selected) == 1
    assert selected[0]["generation_provider"] == "openrouter_api"
    assert selected[0]["provider_generation_model"] == OPENROUTER_DEFAULT_MODEL
    assert selected[0]["provider_candidate_variant_count"] == 3
    assert selected[0]["provider_candidate_variant_received_count"] == 2
