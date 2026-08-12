from __future__ import annotations

import copy
import json
import os
import re
from difflib import SequenceMatcher
from typing import Any

from app.services.openai_http import post_chat_completions_with_retries
from app.services.question_intent import (
    FOCUS_SCOPED_GENERAL_QUESTION_INTENTS,
    GENERAL_QUESTION_INTENTS,
    QUESTION_INTENT_PATTERNS,
    classify_question_intent,
)
from app.services.question_surface import (
    build_question_task_frame,
    replace_official_ksa_surface,
    stable_ksa_evidence_id,
)
from app.settings import settings


_ENTRY_LEVEL_TRIGGER_RE = re.compile(
    r"(수행\s*경험|경험이\s*있다면|해본\s*경험|참여했던|담당했던|실무에서|업무를\s*수행|수립한\s*경험|운영한\s*경험)"
)
_ENTRY_LEVEL_ALREADY_RE = re.compile(r"(유사\s*사례|가정\s*상황|가정해|가정하여|가정하고)")

_DEFAULT_FOLLOW_UPS = [
    "그 상황에서 본인이 맡은 구체적인 역할과 판단 근거를 설명해 주세요.",
    "가장 어려웠던 지점은 무엇이었고 어떻게 해결했습니까?",
    "결과를 다시 평가한다면 어떤 점을 개선하시겠습니까?",
]
_DEFAULT_EVALUATION_POINTS = [
    "상황과 목표를 구조적으로 설명하는가",
    "판단 근거와 의사결정 기준이 명확한가",
    "실행 과정과 협업 방식이 구체적인가",
    "성과와 학습 내용을 사실에 기반해 제시하는가",
]

_METHOD_DEFAULT_FOLLOW_UPS = {
    "경험면접": _DEFAULT_FOLLOW_UPS,
    "상황면접": [
        "먼저 확인해야 할 사실과 기준은 무엇입니까?",
        "그 판단을 선택한 이유와 예상 위험요인은 무엇입니까?",
        "결과가 기대와 다를 때 후속 조치는 어떻게 하겠습니까?",
    ],
    "발표면접": [
        "진단에 활용할 핵심 근거자료는 무엇입니까?",
        "질의응답에서 반대 의견이 나오면 어떤 근거로 답변하겠습니까?",
        "대안 중 우선순위를 가장 높게 둘 방안과 이유는 무엇입니까?",
    ],
    "토론면접": [
        "입장발표에서 제시할 핵심 근거는 무엇입니까?",
        "반대 의견 중 수용할 수 있는 부분과 어려운 부분은 무엇입니까?",
        "최종 합의안에 반드시 포함할 기준은 무엇입니까?",
    ],
    "창의적 문제해결력면접": [
        "미래예측 관점에서 먼저 확인할 변화 신호는 무엇입니까?",
        "원인 가설과 창의적 대안은 어떻게 검증하겠습니까?",
        "실현가능성과 의사결정 기준, 리스크 보완책은 무엇입니까?",
    ],
    "인바스켓면접": [
        "여러 문서와 요청을 어떤 기준으로 분류하겠습니까?",
        "우선순위, 보고, 위임, 직접처리 판단은 어떻게 하겠습니까?",
        "처리 이후 기록과 후속 점검은 어떻게 남기겠습니까?",
    ],
    "직무지식면접": [
        "반드시 확인해야 할 절차와 기준은 무엇입니까?",
        "예외상황에서는 어떤 기준으로 판단하겠습니까?",
        "산출물 품질과 오류 예방은 어떻게 점검하겠습니까?",
    ],
}

_METHOD_DEFAULT_EVALUATION_POINTS = {
    "경험면접": _DEFAULT_EVALUATION_POINTS,
    "상황면접": ["핵심 사실 확인", "판단 기준", "행동 순서", "위험요인 인식", "이해관계자 대응"],
    "발표면접": ["자료 분석력", "논리적 구조화", "질의응답 대응", "대안의 실행가능성", "성과지표 설계"],
    "토론면접": ["입장발표 근거", "경청과 상호작용", "갈등 조정", "최종 합의안 도출"],
    "창의적 문제해결력면접": ["미래예측과 문제 정의", "창의적 사고와 대안 도출", "검증 방법", "실현가능성", "의사결정과 실행계획"],
    "인바스켓면접": ["우선순위 판단", "문서·요청 분류", "보고·위임·직접처리 판단", "시간관리"],
    "직무지식면접": ["절차·기준 이해", "직무지식 적용", "예외상황 판단", "산출물 품질"],
}

SUPPORTED_INTERVIEW_TYPES = (
    "경험면접",
    "상황면접",
    "발표면접",
    "토론면접",
    "창의적 문제해결력면접",
    "인바스켓면접",
    "직무지식면접",
)

