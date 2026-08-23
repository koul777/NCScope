from __future__ import annotations

import math
import os
from typing import Any


DEFAULT_QUALITY_MODEL = "gpt-5.6-sol"
_SUPPORTED_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
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
    default: str = "max",
) -> str:
    """Resolve a model-compatible reasoning effort for quality generation.

    GPT-5.6 supports the full range through ``max``.  For another model we do
    not guess at compatibility: callers omit the parameter and retain that
    model's native defaults instead of sending a request the API may reject.
    """

    model_key = str(model or "").strip().casefold()
    if not model_key.startswith("gpt-5.6"):
        return ""
    raw = str(
        os.getenv(
            specific_env_name,
            os.getenv("OPENAI_REASONING_EFFORT", default),
        )
    ).strip().casefold()
    return raw if raw in _SUPPORTED_REASONING_EFFORTS else default


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
    # Ox Alpha spends a materially larger share of the completion budget on
    # hidden reasoning at ``max``.  The old 12k reserve could cut a structured
    # seven-question JSON object halfway through the first/second item, which
    # forced the runtime into template fallback and erased the provider's
    # strongest work.  Leave enough room for the reasoning pass plus the
    # visible portfolio while retaining the provider's 128k hard ceiling.
    reasoning_reserve = 24_000 if reasoning_effort == "max" else 6_000 if reasoning_effort else 0
    # Serverless deployments can keep Max reasoning enabled while choosing a
    # smaller bounded reserve so a single request fits the platform proxy
    # deadline. The default remains the quality-first 24k reserve; this is an
    # explicit operational knob, not a silent change to the reasoning effort.
    if reasoning_effort == "max":
        try:
            configured_reserve = int(
                str(os.getenv("OPENROUTER_MAX_REASONING_RESERVE", "")).strip()
            )
        except (TypeError, ValueError):
            configured_reserve = 0
        if configured_reserve > 0:
            reasoning_reserve = max(6_000, min(24_000, configured_reserve))
    visible_reserve = 1_200 if reasoning_effort == "max" else 1_050
    return max(4_200, min(128_000, count * visible_reserve + reasoning_reserve))
