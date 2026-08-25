from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app import main
from app.services import kordoc_parser, ncs_mcp_client
from app.services.request_budget import (
    RequestBudgetExceeded,
    remaining_request_budget_sec,
    use_request_budget,
)


def test_document_parses_run_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2)

    def blocking_parse(_data: bytes, filename: str, _label: str) -> dict:
        barrier.wait(timeout=2)
        return {"markdown": filename}

    monkeypatch.setattr(main, "_parse_upload_document", blocking_parse)

    async def run() -> list[dict]:
        return await asyncio.gather(
            main._parse_upload_document_off_loop(b"a", "a.txt", "jd_file"),
            main._parse_upload_document_off_loop(b"b", "b.txt", "jd_file"),
        )

    assert asyncio.run(run()) == [{"markdown": "a.txt"}, {"markdown": "b.txt"}]


def test_document_parse_budget_applies_when_rate_limit_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float | None] = []

    async def downstream(_scope, _receive, _send) -> None:
        observed.append(remaining_request_budget_sec())

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    middleware = main.ExpensiveRequestLimitMiddleware(downstream)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/api/jd/parse-review"},
            receive,
            send,
        )
    )

    assert observed and observed[0] is not None
    assert 0 < float(observed[0]) <= 285


def test_generation_budget_is_a_hard_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    async def downstream(_scope, _receive, _send) -> None:
        try:
            await asyncio.sleep(1.3)
        finally:
            cancelled.set()

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(main, "_generation_request_budget_sec", lambda: 1.0)
    middleware = main.ExpensiveRequestLimitMiddleware(downstream)
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def run() -> float:
        started = time.monotonic()
        await middleware(
            {"type": "http", "method": "POST", "path": "/api/questions/generate"},
            receive,
            send,
        )
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    response_start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = next(message for message in sent if message["type"] == "http.response.body")

    assert elapsed < 1.2
    assert cancelled.is_set()
    assert response_start["status"] == 504
    assert json.loads(response_body["body"])["detail"] == {
        "code": "generation_request_deadline_exhausted",
        "retryable": True,
    }


def test_timed_out_generation_keeps_request_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    active_lock = threading.Lock()
    active = 0
    peak = 0

    def blocking_worker() -> None:
        nonlocal active, peak
        with active_lock:
            active += 1
            peak = max(peak, active)
        started.set()
        try:
            release.wait(timeout=3)
        finally:
            with active_lock:
                active -= 1

    async def downstream(_scope, _receive, send) -> None:
        await main._to_thread_with_request_lease(blocking_worker)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_REQUESTS", "100")
    monkeypatch.setattr(main, "_generation_request_budget_sec", lambda: 0.1)
    middleware = main.ExpensiveRequestLimitMiddleware(downstream)
    middleware._generation_slots = threading.BoundedSemaphore(1)

    async def invoke() -> list[dict]:
        sent: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/questions/generate",
                "client": ("127.0.0.1", 1234),
            },
            receive,
            send,
        )
        return sent

    async def run() -> tuple[list[dict], list[dict], list[dict]]:
        first = await invoke()
        assert started.is_set()
        second = await invoke()
        release.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            with active_lock:
                if active == 0:
                    break
        third = await invoke()
        return first, second, third

    try:
        first, second, third = asyncio.run(run())
    finally:
        release.set()

    assert next(item for item in first if item["type"] == "http.response.start")["status"] == 504
    assert next(item for item in second if item["type"] == "http.response.start")["status"] == 429
    assert next(item for item in third if item["type"] == "http.response.start")["status"] == 200
    assert peak == 1


def test_request_lease_releases_when_thread_submission_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False) is True
    lease = main._RequestConcurrencyLease(semaphore.release)

    async def failed_to_thread(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(main.asyncio, "to_thread", failed_to_thread)

    async def run() -> None:
        token = main._REQUEST_CONCURRENCY_LEASE.set(lease)
        try:
            with pytest.raises(RuntimeError, match="executor unavailable"):
                await main._to_thread_with_request_lease(lambda: None)
        finally:
            main._REQUEST_CONCURRENCY_LEASE.reset(token)
            lease.release()

    asyncio.run(run())
    assert semaphore.acquire(blocking=False) is True
    semaphore.release()


def test_request_lease_releases_when_worker_task_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False) is True
    lease = main._RequestConcurrencyLease(semaphore.release)

    def failed_create_task(coroutine):
        coroutine.close()
        raise RuntimeError("task creation unavailable")

    monkeypatch.setattr(main.asyncio, "create_task", failed_create_task)

    async def run() -> None:
        token = main._REQUEST_CONCURRENCY_LEASE.set(lease)
        try:
            with pytest.raises(RuntimeError, match="task creation unavailable"):
                await main._to_thread_with_request_lease(lambda: None)
        finally:
            main._REQUEST_CONCURRENCY_LEASE.reset(token)
            lease.release()

    asyncio.run(run())
    assert semaphore.acquire(blocking=False) is True
    semaphore.release()


