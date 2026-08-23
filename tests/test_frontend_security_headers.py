from __future__ import annotations

from fastapi.testclient import TestClient

from app import main


def test_root_protects_request_scoped_api_key_ui_with_browser_security_headers() -> None:
    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    csp = response.headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_dynamic_generation_errors_are_never_cacheable() -> None:
    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json={})

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
