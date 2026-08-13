from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any


PRECISION_GROUNDING_POLICY = "precision-grounding-v2"

_MATERIAL_ID_RE = re.compile(r"^(?:mat|scn)_[a-z0-9][a-z0-9_-]{7,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KINDS = {
    "uploaded_document",
    "review_confirmed_document",
    "deterministic_case",
    "generated_scenario",
}
_VALUE_KINDS = {
    "amount",
    "baseline_value",
    "clause_excerpt",
    "formula",
    "numeric_value",
    "numerator_field",
    "denominator_field",
    "quantity",
    "rate",
    "ratio",
    "raw_values",
    "unit_price",
}

_CLAUSE_QUOTE_RE = re.compile(
    r"(?:"
    r"(?:관련\s*(?:규정|계약|조항)(?:이|은|가)?\s*)?몇\s*조(?:\s*몇\s*항)?"
    r"|조항\s*번호"
    r"|원문(?:을|을\s*)?(?:그대로|정확히|인용|말)"
    r"|정확한\s*문구(?:를|를\s*)?(?:인용|말|재현)"
    r"|제\s*\d+\s*조(?:\s*제\s*\d+\s*항)?[^.!?\n]{0,30}(?:원문|인용|그대로)"
    r")",
    re.IGNORECASE,
)
_FORMULA_RECALL_RE = re.compile(
    r"(?:"
    r"분자[^.!?\n]{0,35}분모|분모[^.!?\n]{0,35}분자"
    r"|기준\s*연도(?:의)?\s*(?:값|수치)"
    r"|산식(?:을|까지|의)?\s*(?:재현|대입|설명|말|밝)"
    r"|계산\s*과정(?:을|까지)?\s*(?:설명|말|밝|제시)"
    r"|자료(?:의|에서)\s*어떤\s*(?:값|수치)[^.!?\n]{0,35}(?:계산|산식)"
    r"|어느\s*자료(?:의|에서)\s*어떤\s*수치[^.!?\n]{0,35}산식"
    r")",
    re.IGNORECASE,
)
_UNIT_COMPONENT_RE = re.compile(
    r"(?:단가[^.!?\n]{0,25}수량|수량[^.!?\n]{0,25}단가)"
    r"[^.!?\n]{0,35}(?:계산|산출|정확히\s*말)",
    re.IGNORECASE,
)
_RATIO_RECALL_RE = re.compile(
    r"(?:"
    r"(?:정확한|자료상)\s*(?:비율|달성률|수료율|증가율)"
    r"|(?:비율|달성률|수료율|증가율)[^.!?\n]{0,30}(?:계산|산출|정확히\s*말)"
    r")",
    re.IGNORECASE,
)
_AMOUNT_DIFFERENCE_RE = re.compile(
    r"(?:"
    r"당초\s*(?:요구)?액[^.!?\n]{0,45}(?:조정액|실제\s*집행액)"
    r"|(?:조정액|실제\s*집행액)[^.!?\n]{0,45}당초\s*(?:요구)?액"
    r")[^.!?\n]{0,40}(?:대조|차액|계산|정확)",
    re.IGNORECASE,
)
_EXACT_AMOUNT_RE = re.compile(
    r"(?:"
    r"(?:금액|액수|요구액|집행액|예산액|비용)[^.!?\n]{0,25}"
    r"얼마(?:였|인지|입니까|인가|였습니까)"
    r"|몇\s*원"
    r"|정확한\s*(?:금액|액수|집행액|요구액)"
    r"|(?:당초\s*요구액|기존\s*집행액|공문상\s*금액)"
    r"[^.!?\n]{0,35}(?:찾|말|확인|대조|계산|얼마)"
    r")",
    re.IGNORECASE,
)
_DOCUMENT_VALUE_RE = re.compile(
    r"(?:"
    r"(?<![0-9A-Za-z가-힣])(?:예산표|실적표|제공표|정정표|분석표|대조표|"
    r"집계표|계획표|검증표|처리표|등록표|보완표|실험표|배분표|표|자료|공문)"
    r"(?:에서|의|상)"
    r"[^.!?\n]{0,55}(?:"
    r"찾(?:아|으|겠)|가져(?:오|와)|정확히\s*말"
    r"|계산(?:하|해)|산출(?:하|해)"
    r")"
    r"|계약서에\s*적힌[^.!?\n]{0,35}(?:값|금액|비율|수치)"
    r"|어느\s*수치(?:에서)?\s*(?:가져|찾)"
    r")",
    re.IGNORECASE,
)
_DOCUMENT_ERROR_FINDING_RE = re.compile(
    r"(?:자료|표|공문)[^.!?\n]{0,35}(?:오류|불일치|누락)"
    r"[^.!?\n]{0,20}(?:찾|확인|발견)",
    re.IGNORECASE,
)
_SOURCE_NOUN = (
    r"(?:원자료|계약서|공문|보고서|원장|대장|명세서|재무제표|"
    r"(?:예산|실적|재무|성과|집계|통계|매출|비용|현황|제공)?표|자료)"
)
_SOURCE_REFERENCE_RE = re.compile(
    rf"(?:"
    rf"(?:첨부(?:된)?|제공(?:된)?|주어진|제시된)\s*{_SOURCE_NOUN}"
    rf"|{_SOURCE_NOUN}\s*(?:를|을)?\s*"
    rf"(?:바탕으로|토대로|참고(?:하여|해서|해|하고)|근거로|기준으로|대조(?:하여|해서|해))"
    rf"|{_SOURCE_NOUN}\s*(?:상|에서|의|에\s*(?:적힌|기재된))"
    rf")",
    re.IGNORECASE,
)
_SOURCE_DERIVED_METRIC_RE = re.compile(
    r"(?:"
    r"ROI|투자\s*수익률|손익\s*분기점|"
    r"전년\s*대비\s*(?:증감|증가|감소)률|"
    r"(?:증감|증가|감소|달성|수료|이익|수익)률|"
    r"평균\s*(?:단가|금액|비용|수치)?|합계"
    r")",
    re.IGNORECASE,
)
_SOURCE_AMOUNT_TARGET_RE = re.compile(
    r"(?:"
    r"(?:위약금|계약금|보증금|지체상금)\s*(?:의\s*)?(?:금액|액수)"
    r"|(?:손해배상|예산|집행|요구|조정)\s*(?:금액|액수)"
    r")",
    re.IGNORECASE,
)
_SOURCE_QUOTE_TARGET_RE = re.compile(
    r"(?:"
    r"(?:조항|문구|원문)[^.!?\n]{0,24}(?:그대로\s*(?:옮겨|적|말)|인용|전재|재현)"
    r"|(?:그대로\s*(?:옮겨|적)|인용|전재|재현)[^.!?\n]{0,24}"
    r"(?:조항|문구|원문)"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_DERIVATION_RE = re.compile(
    r"(?:정확(?:한|히)|계산|산출|도출|구하|산정|몇\s*%|얼마)", re.IGNORECASE
)
_GENERAL_ANALYSIS_RE = re.compile(
    r"(?:원인|이유|의미|영향|한계|추세|적정성|타당성|개선|대안|전략)"
    r"[^.!?\n]{0,24}(?:분석|해석|평가|논의|설명|제안|검토|판단)"
    r"|(?:분석|해석|평가|논의|설명|제안|검토|판단)"
    r"[^.!?\n]{0,24}(?:원인|이유|의미|영향|한계|추세|적정성|타당성|개선|대안|전략)",
    re.IGNORECASE,
)
_RETRIEVAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"(?:자료|표|공문|계약서)(?:에서|의|상|에\s*적힌)"
    r"[^.!?\n]{0,60}(?:찾|가져|정확|계산|산식|대조|얼마|수치|비율|금액)"
    r"|(?:찾|가져)[^.!?\n]{0,30}(?:자료|표|공문|계약서)"
    r")",
    re.IGNORECASE,
)
_CANDIDATE_AUTHORED_RE = re.compile(
    r"(?:"
    r"(?:목표값|목표치|산식|비율|금액|조정액|배분안|조정안)"
    r"[^.!?\n]{0,30}(?:설계|제안|설정|정하|선택|배분|조정)"
    r"|(?:설계|제안|설정|정하|선택|배분|조정)"
    r"[^.!?\n]{0,30}(?:목표값|목표치|산식|비율|금액|조정액|배분안|조정안)"
    r"|얼마(?:만큼|로)\s*(?:다시\s*)?조정"
    r"|방금[^.!?\n]{0,30}(?:제시|제안|선택|설계|정한)"
    r")",
    re.IGNORECASE,
)
_CANDIDATE_OWNED_RE = re.compile(
    r"(?:실제|당시|과거|경험|본인|직접\s*작성|직접\s*수행)", re.IGNORECASE
)
_MEMORY_BOUND_RE = re.compile(
    r"(?:가능한\s*범위|기억나는\s*(?:범위|대로)|공개할\s*수\s*있는\s*범위)",
    re.IGNORECASE,
)
_MATERIAL_CLAIM_RE = re.compile(
    r"(?:자료|표|기록|계약서)[^.!?\n]{0,25}"
    r"(?:제공해\s*드리|제공하겠|드리겠|주어지|검토할\s*수\s*있도록\s*드리)",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"\d[\d,.]*\s*(?:조|억|만|천)?\s*원")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_CLAUSE_ID_RE = re.compile(r"제?\s*(\d+)\s*조(?:\s*제?\s*(\d+)\s*항)?")
