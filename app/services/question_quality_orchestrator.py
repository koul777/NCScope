from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable


RUNTIME_QUESTION_ORCHESTRATION_POLICY = "ncs_ksa_runtime_orchestration_v6"


_METHOD_OBSERVATION_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "경험면접": (
        ("상황", "사례", "과제"),
        ("본인", "역할", "행동", "조치", "선택"),
        ("결과", "성과", "영향", "학습", "개선"),
    ),
    "상황면접": (
        ("상황", "동시", "충돌", "제약", "문제"),
        ("판단", "기준", "근거"),
        ("순서", "행동", "조치", "보고", "실행", "위험"),
    ),
    "발표면접": (
        ("발표", "준비시간", "질의응답"),
        ("자료", "진단", "분석", "원인"),
        ("대안", "실행", "성과지표", "우선순위"),
    ),
    "토론면접": (
        ("토론", "입장발표", "입장", "충돌"),
        ("반대", "경청", "조정", "근거"),
        ("합의", "합의안", "실행"),
    ),
    "인바스켓면접": (
        ("인바스켓", "제한시간", "동시", "문서", "요청"),
        ("우선순위", "분류", "보류"),
        ("보고", "위임", "직접처리", "첫", "조치"),
    ),
    "직무지식면접": (
        ("절차", "기준", "규정", "근거"),
        ("적용", "예외", "판단", "설명"),
        ("산출물", "품질", "오류", "점검", "예방"),
    ),
    "창의적 문제해결력면접": (
        ("문제", "정의", "미래예측", "원인", "가설"),
        ("대안", "창의", "검증", "실현가능성"),
        ("의사결정", "실행", "성과지표", "리스크"),
    ),
}


_SHALLOW_KSA_RESTATEMENT_RE = re.compile(
    r"(?:관련(?:하여|한|된|해)?|에\s*관한|에\s*대한)\s*"
    r"(?:본인의\s*)?(?:실제\s*)?(?:경험|사례)\s*"
    r"(?:(?:이|가)?\s*(?:있다면|있으시면|있으십니까|있습니까)\s*)?"
    r"(?:(?:에\s*대해|을|를)?\s*(?:구체적으로\s*)?)?"
    r"(?:말씀(?:해|하여)?\s*주세요|말(?:해|하여)?\s*주세요|"
    r"이야기(?:해|하여)?\s*주세요|공유(?:해|하여)?\s*주세요|"
    r"설명(?:해|하여)?\s*주세요|소개(?:해|하여)?\s*주세요|"
    r"서술(?:해|하여)?\s*주세요|답변(?:해|하여)?\s*주세요|"
    r"들려\s*주세요|"
    r"들어\s*(?:말씀|설명)(?:해|하여)?\s*주세요|"
    r"있으십니까|있습니까)",
    re.IGNORECASE,
)
_BARE_EXPERIENCE_RE = re.compile(
    r"^.{0,70}(?:실제\s*)?경험(?:이)?\s*(?:있으십니까|있습니까)\??\s*"
    r"(?:말씀(?:해|하여)\s*주세요\.?)?$",
    re.IGNORECASE,
)

# Observation keywords alone do not make an interview task.  A malformed
# model response such as "상황 본인 행동 결과" used to satisfy the method
# groups even though it neither asks a question nor gives the candidate an
# instruction.  Require an explicit response act instead of accepting keyword
# stuffing or heading-like fragments.
_RESPONSE_ELICITATION_RE = re.compile(
    r"(?:말씀|설명|제시|발표|작성|제출|답변|선택|판단|분류|도출|정리|수행|"
    r"검토|분석|행동|조치|밝혀|포함|답)(?:을|를)?\s*"
    r"(?:해|하여|하고|한\s*뒤|한\s*후|하되|하신\s*뒤|하신\s*후)?\s*"
    r"(?:주세요|주십시오|보세요|하십시오|하세요|하시겠습니까|하겠습니까)|"
    r"(?:무엇|어떻게|왜|어떤|어느|얼마)[^.!?]{0,180}"
    r"(?:하시겠습니까|하겠습니까|입니까|인가요|합니까|할까요)",
    re.IGNORECASE,
)