_INTERVIEW_TYPE_ALIASES = {
    "경험": "경험면접",
    "경험형": "경험면접",
    "경험면접": "경험면접",
    "행동": "경험면접",
    "행동형": "경험면접",
    "행동면접": "경험면접",
    "행동관찰": "경험면접",
    "행동관찰면접": "경험면접",
    "behavior": "경험면접",
    "behavioral": "경험면접",
    "experience": "경험면접",
    "상황": "상황면접",
    "상황형": "상황면접",
    "상황면접": "상황면접",
    "situation": "상황면접",
    "situational": "상황면접",
    "발표": "발표면접",
    "발표형": "발표면접",
    "발표면접": "발표면접",
    "pt": "발표면접",
    "pt면접": "발표면접",
    "presentation": "발표면접",
    "토론": "토론면접",
    "토론형": "토론면접",
    "토론면접": "토론면접",
    "토의": "토론면접",
    "토의형": "토론면접",
    "토의면접": "토론면접",
    "discussion": "토론면접",
    "debate": "토론면접",
    "창의": "창의적 문제해결력면접",
    "창의형": "창의적 문제해결력면접",
    "창의적문제해결": "창의적 문제해결력면접",
    "창의적문제해결력": "창의적 문제해결력면접",
    "창의적문제해결력면접": "창의적 문제해결력면접",
    "창의적 문제해결력": "창의적 문제해결력면접",
    "창의적 문제해결력면접": "창의적 문제해결력면접",
    "creative": "창의적 문제해결력면접",
    "creative_problem_solving": "창의적 문제해결력면접",
    "problem_solving": "창의적 문제해결력면접",
    "인바스켓": "인바스켓면접",
    "인바스켓형": "인바스켓면접",
    "인바스켓면접": "인바스켓면접",
    "inbasket": "인바스켓면접",
    "in-basket": "인바스켓면접",
    "직무지식": "직무지식면접",
    "직무지식형": "직무지식면접",
    "직무지식면접": "직무지식면접",
    "지식": "직무지식면접",
    "지식형": "직무지식면접",
    "지식면접": "직무지식면접",
    "knowledge": "직무지식면접",
    "job_knowledge": "직무지식면접",
}

_BLIND_HIRING_CUE_RE = re.compile(
    r"(가족|부모|형제|배우자|자녀|나이|연령|출신\s*학교|학교명|학벌|출신\s*지역|출신지역|고향|"
    r"생년\s*월일|출생\s*(?:연도|년도|일|지)|몇\s*살|만\s*\d+\s*세|"
    r"혼인|결혼|기혼|미혼|결혼\s*여부|혼인\s*상태|임신|출산|자녀\s*계획|출산\s*계획|"
    r"외모|용모|(?:키|신장)\s*(?:가|는|를|와|및|/|,|:|：|\d)|체중|성별|종교|정치\s*성향|"
    r"병역|군필|미필|군\s*복무|복무\s*기간|전역|혈액형)"
)

_GENERAL_QUESTION_INTENTS = GENERAL_QUESTION_INTENTS
_FOCUS_SCOPED_GENERAL_QUESTION_INTENTS = FOCUS_SCOPED_GENERAL_QUESTION_INTENTS
_QUESTION_INTENT_PATTERNS = QUESTION_INTENT_PATTERNS


def _interview_type_key(value: str) -> str:
    return re.sub(r"[\s_\-./|()]+", "", str(value or "")).strip().lower()


def _canonical_interview_type(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "경험면접"
    mapped = (
        _INTERVIEW_TYPE_ALIASES.get(raw)
        or _INTERVIEW_TYPE_ALIASES.get(raw.lower())
        or _INTERVIEW_TYPE_ALIASES.get(_interview_type_key(raw))
    )
    return mapped if mapped in SUPPORTED_INTERVIEW_TYPES else "경험면접"


def _contains_blind_hiring_cue(*values: Any) -> bool:
    for value in values:
        if isinstance(value, list):
            if _contains_blind_hiring_cue(*value):
                return True
            continue
        if _BLIND_HIRING_CUE_RE.search(str(value or "")):
            return True
    return False


def _compact_question_dedupe_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"\[[^\]]+\]", " ", raw)
    raw = re.sub(r"[^0-9a-z가-힣]+", " ", raw)
    return re.sub(r"\s+", "", raw)


def _compact_question_intent_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"[^0-9a-z가-힣]+", " ", raw)
    return re.sub(r"\s+", "", raw)


def _question_intent_key(question: Any) -> str:
    return classify_question_intent(question)


def _question_scope_signature(item: dict[str, Any]) -> str:
    method = _canonical_interview_type((item or {}).get("type") or (item or {}).get("method") or "")
    method_key = _compact_question_dedupe_text(method)[:40]
    focus = str(
        (item or {}).get("question_focus")
        or (item or {}).get("focus")
        or (item or {}).get("primary_focus")
        or ""
    ).strip()
    if focus:
        return f"{method_key}|focus:{_compact_question_dedupe_text(focus)[:80]}"

    refs = (item or {}).get("ksa_refs")
    if isinstance(refs, list):
        ref_keys: list[str] = []
        seen_refs: set[str] = set()
        for ref in refs:
            key = _compact_question_dedupe_text(ref)[:80]
            if not key or key in seen_refs:
                continue
            seen_refs.add(key)
            ref_keys.append(key)
        if ref_keys:
            return f"{method_key}|focus:{ref_keys[0]}"

    ncs_code = _compact_question_dedupe_text((item or {}).get("ncsClCd"))[:40]
    competency = _compact_question_dedupe_text((item or {}).get("competency"))[:80]
    if ncs_code or competency:
        return f"{method_key}|scope:{ncs_code}|{competency}"
    return ""


