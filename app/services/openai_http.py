from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

import httpx

from app.services.provider_config import (
    OPENAI_PROVIDER,
    OPENROUTER_PROVIDER,
    generation_provider_headers,
    normalize_generation_provider,
    openrouter_recovery_model,
    provider_base_url,
    provider_error_prefix,
)
from app.services.request_budget import (
    RequestBudgetExceeded,
    clamp_timeout_to_request_budget,
)


_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _provider_key_mismatch(provider: str, api_key: str) -> bool:
    """Fail closed before routing an OpenRouter-prefixed credential."""

    normalized = normalize_generation_provider(provider)
    is_openrouter_key = str(api_key or "").strip().casefold().startswith("sk-or-")
    return is_openrouter_key != (normalized == OPENROUTER_PROVIDER)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _openai_base_urls() -> list[str]:
    """Return one administrator-controlled endpoint; never fail over providers."""

    return [provider_base_url(OPENAI_PROVIDER)]


def _provider_base_urls(provider: str) -> list[str]:
    normalized = normalize_generation_provider(provider)
    if normalized == OPENAI_PROVIDER:
        # Preserve the existing function boundary for tests and administrator
        # OpenAI endpoint overrides.
        return _openai_base_urls()
    return [provider_base_url(normalized)]


def _build_timeout(total_timeout_sec: float | None = None) -> httpx.Timeout:
    read_timeout = float(total_timeout_sec or 60.0)
    read_timeout = max(0.1, min(300.0, read_timeout))
    connect_timeout = min(
        read_timeout,
        _env_float("OPENAI_HTTP_CONNECT_TIMEOUT_SEC", 10.0, 0.1, 60.0),
    )
    write_timeout = min(
        read_timeout,
        _env_float("OPENAI_HTTP_WRITE_TIMEOUT_SEC", min(20.0, read_timeout), 0.1, 120.0),
    )
    pool_timeout = min(
        read_timeout,
        _env_float("OPENAI_HTTP_POOL_TIMEOUT_SEC", 5.0, 0.1, 30.0),
    )
    return httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=write_timeout,
        pool=pool_timeout,
    )


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.TransportError,
        ),
    ):
        return True
    msg = str(exc or "").lower()
    retryable_markers = (
        "winerror 10013",
        "timed out",
        "temporary failure",
        "connection reset",
        "connection aborted",
        "connection refused",
        "network is unreachable",
        "name or service not known",
    )
    return any(m in msg for m in retryable_markers)


def _is_retryable_status(status_code: int) -> bool:
    return int(status_code) in _RETRYABLE_STATUS


def _openrouter_timeout_fallback_effort(provider: str, payload: dict[str, Any]) -> str:
    """Return an explicit latency rescue effort for slow serverless calls.

    Ox Alpha is still attempted with Max first.  A deployment may opt into a
    bounded rescue only after the Max request has timed out; local/default
    behavior remains Max-only when the variable is unset.
    """

    if normalize_generation_provider(provider) != OPENROUTER_PROVIDER:
        return ""
    if str(payload.get("reasoning_effort") or "").strip().casefold() != "max":
        return ""
    effort = str(os.getenv("OPENROUTER_FALLBACK_REASONING_EFFORT", "")).strip().casefold()
    if effort not in {"low", "medium", "high", "xhigh"}:
        return ""
    return effort


def _openrouter_fallback_timeout_sec(primary_timeout_sec: float) -> float:
    try:
        configured = float(
            str(os.getenv("OPENROUTER_FALLBACK_TIMEOUT_SEC", "")).strip()
        )
    except (TypeError, ValueError):
        configured = 0.0
    if configured <= 0:
        configured = float(primary_timeout_sec or 60.0)
    return max(15.0, min(110.0, configured))


def _safe_transport_failure_code(
    exc: BaseException,
    provider: str = OPENAI_PROVIDER,
) -> str:
    """Return a stable provider code without reflecting transport details."""

    prefix = provider_error_prefix(provider)
    if isinstance(exc, httpx.TimeoutException):
        return f"{prefix}_request_timeout"
    normalized = str(exc or "").strip().casefold()
    if any(marker in normalized for marker in ("timed out", "timeout", "readtimeout")):
        return f"{prefix}_request_timeout"
    if isinstance(
        exc,
        (
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.TransportError,
        ),
    ):
        return f"{prefix}_network_unreachable"
    status_match = re.search(r"(?:openai|openrouter)_http_(\d{3})", normalized)
    if status_match:
        return f"{prefix}_http_{status_match.group(1)}"
    return f"{prefix}_request_failed"


