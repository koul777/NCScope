from __future__ import annotations

import pytest

import app.main as main
from app.services.provider_config import (
    GenerationCredentialError,
    OPENAI_DEFAULT_BASE_URL,
    provider_base_url,
    request_supported_generation_providers,
    resolve_generation_credential,
)


def test_public_provider_contract_is_openai_only() -> None:
    assert request_supported_generation_providers() == ("openai_api",)


def test_public_openapi_does_not_advertise_openrouter_credentials() -> None:
    schema_text = str(main.app.openapi()).lower()

    assert "openrouter_api_key" not in schema_text
    assert "generation_model" not in schema_text
    assert "server_ksa_fallback" not in schema_text
    assert "template_fallback" not in schema_text


def test_request_scoped_openai_key_is_required_even_when_server_env_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-key-must-not-be-used")

    provider, model, key = main._resolve_request_generation()

    assert provider == "openai_api"
    assert model
    assert key == ""


def test_openrouter_key_is_rejected_at_the_public_credential_boundary() -> None:
    with pytest.raises(GenerationCredentialError) as exc_info:
        resolve_generation_credential(
            generation_api_key="sk-or-not-accepted",
        )

    assert exc_info.value.code == "generation_provider_invalid"


def test_openai_key_can_only_be_sent_to_the_official_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unapproved.example/v1")

    assert provider_base_url("openai_api") == OPENAI_DEFAULT_BASE_URL


def test_missing_request_key_returns_safe_400() -> None:
    with pytest.raises(main.HTTPException) as exc_info:
        main._require_allowed_openai_key("", provider="openai_api")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "openai_api_key_required"
    assert "sk-server" not in str(exc_info.value.detail)


def test_public_request_cannot_override_the_role_pinned_authoring_model() -> None:
    with pytest.raises(main.HTTPException) as exc_info:
        main._resolve_request_generation(
            openai_api_key="sk-request-scoped-model-policy-test",
            generation_model="gpt-5.6-luna",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "generation_model_override_disabled"
    assert "gpt-5.6-luna" not in str(exc_info.value.detail)
