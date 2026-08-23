"""Deterministic, provider-free fallback for institution interview questions.

This module is deliberately small and side-effect free.  It is used only after
the configured model providers have failed, and it never performs network,
filesystem, environment, or credential work.  Official KSA evidence is a hard
precondition: returning no questions is safer than silently inventing a
generic interview question that appears to be NCS-grounded.
"""

from __future__ import annotations

from typing import Any

from app.services.jd_strategy import (
    _build_interview_by_competency_from_questions,
    _build_ncs_code_template_fallback_question,
    _canonical_interview_method_for_prompt,
    normalize_question_dedup_key,
    _planned_question_sequence_for_prompt,
)
from app.services.question_surface import stable_ksa_evidence_id


_SOURCE = "server_ksa_fallback"
_DEFAULT_METHOD = "경험면접"
_SAFE_FAILURE_CODES = frozenset(
    {
        "fallback_input_invalid",
        "fallback_question_plan_empty",
        "fallback_question_plan_count_mismatch",
        "fallback_ncs_match_unavailable",
        "fallback_official_ksa_unavailable",
        "fallback_evidence_assignment_failed",
        "fallback_question_build_failed",
    }
)
_EXTRA_FOLLOW_UPS = (
    "방금 말씀하신 판단에서 가장 큰 위험은 무엇이었으며, 그 위험을 어떻게 통제하셨습니까?",
    "앞서 언급한 결과를 문서·수치·기록 중 어떤 근거로 확인할 수 있습니까?",
)


