from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.main as main
from app.services import kordoc_parser


def _configure_matching_bridge_key(monkeypatch) -> None:
    private_key_bytes = b"k" * 32
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    public_key_bytes = private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )
    monkeypatch.setattr(
        kordoc_parser,
        "_KORDOC_BRIDGE_ED25519_PUBLIC_KEY_RAW",
        base64.urlsafe_b64encode(public_key_bytes).rstrip(b"=").decode("ascii"),
    )
    monkeypatch.setenv(
        "KORDOC_BRIDGE_ED25519_PRIVATE_KEY",
        base64.urlsafe_b64encode(private_key_bytes).rstrip(b"=").decode("ascii"),
    )


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
    assert payload["document_parsing"]["mode"] == "local_runtime"
    assert payload["document_parsing"]["ready"] is True


def test_health_is_degraded_only_when_mcp_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": False, "ksaAvailable": False},
    )
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_health_requires_stateless_document_contract_on_vercel(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "https://example.vercel.app/api/kordoc-parse")
    monkeypatch.setenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", "configured")
    monkeypatch.delenv("REVIEW_SESSION_SIGNING_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    payload = response.json()
    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["document_parsing"] == {
        "mode": "serverless_bridge",
        "ready": False,
        "bridge_configured": True,
        "bridge_auth_configured": False,
        "stateless_review_configured": False,
        "request_budget_sec": 285,
    }


def test_health_accepts_only_valid_serverless_document_secrets(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "https://example.vercel.app/api/kordoc-parse")
    _configure_matching_bridge_key(monkeypatch)
    monkeypatch.setenv("REVIEW_SESSION_SIGNING_KEY", "r" * 32)
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["document_parsing"] == {
        "mode": "serverless_bridge",
        "ready": True,
        "bridge_configured": True,
        "bridge_auth_configured": True,
        "stateless_review_configured": True,
        "request_budget_sec": 285,
    }


def test_health_rejects_private_key_that_does_not_match_bridge(monkeypatch) -> None:
    private_key = base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode("ascii")
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "https://example.vercel.app/api/kordoc-parse")
    monkeypatch.setenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", private_key)
    monkeypatch.setenv("REVIEW_SESSION_SIGNING_KEY", "r" * 32)
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["document_parsing"]["bridge_auth_configured"] is False


def test_serverless_parse_review_fails_closed_for_weak_signing_key(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "https://example.vercel.app/api/kordoc-parse")
    _configure_matching_bridge_key(monkeypatch)
    monkeypatch.setenv("REVIEW_SESSION_SIGNING_KEY", "weak")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job.txt", b"detail: planning", "text/plain")},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "document_parsing_not_configured",
        "retryable": False,
    }
