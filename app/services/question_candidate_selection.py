"""Deterministic quality-and-diversity selection for question candidates.

The selector is deliberately self-contained.  Candidate generation is used from
several layers of the application, so importing the larger question pipeline
here would introduce an avoidable circular dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence


_QUESTION_KEYS = ("question", "main_question", "text")
_METHOD_KEYS = ("type", "method", "interview_type", "question_type")
_EVIDENCE_ID_KEYS = ("question_evidence_id", "evidence_id", "ksa_evidence_id")
_FOCUS_KEYS = ("question_focus", "ksa_focus", "focus", "primary_focus")
_NCS_CODE_KEYS = ("ncsClCd", "ncs_code", "ncsCode", "unit_code")
_NCS_NAME_KEYS = (
    "ncsLclasCdnm",
    "ncsMclasCdnm",
    "ncsSclasCdnm",
    "ncsSubdCdnm",
    "compeUnitName",
    "ncs_name",
    "unit_name",
)

_RESPONSE_RE = re.compile(
    r"(?:\?|습니까|겠습니까|주세요|제시|설명|말해|작성|발표|선택|판단|"
    r"분석|도출|수립|대응|조치|보고|결정|어떻게|무엇|why|how|describe|explain)",
    re.IGNORECASE,
)
_NUMBER_OR_SPECIFIC_RE = re.compile(
    r"(?:\d|%|퍼센트|분|시간|일|주|개월|명|건|원|단계|기준|자료|지표|"
    r"규정|절차|이해관계자|고객|예산|납기|품질|위험|리스크|deadline|budget|kpi)",
    re.IGNORECASE,
)

_FIELD_SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "context": re.compile(
        r"(?:상황|현장|업무|프로젝트|사업|고객|민원|생산|운영|시스템|조직|팀|"
        r"이해관계자|case|scenario|project|customer|operation)",
        re.IGNORECASE,
    ),
    "constraint": re.compile(
        r"(?:제약|부족|삭감|초과|마감|기한|납기|긴급|동시|제한|예산|인력|시간|"
        r"규정|보안|안전|위험|리스크|불확실|충돌|갈등|반대|오류|장애|"
        r"constraint|deadline|limited|shortage|budget|risk|conflict)",
        re.IGNORECASE,
    ),
    "judgment": re.compile(
        r"(?:판단|결정|선택|우선순위|기준|근거|대안|비교|분석|검토|조정|"
        r"의사결정|trade.?off|prioriti[sz]|decid|judg|rationale)",
        re.IGNORECASE,
    ),
    "output": re.compile(
        r"(?:산출물|보고서|계획서|제안서|발표|보고|대응안|실행안|개선안|"
        r"일정표|체크리스트|지표|kpi|문서|기록|결과물|deliverable|report|plan)",
        re.IGNORECASE,
    ),
}

_CONSTRAINT_PATTERNS: dict[str, re.Pattern[str]] = {
    "time": re.compile(r"(?:마감|기한|납기|긴급|촉박|\d+\s*(?:분|시간|일)|deadline|time limit)", re.I),
    "budget": re.compile(r"(?:예산|비용|원가|재원|삭감|budget|cost)", re.I),
    "workforce": re.compile(r"(?:인력|인원|담당자|결원|리소스|resource|staff)", re.I),
    "conflict": re.compile(r"(?:갈등|충돌|반대|이견|대립|합의|conflict|disagree)", re.I),
    "uncertainty": re.compile(r"(?:불확실|정보\s*부족|자료\s*부족|예측|변동|unknown|uncertain)", re.I),
    "risk": re.compile(r"(?:위험|리스크|안전|보안|규정|법규|감사|risk|safety|compliance)", re.I),
    "quality": re.compile(r"(?:품질|오류|결함|정확|검증|재작업|quality|error|defect)", re.I),
    "concurrency": re.compile(r"(?:동시|복수|여러|다수|대량|한꺼번에|concurrent|multiple)", re.I),
    "priority": re.compile(r"(?:우선순위|선후|먼저|긴급도|중요도|priorit)", re.I),
}

_SCENARIO_PATTERNS: dict[str, re.Pattern[str]] = {
    "budget": re.compile(r"(?:예산|비용|원가|재원|budget|cost)", re.I),
    "schedule": re.compile(r"(?:일정|마감|기한|납기|지연|deadline|schedule)", re.I),
    "customer": re.compile(r"(?:고객|민원|이용자|사용자|customer|client|user)", re.I),
    "data": re.compile(r"(?:데이터|자료|통계|분석|지표|data|metric|analytics)", re.I),
    "safety": re.compile(r"(?:안전|사고|위험|재해|safety|accident)", re.I),
    "quality": re.compile(r"(?:품질|오류|결함|검수|정확|quality|defect|error)", re.I),
    "system": re.compile(r"(?:시스템|장애|서버|프로그램|전산|system|server|outage)", re.I),
    "stakeholder": re.compile(r"(?:이해관계자|부서|협업|갈등|합의|stakeholder|team|conflict)", re.I),
    "compliance": re.compile(r"(?:규정|법규|감사|보안|개인정보|compliance|regulation|privacy)", re.I),
    "workforce": re.compile(r"(?:인력|인원|결원|배치|역할|workforce|staff)", re.I),
    "project": re.compile(r"(?:프로젝트|사업|과제|project|initiative)", re.I),
    "procurement": re.compile(r"(?:조달|구매|계약|발주|협력사|vendor|procurement|contract)", re.I),
    "production": re.compile(r"(?:생산|공정|설비|제조|production|manufactur)", re.I),
    "document": re.compile(r"(?:보고서|문서|기록|제안서|계획서|report|document)", re.I),
}

_SEMANTIC_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(r"(?:퍼센트|프로|%)", re.I)),
    ("budget", re.compile(r"(?:예산|비용|원가|재원|budget|cost)", re.I)),
    ("reduce", re.compile(r"(?:삭감|감축|줄어|줄이|감소|축소|reduce|cut)", re.I)),
    ("shortage", re.compile(r"(?:부족|결원|모자라|제한된|shortage|insufficient|limited)", re.I)),
    ("workforce", re.compile(r"(?:인력|인원|담당자|리소스|workforce|staff|resource)", re.I)),
    ("deadline", re.compile(r"(?:마감|기한|납기|deadline|due date)", re.I)),
    ("delay", re.compile(r"(?:지연|늦어|차질|delay)", re.I)),
    ("priority", re.compile(r"(?:우선순위|선후|먼저|중요도|긴급도|priorit)", re.I)),
    ("decision", re.compile(r"(?:판단|결정|선택|정하|의사결정|decid|judg|choose)", re.I)),
    ("response", re.compile(r"(?:대응|조치|처리|해결|response|respond|resolve)", re.I)),
    ("conflict", re.compile(r"(?:갈등|충돌|반대|이견|대립|conflict|disagree)", re.I)),
    ("risk", re.compile(r"(?:위험|리스크|안전|risk|safety)", re.I)),
    ("evidence", re.compile(r"(?:근거|기준|자료|데이터|evidence|criteria|data)", re.I)),
    ("report", re.compile(r"(?:보고|공유|전달|report|share)", re.I)),
    ("output", re.compile(r"(?:산출물|결과물|계획서|제안서|보고서|deliverable|output)", re.I)),
    ("stakeholder", re.compile(r"(?:이해관계자|부서|고객|협력사|stakeholder)", re.I)),
    ("quality", re.compile(r"(?:품질|오류|결함|정확|quality|error|defect)", re.I)),
    ("system", re.compile(r"(?:시스템|장애|서버|전산|system|outage|server)", re.I)),
)

_TOKEN_STOPWORDS = frozenset(
    {
        "그",
        "이",
        "저",
        "해당",
        "관련",
        "경우",
        "상황",
        "업무",
        "본인",
        "지원자",
        "무엇",
        "어떤",
        "어떻게",
        "설명",
        "제시",
        "주세요",
        "하겠습니까",
        "하시겠습니까",
        "습니까",
        "있습니까",
        "그리고",
        "위해",
        "대한",
        "대해",
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "in",
        "for",
        "how",
        "what",
        "would",
        "you",
    }
)


@dataclass(frozen=True)
class _PreparedCandidate:
    item: dict[str, Any]
    original_index: int
    question: str
    normalized_question: str
    stable_key: str
    quality_score: float
    quality_components: dict[str, float]
    axes: dict[str, Any]
    axis_sets: dict[str, frozenset[str]]
    slot_key: str
    slot_label: str


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def _first_text(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _clean_text(item.get(key))
        if value:
            return value
    return ""


def _question_text(item: Mapping[str, Any]) -> str:
    return _first_text(item, _QUESTION_KEYS)


def _list_text(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [text for text in (_clean_text(part) for part in value) if text]


def _stable_digest(item: Mapping[str, Any]) -> str:
    try:
        serialized = json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = repr(sorted((str(key), str(value)) for key, value in item.items()))
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()


def _normalize_question(value: Any) -> str:
    text = _clean_text(value).lower()
    # Drop a leading display label (for example ``[질문 3]``), but retain text
    # inside ordinary brackets/parentheses because it may carry a real task
    # constraint such as a budget or deadline.
    text = re.sub(r"^\s*\[[^\]]{1,40}\]\s*", " ", text)
    text = re.sub(r"[\[\](){}]", " ", text)
    text = re.sub(r"퍼센트|프로", "%", text)
    text = re.sub(r"(?:해\s*주세요|하여\s*주세요|주시기\s*바랍니다)", "주세요", text)
    text = re.sub(r"(?:하시겠습니까|하겠습니까|할\s*것입니까)", "", text)
    return re.sub(r"[^0-9a-z가-힣%]+", "", text)


def _token_stem(token: str) -> str:
    token = token.lower().strip()
    if len(token) <= 2:
        return token
    # A small, conservative suffix trim improves Korean surface-paraphrase
    # matching without pretending to be a morphological analyser.
    suffixes = (
        "에서는",
        "이라면",
        "으로는",
        "에게는",
        "부터는",
        "까지는",
        "에서도",
        "하고",
        "하며",
        "하여",
        "해서",
        "해야",
        "에서",
        "으로",
        "에게",
        "부터",
        "까지",
        "이라",
        "라면",
        "에는",
        "와의",
        "과의",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "에",
        "의",
        "로",
        "와",
        "과",
        "도",
        "만",
    )
    for suffix in suffixes:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _content_tokens(value: Any) -> frozenset[str]:
    text = _clean_text(value).lower()
    tokens: set[str] = set()
    for raw in re.findall(r"[0-9]+(?:\.[0-9]+)?|[a-z]+|[가-힣]+", text):
        token = _token_stem(raw)
        if len(token) >= 2 and token not in _TOKEN_STOPWORDS:
            tokens.add(token)
    for alias, pattern in _SEMANTIC_ALIASES:
        if pattern.search(text):
            tokens.add(f"@{alias}")
    return frozenset(tokens)


def _char_ngrams(value: str, size: int = 3) -> frozenset[str]:
    if not value:
        return frozenset()
    if len(value) <= size:
        return frozenset({value})
    return frozenset(value[index : index + size] for index in range(len(value) - size + 1))


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _duplicate_kind(left: str, right: str) -> str | None:
    left_normalized = _normalize_question(left)
    right_normalized = _normalize_question(right)
    if not left_normalized or not right_normalized:
        return None
    if left_normalized == right_normalized:
        return "exact"

    shorter = min(len(left_normalized), len(right_normalized))
    longer = max(len(left_normalized), len(right_normalized))
    if shorter < 12:
        return None

    containment = shorter / longer if (
        left_normalized in right_normalized or right_normalized in left_normalized
    ) else 0.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    ngram = _jaccard(_char_ngrams(left_normalized), _char_ngrams(right_normalized))
    tokens = _jaccard(_content_tokens(left), _content_tokens(right))
    left_aliases = {token for token in _content_tokens(left) if token.startswith("@")}
    right_aliases = {token for token in _content_tokens(right) if token.startswith("@")}
    alias_similarity = _jaccard(left_aliases, right_aliases) if left_aliases or right_aliases else 0.0

    if containment >= 0.90:
        return "near"
    if sequence >= 0.86 or ngram >= 0.70:
        return "near"
    if tokens >= 0.76 and sequence >= 0.62:
        return "near"
    shared_aliases = len(left_aliases & right_aliases)
    if alias_similarity >= 0.80 and tokens >= 0.48 and sequence >= 0.54:
        return "near"
    # Reordered Korean paraphrases often have a low character sequence score.
    # Requiring several shared normalized concepts keeps this branch narrower
    # than a plain bag-of-words comparison while still catching those cases.
    if shared_aliases >= 3 and alias_similarity >= 0.62 and tokens >= 0.44:
        return "near"
    if sequence >= 0.72 and ngram >= 0.43 and tokens >= 0.45:
        return "near"
    return None


def _joined_candidate_text(item: Mapping[str, Any], question: str) -> str:
    values: list[str] = [question]
    for key in ("scenario", "context", "task_conditions", "constraints", "assessment_guide"):
        value = item.get(key)
        if isinstance(value, (list, tuple)):
            values.extend(_clean_text(part) for part in value)
        elif isinstance(value, Mapping):
            values.extend(_clean_text(part) for part in value.values())
        else:
            values.append(_clean_text(value))
    return " ".join(value for value in values if value)


def _main_question_score(question: str) -> float:
    compact_length = len(re.sub(r"\s+", "", question))
    if 24 <= compact_length <= 280:
        length_score = 11.0
    elif 15 <= compact_length <= 420:
        length_score = 8.0
    elif compact_length >= 8:
        length_score = 4.0
    else:
        length_score = 1.0

    response_score = 8.0 if _RESPONSE_RE.search(question) else 1.0
    specificity_score = 6.0 if _NUMBER_OR_SPECIFIC_RE.search(question) else 2.0
    token_count = len(re.findall(r"[0-9a-z가-힣]+", question.lower()))
    structure_score = 3.0 if token_count >= 6 else (1.5 if token_count >= 3 else 0.0)
    completion_score = 2.0 if not re.search(r"[,;:/\-]\s*$", question) else 0.0
    return min(30.0, length_score + response_score + specificity_score + structure_score + completion_score)


def _structured_list_score(values: list[str], expected_count: int, maximum: float) -> float:
    count = len(values)
    unique_count = len({_normalize_question(value) for value in values if _normalize_question(value)})
    if count == expected_count:
        count_score = maximum * 0.70
    else:
        distance = abs(count - expected_count)
        count_score = maximum * max(0.0, 0.45 - (0.15 * distance))
    if not values:
        return 0.0
    uniqueness = unique_count / count if count else 0.0
    descriptive = sum(len(re.sub(r"\s+", "", value)) >= 6 for value in values) / count
    quality_score = maximum * 0.30 * ((uniqueness + descriptive) / 2.0)
    return min(maximum, count_score + quality_score)


def _metadata_score(item: Mapping[str, Any]) -> float:
    evidence_id = _first_text(item, _EVIDENCE_ID_KEYS)
    focus = _first_text(item, _FOCUS_KEYS)
    ksa_refs = _list_text(item.get("ksa_refs"))
    ksa_evidence = item.get("ksa_evidence")
    evidence_rows = ksa_evidence if isinstance(ksa_evidence, (list, tuple)) else []
    evidence_row_present = any(isinstance(row, Mapping) and row for row in evidence_rows)

    competency = _clean_text(item.get("competency") or item.get("competency_name"))
    ncs_code = _first_text(item, _NCS_CODE_KEYS)
    ncs_names = [_clean_text(item.get(key)) for key in _NCS_NAME_KEYS]
    nested_ncs = item.get("ncs_metadata")
    nested_ncs_present = isinstance(nested_ncs, Mapping) and any(
        _clean_text(value) for value in nested_ncs.values()
    )

    score = 0.0
    score += 4.0 if evidence_id or evidence_row_present else 0.0
    score += 4.0 if ksa_refs or focus else 0.0
    score += 4.0 if competency else 0.0
    score += 5.0 if ncs_code else 0.0
    score += 3.0 if any(ncs_names) or nested_ncs_present else 0.0
    return score


def _quality(item: Mapping[str, Any], question: str) -> tuple[float, dict[str, float]]:
    follow_ups = _list_text(item.get("follow_ups"))
    evaluation_points = _list_text(item.get("evaluation_points"))
    field_text = _joined_candidate_text(item, question)

    components = {
        "main_question": _main_question_score(question),
        "follow_ups": _structured_list_score(follow_ups, expected_count=3, maximum=15.0),
        "evaluation_points": _structured_list_score(
            evaluation_points, expected_count=4, maximum=15.0
        ),
        "grounding_metadata": _metadata_score(item),
        "field_context": 5.0 if _FIELD_SIGNAL_PATTERNS["context"].search(field_text) else 0.0,
        "constraint": 5.0 if _FIELD_SIGNAL_PATTERNS["constraint"].search(field_text) else 0.0,
        "judgment": 5.0 if _FIELD_SIGNAL_PATTERNS["judgment"].search(field_text) else 0.0,
        "deliverable": 5.0 if _FIELD_SIGNAL_PATTERNS["output"].search(field_text) else 0.0,
    }
    rounded_components = {key: round(value, 3) for key, value in components.items()}
    return round(sum(components.values()), 3), rounded_components


def _axis_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean_text(value).lower())


def _method_axis(item: Mapping[str, Any]) -> str:
    method = _first_text(item, _METHOD_KEYS)
    return method or "unspecified"


def _ksa_axis(item: Mapping[str, Any]) -> tuple[list[str], frozenset[str]]:
    labels: list[str] = []
    tokens: set[str] = set()

    for key in _EVIDENCE_ID_KEYS:
        value = _clean_text(item.get(key))
        if value:
            labels.append(value)
            tokens.add(f"id:{_axis_token(value)}")
            break
    for key in _FOCUS_KEYS:
        value = _clean_text(item.get(key))
        if value:
            labels.append(value)
            tokens.add(f"focus:{_axis_token(value)}")
            break
    for value in _list_text(item.get("ksa_refs")):
        labels.append(value)
        tokens.add(f"focus:{_axis_token(value)}")

    # K/S/A type is a first-class diversity axis.  Earlier selection only saw
    # factor/evidence IDs, so a large pool could fill the target with one KSA
    # type even when the model had generated all three.  Keep the type token
    # separate from factor focus so the selector can enforce balanced coverage.
    for key in ("ksa_type", "ksaTypeName", "factorType", "ksa_type_name"):
        value = _clean_text(item.get(key))
        if value:
            labels.append(value)
            tokens.add(f"type:{_axis_token(value)}")
            break

    trace = item.get("ncs_traceability")
    if isinstance(trace, Mapping):
        value = _clean_text(
            trace.get("ksa_type") or trace.get("ksaTypeName") or trace.get("factorType")
        )
        if value:
            labels.append(value)
            tokens.add(f"type:{_axis_token(value)}")

    evidence_rows = item.get("ksa_evidence")
    if isinstance(evidence_rows, (list, tuple)):
        for row in evidence_rows:
            if not isinstance(row, Mapping):
                continue
            for key in ("evidence_id", "factorName", "factor_name", "ksaTypeName", "ksa_type"):
                value = _clean_text(row.get(key))
                if not value:
                    continue
                prefix = "id" if key == "evidence_id" else "focus"
                labels.append(value)
                tokens.add(f"{prefix}:{_axis_token(value)}")

    unique_labels = list(dict.fromkeys(label for label in labels if label))
    return unique_labels, frozenset(token for token in tokens if not token.endswith(":"))


def _scenario_axis(item: Mapping[str, Any], question: str) -> tuple[str, frozenset[str]]:
    explicit = _clean_text(item.get("scenario_signature"))
    if explicit:
        token = _axis_token(explicit)
        return explicit, frozenset({token}) if token else frozenset()

    text = _joined_candidate_text(item, question)
    categories = [name for name, pattern in _SCENARIO_PATTERNS.items() if pattern.search(text)]
    if categories:
        return "|".join(categories), frozenset(categories)

    salient = sorted(token for token in _content_tokens(text) if not token.startswith("@"))[:3]
    if salient:
        return "|".join(salient), frozenset(salient)
    return "unspecified", frozenset()


def _difficulty_axis(item: Mapping[str, Any], question: str) -> tuple[str, list[str], frozenset[str]]:
    explicit = _first_text(item, ("difficulty", "difficulty_level", "level"))
    if not explicit:
        explicit = _clean_text(item.get("compeUnitLevel"))
    text = _joined_candidate_text(item, question)
    constraints = [name for name, pattern in _CONSTRAINT_PATTERNS.items() if pattern.search(text)]
    tokens = {f"constraint:{name}" for name in constraints}
    if explicit:
        tokens.add(f"level:{_axis_token(explicit)}")
    inferred = explicit or ("constrained" if constraints else "standard")
    return inferred, constraints, frozenset(tokens or {"level:standard"})


def _slot(item: Mapping[str, Any]) -> tuple[str, str]:
    label = _clean_text(item.get("_candidate_slot"))
    return (_axis_token(label), label) if label else ("", "")


def _prepare(item: dict[str, Any], original_index: int) -> _PreparedCandidate:
    question = _question_text(item)
    quality_score, quality_components = _quality(item, question)
    method = _method_axis(item)
    ksa_labels, ksa_tokens = _ksa_axis(item)
    scenario_label, scenario_tokens = _scenario_axis(item, question)
    difficulty_label, constraints, difficulty_tokens = _difficulty_axis(item, question)
    slot_key, slot_label = _slot(item)

    axes: dict[str, Any] = {
        "method": method,
        "ksa": ksa_labels,
        "scenario_signature": scenario_label,
        "difficulty": difficulty_label,
        "constraints": constraints,
        "slot": slot_label or None,
    }
    axis_sets = {
        "method": frozenset({_axis_token(method)}) if method != "unspecified" else frozenset(),
        "ksa": ksa_tokens,
        "scenario": scenario_tokens,
        "difficulty": difficulty_tokens,
    }
    return _PreparedCandidate(
        item=item,
        original_index=original_index,
        question=question,
        normalized_question=_normalize_question(question),
        stable_key=_stable_digest(item),
        quality_score=quality_score,
        quality_components=quality_components,
        axes=axes,
        axis_sets=axis_sets,
        slot_key=slot_key,
        slot_label=slot_label,
    )


_AXIS_WEIGHTS = {
    "method": 0.25,
    "ksa": 0.30,
    "scenario": 0.30,
    "difficulty": 0.15,
}


def _axis_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.25
    return _jaccard(left, right)


def _novelty_score(candidate: _PreparedCandidate, selected: Sequence[_PreparedCandidate]) -> float:
    if not selected:
        return 1.0
    max_similarity = 0.0
    for prior in selected:
        similarity = sum(
            weight * _axis_similarity(candidate.axis_sets[name], prior.axis_sets[name])
            for name, weight in _AXIS_WEIGHTS.items()
        )
        max_similarity = max(max_similarity, similarity)
    return max(0.0, 1.0 - max_similarity)


def _coverage_score(
    candidate: _PreparedCandidate,
    covered: Mapping[str, set[str]],
) -> float:
    score = 0.0
    for name, weight in _AXIS_WEIGHTS.items():
        values = candidate.axis_sets[name]
        if not values:
            continue
        unseen_fraction = len(values - covered[name]) / len(values)
        score += weight * unseen_fraction
    return score


def _coerce_target_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _avoid_texts(avoid_questions: Any) -> list[str]:
    if avoid_questions is None:
        return []
    if isinstance(avoid_questions, (str, bytes)):
        raw_values: Iterable[Any] = [avoid_questions]
    else:
        try:
            raw_values = list(avoid_questions)
        except TypeError:
            raw_values = [avoid_questions]
    values: list[str] = []
    for value in raw_values:
        text = _question_text(value) if isinstance(value, Mapping) else _clean_text(value)
        if text:
            values.append(text)
    return values


def select_question_candidates(
    candidates: Sequence[dict[str, Any]] | None,
    target_count: int,
    avoid_questions: Sequence[str | Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the strongest non-duplicate candidates with diverse coverage.

    The function never mutates a candidate.  Selection is deterministic for the
    same values, including ties: quality ties are resolved by a stable digest of
    candidate content rather than by random choice.

    ``metadata["selected"]`` contains one JSON-serializable audit row per result.
    If candidates carry ``_candidate_slot``, at most one result is returned per
    non-empty slot and all eligible slots are covered when the target permits it.
    """

    raw_candidates = list(candidates or [])
    target = _coerce_target_count(target_count)
    avoid = _avoid_texts(avoid_questions)

    prepared: list[_PreparedCandidate] = []
    empty_count = 0
    invalid_count = 0
    for index, value in enumerate(raw_candidates):
        if not isinstance(value, dict):
            invalid_count += 1
            empty_count += 1
            continue
        entry = _prepare(value, index)
        if not entry.normalized_question:
            empty_count += 1
            continue
        prepared.append(entry)

    # Process the highest-quality representation first so duplicate groups do
    # not accidentally retain a sparse or malformed version of the question.
    prepared.sort(key=lambda entry: (-entry.quality_score, entry.stable_key))

    eligible: list[_PreparedCandidate] = []
    duplicate_count = 0
    exact_duplicate_count = 0
    near_duplicate_count = 0
    avoid_duplicate_count = 0
    for entry in prepared:
        duplicate_kind: str | None = None
        matched_avoid = False
        for avoid_text in avoid:
            duplicate_kind = _duplicate_kind(entry.question, avoid_text)
            if duplicate_kind:
                matched_avoid = True
                break
        if not duplicate_kind:
            for prior in eligible:
                duplicate_kind = _duplicate_kind(entry.question, prior.question)
                if duplicate_kind:
                    break
        if duplicate_kind:
            duplicate_count += 1
            exact_duplicate_count += int(duplicate_kind == "exact")
            near_duplicate_count += int(duplicate_kind == "near")
            avoid_duplicate_count += int(matched_avoid)
            continue
        eligible.append(entry)

    available_slot_labels: dict[str, str] = {}
    for entry in eligible:
        if entry.slot_key:
            available_slot_labels.setdefault(entry.slot_key, entry.slot_label)

    selected_entries: list[_PreparedCandidate] = []
    selection_details: list[dict[str, Any]] = []
    covered: dict[str, set[str]] = {name: set() for name in _AXIS_WEIGHTS}
    used_slots: set[str] = set()
    remaining = list(eligible)
    available_ksa_types = {
        token
        for entry in eligible
        for token in entry.axis_sets.get("ksa", frozenset())
        if token.startswith("type:")
    }
    ksa_type_quota = (
        target // len(available_ksa_types)
        if available_ksa_types and target >= len(available_ksa_types)
        else 0
    )

    while remaining and len(selected_entries) < target:
        pool = [entry for entry in remaining if not entry.slot_key or entry.slot_key not in used_slots]
        if not pool:
            break

        remaining_positions = target - len(selected_entries)
        uncovered_slots = set(available_slot_labels) - used_slots
        if uncovered_slots and remaining_positions <= len(uncovered_slots):
            uncovered_pool = [entry for entry in pool if entry.slot_key in uncovered_slots]
            if uncovered_pool:
                pool = uncovered_pool

        scored: list[tuple[float, float, float, _PreparedCandidate]] = []
        for entry in pool:
            novelty = _novelty_score(entry, selected_entries)
            coverage = _coverage_score(entry, covered)
            selection_score = (0.75 * entry.quality_score) + (17.0 * novelty) + (8.0 * coverage)
            if ksa_type_quota:
                entry_types = {
                    token
                    for token in entry.axis_sets.get("ksa", frozenset())
                    if token.startswith("type:")
                }
                underrepresented = any(
                    sum(
                        1
                        for chosen_entry in selected_entries
                        if token in chosen_entry.axis_sets.get("ksa", frozenset())
                    )
                    < ksa_type_quota
                    for token in entry_types
                )
                if underrepresented:
                    # Strongly prefer an unseen/under-quota KSA type while
                    # retaining quality, method, scenario, and slot signals.
                    selection_score += 10.0
            scored.append((selection_score, novelty, coverage, entry))

        selection_score, novelty, coverage_score, chosen = min(
            scored,
            key=lambda row: (-round(row[0], 12), -row[3].quality_score, row[3].stable_key),
        )
        selected_entries.append(chosen)
        remaining.remove(chosen)
        if chosen.slot_key:
            used_slots.add(chosen.slot_key)
        for name in _AXIS_WEIGHTS:
            covered[name].update(chosen.axis_sets[name])

        selection_details.append(
            {
                "rank": len(selected_entries),
                "candidate_index": chosen.original_index,
                "question": chosen.question,
                "quality_score": chosen.quality_score,
                "selection_score": round(selection_score, 3),
                "novelty_score": round(novelty * 100.0, 3),
                "coverage_score": round(coverage_score * 100.0, 3),
                "quality_components": dict(chosen.quality_components),
                "axes": dict(chosen.axes),
            }
        )

    covered_slots = [
        available_slot_labels[key]
        for key in sorted(used_slots, key=lambda value: available_slot_labels[value].casefold())
    ]
    available_slots = [
        available_slot_labels[key]
        for key in sorted(available_slot_labels, key=lambda value: available_slot_labels[value].casefold())
    ]
    available_slot_count = len(available_slots)
    covered_slot_count = len(covered_slots)

    metadata: dict[str, Any] = {
        "candidate_count": len(raw_candidates),
        "eligible_count": len(eligible),
        "selected_count": len(selected_entries),
        "duplicate_count": duplicate_count,
        "empty_count": empty_count,
        "invalid_count": invalid_count,
        "exact_duplicate_count": exact_duplicate_count,
        "near_duplicate_count": near_duplicate_count,
        "avoid_duplicate_count": avoid_duplicate_count,
        "target_count": target,
        "strategy": "quality_weighted_greedy_coverage_v1",
        "selected": selection_details,
        "slot_coverage": {
            "enabled": bool(available_slots),
            "available_slots": available_slots,
            "covered_slots": covered_slots,
            "available_count": available_slot_count,
            "covered_count": covered_slot_count,
            "coverage_ratio": round(
                covered_slot_count / available_slot_count, 3
            ) if available_slot_count else 1.0,
            "complete": covered_slot_count == available_slot_count,
        },
    }

    return [entry.item for entry in selected_entries], metadata


__all__ = ["select_question_candidates"]
