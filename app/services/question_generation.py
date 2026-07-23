from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

from app.services.openai_http import post_chat_completions_with_retries
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
        "제한시간 이후 기록과 후속 점검은 어떻게 남기겠습니까?",
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
        "- follow_ups 3개는 선택 면접기법의 평가 행동을 각각 다르게 파고들고, 최소 1개는 직무/NCS/KSA 핵심어를 직접 포함합니다.\n"
        "- 각 질문은 [KSA]의 factorName 원문 중 하나를 주 검증 초점으로 선택하고, question과 follow_ups 중 지정 위치에 그 factorName 원문을 그대로 반복합니다.\n"
        "- question과 follow_ups에 글자 그대로 'KSA'라고 쓰지 말고, 실제 factorName 원문을 복사해 넣습니다.\n"
        "- 발표면접, 토론면접, 인바스켓면접, 직무지식면접은 follow_ups[0]에 factorName 원문과 능력단위명을 함께 넣고, 경험면접, 상황면접, 창의적 문제해결력면접은 follow_ups[1]에 넣습니다.\n"
        "- 질문끼리 내용이 겹치면 안 됩니다.\n"
        "- evaluation_points는 4~6개의 측정 가능한 문장으로 작성합니다.\n"
        "- ksa_refs에는 해당 질문과 직접 연결되는 factorName 원문 2~4개를 넣고, 첫 항목은 question에 직접 쓴 주 검증 초점과 일치시킵니다.\n"
        "- 민감하거나 차별적인 질문은 생성하지 않습니다.\n\n"
        "[면접 기법]\n"
        "- 경험면접: 과거 행동 또는 유사 경험을 STAR 방식으로 확인합니다.\n"
        "- 상황면접: 가상의 직무 상황에서 판단 기준, 행동 순서, 위험 대응을 확인합니다.\n"
        "- 발표면접: 준비시간 후 자료 분석, 대안 구성, 실행계획, 성과지표를 발표하고 질의응답 대응을 확인합니다.\n"
        "- 토론면접: 토론시간 안에 입장발표, 반대 의견 검토, 경청, 조정, 최종 합의 형성을 확인합니다.\n"
        "- 창의적 문제해결력면접: 미래예측, 창의적 사고, 상황 판단, 혁신적 사고, 논리 분석, 실현가능성, 문제해결, 의사결정을 과제로 확인합니다.\n"
        "- 인바스켓면접: 제한시간 안에 여러 문서와 요청의 우선순위와 첫 조치를 확인합니다.\n"
        "- 직무지식면접: 절차, 기준, 산출물, 예외상황 적용 능력을 확인합니다.\n\n"
        "[주질문 필수어]\n"
        "- 경험면접: question에 경험, 상황, 본인, 행동, 결과를 직접 포함합니다.\n"
        "- 상황면접: question에 상황, 판단, 기준, 순서, 위험을 직접 포함합니다.\n"
        "- 발표면접: question에 발표과제, 준비시간, 발표, 진단, 대안, 실행, 성과지표, 질의응답을 직접 포함합니다.\n"
        "- 토론면접: question에 토론과제, 토론시간, 입장발표, 충돌, 입장, 반대, 합의를 직접 포함합니다.\n"
        "- 창의적 문제해결력면접: question에 창의적 문제해결력과제, 미래예측, 문제, 정의, 대안, 검증, 실현가능성, 의사결정, 실행을 직접 포함합니다.\n"
        "- 인바스켓면접: question에 인바스켓, 제한시간, 문서, 우선순위, 보고, 위임, 직접처리를 직접 포함합니다.\n"
        "- 직무지식면접: question에 절차, 기준, 산출물, 예외상황을 직접 포함합니다.\n\n"
        "[꼬리질문 품질 기준]\n"
        "- 경험면접: 상황, 역할, 행동, 기준, 성과/개선을 순차적으로 확인합니다.\n"
        "- 상황면접: 확인할 사실, 판단 기준, 위험요인, 이해관계자 대응 또는 후속 조치를 확인합니다.\n"
        "- 발표면접: 진단 근거자료, 대안 우선순위, 반대 의견 답변, 질의응답 대응, 실행 일정이나 성과지표를 확인합니다.\n"
        "- 토론면접: 입장발표 근거, 반대 의견 수용 범위, 조정 방식, 합의안 기준을 확인합니다.\n"
        "- 창의적 문제해결력면접: 미래예측, 문제정의, 원인 가설, 창의적 대안, 검증 방법, 실현가능성과 의사결정을 확인합니다.\n"
        "- 인바스켓면접: 문서·요청 분류, 먼저 처리/보류 판단, 보고·위임·직접처리 선택을 확인합니다.\n"
        "- 직무지식면접: 기준·규정, 예외상황, 산출물 품질, 오류 리스크 또는 교육 순서를 확인합니다.\n\n"
        "[KSA 원문 보존 예시]\n"
        "- 통과: question='문서작성에서 문서 요구사항 파악을 적용한 경험을 말씀해 주세요...' / follow_ups[1]='문서 요구사항 파악을 기준으로 어떤 행동을 선택했습니까?'\n"
        "- 실패: question='문서작성에서 KSA를 적용한 경험...' / follow_ups[1]='그 판단의 이유는 무엇입니까?'처럼 factorName 원문이 빠진 문장.\n\n"
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
    for row in (ncs_matches or [])[:8]:
        code = str(row.get("ncsClCd", "")).strip()
        name = str(row.get("compeUnitName", "")).strip()
        desc = str(row.get("compeUnitDef", "")).strip()
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
        unit = str(row.get("compeUnitName", "")).strip()
        ksa_lines.append(f"- {factor} | unit={unit} | source={src}")

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

    return {
        "question": question,
        "type": interview_type,
        "competency": str(item.get("competency", "")).strip(),
        "ncsClCd": str(item.get("ncsClCd", "")).strip(),
        "evaluation_points": evaluation_points,
        "follow_ups": follow_ups,
        "follow_up": follow_ups[0],
        "ksa_refs": ksa_refs,
    }


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
        key = re.sub(r"\s+", " ", normalized["question"]).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
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
    api_key = str(api_key_override or "").strip() or settings.openai_key()
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

        parsed = _parse_openai_response(_extract_message_content(data))
        if not parsed:
            continue

        for row in parsed:
            q_key = re.sub(r"\s+", " ", str((row or {}).get("question", "")).strip()).lower()
            if not q_key or q_key in seen:
                continue
            seen.add(q_key)
            merged.append(row)
            if len(merged) >= target_n:
                break
        if len(merged) >= target_n:
            break

    return merged[:target_n]

