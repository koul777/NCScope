from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import HTTPException

from app import main
from app.services import kordoc_parser
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