def _sleep_backoff(attempt: int) -> None:
    base = _env_float("OPENAI_HTTP_RETRY_BACKOFF_SEC", 0.8, 0.1, 10.0)
    max_backoff = _env_float("OPENAI_HTTP_RETRY_MAX_BACKOFF_SEC", 6.0, 0.5, 30.0)
    backoff = min(max_backoff, base * max(1, attempt))
    time.sleep(backoff)


def _curl_fallback_enabled() -> bool:
    # Keep this opt-in because the curl command line can expose Authorization
    # headers to process inspection or endpoint telemetry.
    default_enabled = False
    return _env_bool("OPENAI_HTTP_CURL_FALLBACK_ENABLED", default_enabled)


def _is_socket_permission_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return "winerror 10013" in msg or "permission denied" in msg


def _run_curl_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None,
    timeout_sec: float,
) -> tuple[int, str]:
    curl_bin = shutil.which("curl") or shutil.which("curl.exe")
    if not curl_bin:
        raise RuntimeError("curl_not_found")

    connect_timeout = _env_float("OPENAI_HTTP_CONNECT_TIMEOUT_SEC", 10.0, 1.0, 60.0)
    max_time = max(3.0, min(300.0, float(timeout_sec or 60.0)))
    cmd = [
        curl_bin,
        "-sS",
        "-X",
        str(method or "GET").upper(),
        url,
        "-H",
        f"Authorization: Bearer {api_key}",
        "-H",
        "Content-Type: application/json",
        "--connect-timeout",
        str(int(round(connect_timeout))),
        "--max-time",
        str(int(round(max_time))),
        "-w",
        "\n__HTTP_STATUS__:%{http_code}\n",
    ]
    stdin_text = None
    if payload is not None:
        cmd.extend(["--data-binary", "@-"])
        stdin_text = json.dumps(payload, ensure_ascii=False)

    completed = subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, max_time + 2.0),
        check=False,
    )
    stdout = str(completed.stdout or "")
    stderr = str(completed.stderr or "").strip()
    if completed.returncode != 0 and not stdout:
        raise RuntimeError(f"curl_failed_{completed.returncode}: {stderr[:200]}")

    marker = "__HTTP_STATUS__:"
    idx = stdout.rfind(marker)
    if idx < 0:
        raise RuntimeError(f"curl_status_missing: {(stderr or stdout)[:200]}")
    body = stdout[:idx].strip()
    status_line = stdout[idx + len(marker):].strip().splitlines()[0].strip()
    try:
        status = int(status_line)
    except Exception:
        status = 0
    if status <= 0:
        raise RuntimeError(f"curl_status_invalid: {status_line[:40]}")
    return status, body


def _request_models_with_curl(api_key: str, timeout_sec: float) -> tuple[bool, str]:
    last_msg = ""
    for base in _openai_base_urls():
        url = f"{base}/models"
        try:
            status, _body = _run_curl_json(
                method="GET",
                url=url,
                api_key=api_key,
                payload=None,
                timeout_sec=timeout_sec,
            )
            if 200 <= status < 300:
                return True, ""
            last_msg = f"http_{status}"
        except Exception as e:
            last_msg = str(e)
            continue
    return False, (last_msg or "curl_models_check_failed")


def _chat_with_curl(url: str, payload: dict[str, Any], api_key: str, timeout_sec: float) -> dict[str, Any]:
    status, body = _run_curl_json(
        method="POST",
        url=url,
        api_key=api_key,
        payload=payload,
        timeout_sec=timeout_sec,
    )
    if status == 200:
        return json.loads(body or "{}")
    raise RuntimeError(f"openai_http_{status}")


