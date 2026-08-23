from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main


REQUEST_KEY = "sk-request-provider-rejection-secret"


@pytest.mark.parametrize("provider", ["codex", "codex_cli", "claude", "claude_code"])
def test_status_rejects_personal_subscription_cli_providers(provider: str) -> None:
    with TestClient(main.app) as client:
        response = client.get(
            "/api/generation-provider/status",
            params={"provider": provider},
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "openai_api" in detail
    assert "openrouter_api" in detail
    assert "personal Codex and Claude Code subscription logins are disabled" in detail


@pytest.mark.parametrize("provider", ["codex", "codex_cli", "claude", "claude_code"])
def test_generation_rejects_personal_subscription_cli_providers_before_runtime(
    monkeypatch,
    provider: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-key-must-be-ignored")

    def unexpected_builder(**_kwargs):
        raise AssertionError("a rejected personal provider must not reach generation")

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", unexpected_builder)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "generation_provider": provider,
                "openai_api_key": REQUEST_KEY,
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "generation_provider_invalid"
    assert detail["provider"] == provider
    assert detail["retryable"] is False
    assert REQUEST_KEY not in response.text
    assert REQUEST_KEY not in response.text
