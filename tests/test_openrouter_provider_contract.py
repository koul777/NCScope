from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.provider_config import (
    GenerationCredentialError,
    OPENAI_DEFAULT_BASE_URL,
    provider_base_url,
    request_supported_generation_providers,
    resolve_generation_credential,
)


OPENROUTER_KEY = "sk-or-public-provider-is-disabled"


def test_public_generation_provider_list_excludes_openrouter() -> None:
    assert request_supported_generation_providers() == ("openai_api",)


def test_openrouter_key_is_rejected_before_generation() -> None:
    with pytest.raises(GenerationCredentialError) as exc_info:
        resolve_generation_credential(generation_api_key=OPENROUTER_KEY)

    assert exc_info.value.code == "generation_provider_invalid"
    assert OPENROUTER_KEY not in str(exc_info.value)


def test_openrouter_status_probe_is_rejected_as_unsupported() -> None:
    with TestClient(main.app) as client:
        response = client.get(
            "/api/generation-provider/status",
            params={"provider": "openrouter_api"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "generation_provider_unsupported"
    assert "openrouter_api" not in response.json()["detail"].get("supported", [])


def test_json_generation_rejects_openrouter_without_reflecting_key() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "generation_provider": "openrouter_api",
                "generation_api_key": OPENROUTER_KEY,
                "notice_text": "직무 내용",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "generation_provider_invalid"
    assert OPENROUTER_KEY not in response.text


def test_openai_byok_host_cannot_be_redirected_to_compatible_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://third-party.example/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", OPENROUTER_KEY)

    assert provider_base_url("openai_api") == OPENAI_DEFAULT_BASE_URL
    provider, _model, key = main._resolve_request_generation()
    assert provider == "openai_api"
    assert key == ""