def _post_json_response(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    deadline: float,
) -> tuple[int, dict[str, Any]]:
    """Read one completion with a wall-clock deadline.

    ``httpx`` read timeouts are per-read, so a provider that drips reasoning
    chunks can exceed the intended request budget indefinitely. Stream the
    response and enforce the total deadline between chunks. Test doubles that
    only expose ``post`` retain the old path.
    """

    stream = getattr(client, "stream", None)
    if not callable(stream):
        response = client.post(url, headers=headers, json=payload)
        return int(response.status_code), response.json() if response.status_code == 200 else {}

    chunks: list[bytes] = []
    with stream("POST", url, headers=headers, json=payload) as response:
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise httpx.ReadTimeout("provider wall-clock deadline exceeded")
            if chunk:
                chunks.append(bytes(chunk))
        if int(response.status_code) != 200:
            return int(response.status_code), {}
    if time.monotonic() > deadline:
        raise httpx.ReadTimeout("provider wall-clock deadline exceeded")
    try:
        decoded = json.loads(b"".join(chunks).decode("utf-8", errors="replace") or "{}")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("provider_response_invalid_json") from exc
    return 200, decoded if isinstance(decoded, dict) else {}


def post_chat_completions_with_retries(
    payload: dict[str, Any],
    api_key: str,
    timeout_sec: float = 60.0,
    max_attempts: int | None = None,
    provider: str = OPENAI_PROVIDER,
) -> dict[str, Any]:
    provider = normalize_generation_provider(provider)
    error_prefix = provider_error_prefix(provider)
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError(f"{error_prefix}_api_key_not_set")
    if _provider_key_mismatch(provider, key):
        raise RuntimeError("generation_provider_key_mismatch")

    attempts = int(max_attempts or 0) if max_attempts is not None else 0
    if attempts <= 0:
        attempts = _env_int("OPENAI_HTTP_MAX_RETRIES", 3, 1, 8)

    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    headers.update(generation_provider_headers(provider))

    # A caller that supplies an explicit POST budget is asking for a hard
    # upper bound.  The optional curl subprocess is both an extra request and
    # can expose the bearer header in process arguments, so it is never part
    # of that bounded path.
    use_curl_fallback = _curl_fallback_enabled() and max_attempts is None
    last_error: Exception | None = None
    fallback_attempted = False
    for attempt in range(1, attempts + 1):
        # One loop iteration means one upstream POST.  Alternating the proxy
        # environment on later retries retains the old recovery behavior
        # without silently doubling every configured attempt.
        trust_env = attempt % 2 == 1
        for base in _provider_base_urls(provider):
            url = f"{base}/chat/completions"
            try:
                try:
                    attempt_timeout_sec = clamp_timeout_to_request_budget(
                        timeout_sec,
                        reserve_sec=2.0,
                    )
                except RequestBudgetExceeded as budget_exc:
                    raise httpx.ReadTimeout(
                        "generation request deadline exhausted"
                    ) from budget_exc
                timeout = _build_timeout(attempt_timeout_sec)
                with httpx.Client(
                    timeout=timeout,
                    limits=limits,
                    http2=False,
                    trust_env=trust_env,
                ) as client:
                    status_code, response_payload = _post_json_response(
                        client,
                        url,
                        headers=headers,
                        payload=payload,
                        deadline=time.monotonic() + attempt_timeout_sec,
                    )
                if status_code == 200:
                    return response_payload
                err = RuntimeError(f"{error_prefix}_http_{status_code}")
                if _is_retryable_status(status_code):
                    last_error = err
                    break
                raise err
            except Exception as e:
                if not _is_retryable_exception(e):
                    raise
                last_error = e

                # A Max response can spend the whole serverless request
                # window in hidden reasoning.  If the deployment explicitly
                # opts in, make one same-origin rescue request with a lower
                # effort before giving up. This preserves Max whenever it
                # finishes and avoids silently changing local behavior.
                fallback_effort = _openrouter_timeout_fallback_effort(
                    provider,
                    payload,
                )
                if (
                    fallback_effort
                    and not fallback_attempted
                    and isinstance(e, httpx.TimeoutException)
                ):
                    fallback_attempted = True
                    fallback_payload = dict(payload)
                    recovery_model = openrouter_recovery_model()
                    if recovery_model:
                        fallback_payload["model"] = recovery_model
                        fallback_payload.pop("reasoning_effort", None)
                        fallback_payload.pop("response_format", None)
                        if _env_bool("OPENROUTER_RECOVERY_JSON_MODE", True):
                            fallback_payload["response_format"] = {
                                "type": "json_object"
                            }
                    else:
                        fallback_payload["reasoning_effort"] = fallback_effort
                    try:
                        fallback_timeout_sec = clamp_timeout_to_request_budget(
                            _openrouter_fallback_timeout_sec(timeout_sec),
                            reserve_sec=2.0,
                        )
                        fallback_timeout = _build_timeout(fallback_timeout_sec)
                        with httpx.Client(
                            timeout=fallback_timeout,
                            limits=limits,
                            http2=False,
                            trust_env=trust_env,
                        ) as fallback_client:
                            fallback_status, fallback_response_payload = _post_json_response(
                                fallback_client,
                                url,
                                headers=headers,
                                payload=fallback_payload,
                                deadline=time.monotonic() + fallback_timeout_sec,
                            )
                        if fallback_status == 200:
                            recovered_payload = dict(fallback_response_payload)
                            recovered_payload[
                                "_ncscope_openrouter_timeout_recovery_used"
                            ] = True
                            return recovered_payload
                        fallback_error = RuntimeError(
                            f"{error_prefix}_fallback_http_{fallback_status}"
                        )
                        if not _is_retryable_status(fallback_status):
                            raise fallback_error
                        last_error = fallback_error
                    except RequestBudgetExceeded:
                        last_error = httpx.ReadTimeout(
                            "generation request deadline exhausted"
                        )
                    except Exception as fallback_exc:
                        if not _is_retryable_exception(fallback_exc):
                            raise
                        last_error = fallback_exc
                if use_curl_fallback and (
                    _is_socket_permission_error(e) or attempt >= 2
                ):
                    try:
                        return _chat_with_curl(
                            url=url,
                            payload=payload,
                            api_key=key,
                            timeout_sec=timeout_sec,
                        )
                    except Exception as curl_exc:
                        if not _is_retryable_exception(curl_exc):
                            raise
                        last_error = curl_exc
                continue
            continue
        if attempt < attempts:
            _sleep_backoff(attempt)

    if last_error:
        raise RuntimeError(_safe_transport_failure_code(last_error, provider))
    raise RuntimeError(f"{error_prefix}_request_failed")