_DEPARTMENT_RE = re.compile(r"([A-Za-z가-힣0-9]+부서)")

# Allocation prompts are different from ordinary open estimates.  Asking for a
# priority, a qualitative support level, or an allocation *criterion* leaves a
# defensible answer open.  Asking the candidate to fill an amount/quantity or
# a numeric share does not: the available total and its unit must be visible.
# Keep this detector deliberately tied to allocation nouns so that generic
# requests to "수치화" an outcome do not become precision false positives.
_QUANTIFIED_ALLOCATION_RE = re.compile(
    r"(?:"
    r"(?:(?:사업|과제|부서|프로그램|항목)(?:별|마다)|"
    r"각\s*(?:사업|과제|부서|프로그램|항목)(?:에|에는|마다)?)\s*"
    r"(?:예산|재원|인력|인원|자원|지원)?\s*"
    r"(?:배분|배정|지원|조정)\s*(?:량|액|금액|수치|비율)"
    r"|(?:(?:사업|과제|부서|프로그램|항목)(?:별|마다)|"
    r"각\s*(?:사업|과제|부서|프로그램|항목)(?:에|에는|마다)?)[^.!?\n]{0,24}"
    r"(?:몇\s*(?:명|원)|얼마(?:씩|를)?|\d+(?:\.\d+)?\s*%씩?)"
    r"|(?:배분|배정|지원|조정)\s*(?:량|액|금액|수치|비율)"
    r"[^.!?\n]{0,28}(?:수치화|정량화|명시|기록|표시|제시|산출|정하)"
    r"|(?:배분|배정|지원|조정)[^.!?\n]{0,24}"
    r"(?:몇\s*(?:명|원)|얼마(?:씩|를)?|정확한\s*(?:금액|수량|비율)|수치화|정량화)"
    r")",
    re.IGNORECASE,
)
_ALLOCATION_BUDGET_CONTEXT_RE = re.compile(
    r"(?:예산|재원|지원액|배분액|배정액|조정액|금액)", re.IGNORECASE
)
_ALLOCATION_STAFF_CONTEXT_RE = re.compile(
    r"(?:인력|인원|인원수|정원|배정\s*인원)", re.IGNORECASE
)
_ALLOCATION_RATIO_CONTEXT_RE = re.compile(
    r"(?:배분|배정|지원|조정)\s*(?:비율|률)|(?:수치|정량)[^.!?\n]{0,12}%|"
    r"몇\s*%|퍼센트",
    re.IGNORECASE,
)
_BUDGET_TOTAL_RE = re.compile(
    r"(?:"
    r"(?:총\s*(?:예산|재원|금액)|가용\s*(?:예산|재원|금액)|"
    r"(?:배정|배분|사용|지원)할\s*수\s*있는\s*(?:금액|예산|재원)|"
    r"(?:예산|재원|금액)\s*(?:총액|한도|상한)|(?:총액|한도|가용액))"
    r"[^.!?\n]{0,20}\d[\d,.]*\s*(?:조|억|만|천)?\s*원"
    r"|\d[\d,.]*\s*(?:조|억|만|천)?\s*원[^.!?\n]{0,20}"
    r"(?:총\s*(?:예산|재원|금액)|가용\s*(?:예산|재원|금액)|"
    r"(?:배정|배분|사용|지원)할\s*수\s*있는\s*(?:금액|예산|재원)|"
    r"(?:예산|재원|금액)\s*(?:총액|한도|상한)|(?:총액|한도|가용액))"
    r")",
    re.IGNORECASE,
)
_STAFF_TOTAL_RE = re.compile(
    r"(?:"
    r"(?:총\s*(?:인력|인원|정원)|가용\s*(?:인력|인원|정원)|"
    r"(?:인력|인원|정원)\s*(?:총원|한도|상한))"
    r"[^.!?\n]{0,20}\d[\d,.]*\s*명"
    r"|\d[\d,.]*\s*명[^.!?\n]{0,20}"
    r"(?:총\s*(?:인력|인원|정원)|가용\s*(?:인력|인원|정원)|"
    r"(?:인력|인원|정원)\s*(?:총원|한도|상한))"
    r")",
    re.IGNORECASE,
)
_GENERIC_ALLOCATION_TOTAL_RE = re.compile(
    r"(?:"
    r"(?:총량|총\s*자원|가용\s*자원|자원\s*(?:한도|상한))"
    r"[^.!?\n]{0,20}\d[\d,.]*\s*(?:단위|점|개|시간|명|원)"
    r"|\d[\d,.]*\s*(?:단위|점|개|시간|명|원)[^.!?\n]{0,20}"
    r"(?:총량|총\s*자원|가용\s*자원|자원\s*(?:한도|상한))"
    r")",
    re.IGNORECASE,
)
_MATERIAL_BUDGET_TOTAL_RE = re.compile(
    r"(?:총\s*(?:예산|재원|금액)|가용\s*(?:예산|재원|금액)|"
    r"(?:배정|배분|사용|지원)할\s*수\s*있는\s*(?:금액|예산|재원)|"
    r"(?:예산|재원|금액)\s*(?:총액|한도|상한)|(?:총액|한도|가용액))",
    re.IGNORECASE,
)
_MATERIAL_STAFF_TOTAL_RE = re.compile(
    r"(?:총\s*(?:인력|인원|정원)|가용\s*(?:인력|인원|정원)|"
    r"(?:인력|인원|정원)\s*(?:총원|한도|상한))",
    re.IGNORECASE,
)
_MATERIAL_GENERIC_TOTAL_RE = re.compile(
    r"(?:총량|총\s*자원|가용\s*자원|자원\s*(?:한도|상한))", re.IGNORECASE
)


