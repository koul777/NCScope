from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from scripts import verify_generation_latency as latency


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@pytest.mark.parametrize("question_count", [1, 2, 5, 6, 15, 20])
def test_payload_has_exact_count_without_any_api_key(question_count: int) -> None:
    payload = latency.build_generation_payload(question_count)

    items = payload["question_plan"]["items"]
    assert sum(row["main_count"] for row in items) == question_count
    assert len(items) == 1
    assert items[0]["main_count"] == question_count
    assert len(payload["selected_ncs"]) == len(items)
    assert payload["interview_methods"] == ["경험면접"]
    serialized = json.dumps(payload).lower()
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_request_uses_public_json_endpoint_and_reports_elapsed() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(200, {"generated_questions_total_count": 15, "strategy": {}})

    ticks = iter([10.0, 12.5])
    result = latency.request_generation(
        base_url="https://example.test/",
        question_count=15,
        follow_up_count=3,
        timeout=125.0,
        opener=opener,
        clock=lambda: next(ticks),
    )

    assert captured["url"] == "https://example.test/api/questions/generate-from-text"
    assert captured["timeout"] == 125.0
    assert captured["headers"]["Content-type"] == "application/json; charset=utf-8"
    assert "Authorization" not in captured["headers"]
    assert "generation_api_key" not in captured["payload"]
    assert result.status == 200
    assert result.elapsed_sec == 2.5


def test_summary_keeps_only_operational_metadata() -> None:
    result = latency.HttpResult(
        status=200,
        elapsed_sec=58.1239,
        body={
            "generated_questions_total_count": 20,
            "strategy": {
                "provider_generation_request_count": 2,
                "generation_batching": {
                    "batch_count": 1,
                    "batch_size_limit": 20,
                    "max_concurrency": 1,
                },
                "interview_questions": [{"question": "출력되면 안 되는 질문"}],
            },
        },
    )

    summary = latency.summarize_result(
        question_count=20,
        result=result,
        latency_budget_sec=115.0,
    )

    assert summary == {
        "case": "questions_20",
        "expected_outcome": "success",
        "requested_count": 20,
        "http_status": 200,
        "elapsed_sec": 58.124,
        "latency_budget_sec": 115.0,
        "returned_count": 20,
        "provider_request_count": 2,
        "generation_batch_count": 1,
        "generation_batch_size_limit": 20,
        "generation_batch_concurrency": 1,
        "within_latency_budget": True,
        "passed": True,
    }
    assert "출력되면 안 되는 질문" not in json.dumps(summary, ensure_ascii=False)


def test_http_error_is_sanitized_to_bounded_error_metadata() -> None:
    error_body = {
        "detail": {
            "code": "openrouter_request_timeout",
            "provider": "openrouter_api",
            "retryable": True,
            "message": "timed out",
            "secret_debug_payload": "must not leak",
        }
    }

    def opener(_request, *, timeout):
        assert timeout == 125.0
        raise HTTPError(
            "https://example.test/api/questions/generate-from-text",
            504,
            "Gateway Timeout",
            {},
            io.BytesIO(json.dumps(error_body).encode("utf-8")),
        )

    ticks = iter([1.0, 91.0])
    result = latency.request_generation(
        base_url="https://example.test",
        question_count=1,
        follow_up_count=3,
        timeout=125.0,
        opener=opener,
        clock=lambda: next(ticks),
    )
    summary = latency.summarize_result(
        question_count=1,
        result=result,
        latency_budget_sec=115.0,
    )

    assert summary["http_status"] == 504
    assert summary["passed"] is False
    assert summary["error"] == {
        "code": "openrouter_request_timeout",
        "provider": "openrouter_api",
        "retryable": True,
        "message": "timed out",
    }
    assert "secret_debug_payload" not in json.dumps(summary)


@pytest.mark.parametrize("question_count", [6, 15, 20])
def test_expected_limit_rejection_requires_fast_4xx(question_count: int) -> None:
    result = latency.HttpResult(
        status=422,
        elapsed_sec=0.125,
        body={
            "detail": {
                "code": "question_plan_capacity_exceeded",
                "max_main_questions": 5,
                "requested_main_questions": question_count,
                "retryable": False,
            }
        },
    )

    summary = latency.summarize_result(
        question_count=question_count,
        result=result,
        latency_budget_sec=5.0,
        expect_rejection=True,
    )

    assert summary["expected_outcome"] == "pre_provider_rejection"
    assert summary["returned_count"] == 0
    assert summary["http_status"] == 422
    assert summary["passed"] is True


@pytest.mark.parametrize(
    ("status", "elapsed"),
    [(200, 0.1), (502, 0.1), (422, 5.001)],
)
def test_expected_limit_rejection_fails_on_success_5xx_or_slow_4xx(
    status: int,
    elapsed: float,
) -> None:
    summary = latency.summarize_result(
        question_count=15,
        result=latency.HttpResult(
            status=status,
            elapsed_sec=elapsed,
            body={
                "detail": {
                    "code": "question_plan_capacity_exceeded",
                    "max_main_questions": 5,
                    "requested_main_questions": 15,
                }
            },
        ),
        latency_budget_sec=5.0,
        expect_rejection=True,
    )

    assert summary["passed"] is False


def test_count_parser_supports_release_cases_and_rejects_out_of_range() -> None:
    assert latency.parse_counts("1,15,20") == (1, 15, 20)
    with pytest.raises(Exception):
        latency.parse_counts("31")


def test_case_count_roles_match_the_single_question_capacity_contract() -> None:
    assert latency.validate_case_counts((1, 5), (6, 15, 20)) == (
        (1, 5),
        (6, 15, 20),
    )
    with pytest.raises(ValueError, match="production-safe success counts"):
        latency.validate_case_counts((6,), (15,))
    with pytest.raises(ValueError, match="rejection counts"):
        latency.validate_case_counts((1,), (5,))
