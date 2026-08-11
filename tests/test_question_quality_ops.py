from __future__ import annotations

import pytest

from app.services.question_quality_ops import (
    ALLOWED_FEEDBACK_CODES,
    QUALITY_POLICY_VERSION,
    aggregate_quality_metrics,
    derive_quality_control,
    feedback_prompt_context,
    sanitize_feedback_payload,
)


def _strategy(*, passed: bool = True, checks: dict[str, bool] | None = None) -> dict:
    checks = checks or {
        "ksa_grounded": True,
        "job_specific_context": True,
        "official_sample_format": True,
        "blind_hiring_safe": True,
        "focus_scenario_coherence": True,
    }
    return {
        "question_quality_report": {
            "passed": passed,
            "summary": {"needs_review_count": 0},
            "items": [{"ready": passed, "checks": checks, "issues": []}],
        }
    }


def test_policy_and_feedback_code_contract() -> None:
    assert QUALITY_POLICY_VERSION == "ncs-official-task-evaluation-pair-v19-skill-object-action"
    assert ALLOWED_FEEDBACK_CODES == {
        "wrong_ncs_alignment",
        "behavioral_not_star_like",
        "too_generic",
        "duplicate_intent",
        "difficulty_too_low",
        "difficulty_too_high",
        "legal_or_bias_risk",
        "unnatural_wording",
        "focus_scenario_mismatch",
        "missing_task_conditions",
        "missing_ksa_evidence",
        "method_task_mismatch",
        "excellent_golden_candidate",
    }


def test_derive_quality_control_allows_clean_official_report() -> None:
    result = derive_quality_control(_strategy())

    assert result == {
        "policy_version": QUALITY_POLICY_VERSION,
        "review_required": False,
        "escalation_required": False,
        "exception_allowed": True,
        "trigger_codes": [],
    }


def test_derive_quality_control_failed_report_and_fallback_require_review() -> None:
    strategy = _strategy(passed=False)
    strategy["generation_mode"] = "hybrid_ai_with_template_fallback"

    result = derive_quality_control(strategy)

    assert result["review_required"] is True
    assert result["escalation_required"] is False
    assert result["exception_allowed"] is True
    assert "question_quality_report_failed" in result["trigger_codes"]
    assert "needs_review" in result["trigger_codes"]
    assert "template_fallback" in result["trigger_codes"]


@pytest.mark.parametrize(
    ("check_name", "expected_trigger"),
    [
        ("ksa_grounded", "ksa_grounded"),
        ("job_specific_context", "job_specific_context"),
        ("official_sample_format", "official_sample_format"),
        ("focus_scenario", "focus_scenario_coherence"),
        ("standardized_task_conditions", "standardized_task_conditions"),
        ("ksa_measurement_task", "ksa_measurement_task"),
        ("main_question_method_shape", "main_question_method_shape"),
    ],
)
def test_derive_quality_control_evidence_gate_failures_escalate(
    check_name: str,
    expected_trigger: str,
) -> None:
    strategy = _strategy(checks={check_name: False, "blind_hiring_safe": True})

    result = derive_quality_control(strategy)

    assert result["review_required"] is True
    assert result["escalation_required"] is True
    assert result["exception_allowed"] is True
    assert expected_trigger in result["trigger_codes"]


def test_derive_quality_control_blind_failure_forbids_exception() -> None:
    strategy = _strategy(checks={"blind_hiring_safe": False})

    result = derive_quality_control(strategy)

    assert result["review_required"] is True
    assert result["escalation_required"] is True
    assert result["exception_allowed"] is False
    assert "blind_hiring_safe" in result["trigger_codes"]


def test_derive_quality_control_reads_runtime_orchestration_degradation() -> None:
    strategy = _strategy()
    strategy["question_quality_orchestration"] = {
        "status": "needs_review",
        "repair_error_count": 2,
        "operational_warnings": ["fallback_adjustment_degraded"],
    }

    result = derive_quality_control(strategy)

    assert result["review_required"] is True
    assert result["escalation_required"] is True
    assert "runtime_orchestration_needs_review" in result["trigger_codes"]
    assert "runtime_repair_error" in result["trigger_codes"]


def test_derive_quality_control_reads_report_level_issue_and_review_flags() -> None:
    strategy = {
        "question_quality_report": {
            "passed": True,
            "needs_review": True,
            "issues": ["legal_or_bias_risk"],
            "items": [],
        }
    }

    result = derive_quality_control(strategy)

    assert result["review_required"] is True
    assert result["escalation_required"] is True
    assert result["exception_allowed"] is False
    assert result["trigger_codes"] == ["needs_review", "blind_hiring_safe"]