def _sha256(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _method(item: Mapping[str, Any]) -> str:
    value = _text(item.get("type") or item.get("method")).lower()
    aliases = {
        "behavioral": "경험면접",
        "behavioral_interview": "경험면접",
        "experience": "경험면접",
        "situational": "상황면접",
        "presentation": "발표면접",
    }
    return aliases.get(value, value)


def _follow_up_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("text") or value.get("question"))
    return _text(value)


def _candidate_texts(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    question = _text(item.get("question"))
    if question:
        rows.append(("question", question))
    follow_ups = item.get("follow_ups")
    if isinstance(follow_ups, Sequence) and not isinstance(
        follow_ups, (str, bytes, bytearray)
    ):
        for index, value in enumerate(follow_ups):
            follow_up = _follow_up_text(value)
            if follow_up:
                rows.append((f"follow_ups[{index}]", follow_up))
    for field in ("candidate_instruction", "task_instruction"):
        instruction = _text(item.get(field))
        if instruction:
            rows.append((field, instruction))
    evaluation_points = item.get("evaluation_points")
    if isinstance(evaluation_points, Sequence) and not isinstance(
        evaluation_points, (str, bytes, bytearray)
    ):
        for index, value in enumerate(evaluation_points):
            point = _follow_up_text(value)
            if point:
                rows.append((f"evaluation_points[{index}]", point))
    return rows


def _clauses(text: str) -> list[str]:
    clauses = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+|\s*[;；]\s*", text)
        if part.strip()
    ]
    return clauses or ([text] if text else [])


