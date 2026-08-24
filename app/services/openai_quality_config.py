from __future__ import annotations

import math
import os
from typing import Any


# Role-based OpenAI model profile. NCScope deliberately separates lightweight
# candidate ordering, Korean question authorship, and independent quality
# judgement instead of silently using one model for every task.
DEFAULT_NCS_RERANK_MODEL = "gpt-5.6-luna"
DEFAULT_QUESTION_MODEL = "gpt-5.6-terra"
DEFAULT_QUALITY_REVIEW_MODEL = "gpt-5.6-sol"
DEFAULT_QUALITY_REGENERATION_MODEL = "gpt-5.6-sol"

# Backward-compatible import used by older quality-first call sites.
DEFAULT_QUALITY_MODEL = DEFAULT_QUESTION_MODEL


def openai_role_model(role: str, *, explicit_model: str = "") -> str:
    """Resolve one OpenAI role without collapsing independent stages.

    A request model override applies only to initial question authorship. The
    reviewer and quality-regeneration roles remain independently configured.
    """

    normalized_role = str(role or "").strip().casefold()
    role_config = {
        "ncs_rerank": ("OPENAI_RERANK_MODEL", DEFAULT_NCS_RERANK_MODEL),
        "question_authoring": ("OPENAI_STRATEGY_MODEL", DEFAULT_QUESTION_MODEL),
        "auxiliary_question_authoring": (
            "OPENAI_QUESTION_MODEL",
            DEFAULT_QUESTION_MODEL,
        ),
        "quality_review": (
            "OPENAI_QUALITY_REVIEW_MODEL",
            DEFAULT_QUALITY_REVIEW_MODEL,
        ),
        "quality_regeneration": (
            "OPENAI_QUALITY_REGENERATION_MODEL",
            DEFAULT_QUALITY_REGENERATION_MODEL,
        ),
    }
    env_name, fallback = role_config.get(
        normalized_role,
        ("OPENAI_STRATEGY_MODEL", DEFAULT_QUESTION_MODEL),
    )
    if normalized_role == "question_authoring":
        requested = str(explicit_model or "").strip()
        if requested:
            return requested
    return str(os.getenv(env_name, "").strip() or fallback)
_GPT_56_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
_GPT_54_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh"}
)


def quality_candidate_multiplier(
    env_name: str,
    *,
    default: float = 3.0,
) -> float:
    """Return the configured quality-first candidate multiplier.

    The production policy intentionally keeps the pool between two and three
    times the requested output.  A value of ``1`` remains available as an
    explicit operational escape hatch for constrained local environments.
    """

    raw = str(os.getenv(env_name, str(default))).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if value <= 1.0:
        return 1.0
    return max(2.0, min(3.0, value))


def quality_candidate_variants(
    env_name: str,
    *,
    default: float = 3.0,
) -> int:
    return max(1, min(3, int(math.ceil(quality_candidate_multiplier(env_name, default=default)))))


def quality_reasoning_effort(
    model: str,
    *,
    specific_env_name: str,
    default: str = "high",
) -> str:
    """Resolve a model-compatible reasoning effort for quality generation.

    GPT-5.6 supports the full range through ``max``; GPT-5.4 supports up to
    ``xhigh``. For another model we do not guess at compatibility: callers
    omit the parameter and retain that model's native defaults instead of
    sending a request the API may reject.
    """

    model_key = str(model or "").strip().casefold()
    if model_key.startswith("gpt-5.6"):
        supported_efforts = _GPT_56_REASONING_EFFORTS
    elif model_key.startswith("gpt-5.4"):
        supported_efforts = _GPT_54_REASONING_EFFORTS
    else:
        return ""
    fallback = default if default in supported_efforts else "high"
    raw = str(
        os.getenv(
            specific_env_name,
            os.getenv("OPENAI_REASONING_EFFORT", fallback),
        )
    ).strip().casefold()
    return raw if raw in supported_efforts else fallback


def apply_quality_reasoning(
    payload: dict[str, Any],
    *,
    model: str,
    specific_env_name: str,
) -> str:
    """Apply quality-first reasoning controls in-place and return the effort."""

    effort = quality_reasoning_effort(
        model,
        specific_env_name=specific_env_name,
    )
    if not effort:
        return ""
    payload["reasoning_effort"] = effort
    if effort != "none":
        # Sampling controls are not consistently accepted by reasoning models,
        # and the candidate pool already supplies the desired variation.
        payload.pop("temperature", None)
        payload.pop("top_p", None)
        payload.pop("presence_penalty", None)
        payload.pop("frequency_penalty", None)
    return effort


def quality_completion_budget(target_count: int, *, reasoning_effort: str = "") -> int:
    """Budget visible structured output plus room for model reasoning."""

    count = max(1, int(target_count or 1))
    # Leave enough room for the reasoning pass plus the visible structured
    # portfolio while retaining the provider's 128k hard ceiling.
    reasoning_reserve = 24_000 if reasoning_effort == "max" else 6_000 if reasoning_effort else 0
    # Serverless deployments can keep Max reasoning enabled while choosing a
    # smaller bounded reserve so a single request fits the platform proxy
    # deadline. The default remains the quality-first 24k reserve; this is an
    # explicit operational knob, not a silent change to the reasoning effort.
    if reasoning_effort == "max":
        try:
            configured_reserve = int(
                str(os.getenv("OPENAI_MAX_REASONING_RESERVE", "")).strip()
            )
        except (TypeError, ValueError):
            configured_reserve = 0
        if configured_reserve > 0:
            reasoning_reserve = max(6_000, min(24_000, configured_reserve))
    visible_reserve = 1_200 if reasoning_effort == "max" else 1_050
    return max(4_200, min(128_000, count * visible_reserve + reasoning_reserve))
