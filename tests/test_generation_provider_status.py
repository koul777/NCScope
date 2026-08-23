from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main


SERVER_KEY = "sk-server-key-must-be-ignored-by-status"
QUERY_KEY = "sk-query-key-must-not-be-consumed"


def test_status_advertises_request_scoped_api_key_without_verifying_server_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "codex_cli")

    def unexpected_verification(_api_key: str) -> tuple[bool, str]:
        raise AssertionError("status must not inspect or verify a credential")

    monkeypatch.setattr(
        main,
        "_verify_institution_openai_api",
        unexpected_verification,
    )

    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openrouter_api"
    assert payload["configured_default"] == "openrouter_api"
    assert payload["auth_mode"] == "request_scoped_api_key"
    assert payload["status"] == "key_required"
    assert payload["available"] is True
    assert payload["authenticated"] is False
    assert payload["requires_request_api_key"] is True
    assert payload["local_only"] is False
    assert payload["login_command"] == ""
    assert isinstance(payload["message"], str) and payload["message"]
    assert SERVER_KEY not in response.text


def test_status_is_independent_of_server_openai_environment(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with TestClient(main.app) as client:
        without_server_key = client.get("/api/generation-provider/status")

    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)
    with TestClient(main.app) as client:
        with_server_key = client.get("/api/generation-provider/status")

    assert without_server_key.status_code == 200
    assert with_server_key.status_code == 200
    for response in (without_server_key, with_server_key):
        payload = response.json()
        assert payload["auth_mode"] == "request_scoped_api_key"
        assert payload["status"] == "key_required"
        assert payload["authenticated"] is False
        assert payload["requires_request_api_key"] is True
        assert SERVER_KEY not in response.text


def test_status_advertises_configured_openrouter_server_environment_without_echoing_it(
    monkeypatch,
) -> None:
    server_key = "sk-or-v1-server-key-must-not-be-echoed"
    monkeypatch.setenv("OPENROUTER_API_KEY", server_key)
    monkeypatch.setenv("OPENROUTER_ALLOW_SERVER_KEY", "true")

    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status?provider=openrouter")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openrouter_api"
    assert payload["auth_mode"] == "server_env_api_key"
    assert payload["credential_managed_by"] == "server_env"
    assert payload["requires_request_api_key"] is False
    assert payload["credential_configured"] is True
    assert payload["status"] == "configured"
    assert payload["available"] is True
    assert payload["authenticated"] is False
    assert payload["server_key_state"] == "configured"
    assert payload["server_key_enabled"] is True
    assert payload["generation_limits"] == {
        "max_main_questions_per_request": 5,
        "max_follow_up_questions_per_main": 5,
        "max_ncs_details_per_request": 1,
        "max_interview_methods_per_request": 1,
        "request_budget_sec": 285,
    }
    assert "Vercel 환경변수의 OpenRouter API 키" in payload["message"]
    assert server_key not in response.text


def test_status_reports_invalid_opted_in_openrouter_server_key_without_echoing_it(
    monkeypatch,
) -> None:
    server_key = "not-an-openrouter-key"
    monkeypatch.setenv("OPENROUTER_API_KEY", server_key)
    monkeypatch.setenv("OPENROUTER_ALLOW_SERVER_KEY", "true")

    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status?provider=openrouter")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "key_invalid"
    assert payload["available"] is False
    assert payload["credential_configured"] is False
    assert payload["server_key_state"] == "invalid"
    assert server_key not in response.text


def test_status_does_not_consume_or_echo_query_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/generation-provider/status",
            params={"openai_api_key": QUERY_KEY},
        )

    assert response.status_code == 400
    assert "generation request body" in response.text
    assert QUERY_KEY not in response.text


def test_health_uses_only_mcp_readiness_and_advertises_request_scoped_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "claude_code")
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    def unexpected_verification(_api_key: str) -> tuple[bool, str]:
        raise AssertionError("health must not verify a request-scoped credential")

    monkeypatch.setattr(
        main,
        "_verify_institution_openai_api",
        unexpected_verification,
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["keys"]["openai"] is False
    assert payload["keys"]["openai_institution_managed"] is False
    assert payload["keys"]["openai_request_scoped"] is True
    assert payload["keys"]["openai_authenticated"] is False
    descriptor = payload["question_generation"]
    assert descriptor["provider"] == "openrouter_api"
    assert descriptor["auth_mode"] == "request_scoped_api_key"
    assert descriptor["requires_request_api_key"] is True
    assert descriptor["local_only"] is False
    assert SERVER_KEY not in response.text


def test_health_is_degraded_only_when_mcp_is_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": False, "ksaAvailable": True},
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["keys"]["openai_request_scoped"] is True
    assert SERVER_KEY not in response.text