def _required_formula_kinds(text: str) -> list[str]:
    required: list[str] = []
    if "산식" in text or "계산 과정" in text:
        required.append("formula")
    if "분자" in text:
        required.append("numerator_field")
    if "분모" in text:
        required.append("denominator_field")
    if re.search(r"기준\s*연도(?:의)?\s*(?:값|수치)", text):
        required.append("baseline_value")
    if not required:
        required.append("numeric_value")
    return required


def _detect_source_bound_precision_demand(
    text: str,
) -> tuple[str, str, list[str]] | None:
    """Detect fixed values implicitly delegated to an external document.

    Generated questions often avoid the older ``자료에서 계산하세요`` shape by
    using connective paraphrases such as ``표를 바탕으로`` or by ending on a
    value noun.  A source reference alone is not enough: the clause must also
    request a concrete amount or derived metric.  Pure interpretation and
    improvement prompts stay outside this precision gate.
    """

    if not _SOURCE_REFERENCE_RE.search(text):
        return None
    if _SOURCE_QUOTE_TARGET_RE.search(text):
        return "clause_quote", "quote", ["clause_excerpt"]
    if _SOURCE_AMOUNT_TARGET_RE.search(text):
        return "amount_retrieval", "retrieve", ["amount"]
    metric_match = _SOURCE_DERIVED_METRIC_RE.search(text)
    if not metric_match:
        return None
    trailing = text[metric_match.end() :]
    target_only_ending = bool(
        re.fullmatch(r"\s*(?:은|는|이|가|을|를)?\s*[?.!]?\s*", trailing)
    )
    if _EXPLICIT_DERIVATION_RE.search(text) or target_only_ending:
        return "source_metric_calculation", "calculate", ["numeric_value"]
    return None


def _allocation_required_kinds(text: str, context: str) -> list[str]:
    """Return the candidate-visible totals needed by a numeric allocation."""

    combined = f"{context} {text}"
    required: list[str] = []
    if _ALLOCATION_BUDGET_CONTEXT_RE.search(combined):
        required.append("allocation_amount_total")
    if _ALLOCATION_STAFF_CONTEXT_RE.search(combined):
        required.append("allocation_quantity_total")
    # A ratio is only an additional operand when the demanded field itself is
    # a numeric share.  Merely listing "공통 비율 조정" as one qualitative
    # option must not turn the whole question into a ratio calculation.
    if _ALLOCATION_RATIO_CONTEXT_RE.search(text):
        required.append("allocation_ratio_basis")
    if not required:
        required.append("allocation_total")
    return list(dict.fromkeys(required))


def _detect_precision_demand(
    text: str, *, context: str = ""
) -> tuple[str, str, list[str]] | None:
    if _CLAUSE_QUOTE_RE.search(text):
        return "clause_quote", "quote", ["clause_excerpt"]
    if _FORMULA_RECALL_RE.search(text):
        return "formula_retrieval", "calculate", _required_formula_kinds(text)
    if _UNIT_COMPONENT_RE.search(text):
        return "unit_component_calculation", "calculate", ["unit_price", "quantity"]
    if _RATIO_RECALL_RE.search(text):
        return "ratio_calculation", "calculate", ["ratio"]
    if _AMOUNT_DIFFERENCE_RE.search(text):
        return "amount_difference", "calculate", [
            "original_amount",
            "adjusted_amount",
        ]
    if _QUANTIFIED_ALLOCATION_RE.search(text):
        return (
            "quantified_allocation",
            "allocate",
            _allocation_required_kinds(text, context),
        )
    if _EXACT_AMOUNT_RE.search(text):
        return "amount_retrieval", "retrieve", ["amount"]
    if _DOCUMENT_VALUE_RE.search(text) and not _DOCUMENT_ERROR_FINDING_RE.search(text):
        return "document_value_retrieval", "retrieve", ["numeric_value"]
    source_bound = _detect_source_bound_precision_demand(text)
    if source_bound is not None:
        return source_bound
    return None