def test_derive_quality_control_is_conservative_for_missing_report_and_blockers() -> None:
    missing = derive_quality_control({})
    blocked = derive_quality_control(_strategy(), coverage_blockers=[{"type": "unit_name_only"}])

    assert missing["review_required"] is True
    assert missing["escalation_required"] is False
    assert missing["trigger_codes"] == ["missing_question_quality_report"]
    assert blocked["review_required"] is True
    assert blocked["escalation_required"] is True
    assert "coverage_blocker" in blocked["trigger_codes"]


def test_sanitize_feedback_payload_normalizes_and_deduplicates() -> None:
    result = sanitize_feedback_payload(
        {
            "run_id": " run-1 ",
            "question_hash": " abc123 ",
            "verdict": " NEEDS_EDIT ",
            "issue_codes": ["too_generic", " TOO_GENERIC ", "unnatural_wording"],
            "note": "  직무 상황을 구체화하세요.  ",
            "reviewer_ref": " reviewer-7 ",
            "question_text": "  일반적인 경험을 말해 주세요.  ",
            "ncs_code": " 0202030201_25v3 ",
            "method": " 경험면접 ",
            "question_index": 7,
            "review_token": " review-run-token ",
            "expected_review_id": 42,
        }
    )

    assert result == {
        "run_id": "run-1",
        "question_hash": "abc123",
        "verdict": "needs_edit",
        "issue_codes": ["too_generic", "unnatural_wording"],
        "note": "직무 상황을 구체화하세요.",
        "reviewer_ref": "reviewer-7",
        "question_text": "일반적인 경험을 말해 주세요.",
        "ncs_code": "0202030201_25v3",
        "method": "경험면접",
        "question_index": 7,
        "review_token": "review-run-token",
        "expected_review_id": 42,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"openai_api_key": "sk-secret"},
        {"이력서": "경력 내용"},
        {"candidate_answer": "지원자 응답"},
        {"unapproved_metadata": "x"},
    ],
)
def test_sanitize_feedback_payload_rejects_sensitive_or_extra_fields(extra: dict) -> None:
    payload = {
        "run_id": "run-1",
        "question_hash": "hash",
        "verdict": "reject",
        "issue_codes": ["too_generic"],
        **extra,
    }

    with pytest.raises(ValueError):
        sanitize_feedback_payload(payload)


def test_sanitize_feedback_payload_rejects_invalid_values_and_length() -> None:
    base = {
        "run_id": "run-1",
        "question_hash": "hash",
        "verdict": "reject",
        "issue_codes": ["too_generic"],
    }
    with pytest.raises(ValueError, match="verdict"):
        sanitize_feedback_payload({**base, "verdict": "maybe"})
    with pytest.raises(ValueError, match="unsupported"):
        sanitize_feedback_payload({**base, "issue_codes": ["not_allowed"]})
    with pytest.raises(ValueError, match="500"):
        sanitize_feedback_payload({**base, "note": "x" * 501})
    with pytest.raises(ValueError, match="80"):
        sanitize_feedback_payload({**base, "reviewer_ref": "r" * 81})
    with pytest.raises(ValueError, match="128"):
        sanitize_feedback_payload({**base, "review_token": "t" * 129})
    with pytest.raises(ValueError, match="1600"):
        sanitize_feedback_payload({**base, "question_text": "q" * 1601})
    with pytest.raises(ValueError, match="64"):
        sanitize_feedback_payload({**base, "ncs_code": "n" * 65})
    with pytest.raises(ValueError, match="64"):
        sanitize_feedback_payload({**base, "method": "m" * 65})
    with pytest.raises(ValueError, match="between 1 and 500"):
        sanitize_feedback_payload({**base, "question_index": 0})
    with pytest.raises(ValueError, match="integer"):
        sanitize_feedback_payload({**base, "question_index": True})
    with pytest.raises(ValueError, match="integer"):
        sanitize_feedback_payload({**base, "expected_review_id": True})
    assert sanitize_feedback_payload({**base, "expected_review_id": 0})["expected_review_id"] == 0
    with pytest.raises(ValueError, match="zero or greater"):
        sanitize_feedback_payload({**base, "expected_review_id": -1})


@pytest.mark.parametrize(
    "field",
    ["note", "question_text", "reviewer_ref"],
)
def test_sanitize_feedback_payload_rejects_api_key_like_content_in_allowed_fields(field: str) -> None:
    payload = {
        "run_id": "run-1",
        "question_hash": "hash",
        "verdict": "reject",
        "issue_codes": ["too_generic"],
        field: "accidentally included sk-proj-1234567890abcdef",
    }

    with pytest.raises(ValueError, match="API-key-like"):
        sanitize_feedback_payload(payload)


