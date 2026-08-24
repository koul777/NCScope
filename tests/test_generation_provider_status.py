from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main


def test_status_advertises_openai_request_key_without_verifying_a_secret(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-key-must-not-be-read")
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "openrouter_api")

    def unexpected_verification(_api_key: str):
        raise AssertionError("status must not inspect or verify a credential")

    monkeypatch.setattr(main, "_verify_institution_openai_api", unexpected_verification)
    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai_api"
    assert payload["supported_providers"] == ["openai_api"]
    assert payload["status"] == "key_required"
    assert payload["available"] is True
    assert payload["authenticated"] is False
    assert payload["credential_configured"] is False
    assert payload["credential_managed_by"] == "request"
    assert payload["requires_request_api_key"] is True
    assert payload["supports_custom_model"] is False
    assert payload["model_orchestration"] == {
        "policy": "role-based-openai-models-diversity-v2",
        "ncs_candidate_rerank": "gpt-5.6-luna",
        "question_authoring": "gpt-5.6-terra",
        "quality_review": "gpt-5.6-sol",
        "quality_regeneration": "gpt-5.6-sol",
    }
    assert "sk-server" not in response.text


def test_status_rejects_openrouter_provider_even_when_server_key_exists(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_ALLOW_SERVER_KEY", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-server-key")

    with TestClient(main.app) as client:
        response = client.get("/api/generation-provider/status?provider=openrouter_api")

    assert response.status_code == 400
    assert "sk-or-server-key" not in response.text


def test_status_rejects_query_credentials(monkeypatch) -> None:
    secret = "sk-query-secret"
    with TestClient(main.app) as client:
        response = client.get(
            f"/api/generation-provider/status?openai_api_key={secret}"
        )

    assert response.status_code == 400
    assert secret not in response.text


def test_health_depends_on_mcp_and_reports_openai_byok(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-key-must-not-be-read")
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["keys"]["openai"] is False
    assert payload["keys"]["openai_institution_managed"] is False
    assert payload["keys"]["openai_request_scoped"] is True
    assert payload["question_generation"]["provider"] == "openai_api"
    assert payload["question_generation"]["requires_request_api_key"] is True


def test_health_is_degraded_only_when_mcp_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": False, "ksaAvailable": False},
    )
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
