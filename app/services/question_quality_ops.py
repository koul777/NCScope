from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


QUALITY_POLICY_VERSION = "ncs-field-realism-v24"

ALLOWED_FEEDBACK_CODES = frozenset(
    {
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
)
# Short alias for callers that treat the values as a schema enum.
FEEDBACK_CODES = ALLOWED_FEEDBACK_CODES

_ALLOWED_VERDICTS = frozenset({"approve", "reject", "needs_edit"})
_FEEDBACK_FIELDS = frozenset(
    {
        "run_id",
        "question_hash",
        "verdict",
        "issue_codes",
        "note",
        "reviewer_ref",
        "question_text",
        "ncs_code",
        "method",
        "question_index",
        "review_token",
        "expected_review_id",
    }
)
_API_KEY_VALUE_RE = re.compile(
    r"(?:\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{8,}|\b(?:openai[_-]?)?api[_-]?key\s*[:=])",
    re.IGNORECASE,
)
_ESCALATION_CHECK_ALIASES = {
    "ksa_grounded": "ksa_grounded",
    "job_specific_context": "job_specific_context",
    "official_sample_format": "official_sample_format",
    "blind": "blind_hiring_safe",
    "blind_hiring_safe": "blind_hiring_safe",
    "legal_or_bias_risk": "blind_hiring_safe",
    "focus_scenario": "focus_scenario_coherence",
    "focus_scenario_coherence": "focus_scenario_coherence",
    "focus_scenario_mismatch": "focus_scenario_coherence",
    "decision_dilemma_quality": "focus_scenario_coherence",
    "debate_option_defensibility": "focus_scenario_coherence",
    "debate_outcome_flexibility": "focus_scenario_coherence",
    "debate_case_neutrality": "focus_scenario_coherence",
    "missing_task_conditions": "standardized_task_conditions",
    "standardized_task_conditions": "standardized_task_conditions",
    "case_materials_sufficient": "standardized_task_conditions",
    "inbasket_authority_context": "standardized_task_conditions",
    "missing_ksa_evidence": "ksa_measurement_task",
    "ksa_measurement_task": "ksa_measurement_task",
    "method_task_mismatch": "main_question_method_shape",
    "main_question_method_shape": "main_question_method_shape",
    "field_realism": "field_realism",
}
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "apikey",
        "openaiapikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
        "resume",
        "resumetext",
        "resumefile",
        "cv",
        "candidateanswer",
        "applicantanswer",
        "candidateanswers",
        "applicantanswers",
        "이력서",
        "지원자답변",
        "지원자응답",
        "응시자답변",
        "응시자응답",
    }
)
_ISSUE_GUIDANCE_KO = {
    "wrong_ncs_alignment": "NCS 직무·능력단위와 질문의 정렬을 바로잡기",
    "behavioral_not_star_like": "과거의 구체적 상황·과제·행동·결과가 드러나는 STAR 질문으로 바꾸기",
    "too_generic": "실제 직무 맥락, 제약, 판단 기준을 구체화하기",
    "duplicate_intent": "기존 질문과 같은 평가 의도나 상황 프레임을 반복하지 않기",
    "difficulty_too_low": "직무 판단과 근거를 확인할 수 있도록 난이도를 높이기",
    "difficulty_too_high": "지원 직급에서 답할 수 있는 범위로 난이도를 낮추기",
    "legal_or_bias_risk": "법적·블라인드 채용 위험이 있는 요소를 제거하기",
    "unnatural_wording": "체크리스트식 표현을 피하고 자연스러운 한국어 질문으로 다듬기",
    "focus_scenario_mismatch": "평가 초점과 질문 시나리오를 일치시키기",
    "missing_task_conditions": "응시자 지시문, 동일 시간·자료 조건과 필수 산출물을 명시하기",
    "missing_ksa_evidence": (
        "K는 판단 근거·적용 범위·예외, S는 수행 단계·조치·산출물·품질 확인, "
        "A는 압박·상충 요구 속 선택 행동과 감수한 결과가 답변에서 관찰되도록 바꾸기"
    ),
    "method_task_mismatch": (
        "경험은 상황·본인 행동·결과, 상황은 판단 기준·행동 순서, 발표·토론·인바스켓은 "
        "표준화된 자료·시간·산출물, 직무지식은 기준·적용·예외가 드러나는 과제로 바꾸기"
    ),
}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return (
        "apikey" in normalized
        or "password" in normalized
        or "accesstoken" in normalized
        or "refreshtoken" in normalized
        or "이력서" in normalized
        or (("지원자" in normalized or "응시자" in normalized) and ("답변" in normalized or "응답" in normalized))
    )


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "y", "pass", "passed"}:
            return True
        if normalized in {"false", "0", "no", "n", "fail", "failed"}:
            return False
    return None


