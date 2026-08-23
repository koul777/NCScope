"""Request-wide wall-clock budget shared by blocking provider clients."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar(
    "ncscope_request_deadline",
    default=None,
)


class RequestBudgetExceeded(TimeoutError):
    """Raised before starting work that cannot fit in the request budget."""


@contextmanager
def use_request_budget(timeout_sec: float) -> Iterator[None]:
    seconds = max(1.0, float(timeout_sec or 1.0))
    token = _REQUEST_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _REQUEST_DEADLINE.reset(token)


def remaining_request_budget_sec() -> float | None:
    deadline = _REQUEST_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def clamp_timeout_to_request_budget(
    requested_timeout_sec: float,
    *,
    reserve_sec: float = 1.0,
    minimum_sec: float = 0.1,
) -> float:
    requested = max(minimum_sec, float(requested_timeout_sec or minimum_sec))
    remaining = remaining_request_budget_sec()
    if remaining is None:
        return requested
    available = remaining - max(0.0, float(reserve_sec or 0.0))
    if available <= minimum_sec:
        raise RequestBudgetExceeded("generation request deadline exhausted")
    return max(minimum_sec, min(requested, available))
