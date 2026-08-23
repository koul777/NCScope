from __future__ import annotations

from app.services.openai_quality_config import (
    apply_quality_reasoning,
    quality_candidate_multiplier,
    quality_candidate_variants,
    quality_completion_budget,
)


def test_quality_candidate_policy_defaults_to_three_variants(monkeypatch) -> None:
    monkeypatch.delenv("TEST_CANDIDATE_MULTIPLIER", raising=False)

    assert quality_candidate_multiplier("TEST_CANDIDATE_MULTIPLIER") == 3.0
    assert quality_candidate_variants("TEST_CANDIDATE_MULTIPLIER") == 3


def test_quality_candidate_policy_clamps_to_two_or_three_with_one_as_escape_hatch(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_CANDIDATE_MULTIPLIER", "1")
    assert quality_candidate_multiplier("TEST_CANDIDATE_MULTIPLIER") == 1.0
    assert quality_candidate_variants("TEST_CANDIDATE_MULTIPLIER") == 1

    monkeypatch.setenv("TEST_CANDIDATE_MULTIPLIER", "1.4")
    assert quality_candidate_multiplier("TEST_CANDIDATE_MULTIPLIER") == 2.0
    assert quality_candidate_variants("TEST_CANDIDATE_MULTIPLIER") == 2

    monkeypatch.setenv("TEST_CANDIDATE_MULTIPLIER", "9")
    assert quality_candidate_multiplier("TEST_CANDIDATE_MULTIPLIER") == 3.0
    assert quality_candidate_variants("TEST_CANDIDATE_MULTIPLIER") == 3


def test_max_reasoning_is_applied_only_to_compatible_quality_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "max")
    payload = {
        "model": "gpt-5.6-sol",
        "temperature": 0.25,
        "top_p": 0.9,
    }

    effort = apply_quality_reasoning(
        payload,
        model="gpt-5.6-sol",
        specific_env_name="TEST_REASONING_EFFORT",
    )

    assert effort == "max"
    assert payload["reasoning_effort"] == "max"
    assert "temperature" not in payload
    assert "top_p" not in payload

    legacy_payload = {"temperature": 0.25}
    assert (
        apply_quality_reasoning(
            legacy_payload,
            model="gpt-4o-mini",
            specific_env_name="TEST_REASONING_EFFORT",
        )
        == ""
    )
    assert legacy_payload == {"temperature": 0.25}


def test_max_reasoning_budget_leaves_room_for_reasoning_and_structured_output() -> None:
    assert quality_completion_budget(5, reasoning_effort="max") >= 17_000
    assert quality_completion_budget(50, reasoning_effort="max") <= 128_000
