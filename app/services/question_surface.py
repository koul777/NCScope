"""Translate official NCS evidence into candidate-facing interview language.

Official KSA labels are evidence identifiers, not necessarily grammatical
phrases.  This module keeps the evidence stable while producing a separate,
observable task frame for questions and follow-ups.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_KSA_TYPE_SUFFIX_RE = re.compile(r"\s*(?:관련\s*)?(?:능력|기술|스킬|지식)\s*$")
_DANGLING_END_RE = re.compile(
    r"(?:에\s*대한|에\s*관한|을\s*위한|를\s*위한|에\s*따른|을\s*통한|를\s*통한|"
    r"하고|하며|하여|해서|따른|통한|관련된|하는|되는|대한|위한|의)\s*$"
)
_SENTENCE_END_RE = re.compile(r"(?:다|한다|된다|있다|없다)[.!?]?\s*$")
_GENERIC_OBJECT_KEYS = {
    "지식",
    "기술",
    "능력",
    "태도",
    "핵심수행기준",
    "해당업무",
    "관련업무",
}


def _clean(value: Any, *, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n'\"")
    return text[:max_chars].strip()


def _key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()


def normalize_ksa_type(value: Any, factor_name: Any = "") -> str:
    raw = _key(value)
    if raw in {"k", "knowledge", "지식"} or "지식" in raw:
        return "지식"
    if raw in {"s", "skill", "skills", "기술"} or any(token in raw for token in ("기술", "스킬")):
        return "기술"
    if raw in {"a", "attitude", "태도"} or "태도" in raw:
        return "태도"

    factor_key = _key(factor_name)
    if any(token in factor_key for token in ("태도", "자세", "의지", "의식", "성실", "적극성", "책임감")):
        return "태도"
    if any(
        token in factor_key
        for token in ("지식", "개념", "원리", "이론", "종류", "특성", "법규", "법령", "동향", "규정", "세법")
    ):
        return "지식"
    if any(
        token in factor_key
        for token in (
            "능력",
            "기술",
            "스킬",
            "작성",
            "수립",
            "분석",
            "점검",
            "활용",
            "파악",
            "검증",
            "산정",
            "설정",
            "조작",
            "선별",
            "중재",
            "기법",
            "시스템",
        )
    ):
        return "기술"
    return ""


def stable_ksa_evidence_id(row: dict[str, Any] | None) -> str:
    """Return a deterministic, non-secret identifier for one official KSA row."""

    source = row if isinstance(row, dict) else {}
    identity = {
        "ncs_code": _clean(source.get("ncsClCd") or source.get("unit_code"), max_chars=80),
        "element": _clean(source.get("elementName") or source.get("element_name"), max_chars=160),
        "ksa_type": normalize_ksa_type(
            source.get("ksaTypeName") or source.get("factorType") or source.get("ksa_type"),
            source.get("factorName") or source.get("factor_name"),
        ),
        "factor": _clean(source.get("factorName") or source.get("factor_name"), max_chars=240),
        "factor_no": _clean(source.get("ksaNo") or source.get("factorNo") or source.get("number"), max_chars=80),
        "source": _clean(source.get("factorSource") or source.get("source"), max_chars=80),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"ksa_{digest[:24]}"


def _useful_object(value: Any, *, official_factor: str = "") -> bool:
    text = _clean(value, max_chars=180)
    key = _key(text)
    if not text or len(text) > 80 or len(key) < 3 or key in _GENERIC_OBJECT_KEYS:
        return False
    if official_factor and _key(official_factor) == key:
        return False
    if _SENTENCE_END_RE.search(text):
        return False
    return not _DANGLING_END_RE.search(text)


def _definition_object(definition: Any, competency_name: Any) -> str:
    text = _clean(definition, max_chars=240)
    competency = _clean(competency_name, max_chars=80)
    if not text:
        return ""
    if competency:
        text = re.sub(rf"^{re.escape(competency)}\s*(?:이란|은|는)?\s*", "", text).strip()
    first = re.split(r"[.;。]|(?:하기\s*위해)|(?:하기\s*위하여)|(?:하는\s*능력)", text, maxsplit=1)[0].strip()
    first = re.sub(r"^(?:업무를?|과업을?)\s*", "", first).strip()
    return first if 4 <= len(_key(first)) <= 36 and not _SENTENCE_END_RE.search(first) else ""


def _compact_task_base(value: Any, *, max_chars: int = 62) -> str:
    text = _clean(value, max_chars=180).strip(" ,.;:·ㆍ/-")
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].strip(" ,.;:·ㆍ/-")
    clipped = re.sub(
        r"\s*(?:및|또는|혹은|그리고|에\s*대한|에\s*관한|을\s*위한|를\s*위한|의)\s*$",
        "",
        clipped,
    ).strip(" ,.;:·ㆍ/-")
    return clipped or text[:max_chars].strip(" ,.;:·ㆍ/-")


def _nominalize_attitude_clause(value: Any) -> str:
    text = _clean(value, max_chars=160)
    text = re.sub(r"위해\s*(?:적극적으로\s*)?노력하려는", "위한 적극적", text)
    text = re.sub(r"위해\s*(?:적극적으로\s*)?노력하는", "위한 적극적", text)
    text = re.sub(
        r"(?P<object>.+?(?:을|를))\s*하고자\s*하는",
        r"\g<object> 실행하는",
        text,
    )
    text = re.sub(
        r"(?P<object>.+?(?:을|를))\s*하고자\s*한",
        r"\g<object> 실행한",
        text,
    )
    text = re.sub(
        r"(?P<object>.+?(?:을|를))\s*하려는",
        r"\g<object> 실행하는",
        text,
    )
    # When "하고자" follows a verb stem ("달성하고자") it expresses
    # intention, not the generic verb "하다". Preserve that stem instead of
    # creating fused wording such as "달성실행하는".
    text = re.sub(r"(?P<stem>[가-힣]+)하고자\s*하는", r"\g<stem>하려는", text)
    text = re.sub(r"(?P<stem>[가-힣]+)하고자\s*한", r"\g<stem>하려 했던", text)
    text = re.sub(r"할\s*수\s*있도록", "가능하도록", text)
    text = re.sub(r"할\s*수\s*있는", "가능한", text)
    return text.strip()


def _nominalize_capability_clause(value: Any) -> str:
    text = _clean(value, max_chars=180)
    action_nouns = (
        "포착|분석|검토|평가|판단|확인|파악|해석|예측|계산|측정|조치|적용|"
        "식별|설명|수행|작성|수립|설정|선택|선정|조정|수집|분류|처리|운영|"
        "관리|점검|검증|산정|조사|배치|의사결정"
    )
    # Some official labels join a capability clause to an action noun, for
    # example "영업기회를 찾아낼 수 있는 기회포착 능력".  The capability
    # wording is evidence metadata; the public task should be the observable
    # action object ("영업기회 포착"), not a dangling adjective clause.
    def _join_capability_action(match: re.Match[str]) -> str:
        object_text = str(match.group("object") or "").strip()
        action = str(match.group("action") or "").strip()
        # "영업기회" + "기회포착" should become "영업기회 포착",
        # while unrelated pairs keep both terms.
        for overlap in range(min(len(object_text), len(action)), 1, -1):
            if object_text.endswith(action[:overlap]):
                remainder = action[overlap:].strip()
                return f"{object_text} {remainder}".strip()
        return f"{object_text} {action}".strip()

    text = re.sub(
        rf"(?P<object>.+?)(?:을|를)\s*[가-힣·ㆍ]+\s*수\s*있는\s+"
        rf"(?P<action>[가-힣]*(?:{action_nouns}))\s*$",
        _join_capability_action,
        text,
    )

    def _nominalize_trailing_capability(match: re.Match[str]) -> str:
        object_text = str(match.group("object") or "").strip()
        verb = re.sub(r"할$", "", str(match.group("verb") or "").strip())
        return f"{object_text} {verb}".strip()

    text = re.sub(
        r"(?P<object>.+?)(?:을|를)\s*(?P<verb>[가-힣·ㆍ]+)\s*수\s*있는\s*$",
        _nominalize_trailing_capability,
        text,
    )
    text = re.sub(
        r"(?P<object>.+?)(?:을|를)\s*(?P<verb>[가-힣·ㆍ]+)(?:하는|한)\s*$",
        r"\g<object> \g<verb>",
        text,
    )
    text = re.sub(r"(?P<verb>[가-힣·ㆍ]+)할\s*수\s*있는\s*$", r"\g<verb>", text)
    text = re.sub(r"(?P<verb>[가-힣]+)할\s*수\s*있도록", r"\g<verb> 가능하도록", text)
    text = re.sub(r"할\s*수\s*있는", "하는 데 필요한", text)
    text = re.sub(r"(?P<first>[가-힣]+)(?:하고|하며|하여)\s*(?P<second>[가-힣]+)\s*$", r"\g<first>·\g<second>", text)
    text = re.sub(
        r"(?P<object>.+?)(?:을|를)\s*(?P<actions>[가-힣]+(?:·[가-힣]+)+)(?:하는|한)?\s*$",
        r"\g<object> \g<actions>",
        text,
    )
    return text.strip()


def _element_task_object(element_name: Any, kind: str) -> str:
    element = _KSA_TYPE_SUFFIX_RE.sub("", _clean(element_name, max_chars=100)).strip()
    if not element:
        return ""
    element = re.sub(r"\s*(?:하기|시키기|해내기)\s*$", "", element).strip()
    element = _compact_task_base(element)
    if not element:
        return ""
    if kind == "기술":
        if element.endswith("설정"):
            return f"{element}·확인 절차"
        if element.endswith(("선정", "선택", "조정", "조율", "활용", "의사결정")):
            return f"{element}·검증 절차"
    suffix = {
        "지식": " 관련 정보 확인·판단 기준",
        "기술": " 수행·검증 절차",
        "태도": " 수행 시 행동 기준",
    }.get(kind, " 업무 판단·수행 기준")
    return f"{element}{suffix}"


def _repair_factor_object(factor_name: Any, kind: str) -> str:
    label = _clean(factor_name, max_chars=100)
    candidate = _KSA_TYPE_SUFFIX_RE.sub("", label).strip()
    if not candidate:
        return ""

    if kind == "태도":
        candidate = re.sub(
            r"\s*(?:(?:하려는|하고자\s*하는)\s*)?(?:태도|자세|의지|의식)\s*$",
            "",
            candidate,
        ).strip()
        candidate = _nominalize_attitude_clause(candidate)
        if _DANGLING_END_RE.search(candidate):
            candidate = f"{_DANGLING_END_RE.sub('', candidate).strip()} 관련"
        candidate = re.sub(
            r"(?P<object>.+?(?:을|를))\s*위해\s*(?:적극적으로\s*)?노력(?:하는|하려는)?\s*$",
            r"\g<object> 위한 적극적",
            candidate,
        ).strip()
        candidate = re.sub(r"\s*(?:적극적으로\s*)?노력(?:하는|하려는)?\s*$", "", candidate).strip()
        if candidate.endswith("유지"):
            return f"{_compact_task_base(candidate[:-2])} 준수 행동 기준"
        if candidate.endswith("우선"):
            priority_base = re.sub(r"(?:을|를)$", "", candidate[:-2].strip()).strip()
            return f"{_compact_task_base(priority_base)} 우선 행동 기준"
        if candidate.endswith("주의"):
            return f"{_compact_task_base(candidate[:-2])} 안전·주의 행동 기준"
        def _attitude_object_action(match: re.Match[str]) -> str:
            object_text = str(match.group("object") or "").strip()
            particle = str(match.group("particle") or "").strip()
            action = str(match.group("action") or "").strip()
            if action.endswith(("하려는", "하는", "했던")):
                return f"{object_text}{particle} {action}"
            return f"{object_text} {action}"

        candidate = re.sub(
            r"(?P<object>[0-9A-Za-z가-힣()·ㆍ/-]+)(?P<particle>을|를)\s+"
            r"(?P<action>[0-9A-Za-z가-힣()·ㆍ/-]+)$",
            _attitude_object_action,
            candidate,
        ).strip()
        candidate = _compact_task_base(candidate)
        return f"{candidate} 행동 기준" if candidate else ""

    if kind == "지식":
        if _DANGLING_END_RE.search(candidate):
            candidate = f"{_DANGLING_END_RE.sub('', candidate).strip()} 관련"
        knowledge_base = _nominalize_capability_clause(candidate)
        knowledge_base = re.sub(r"\s+관련\s+", " ", knowledge_base).strip()
        knowledge_base = re.sub(r"\s*(?:개념|원리|이론)\s*$", "", knowledge_base).strip()
        knowledge_base = re.sub(r"\s*종류\s*$", " 유형 구분", knowledge_base).strip()
        knowledge_base = re.sub(r"\s*특성\s*$", " 특성 분석", knowledge_base).strip()
        if re.search(r"(?:법규|법령)$", knowledge_base):
            knowledge_base = re.sub(r"(?:법규|법령)$", "규정", knowledge_base).strip()
        elif knowledge_base.endswith("동향"):
            knowledge_base = f"{knowledge_base[:-2].strip()} 환경 변화 분석"
        elif knowledge_base == "세법":
            knowledge_base = "세무 규정"
        knowledge_base = _compact_task_base(knowledge_base)
        if not knowledge_base:
            return ""
        action_suffixes = (
            "평가", "검토", "이해", "분석", "판단", "확인", "파악", "해석",
            "예측", "계산", "측정", "조치", "적용", "식별", "설명", "수행",
        )
        if knowledge_base.endswith(action_suffixes):
            return f"{knowledge_base}·판단 기준"
        decision_suffixes = ("선정", "설정", "추천", "할당", "구성", "확정", "결정")
        if knowledge_base.endswith(decision_suffixes):
            return f"{knowledge_base} 기준"
        if knowledge_base.endswith("기준"):
            return f"{knowledge_base} 적용·검증 절차"
        if knowledge_base.endswith("규정"):
            return f"{knowledge_base} 적용·판단 기준"
        return f"{knowledge_base} 확인·판단 기준"

    if kind == "기술":
        candidate = _nominalize_capability_clause(candidate)
        candidate = re.sub(r"수행하는\s*$", "실행하는", candidate)
        candidate = _DANGLING_END_RE.sub("", candidate).strip()
        candidate = re.sub(r"할\s*수\s*있도록", "하기 위한", candidate)
        candidate = re.sub(r"할\s*수\s*있는", "하기 위한", candidate)
        candidate = re.sub(r"하기\s*위한\s*$", "하는", candidate).strip()
        candidate = re.sub(r"\s*하려는\s*$", "", candidate).strip()
        candidate = re.sub(r"\s*에\s*(?:대한|관한)\s*(?=(?:활용|해석|분석|검토|판단|적용)\s*$)", " ", candidate)
        candidate = re.sub(r"(?P<action>활용|설정)\s+수행\s*$", r"\g<action>", candidate)
        candidate = re.sub(
            r"(?P<object>.+?)(?:을|를)\s*(?P<action>정리|설정|선택|선정|선별|조정|조율|활용|적용|"
            r"해석|조사|의사결정|포착|수정·보완|기획|실행|할당|배치|결정|판별|식별|도출|구현|"
            r"설계|제작|사용|운반|처치|치료|문서화|가공|조절|교체|대응|해결|반영|갱신|계획|"
            r"구성|비교|통제|보고|분할|계산|추정|예측|방지|추출|운전|보수|응용|조작|평가|진단|제시|개선)$",
            r"\g<object> \g<action>",
            candidate,
        )
        if candidate.endswith("수행") and (
            re.search(r"(?:을|를).{0,100}\s수행$", candidate)
            or re.search(r"(?:게|히|적으로|동시에|바탕으로)\s+수행$", candidate)
        ):
            return f"{_compact_task_base(candidate)}하는 절차와 결과 검증 기준"
        skill_replacements = (
            (r"\s*예측\s*기법$", " 예측·검증 절차"),
            (r"\s*기법$", " 방법 적용·검증 절차"),
            (r"\s*시스템$", " 도구 활용 절차"),
            (r"\s*수립$", " 작성·검토 절차"),
            (r"\s*수집$", " 수집·확인 절차"),
            (r"\s*분류$", " 분류·확인 절차"),
            (r"\s*처리$", " 처리·확인 절차"),
            (r"\s*운영$", " 운영·점검 절차"),
            (r"\s*관리$", " 관리·점검 절차"),
            (r"\s*파악$", " 확인 절차"),
            (r"\s*분석$", " 검토 절차"),
            (r"\s*검증$", " 확인·검증 절차"),
            (r"\s*점검$", " 확인 절차"),
            (r"\s*작성$", " 산출물 작성 절차"),
            (r"\s*산정$", " 계산·검증 절차"),
            (r"\s*설정$", " 설정·확인 절차"),
            (r"\s*선택$", " 선정·확인 절차"),
            (r"\s*선정$", " 선정·확인 절차"),
            (r"\s*선별$", " 선정·확인 절차"),
            (r"\s*조정$", " 조정·검증 절차"),
            (r"\s*조율$", " 조율·검증 절차"),
            (r"\s*중재$", " 조정 절차"),
            (r"\s*활용$", " 활용·검증 절차"),
            (r"\s*적용$", " 적용·검증 절차"),
            (r"\s*해석$", " 해석·검증 절차"),
            (r"\s*조사$", " 조사·확인 절차"),
            (r"\s*의사결정$", " 의사결정·검증 절차"),
            (r"\s*포착$", " 포착·검증 절차"),
            (r"\s*수정·보완$", " 수정·보완·검증 절차"),
            (r"\s*배양$", " 배양·확인 절차"),
            (r"\s*개발$", " 개발·검증 절차"),
            (r"\s*수행$", " 수행·검증 절차"),
            (r"\s*유지$", " 유지·점검 절차"),
        )
        for pattern, replacement in skill_replacements:
            repaired = re.sub(pattern, replacement, candidate).strip()
            if repaired != candidate:
                return repaired
        candidate = re.sub(r"\s*(?:하는|되는)\s*$", "", candidate).strip()
        candidate = _compact_task_base(candidate)
        if not candidate:
            return ""
        if candidate.endswith("절차"):
            return f"{candidate} 적용·검증 기준"
        if candidate.endswith(("방법", "기법")):
            return f"{candidate} 적용·검증 절차"
        if candidate.endswith(("도구", "장비", "시스템", "프로그램", "소프트웨어")):
            return f"{candidate} 활용·검증 절차"
        action_suffixes = (
            "기획", "실행", "할당", "배치", "결정", "판별", "식별", "도출", "구현",
            "설계", "제작", "사용", "운반", "처치", "치료", "문서화", "시뮬레이션",
            "가공", "조절", "정리", "교체", "대응", "해결", "반영", "갱신", "계획",
            "구성", "비교", "통제", "보고", "분할", "계산", "추정", "예측", "방지",
            "추출", "운전", "보수", "응용", "조작", "평가", "진단", "제시", "개선",
        )
        if candidate.endswith(action_suffixes):
            return f"{candidate}·검증 절차"
        if re.search(r"[가-힣](?:는|은)$", candidate):
            return f"{candidate} 절차와 결과 검증 기준"
        if candidate.endswith("게"):
            return f"{candidate} 처리하는 절차와 결과 검증 기준"
        if candidate.endswith("으로"):
            return f"{candidate} 판단·수행하는 절차와 결과 검증 기준"
        return f"{candidate} 관련 실무 적용·검증 절차"
    return ""


def public_task_object(
    *,
    factor_name: Any,
    ksa_type: Any = "",
    element_name: Any = "",
    competency_name: Any = "",
    competency_definition: Any = "",
) -> tuple[str, str]:
    """Choose a grammatical public task object without exposing the KSA label."""

    official = _clean(factor_name, max_chars=120)
    kind = normalize_ksa_type(ksa_type, official)

    # Prefer the factor-specific task.  Element labels are shared by many KSA
    # rows and choosing them first collapses distinct knowledge, skill, and
    # attitude evidence into one interview prompt.
    repaired = _repair_factor_object(official, kind)
    if _useful_object(repaired, official_factor=official):
        return repaired, "factor_repair"

    element = _element_task_object(element_name, kind)
    if _useful_object(element, official_factor=official):
        return element, "element_name"

    definition = _definition_object(competency_definition, competency_name)
    if _useful_object(definition, official_factor=official):
        return definition, "competency_definition"

    competency = _clean(competency_name, max_chars=80)
    if _useful_object(competency, official_factor=official):
        suffix = {
            "지식": " 판단 기준",
            "기술": " 수행 절차",
            "태도": " 행동 기준",
        }.get(kind, " 업무 기준")
        return f"{competency}{suffix}", "competency_name"

    fallback = {
        "지식": "업무 범위와 예외를 구분하는 판단 기준",
        "기술": "업무 수행 절차와 산출물 품질 기준",
        "태도": "상충하는 요구 속에서 지켜야 할 행동 기준",
    }.get(kind, "업무 판단과 수행 기준")
    return fallback, "safe_fallback"


def build_question_task_frame(
    *,
    evidence_row: dict[str, Any] | None,
    factor_name: Any,
    ksa_type: Any = "",
    element_name: Any = "",
    competency_name: Any = "",
    competency_definition: Any = "",
    decision_dilemma: Any = "",
) -> dict[str, str]:
    """Build the semantic bridge used by model and deterministic renderers."""

    kind = normalize_ksa_type(ksa_type, factor_name)
    task_object, surface_source = public_task_object(
        factor_name=factor_name,
        ksa_type=kind,
        element_name=element_name,
        competency_name=competency_name,
        competency_definition=competency_definition,
    )
    task_statement = {
        "지식": f"{task_object}을 근거로 적용 범위와 예외를 판단한다",
        "기술": f"{task_object}에 따라 조치하고 산출물의 품질을 확인한다",
        "태도": f"{task_object}을 압박과 이해 충돌 속에서도 행동으로 보여준다",
    }.get(kind, f"{task_object}에 따라 판단하고 결과를 확인한다")
    observable_behavior = {
        "지식": "확인 자료, 판단 기준, 적용 범위, 예외와 오류 위험을 설명한다",
        "기술": "수행 순서, 사용 자료·도구, 조치, 산출물과 품질 확인 결과를 제시한다",
        "태도": "상충하는 요구 속 선택 행동, 감수한 비용과 후속 책임을 제시한다",
    }.get(kind, "판단 근거, 구체적 행동, 산출물과 결과 확인 방법을 제시한다")
    return {
        "evidence_id": stable_ksa_evidence_id(evidence_row) if evidence_row else "",
        "ksa_type": kind,
        "task_object": task_object,
        "task_statement": task_statement,
        "decision_dilemma": _clean(decision_dilemma, max_chars=240),
        "observable_behavior": observable_behavior,
        "surface_source": surface_source,
    }


def has_dangling_surface(value: Any) -> bool:
    """Expose the public-language guard for validation and regression tests."""

    return bool(_DANGLING_END_RE.search(_clean(value, max_chars=200)))


def official_ksa_surface_aliases(factor_name: Any) -> list[str]:
    """Return official or mechanically truncated labels unsafe for public copy."""

    official = _clean(factor_name, max_chars=160)
    if not official:
        return []
    aliases = [official]
    stripped = _KSA_TYPE_SUFFIX_RE.sub("", official).strip()
    if stripped and stripped != official:
        aliases.append(stripped)
    return list(dict.fromkeys(aliases))


def replace_official_ksa_surface(value: Any, factor_name: Any, public_surface: Any) -> tuple[str, bool]:
    """Replace unsafe evidence labels while correcting an attached Korean particle."""

    text = str(value or "").strip()
    surface = _clean(public_surface, max_chars=160)
    if not text or not surface:
        return text, False

    def _has_final_consonant(korean_text: str) -> bool:
        for char in reversed(str(korean_text or "").strip()):
            code = ord(char)
            if 0xAC00 <= code <= 0xD7A3:
                return ((code - 0xAC00) % 28) != 0
        return False

    particle_pairs = {
        "은": ("은", "는"),
        "는": ("은", "는"),
        "이": ("이", "가"),
        "가": ("이", "가"),
        "을": ("을", "를"),
        "를": ("을", "를"),
        "과": ("과", "와"),
        "와": ("과", "와"),
    }
    alias_patterns: list[str] = []
    for alias in sorted(official_ksa_surface_aliases(factor_name), key=len, reverse=True):
        alias_pattern_parts: list[str] = []
        whitespace_pending = False
        for char in alias:
            if char.isspace():
                whitespace_pending = True
                continue
            if whitespace_pending:
                alias_pattern_parts.append(r"\s*")
                whitespace_pending = False
            if char in "·ㆍ․":
                alias_pattern_parts.append(r"[·ㆍ․]")
            else:
                alias_pattern_parts.append(re.escape(char))
        alias_patterns.append("".join(alias_pattern_parts))

    if not alias_patterns:
        return text, False
    pattern = re.compile(
        rf"(?<![0-9A-Za-z가-힣])(?:['\"“”‘’]\s*)?(?:{'|'.join(alias_patterns)})(?:\s*['\"“”‘’])?"
        rf"(?P<particle>은|는|이|가|을|를|과|와)?(?![0-9A-Za-z가-힣])"
    )

    def _replacement(match: re.Match[str]) -> str:
        particle = str(match.group("particle") or "")
        if not particle:
            return surface
        final_particle, open_particle = particle_pairs[particle]
        return surface + (final_particle if _has_final_consonant(surface) else open_particle)

    repaired = pattern.sub(_replacement, text)
    return repaired, repaired != text