def test_nested_mcp_workers_retain_outer_request_slot_until_they_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCS_MCP_KSA_CONCURRENCY", "2")
    slow_started = threading.Event()
    release_slow = threading.Event()
    slow_finished = threading.Event()
    semaphore = threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False) is True
    lease = main._RequestConcurrencyLease(semaphore.release)

    def mixed_call(_name: str, arguments: dict) -> dict:
        if arguments["unit_code"] == "U0":
            assert slow_started.wait(timeout=1.0)
            raise ncs_mcp_client.NcsMcpError("primary fast failure")
        slow_started.set()
        try:
            assert release_slow.wait(timeout=2.0)
        finally:
            slow_finished.set()
        return {}

    monkeypatch.setattr(ncs_mcp_client, "_call_tool", mixed_call)

    async def run() -> None:
        token = main._REQUEST_CONCURRENCY_LEASE.set(lease)
        try:
            with pytest.raises(ncs_mcp_client.NcsMcpError, match="primary fast failure"):
                await main._to_thread_with_request_lease(
                    ncs_mcp_client.get_ksa_by_units,
                    [{"ncsClCd": "U0"}, {"ncsClCd": "U1"}],
                    1,
                )
        finally:
            main._REQUEST_CONCURRENCY_LEASE.reset(token)
            lease.release()

    try:
        asyncio.run(run())
        assert semaphore.acquire(blocking=False) is False
    finally:
        release_slow.set()
        assert slow_finished.wait(timeout=1.0)
    assert semaphore.acquire(timeout=1.0) is True
    semaphore.release()


def test_kordoc_timeout_is_clamped_to_shared_document_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}

    def local_parse(
        _data: bytes,
        *,
        filename: str,
        ocr: bool,
        timeout: float,
    ) -> dict:
        _ = filename, ocr
        observed["timeout"] = timeout
        return {"success": True, "markdown": "ok"}

    monkeypatch.setattr(kordoc_parser, "_parse_with_local_kordoc", local_parse)
    monkeypatch.setattr(kordoc_parser, "_kordoc_timeout_seconds", lambda: 120)

    with use_request_budget(10):
        result = kordoc_parser.parse_with_kordoc(b"document", filename="job.pdf")

    assert result["markdown"] == "ok"
    assert 4.0 < observed["timeout"] <= 5.0


def test_document_parse_deadline_maps_to_controlled_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exhausted(*_args, **_kwargs):
        raise RequestBudgetExceeded("deadline")

    monkeypatch.setattr(main, "_parse_upload_document", exhausted)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            main._parse_upload_document_off_loop(b"document", "job.pdf", "jd_file")
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {
        "code": "document_parse_deadline_exhausted",
        "retryable": True,
    }


def test_document_parse_wall_clock_deadline_maps_to_controlled_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_parse(*_args, **_kwargs):
        time.sleep(0.4)
        return {"markdown": "too late"}

    monkeypatch.setattr(main, "_parse_upload_document", slow_parse)
    async def run() -> tuple[float, HTTPException]:
        started = time.monotonic()
        with use_request_budget(1.2):
            with pytest.raises(HTTPException) as exc_info:
                await main._parse_upload_document_off_loop(
                    b"document",
                    "job.pdf",
                    "jd_file",
                )
        return time.monotonic() - started, exc_info.value

    elapsed, error = asyncio.run(run())

    assert elapsed < 0.35
    assert error.status_code == 504


def test_timed_out_document_worker_keeps_capacity_slot_until_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    started = threading.Event()
    monkeypatch.setattr(main, "_DOCUMENT_WORK_SLOTS", threading.BoundedSemaphore(1))

    def blocked_parse(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return {"markdown": "done"}

    monkeypatch.setattr(main, "_parse_upload_document", blocked_parse)

    async def run() -> None:
        with use_request_budget(1.2):
            with pytest.raises(HTTPException) as first_error:
                await main._parse_upload_document_off_loop(
                    b"document",
                    "first.pdf",
                    "jd_file",
                )
        assert first_error.value.status_code == 504
        assert started.is_set()

        with pytest.raises(HTTPException) as capacity_error:
            await main._parse_upload_document_off_loop(
                b"document",
                "second.pdf",
                "jd_file",
            )
        assert capacity_error.value.status_code == 429
        release.set()
        await asyncio.sleep(0.05)

        result = await main._parse_upload_document_off_loop(
            b"document",
            "third.pdf",
            "jd_file",
        )
        assert result == {"markdown": "done"}

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_cancelled_queued_document_worker_releases_reserved_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_executor = threading.Event()
    executor_started = threading.Event()
    document_function_called = threading.Event()
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(main, "_DOCUMENT_WORK_SLOTS", semaphore)

    def occupy_executor() -> None:
        executor_started.set()
        release_executor.wait(timeout=3)

    def queued_document_function() -> dict[str, str]:
        document_function_called.set()
        return {"markdown": "must not run after cancellation"}

    async def run() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = asyncio.create_task(asyncio.to_thread(occupy_executor))
        while not executor_started.is_set():
            await asyncio.sleep(0.005)
        try:
            with use_request_budget(1.2):
                with pytest.raises(HTTPException) as exc_info:
                    await main._run_document_work_off_loop(
                        queued_document_function
                    )
            assert exc_info.value.status_code == 504
            assert semaphore.acquire(blocking=False) is True
            semaphore.release()
        finally:
            release_executor.set()
            await blocker
            await asyncio.sleep(0.05)

    asyncio.run(run())
    assert document_function_called.is_set() is False


def test_ksa_fetch_helper_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_fetch(**_kwargs):
        time.sleep(0.2)
        return [{"ncsClCd": "0101010101_20v1", "factorName": "factor"}]

    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", slow_fetch)

    async def run() -> tuple[float, list[dict]]:
        started = time.monotonic()
        fetch_task = asyncio.create_task(
            main._fetch_ncs_ksa_or_502_off_loop([], 1, 1)
        )
        await asyncio.sleep(0.02)
        heartbeat_elapsed = time.monotonic() - started
        return heartbeat_elapsed, await fetch_task

    heartbeat_elapsed, rows = asyncio.run(run())

    assert heartbeat_elapsed < 0.1
    assert rows[0]["factorName"] == "factor"


def test_parse_review_rejects_response_above_serverless_safe_limit() -> None:
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("large.txt", b"\x00" * (800 * 1024), "text/plain")},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "parsed_document_response_too_large"