def check_openai_connectivity_with_retries(
    api_key: str,
    timeout: httpx.Timeout | None = None,
    max_attempts: int | None = None,
    provider: str = OPENAI_PROVIDER,
) -> tuple[bool, str]:
    provider = normalize_generation_provider(provider)
    key = str(api_key or "").strip()
    if not key:
        return False, "missing_api_key"
    if _provider_key_mismatch(provider, key):
        return False, "generation_provider_key_mismatch"

    attempts = int(max_attempts or 0) if max_attempts is not None else 0
    if attempts <= 0:
        attempts = _env_int("OPENAI_NET_CHECK_RETRIES", 2, 1, 5)

    timeout_obj = timeout or _build_timeout(15.0)
    limits = httpx.Limits(max_keepalive_connections=4, max_connections=8)
    headers = {"Authorization": f"Bearer {key}"}
    headers.update(generation_provider_headers(provider))

    last_msg = ""
    # An explicit budget is a hard upper bound on outbound requests.  It also
    # disables the subprocess fallback because that would be an uncounted
    # request and could expose a request-scoped bearer token in process args.
    use_curl_fallback = _curl_fallback_enabled() and max_attempts is None
    base_urls = _provider_base_urls(provider)
    for attempt in range(1, attempts + 1):
        base = base_urls[(attempt - 1) % len(base_urls)]
        url = f"{base}/models"
        trust_env = attempt % 2 == 1
        try:
            with httpx.Client(timeout=timeout_obj, limits=limits, http2=False, trust_env=trust_env) as client:
                resp = client.get(url, headers=headers)
            # Readiness requires successful authentication, not merely
            # network reachability.  401/403 must remain failures.
            if 200 <= resp.status_code < 300:
                return True, ""
            last_msg = f"http_{resp.status_code}"
            if not _is_retryable_status(resp.status_code):
                return False, last_msg
        except Exception as e:
            last_msg = str(e)
            if not _is_retryable_exception(e):
                return False, last_msg
            if use_curl_fallback and (_is_socket_permission_error(e) or attempt >= 2):
                curl_ok, curl_msg = _request_models_with_curl(api_key=key, timeout_sec=15.0)
                if curl_ok:
                    return True, ""
                last_msg = curl_msg
        if attempt < attempts:
            _sleep_backoff(attempt)

    return False, (last_msg or "openai_connectivity_check_failed")
