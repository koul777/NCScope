from starlette.requests import Request

import pytest
from fastapi import HTTPException

from app.main import (
    _require_local_codex_request,
    _require_local_subscription_cli_request,
)


def _request_from(host: str, headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/questions/generate-from-text",
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in (headers or {}).items()
            ],
            "client": (host, 54321),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "testclient"])
def test_legacy_codex_guard_accepts_loopback_clients(host: str) -> None:
    _require_local_codex_request(_request_from(host))


def test_legacy_codex_guard_rejects_remote_client() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_local_codex_request(_request_from("203.0.113.7"))

    assert exc_info.value.status_code == 403


def test_legacy_codex_guard_rejects_loopback_peer_with_remote_x_forwarded_for() -> None:
    request = _request_from(
        "127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.7"},
    )

    with pytest.raises(HTTPException) as exc_info:
        _require_local_codex_request(request)

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    ("header_name", "header_value"),
    [
        ("Forwarded", "for=203.0.113.7;proto=https"),
        ("X-Real-IP", "203.0.113.7"),
        ("X-Forwarded-Host", "public.example"),
        ("X-Forwarded-Proto", "https"),
        ("Client-IP", "203.0.113.7"),
    ],
)
def test_codex_cli_rejects_other_proxy_headers_from_loopback_peer(
    header_name: str,
    header_value: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_local_codex_request(
            _request_from("::1", headers={header_name: header_value})
        )

    assert exc_info.value.status_code == 403


def test_request_scoped_openai_api_is_not_subject_to_legacy_local_cli_guard() -> None:
    _require_local_subscription_cli_request(
        _request_from(
            "203.0.113.7",
            headers={"X-Forwarded-For": "198.51.100.9"},
        ),
        "openai_api",
    )
