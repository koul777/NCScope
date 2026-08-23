from __future__ import annotations

import asyncio

import pytest

from app.services import ncs_mcp_client, openai_http
from app.services.request_budget import (
    clamp_timeout_to_request_budget,
    remaining_request_budget_sec,
    use_request_budget,
)


def test_request_budget_clamps_stage_timeout_and_resets_context() -> None:
    assert remaining_request_budget_sec() is None


def test_request_budget_propagates_into_asyncio_to_thread() -> None:
    async def read_budget_inside_worker_thread() -> float | None:
        return await asyncio.to_thread(remaining_request_budget_sec)

    with use_request_budget(10):
        outer_remaining = remaining_request_budget_sec()
        worker_remaining = asyncio.run(read_budget_inside_worker_thread())

    assert outer_remaining is not None
    assert worker_remaining is not None
    assert 0.0 < worker_remaining <= outer_remaining
    assert remaining_request_budget_sec() is None

    with use_request_budget(10):
        remaining = remaining_request_budget_sec()
        assert remaining is not None and 9.0 < remaining <= 10.0
        assert 7.0 < clamp_timeout_to_request_budget(30, reserve_sec=2) <= 8.0

    assert remaining_request_budget_sec() is None


def test_openrouter_does_not_start_after_request_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_http.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("provider client must not start"),
    )

    with use_request_budget(1):
        with pytest.raises(RuntimeError, match="^openrouter_request_timeout$"):
            openai_http.post_chat_completions_with_retries(
                payload={"model": "stealth/ox-alpha", "reasoning_effort": "max"},
                api_key="sk-or-test-request-budget",
                timeout_sec=8,
                max_attempts=1,
                provider="openrouter_api",
            )


def test_ncs_mcp_does_not_start_after_request_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCS_MCP_URL", "https://ncs.example/api/mcp")
    monkeypatch.setattr(
        ncs_mcp_client.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("NCS client must not start"),
    )

    with use_request_budget(1):
        with pytest.raises(ncs_mcp_client.NcsMcpError, match="deadline exhausted"):
            ncs_mcp_client._rpc("tools/list")
