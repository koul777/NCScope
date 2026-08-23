"""Public latency/capacity smoke for the institution question generator.

The multipart upload route requires a document-bound signed review session and
the original source files. A durable deployment probe cannot safely manufacture
that state. This probe therefore uses the public manual-text route, which joins
the same NCS KSA lookup and OpenRouter generation orchestrator after input
review, while requiring no browser key or API key in the process environment.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://ncscope.vercel.app"
GENERATION_PATH = "/api/questions/generate-from-text"
DEFAULT_SUCCESS_COUNTS = (1, 5)
DEFAULT_REJECTION_COUNTS = (6, 15, 20)

# Stable public NCS competency-unit identifiers used by the production workflow.
# The public contract selects exactly one detail and one interview method. Counts
# above the public five-question limit are intentionally sent against that same
# detail to verify pre-model capacity rejection.
SMOKE_NCS_UNITS: tuple[dict[str, str], ...] = (
    {
        "ncsClCd": "0202030209_22v2",
        "compeUnitName": "사무자동화 프로그램 활용",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "업무 목적에 맞는 자료를 수집·정리하고 문서를 작성·검토하는 능력",
    },
    {
        "ncsClCd": "0202030202_25v3",
        "compeUnitName": "자료관리",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "업무 자료의 분류·보존·검색 기준을 적용하고 최신 상태를 유지하는 능력",
    },
    {
        "ncsClCd": "0202030203_25v3",
        "compeUnitName": "업무보고서작성",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "업무 진행 결과와 근거 자료를 분석해 보고서와 성과 지표를 작성하는 능력",
    },
)


@dataclass(frozen=True)
class HttpResult:
    status: int | None
    elapsed_sec: float
    body: dict[str, Any]
    transport_error: str = ""


def _distribute_questions(total: int, unit_count: int) -> list[int]:
    if total < 1:
        raise ValueError("question count must be positive")
    if unit_count < 1:
        raise ValueError("unit_count must be positive")
    if total > unit_count * 10:
        raise ValueError("question count exceeds the API's per-detail limit")
    active = min(unit_count, total)
    quotient, remainder = divmod(total, active)
    counts = [quotient + (1 if index < remainder else 0) for index in range(active)]
    if any(count > 10 for count in counts):
        raise ValueError("question count exceeds the API's per-detail limit")
    return counts


def build_generation_payload(question_count: int, *, follow_up_count: int = 3) -> dict[str, Any]:
    if not 0 <= follow_up_count <= 5:
        raise ValueError("follow_up_count must be between 0 and 5")
    units = [dict(SMOKE_NCS_UNITS[0])]
    items = [
        {
            "detail": units[0]["ncsSubdCdnm"],
            "enabled": True,
            "main_count": question_count,
            "follow_up_count": follow_up_count,
        }
    ]
    return {
        "notice_text": (
            "공공기관 사무행정 담당자를 채용한다. 업무 목적에 맞는 자료 정리와 문서 작성 역량이 필요하다."
        ),
        "duty_text": (
            "공고문과 업무 자료를 확인하고 문서·보고서를 작성하며, 누락·오류 자료를 정정하고 결과를 공유한다."
        ),
        "evaluation_text": (
            "문서 기준 적용, 자료 오류 원인 분석, 보고 품질과 협업 소통 결과를 구조화된 질문으로 평가한다."
        ),
        "selected_ncs": units,
        "question_plan": {"items": items},
        "interview_methods": ["경험면접"],
        "include_all_questions": True,
    }


def _validated_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        raise ValueError("base URL must be an http(s) URL without credentials")
    if parts.query or parts.fragment:
        raise ValueError("base URL must not include a query or fragment")
    return normalized


def _decode_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def request_generation(
    *,
    base_url: str,
    question_count: int,
    follow_up_count: int,
    timeout: float,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.perf_counter,
) -> HttpResult:
    payload = build_generation_payload(question_count, follow_up_count=follow_up_count)
    request = Request(
        f"{_validated_base_url(base_url)}{GENERATION_PATH}",
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "NCScope-generation-latency-smoke/1.0",
        },
        method="POST",
    )
    started = clock()
    try:
        with opener(request, timeout=timeout) as response:
            body = _decode_json(response.read())
            return HttpResult(
                status=int(response.status),
                elapsed_sec=max(0.0, clock() - started),
                body=body,
            )
    except HTTPError as exc:
        return HttpResult(
            status=int(exc.code),
            elapsed_sec=max(0.0, clock() - started),
            body=_decode_json(exc.read()),
        )
    except (TimeoutError, socket.timeout, URLError, OSError) as exc:
        return HttpResult(
            status=None,
            elapsed_sec=max(0.0, clock() - started),
            body={},
            transport_error=type(exc).__name__,
        )


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_summary(body: dict[str, Any], transport_error: str) -> dict[str, Any] | None:
    if transport_error:
        return {"type": transport_error}
    detail = body.get("detail")
    if isinstance(detail, dict):
        summary = {
            key: detail.get(key)
            for key in (
                "code",
                "provider",
                "retryable",
                "max_main_questions",
                "requested_main_questions",
            )
            if detail.get(key) is not None
        }
        message = str(detail.get("message") or detail.get("detail") or "").strip()
        if message:
            summary["message"] = message[:240]
        return summary or {"type": "http_error"}
    if detail is not None:
        return {"message": str(detail).strip()[:240]}
    return None


def summarize_result(
    *,
    question_count: int,
    result: HttpResult,
    latency_budget_sec: float,
    expect_rejection: bool = False,
) -> dict[str, Any]:
    strategy = result.body.get("strategy") if isinstance(result.body.get("strategy"), dict) else {}
    batching = strategy.get("generation_batching") if isinstance(strategy.get("generation_batching"), dict) else {}
    returned_count = _integer(result.body.get("generated_questions_total_count"))
    if returned_count is None:
        questions = strategy.get("interview_questions")
        returned_count = len(questions) if isinstance(questions, list) else 0
    rejection_detail = result.body.get("detail") if isinstance(result.body.get("detail"), dict) else {}
    status_ok = (
        result.status == 422
        and rejection_detail.get("code") == "question_plan_capacity_exceeded"
        and _integer(rejection_detail.get("max_main_questions")) == 5
        and _integer(rejection_detail.get("requested_main_questions")) == question_count
        if expect_rejection
        else result.status == 200
    )
    count_ok = True if expect_rejection else returned_count >= question_count
    within_budget = result.elapsed_sec <= latency_budget_sec
    summary: dict[str, Any] = {
        "case": f"questions_{question_count}",
        "expected_outcome": "pre_provider_rejection" if expect_rejection else "success",
        "requested_count": question_count,
        "http_status": result.status,
        "elapsed_sec": round(result.elapsed_sec, 3),
        "latency_budget_sec": latency_budget_sec,
        "returned_count": returned_count,
        "provider_request_count": _integer(strategy.get("provider_generation_request_count")),
        "generation_batch_count": _integer(batching.get("batch_count")),
        "generation_batch_size_limit": _integer(batching.get("batch_size_limit")),
        "generation_batch_concurrency": _integer(batching.get("max_concurrency")),
        "within_latency_budget": within_budget,
        "passed": bool(status_ok and count_ok and within_budget),
    }
    error = _error_summary(result.body, result.transport_error)
    if error:
        summary["error"] = error
    return summary


def parse_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(int(part.strip()) for part in str(value).split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("counts must be comma-separated integers") from exc
    if not counts or any(count < 1 or count > len(SMOKE_NCS_UNITS) * 10 for count in counts):
        raise argparse.ArgumentTypeError("counts must contain values between 1 and 30")
    return counts


def validate_case_counts(
    success_counts: Iterable[int],
    rejection_counts: Iterable[int],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    successes = tuple(int(value) for value in success_counts)
    rejections = tuple(int(value) for value in rejection_counts)
    if set(successes) & set(rejections):
        raise ValueError("success and rejection counts must not overlap")
    if any(value < 1 or value > 5 for value in successes):
        raise ValueError("production-safe success counts must be between 1 and 5")
    if any(value <= 5 or value > 30 for value in rejections):
        raise ValueError("rejection counts must be between 6 and 30")
    return successes, rejections


def run_cases(
    *,
    base_url: str,
    success_counts: Iterable[int],
    rejection_counts: Iterable[int],
    follow_up_count: int,
    timeout: float,
    latency_budget_sec: float,
    rejection_budget_sec: float,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    cases = [
        *((int(count), False, latency_budget_sec) for count in success_counts),
        *((int(count), True, rejection_budget_sec) for count in rejection_counts),
    ]
    for count, expect_rejection, budget_sec in cases:
        result = request_generation(
            base_url=base_url,
            question_count=count,
            follow_up_count=follow_up_count,
            timeout=timeout,
        )
        summary = summarize_result(
            question_count=count,
            result=result,
            latency_budget_sec=budget_sec,
            expect_rejection=expect_rejection,
        )
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), flush=True)
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify public one-question generation and fast pre-provider rejection "
            "of oversized requests. No API key is read or sent."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--success-counts", type=parse_counts, default=DEFAULT_SUCCESS_COUNTS)
    parser.add_argument("--rejection-counts", type=parse_counts, default=DEFAULT_REJECTION_COUNTS)
    parser.add_argument("--follow-ups", type=int, choices=range(0, 6), default=3)
    parser.add_argument("--timeout", type=float, default=295.0)
    parser.add_argument("--latency-budget", type=float, default=285.0)
    parser.add_argument("--rejection-budget", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        base_url = _validated_base_url(args.base_url)
        if args.timeout <= 0 or args.latency_budget <= 0 or args.rejection_budget <= 0:
            raise ValueError("timeout and latency budgets must be positive")
        success_counts, rejection_counts = validate_case_counts(
            args.success_counts,
            args.rejection_counts,
        )
    except ValueError as exc:
        parser.error(str(exc))
    summaries = run_cases(
        base_url=base_url,
        success_counts=success_counts,
        rejection_counts=rejection_counts,
        follow_up_count=args.follow_ups,
        timeout=args.timeout,
        latency_budget_sec=args.latency_budget,
        rejection_budget_sec=args.rejection_budget,
    )
    failed = [row["case"] for row in summaries if not row["passed"]]
    aggregate = {
        "suite": "ncscope_generation_latency_v1",
        "base_url": base_url,
        "passed": not failed,
        "case_count": len(summaries),
        "failed_cases": failed,
    }
    print(json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