def _positive_count(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _iter_issue_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        parts: Iterable[Any] = re.split(r"[;,|]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        parts = value
    else:
        return []
    return [str(part).strip().casefold() for part in parts if str(part).strip()]


def _has_coverage_blocker(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if not value:
            return False
        return any(_has_coverage_blocker(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return any(_has_coverage_blocker(item) for item in value)
    return bool(value)


def _template_fallback_used(strategy: Mapping[str, Any], report: Mapping[str, Any]) -> bool:
    if _bool_value(strategy.get("template_fallback_used")) is True:
        return True
    if _positive_count(strategy.get("template_fallback_questions")):
        return True

    generation_mode = str(strategy.get("generation_mode") or "").strip().casefold()
    if "template_fallback" in generation_mode:
        return True
    if any(
        "template_fallback" in str(container.get("status") or "").strip().casefold()
        for container in (strategy, report)
    ):
        return True

    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    if _positive_count(summary.get("template_fallback_questions")):
        return True

    for collection in (strategy.get("interview_questions"), report.get("items")):
        if not isinstance(collection, Sequence) or isinstance(collection, (bytes, bytearray, str)):
            continue
        for row in collection:
            if not isinstance(row, Mapping):
                continue
            source = str(row.get("question_source") or row.get("source") or "").strip().casefold()
            if source == "template_fallback":
                return True
    return False


def derive_quality_control(
    strategy: Mapping[str, Any] | None,
    coverage_blockers: Any = None,
) -> dict[str, Any]:
    """Derive conservative review controls from an existing quality report.

    The function never mutates ``strategy``. A missing or malformed report is a
    review condition, while escalation is reserved for the requested evidence,
    official-format, blind-hiring, scenario-focus, and coverage gates.
    """

    strategy_map: Mapping[str, Any] = strategy if isinstance(strategy, Mapping) else {}
    raw_report = strategy_map.get("question_quality_report")
    report: Mapping[str, Any] = raw_report if isinstance(raw_report, Mapping) else {}
    triggers: list[str] = []

    def trigger(code: str) -> None:
        if code and code not in triggers:
            triggers.append(code)

    review_required = False
    escalation_required = False
    blind_failure = False

    if not report:
        review_required = True
        trigger("missing_question_quality_report")
    else:
        passed = _bool_value(report.get("passed"))
        if passed is not True:
            review_required = True
            trigger("question_quality_report_failed")

        statuses = (
            strategy_map.get("status"),
            report.get("status"),
            (report.get("summary") or {}).get("status")
            if isinstance(report.get("summary"), Mapping)
            else None,
        )
        if any(str(status or "").strip().casefold() == "needs_review" for status in statuses):
            review_required = True
            trigger("needs_review")

        summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
        if _positive_count(summary.get("needs_review_count")):
            review_required = True
            trigger("needs_review")
        if _bool_value(report.get("needs_review")) is True or _bool_value(summary.get("needs_review")) is True:
            review_required = True
            trigger("needs_review")

        for issue_source in (report.get("issues"), summary.get("issues")):
            for issue in _iter_issue_codes(issue_source):
                canonical = _ESCALATION_CHECK_ALIASES.get(issue)
                if canonical:
                    escalation_required = True
                    review_required = True
                    trigger(canonical)
                    if canonical == "blind_hiring_safe":
                        blind_failure = True

        check_containers: list[Mapping[str, Any]] = []
        if isinstance(report.get("checks"), Mapping):
            check_containers.append(report["checks"])

        items = report.get("items")
        if isinstance(items, Sequence) and not isinstance(items, (bytes, bytearray, str)):
            for item in items:
                if not isinstance(item, Mapping):
                    review_required = True
                    trigger("needs_review")
                    continue
                if _bool_value(item.get("ready")) is False or _bool_value(item.get("needs_review")) is True:
                    review_required = True
                    trigger("needs_review")
                if isinstance(item.get("checks"), Mapping):
                    check_containers.append(item["checks"])

                for issue in _iter_issue_codes(item.get("issues")):
                    canonical = _ESCALATION_CHECK_ALIASES.get(issue)
                    if canonical:
                        escalation_required = True
                        review_required = True
                        trigger(canonical)
                        if canonical == "blind_hiring_safe":
                            blind_failure = True

        for checks in check_containers:
            for check_name, result in checks.items():
                canonical = _ESCALATION_CHECK_ALIASES.get(str(check_name).strip().casefold())
                if canonical and _bool_value(result) is False:
                    escalation_required = True
                    review_required = True
                    trigger(canonical)
                    if canonical == "blind_hiring_safe":
                        blind_failure = True

    if _template_fallback_used(strategy_map, report):
        review_required = True
        trigger("template_fallback")

    orchestration = (
        strategy_map.get("question_quality_orchestration")
        if isinstance(strategy_map.get("question_quality_orchestration"), Mapping)
        else {}
    )
    if str(orchestration.get("status") or "").strip().casefold() == "needs_review":
        review_required = True
        trigger("runtime_orchestration_needs_review")
    if _positive_count(orchestration.get("repair_error_count")):
        review_required = True
        escalation_required = True
        trigger("runtime_repair_error")

    effective_blockers = coverage_blockers
    if effective_blockers is None:
        effective_blockers = strategy_map.get("coverage_blockers")
    if _has_coverage_blocker(effective_blockers):
        review_required = True
        escalation_required = True
        trigger("coverage_blocker")

    return {
        "policy_version": QUALITY_POLICY_VERSION,
        "review_required": review_required,
        "escalation_required": escalation_required,
        "exception_allowed": not blind_failure,
        "trigger_codes": triggers,
    }


def sanitize_feedback_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return the small, non-sensitive feedback persistence shape."""

    if not isinstance(payload, Mapping):
        raise ValueError("feedback payload must be an object")

    sensitive = [str(key) for key in payload if _is_sensitive_key(key)]
    if sensitive:
        raise ValueError(f"sensitive feedback fields are not allowed: {', '.join(sensitive)}")

    # Machine-issued identifiers are validated against persisted hashes/IDs in
    # the repository.  A URL-safe random review token can legitimately contain
    # an ``sk-`` substring, so treating it as user-authored text creates a rare
    # false positive that blocks every review operation for that run.
    free_text_values = (
        value
        for key, value in payload.items()
        if str(key) not in {"review_token", "run_id", "question_hash"}
    )
    for value in free_text_values:
        values = value if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)) else [value]
        if any(isinstance(item, str) and _API_KEY_VALUE_RE.search(item) for item in values):
            raise ValueError("API-key-like content is not allowed in feedback")

    unexpected = [str(key) for key in payload if key not in _FEEDBACK_FIELDS]
    if unexpected:
        raise ValueError(f"unexpected feedback fields: {', '.join(unexpected)}")

    missing = [field for field in ("run_id", "question_hash", "verdict", "issue_codes") if field not in payload]
    if missing:
        raise ValueError(f"missing feedback fields: {', '.join(missing)}")

    run_id = payload.get("run_id")
    question_hash = payload.get("question_hash")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(question_hash, str) or not question_hash.strip():
        raise ValueError("question_hash must be a non-empty string")

    verdict = payload.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().casefold() not in _ALLOWED_VERDICTS:
        raise ValueError("verdict must be approve, reject, or needs_edit")
    normalized_verdict = verdict.strip().casefold()

    raw_codes = payload.get("issue_codes")
    if not isinstance(raw_codes, Sequence) or isinstance(raw_codes, (bytes, bytearray, str)):
        raise ValueError("issue_codes must be a list")
    issue_codes: list[str] = []
    for raw_code in raw_codes:
        if not isinstance(raw_code, str):
            raise ValueError("issue_codes must contain strings")
        code = raw_code.strip().casefold()
        if code not in ALLOWED_FEEDBACK_CODES:
            raise ValueError(f"unsupported feedback issue code: {raw_code}")
        if code not in issue_codes:
            issue_codes.append(code)

    sanitized: dict[str, Any] = {
        "run_id": run_id.strip(),
        "question_hash": question_hash.strip(),
        "verdict": normalized_verdict,
        "issue_codes": issue_codes,
    }

    for field, max_length in (("note", 500), ("reviewer_ref", 80), ("review_token", 128)):
        if field not in payload or payload.get(field) is None:
            continue
        value = payload.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        value = value.strip()
        if len(value) > max_length:
            raise ValueError(f"{field} must be at most {max_length} characters")
        sanitized[field] = value

    for field, max_length in (("question_text", 1600), ("ncs_code", 64), ("method", 64)):
        if field not in payload or payload.get(field) is None:
            continue
        value = payload.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        value = value.strip()
        if len(value) > max_length:
            raise ValueError(f"{field} must be at most {max_length} characters")
        sanitized[field] = value

    if "question_index" in payload and payload.get("question_index") is not None:
        question_index = payload.get("question_index")
        if isinstance(question_index, bool) or not isinstance(question_index, int):
            raise ValueError("question_index must be an integer")
        if not 1 <= question_index <= 500:
            raise ValueError("question_index must be between 1 and 500")
        sanitized["question_index"] = question_index

    if "expected_review_id" in payload and payload.get("expected_review_id") is not None:
        expected_review_id = payload.get("expected_review_id")
        if isinstance(expected_review_id, bool) or not isinstance(expected_review_id, int):
            raise ValueError("expected_review_id must be an integer")
        if expected_review_id < 0:
            raise ValueError("expected_review_id must be zero or greater")
        sanitized["expected_review_id"] = expected_review_id

    return sanitized


def _event_question(event: Mapping[str, Any]) -> str:
    for key in ("question", "question_text", "reviewed_question", "main_question"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()[:600]
    return ""


def _event_matches_ncs(event: Mapping[str, Any], ncs_code: str) -> bool:
    expected = str(ncs_code or "").strip()
    if not expected:
        return True
    for key in ("ncs_code", "ncsClCd", "ncs_cl_cd"):
        if key not in event:
            continue
        actual = str(event.get(key) or "").strip()
        return not actual or actual == expected
    return True


def feedback_prompt_context(
    events: Iterable[Mapping[str, Any]] | None,
    ncs_code: str,
    max_items: int = 8,
) -> str:
    """Build Korean avoidance/improvement context from negative review events."""

    try:
        limit = int(max_items)
    except (TypeError, ValueError):
        limit = 8
    if limit <= 0:
        return ""

    selected: list[tuple[str, list[str], str]] = []
    for event in events or []:
        if not isinstance(event, Mapping) or not _event_matches_ncs(event, ncs_code):
            continue
        verdict = str(event.get("verdict") or "").strip().casefold()
        if verdict not in {"reject", "needs_edit"}:
            continue
        codes = [code for code in _iter_issue_codes(event.get("issue_codes")) if code in ALLOWED_FEEDBACK_CODES]
        if "excellent_golden_candidate" in codes:
            continue
        question = _event_question(event)
        reference = str(event.get("question_hash") or "").strip()
        if not question and not reference and not codes:
            continue
        selected.append((question, codes, reference))
        if len(selected) >= limit:
            break

    if not selected:
        return ""

    code_label = str(ncs_code or "").strip() or "미지정"
    lines = [
        f"[이전 거절·수정 피드백 — NCS {code_label}]",
        "아래 질문의 문장과 평가 의도를 반복하지 말고, 지적된 문제를 반영해 새로운 직무 시나리오와 검증 초점으로 개선하세요.",
    ]
    for index, (question, codes, reference) in enumerate(selected, start=1):
        if question:
            lines.append(f"{index}. 반복 금지 질문: {question}")
        else:
            lines.append(f"{index}. 반복 금지 질문 식별자: {reference}")
        if codes:
            guidance = "; ".join(
                f"{code}({_ISSUE_GUIDANCE_KO.get(code, '해당 문제를 수정하기')})" for code in codes
            )
            lines.append(f"   개선 지시: {guidance}")
        else:
            lines.append("   개선 지시: 거절된 표현과 동일한 의도를 피하고 직무 근거를 구체화하기")
    return "\n".join(lines)


def _run_escalated(run: Mapping[str, Any]) -> bool:
    direct = _bool_value(run.get("escalation_required"))
    if direct is not None:
        return direct
    for key in ("quality_control", "question_quality_control"):
        nested = run.get(key)
        if isinstance(nested, Mapping):
            value = _bool_value(nested.get("escalation_required"))
            if value is not None:
                return value
    return bool(derive_quality_control(run).get("escalation_required"))


def aggregate_quality_metrics(
    runs: Iterable[Mapping[str, Any]] | None,
    reviews: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Aggregate rates and feedback-code counts without mutating inputs."""

    run_rows = [row for row in (runs or []) if isinstance(row, Mapping)]
    escalation_count = sum(1 for row in run_rows if _run_escalated(row))

    valid_reviews: list[Mapping[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    for review in reviews or []:
        if not isinstance(review, Mapping):
            continue
        verdict = str(review.get("verdict") or "").strip().casefold()
        if verdict not in _ALLOWED_VERDICTS:
            continue
        valid_reviews.append(review)
        seen_codes: set[str] = set()
        for code in _iter_issue_codes(review.get("issue_codes")):
            if code in ALLOWED_FEEDBACK_CODES and code not in seen_codes:
                issue_counts[code] += 1
                seen_codes.add(code)

    review_count = len(valid_reviews)
    approval_count = sum(
        1 for review in valid_reviews if str(review.get("verdict") or "").strip().casefold() == "approve"
    )
    rejection_count = sum(
        1 for review in valid_reviews if str(review.get("verdict") or "").strip().casefold() == "reject"
    )

    return {
        "approval_rate": approval_count / review_count if review_count else 0.0,
        "rejection_rate": rejection_count / review_count if review_count else 0.0,
        "escalation_rate": escalation_count / len(run_rows) if run_rows else 0.0,
        "issue_counts": dict(sorted(issue_counts.items())),
    }


__all__ = [
    "ALLOWED_FEEDBACK_CODES",
    "FEEDBACK_CODES",
    "QUALITY_POLICY_VERSION",
    "aggregate_quality_metrics",
    "derive_quality_control",
    "feedback_prompt_context",
    "sanitize_feedback_payload",
]
