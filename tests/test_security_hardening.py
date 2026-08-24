from __future__ import annotations

import asyncio
import copy
import time
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


def _stateless_signed_review(monkeypatch) -> tuple[dict, bytes, str]:
    signing_key = "test-only-review-session-signing-key"
    text = "signed stateless review session text"
    upload_bytes = text.encode("utf-8")
    structured = {"document": {"markdown": text}, "fields": {}}

    monkeypatch.setenv("REVIEW_SESSION_SIGNING_KEY", signing_key)
    monkeypatch.setattr(main, "create_review_session", lambda _payload: None)
    monkeypatch.setattr(main, "prune_review_sessions", lambda **_kwargs: None)
    monkeypatch.setattr(main, "get_review_session", lambda *_args, **_kwargs: None)

    public = main._create_review_session(upload_bytes, structured, "folder/job.txt")
    main._REVIEW_SESSION_BY_ID.pop(public["id"], None)
    review = {
        **structured,
        "review_confirmed": True,
        "review_session_id": public["id"],
        "review_session": public,
    }
    return review, upload_bytes, signing_key


def test_signed_review_session_survives_serverless_instance_change(monkeypatch) -> None:
    review, upload_bytes, signing_key = _stateless_signed_review(monkeypatch)

    public = review["review_session"]
    assert public["filename"] == "job.txt"
    assert public["token"].startswith("v1.")
    assert signing_key not in str(public)

    validated = main._validate_review_session(review, upload_bytes, "folder/job.txt")

    assert validated["markdown"] == review["document"]["markdown"]
    assert validated["document_sha256"] == main._sha256_bytes(upload_bytes)


def test_signed_review_session_rejects_public_metadata_and_token_tampering(monkeypatch) -> None:
    review, upload_bytes, _ = _stateless_signed_review(monkeypatch)
    original = review["review_session"]
    mutations = {
        "document_sha256": "0" * 64,
        "markdown_sha256": "f" * 64,
        "filename": "other.txt",
        "created_at": original["created_at"] + 1,
        "expires_at": original["expires_at"] + 1,
        "token": original["token"][:-1] + ("A" if original["token"][-1] != "A" else "B"),
    }

    for field, value in mutations.items():
        tampered = copy.deepcopy(review)
        tampered["review_session"][field] = value
        with pytest.raises(HTTPException) as exc_info:
            main._validate_review_session(tampered, upload_bytes, "folder/job.txt")
        assert exc_info.value.status_code == 409, field


def test_signed_review_session_rejects_document_markdown_and_filename_mismatch(monkeypatch) -> None:
    review, upload_bytes, _ = _stateless_signed_review(monkeypatch)

    with pytest.raises(HTTPException) as upload_error:
        main._validate_review_session(review, upload_bytes + b"!", "folder/job.txt")
    assert upload_error.value.status_code == 409

    markdown_tampered = copy.deepcopy(review)
    markdown_tampered["document"]["markdown"] += "!"
    with pytest.raises(HTTPException) as markdown_error:
        main._validate_review_session(markdown_tampered, upload_bytes, "folder/job.txt")
    assert markdown_error.value.status_code == 400

    markdown_missing = copy.deepcopy(review)
    del markdown_missing["document"]["markdown"]
    with pytest.raises(HTTPException) as missing_error:
        main._validate_review_session(markdown_missing, upload_bytes, "folder/job.txt")
    assert missing_error.value.status_code == 400

    with pytest.raises(HTTPException) as filename_error:
        main._validate_review_session(review, upload_bytes, "renamed.txt")
    assert filename_error.value.status_code == 409


@pytest.mark.parametrize("created_at_offset", [61, -(main._REVIEW_SESSION_TTL_SEC + 1)])
def test_signed_review_session_rejects_future_or_expired_timestamp(
    monkeypatch,
    created_at_offset: int,
) -> None:
    review, upload_bytes, signing_key = _stateless_signed_review(monkeypatch)
    created_at = time.time() + created_at_offset
    public = review["review_session"]
    public["created_at"] = created_at
    public["expires_at"] = created_at + main._REVIEW_SESSION_TTL_SEC
    public["token"] = main._sign_review_session(public, signing_key.encode("utf-8"))

    with pytest.raises(HTTPException) as exc_info:
        main._validate_review_session(review, upload_bytes, "folder/job.txt")

    assert exc_info.value.status_code == 409


def test_signed_review_session_rejects_unsafe_filename_even_with_valid_signature(monkeypatch) -> None:
    review, upload_bytes, signing_key = _stateless_signed_review(monkeypatch)
    public = review["review_session"]
    public["filename"] = "../job.txt"
    public["token"] = main._sign_review_session(public, signing_key.encode("utf-8"))

    with pytest.raises(HTTPException) as exc_info:
        main._validate_review_session(review, upload_bytes, "folder/job.txt")

    assert exc_info.value.status_code == 409


@pytest.mark.parametrize(
    "path",
    [
        "/api/ncs/units/options",
        "/api/alio/attachments",
        "/api/alio/attachment",
    ],
)
def test_expensive_request_limit_returns_429_only_after_configured_limit(
    monkeypatch,
    path: str,
) -> None:
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
            "path": path,
            "client": ("198.51.100.10", 1234),
            "headers": [],
        }
        await middleware(scope, receive, send)
        await middleware(scope, receive, send)

    asyncio.run(invoke())

    assert statuses == [200, 429]


@pytest.mark.parametrize(
    "path",
    [
        "/api/alio/attachments",
        "/api/alio/attachment",
    ],
)
def test_expensive_request_limit_covers_alio_download_paths(path: str) -> None:
    assert main.ExpensiveRequestLimitMiddleware._is_expensive(path)
