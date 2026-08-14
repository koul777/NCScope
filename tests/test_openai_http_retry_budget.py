from __future__ import annotations

import httpx
import pytest

from app.services import openai_http


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, *, trust_env: bool, outcomes: list[object], calls: list[bool]) -> None:
        self._trust_env = trust_env
        self._outcomes = outcomes
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, *_args, **_kwargs):
        self._calls.append(self._trust_env)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, *_args, **_kwargs):
        self._calls.append(self._trust_env)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[object],
) -> list[bool]:
    calls: list[bool] = []

    def client_factory(**kwargs):
        return _Client(
            trust_env=kwargs["trust_env"],
            outcomes=outcomes,
            calls=calls,
        )

    monkeypatch.setattr(openai_http.httpx, "Client", client_factory)
    monkeypatch.setattr(openai_http, "_sleep_backoff", lambda _attempt: None)
    monkeypatch.setattr(openai_http, "_curl_fallback_enabled", lambda: True)
    return calls


def test_explicit_single_attempt_is_exactly_one_post_and_never_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_client(monkeypatch, [httpx.ConnectError("offline")])
    monkeypatch.setattr(
        openai_http,
        "_chat_with_curl",
        lambda **_kwargs: pytest.fail("bounded path must not use curl"),
    )

    with pytest.raises(RuntimeError):
        openai_http.post_chat_completions_with_retries(
            payload={"model": "test"},
            api_key="request-key",
            max_attempts=1,
        )

    assert calls == [True]


def test_retry_budget_counts_posts_and_alternates_proxy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_client(
        monkeypatch,
        [
            httpx.ConnectError("first"),
            httpx.ConnectError("second"),
            _Response(200, {"choices": []}),
        ],
    )

    result = openai_http.post_chat_completions_with_retries(
        payload={"model": "test"},
        api_key="request-key",
        max_attempts=3,
    )

    assert result == {"choices": []}
    assert calls == [True, False, True]


def test_nonretryable_response_does_not_consume_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_client(monkeypatch, [_Response(401)])

    with pytest.raises(RuntimeError, match="openai_http_401"):
        openai_http.post_chat_completions_with_retries(
            payload={"model": "test"},
            api_key="request-key",
            max_attempts=3,
        )

    assert calls == [True]


@pytest.mark.parametrize(
    ("transport_error", "expected_code"),
    [
        (httpx.ReadTimeout(""), "openai_request_timeout"),
        (httpx.ConnectError("secret endpoint detail"), "openai_network_unreachable"),
    ],
)
def test_exhausted_transport_failure_returns_safe_actionable_code(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: Exception,
    expected_code: str,
) -> None:
    _patch_client(monkeypatch, [transport_error])

    with pytest.raises(RuntimeError, match=f"^{expected_code}$"):
        openai_http.post_chat_completions_with_retries(
            payload={"model": "test"},
            api_key="request-key",
            max_attempts=1,
        )


def test_explicit_connectivity_budget_is_one_get_and_never_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_client(monkeypatch, [httpx.ConnectError("offline")])
    monkeypatch.setattr(
        openai_http,
        "_request_models_with_curl",
        lambda **_kwargs: pytest.fail("bounded path must not use curl"),
    )

    ok, message = openai_http.check_openai_connectivity_with_retries(
        "request-key",
        max_attempts=1,
    )

    assert ok is False
    assert message == "offline"
    assert calls == [True]


def test_connectivity_retry_budget_counts_gets_and_alternates_proxy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_client(
        monkeypatch,
        [
            httpx.ConnectError("first"),
            httpx.ConnectError("second"),
            _Response(200),
        ],
    )

    ok, message = openai_http.check_openai_connectivity_with_retries(
        "request-key",
        max_attempts=3,
    )

    assert ok is True
    assert message == ""
    assert calls == [True, False, True]
