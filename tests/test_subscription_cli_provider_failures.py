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
    assert response.json()["detail"] == (
        "generation_provider must be 'openai_api'; personal Codex and "
        "Claude Code subscription logins are disabled for institutional use"
    )


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
    assert response.json()["detail"] == (
        "generation_provider must be 'openai_api'; personal Codex and "
        "Claude Code subscription logins are disabled for institutional use"
    )
    assert REQUEST_KEY not in response.text