def _question_has_focus_scope(item: dict[str, Any]) -> bool:
    focus = str(
        (item or {}).get("question_focus")
        or (item or {}).get("focus")
        or (item or {}).get("primary_focus")
        or ""
    ).strip()
    if focus:
        return True
    refs = (item or {}).get("ksa_refs")
    return bool(
        isinstance(refs, list)
        and any(_compact_question_dedupe_text(ref) for ref in refs)
    )


_QUESTION_SCENARIO_FRAMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("schedule_delay", ("일정지연", "납기지연", "기한지연")),
    ("data_mismatch", ("자료불일치", "데이터불일치", "기준불일치")),
    ("stakeholder_conflict", ("이해관계자충돌", "의견충돌", "이해관계자갈등")),
    ("exception", ("예외상황", "예외처리")),
    ("resource_constraint", ("자원제약", "인력부족", "예산제약")),
    ("quality_risk", ("품질리스크", "품질위험", "오류위험")),
)


def _question_scenario_signature(question: Any) -> str:
    compact = _compact_question_intent_text(question)
    if not compact:
        return ""
    matches = [
        name
        for name, markers in _QUESTION_SCENARIO_FRAMES
        if any(_compact_question_intent_text(marker) in compact for marker in markers)
    ]
    return "+".join(matches)


_QUESTION_CONTEXT_BOILERPLATE = (
    "업무에서",
    "업무중",
    "업무과정에서",
    "경험을말씀해주세요",
    "사례를구체적으로설명해주세요",
    "사례를구체적으로말씀해주세요",
    "당시상황",
    "본인행동",
    "본인의행동",
    "행동과결과",
    "행동결과",
    "해결한",
    "대응한",
    "설명해주세요",
    "말씀해주세요",
)


def _question_context_signature(item: dict[str, Any]) -> str:
    compact = _compact_question_intent_text((item or {}).get("question"))
    if not compact:
        return ""
    refs = (item or {}).get("ksa_refs")
    first_ref = str(refs[0] or "").strip() if isinstance(refs, list) and refs else ""
    focus = _compact_question_intent_text(
        (item or {}).get("question_focus")
        or first_ref
    )
    if focus:
        compact = compact.replace(focus, "")
    for phrase in _QUESTION_CONTEXT_BOILERPLATE:
        compact = compact.replace(_compact_question_intent_text(phrase), "")
    return compact


def _question_dedupe_keys(item: dict[str, Any]) -> list[str]:
    question = str((item or {}).get("question") or "").strip()
    surface_key = _compact_question_dedupe_text(question)
    if not surface_key:
        return []

    keys = [f"surface:{surface_key}"]
    intent = _question_intent_key(question)
    if not intent:
        return keys

    scope = _question_scope_signature(item)
    if intent in _GENERAL_QUESTION_INTENTS:
        if intent in _FOCUS_SCOPED_GENERAL_QUESTION_INTENTS and scope and _question_has_focus_scope(item):
            keys.append(f"intent:{intent}|{scope}")
        else:
            keys.append(f"intent:{intent}|general")
    return keys


def _question_already_seen(item: dict[str, Any], seen: set[str]) -> bool:
    keys = _question_dedupe_keys(item)
    if not keys:
        return True
    if any(key in seen for key in keys):
        return True
    seen.update(keys)
    return False


def _char_ngrams(value: str, n: int = 3) -> set[str]:
    compact = _compact_question_dedupe_text(value)
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(0, len(compact) - n + 1)}


def _question_surface_similarity(left: str, right: str) -> float:
    left_key = _compact_question_dedupe_text(left)
    right_key = _compact_question_dedupe_text(right)
    if not left_key or not right_key:
        return 0.0
    sequence_ratio = SequenceMatcher(None, left_key, right_key).ratio()
    left_grams = _char_ngrams(left_key)
    right_grams = _char_ngrams(right_key)
    if not left_grams or not right_grams:
        return sequence_ratio
    gram_ratio = len(left_grams & right_grams) / len(left_grams | right_grams)
    return max(sequence_ratio, gram_ratio)


def _question_near_duplicate_seen(item: dict[str, Any], accepted: list[dict[str, Any]]) -> bool:
    scope = _question_scope_signature(item)
    if not scope:
        return False
    question = str((item or {}).get("question") or "").strip()
    if len(_compact_question_dedupe_text(question)) < 24:
        return False
    for previous in accepted:
        if _question_scope_signature(previous) != scope:
            continue
        previous_question = str((previous or {}).get("question") or "").strip()
        scenario = _question_scenario_signature(question)
        previous_scenario = _question_scenario_signature(previous_question)
        if scenario and previous_scenario and scenario != previous_scenario:
            continue
        context = _question_context_signature(item)
        previous_context = _question_context_signature(previous)
        if (
            context
            and previous_context
            and SequenceMatcher(None, context, previous_context).ratio() < 0.45
        ):
            continue
        if _question_surface_similarity(question, previous_question) >= 0.84:
            return True
    return False