_SCENARIO_SIGNATURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deadline_missing_evidence", ("마감시간", "자료일부", "자료 일부", "누락")),
    ("stakeholder_rule_conflict", ("관련부서", "관련 부서", "현장처리기준", "현장 처리 기준")),
    ("exception_quality_risk", ("예외요청", "예외 요청", "품질오류", "품질 오류")),
    ("resource_constraint", ("인력과시간", "인력과 시간", "우선순위를다시", "우선순위를 다시")),
    ("speed_error_prevention", ("처리속도", "처리 속도", "오류예방", "오류 예방")),
    ("procedure_insufficient", ("기존절차", "기존 절차", "추가확인", "추가 확인")),
    ("handover_rule_mismatch", ("인수인계", "최신규정", "최신 규정")),
    ("complaint_approval_delay", ("민원확대", "민원 확대", "승인지연", "승인 지연")),
    ("security_urgent_request", ("안전보안", "안전·보안", "긴급요청", "긴급 요청")),
    ("vendor_repeat_defect", ("협력업체", "반복오류", "반복 오류")),
    ("metric_evidence_gap", ("성과지표", "원인자료", "원인 자료", "불충분")),
    ("workload_surge", ("업무량", "급증", "처리순서", "처리 순서")),
    ("system_outage_manual_record", ("시스템장애", "시스템 장애", "수기기록", "수기 기록")),
    ("rule_change_mixed_form", ("규정변경", "규정 변경", "구버전", "양식")),
    ("approval_change_notice_gap", ("승인기준", "승인 기준", "변경공지", "변경 공지", "전달되지")),
    ("stakeholder_success_risk", ("성공기준", "성공 기준", "허용위험", "허용 위험")),
    ("ineffective_initial_action", ("초기조치", "초기 조치", "재설계")),
    ("low_trust_cross_validation", ("신뢰성", "교차검증")),
    ("short_long_term_tradeoff", ("단기성과", "단기 성과", "장기재발", "장기 재발")),
    ("requester_urgent_pressure", ("요청부서", "요청 부서", "즉시처리", "즉시 처리")),
    ("reviewer_evidence_pressure", ("검토담당자", "검토 담당자", "증빙완결", "증빙 완결")),
    ("service_delay_alternative", ("서비스담당자", "서비스 담당자", "지연안내", "지연 안내", "대안제시", "대안 제시")),
    ("quality_verification_hold", ("품질담당자", "품질 담당자", "추가검증", "추가 검증", "처리보류", "처리 보류")),
    ("decision_cost_pressure", ("의사결정자", "비용최소화", "비용 최소화")),
    ("external_liability_dispute", ("외부협력자", "외부 협력자", "오류책임", "오류 책임", "이견")),
    ("new_owner_support", ("신규담당자", "신규 담당자", "지원요청", "지원 요청")),
    ("approver_absent", ("승인권자", "자리를비운", "자리를 비운")),
)


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def _question_text(item: dict[str, Any]) -> str:
    return str((item or {}).get("question") or "").strip()


def _question_focus(item: dict[str, Any]) -> str:
    focus = str((item or {}).get("question_focus") or "").strip()
    if focus:
        return focus
    refs = (item or {}).get("ksa_refs")
    if isinstance(refs, list) and refs:
        return str(refs[0] or "").strip()
    return ""


def _operational_focus(item: dict[str, Any]) -> str:
    """Return the action noun used when an official factor has a type suffix."""
    focus = _question_focus(item)
    candidate = re.sub(
        r"\s*(?:관련\s*)?(?:능력|기술|스킬|지식)\s*$",
        "",
        focus,
    ).strip()
    return candidate if len(_compact(candidate)) >= 2 else focus


def _focus_visible(item: dict[str, Any], question: str) -> bool:
    focus = _question_focus(item)
    if not focus:
        return False
    compact_question = _compact(question)
    compact_focus = _compact(focus)
    compact_operational_focus = _compact(_operational_focus(item))
    if (
        compact_focus
        and compact_focus in compact_question
        or compact_operational_focus
        and compact_operational_focus in compact_question
    ):
        return True
    tokens = [
        token
        for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", focus)
        if token not in {"능력", "기술", "지식", "태도", "관련", "업무", "수행"}
    ]
    if not tokens:
        return False
    hits = sum(_compact(token) in compact_question for token in tokens)
    return hits >= (1 if len(tokens) == 1 else 2)