def _clean_text(value: Any, *, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _empty_result(
    *,
    failure_code: str,
    ncs_matches: list[dict[str, Any]] | None,
    requested_count: int = 0,
) -> dict[str, Any]:
    """Return a stable fail-closed object without reflecting input or errors."""

    safe_code = (
        failure_code
        if failure_code in _SAFE_FAILURE_CODES
        else "fallback_question_build_failed"
    )
    return {
        "interview_questions": [],
        "interview_by_competency": [],
        "ncs_link": _ncs_links(ncs_matches),
        "question_count": 0,
        "requested_question_count": max(0, int(requested_count or 0)),
        "question_source": _SOURCE,
        "generation_mode": _SOURCE,
        "generation_provider": _SOURCE,
        "question_generation_policy": "official_ksa_deterministic_fail_closed",
        "provider_fallback_used": True,
        "degraded": True,
        "human_review_required": True,
        "question_release_status": "human_review_required",
        "fallback_generated": False,
        "fallback_failure_code": safe_code,
    }


def _ncs_links(
    ncs_matches: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in ncs_matches or []:
        if not isinstance(raw, dict):
            continue
        code = _clean_text(raw.get("ncsClCd"), limit=80)
        if not code or code in seen:
            continue
        seen.add(code)
        links.append(
            {
                "ncsClCd": code,
                "compeUnitName": _clean_text(raw.get("compeUnitName"), limit=160),
                "why": "확정된 NCS 능력단위와 공식 KSA 근거를 사용한 서버 폴백",
            }
        )
        if len(links) >= 6:
            break
    return links


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _requested_count(
    question_plan: dict[str, Any] | None,
    target_count: int | None,
) -> int:
    explicit = _positive_int(target_count)
    if target_count is not None:
        return explicit
    if not isinstance(question_plan, dict):
        return 0
    planned = _positive_int(question_plan.get("total_main_count"))
    if planned:
        return planned
    sequence = question_plan.get("question_sequence")
    if isinstance(sequence, list):
        return len([row for row in sequence if isinstance(row, dict)])
    return 0


def _official_ksa_rows(
    ncs_ksa: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in ncs_ksa or []:
        if not isinstance(raw, dict):
            continue
        code = _clean_text(raw.get("ncsClCd") or raw.get("unit_code"), limit=80)
        factor = _clean_text(raw.get("factorName") or raw.get("factor_name"), limit=240)
        if not code or not factor:
            continue
        row = dict(raw)
        row["ncsClCd"] = code
        row["factorName"] = factor
        rows.append(row)
    return rows


def _evidence_row(
    rows: list[dict[str, Any]],
    *,
    ncs_code: str,
    evidence_id: str,
    factor_name: str,
) -> dict[str, Any] | None:
    code = _clean_text(ncs_code, limit=80)
    evidence = _clean_text(evidence_id, limit=80)
    factor = _clean_text(factor_name, limit=240)
    if not code or not evidence or not factor:
        return None
    for row in rows:
        if _clean_text(row.get("ncsClCd"), limit=80) != code:
            continue
        if stable_ksa_evidence_id(row) != evidence:
            continue
        if _clean_text(row.get("factorName"), limit=240) != factor:
            continue
        return dict(row)
    return None


def _follow_ups_for_count(question: dict[str, Any], count: int) -> list[str]:
    desired = max(0, min(5, int(count or 0)))
    existing = [
        _clean_text(value, limit=500)
        for value in (question.get("follow_ups") or [])
        if _clean_text(value, limit=500)
    ]
    for value in _EXTRA_FOLLOW_UPS:
        if len(existing) >= desired:
            break
        if value not in existing:
            existing.append(value)
    return existing[:desired]


def _is_avoided_question(question: Any, avoided: list[str]) -> bool:
    text = _clean_text(question, limit=2000)
    if not text:
        return True
    key = normalize_question_dedup_key(text)
    for previous in avoided:
        previous_text = _clean_text(previous, limit=2000)
        if not previous_text:
            continue
        if key and key == normalize_question_dedup_key(previous_text):
            return True
        # The fallback renderer deliberately rotates the observable decision
        # angle while keeping the same official KSA focus. A fuzzy similarity
        # check here would reject those valid variations and advance by a full
        # variation cycle, producing the exact question we were trying to
        # avoid. Exact normalized-key matching is the contract for the
        # user-visible "same question" history guard; semantic diversity is
        # enforced separately by the quality gate.
    return False


def _unit_for_code(
    ncs_matches: list[dict[str, Any]],
    *,
    ncs_code: str,
    planned: dict[str, Any],
) -> dict[str, Any] | None:
    code = _clean_text(ncs_code, limit=80)
    for raw in ncs_matches:
        if not isinstance(raw, dict):
            continue
        if _clean_text(raw.get("ncsClCd"), limit=80) != code:
            continue
        unit = dict(raw)
        unit["ncsClCd"] = code
        if not _clean_text(unit.get("compeUnitName"), limit=160):
            unit["compeUnitName"] = _clean_text(
                planned.get("compeUnitName"), limit=160
            )
        return unit
    return None


def _build_result(
    *,
    question_plan: dict[str, Any],
    interview_methods: list[str],
    ncs_matches: list[dict[str, Any]],
    official_ksa: list[dict[str, Any]],
    requested_count: int,
    presentation_material_text: str = "",
    job_context_text: str = "",
    generation_offset: int = 0,
    avoid_questions: list[str] | None = None,
) -> dict[str, Any]:
    raw_sequence = question_plan.get("question_sequence")
    if not isinstance(raw_sequence, list) or not raw_sequence:
        return _empty_result(
            failure_code="fallback_question_plan_empty",
            ncs_matches=ncs_matches,
            requested_count=requested_count,
        )
    sequence_count = len([row for row in raw_sequence if isinstance(row, dict)])
    if sequence_count != requested_count:
        return _empty_result(
            failure_code="fallback_question_plan_count_mismatch",
            ncs_matches=ncs_matches,
            requested_count=requested_count,
        )

    methods = [
        _canonical_interview_method_for_prompt(value)
        for value in interview_methods
        if _clean_text(value, limit=80)
    ] or [_DEFAULT_METHOD]
    planned_sequence = _planned_question_sequence_for_prompt(
        question_plan,
        methods,
        requested_count,
        ncs_matches=ncs_matches,
        ncs_ksa=official_ksa,
    )
    if len(planned_sequence) != requested_count:
        return _empty_result(
            failure_code="fallback_question_plan_count_mismatch",
            ncs_matches=ncs_matches,
            requested_count=requested_count,
        )

    questions: list[dict[str, Any]] = []
    avoided_questions = [
        _clean_text(value, limit=2000)
        for value in (avoid_questions or [])
        if _clean_text(value, limit=2000)
    ]
    base_offset = max(0, int(generation_offset or 0))
    for offset, planned in enumerate(planned_sequence):
        slot_index = base_offset + offset
        ncs_code = _clean_text(planned.get("ncsClCd"), limit=80)
        evidence_id = _clean_text(planned.get("evidence_id"), limit=80)
        factor_name = _clean_text(planned.get("required_factorName"), limit=240)
        evidence = _evidence_row(
            official_ksa,
            ncs_code=ncs_code,
            evidence_id=evidence_id,
            factor_name=factor_name,
        )
        if evidence is None:
            return _empty_result(
                failure_code="fallback_evidence_assignment_failed",
                ncs_matches=ncs_matches,
                requested_count=requested_count,
            )
        unit = _unit_for_code(
            ncs_matches,
            ncs_code=ncs_code,
            planned=planned,
        )
        if unit is None:
            return _empty_result(
                failure_code="fallback_ncs_match_unavailable",
                ncs_matches=ncs_matches,
                requested_count=requested_count,
            )

        method = _canonical_interview_method_for_prompt(
            planned.get("type") or methods[offset % len(methods)]
        ) or _DEFAULT_METHOD
        attempts = 0
        while True:
            question = _build_ncs_code_template_fallback_question(
                unit=unit,
                comp_name=(
                    _clean_text(unit.get("compeUnitName"), limit=160)
                    or _clean_text(planned.get("compeUnitName"), limit=160)
                    or "핵심 직무"
                ),
                ncs_code=ncs_code,
                ksa_terms=[factor_name],
                evidence_terms=[factor_name],
                evidence_rows=[evidence],
                index=slot_index,
                method_override=method,
                case_slot_id=f"server-fallback-{slot_index + 1}",
                case_slot_signature=f"server-fallback:{slot_index}:{ncs_code}:{evidence_id}:{method}",
                presentation_material_text=(
                    presentation_material_text if method == "발표면접" else ""
                ),
                job_context_text=job_context_text,
            )
            candidate_text = _clean_text(question.get("question"), limit=2000)
            already_used = [
                _clean_text(row.get("question"), limit=2000)
                for row in questions
                if isinstance(row, dict)
            ]
            if not _is_avoided_question(candidate_text, [*avoided_questions, *already_used]):
                break
            # Each fallback renderer rotates its observable situation by slot.
            # Advance until the candidate clears both the caller's history and
            # questions already assembled for this request.  The bound keeps a
            # hostile, very large history from consuming the whole request.
            attempts += 1
            if attempts >= 25:
                break
            slot_index += 1
        if not _clean_text(question.get("question"), limit=2000):
            return _empty_result(
                failure_code="fallback_question_build_failed",
                ncs_matches=ncs_matches,
                requested_count=requested_count,
            )
        follow_up_count = _positive_int(planned.get("follow_up_count"))
        if planned.get("follow_up_count") in {0, "0"}:
            follow_up_count = 0
        elif not follow_up_count:
            follow_up_count = 3
        question["follow_ups"] = _follow_ups_for_count(question, follow_up_count)
        question["question_type"] = method
        question["type"] = method
        question["method"] = method
        question["question_source"] = _SOURCE
        question["provider_fallback_used"] = True
        question["degraded"] = True
        question["human_review_required"] = True
        question["model_question_preserved"] = False
        question["question_evidence_id"] = evidence_id
        question["question_evidence_required"] = True
        question["question_focus"] = factor_name
        question["question_focus_source"] = "official_ksa"
        question["ksa_refs"] = [factor_name]
        question["question_variation_index"] = slot_index
        questions.append(question)

    if len(questions) != requested_count:
        return _empty_result(
            failure_code="fallback_question_build_failed",
            ncs_matches=ncs_matches,
            requested_count=requested_count,
        )

    return {
        "interview_questions": questions,
        "interview_by_competency": _build_interview_by_competency_from_questions(
            questions
        ),
        "ncs_link": _ncs_links(ncs_matches),
        "question_count": len(questions),
        "requested_question_count": requested_count,
        "question_source": _SOURCE,
        "generation_mode": _SOURCE,
        "generation_provider": _SOURCE,
        "provider_generation_model": "",
        "question_generation_policy": "official_ksa_deterministic_fail_closed",
        "provider_fallback_used": True,
        "degraded": True,
        "human_review_required": True,
        "question_release_status": "human_review_required",
        "fallback_generated": True,
        "fallback_failure_code": "",
        "ncs_ksa_available": True,
    }


def build_server_ksa_fallback_strategy(
    *,
    question_plan: dict[str, Any] | None,
    interview_methods: list[str] | None,
    ncs_matches: list[dict[str, Any]] | None,
    ncs_ksa: list[dict[str, Any]] | None,
    target_count: int | None = None,
    presentation_material_text: str = "",
    job_context_text: str = "",
    generation_offset: int | None = None,
    avoid_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Build an exact-size interview strategy without contacting a provider.

    The function never raises a data- or provider-derived exception to its
    caller.  Failure is represented by an empty, degraded result containing a
    stable machine code; input text and exception details are never reflected.
    """

    requested_count = _requested_count(question_plan, target_count)
    safe_matches = [
        dict(row) for row in (ncs_matches or []) if isinstance(row, dict)
    ]
    if not isinstance(question_plan, dict) or requested_count < 1:
        return _empty_result(
            failure_code="fallback_input_invalid",
            ncs_matches=safe_matches,
            requested_count=requested_count,
        )
    official_ksa = _official_ksa_rows(ncs_ksa)
    if not official_ksa:
        return _empty_result(
            failure_code="fallback_official_ksa_unavailable",
            ncs_matches=safe_matches,
            requested_count=requested_count,
        )
    try:
        return _build_result(
            question_plan=dict(question_plan),
            interview_methods=[
                str(value) for value in (interview_methods or []) if str(value).strip()
            ],
            ncs_matches=safe_matches,
            official_ksa=official_ksa,
            requested_count=requested_count,
            presentation_material_text=str(presentation_material_text or "").strip()[:6000],
            job_context_text=str(job_context_text or "").strip()[:6000],
            generation_offset=max(0, int(generation_offset or 0)),
            avoid_questions=[
                _clean_text(value, limit=2000)
                for value in (avoid_questions or [])
                if _clean_text(value, limit=2000)
            ],
        )
    except Exception:
        return _empty_result(
            failure_code="fallback_question_build_failed",
            ncs_matches=safe_matches,
            requested_count=requested_count,
        )


__all__ = ["build_server_ksa_fallback_strategy"]
