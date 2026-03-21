from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

import httpx

from app.settings import settings


_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


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
    base_raw = str(os.getenv("OPENAI_BASE_URLS", "")).strip()
    candidates: list[str] = []
    if base_raw:
        candidates.extend([x.strip() for x in base_raw.split(",") if x.strip()])
    candidates.append(str(settings.openai_base_url or "").strip())

    out: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        normalized = base.rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out or ["https://api.openai.com/v1"]


def _build_timeout(total_timeout_sec: float | None = None) -> httpx.Timeout:
    read_timeout = float(total_timeout_sec or 60.0)
    read_timeout = max(8.0, min(300.0, read_timeout))
    connect_timeout = _env_float("OPENAI_HTTP_CONNECT_TIMEOUT_SEC", 10.0, 1.0, 60.0)
    write_timeout = _env_float("OPENAI_HTTP_WRITE_TIMEOUT_SEC", min(20.0, read_timeout), 1.0, 120.0)
    pool_timeout = _env_float("OPENAI_HTTP_POOL_TIMEOUT_SEC", 5.0, 0.5, 30.0)
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


def _sleep_backoff(attempt: int) -> None:
    base = _env_float("OPENAI_HTTP_RETRY_BACKOFF_SEC", 0.8, 0.1, 10.0)
    max_backoff = _env_float("OPENAI_HTTP_RETRY_MAX_BACKOFF_SEC", 6.0, 0.5, 30.0)
    backoff = min(max_backoff, base * max(1, attempt))
    time.sleep(backoff)


def _curl_fallback_enabled() -> bool:
    # Windows에서 socket policy/EDR로 python outbound가 막히는 경우를 우회하기 위한 옵션.
    default_enabled = os.name == "nt"
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
            if 200 <= status < 500:
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
    body_preview = str(body or "").strip().replace("\n", " ")[:200]
    raise RuntimeError(f"openai_http_{status}: {body_preview}")


def post_chat_completions_with_retries(
    payload: dict[str, Any],
    api_key: str,
    timeout_sec: float = 60.0,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    attempts = int(max_attempts or 0) if max_attempts is not None else 0
    if attempts <= 0:
        attempts = _env_int("OPENAI_HTTP_MAX_RETRIES", 3, 1, 8)

    timeout = _build_timeout(timeout_sec)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    use_curl_fallback = _curl_fallback_enabled()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        for base in _openai_base_urls():
            url = f"{base}/chat/completions"
            for trust_env in (True, False):
                try:
                    with httpx.Client(timeout=timeout, limits=limits, http2=False, trust_env=trust_env) as client:
                        resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        return resp.json()
                    body_preview = (resp.text or "").strip().replace("\n", " ")[:200]
                    err = RuntimeError(f"openai_http_{resp.status_code}: {body_preview}")
                    if _is_retryable_status(resp.status_code):
                        last_error = err
                        break
                    raise err
                except Exception as e:
                    if not _is_retryable_exception(e):
                        raise
                    last_error = e
                    # python socket permission 에러/지속 실패면 curl 경로도 시도.
                    if use_curl_fallback and (_is_socket_permission_error(e) or attempt >= 2):
                        try:
                            return _chat_with_curl(url=url, payload=payload, api_key=key, timeout_sec=timeout_sec)
                        except Exception as curl_exc:
                            if not _is_retryable_exception(curl_exc):
                                # HTTP 4xx/5xx 등 명시적 실패는 그대로 반환.
                                raise
                            last_error = curl_exc
                    continue
            # trust_env true/false 경로 모두 실패하면 다음 base로 이동.
            continue
        if attempt < attempts:
            _sleep_backoff(attempt)

    if last_error:
        raise RuntimeError(str(last_error))
    raise RuntimeError("openai_request_failed")


def check_openai_connectivity_with_retries(
    api_key: str,
    timeout: httpx.Timeout | None = None,
    max_attempts: int | None = None,
) -> tuple[bool, str]:
    key = str(api_key or "").strip()
    if not key:
        return False, "missing_api_key"

    attempts = int(max_attempts or 0) if max_attempts is not None else 0
    if attempts <= 0:
        attempts = _env_int("OPENAI_NET_CHECK_RETRIES", 2, 1, 5)

    timeout_obj = timeout or _build_timeout(15.0)
    limits = httpx.Limits(max_keepalive_connections=4, max_connections=8)
    headers = {"Authorization": f"Bearer {key}"}

    last_msg = ""
    use_curl_fallback = _curl_fallback_enabled()
    for attempt in range(1, attempts + 1):
        for base in _openai_base_urls():
            url = f"{base}/models"
            for trust_env in (True, False):
                try:
                    with httpx.Client(timeout=timeout_obj, limits=limits, http2=False, trust_env=trust_env) as client:
                        resp = client.get(url, headers=headers)
                    # 2xx and 4xx are both connectivity success.
                    if 200 <= resp.status_code < 500:
                        return True, ""
                    last_msg = f"http_{resp.status_code}"
                    if not _is_retryable_status(resp.status_code):
                        return False, last_msg
                    break
                except Exception as e:
                    last_msg = str(e)
                    if not _is_retryable_exception(e):
                        return False, last_msg
                    if use_curl_fallback and (_is_socket_permission_error(e) or attempt >= 2):
                        curl_ok, curl_msg = _request_models_with_curl(api_key=key, timeout_sec=15.0)
                        if curl_ok:
                            return True, ""
                        last_msg = curl_msg
                    continue
            continue
        if attempt < attempts:
            _sleep_backoff(attempt)

    return False, (last_msg or "openai_connectivity_check_failed")