def _observation_group_hits(method: str, question: str) -> list[bool]:
    compact = _compact(question)
    return [
        any(_compact(marker) in compact for marker in group)
        for group in _METHOD_OBSERVATION_GROUPS.get(method, ())
    ]


def _elicits_candidate_response(question: str) -> bool:
    return bool(_RESPONSE_ELICITATION_RE.search(str(question or "").strip()))


def _has_sufficient_task_detail(question: str) -> bool:
    """Reject label-like prompts that merely append a polite request.

    Forty compact characters still allows a concise job-knowledge question,
    while requiring enough room to state a context, the evidence-producing
    action, and an outcome/constraint rather than a list of rubric words.
    """

    return len(_compact(question)) >= 40


def _generic_focus_self_report(item: dict[str, Any], question: str) -> bool:
    """Catch shallow synonyms placed immediately after the KSA label.

    A concrete task may still use words such as ``활용`` or ``경험`` later in
    the sentence.  Only the short label-adjacent form is rejected, leaving room
    for an intervening action, object, output, or decision constraint.
    """

    compact_question = _compact(question)
    focus_candidates = list(
        dict.fromkeys(
            value
            for value in (
                _compact(_question_focus(item)),
                _compact(_operational_focus(item)),
            )
            if value
        )
    )
    if not compact_question or not focus_candidates:
        return False
    for compact_focus in focus_candidates:
        focus_at = compact_question.find(compact_focus)
        if focus_at < 0:
            continue
        tail = compact_question[focus_at + len(compact_focus) : focus_at + len(compact_focus) + 45]
        if re.match(
            r"^(?:"
            r"(?:을|를|이|가|으로|로)?(?:에대해|에관해|관련하여|관련한)?"
            r"(?:직접)?(?:발휘|활용|사용|적용|수행|실천|구사|보유|보여)"
            r"(?:한|했던|하신|해본|준|주신|주었던)?(?:실제)?(?:경험|사례)"
            r"|(?:에대한|에관한)(?:실제)?(?:경험|사례)"
            r")",
            tail,
        ):
            return True
    return False


def _ksa_type(item: dict[str, Any]) -> str:
    raw = _compact((item or {}).get("question_focus_type"))
    if raw in {"k", "knowledge", "지식"} or "지식" in raw:
        return "지식"
    if raw in {"s", "skill", "skills", "기술"} or any(token in raw for token in ("기술", "스킬")):
        return "기술"
    if raw in {"a", "attitude", "태도"} or "태도" in raw:
        return "태도"
    focus = _compact(_question_focus(item))
    if any(token in focus for token in ("태도", "자세", "의지", "의식", "성실", "책임감")):
        return "태도"
    if any(token in focus for token in ("지식", "개념", "원리", "이론", "법규", "법령")):
        return "지식"
    if any(token in focus for token in ("능력", "기술", "작성", "수립", "분석", "산정", "점검", "파악")):
        return "기술"
    return ""