def _registry_items(material_registry: Any) -> list[Mapping[str, Any]]:
    raw = material_registry
    if isinstance(raw, Mapping) and isinstance(raw.get("material_registry"), Mapping):
        raw = raw["material_registry"]
    if isinstance(raw, Mapping):
        raw = raw.get("items", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def _material_scope_ids(material: Mapping[str, Any]) -> set[str]:
    values: list[Any] = []
    for field in ("question_id", "allowed_question_id"):
        if material.get(field) is not None:
            values.append(material.get(field))
    for field in ("question_ids", "allowed_question_ids"):
        raw = material.get(field)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            values.extend(raw)
    return {_text(value) for value in values if _text(value)}


def _valid_locator(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return any(
        value.get(key) not in (None, "", [])
        for key in ("page", "block", "span", "row", "start", "end")
    )


def _material_validation_codes(
    material: Mapping[str, Any], *, question_id: str
) -> list[str]:
    material_id = _text(material.get("material_id"))
    codes: list[str] = []
    if not _MATERIAL_ID_RE.fullmatch(material_id):
        codes.append("invalid_material_id")
    source_kind = _text(material.get("source_kind"))
    if source_kind not in _SOURCE_KINDS:
        codes.append("invalid_material_provenance")
    if material.get("candidate_visible") is not True:
        codes.append("material_not_candidate_visible")
    if material.get("origin_verified") is not True:
        codes.append("material_origin_unverified")
    value_kind = _text(material.get("value_kind"))
    if value_kind not in _VALUE_KINDS:
        codes.append("invalid_material_value_kind")
    if material.get("value") in (None, "", [], {}):
        codes.append("material_value_missing")
    if source_kind in {"uploaded_document", "review_confirmed_document"}:
        if not _SHA256_RE.fullmatch(_text(material.get("document_sha256")).lower()):
            codes.append("invalid_material_provenance")
        if not _valid_locator(material.get("locator")):
            codes.append("invalid_material_provenance")
    elif source_kind in {"deterministic_case", "generated_scenario"} and not _valid_locator(
        material.get("locator")
    ):
        codes.append("invalid_material_provenance")
    scope_ids = _material_scope_ids(material)
    if scope_ids and (not question_id or question_id not in scope_ids):
        codes.append("cross_question_material_ref")
    return list(dict.fromkeys(codes))


def _registry_index(
    material_registry: Any, *, question_id: str
) -> tuple[dict[str, Mapping[str, Any]], dict[str, list[str]]]:
    index: dict[str, Mapping[str, Any]] = {}
    validations: dict[str, list[str]] = {}
    duplicates: set[str] = set()
    for material in _registry_items(material_registry):
        material_id = _text(material.get("material_id"))
        if not material_id:
            continue
        if material_id in index:
            duplicates.add(material_id)
            continue
        index[material_id] = material
        validations[material_id] = _material_validation_codes(
            material, question_id=question_id
        )
    for material_id in duplicates:
        validations.setdefault(material_id, []).append("duplicate_material_id")
    return index, validations


def _case_material_refs(item: Mapping[str, Any]) -> tuple[list[str], bool]:
    raw = item.get("case_material_refs", [])
    if raw in (None, ""):
        return [], True
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return [], False
    refs = [_text(value) for value in raw]
    return [value for value in refs if value], all(bool(value) for value in refs)


def _material_text(material: Mapping[str, Any]) -> str:
    fields: list[str] = []
    for key in ("field", "source_label", "value"):
        value = material.get(key)
        if value not in (None, ""):
            fields.append(_text(value))
    raw_fields = material.get("fields")
    if isinstance(raw_fields, Mapping):
        fields.extend(_text(value) for value in raw_fields.keys())
    elif isinstance(raw_fields, Sequence) and not isinstance(
        raw_fields, (str, bytes, bytearray)
    ):
        fields.extend(_text(value) for value in raw_fields)
    components = material.get("components")
    if isinstance(components, Mapping):
        fields.extend(_text(value) for value in components.keys())
    elif isinstance(components, Sequence) and not isinstance(
        components, (str, bytes, bytearray)
    ):
        fields.extend(_text(value) for value in components)
    return " ".join(value for value in fields if value)


def _kind_aliases(material: Mapping[str, Any]) -> set[str]:
    value_kind = _text(material.get("value_kind"))
    aliases = {value_kind}
    if value_kind == "rate":
        aliases.add("ratio")
    if value_kind in {"amount", "numeric_value", "ratio", "rate"}:
        aliases.add("numeric_value")
    material_text = _material_text(material)
    marker_aliases = {
        "numerator_field": ("분자", "수료자 수", "완료 건수"),
        "denominator_field": ("분모", "신청자 수", "전체 건수"),
        "baseline_value": ("기준연도", "기준값"),
        "unit_price": ("단가",),
        "quantity": ("수량",),
        "original_amount": ("당초", "요구액"),
        "adjusted_amount": ("조정액", "조정 금액"),
    }
    for alias, markers in marker_aliases.items():
        if any(marker in material_text for marker in markers):
            aliases.add(alias)
    return aliases


def _requested_entities(text: str) -> tuple[set[str], set[tuple[str, str]]]:
    departments = {match.group(1) for match in _DEPARTMENT_RE.finditer(text)}
    clauses = {
        (match.group(1), match.group(2) or "") for match in _CLAUSE_ID_RE.finditer(text)
    }
    return departments, clauses


def _material_semantically_matches(text: str, material: Mapping[str, Any]) -> bool:
    requested_departments, requested_clauses = _requested_entities(text)
    material_text = _material_text(material)
    material_departments, material_clauses = _requested_entities(material_text)
    if requested_departments and not (requested_departments & material_departments):
        return False
    if requested_clauses and not (requested_clauses & material_clauses):
        return False
    return True


def _material_provides(material: Mapping[str, Any], required_kind: str) -> bool:
    aliases = _kind_aliases(material)
    material_text = _material_text(material)
    if required_kind == "allocation_amount_total":
        return bool(
            {"amount", "numeric_value", "raw_values"} & aliases
            and _MATERIAL_BUDGET_TOTAL_RE.search(material_text)
        )
    if required_kind == "allocation_quantity_total":
        return bool(
            {"quantity", "numeric_value", "raw_values"} & aliases
            and _MATERIAL_STAFF_TOTAL_RE.search(material_text)
        )
    if required_kind == "allocation_ratio_basis":
        return bool({"ratio", "rate", "raw_values"} & aliases)
    if required_kind == "allocation_total":
        return bool(
            {"amount", "quantity", "numeric_value", "raw_values"} & aliases
            and (
                _MATERIAL_GENERIC_TOTAL_RE.search(material_text)
                or _MATERIAL_BUDGET_TOTAL_RE.search(material_text)
                or _MATERIAL_STAFF_TOTAL_RE.search(material_text)
            )
        )
    if required_kind == "amount":
        return "amount" in aliases
    if required_kind == "ratio":
        return bool({"ratio", "rate"} & aliases)
    if required_kind == "formula":
        return "formula" in aliases
    if required_kind == "clause_excerpt":
        return "clause_excerpt" in aliases
    if required_kind == "numeric_value":
        return "numeric_value" in aliases or "raw_values" in aliases
    return required_kind in aliases


def _experience_owned(
    item: Mapping[str, Any],
    clause: str,
    *,
    precision_detected: bool,
    demand_kind: str = "",
) -> bool:
    context = f"{_text(item.get('question'))} {clause}"
    if _method(item) != "경험면접" and "경험" not in context:
        return False
    if not _CANDIDATE_OWNED_RE.search(context):
        return False
    if demand_kind == "quantified_allocation":
        return True
    return not precision_detected or bool(_MEMORY_BOUND_RE.search(context))


def _candidate_authored(clause: str, demand_kind: str) -> bool:
    # Inventing exact shares does not supply the missing total.  Numeric
    # allocations are admitted only by a complete visible scenario, trusted
    # registry evidence, or a candidate-owned past experience.
    if demand_kind == "quantified_allocation":
        return False
    if not _CANDIDATE_AUTHORED_RE.search(clause) or _RETRIEVAL_CONTEXT_RE.search(
        clause
    ):
        return False
    # A request may append "propose an adjustment" after asking the applicant
    # to recover two unavailable source amounts.  The proposal does not ground
    # those fixed inputs.  A visible amount or an explicit reference to the
    # applicant's just-authored answer is required for that mixed wording.
    if demand_kind == "amount_difference" and not (
        _CURRENCY_RE.search(clause)
        or re.search(r"(?:방금|앞서|본인이)[^.!?\n]{0,35}(?:제안|제시|조정)", clause)
    ):
        return False
    if re.search(r"(?:당초\s*요구액|기존\s*집행액|기준\s*연도)", clause) and not (
        _CURRENCY_RE.search(clause)
        or re.search(r"(?:방금|앞서|본인이)[^.!?\n]{0,35}(?:제안|제시|선택)", clause)
    ):
        return False
    return True


def _paired_ratio_facts(text: str) -> bool:
    numerator = re.search(
        r"(?:수료|완료|성공|달성)[^.!?\n]{0,12}\d[\d,.]*\s*(?:명|건|개)", text
    )
    denominator = re.search(
        r"(?:신청|전체|접수|참여)[^.!?\n]{0,12}\d[\d,.]*\s*(?:명|건|개)", text
    )
    return bool(numerator and denominator)


def _complete_percentage_set(text: str) -> bool:
    values = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    return len(values) >= 2 and abs(sum(values) - 100.0) < 1e-6


def _self_contained(
    demand_kind: str, required_kinds: Sequence[str], visible_text: str
) -> bool:
    if _RETRIEVAL_CONTEXT_RE.search(visible_text):
        return False
    if demand_kind == "clause_quote":
        return False
    if demand_kind == "amount_difference":
        return len(_CURRENCY_RE.findall(visible_text)) >= 2
    if demand_kind == "amount_retrieval":
        return bool(_CURRENCY_RE.search(visible_text))
    if demand_kind in {"ratio_calculation", "formula_retrieval"}:
        if any(
            kind in required_kinds
            for kind in ("numerator_field", "denominator_field", "baseline_value")
        ):
            return _paired_ratio_facts(visible_text) and "baseline_value" not in required_kinds
        return _paired_ratio_facts(visible_text) or len(_PERCENT_RE.findall(visible_text)) >= 2
    if demand_kind == "unit_component_calculation":
        return bool(
            re.search(r"단가[^.!?\n]{0,12}\d", visible_text)
            and re.search(r"수량[^.!?\n]{0,12}\d", visible_text)
        )
    if demand_kind == "quantified_allocation":
        availability = {
            "allocation_amount_total": bool(_BUDGET_TOTAL_RE.search(visible_text)),
            "allocation_quantity_total": bool(_STAFF_TOTAL_RE.search(visible_text)),
            "allocation_ratio_basis": bool(
                _complete_percentage_set(visible_text)
                or _BUDGET_TOTAL_RE.search(visible_text)
                or _STAFF_TOTAL_RE.search(visible_text)
                or _GENERIC_ALLOCATION_TOTAL_RE.search(visible_text)
            ),
            "allocation_total": bool(
                _GENERIC_ALLOCATION_TOTAL_RE.search(visible_text)
                or _BUDGET_TOTAL_RE.search(visible_text)
                or _STAFF_TOTAL_RE.search(visible_text)
            ),
        }
        return all(availability.get(kind, False) for kind in required_kinds)
    return False


def _material_failure_reason(
    *,
    demand_kind: str,
    refs: Sequence[str],
    ref_codes: Mapping[str, Sequence[str]],
    claimed_material: bool,
) -> str:
    priority = (
        "invalid_material_ref_contract",
        "invalid_material_id",
        "dangling_material_ref",
        "cross_question_material_ref",
        "material_not_candidate_visible",
        "material_origin_unverified",
        "invalid_material_provenance",
        "invalid_material_value_kind",
        "material_value_missing",
        "duplicate_material_id",
    )
    found = {code for ref in refs for code in ref_codes.get(ref, ())}
    for code in priority:
        if code in found:
            return code
    if claimed_material and not refs:
        return "claimed_material_not_attached"
    if demand_kind == "formula_retrieval":
        return "missing_candidate_visible_formula_material"
    if demand_kind == "clause_quote":
        return "missing_clause_excerpt"
    if demand_kind == "amount_difference":
        return "missing_amount_material"
    if demand_kind == "quantified_allocation":
        return "missing_allocation_total"
    return "missing_material_ref" if not refs else "material_kind_or_field_mismatch"


def _trusted_material_result(
    *,
    clause: str,
    demand_kind: str,
    required_kinds: Sequence[str],
    refs: Sequence[str],
    registry: Mapping[str, Mapping[str, Any]],
    ref_codes: Mapping[str, Sequence[str]],
    claimed_material: bool,
) -> tuple[bool, list[str], str]:
    eligible = [
        ref
        for ref in refs
        if ref in registry and not ref_codes.get(ref)
    ]
    if not eligible:
        return False, [], _material_failure_reason(
            demand_kind=demand_kind,
            refs=refs,
            ref_codes=ref_codes,
            claimed_material=claimed_material,
        )

    semantic = [
        ref
        for ref in eligible
        if _material_semantically_matches(clause, registry[ref])
    ]
    if not semantic:
        return False, [], "material_semantic_mismatch"

    matched: list[str] = []
    for required_kind in required_kinds:
        providers = [
            ref
            for ref in semantic
            if _material_provides(registry[ref], required_kind)
        ]
        if not providers:
            reason = (
                "missing_candidate_visible_formula_material"
                if demand_kind == "formula_retrieval"
                else "material_kind_or_field_mismatch"
            )
            return False, list(dict.fromkeys(matched)), reason
        matched.extend(providers)
    return True, list(dict.fromkeys(matched)), "grounded_by_candidate_visible_material"


def _issue(code: str, location: str, hashed_text: str) -> dict[str, str]:
    return {"code": code, "location": location, "text_sha256": hashed_text}


def evaluate_question_precision_grounding(
    item: Mapping[str, Any] | str,
    material_registry: Any = None,
) -> dict[str, Any]:
    """Fail closed when a question demands unavailable exact information.

    Only the separately supplied ``material_registry`` is trusted.  Registry
    rows embedded in model output are deliberately ignored.  The result never
    copies question text or material values; it emits locations, stable IDs,
    reason codes, and SHA-256 hashes only.
    """

    source_item: Mapping[str, Any]
    if isinstance(item, Mapping):
        source_item = item
    else:
        source_item = {"question": _text(item)}

    question_id = _text(source_item.get("question_id") or source_item.get("id"))
    refs, refs_contract_valid = _case_material_refs(source_item)
    registry, registry_validation = _registry_index(
        material_registry, question_id=question_id
    )
    ref_codes: dict[str, list[str]] = {}
    reference_issues: list[dict[str, str]] = []
    if not refs_contract_valid:
        reference_issues.append(
            _issue(
                "invalid_material_ref_contract",
                "case_material_refs",
                _sha256(source_item.get("case_material_refs")),
            )
        )
    for index, ref in enumerate(refs):
        codes: list[str] = []
        if not _MATERIAL_ID_RE.fullmatch(ref):
            codes.append("invalid_material_id")
        if ref not in registry:
            codes.append("dangling_material_ref")
        else:
            codes.extend(registry_validation.get(ref, []))
        ref_codes[ref] = list(dict.fromkeys(codes))
        reference_issues.extend(
            _issue(code, f"case_material_refs[{index}]", _sha256(ref))
            for code in ref_codes[ref]
        )

    candidate_rows = _candidate_texts(source_item)
    all_visible_text = " ".join(text for _, text in candidate_rows)
    question_visible_text = _text(source_item.get("question"))
    claimed_material = bool(_MATERIAL_CLAIM_RE.search(all_visible_text))
    demands: list[dict[str, Any]] = []
    demand_issues: list[dict[str, str]] = []

    for location, text in candidate_rows:
        for clause_index, clause in enumerate(_clauses(text)):
            detected = _detect_precision_demand(clause, context=question_visible_text)
            if detected is None:
                continue
            demand_kind, mode, required_kinds = detected
            clause_hash = _sha256(clause)
            basis = "none"
            disposition = "reject"
            reason = "unsupported_precision_demand"
            matched_material_ids: list[str] = []

            if _experience_owned(
                source_item,
                clause,
                precision_detected=True,
                demand_kind=demand_kind,
            ):
                basis = "candidate_owned"
                disposition = "allow"
                reason = "candidate_owned_evidence"
            elif _candidate_authored(clause, demand_kind):
                basis = "candidate_authored"
                disposition = "allow"
                reason = "candidate_authored_value"
            elif _self_contained(
                demand_kind,
                required_kinds,
                f"{question_visible_text} {clause}",
            ):
                basis = "self_contained_scenario"
                disposition = "allow"
                reason = "self_contained_candidate_visible_fact"
            else:
                grounded, matched_material_ids, reason = _trusted_material_result(
                    clause=clause,
                    demand_kind=demand_kind,
                    required_kinds=required_kinds,
                    refs=refs,
                    registry=registry,
                    ref_codes=ref_codes,
                    claimed_material=claimed_material,
                )
                if grounded:
                    basis = "trusted_material"
                    disposition = "allow"
                else:
                    demand_issues.append(_issue(reason, location, clause_hash))

            demands.append(
                {
                    "location": location,
                    "clause_index": clause_index,
                    "kind": demand_kind,
                    "mode": mode,
                    "text_sha256": clause_hash,
                    "required_value_kinds": list(required_kinds),
                    "matched_material_ids": matched_material_ids,
                    "basis": basis,
                    "disposition": disposition,
                    "reason": reason,
                }
            )

    issues = [*reference_issues, *demand_issues]
    detailed_codes = list(dict.fromkeys(issue["code"] for issue in issues))
    issue_codes = (
        ["unsupported_precision_demand", *detailed_codes] if issues else []
    )
    checks = {
        "all_precision_demands_grounded": all(
            demand["disposition"] == "allow" for demand in demands
        ),
        "material_refs_well_formed": refs_contract_valid
        and all("invalid_material_id" not in codes for codes in ref_codes.values()),
        "no_dangling_material_refs": all(
            "dangling_material_ref" not in codes for codes in ref_codes.values()
        ),
        "all_referenced_materials_origin_verified": all(
            "material_origin_unverified" not in codes
            and "invalid_material_provenance" not in codes
            for codes in ref_codes.values()
        ),
        "all_referenced_materials_candidate_visible": all(
            "material_not_candidate_visible" not in codes
            for codes in ref_codes.values()
        ),
        "all_referenced_materials_question_scoped": all(
            "cross_question_material_ref" not in codes
            for codes in ref_codes.values()
        ),
        "all_referenced_materials_have_value_kind": all(
            "invalid_material_value_kind" not in codes
            and "material_value_missing" not in codes
            for codes in ref_codes.values()
        ),
        "no_duplicate_material_ids": all(
            "duplicate_material_id" not in codes for codes in ref_codes.values()
        ),
    }
    passed = all(checks.values())
    return {
        "policy": PRECISION_GROUNDING_POLICY,
        "policy_version": PRECISION_GROUNDING_POLICY,
        "passed": passed,
        "checks": checks,
        "issues": issues,
        "issue_codes": issue_codes,
        "demands": demands,
        "metrics": {
            "candidate_text_count": len(candidate_rows),
            "precision_demand_count": len(demands),
            "grounded_demand_count": sum(
                demand["disposition"] == "allow" for demand in demands
            ),
            "rejected_demand_count": sum(
                demand["disposition"] == "reject" for demand in demands
            ),
            "referenced_material_count": len(refs),
            "matched_material_count": len(
                {
                    material_id
                    for demand in demands
                    for material_id in demand["matched_material_ids"]
                }
            ),
        },
    }


__all__ = [
    "PRECISION_GROUNDING_POLICY",
    "evaluate_question_precision_grounding",
]
