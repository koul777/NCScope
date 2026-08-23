from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


OPENAI_PROVIDER = "openai_api"
OPENROUTER_PROVIDER = "openrouter_api"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "stealth/ox-alpha"
OPENROUTER_FREE_RECOVERY_MODEL = "openai/gpt-oss-20b"
_OPENROUTER_REASONING_EFFORTS = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)
_OPENROUTER_HIGH_REASONING_METHODS = frozenset(
    {
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "창의적 문제해결력면접",
    }
)

_PROVIDER_ALIASES = {
    "openai": OPENAI_PROVIDER,
    OPENAI_PROVIDER: OPENAI_PROVIDER,
    "open-router": OPENROUTER_PROVIDER,
    "open_router": OPENROUTER_PROVIDER,
    "openrouter": OPENROUTER_PROVIDER,
    OPENROUTER_PROVIDER: OPENROUTER_PROVIDER,
}
_REQUEST_PROVIDERS = frozenset({OPENAI_PROVIDER, OPENROUTER_PROVIDER})

_PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {
    OPENAI_PROVIDER: {
        "provider": OPENAI_PROVIDER,
        "provider_label": "OpenAI API",
        "key_label": "OpenAI API 키",
        "request_message": "OpenAI API 키를 입력해 주세요. 키는 생성 요청에만 사용됩니다.",
        "base_url": OPENAI_DEFAULT_BASE_URL,
        "default_model": "gpt-5.6-sol",
        "auth_mode": "request_scoped_api_key",
        "requires_request_api_key": True,
        "credential_managed_by": "request",
        "supports_custom_model": True,
        "local_only": False,
    },
    OPENROUTER_PROVIDER: {
        "provider": OPENROUTER_PROVIDER,
        "provider_label": "OpenRouter · Ox Alpha",
        "key_label": "OpenRouter API 키",
        "request_message": "OpenRouter API 키를 입력해 주세요. 키는 생성 요청에만 사용됩니다.",
        "base_url": OPENROUTER_DEFAULT_BASE_URL,
        "default_model": OPENROUTER_DEFAULT_MODEL,
        "auth_mode": "request_scoped_api_key",
        "requires_request_api_key": True,
        "credential_managed_by": "request",
        "supports_custom_model": False,
        "local_only": False,
    },
}

_REQUEST_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "generation_request_context",
    default={},
)


@dataclass(frozen=True)
class ResolvedGenerationCredential:
    provider: str
    api_key: str
    key_field: str