def _render_question_generation_prompt(
    ncs_lines: list[str],
    ksa_lines: list[str],
    jd_text: str,
    strengths: str,
    mode: str,
    target_count: int,
    extra_context: str,
) -> str:
    mode_hint = {
        "ncs_code_only": "NCS 코드 중심 구조화 면접",
        "diverse": "다양한 유형의 구조화 면접",
        "personalized": "지원자 맥락 반영 구조화 면접",
        "ksa_driven": "KSA 직접 검증 구조화 면접",
        "local_pack": "JD와 KSA 통합 구조화 면접",
    }.get(mode, "구조화 면접")

    return (
        "아래 컨텍스트를 바탕으로 구조화 면접 질문을 생성하세요.\n"
        f"모드: {mode_hint}\n"
        f"생성 개수: {target_count}\n\n"
        "[규칙]\n"
        "- 반드시 한국어로 작성합니다.\n"
        "- 질문은 선택된 면접 기법의 목적과 답변 방식을 분명히 반영해야 합니다.\n"
        "- 각 질문은 하나의 역량만 검증합니다.\n"
        "- 각 질문마다 follow_ups 3개를 포함합니다.\n"
        "- follow_ups는 주질문, 구체화, 판단 근거, 결과/교훈 순서로 깊어져야 합니다.\n"
        "- follow_ups 3개는 선택 면접기법의 평가 행동을 각각 다르게 파고들고, 최소 1개는 직무명 또는 public_focus를 포함합니다.\n"
        "- 각 질문은 [KSA]에서 evidence_id 하나를 주 검증 근거로 선택합니다. official_factor는 내부 근거 라벨이며 지원자용 question, follow_ups, evaluation_points에 복사하거나 따옴표로 인용하지 않습니다.\n"
        "- 지원자용 문장에는 같은 행의 public_focus, task_statement, observable_behavior를 사용합니다.\n"
        "- official_factor 이름만 되묻는 질문은 금지하며, 관찰 가능한 판단·행동·산출물을 과제로 요구합니다.\n"
        "- 지식은 자료와 예외상황에서 판단 근거, 적용 범위와 오류 위험을 설명하게 합니다.\n"
        "- 기술은 수행 순서·구체 조치·사용 자료나 도구·산출물·품질 확인을 보여주게 합니다.\n"
        "- 태도는 마감 압박·이해관계 충돌·품질 위험에서 일관된 선택 행동과 후속 책임을 보여주게 합니다.\n"
        "- 경험면접 이외의 기법은 과거 경험 유무를 묻지 않고 해당 과제 수행으로 판단·행동·산출물을 관찰합니다.\n"
        "- 필수 형식어를 체크리스트처럼 나열하지 말고 하나의 실제 직무 상황과 의사결정 흐름으로 묶습니다.\n"
        "- 발표·토론·인바스켓·직무지식면접은 follow_ups[0]에서 확인 자료·사실·기준을 묻고, 경험·상황·창의적 문제해결력면접은 follow_ups[1]에서 판단 이유나 구체 행동을 묻습니다.\n"
        "- 질문끼리 내용이 겹치면 안 됩니다.\n"
        "- 같은 면접기법이나 같은 evidence_id를 반복할 때는 상황 프레임을 다르게 사용합니다: 일정 지연, 자료 불일치, 이해관계자 충돌, 예외상황, 자원 제약, 품질 리스크.\n"
        "- evaluation_points는 4~6개의 측정 가능한 문장으로 작성합니다.\n"
        "- question_evidence_id에는 선택한 evidence_id를, question_focus_surface에는 같은 행의 public_focus를 넣습니다.\n"
        "- question_focus와 ksa_refs는 평가위원용 내부 필드입니다. question_focus에는 official_factor 하나를 넣고 ksa_refs 첫 항목과 일치시키되 지원자용 문장에는 노출하지 않습니다.\n"
        "- 민감하거나 차별적인 질문은 생성하지 않습니다.\n\n"
        "[면접 기법]\n"
        "- 경험면접: 과거 행동 또는 유사 경험을 STAR 방식으로 확인합니다.\n"
        "- 상황면접: 가상의 직무 상황에서 판단 기준, 행동 순서, 위험 대응을 확인합니다.\n"
        "- 발표면접: 자료 분석, 대안 구성, 실행계획, 성과지표를 발표하고 질의응답 대응을 확인합니다.\n"
        "- 토론면접: 현장 사건에서 양립하기 어려운 두 정책 대안의 근거와 위험을 검토하고 공동 합의를 형성하는 과정을 확인합니다.\n"
        "- 창의적 문제해결력면접: 미래예측, 창의적 사고, 상황 판단, 혁신적 사고, 논리 분석, 실현가능성, 문제해결, 의사결정을 과제로 확인합니다.\n"
        "- 인바스켓면접: 동시에 들어온 여러 문서와 요청의 우선순위와 첫 조치를 확인합니다.\n"
        "- 직무지식면접: 절차, 기준, 산출물, 예외상황 적용 능력을 확인합니다.\n\n"
        "[주질문 필수어]\n"
        "- 경험면접: question에 경험, 상황, 본인, 행동, 결과를 직접 포함합니다.\n"
        "- 상황면접: question에 상황, 판단, 기준, 순서, 위험을 직접 포함합니다.\n"
        "- 발표면접: question에 발표과제, 발표, 진단, 대안, 실행, 성과지표, 질의응답을 직접 포함합니다.\n"
        "- 토론면접: question에 토론과제, 현장 사건, 충돌하는 두 입장, 근거, 위험, 합의를 직접 포함합니다. 시간과 제출요건은 별도 task_conditions에서 관리하므로 반복하지 않습니다.\n"
        "- 창의적 문제해결력면접: question에 창의적 문제해결력과제, 미래예측, 문제, 정의, 대안, 검증, 실현가능성, 의사결정, 실행을 직접 포함합니다.\n"
        "- 인바스켓면접: question에 인바스켓, 문서, 우선순위, 보고, 위임, 직접처리를 직접 포함합니다.\n"
        "- 준비·발표·토론·질의응답 시간과 제출 방식은 question에 쓰지 말고 별도 task_conditions에만 둡니다.\n"
        "- 직무지식면접: question에 절차, 기준, 산출물, 예외상황을 직접 포함합니다.\n\n"
        "[꼬리질문 품질 기준]\n"
        "- 경험면접: 상황, 역할, 행동, 기준, 성과/개선을 순차적으로 확인합니다.\n"
        "- 상황면접: 확인할 사실, 판단 기준, 위험요인, 이해관계자 대응 또는 후속 조치를 확인합니다.\n"
        "- 발표면접: 진단 근거자료, 대안 우선순위, 반대 의견 답변, 질의응답 대응, 실행 일정이나 성과지표를 확인합니다.\n"
        "- 토론면접: 확인할 문서·사실, 상대 근거의 타당성, 수용·불수용 기준, 합의안의 적용 범위·예외·검증 기준과 실행 책임을 확인합니다.\n"
        "- 창의적 문제해결력면접: 미래예측, 문제정의, 원인 가설, 창의적 대안, 검증 방법, 실현가능성과 의사결정을 확인합니다.\n"
        "- 인바스켓면접: 문서·요청 분류, 먼저 처리/보류 판단, 보고·위임·직접처리 선택을 확인합니다.\n"
        "- 직무지식면접: 기준·규정, 예외상황, 산출물 품질, 오류 리스크 또는 교육 순서를 확인합니다.\n\n"
        "[공식 근거와 지원자 문장 분리 예시]\n"
        "- 통과: official_factor='승인된 변경에 대한 지식'은 내부 필드에만 두고, question에는 public_focus='승인된 변경 관련 판단 기준'을 사용해 적용 범위·예외·검증 기준을 묻습니다.\n"
        "- 실패: official_factor를 따옴표로 인용하거나 '승인된 변경에 대한'처럼 불완전하게 잘라 지원자에게 노출하는 문장.\n\n"
        "[기법 선택]\n"
        "- 추가 컨텍스트에 선택 기법이 있으면 그 기법만 사용합니다.\n"
        "- 선택 기법이 없으면 경험면접, 상황면접, 발표면접, 토론면접, 인바스켓면접, 직무지식면접을 우선 섞고, 복합 문제해결 문맥이 있으면 창의적 문제해결력면접도 포함합니다.\n\n"
        "[출력 형식]\n"
        "JSON 객체 하나만 출력:\n"
        "{\n"
        '  "interview_questions": [\n'
        "    {\n"
        '      "type": "경험면접|상황면접|발표면접|토론면접|창의적 문제해결력면접|인바스켓면접|직무지식면접",\n'
        '      "competency": "능력단위명",\n'
        '      "ncsClCd": "코드",\n'
        '      "question": "주질문",\n'
        '      "follow_ups": ["구체화", "판단 근거", "결과/교훈"],\n'
        '      "evaluation_points": ["항목1", "항목2", "항목3", "항목4"],\n'
        '      "question_evidence_id": "ksa_...",\n'
        '      "question_focus_surface": "지원자용 업무 초점",\n'
        '      "question_focus": "내부 주 검증 official_factor",\n'
        '      "ksa_refs": ["KSA1", "KSA2"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "[NCS]\n"
        f"{chr(10).join(ncs_lines) if ncs_lines else '- 없음'}\n\n"
        "[KSA]\n"
        f"{chr(10).join(ksa_lines) if ksa_lines else '- 없음'}\n\n"
        + (f"[JD]\n{jd_text[:1500]}\n\n" if jd_text else "")
        + (f"[강점/프로필]\n{strengths[:1500]}\n\n" if strengths else "")
        + (f"[추가 컨텍스트]\n{extra_context[:1500]}\n" if extra_context else "")
    )


