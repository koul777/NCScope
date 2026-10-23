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
_KNOWLEDGE_HEADS = (
    "방법", "개념", "원리", "종류", "법령", "법규", "규정", "제도",
    "현황", "동향", "이해", "특성", "구조",
)
_KNOWLEDGE_HEAD_RE = re.compile("|".join(_KNOWLEDGE_HEADS))
_DANGLING_END_RE = re.compile(
    r"(?:에\s*대한|에\s*관한|을\s*위한|를\s*위한|에\s*따른|을\s*통한|를\s*통한|"
    r"하고|하며|하여|해서|따른|통한|관련된|하는|되는|대한|위한|의)\s*$"
)
# Only used when dropping 수행. Ambiguous endings keep that noun, rather
# than exposing a connective, particle or modifier as the task object.
_PERFORMANCE_NON_NOUN_END_RE = re.compile(
    r"(?:및|또는|혹은|그리고|[·ㆍ․,/]|와|과|에서|으로|로|을|를|의|에|은|는|"
    r"한|된|인|할|될|던|게|히|적으로)\s*$"
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


_EXPLICIT_KSA_WORDS = {
    "지식": frozenset({"knowledge", "지식"}),
    "기술": frozenset({"skill", "skills", "기술", "스킬"}),
    "태도": frozenset({"attitude", "attitudes", "태도"}),
}
_EXPLICIT_KSA_LETTERS = {"k": "지식", "s": "기술", "a": "태도"}
_GLUED_KSA_WORDS = frozenset(
    {"knowledge", "skill", "skills", "attitude", "attitudes"}
)


def classify_explicit_ksa_type(value: Any) -> str:
    """Map an explicit KSA type label to 지식, 기술, or 태도.

    Official rows and model payloads use Korean names, English names,
    single-letter codes, and transport forms such as ``K/Knowledge``.
    A factor name is not consulted here. Mixed labels such as
    ``Knowledge/Skill`` stay unresolved so one field cannot be forced into
    the wrong measurement type.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"[^0-9a-z가-힣]+", "", raw.casefold())
    # Korean type words keep their historical priority, including labels such
    # as ``기술적 태도`` that name the earlier type in the source text.
    if "지식" in compact:
        return "지식"
    if any(token in compact for token in ("기술", "스킬")):
        return "기술"
    if "태도" in compact:
        return "태도"

    tokens = re.findall(r"[a-z]+|\d+", raw.casefold())
    expanded: list[str] = []
    has_digit = False
    for token in tokens:
        if token.isdigit():
            has_digit = True
            continue
        expanded.append(token)
        if len(token) > 1 and token[0] in _EXPLICIT_KSA_LETTERS:
            remainder = token[1:]
            if remainder in _GLUED_KSA_WORDS:
                expanded.append(remainder)

    word_hits = [
        kind
        for kind, vocab in _EXPLICIT_KSA_WORDS.items()
        if any(token in vocab for token in expanded)
    ]
    if len(word_hits) == 1:
        return word_hits[0]
    if len(word_hits) > 1:
        return ""
    if (
        not has_digit
        and len(expanded) == 1
        and expanded[0] in _EXPLICIT_KSA_LETTERS
    ):
        return _EXPLICIT_KSA_LETTERS[expanded[0]]
    return ""


def normalize_ksa_type(value: Any, factor_name: Any = "") -> str:
    explicit = classify_explicit_ksa_type(value)
    if explicit:
        return explicit

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


def _compact_knowledge_base(value: str, *, max_chars: int = 62) -> str:
    """Keep the knowledge head; shorten glosses and leading modifiers only."""
    text = str(value or "").strip(" ,.;:·ㆍ/-")
    if len(text) >= max_chars and re.search(r"에\s*(?:대한|관한)\s+\S+적$", text):
        return ""
    if len(text) <= max_chars:
        return text
    # Parenthetical expansions can consume the entire budget before the
    # actual subject (e.g. GIS ... 인구통계 분석 방법) is reached.
    def remove_gloss(match: re.Match[str]) -> str:
        particle = match.group("particle")
        noun_particle = _object_particle(text[:match.start()])
        if noun_particle == "을":
            return particle.translate(str.maketrans("를가는와", "을이은과"))
        if noun_particle == "를":
            return particle.translate(str.maketrans("을이은과", "를가는와"))
        return particle

    while re.search(r"\([^()]*\)", text):
        text = re.sub(r"\([^()]*\)(?P<particle>[을를이가은는과와]?)", remove_gloss, text)
    text = re.sub(r"\s+", " ", text).strip()
    if _ends_with_modifier(text) or _DANGLING_END_RE.search(text):
        return ""
    if len(text) <= max_chars:
        return text
    # A labelled list has its head before the details. Cutting its front
    # would turn one enumerated item into the whole knowledge topic.
    if re.search(r"\s[-:：]\s|:\s", text):
        return ""
    heads = set(_KNOWLEDGE_HEAD_RE.findall(text))
    words = text.split()
    for start in range(1, len(words) - 1):
        tail = " ".join(words[start:])
        if len(tail) > max_chars or words[start - 1].endswith("의"):
            continue
        if re.search(r"^(?:및|또는|혹은|그리고|등)$|(?:따른|대한|관한|위한|통한|의한|관련된)$", words[start]):
            continue
        if words[start].endswith((",", "·", "ㆍ")) or tail.count("(") != tail.count(")"):
            continue
        if heads - set(_KNOWLEDGE_HEAD_RE.findall(tail)):
            continue
        return tail
    # Never fall back to a prefix cut: the caller can use the official element.
    return ""


def _object_particle(word: str) -> str:
    """Return 을/를 for the last Korean word, or "" when it cannot be told."""

    text = str(word or "").strip()
    # ``표준(Code &; Standard)`` takes the particle of ``표준``.
    while True:
        stripped = re.sub(r"\s*\([^()]*\)$", "", text).strip()
        if stripped == text:
            break
        text = stripped
    last = text[-1:]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 else "를"
    return ""


def _nominalize_attitude_clause(value: Any) -> str:
    text = _clean(value, max_chars=160)
    text = re.sub(r"위해\s*(?:적극적으로\s*)?노력하려는", "위한 적극적", text)
    text = re.sub(r"위해\s*(?:적극적으로\s*)?노력하는", "위한 적극적", text)
    # ``커뮤니케이션을 하고자 하는`` keeps the official verb ``하다``.
    # Rewriting it as ``실행하는`` named an action the KSA never states.
    text = re.sub(
        r"(?P<object>.+?(?:을|를))\s*하고자\s*하는",
        r"\g<object> 하려는",
        text,
    )
    # ``하고자 한`` is the same present intention; a past ``하려 했던``
    # clashed with the hypothetical situation/discussion/in-basket prompts.
    text = re.sub(
        r"(?P<object>.+?(?:을|를))\s*하고자\s*한(?![가-힣])",
        r"\g<object> 하려는",
        text,
    )
    # When "하고자" follows a verb stem ("달성하고자") it expresses
    # intention, not the generic verb "하다". Preserve that stem instead of
    # creating fused wording such as "달성실행하는".
    text = re.sub(r"(?P<stem>[가-힣]+)하고자\s*하는", r"\g<stem>하려는", text)
    text = re.sub(r"(?P<stem>[가-힣]+)하고자\s*한(?![가-힣])", r"\g<stem>하려는", text)
    # ``제시할 수 있는`` stays as written: turning it into ``가능한`` fused
    # the verb into ``제시가능한``.
    return text.strip()


# Attitude labels that end in a trait noun read as ``책임감 있는 행동``.
# Nouns such as ``사고``, ``숙지`` or ``달성`` do not take ``있는`` and are
# left as written.
_ATTITUDE_TRAIT_NOUNS = (
    "책임감",
    "사명감",
    "적극성",
    "성실성",
    "유연성",
    "융통성",
    "일관성",
    "자부심",
    "열의",
    "성의",
)

# Verbal attitude nouns that read badly as ``… 사고 행동`` or ``… 대처
# 행동``.  The official noun becomes its own verb (``사고하는``,
# ``대처하는``); nothing else is added.  Value: (verb stem, object kind),
# where "obj" takes 을/를, "에" takes 에 and "" takes no object.
_ATTITUDE_VERBAL_NOUNS = {
    "사고": ("사고", ""),
    "숙지": ("숙지", "obj"),
    "달성": ("달성", "obj"),
    "판단력": ("판단", "obj"),
    "관찰력": ("관찰", "obj"),
    "대처": ("대처", "에"),
}
_NOUN_ENDING_IN_UI = (
    "주의", "협의", "합의", "회의", "논의", "창의", "건의", "토의", "결의",
    "문의", "심의", "정의", "동의", "강의", "의의",
)
_LINKING_MODIFIERS = ("대한", "관한", "위한", "통한", "의한", "따른")
# ``필요한 판단력`` describes the noun, not a manner: ``필요하게 판단하는``
# is broken Korean.  ``기초한``·``근거한`` are verbs, not manners.
_NON_MANNER_MODIFIERS = (
    "필요한", "중요한", "가능한", "관련한", "인한", "기초한", "기반한", "근거한", "해당한",
    "필수적", "필수적인", "기본적", "기본적인", "핵심적", "핵심적인",
)


def _adverb_from_modifier(word: str) -> str:
    """``정확한`` → ``정확하게``, ``논리적인`` → ``논리적으로``; else ""."""

    if word in _LINKING_MODIFIERS or word in _NON_MANNER_MODIFIERS:
        return ""
    # ``선정가능한`` written as one word is still ``가능한``.
    if word.endswith(("가능한", "필요한", "중요한")):
        return ""
    if word.endswith("적인") and len(word) > 2:
        return f"{word[:-1]}으로"
    if word.endswith("적") and len(word) > 1:
        return f"{word}으로"
    if word.endswith(("있는", "없는", "깊은", "높은", "넓은", "좋은")):
        return f"{word[:-1]}게"
    if word.endswith("빠른"):
        return f"{word[:-1]}르게"
    if word.endswith("한") and len(word) > 1:
        return f"{word[:-1]}하게"
    return ""


def _verbalize_attitude_noun(candidate: str, *, intention: bool) -> str:
    """Turn ``정확한 판단력`` into ``정확하게 판단하는 행동``.

    Only the six verbal nouns above are changed, and only when the words
    before them can be read as the object or adverb of that verb.  Anything
    else (coordinated nouns, a verb clause modifying the noun, a bare
    ``사고`` that may mean an accident) returns "" and stays as written.
    """

    words = str(candidate or "").split()
    if not words or words[-1] not in _ATTITUDE_VERBAL_NOUNS:
        return ""
    verb, object_kind = _ATTITUDE_VERBAL_NOUNS[words[-1]]
    rest = words[:-1]
    adverb = ""
    if rest:
        modifier = rest[-1]
        adverb = _adverb_from_modifier(modifier)
        if adverb:
            rest = rest[:-1]
            # ``선별할 수 있는 판단력`` is a capability clause, not an adverb.
            if modifier in ("있는", "없는") and rest[-1:] == ["수"]:
                return ""
            # ``객관적으로 이해한 판단력``: after an adverb or object, ``~한``
            # is a past verb clause, not a manner (``이해하게``).
            if modifier.endswith("한") and rest and rest[-1].endswith(("으로", "게", "히", "을", "를")):
                return ""
            # ``주의 깊은``, ``끈기 있는`` are one modifier with their noun.
            if modifier in ("깊은", "있는", "없는") and rest and re.fullmatch(r"[가-힣]+", rest[-1]):
                adverb = f"{rest[-1]} {adverb}"
                rest = rest[:-1]
            # So is ``목표 지향적`` before ``사고``.
            elif (
                verb == "사고"
                and modifier.endswith(("적", "적인"))
                and rest
                and re.fullmatch(r"[가-힣]+", rest[-1])
                and rest[-1] not in _LINKING_MODIFIERS
                and not re.search(r"(?:의|에|을|를|로|와|과|은|는|이|가|한|된|인|할|던|적|른|운)$", rest[-1])
            ):
                adverb = f"{rest[-1]} {adverb}"
                rest = rest[:-1]
            # ``신속하고 정확한``: the whole adverb chain moves together.
            while rest and rest[-1].endswith(("하고", "하며", "이며", "이고")):
                adverb = f"{rest[-1]} {adverb}"
                rest = rest[:-1]
    if verb == "사고" and not adverb:
        return ""
    if rest:
        previous = rest[-1]
        if previous == "및" or previous.endswith((",", "와", "과", "/", "·")):
            return ""
        # ``판단력 관찰력`` is a space-separated list, not an object.
        if previous in _ATTITUDE_VERBAL_NOUNS or previous.endswith("력"):
            return ""
        if previous in ("대한", "관한"):
            owner = rest[-2] if len(rest) >= 2 else ""
            if object_kind == "obj" and owner.endswith("에") and len(owner) > 1:
                particle = _object_particle(owner[:-1])
                if not particle:
                    return ""
                rest = [*rest[:-2], f"{owner[:-1]}{particle}"]
            elif object_kind == "에" and owner.endswith("에"):
                rest = rest[:-1]
            else:
                rest = [*rest[:-1], f"{previous[:-1]}해"]
        elif previous == "위한":
            rest = [*rest[:-1], "위해"]
        elif previous == "따른":
            rest = [*rest[:-1], "따라"]
        elif previous.endswith("별"):
            rest = [*rest[:-1], f"{previous}로"]
        elif previous in ("시", "때") or previous.endswith(
            ("에", "에서", "으로", "하고", "하며", "하게", "있게", "히")
        ):
            pass
        elif previous.endswith(("을", "를")):
            # ``요령을 숙지``: the object is already marked.
            if object_kind != "obj":
                return ""
        elif re.search(r"(?:할|될)$", previous) or (
            previous.endswith("로") and previous[:-1] in _ATTITUDE_VERBAL_NOUNS
        ):
            # ``숙지로`` is an instrumental phrase; ``회로`` stays a noun.
            return ""
        elif previous.endswith("의") and previous not in _NOUN_ENDING_IN_UI:
            if not object_kind:
                return ""
            owner = previous[:-1]
            particle = _object_particle(owner) if object_kind == "obj" else "에"
            if not owner or not particle:
                return ""
            rest = [*rest[:-1], f"{owner}{particle}"]
        elif previous in _LINKING_MODIFIERS or _ends_with_modifier(previous) or re.search(
            r"(?:는|던|된|은|운|ㄴ)$", previous
        ):
            return ""
        else:
            if not object_kind:
                return ""
            # ``적극적인 문제 대처``: the modifier belongs to ``문제 대처``;
            # ``적극적인 문제에 대처하는`` would break it.
            if len(rest) >= 2 and rest[-2] not in _LINKING_MODIFIERS and (
                _ends_with_modifier(rest[-2]) or re.search(r"(?:는|은|던|된)$", rest[-2])
            ):
                return ""
            particle = _object_particle(previous) if object_kind == "obj" else "에"
            if not particle:
                return ""
            rest = [*rest[:-1], f"{previous}{particle}"]
    ending = "하려는" if intention else "하는"
    return " ".join([*rest, *([adverb] if adverb else []), f"{verb}{ending}", "행동"])


def _compact_clause_tail(value: str, *, max_chars: int = 77) -> str:
    """Shorten a verbal attitude clause from the front so its verb survives.

    Up to ``max_chars`` the whole official clause is kept (the surface stays
    within the 80-character limit with `` 행동``).  Beyond it, the cut never
    drops the owner of the next word (``협력대상국의``), never starts on an
    enumerated item (``지역적,``), a cut-off modifier or inside parentheses.
    Without such a cut "" lets the caller fall back to the element.
    """

    text = _clean(value, max_chars=180)
    if len(text) <= max_chars:
        return text
    words = text.split()
    for start in range(1, len(words) - 1):
        kept = words[start:]
        if len(" ".join(kept)) > max_chars:
            continue
        first = kept[0]
        if words[start - 1].endswith("의") or first.endswith(","):
            continue
        if ")" in first and "(" not in first:
            continue
        if re.search(r"(?:^(?:및|또는|혹은|그리고|등)$|(?:따른|대한|관한|위한|통한|의한|관련된)$)", first):
            continue
        clause = " ".join(kept)
        if clause.count("(") != clause.count(")"):
            continue
        return clause.strip(" ,.;:·ㆍ/-")
    return ""


def _ends_with_modifier(text: str) -> bool:
    """True when the last word modifies a noun (``세심한``, ``충분한``)."""

    last = str(text or "").split()[-1:] or [""]
    return bool(re.search(r"(?:한|는|은|된|인|던|운|른|적)$", last[0]))


def _nominalize_capability_clause(value: Any, *, max_chars: int = 180) -> str:
    text = _clean(value, max_chars=max_chars)
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
        r"(?P<object>.+?)(?:을|를)\s*(?P<verb>[가-힣·ㆍ]+할)\s*수\s*있는\s*$",
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


def _source_action_work(action: str, *, verbal: bool = False) -> str:
    """Nominalize an existing action without adding a second obligation.

    A bare official action label cannot be returned unchanged. A transitive
    action can take its source noun as an object; modifiers and uncertain
    arguments retain the noun phrase with 작업. Never turn a manner such as
    "자동 및 수동" into the object, or attach 을 to "비정상 대응".
    """
    action = _clean(action, max_chars=180)
    if not action:
        return ""
    parts = action.rsplit(" ", 1)
    if len(parts) == 2:
        obj, verb = parts
        verbal = verbal or bool(re.search(r"(?:을|를)$|(?:을|를)\s+.+(?:게|히|으로|로)$", obj))
        transitive = {
            "수립", "수집", "정보검색", "분류", "처리", "운영", "관리", "파악",
            "분석", "검증", "점검", "작성", "산정", "설정", "선택", "선정",
            "선별", "활용", "적용", "해석", "조사", "포착", "수정·보완", "배양",
            "개발", "유지", "조작", "계산", "정리", "제작", "설계", "구현",
        }
        manner = {"실시간", "수시", "상시", "정기", "즉시", "사전", "사후", "우선", "동시", "자동", "수동", "일괄"}
        # These words qualify the action; they are not its direct object.
        # In particular 작성·운영 / 작성 운영 must retain both source acts.
        dependent = {"관련", "전", "후", "시", "중", "간", "내", "외", "등"}
        noun_endings = (
            "안전", "비중", "하중", "지시", "제시", "표시", "표시등", "전등",
            "경고등", "조명등", "신호등", "회전", "발전", "충전", "방전",
            "정전", "보전", "도시", "게시", "전시", "투시", "감시", "실시",
            "기후", "징후",
        )
        attached_dependent = obj.endswith(("관련", "전", "후", "시", "중", "등")) and not obj.endswith(noun_endings)
        action_nouns = transitive | {
            "수행", "검토", "평가", "판단", "확인", "예측", "측정", "조치",
            "식별", "설명", "조정", "조율", "대응", "이해", "사용",
            "기획", "실행", "할당", "배치", "결정", "판별", "도출", "운반",
            "처치", "치료", "문서화", "가공", "조절", "교체", "해결", "반영",
            "갱신", "계획", "구성", "비교", "통제", "보고", "분할", "추정",
            "방지", "추출", "운전", "보수", "응용", "진단", "제시", "개선",
            "확보", "지원", "교육", "제어", "정비", "의사소통",
        }
        if (
            not verbal
            and verb in transitive
            and not _PERFORMANCE_NON_NOUN_END_RE.search(obj)
            and not _ends_with_modifier(obj)
            and not obj.endswith("별")
            and obj.split()[-1] not in manner
            and obj.split()[-1] not in dependent
            and not attached_dependent
            and not obj.endswith((*action_nouns, "관련"))
            and not re.search(r"(?:대한|관한|통한|따른|위한|하고|하며|하여|함께|따라|통해|위해|대해)$", obj)
        ):
            particle = _object_particle(obj)
            if particle:
                action = f"{obj}{particle} {verb}"
                verbal = True
    surface = f"{action}하는 작업" if verbal else f"{action} 작업"
    # Preserve named methods in full; an overlong phrase uses the element
    # fallback rather than dropping the task's object from its front.
    return surface if len(surface) <= 80 else ""


def _element_task_object(element_name: Any, kind: str) -> str:
    element = _KSA_TYPE_SUFFIX_RE.sub("", _clean(element_name, max_chars=100)).strip()
    if not element:
        return ""
    if kind in {"지식", "기술"}:
        # Keep the element's own action; 시키기/해내기 must not lose their
        # causative/completion verb when used as the fallback task.
        if element.endswith(("시키기", "해내기")):
            clause = re.sub(r"시키기$", "시키는", element)
            clause = re.sub(r"해내기$", "해내는", clause)
            clause = _compact_task_base(clause, max_chars=70)
            return f"{clause} 작업" if kind == "기술" else f"{clause} 데 필요한 내용"
        action = _compact_task_base(re.sub(r"\s*하기\s*$", "", element).strip())
        if not action:
            return ""
        if kind == "지식":
            return f"{action}에 관한 내용"
        return action if _useful_object(action) else _source_action_work(action)
    element = re.sub(r"\s*(?:하기|시키기|해내기)\s*$", "", element).strip()
    element = _compact_task_base(element)
    if not element:
        return ""
    suffix = {
        "태도": " 수행 시 행동",
    }.get(kind, " 업무 판단·수행 기준")
    return f"{element}{suffix}"


def _repair_factor_object(factor_name: Any, kind: str) -> str:
    label = _clean(factor_name, max_chars=len(str(factor_name or "")) if kind == "지식" else 100)
    candidate = _KSA_TYPE_SUFFIX_RE.sub("", label).strip()
    if not candidate:
        return ""

    if kind == "태도":
        # ``의지``·``의식``·``노력`` state an intention (``숙지하려는``);
        # ``태도``·``자세``·``능력`` keep the plain ``숙지하는``.
        intention_head = bool(re.search(r"(?:의지|의식|노력)$", candidate))
        # Replace only the attitude head noun.  The official clause before
        # it (``정확도를 기하려는``, ``고객과 협의하려는``) is already a
        # modifier of the observable behaviour; stripping its ending left
        # broken stems such as ``정확도 기`` or ``협 관련``.
        verbal_clause = re.search(
            r"(?:려는|고자\s*하는|고자\s*한|하는|수\s*있는|(?:을|를|기)\s*위한)"
            r"\s*(?:태도|자세|의지|의식|노력)\s*$",
            candidate,
        )
        head_nouns = r"태도|자세|의지|의식|노력" if verbal_clause else r"태도|자세|의지|의식"
        candidate = re.sub(rf"\s*(?:{head_nouns})\s*$", "", candidate).strip()
        # Some extracted rows already contain the public suffix. Remove it
        # before rebuilding the surface so ``행동 기준 행동 기준`` cannot be
        # produced.
        candidate = re.sub(r"\s*행동(?:\s*기준)?\s*$", "", candidate).strip()
        # NCS rows sometimes end with a purpose clause such as
        # ``입사예정자의 조직적응을 적극적으로 도와주고자 하는 태도``.
        # After removing ``태도`` the remaining ``...도와주고자`` must be
        # nominalized before the public 행동 기준 suffix is attached.
        candidate = re.sub(
            r"(?P<object>.+?)(?:을|를)\s*(?:적극적으로\s*)?(?:도와주고자(?:\s*하는)?|도와주려는|지원하고자(?:\s*하는)?|지원하려는)$",
            r"\g<object> 지원",
            candidate,
        ).strip()
        candidate = _nominalize_attitude_clause(candidate)
        # ``선택가능한 결정적 행동`` is an incomplete/non-operational source
        # fragment, not a measurable task. Let the caller fall back to the
        # NCS element or competency name instead of exposing it verbatim.
        if re.search(r"선택\s*(?:가능|할\s*수\s*있)|결정적\s*$|결정적\s+행동", candidate):
            return ""
        if verbal_clause and re.search(r"(?:려는|하는|위한|있는)$", candidate):
            clause = _compact_clause_tail(candidate)
            if re.search(r"(?:려는|하는|위한|있는)$", clause):
                return f"{clause} 행동"
        # ``협의``, ``주의``, ``열의`` are nouns, not a dangling genitive ``의``.
        if _DANGLING_END_RE.search(candidate) and not re.search(
            r"(?:(?:^|\s)[가-힣]|주|협|합|회|논|창|건|토|결|문|심)의$", candidate
        ):
            candidate = f"{_DANGLING_END_RE.sub('', candidate).strip()} 관련"
        candidate = re.sub(
            r"(?P<object>.+?(?:을|를))\s*위해\s*(?:적극적으로\s*)?노력(?:하는|하려는)?\s*$",
            r"\g<object> 위한 적극적",
            candidate,
        ).strip()
        candidate = re.sub(r"\s*(?:적극적으로\s*)?노력(?:하는|하려는)?\s*$", "", candidate).strip()
        # Attitude surfaces name the observable behaviour itself.  A
        # ``행동 기준`` suffix turned them into a rule to recite and made
        # prompts repeat "행동 기준이 드러난 … 행동".
        if candidate.endswith("유지"):
            # ``정확성 유지`` names keeping the accuracy; ``준수`` is a
            # different obligation the KSA never states.
            keep_base = re.sub(r"\s*(?:을|를)$", "", _compact_task_base(candidate[:-2])).strip()
            if keep_base:
                particle = _object_particle(keep_base)
                if not particle:
                    # ``KPI 유지``: keep the official wording, do not guess.
                    return f"{keep_base} 유지 행동"
                return f"{keep_base}{particle} 유지하는 행동"
        if candidate.endswith("우선"):
            priority_base = re.sub(r"(?:을|를)$", "", candidate[:-2].strip()).strip()
            return f"{_compact_task_base(priority_base)} 우선 행동"
        if candidate.endswith("주의") and not re.search(r"[가-힣]주의$", candidate):
            # Keep the official ``주의``; ``안전`` is not in the KSA.
            caution_base = _compact_task_base(candidate[:-2])
            if caution_base and not _ends_with_modifier(caution_base):
                return f"{caution_base}에 주의하는 행동"
            return f"{caution_base} 주의 행동".strip()
        def _attitude_object_action(match: re.Match[str]) -> str:
            object_text = str(match.group("object") or "").strip()
            particle = str(match.group("particle") or "").strip()
            action = str(match.group("action") or "").strip()
            if action.endswith(("려는", "는", "했던")):
                return f"{object_text}{particle} {action}"
            return f"{object_text} {action}"

        candidate = re.sub(
            r"(?P<object>[0-9A-Za-z가-힣()·ㆍ/-]+)(?P<particle>을|를)\s+"
            r"(?P<action>[0-9A-Za-z가-힣()·ㆍ/-]+)$",
            _attitude_object_action,
            candidate,
        ).strip()
        candidate = _compact_task_base(candidate)
        observance = re.match(r"(?P<object>.+?)의\s+준수$", candidate)
        if observance:
            particle = _object_particle(observance.group("object"))
            if not particle:
                # ``Code의 준수``: keep the official particle, do not guess.
                return f"{candidate} 행동"
            candidate = f"{observance.group('object')}{particle} 준수"
        verbalized = _verbalize_attitude_noun(candidate, intention=intention_head)
        if verbalized:
            return verbalized
        if candidate.endswith(("의사소통", "소통", "협업", "조율", "준수")):
            return f"{candidate}하는 행동"
        if candidate.endswith("이해"):
            # ``공유`` is not in the KSA; keep ``이해`` as the act.
            if _ends_with_modifier(candidate[:-2]):
                return f"{candidate} 행동"
            return f"{candidate}하는 행동"
        if candidate.endswith(_ATTITUDE_TRAIT_NOUNS):
            return f"{candidate} 있는 행동"
        return f"{candidate} 행동" if candidate else ""

    if kind == "지식":
        if _DANGLING_END_RE.search(candidate):
            candidate = _DANGLING_END_RE.sub('', candidate).strip()
        knowledge_base = _nominalize_capability_clause(candidate, max_chars=len(candidate))
        if re.search(r"수\s*있는", knowledge_base):
            return ""
        knowledge_base = re.sub(r"전략적인", "전략적", knowledge_base).strip()
        knowledge_base = re.sub(r"\s+관련\s+", " ", knowledge_base).strip()
        knowledge_base = re.sub(r"\s*관련$", "", knowledge_base).strip()
        # Some sources already name content. Remove the relation before
        # rebuilding it so 내용에 관한 내용 cannot be stacked.
        knowledge_base = re.sub(r"에\s*(?:대한|관한)\s*내용$", " 내용", knowledge_base).strip()
        # A trend, concept or law is knowledge content, not a new standard
        # or analysis task. Keep those source nouns and let the template ask
        # how the candidate uses that knowledge in a decision.
        knowledge_base = _compact_knowledge_base(knowledge_base)
        # Removing a long gloss can expose a trailing "...에 관한 지식".
        if knowledge_base.endswith("지식"):
            knowledge_base = _DANGLING_END_RE.sub(
                "", re.sub(r"\s*지식$", "", knowledge_base)
            ).strip()
        # Preserve a complete source check clause when it fits, including
        # its parenthetical terms, before considering a shortened noun phrase.
        source_check = re.match(
            r"(?P<scope>.+(?:는지|여부))\s*(?P<action>확인|검증)\s*(?:할\s*수\s*있는|하는)",
            candidate,
        )
        if source_check and not candidate.endswith(_KNOWLEDGE_HEADS):
            check_surface = f"{source_check.group('scope')}를 {source_check.group('action')}하는 내용"
            if _useful_object(check_surface, official_factor=label):
                return check_surface
        if not knowledge_base:
            return ""
        if knowledge_base.endswith("내용") and _key(knowledge_base) != _key(label):
            return knowledge_base
        return f"{knowledge_base}에 관한 내용"

    if kind == "기술":
        candidate = _nominalize_capability_clause(candidate)
        # Irregular verbs (옮길/도울/다룰 …) cannot be nominalized by
        # deleting 할. Use the official element instead of a capability task.
        if re.search(r"수\s*있는", candidate):
            return ""
        candidate = re.sub(
            r"(?P<object>.+?)(?:을|를)\s*위한\s+(?:관련\s+)?(?P<action>.+)$",
            r"\g<object> \g<action>",
            candidate,
        ).strip()
        candidate = re.sub(r"수행하는\s*$", "실행하는", candidate)
        candidate = _DANGLING_END_RE.sub("", candidate).strip()
        candidate = re.sub(r"할\s*수\s*있도록", "하기 위한", candidate)
        candidate = re.sub(r"할\s*수\s*있는", "하기 위한", candidate)
        candidate = re.sub(r"하기\s*위한\s*$", "하는", candidate).strip()
        candidate = re.sub(r"\s*하려는\s*$", "", candidate).strip()
        candidate = re.sub(r"\s*에\s*(?:대한|관한)\s*(?=(?:활용|해석|분석|검토|판단|적용)\s*$)", " ", candidate)
        candidate = re.sub(r"(?P<action>활용|설정)\s+수행\s*$", r"\g<action>", candidate)
        candidate = re.sub(r"관리\s+관리$", "관리", candidate)
        candidate = re.sub(
            r"(?P<object>.+?)(?:을|를)\s*(?P<action>정리|설정|선택|선정|선별|조정|조율|활용|적용|"
            r"수집|정보검색|분류|처리|운영|관리|파악|점검|배양|유지|"
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
            return _source_action_work(candidate, verbal=True)
        if candidate.endswith("수행"):
            task = re.sub(r"\s*수행$", "", candidate).strip()
            if (
                _useful_object(task, official_factor=label)
                and not _PERFORMANCE_NON_NOUN_END_RE.search(task)
            ):
                return task
            # Keep 수행 when the remainder is incomplete or too short (면담).
            # It is still distinct from the official 능력/기술 evidence label.
            # A middle dot or slash join keeps the source spacing
            # (``계획·수행``); only a bare noun gets a space
            # (``역할수행`` -> ``역할 수행``).
            if re.search(r"[·ㆍ․/]\s*수행$", candidate) or not task:
                retained = candidate
            else:
                retained = f"{task} 수행"
            if _useful_object(retained, official_factor=label):
                return retained
        coordination = re.match(r"(?P<object>.+?)\s*(?P<action>조율|조정|중재)$", candidate)
        if coordination:
            # Coordination skills are the coordinating act itself; do not turn
            # them into a verification procedure the KSA never names.
            coordination_object = _compact_task_base(coordination.group("object"))
            if coordination_object:
                return f"{coordination_object} 조정" if coordination.group("action") == "중재" else (
                    f"{coordination_object} {coordination.group('action')}"
                )
        # A skill names the act, not an extra verification procedure. Keep
        # the factor-specific object when removing its type/capability label.
        # If the result is the complete label, turn its own action into a
        # clause instead of adding a verification procedure.
        skill_actions = (
            (r"\s*예측\s*기법$", " 예측"),
            (r"\s*기법$", " 방법 적용"),
            (r"\s*시스템$", " 도구 활용"),
            (r"\s*수립$", " 수립"),
            (r"\s*분석$", " 분석"),
            (r"\s*검증$", " 검증"),
            (r"\s*작성$", " 작성"),
            (r"\s*산정$", " 계산"),
            (r"\s*활용$", " 활용"),
            (r"\s*적용$", " 적용"),
            (r"\s*해석$", " 해석"),
            (r"\s*의사결정$", " 의사결정"),
            (r"\s*포착$", " 포착"),
            (r"\s*수정·보완$", " 수정·보완"),
            (r"\s*개발$", " 개발"),
            (r"\s*수집$", " 수집"),
            (r"\s*정보검색$", " 정보 검색"),
            (r"\s*분류$", " 분류"),
            (r"\s*처리$", " 처리"),
            (r"\s*운영$", " 운영"),
            (r"\s*관리$", " 관리"),
            (r"\s*파악$", " 파악"),
            (r"\s*점검$", " 점검"),
            (r"\s*설정$", " 설정"),
            (r"\s*선택$", " 선택"),
            (r"\s*선정$", " 선정"),
            (r"\s*선별$", " 선별"),
            (r"\s*조사$", " 조사"),
            (r"\s*배양$", " 배양"),
            (r"\s*유지$", " 유지"),
        )
        for pattern, replacement in skill_actions:
            if re.search(pattern, candidate):
                action = re.sub(pattern, replacement, candidate).strip()
                # A short action (``분석``) is rejected by public_task_object,
                # which then uses the element name. Falling through to the
                # replacements below instead invented wording such as
                # ``검토 절차`` for ``분석 능력``.
                if _key(action) != _key(label):
                    return action
                return _source_action_work(candidate)
        candidate = re.sub(r"\s*(?:하는|되는)\s*$", "", candidate).strip()
        candidate = _compact_task_base(candidate)
        if not candidate:
            return ""
        if candidate.endswith("절차"):
            return f"{candidate} 적용"
        if candidate.endswith(("방법", "기법")):
            return f"{candidate} 적용"
        if candidate.endswith(("도구", "장비", "시스템", "프로그램", "소프트웨어")):
            return f"{candidate} 활용"
        action_suffixes = (
            "기획", "실행", "할당", "배치", "결정", "판별", "식별", "도출", "구현",
            "설계", "제작", "사용", "운반", "처치", "치료", "문서화", "시뮬레이션",
            "가공", "조절", "정리", "교체", "대응", "해결", "반영", "갱신", "계획",
            "구성", "비교", "통제", "보고", "분할", "계산", "추정", "예측", "방지",
            "추출", "운전", "보수", "응용", "조작", "평가", "진단", "제시", "개선",
        )
        if candidate.endswith(action_suffixes):
            return candidate if _key(candidate) != _key(label) else _source_action_work(candidate)
        original_clause = _KSA_TYPE_SUFFIX_RE.sub("", label).strip()
        if re.search(r"(?:하는|되는|수\s*있는)$", original_clause):
            clause = re.sub(r"할\s*수\s*있는$", "하는", original_clause)
            clause = _compact_clause_tail(clause, max_chars=77)
            return f"{clause} 작업" if clause else ""
        if re.search(r"[가-힣](?:는|은)$", candidate):
            return f"{candidate} 작업"
        if candidate.endswith(("게", "으로")):
            return ""
        if _key(candidate) != _key(label):
            return candidate
        if candidate.endswith(("요령", "기준", "규칙")):
            return f"{candidate} 적용"
        return f"{candidate} 관련 작업"
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

    kind = normalize_ksa_type(ksa_type, factor_name)
    official = _clean(factor_name, max_chars=len(str(factor_name or "")) if kind == "지식" else 120)

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
            "지식": " 확인 기준",
            "기술": " 수행 절차",
            "태도": " 수행 행동",
        }.get(kind, " 업무 기준")
        return f"{competency}{suffix}", "competency_name"

    fallback = {
        "지식": "업무 범위와 예외를 구분하는 기준",
        "기술": "업무 수행 절차와 산출물 품질 기준",
        "태도": "상충하는 요구 속에서도 책임을 다하는 행동",
    }.get(kind, "업무 판단과 수행 기준")
    return fallback, "safe_fallback"


def _knowledge_subject_label(factor_name: Any) -> str:
    label = _clean(factor_name, max_chars=120)
    if not label:
        return ""
    subject = _KSA_TYPE_SUFFIX_RE.sub("", label).strip()
    subject = re.sub(r"\s*에\s+대한\s*$", "", subject).strip()
    subject = re.sub(r"\s*관련\s*$", "", subject).strip()
    return subject


def _knowledge_task_semantics(
    factor_name: Any,
    task_object: str,
) -> tuple[str, str]:
    default_statement = f"{task_object}을 근거로 적용 범위와 예외를 판단한다"
    default_behavior = "확인 자료, 판단 기준, 적용 범위, 예외와 오류 위험을 설명한다"
    subject = _knowledge_subject_label(factor_name)
    if not subject:
        return default_statement, default_behavior

    if subject == "승인된 변경":
        return (
            "승인된 변경 내용을 확인해 지금 맡은 역할과 목표에 바로 반영할 범위와 제외할 업무를 가른다",
            "당시 상황과 맡은 역할·목표, 확인한 승인 변경 내용과 기존 계획, 무엇을 적용하고 무엇을 제외했는지의 근거, 그 판단 때문에 직접 바꾼 행동과 관찰 결과를 설명한다",
        )
    if subject == "과거 단계 문서":
        return (
            "과거 단계 문서에서 지금 단계에 이어 써야 할 근거와 더 이상 따르지 않을 내용을 가른다",
            "당시 상황과 맡은 역할·목표, 대조한 과거 단계 문서와 현재 조건, 어떤 내용을 적용하거나 배제했는지의 근거, 그 판단 때문에 직접 달라진 행동과 결과를 설명한다",
        )
    if subject == "과거 프로젝트 교훈":
        return (
            "과거 프로젝트 교훈 중 현재 상황에 다시 써야 할 원칙과 그대로 따르지 않을 조건을 가른다",
            "당시 상황과 맡은 역할·목표, 참조한 과거 프로젝트 교훈과 현재 조건의 차이, 적용·배제 근거, 그 판단 때문에 직접 택한 행동과 관찰 결과를 설명한다",
        )
    return default_statement, default_behavior


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
    knowledge_statement, knowledge_behavior = _knowledge_task_semantics(
        factor_name,
        task_object,
    )
    task_statement = {
        "지식": knowledge_statement,
        "기술": f"{task_object}에 따라 조치하고 산출물의 품질을 확인한다",
        "태도": f"{task_object}을 압박과 이해 충돌 속에서도 행동으로 보여준다",
    }.get(kind, f"{task_object}에 따라 판단하고 결과를 확인한다")
    observable_behavior = {
        "지식": knowledge_behavior,
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

    text = _clean(value, max_chars=200)
    return bool(
        _DANGLING_END_RE.search(text)
        or re.search(
            r"(?:하고자|고자)\s*(?:관련\s*)?(?:행동(?:\s*기준)?|확인(?:·판단)?\s*기준|수행·검증\s*절차)$",
            text,
        )
    )


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
