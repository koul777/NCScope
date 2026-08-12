from __future__ import annotations

import re
from typing import Any

GENERAL_QUESTION_INTENTS = {
    "motivation",
    "job_understanding",
    "collaboration_conflict",
    "strength_weakness",
    "growth_plan",
    "public_ethics",
}

FOCUS_SCOPED_GENERAL_QUESTION_INTENTS = {
    "collaboration_conflict",
    "public_ethics",
}

QUESTION_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("motivation", ("지원동기", "지원한동기", "지원한이유", "지원하게된", "관심을갖게", "입사하고싶", "선택한이유")),
    ("job_understanding", ("직무이해", "주요업무", "업무내용", "역할이해", "기관이해", "우리기관", "해당업무")),
    ("collaboration_conflict", ("협업", "갈등", "의견차이", "대립", "소통", "조율", "조정")),
    ("strength_weakness", ("강점", "약점", "장점", "단점", "한계", "보완점")),
    ("growth_plan", ("입사후", "성장계획", "성장목표", "기여방안", "기여할수", "포부", "경력개발", "향후계획")),
    ("public_ethics", ("공공성", "윤리", "청렴", "공정성", "공정한", "공정하게", "책임감")),
    ("creative_problem", ("창의적문제해결력", "미래예측", "문제정의", "대안", "검증", "실현가능성", "의사결정")),
    ("presentation_task", ("발표과제", "발표", "진단", "대안", "성과지표", "질의응답")),
    ("discussion_task", ("토론과제", "입장발표", "반박", "합의", "충돌")),
    ("inbasket_priority", ("인바스켓", "문서", "우선순위", "보고", "위임", "직접처리")),
    ("job_knowledge", ("절차", "기준", "제출물", "예외상황", "규정", "오류")),
    ("data_analysis", ("자료", "데이터", "분석", "근거", "지표")),
    ("planning_execution", ("계획", "수립", "예산", "흐름", "실행", "일정", "자원", "우선순위")),
    ("quality_risk", ("안전", "점검", "오류", "리스크", "위험", "예방")),
    ("problem_solving", ("문제", "원인", "해결", "개선", "대안", "성과")),
    ("experience_behavior", ("경험", "사례", "상황", "본인", "행동", "결과")),
    ("situation_judgment", ("상황", "판단", "기준", "순서", "위험", "대응")),
)


def compact_question_intent_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"[^0-9a-z가-힣]+", " ", raw)
    return re.sub(r"\s+", "", raw)


def classify_question_intent(question: Any, *, unknown: str = "") -> str:
    compact = compact_question_intent_text(question)
    if not compact:
        return unknown

    explicit_task_markers = (
        ("inbasket_priority", ("인바스켓", "인바스켓과제")),
        ("creative_problem", ("창의적문제해결력과제", "창의적문제해결과제")),
        ("presentation_task", ("발표과제",)),
        ("discussion_task", ("토론과제",)),
    )
    for intent, markers in explicit_task_markers:
        if any(compact_question_intent_text(marker) in compact for marker in markers):
            return intent

    pattern_map = dict(QUESTION_INTENT_PATTERNS)
    experience_markers = pattern_map.get("experience_behavior", ())
    experience_hits = sum(
        1 for marker in experience_markers if compact_question_intent_text(marker) in compact
    )
    experience_overlap = (
        experience_hits >= 2
        and (
            compact_question_intent_text("경험") in compact
            or compact_question_intent_text("사례") in compact
        )
    )
    # A fully formed behavioural-experience prompt can legitimately contain
    # planning, data, risk, and procedure vocabulary.  Those are the evidence
    # being probed, not the interview format, so the explicit STAR-like shape
    # must win before narrower topic classifiers.
    if experience_overlap and experience_hits >= 4:
        return "experience_behavior"

    job_knowledge_markers = pattern_map.get("job_knowledge", ())
    job_knowledge_hits = sum(
        1 for marker in job_knowledge_markers if compact_question_intent_text(marker) in compact
    )
    if job_knowledge_hits >= 2:
        return "job_knowledge"

    situation_markers = pattern_map.get("situation_judgment", ())
    situation_hits = sum(
        1 for marker in situation_markers if compact_question_intent_text(marker) in compact
    )
    if situation_hits >= 3:
        return "situation_judgment"

    for intent, markers in QUESTION_INTENT_PATTERNS:
        hits = sum(1 for marker in markers if compact_question_intent_text(marker) in compact)
        if intent == "motivation" and compact_question_intent_text("선택한 이유") in compact:
            has_application_context = any(
                compact_question_intent_text(cue) in compact
                for cue in ("지원", "입사", "기관", "회사")
            )
            has_strong_motivation_marker = any(
                compact_question_intent_text(marker) in compact
                for marker in markers
                if marker != "선택한이유"
            )
            if not has_application_context and not has_strong_motivation_marker:
                continue
        required = 1 if intent in GENERAL_QUESTION_INTENTS else 2
        if intent == "collaboration_conflict" and experience_overlap:
            required = 2
        if intent == "job_understanding" and experience_overlap:
            continue
        if intent in {"inbasket_priority", "problem_solving"} and hits >= required and experience_overlap:
            return "experience_behavior"
        if hits >= required:
            return intent
    return unknown