def _ksa_type_operationalized(item: dict[str, Any], question: str) -> bool:
    kind = _ksa_type(item)
    compact = _compact(question)
    if not kind:
        return True
    compact_focus = _compact(_question_focus(item))
    compact_operational_focus = _compact(_operational_focus(item))
    evidence_text = compact
    for focus_value in (compact_focus, compact_operational_focus):
        if focus_value:
            evidence_text = evidence_text.replace(focus_value, "")
    marker_groups = {
        "지식": (
            ("활용", "근거", "기준", "절차", "분석", "적용"),
            ("판단", "예외", "오류", "위험", "산출", "품질", "범위", "해결", "결과", "성과"),
        ),
        "기술": (
            ("발휘", "수행", "행동", "조치", "실행", "작성", "처리", "검증", "점검"),
            ("산출", "결과", "성과", "품질", "검증", "점검", "오류", "해결", "완료", "지표", "우선순위", "분류", "판단"),
        ),
        "태도": (
            ("행동", "선택", "유지", "준수", "대응", "조정", "통제", "우선", "책임"),
            ("압박", "제약", "충돌", "위험", "마감", "예외", "반대", "우선", "유지", "준수", "정확", "안전", "품질"),
        ),
    }[kind]
    if not all(any(_compact(marker) in evidence_text for marker in group) for group in marker_groups):
        return False
    if kind == "기술" and (compact_focus or compact_operational_focus):
        linked_markers = (
            "발휘", "수행", "행동", "조치", "실행", "작성", "검증", "점검", "산출", "결과", "성과", "품질",
        )
        linked_patterns = [
            re.escape(focus_value)
            + r".{0,120}(?:"
            + "|".join(re.escape(_compact(marker)) for marker in linked_markers)
            + r")"
            for focus_value in dict.fromkeys((compact_focus, compact_operational_focus))
            if focus_value
        ]
        if not any(re.search(pattern, compact) for pattern in linked_patterns):
            return False
    if kind == "태도":
        if compact_focus and re.search(re.escape(compact_focus) + r".{0,10}적용", compact):
            return False
    return True


def evaluate_ksa_measurement(item: dict[str, Any]) -> dict[str, Any]:
    """Decide whether the main task elicits observable evidence of its KSA.

    Merely repeating a factor name and asking whether the applicant has related
    experience is intentionally rejected.  The main task must include the
    observable structure of its selected interview method.
    """

    question = _question_text(item)
    method = str((item or {}).get("type") or (item or {}).get("method") or "").strip()
    group_hits = _observation_group_hits(method, question)
    shallow_restatement = bool(
        _SHALLOW_KSA_RESTATEMENT_RE.search(question)
        or _BARE_EXPERIENCE_RE.search(question)
        or _generic_focus_self_report(item, question)
    )
    checks = {
        "has_question": bool(question),
        "has_supported_method": method in _METHOD_OBSERVATION_GROUPS,
        "focus_visible": _focus_visible(item, question),
        "ksa_type_operationalized": _ksa_type_operationalized(item, question),
        "observable_task": bool(group_hits and all(group_hits)),
        "elicits_response": _elicits_candidate_response(question),
        "sufficient_task_detail": _has_sufficient_task_detail(question),
        "not_ksa_restatement": not shallow_restatement,
    }
    issues = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not issues,
        "method": method,
        "focus": _question_focus(item),
        "checks": checks,
        "issues": issues,
        "observation_group_hits": group_hits,
    }


def normalize_history_key(value: Any) -> str:
    return _compact(value)


def question_text_similarity(left: Any, right: Any) -> float:
    left_key = normalize_history_key(left)
    right_key = normalize_history_key(right)
    if not left_key or not right_key:
        return 0.0
    sequence = SequenceMatcher(None, left_key, right_key).ratio()
    if len(left_key) < 3 or len(right_key) < 3:
        return sequence
    left_grams = {left_key[index : index + 3] for index in range(len(left_key) - 2)}
    right_grams = {right_key[index : index + 3] for index in range(len(right_key) - 2)}
    union = left_grams | right_grams
    jaccard = len(left_grams & right_grams) / len(union) if union else 0.0
    return max(sequence, jaccard)


def question_scenario_signature(value: Any) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    matches = [
        name
        for name, markers in _SCENARIO_SIGNATURE_MARKERS
        if any(_compact(marker) in compact for marker in markers)
    ]
    return "+".join(matches)


def is_history_duplicate(question: Any, history: list[str], threshold: float = 0.86) -> bool:
    text = str(question or "").strip()
    key = normalize_history_key(text)
    if not key:
        return True
    for previous in history:
        previous_text = str(previous or "").strip()
        if not previous_text:
            continue
        if key == normalize_history_key(previous_text):
            return True
        current_scenario = question_scenario_signature(text)
        previous_scenario = question_scenario_signature(previous_text)
        if current_scenario != previous_scenario and (current_scenario or previous_scenario):
            continue
        if question_text_similarity(text, previous_text) >= threshold:
            return True
    return False