def test_sanitize_feedback_payload_accepts_machine_token_with_sk_like_substring() -> None:
    result = sanitize_feedback_payload(
        {
            "run_id": "run-1",
            "question_hash": "hash",
            "verdict": "approve",
            "issue_codes": [],
            "review_token": "qqt_random-sk-1234567890abcdef-fragment",
        }
    )

    assert result["review_token"] == "qqt_random-sk-1234567890abcdef-fragment"


def test_feedback_prompt_context_uses_only_negative_non_golden_matching_events() -> None:
    events = [
        {
            "ncs_code": "A",
            "verdict": "approve",
            "question": "승인된 질문",
            "issue_codes": [],
        },
        {
            "ncs_code": "A",
            "verdict": "reject",
            "question_text": "일반적인 경험을 말해 주세요.",
            "issue_codes": ["too_generic", "duplicate_intent"],
        },
        {
            "ncs_code": "A",
            "verdict": "needs_edit",
            "question": "우수하지만 수정으로 잘못 저장된 질문",
            "issue_codes": ["excellent_golden_candidate"],
        },
        {
            "ncs_code": "B",
            "verdict": "reject",
            "question": "다른 NCS 질문",
            "issue_codes": ["wrong_ncs_alignment"],
        },
        {
            "ncs_code": "A",
            "verdict": "needs_edit",
            "question": "어색한 두 번째 질문",
            "issue_codes": ["unnatural_wording"],
        },
    ]

    context = feedback_prompt_context(events, "A", max_items=2)

    assert "반복 금지 질문" in context
    assert "일반적인 경험을 말해 주세요." in context
    assert "too_generic" in context
    assert "어색한 두 번째 질문" in context
    assert "승인된 질문" not in context
    assert "우수하지만" not in context
    assert "다른 NCS 질문" not in context


def test_feedback_prompt_context_returns_empty_when_nothing_is_actionable() -> None:
    assert feedback_prompt_context([], "A") == ""
    assert feedback_prompt_context([{"verdict": "approve", "question": "좋은 질문"}], "A") == ""
    assert feedback_prompt_context([{"verdict": "reject", "question": "질문"}], "A", max_items=0) == ""


def test_feedback_prompt_context_turns_ksa_and_method_failures_into_specific_regeneration_rules() -> None:
    context = feedback_prompt_context(
        [
            {
                "ncs_code": "A",
                "verdict": "needs_edit",
                "question": "능력과 관련된 경험을 말씀해 주세요.",
                "issue_codes": ["missing_ksa_evidence", "method_task_mismatch"],
            }
        ],
        "A",
    )

    assert "K는 판단 근거·적용 범위·예외" in context
    assert "S는 수행 단계·조치·산출물·품질 확인" in context
    assert "A는 압박·상충 요구 속 선택 행동" in context
    assert "경험은 상황·본인 행동·결과" in context
    assert "발표·토론·인바스켓" in context
    assert "해당 문제를 수정하기" not in context


def test_aggregate_quality_metrics_counts_rates_and_unique_issues_per_review() -> None:
    runs = [
        {"escalation_required": True},
        {"quality_control": {"escalation_required": False}},
        _strategy(checks={"ksa_grounded": False}),
        "ignored",
    ]
    reviews = [
        {"verdict": "approve", "issue_codes": ["excellent_golden_candidate"]},
        {"verdict": "reject", "issue_codes": ["too_generic", "too_generic"]},
        {"verdict": "needs_edit", "issue_codes": ["too_generic", "unnatural_wording"]},
        {"verdict": "unknown", "issue_codes": ["too_generic"]},
    ]

    metrics = aggregate_quality_metrics(runs, reviews)

    assert metrics["approval_rate"] == pytest.approx(1 / 3)
    assert metrics["rejection_rate"] == pytest.approx(1 / 3)
    assert metrics["escalation_rate"] == pytest.approx(2 / 3)
    assert metrics["issue_counts"] == {
        "excellent_golden_candidate": 1,
        "too_generic": 2,
        "unnatural_wording": 1,
    }


def test_aggregate_quality_metrics_has_safe_zero_denominators() -> None:
    assert aggregate_quality_metrics([], []) == {
        "approval_rate": 0.0,
        "rejection_rate": 0.0,
        "escalation_rate": 0.0,
        "issue_counts": {},
    }