class GenerationCredentialError(ValueError):
    def __init__(self, code: str, provider: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.provider = provider
        self.message = message


def normalize_generation_provider(value: Any = "", *, default: str = OPENAI_PROVIDER) -> str:
    raw = str(value or "").strip().casefold()
    if not raw:
        return default
    return _PROVIDER_ALIASES.get(raw, raw)


def configured_generation_provider(default: str = OPENROUTER_PROVIDER) -> str:
    provider = normalize_generation_provider(
        os.getenv("INTERVIEW_GENERATION_PROVIDER")
        or os.getenv("JD_STRATEGY_PROVIDER")
        or "",
        default=default,
    )
    return provider if provider in _REQUEST_PROVIDERS else default


def request_supported_generation_providers() -> tuple[str, ...]:
    return (OPENROUTER_PROVIDER, OPENAI_PROVIDER)


def generation_provider_config(provider: Any = "") -> dict[str, Any]:
    normalized = normalize_generation_provider(
        provider,
        default=configured_generation_provider(),
    )
    config = _PROVIDER_CONFIGS.get(normalized)
    if config:
        return dict(config)
    return {
        "provider": normalized,
        "provider_label": normalized or "설정 오류",
        "key_label": "API 키",
        "request_message": "지원되지 않는 생성 공급자입니다.",
        "base_url": "",
        "default_model": "",
        "auth_mode": "misconfigured",
        "requires_request_api_key": False,
        "credential_managed_by": "request",
        "supports_custom_model": False,
        "local_only": False,
    }


def detect_generation_provider_from_key(api_key: str) -> str:
    key = str(api_key or "").strip().casefold()
    if key.startswith("sk-or-"):
        return OPENROUTER_PROVIDER
    if key.startswith("sk-"):
        return OPENAI_PROVIDER
    raise GenerationCredentialError(
        "generation_api_key_invalid",
        "",
        "API 키는 OpenRouter sk-or- 또는 OpenAI sk- 형식이어야 합니다.",
    )


def resolve_generation_credential(
    *,
    generation_api_key: Any = "",
    openai_api_key: Any = "",
    openrouter_api_key: Any = "",
    requested_provider: Any = "",
) -> ResolvedGenerationCredential:
    """Resolve exactly one request credential without consulting the environment."""

    supplied = [
        (name, str(value or "").strip())
        for name, value in (
            ("generation_api_key", generation_api_key),
            ("openai_api_key", openai_api_key),
            ("openrouter_api_key", openrouter_api_key),
        )
        if str(value or "").strip()
    ]
    explicit_provider = normalize_generation_provider(requested_provider, default="")
    if explicit_provider and explicit_provider not in _REQUEST_PROVIDERS:
        raise GenerationCredentialError(
            "generation_provider_invalid",
            explicit_provider,
            "generation_provider는 openai_api 또는 openrouter_api여야 합니다.",
        )
    if len(supplied) > 1:
        raise GenerationCredentialError(
            "generation_api_key_ambiguous",
            explicit_provider,
            "API 키 필드는 하나만 보내 주세요.",
        )
    if not supplied:
        provider = explicit_provider or configured_generation_provider()
        return ResolvedGenerationCredential(provider=provider, api_key="", key_field="")

    key_field, api_key = supplied[0]
    detected_provider = detect_generation_provider_from_key(api_key)
    if key_field == "openrouter_api_key" and detected_provider != OPENROUTER_PROVIDER:
        raise GenerationCredentialError(
            "generation_provider_key_mismatch",
            OPENROUTER_PROVIDER,
            "OpenRouter에는 sk-or- 형식의 키만 사용할 수 있습니다.",
        )
    if explicit_provider and explicit_provider != detected_provider:
        raise GenerationCredentialError(
            "generation_provider_key_mismatch",
            explicit_provider,
            "선택한 공급자와 API 키 접두어가 일치하지 않습니다.",
        )
    return ResolvedGenerationCredential(
        provider=detected_provider,
        api_key=api_key,
        key_field=key_field,
    )


def request_key_error_code(provider: Any, suffix: str) -> str:
    normalized = normalize_generation_provider(provider, default="generation")
    prefix = "openrouter" if normalized == OPENROUTER_PROVIDER else "openai_api"
    return f"{prefix}_{suffix}"


def request_key_error_message(provider: Any, *, invalid: bool = False) -> str:
    config = generation_provider_config(provider)
    key_label = str(config.get("key_label") or "API 키")
    return (
        f"{key_label} 형식이 올바르지 않습니다."
        if invalid
        else f"{key_label}를 입력해 주세요."
    )


def sanitize_generation_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model:
        return ""
    if len(model) > 200 or any(ord(char) < 33 or ord(char) > 126 for char in model):
        raise ValueError("generation_model_invalid")
    return model


def resolve_generation_model(
    *,
    provider: Any = "",
    explicit_model: Any = "",
    env_name: str = "",
    fallback_default: str = "",
) -> str:
    normalized = normalize_generation_provider(
        provider or current_generation_request_context().get("generation_provider", ""),
        default=configured_generation_provider(),
    )
    if normalized == OPENROUTER_PROVIDER:
        return OPENROUTER_DEFAULT_MODEL

    for candidate in (
        explicit_model,
        current_generation_request_context().get("generation_model", ""),
        os.getenv(env_name, "") if env_name else "",
    ):
        try:
            model = sanitize_generation_model(candidate)
        except ValueError:
            model = ""
        if model:
            return model
    provider_default = str(generation_provider_config(normalized).get("default_model") or "")
    return provider_default or fallback_default


def provider_model(provider: Any, openai_model: str) -> str:
    normalized = normalize_generation_provider(provider)
    if normalized == OPENROUTER_PROVIDER:
        return OPENROUTER_DEFAULT_MODEL
    return str(openai_model or "").strip()


def openrouter_recovery_model() -> str:
    """Return an administrator-controlled OpenRouter failover model.

    The value never comes from a browser request. Keeping it server-owned
    preserves the Ox Alpha product contract while allowing a bounded recovery
    when that free preview model times out or emits unusable JSON.
    """

    model = str(os.getenv("OPENROUTER_RECOVERY_MODEL") or "").strip()
    if not model:
        return ""
    if len(model) > 200 or any(ord(char) < 33 or ord(char) > 126 for char in model):
        return ""
    return model


def openrouter_reasoning_effort(
    *,
    interview_methods: Any = None,
    target_count: Any = 1,
    follow_up_count: Any = 3,
    stage: str = "primary",
) -> tuple[str, str]:
    """Resolve a bounded reasoning profile for the current generation stage.

    Ordinary one-question generation stays at the deployment's configured
    effort.  Production sets that profile to medium. Presentation, debate, in-basket and creative problem-solving
    questions require more cross-constraint reasoning, so they are promoted to
    the server-owned high-risk effort.  A quality retry is also high-risk by
    definition because it is only spent after a failed quality gate.  The
    policy changes effort, not request count, and remains overrideable through
    dedicated server environment variables.
    """

    methods = {
        str(value or "").strip()
        for value in (interview_methods or [])
        if str(value or "").strip()
    }
    try:
        count = max(1, int(target_count or 1))
    except (TypeError, ValueError):
        count = 1
    try:
        followups = max(0, int(follow_up_count or 0))
    except (TypeError, ValueError):
        followups = 0

    normalized_stage = str(stage or "primary").strip().casefold()
    high_risk = bool(methods & _OPENROUTER_HIGH_REASONING_METHODS)
    # Multi-slot plans with deep follow-ups are materially harder even when
    # the selected method itself is not one of the high-risk formats.
    if count >= 3 and followups >= 4:
        high_risk = True

    if normalized_stage in {"quality_retry", "quality_recheck", "retry"}:
        env_name = "OPENROUTER_QUALITY_RETRY_REASONING_EFFORT"
        default = "high"
        reason = "quality_retry"
    elif high_risk:
        env_name = "OPENROUTER_HIGH_RISK_REASONING_EFFORT"
        # Existing local runs defaulted Ox Alpha to Max. Production pins this
        # profile to high in vercel.json so the upgrade is bounded there.
        default = "max"
        reason = "high_risk_interview_method" if methods & _OPENROUTER_HIGH_REASONING_METHODS else "complex_question_plan"
    else:
        env_name = "OPENROUTER_PRIMARY_REASONING_EFFORT"
        # Keep the existing local quality-first default. Production explicitly
        # sets this variable to ``medium`` in vercel.json to bound latency.
        default = "max"
        reason = "standard_generation"

    if normalized_stage in {"quality_retry", "quality_recheck", "retry"}:
        configured_raw = os.getenv(env_name)
        if configured_raw is None:
            configured_raw = os.getenv("OPENROUTER_INVALID_OUTPUT_RETRY_REASONING_EFFORT")
        if configured_raw is None:
            configured_raw = os.getenv("OPENROUTER_FALLBACK_REASONING_EFFORT")
        configured = str(configured_raw or default).strip().casefold()
    else:
        configured = str(os.getenv(env_name, default) or default).strip().casefold()
    if configured not in _OPENROUTER_REASONING_EFFORTS:
        configured = default
    return configured, reason


def provider_base_url(provider: Any) -> str:
    normalized = normalize_generation_provider(provider)
    if normalized == OPENROUTER_PROVIDER:
        return OPENROUTER_DEFAULT_BASE_URL
    configured = str(os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/")
    if configured.startswith("https://"):
        return configured
    return OPENAI_DEFAULT_BASE_URL


def provider_error_prefix(provider: Any) -> str:
    return (
        "openrouter"
        if normalize_generation_provider(provider) == OPENROUTER_PROVIDER
        else "openai"
    )


def provider_candidate_concurrency(provider: Any, requested_variants: int) -> int:
    requested = max(1, min(3, int(requested_variants or 1)))
    if normalize_generation_provider(provider) != OPENROUTER_PROVIDER:
        return 1
    try:
        configured = int(str(os.getenv("OPENROUTER_CANDIDATE_CONCURRENCY", "3")).strip())
    except (TypeError, ValueError):
        configured = 3
    return max(1, min(requested, configured, 3))


def provider_timeout_sec(
    provider: Any,
    openai_timeout_sec: float,
    *,
    openrouter_env_name: str = "OPENROUTER_TIMEOUT_SEC",
) -> float:
    if normalize_generation_provider(provider) != OPENROUTER_PROVIDER:
        return max(8.0, min(300.0, float(openai_timeout_sec or 60.0)))
    try:
        configured = float(
            str(os.getenv(openrouter_env_name, os.getenv("OPENROUTER_TIMEOUT_SEC", "105"))).strip()
        )
    except (TypeError, ValueError):
        configured = 105.0
    # Keep a bounded lower cap so serverless deployments can fail over from a
    # slow Max-reasoning request before the platform proxy deadline.  The
    # caller still controls the practical budget through the environment.
    return max(8.0, min(110.0, configured))


def prepare_chat_payload(payload: dict[str, Any], provider: Any) -> dict[str, Any]:
    """Create a capability-safe OpenAI-compatible payload."""

    normalized = normalize_generation_provider(provider)
    prepared = copy.deepcopy(payload)
    if normalized != OPENROUTER_PROVIDER:
        return prepared
    internal_recovery_model = str(
        prepared.pop("_openrouter_internal_recovery_model", "") or ""
    ).strip()
    internal_reasoning_effort = str(
        prepared.pop("_openrouter_internal_reasoning_effort", "") or ""
    ).strip().casefold()
    recovery_model = (
        openrouter_recovery_model()
        if internal_recovery_model == "configured"
        else internal_recovery_model
    )
    if (
        not recovery_model
        or len(recovery_model) > 200
        or any(ord(char) < 33 or ord(char) > 126 for char in recovery_model)
    ):
        recovery_model = ""
    prepared["model"] = recovery_model or OPENROUTER_DEFAULT_MODEL
    prepared.pop("n", None)
    recovery_effort = str(
        prepared.pop("_openrouter_internal_recovery_effort", "") or ""
    ).strip().casefold()
    configured_effort = str(
        os.getenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "") or ""
    ).strip().casefold()
    if recovery_model:
        # The free router chooses among models with different reasoning
        # parameter support. Let it use each model's native default.
        prepared.pop("reasoning_effort", None)
    else:
        prepared["reasoning_effort"] = (
            recovery_effort
            if recovery_effort in {"low", "medium", "high", "xhigh"}
            else internal_reasoning_effort
            if internal_reasoning_effort in _OPENROUTER_REASONING_EFFORTS
            else configured_effort
            if configured_effort in _OPENROUTER_REASONING_EFFORTS
            else "max"
        )
    for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
        prepared.pop(key, None)
    if "max_completion_tokens" in prepared:
        prepared["max_tokens"] = prepared.pop("max_completion_tokens")
    response_format = prepared.get("response_format")
    if recovery_model:
        # Free endpoints vary in structured-output parameter support. The
        # prompt still requires JSON and the response goes through the same
        # strict decoder/count/content checks, so omit only this transport
        # capability hint for the recovery request.
        prepared.pop("response_format", None)
    elif (
        isinstance(response_format, dict)
        and str(response_format.get("type") or "").casefold() == "json_schema"
    ):
        prepared["response_format"] = {"type": "json_object"}
    return prepared


def current_generation_request_context() -> dict[str, Any]:
    return dict(_REQUEST_CONTEXT.get() or {})


def current_generation_provider() -> str:
    return normalize_generation_provider(
        current_generation_request_context().get("generation_provider", ""),
        default=configured_generation_provider(),
    )


def current_generation_base_url() -> str:
    return provider_base_url(current_generation_provider())


def generation_provider_headers(provider: Any) -> dict[str, str]:
    if normalize_generation_provider(provider) != OPENROUTER_PROVIDER:
        return {}
    headers: dict[str, str] = {}
    http_referer = str(os.getenv("OPENROUTER_HTTP_REFERER", "")).strip()
    app_title = str(os.getenv("OPENROUTER_APP_TITLE", "NCScope")).strip()
    if http_referer.startswith("https://"):
        headers["HTTP-Referer"] = http_referer
    if app_title:
        headers["X-OpenRouter-Title"] = app_title[:120]
    return headers


def current_generation_headers() -> dict[str, str]:
    return generation_provider_headers(current_generation_provider())


@contextmanager
def use_generation_request(
    *,
    provider: Any = "",
    generation_model: Any = "",
) -> Iterator[dict[str, Any]]:
    normalized = normalize_generation_provider(
        provider,
        default=configured_generation_provider(),
    )
    request_context = {
        "generation_provider": normalized,
        "generation_model": resolve_generation_model(
            provider=normalized,
            explicit_model=generation_model,
        ),
    }
    token = _REQUEST_CONTEXT.set(request_context)
    try:
        yield dict(request_context)
    finally:
        _REQUEST_CONTEXT.reset(token)


def push_generation_request(
    *,
    provider: Any = "",
    generation_model: Any = "",
):
    normalized = normalize_generation_provider(
        provider,
        default=configured_generation_provider(),
    )
    config = generation_provider_config(normalized)
    request_context = {
        "generation_provider": normalized,
        "generation_model": sanitize_generation_model(generation_model),
        "base_url": str(config.get("base_url") or "").rstrip("/"),
    }
    token = _REQUEST_CONTEXT.set(request_context)
    return token


def pop_generation_request(token) -> None:
    _REQUEST_CONTEXT.reset(token)