RepairQuestion = Callable[[dict[str, Any], int, list[str], int], dict[str, Any] | None]


def orchestrate_question_set(
    questions: list[dict[str, Any]],
    *,
    avoid_questions: list[str] | None = None,
    repair_question: RepairQuestion | None = None,
    max_repair_attempts: int = 6,
    required_repair_reasons: dict[int, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit, repair and re-audit a question set without failing the full set.

    Repair exceptions are isolated to the affected item and returned as stage
    evidence.  This prevents a second-generation repair error from turning an
    otherwise valid response into an HTTP 500.
    """

    history = [str(value or "").strip() for value in (avoid_questions or []) if str(value or "").strip()]
    accepted_texts: list[str] = []
    output: list[dict[str, Any]] = []
    item_events: list[dict[str, Any]] = []
    initial_failure_count = 0
    repaired_count = 0
    repair_error_count = 0

    for index, raw in enumerate(questions):
        original = dict(raw) if isinstance(raw, dict) else {}
        measurement = evaluate_ksa_measurement(original)
        reasons = list(measurement["issues"])
        reasons.extend(
            str(reason or "").strip()
            for reason in (required_repair_reasons or {}).get(index, [])
            if str(reason or "").strip()
        )
        if is_history_duplicate(_question_text(original), [*history, *accepted_texts]):
            reasons.append("history_duplicate")
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            initial_failure_count += 1

        selected = original
        attempts = 0
        errors: list[str] = []
        final_reasons = list(reasons)
        last_candidate_reasons: list[str] = []
        if reasons and repair_question is not None:
            for attempt in range(1, max(1, int(max_repair_attempts)) + 1):
                attempts = attempt
                try:
                    candidate = repair_question(dict(original), index, list(reasons), attempt)
                except Exception as exc:  # item-level isolation is intentional
                    repair_error_count += 1
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                if not isinstance(candidate, dict):
                    continue
                candidate_measurement = evaluate_ksa_measurement(candidate)
                candidate_reasons = list(candidate_measurement["issues"])
                if is_history_duplicate(_question_text(candidate), [*history, *accepted_texts]):
                    candidate_reasons.append("history_duplicate")
                candidate_reasons = list(dict.fromkeys(candidate_reasons))
                if candidate_reasons:
                    last_candidate_reasons = candidate_reasons
                    continue
                selected = candidate
                final_reasons = []
                last_candidate_reasons = []
                repaired_count += 1
                break
            if selected is original:
                # The returned item is still the original candidate. Preserve
                # its audit reasons instead of accidentally reporting only the
                # last discarded repair candidate's issues.
                final_reasons = list(dict.fromkeys([*reasons, "repair_exhausted"]))

        output.append(selected)
        selected_text = _question_text(selected)
        if selected_text:
            accepted_texts.append(selected_text)
        item_events.append(
            {
                "index": index + 1,
                "initial_issues": reasons,
                "repair_attempts": attempts,
                "repaired": selected is not original,
                "final_issues": final_reasons,
                "last_candidate_issues": last_candidate_reasons,
                "errors": errors,
            }
        )

    unresolved_count = sum(bool(event["final_issues"]) for event in item_events)
    metadata = {
        "policy": RUNTIME_QUESTION_ORCHESTRATION_POLICY,
        "status": "passed" if not unresolved_count else "needs_review",
        "stages": [
            {
                "name": "ksa_measurement_audit",
                "status": "passed" if not initial_failure_count else "repair_required",
                "failed_count": initial_failure_count,
            },
            {
                "name": "history_dedup_and_repair",
                "status": "passed" if not unresolved_count else "partial",
                "repaired_count": repaired_count,
                "repair_error_count": repair_error_count,
            },
            {
                "name": "final_recheck",
                "status": "passed" if not unresolved_count else "needs_review",
                "unresolved_count": unresolved_count,
            },
        ],
        "question_count": len(output),
        "history_count": len(history),
        "initial_failure_count": initial_failure_count,
        "repaired_count": repaired_count,
        "repair_error_count": repair_error_count,
        "unresolved_count": unresolved_count,
        "items": item_events,
    }
    return output, metadata