def _soften_entry_level_question(question: str) -> str:
    q = str(question or "").strip()
    if not q:
        return q
    if _ENTRY_LEVEL_ALREADY_RE.search(q):
        return q
    if not _ENTRY_LEVEL_TRIGGER_RE.search(q):
        return q

    replacements: list[tuple[str, str]] = [
        (r"수행\s*경험에서", "수행했거나 유사 상황을 가정한 사례에서"),
        (r"수행\s*경험을", "수행했거나 유사 상황을 가정한 사례를"),
        (r"수립한\s*경험", "수립했거나 유사 상황을 가정한 사례"),
        (r"운영한\s*경험", "운영했거나 유사 상황을 가정한 사례"),
        (r"경험이\s*있다면", "경험이나 유사 사례(가정 상황 포함)가 있다면"),
        (r"경험에\s*대해", "경험 또는 유사 사례(가정 상황 포함)에 대해"),
        (r"참여했던", "참여했거나 유사한"),
        (r"담당했던", "담당했거나 유사한"),
    ]
    out = q
    for pattern, repl in replacements:
        new_q = re.sub(pattern, repl, out, count=1)
        if new_q != out:
            out = new_q
            break
    if out == q:
        out = re.sub(r"경험", "경험 또는 유사 사례(가정 상황 포함)", q, count=1)
    return out


