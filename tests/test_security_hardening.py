from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import external_api


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.example/collect",
        "//attacker.example/collect",
        "../outside",
        "Ncs1info/ncsinfo.do?next=https://attacker.example",
    ],
)
def test_fetch_ncs_rejects_non_relative_or_out_of_base_paths(monkeypatch, path: str) -> None:
    monkeypatch.setenv("NCS_SERVICE_KEY", "test-service-key")
    client = MagicMock()
    monkeypatch.setattr(external_api.httpx, "Client", client)

    with pytest.raises(ValueError):
        external_api.fetch_ncs(path=path, query={})

    client.assert_not_called()


def test_fetch_ncs_keeps_relative_request_on_configured_host(monkeypatch) -> None:
    monkeypatch.setenv("NCS_SERVICE_KEY", "test-service-key")
    response = MagicMock(status_code=200, headers={"content-type": "application/json"}, text="{}")
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    monkeypatch.setattr(external_api.httpx, "Client", lambda **_kwargs: client)

    result = external_api.fetch_ncs(path="Ncs1info/ncsinfo.do", query={"pageNo": 1})

    requested_url, = client.__enter__.return_value.get.call_args.args
    requested_params = client.__enter__.return_value.get.call_args.kwargs["params"]
    assert requested_url == "https://www.ncs.go.kr/api/Ncs1info/ncsinfo.do"
    assert requested_params == {"pageNo": 1, "serviceKey": "test-service-key"}
    assert result["status_code"] == 200


def test_legacy_ncs_proxy_requires_admin_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_LEGACY_NCS_API", "true")
    monkeypatch.setenv("ENABLE_ADMIN_ENDPOINTS", "false")

    with pytest.raises(HTTPException) as exc_info:
        main.ncs_proxy(path="Ncs1info/ncsinfo.do")

    assert exc_info.value.status_code == 403


def test_review_session_can_be_loaded_after_local_cache_is_cleared() -> None:
    main.init_db()
    text = "persistent review session text"
    structured = {"document": {"markdown": text}, "fields": {}}
    session = main._create_review_session(text.encode("utf-8"), structured, "jd.txt")
    main._REVIEW_SESSION_BY_ID.pop(session["id"], None)

    payload = {
        **structured,
        "review_confirmed": True,
        "review_session_id": session["id"],
        "review_session": session,
    }
    validated = main._validate_review_session(payload, text.encode("utf-8"))

    assert validated["markdown"] == text


def test_expensive_request_limit_returns_429_only_after_configured_limit(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", "60")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_WINDOW", "1")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_REQUESTS_PER_WINDOW", "1")

    statuses: list[int] = []

    async def downstream(scope, receive, send):
        del receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = main.ExpensiveRequestLimitMiddleware(downstream)

    async def invoke() -> None:
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/ncs/units/options",
            "client": ("198.51.100.10", 1234),
            "headers": [],
        }
        await middleware(scope, receive, send)
        await middleware(scope, receive, send)

    asyncio.run(invoke())

    assert statuses == [200, 429]