def _build_question_generation_prompt(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    jd_text: str = "",
    strengths: str = "",
    mode: str = "diverse",
    target_count: int = 6,
    extra_context: str = "",
) -> str:
    ncs_lines: list[str] = []
    units_by_code: dict[str, dict[str, Any]] = {}
    units_by_name: dict[str, dict[str, Any]] = {}
    for row in (ncs_matches or [])[:8]:
        code = str(row.get("ncsClCd", "")).strip()
        name = str(row.get("compeUnitName", "")).strip()
        desc = str(row.get("compeUnitDef", "")).strip()
        if code:
            units_by_code[code] = row
        if name:
            units_by_name[name] = row
        if code and name:
            ncs_lines.append(f"- {code} | {name} | {desc[:220]}")

    ksa_lines: list[str] = []
    seen_ksa: set[str] = set()
    for row in (ncs_ksa or [])[:40]:
        factor = str(row.get("factorName", "")).strip()
        if not factor:
            continue
        norm = re.sub(r"\s+", "", factor)
        if norm in seen_ksa:
            continue
        seen_ksa.add(norm)
        src = str(row.get("factorSource", "")).strip()
        factor_type = str(
            row.get("ksaTypeName") or row.get("factorType") or row.get("ksa_type") or ""
        ).strip()
        unit = str(row.get("compeUnitName", "")).strip()
        code = str(row.get("ncsClCd") or "").strip()
        unit_row = units_by_code.get(code) or units_by_name.get(unit) or {}
        frame = build_question_task_frame(
            evidence_row=row,
            factor_name=factor,
            ksa_type=factor_type,
            element_name=row.get("elementName") or row.get("element_name") or "",
            competency_name=unit or unit_row.get("compeUnitName") or "",
            competency_definition=row.get("compeUnitDef") or unit_row.get("compeUnitDef") or "",
        )
        ksa_lines.append(
            "- "
            f"evidence_id={frame['evidence_id']} | official_factor={factor} | "
            f"public_focus={frame['task_object']} | task_statement={frame['task_statement']} | "
            f"observable_behavior={frame['observable_behavior']} | type={factor_type or '미분류'} | "
            f"unit={unit} | source={src}"
        )

    return _render_question_generation_prompt(
        ncs_lines=ncs_lines,
        ksa_lines=ksa_lines,
        jd_text=jd_text,
        strengths=strengths,
        mode=mode,
        target_count=target_count,
        extra_context=extra_context,
    )


def _extract_json_text(response_text: str) -> str:
    txt = str(response_text or "").strip()
    if not txt:
        return ""
    block = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
    if block:
        return block.group(1).strip()
    start_obj = txt.find("{")
    start_arr = txt.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        return txt
    return txt[min(starts):].strip()


def _slice_balanced_json(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""

    start_obj = raw.find("{")
    start_arr = raw.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        return ""
    start = min(starts)

    stack: list[str] = []
    in_string = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append(ch)
            continue
        if ch in "}]":
            if not stack:
                continue
            open_ch = stack.pop()
            if (open_ch == "{" and ch != "}") or (open_ch == "[" and ch != "]"):
                return ""
            if not stack:
                return raw[start: idx + 1].strip()
    return ""


def _extract_message_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            txt = str(part.get("text", "")).strip()
            if txt:
                parts.append(txt)
        return "\n".join(parts)
    return str(content or "")


def _normalize_question_item(item: dict[str, Any]) -> dict[str, Any] | None:
    question = _soften_entry_level_question(str(item.get("question", "")).strip())
    if not question:
        return None
    interview_type = _canonical_interview_type(item.get("type", "경험면접"))

    raw_follow_ups = item.get("follow_ups")
    follow_ups: list[str] = []
    if isinstance(raw_follow_ups, list):
        follow_ups = [_soften_entry_level_question(str(x).strip()) for x in raw_follow_ups if str(x).strip()]
    else:
        single = str(item.get("follow_up", "")).strip()
        if single:
            follow_ups = [_soften_entry_level_question(single)]
    if len(follow_ups) < 3:
        for f in _METHOD_DEFAULT_FOLLOW_UPS.get(interview_type, _DEFAULT_FOLLOW_UPS):
            if len(follow_ups) >= 3:
                break
            if f in follow_ups:
                continue
            follow_ups.append(f)
    follow_ups = follow_ups[:3]

    ev = item.get("evaluation_points")
    evaluation_points = [str(x).strip() for x in (ev or []) if str(x).strip()] if isinstance(ev, list) else []
    if len(evaluation_points) < 4:
        for d in _METHOD_DEFAULT_EVALUATION_POINTS.get(interview_type, _DEFAULT_EVALUATION_POINTS):
            if len(evaluation_points) >= 4:
                break
            if d in evaluation_points:
                continue
            evaluation_points.append(d)
    evaluation_points = evaluation_points[:6]

    ksa = item.get("ksa_refs")
    ksa_refs = [str(x).strip() for x in (ksa or []) if str(x).strip()] if isinstance(ksa, list) else []

    if _contains_blind_hiring_cue(question, follow_ups, evaluation_points):
        return None

    normalized = {
        "question": question,
        "type": interview_type,
        "competency": str(item.get("competency", "")).strip(),
        "ncsClCd": str(item.get("ncsClCd", "")).strip(),
        "evaluation_points": evaluation_points,
        "follow_ups": follow_ups,
        "follow_up": follow_ups[0],
        "ksa_refs": ksa_refs,
    }
    evidence_id = str(item.get("question_evidence_id") or item.get("evidence_id") or "").strip()
    surface_focus = str(item.get("question_focus_surface") or item.get("public_focus") or "").strip()
    if evidence_id:
        normalized["question_evidence_id"] = evidence_id
        normalized["question_evidence_required"] = True
    if surface_focus:
        normalized["question_focus_surface"] = surface_focus
    if isinstance(item.get("question_task_frame"), dict):
        normalized["question_task_frame"] = dict(item.get("question_task_frame") or {})
    if isinstance(item.get("task_conditions"), dict):
        normalized["task_conditions"] = dict(item.get("task_conditions") or {})
    focus = str(item.get("question_focus") or item.get("focus") or item.get("primary_focus") or "").strip()
    if ksa_refs:
        normalized["question_focus"] = focus if focus in ksa_refs else ksa_refs[0]
        if normalized["question_focus"] in ksa_refs and ksa_refs[0] != normalized["question_focus"]:
            normalized["ksa_refs"] = [
                normalized["question_focus"],
                *[ref for ref in ksa_refs if ref != normalized["question_focus"]],
            ]
    elif focus:
        normalized["question_focus"] = focus
    return normalized


def _parse_openai_response(response_text: str) -> list[dict[str, Any]]:
    raw = _extract_json_text(response_text)
    candidates = [raw, _slice_balanced_json(raw), _slice_balanced_json(str(response_text or ""))]
    data: Any | None = None
    seen_candidates: set[str] = set()
    for candidate in candidates:
        cand = str(candidate or "").strip()
        if not cand or cand in seen_candidates:
            continue
        seen_candidates.add(cand)
        try:
            data = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        return []

    if isinstance(data, dict) and isinstance(data.get("interview_questions"), list):
        items = data.get("interview_questions") or []
    elif isinstance(data, dict) and isinstance(data.get("questions"), list):
        items = data.get("questions") or []
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data.get("items") or []
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data.get("data") or []
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        items = []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_question_item(row)
        if not normalized:
            continue
        if _question_near_duplicate_seen(normalized, out):
            continue
        if _question_already_seen(normalized, seen):
            continue
        out.append(normalized)
    return out


def _attach_candidate_surface_evidence(
    item: dict[str, Any],
    *,
    ncs_ksa: list[dict[str, Any]] | None,
    ncs_matches: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Link a model item to official evidence and repair only exposed fields.

    The official factor stays in internal grounding fields.  If the model copied
    that label into candidate-visible text, only those fields are rewritten with
    the grammatical public task object; the model's scenario and main intent are
    otherwise preserved.
    """

    out = dict(item or {})
    official_rows = [row for row in (ncs_ksa or []) if isinstance(row, dict)]
    if not official_rows:
        return out

    evidence_id = str(out.get("question_evidence_id") or "").strip()
    focus = str(out.get("question_focus") or "").strip()
    refs = [
        str(value or "").strip()
        for value in (out.get("ksa_refs") or [])
        if str(value or "").strip()
    ] if isinstance(out.get("ksa_refs"), list) else []
    declared_factors = {re.sub(r"\s+", "", value).casefold() for value in [focus, *refs] if value}
    code = str(out.get("ncsClCd") or "").strip()

    selected: dict[str, Any] | None = None
    if evidence_id:
        selected = next(
            (row for row in official_rows if stable_ksa_evidence_id(row) == evidence_id),
            None,
        )
    if selected is None and declared_factors:
        selected = next(
            (
                row
                for row in official_rows
                if re.sub(r"\s+", "", str(row.get("factorName") or "")).casefold() in declared_factors
                and (not code or not str(row.get("ncsClCd") or "").strip() or str(row.get("ncsClCd") or "").strip() == code)
            ),
            None,
        )
    if selected is None:
        return out

    unit_code = str(selected.get("ncsClCd") or code).strip()
    unit_name = str(selected.get("compeUnitName") or out.get("competency") or "").strip()
    unit_row = next(
        (
            row
            for row in (ncs_matches or [])
            if isinstance(row, dict)
            and (
                (unit_code and str(row.get("ncsClCd") or "").strip() == unit_code)
                or (unit_name and str(row.get("compeUnitName") or "").strip() == unit_name)
            )
        ),
        {},
    )
    frame = build_question_task_frame(
        evidence_row=selected,
        factor_name=selected.get("factorName") or focus,
        ksa_type=selected.get("ksaTypeName") or selected.get("factorType") or selected.get("ksa_type") or "",
        element_name=selected.get("elementName") or selected.get("element_name") or "",
        competency_name=unit_name or unit_row.get("compeUnitName") or "",
        competency_definition=selected.get("compeUnitDef") or unit_row.get("compeUnitDef") or "",
    )
    primary_surface = str(frame.get("task_object") or "").strip()
    if not primary_surface:
        return out

    replacement_pairs: list[tuple[str, str]] = []
    for row in official_rows:
        raw_factor = str(row.get("factorName") or "").strip()
        factor_key = re.sub(r"\s+", "", raw_factor).casefold()
        if len(factor_key) < 4 or (row is not selected and factor_key not in declared_factors):
            continue
        row_frame = frame if row is selected else build_question_task_frame(
            evidence_row=row,
            factor_name=raw_factor,
            ksa_type=row.get("ksaTypeName") or row.get("factorType") or row.get("ksa_type") or "",
            element_name=row.get("elementName") or row.get("element_name") or "",
            competency_name=row.get("compeUnitName") or unit_name,
            competency_definition=row.get("compeUnitDef") or unit_row.get("compeUnitDef") or "",
        )
        public_surface = str(row_frame.get("task_object") or "").strip()
        if public_surface and public_surface != raw_factor:
            replacement_pairs.append((raw_factor, public_surface))

    repaired_fields: list[str] = []

    def _repair_text(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        repaired = text
        for raw_factor, public_surface in replacement_pairs:
            repaired, _ = replace_official_ksa_surface(repaired, raw_factor, public_surface)
        if repaired != text:
            repaired_fields.append(field_name)
        return repaired

    out["question"] = _repair_text(out.get("question"), "question")
    out["follow_ups"] = [
        _repair_text(value, "follow_ups")
        for value in (out.get("follow_ups") or [])
        if str(value or "").strip()
    ]
    if out["follow_ups"]:
        out["follow_up"] = out["follow_ups"][0]
    out["evaluation_points"] = [
        _repair_text(value, "evaluation_points")
        for value in (out.get("evaluation_points") or [])
        if str(value or "").strip()
    ]
    out["question_focus_surface"] = primary_surface
    out["question_task_frame"] = frame
    out["question_evidence_id"] = str(frame.get("evidence_id") or "")
    out["question_evidence_required"] = True
    if repaired_fields:
        out["candidate_surface_repairs"] = sorted(set(repaired_fields))
    return out


def _generate_questions_with_openai_from_ncs(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    jd_text: str = "",
    strengths: str = "",
    target_count: int = 6,
    mode: str = "diverse",
    extra_context: str = "",
    api_key_override: str = "",
) -> list[dict[str, Any]]:
    api_key = settings.resolve_openai_key(api_key_override)
    if not api_key:
        return []

    prompt = _build_question_generation_prompt(
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        jd_text=jd_text,
        strengths=strengths,
        mode=mode,
        target_count=target_count,
        extra_context=extra_context,
    )

    try:
        target_n = max(1, int(target_count or 1))
    except Exception:
        target_n = 1

    try:
        timeout_sec = float(str(os.getenv("OPENAI_QUESTION_TIMEOUT_SEC", "60")).strip() or "60")
    except Exception:
        timeout_sec = 60.0
    timeout_sec = max(15.0, min(240.0, timeout_sec))

    try:
        max_variants = int(str(os.getenv("OPENAI_QUESTION_VARIANT_ATTEMPTS", "3")).strip() or "3")
    except Exception:
        max_variants = 3
    max_variants = max(1, min(3, max_variants))

    payload_base = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "공공기관 구조화 면접 설계 전문가입니다. JSON만 출력하세요."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 4000,
    }

    attempts: list[tuple[dict[str, Any], float]] = []
    p1 = copy.deepcopy(payload_base)
    p1["response_format"] = {"type": "json_object"}
    attempts.append((p1, timeout_sec))

    p2 = copy.deepcopy(payload_base)
    attempts.append((p2, min(240.0, timeout_sec + 20.0)))

    p3 = copy.deepcopy(payload_base)
    p3["temperature"] = 0.3
    p3["messages"][1]["content"] = (
        str(p3["messages"][1]["content"])
        + "\n\n중요: 설명문 없이 JSON만 출력하세요. 유효한 JSON 객체 1개만 반환하세요."
    )
    attempts.append((p3, min(240.0, timeout_sec + 30.0)))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload, req_timeout in attempts[:max_variants]:
        try:
            data = post_chat_completions_with_retries(
                payload=payload,
                api_key=api_key,
                timeout_sec=req_timeout,
            )
        except Exception:
            continue

        parsed = [
            _attach_candidate_surface_evidence(
                row,
                ncs_ksa=ncs_ksa,
                ncs_matches=ncs_matches,
            )
            for row in _parse_openai_response(_extract_message_content(data))
        ]
        if not parsed:
            continue

        for row in parsed:
            if _question_near_duplicate_seen(row, merged):
                continue
            if _question_already_seen(row, seen):
                continue
            merged.append(row)
            if len(merged) >= target_n:
                break
        if len(merged) >= target_n:
            break

    return merged[:target_n]

