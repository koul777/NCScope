"""Kordoc document parsing and JD section extraction.

Kordoc is a Node package, while the application is FastAPI/Python.  The small
JSON bridge keeps the two runtimes independent and lets the review API expose
the original block/page evidence before NCS lookup is started.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.request_budget import clamp_timeout_to_request_budget


logger = logging.getLogger(__name__)


class KordocParseError(RuntimeError):
    """Raised when Kordoc cannot parse an uploaded document."""


class _LocalKordocUnavailable(KordocParseError):
    """Raised only when the local Node/Kordoc runtime cannot be started."""


_KORDOC_PARSER_VERSION = "4.9.1"
_KORDOC_BRIDGE_MAX_BYTES = 4 * 1024 * 1024
_KORDOC_BRIDGE_PATH = "/api/kordoc-parse"
_KORDOC_BRIDGE_ED25519_PUBLIC_KEY_RAW = (
    "VBlGEy_kzpdThiEEmtrGj7hU6bfUkNtw0SjIrXwK8vA"
)
_SAFE_PARSER_NAMES = {
    "kordoc",
    "plain_text",
    "pdf_text_fallback",
    "hwp_text_fallback",
    "hwpx_text_fallback",
    "mixed_document_parsers",
    "unknown",
}

_BULLET_CHARS = "•○●□■▪▫◦ㅇ"


_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "duties": (
        "수행업무",
        "직무수행내용",
        "주요업무",
        "담당업무",
        "직무내용",
        "직무내용(세부업무)",
        "직무내용 세부업무",
        "수행내용",
        "담당직무",
        "기관주요업무",
    ),
    "qualifications": (
        "지원자격",
        "자격요건",
        "응시자격",
        "필수자격",
        "필요자격",
        "자격기준",
        "자격사항",
        "지원요건",
        "응시요건",
        "관련 자격",
        "관련자격",
    ),
    "preferences": (
        "우대사항",
        "우대조건",
        "가점사항",
        "우대요건",
    ),
    "knowledge": ("필요지식", "지식"),
    "skills": ("필요기술", "기술", "필요지식/기술", "필요지식 및 기술", "필요능력", "필요 역량"),
    "attitudes": ("직무수행태도", "수행태도", "태도"),
    "basic_competencies": ("직업기초능력", "기초능력"),
    "ability_units": (
        "요구능력단위",
        "주요능력단위",
        "능력단위",
        "능력단위명",
        "능력단위명칭",
    ),
    "ncs_detail": (
        "세분류",
        "세분류명",
        "NCS세분류",
        "NCS 세분류",
        "NCS세분류명",
        "NCS 세분류명",
        "직무 세분류",
        "세분류(직무)",
        "세분류(직무명)",
        "NCS분류체계 세분류",
        "NCS 분류체계 세분류",
        "세분류(특화분류)",
        "NCS 세분류(특화분류)",
        "소분류 세분류",
        "소분류 세분류(특화분류)",
    ),
}

_NOTICE_REVIEW_ALIASES: dict[str, tuple[str, ...]] = {
    "duty_text": (
        "담당업무",
        "수행업무",
        "직무수행내용",
        "직무내용",
        "주요업무",
        "채용분야 주요업무",
        "직무기술서",
    ),
    "evaluation_text": (
        "평가항목",
        "평가기준",
        "면접평가",
        "면접 평가",
        "면접전형",
        "면접심사",
        "심사기준",
        "전형방법",
        "직무능력",
        "직업기초능력",
    ),
    "qualification_text": (
        "지원자격",
        "응시자격",
        "자격요건",
        "필수자격",
        "지원요건",
    ),
    "preference_text": (
        "우대사항",
        "우대조건",
        "가점사항",
        "우대요건",
    ),
}

# These labels are safe enough to treat as transitions even when a PDF text
# layer glues the next table row directly onto the previous row's value.
_INLINE_SECTION_TRANSITION_ALIASES: dict[str, tuple[str, ...]] = {
    "knowledge": ("필요지식",),
    "skills": ("필요지식/기술", "필요지식 및 기술", "필요기술"),
    "attitudes": ("직무수행태도", "수행태도"),
    "qualifications": (
        "필요자격",
        "필수자격",
        "지원자격",
        "응시자격",
        "자격요건",
        "자격사항",
    ),
    "preferences": ("우대사항", "우대조건", "가점사항", "우대요건"),
    "basic_competencies": ("직업기초능력",),
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Co")
    text = re.sub(r"[·ᆞ․‧•∙⋅・ㆍ]", "", text)
    return re.sub(r"[\s:：·•\-_/\\()\[\]{}]+", "", text).lower()


_SECTION_ALIAS_KEYS = {
    section: frozenset(_norm(alias) for alias in aliases)
    for section, aliases in _SECTION_ALIASES.items()
}
_ABBREVIATED_DETAIL_LABEL_KEYS = frozenset({_norm("세"), _norm("세분")})


def _scope_label_key(value: Any) -> str:
    """Normalize a displayed detail label for exact scope association."""

    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\(\s*이하\b[^)]*\)", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _exact_detail_hint_matches(
    value: Any,
    detail_candidates: list[str],
) -> tuple[list[str], str]:
    """Match an explicit ability-unit detail hint without fuzzy inference.

    Some institutions qualify a current detail name in the source, for example
    ``사무행정(기록물)`` while the classification table declares
    ``사무행정``. Accept the base only when one trailing, non-nested
    parenthetical qualifier can be removed and the remaining label is an exact
    normalized member of this document's extracted detail set.
    """

    hint = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not hint:
        return [], ""
    hint_key = _scope_label_key(hint)
    exact = [
        detail
        for detail in detail_candidates
        if hint_key and _scope_label_key(detail) == hint_key
    ]
    if exact:
        return exact, "embedded_exact_detail_hint"

    # This is an evidence alias, not a general parenthetical-stripping rule.
    # Current NCS contains canonical detail names with meaningful parentheses,
    # so broad suffix removal would silently corrupt valid classifications.
    qualified_aliases = {
        _scope_label_key("사무행정(기록물)"): _scope_label_key("사무행정"),
    }
    target_key = qualified_aliases.get(hint_key)
    if not target_key:
        return [], ""
    base_matches = [
        detail
        for detail in detail_candidates
        if _scope_label_key(detail) == target_key
    ]
    if len(base_matches) == 1:
        return base_matches, "embedded_exact_base_detail_hint"
    return [], ""


def _clean_text(value: Any, *, normalize_nfkc: bool = True) -> str:
    text = str(value or "")
    if normalize_nfkc:
        text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip(" |\t\r\n:：-•")
    return text.strip()


def _split_table_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    raw = raw.strip("|")
    return [_clean_text(part, normalize_nfkc=False) for part in raw.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(not cell or re.fullmatch(r"[-: ]+", cell or "") for cell in cells)


def _split_items(text: str, *, normalize_nfkc: bool = True) -> list[str]:
    value = _clean_text(text, normalize_nfkc=normalize_nfkc)
    if not value:
        return []
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    parts = re.split(r"\n+|(?<=;)\s*|(?<=；)\s*|(?<=•)\s*", value)
    output: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = re.sub(r"^(?:[-*•○●□■\xa1]|\d+[.)]|[가-힣][.)])\s*", "", part.strip())
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _section_for_label(label: str) -> str | None:
    key = _norm(label)
    if not key:
        return None
    for section, alias_keys in _SECTION_ALIAS_KEYS.items():
        if key in alias_keys:
            return section
    if "세분류" in key and any(marker in key for marker in ("ncs", "특화분류", "소분류")):
        return "ncs_detail"
    return None


def _is_abbreviated_detail_label(value: Any) -> bool:
    """Recognize ``세`` only inside an already identified classification table.

    Some public-institution HWP tables abbreviate the four hierarchy rows to
    ``대/중/소/세``.  Treating ``세`` as a global alias would be unsafe because
    it is an ordinary Korean syllable, so callers must first establish the
    surrounding ``분류체계`` context.
    """

    return _norm(value) in _ABBREVIATED_DETAIL_LABEL_KEYS


def _section_prefix_for_text(value: str) -> tuple[str, str] | None:
    """Read a table label even when PDF flattening glues its value to it.

    Kordoc normally preserves table cells, but some HWP-to-PDF conversions
    return lines such as ``필요지식·전기도면...`` or
    ``직무수행태도안전관리...``.  Exact-label matching leaves every later
    line inside the previous section, so use only unambiguous section labels
    as line prefixes and preserve the attached value for human review.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    ambiguous = {_norm("지식"), _norm("기술"), _norm("태도")}
    aliases = sorted(
        (
            (section, alias)
            for section, values in _SECTION_ALIASES.items()
            for alias in values
            if _norm(alias) not in ambiguous
        ),
        key=lambda item: len(_norm(item[1])),
        reverse=True,
    )
    for section, alias in aliases:
        # PDF text can insert spaces or line-break remnants between the label
        # characters.  At this point each input is already one logical line,
        # so optional whitespace is safe and keeps the remainder indexable.
        pattern = r"\s*".join(re.escape(char) for char in alias if not char.isspace())
        match = re.match(rf"^{pattern}\s*[:：\-]?\s*(.*)$", text, flags=re.DOTALL)
        if match:
            return section, match.group(1).strip()
    return None


def _split_inline_section_transitions(value: str) -> tuple[str, list[tuple[str, str]]] | None:
    """Split strong table labels that were concatenated onto one PDF line."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    matches: list[tuple[int, int, str]] = []
    for section, aliases in _INLINE_SECTION_TRANSITION_ALIASES.items():
        for alias in aliases:
            pattern = r"\s*".join(re.escape(char) for char in alias if not char.isspace())
            for match in re.finditer(pattern, text):
                matches.append((match.start(), match.end(), section))
    if not matches:
        return None

    # Prefer the longest label at the same position (for example
    # ``필요지식/기술`` over its ``필요지식`` prefix) and drop overlaps.
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str]] = []
    for start, end, section in matches:
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end, section))

    prefix = text[: selected[0][0]].strip()
    segments: list[tuple[str, str]] = []
    for index, (_start, end, section) in enumerate(selected):
        next_start = selected[index + 1][0] if index + 1 < len(selected) else len(text)
        remainder = text[end:next_start]
        remainder = re.sub(r"^\s*[:：\-]?\s*", "", remainder).strip()
        segments.append((section, remainder))
    return prefix, segments


_DETAIL_NON_VALUE_LABELS = (
    "대분류",
    "중분류",
    "소분류",
    "세분류",
    "분류체계",
    "ncs분류체계",
    "직무정의",
    "직무 설명",
    "핵심책무",
    "주요사업",
    "기관주요사업",
    "기관주요업무",
    "공단 소개",
    "공단 주요 사업",
    "공단주요사업",
    "능력단위",
    "능력단위명",
    "능력단위명칭",
    "능력단위코드",
    "요구 능력 단위",
    "직무수행내용",
    "필요지식",
    "필요기술",
    "필요능력",
    "필요 역량",
    "직무수행태도",
    "관련자격",
    "요건",
    "교육요건",
    "NCS기반 채용전형 절차",
    "서류접수 → 면접시험",
    "공고문 참조",
    "제한없음",
    "의사소통능력",
    "수리능력",
    "문제해결능력",
    "자기개발능력",
    "자원관리능력",
    "대인관계능력",
    "정보능력",
    "기술능력",
    "조직이해능력",
    "직업윤리",
    "디지털능력",
    "자기관리능력",
    "해당사항 없음",
    "해당 없음",
    "없음",
    "미정",
)
_BASIC_COMPETENCY_LABELS = (
    "의사소통능력",
    "수리능력",
    "문제해결능력",
    "자기개발능력",
    "자원관리능력",
    "대인관계능력",
    "정보능력",
    "기술능력",
    "조직이해능력",
    "직업윤리",
    "디지털능력",
    "자기관리능력",
)


@lru_cache(maxsize=16)
def _normalized_literal_keys(labels: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_norm(label) for label in labels)


def _looks_like_detail_candidate(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    if re.fullmatch(r"(?:p(?:age)?\.?|페이지)\s*\d+(?:\s*/\s*\d+)?", text, flags=re.IGNORECASE):
        return False
    # Broken PDF table cells sometimes expose only one side of a parenthesized
    # label (for example ``(글로벌경영사무`` / ``지원)``).  Such fragments are
    # never complete NCS detail names and must not be promoted independently.
    for opener, closer in (("(", ")"), ("[", "]")):
        if text.count(opener) != text.count(closer):
            return False
    key = _norm(text)
    # A wide classification table can place the adjacent code-column header
    # after the detail header in the same physical row. It is metadata, never
    # a source-stated NCS detail.
    if re.fullmatch(r"ncs(?:분류)?(?:코드|code)(?:8자리)?", key):
        return False
    if not key or key in _normalized_literal_keys(_DETAIL_NON_VALUE_LABELS):
        return False
    noise_fragments = {
        "개발전",
        "직무개요",
        "직무정의",
        "ncs세분류직무설명",
        "세부직무",
        "세부직무및직무수행내용",
        "직무수행내용",
        "ncs미개발",
    }
    if any(fragment in key for fragment in noise_fragments):
        return False
    if re.fullmatch(r"(?:지식|기술|태도)명\\*(?:ncs)?참고", key):
        return False
    if re.search(r"(?:https?://|www\.)", text, flags=re.IGNORECASE):
        return False
    if sum(
        label_key in key
        for label_key in _normalized_literal_keys(_BASIC_COMPETENCY_LABELS)
    ) >= 2:
        return False
    if "미개발" in key:
        return False
    if _section_for_label(text) and _section_for_label(text) != "ncs_detail":
        return False
    compact = re.sub(r"\s+", "", text)
    if re.search(r"[○●□■※]", text):
        return False
    # Current official detail names are authoritative even when they are
    # longer than the generic prose heuristic or contain words such as "및".
    # This preserves labels like 지능형교통체계(ITS) 운영 및 유지관리.
    if key in _official_ncs_detail_name_keys():
        return True
    if len(compact) > 18 and any(marker in text for marker in ("업무", "부대업무", "잡역", " 및 ")):
        return False
    if len(text) > 40:
        return False
    return bool(re.search(r"[가-힣A-Za-z]", text))


def _is_non_ncs_table_label(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    section = _section_for_label(text)
    if section and section != "ncs_detail":
        return True
    key = _norm(text)
    labels = {
        "주요사업",
        "기관주요사업",
        "기관 주요사업",
        "기관주요업무",
        "기관 주요업무",
        "주요업무",
        "담당업무",
        "직무내용",
        "직무 내용",
        "직무정의",
        "직무수행내용",
        "직무 수행내용",
        "세부업무",
        "능력단위",
        "능력단위명",
        "능력단위코드",
        "중점 수행분야",
        "중점수행분야",
        "필요지식",
        "필요기술",
        "직무수행태도",
        "관련자격",
        "근무예정부서",
        "채용분야",
        "공단 소개",
        "공단 주요 사업",
        "공단주요사업",
        "NCS기반 채용전형 절차",
        "전형방법",
        "요건",
        "교육요건",
        "핵심책무",
        "직무 설명",
    }
    return key in {_norm(label) for label in labels}


def _is_detail_value_stop_label(value: str) -> bool:
    """Return whether a flattened row has moved beyond the detail values."""

    key = _norm(value)
    labels = {
        "능력단위",
        "능력단위명",
        "능력단위명칭",
        "능력단위코드",
        "요구 능력 단위",
        "직무수행내용",
        "주요업무",
        "담당업무",
        "필요지식",
        "필요기술",
        "직무수행태도",
        "직업기초능력",
        "직업공통능력",
        "관련자격",
        "공단 소개",
        "공단 주요 사업",
        "공단주요사업",
        "NCS기반 채용전형 절차",
        "전형방법",
        "요건",
        "교육요건",
        "핵심책무",
        "직무 설명",
    }
    return bool(key and key in {_norm(label) for label in labels})


def _row_declares_no_ncs_mapping(cells: list[str]) -> bool:
    key = _norm(" ".join(str(cell or "") for cell in cells))
    return bool(
        key
        and "ncs" in key
        and (
            "mapping가능한직무" in key
            or "매핑가능한직무" in key
            or "mapping" in key
            or "분류체계미개발" in key
            or "미개발분야" in key
        )
        and any(marker in key for marker in ("없어", "없음", "미개발", "별도분석"))
    )


def _row_contains_classification_marker(cells: list[str]) -> bool:
    return any(_norm(cell) in {_norm("분류체계"), _norm("NCS 분류체계")} for cell in cells)


def _is_blank_or_dash_cell(value: Any) -> bool:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _clean_text(raw)
    if not text:
        return True
    return not bool(re.search(r"[가-힣A-Za-z0-9]", text))


def _detail_candidate_filter_reason(value: Any) -> str:
    text = _clean_text(value)
    if _is_blank_or_dash_cell(value):
        return "blank_or_dash_detail_cell"
    key = _norm(text)
    if key in {_norm(item) for item in ("해당사항 없음", "해당 없음", "없음", "미정")}:
        return "declared_no_mapping"
    if _row_declares_no_ncs_mapping([text]) or ("세분류" in key and "미개발" in key):
        return "declared_no_mapping"
    if "미개발" in key:
        return "undeveloped_ncs_value"
    if _section_for_label(text):
        return "classification_label_not_value"
    if len(text) > 40:
        return "value_too_long"
    if len(re.sub(r"\s+", "", text)) > 18 and any(marker in text for marker in ("업무", "부대업무", "잡역", " 및 ")):
        return "duty_text_not_detail"
    if re.search(r"[○●□■※]", text):
        return "bullet_or_note_text"
    return "filtered_candidate_not_detail_like"


def _row_has_ncs_classification_context(cells: list[str]) -> bool:
    key = _norm(" ".join(str(cell or "") for cell in cells))
    if not key:
        return False
    if "ncs" in key and any(marker in key for marker in ("분류체계", "대분류", "중분류", "소분류", "세분류")):
        return True
    return "분류체계" in key and any(marker in key for marker in ("대분류", "중분류", "소분류", "세분류"))


def _ncs_detail_absence_diagnostics(markdown: str) -> dict[str, Any]:
    text = _clean_text(markdown)
    key = _norm(text)
    states: list[str] = []
    evidence: list[str] = []
    filtered_reasons: list[str] = []
    base_reason = _ncs_detail_absence_reason(markdown)
    pipe_detail_index: int | None = None

    def add_state(value: str) -> None:
        if value and value not in states:
            states.append(value)

    def add_evidence(value: Any) -> None:
        snippet = re.sub(r"\s+", " ", _clean_text(value))[:160]
        if snippet and snippet not in evidence:
            evidence.append(snippet)

    def add_filtered_reason(reason: str) -> None:
        if reason and reason not in filtered_reasons:
            filtered_reasons.append(reason)

    def note_detail_value(value: Any, source: Any) -> None:
        reason = _detail_candidate_filter_reason(value)
        if reason == "declared_no_mapping":
            add_state("declared_no_mapping")
        elif reason == "blank_or_dash_detail_cell":
            add_state("blank_or_dash_detail_cell")
        elif not _looks_like_detail_candidate(str(value or "")):
            add_filtered_reason(reason)
            add_state(f"filtered_candidate_reason={reason}")
        add_evidence(source)

    if _row_declares_no_ncs_mapping([text]) or ("세분류" in key and "미개발" in key):
        add_state("declared_no_mapping")
        add_evidence(text)

    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_table_row(line)
        if cells:
            if _is_separator_row(cells):
                continue
            if _row_has_ncs_classification_context(cells):
                add_state("saw_ncs_table")
                add_evidence(line)
            if any(_section_for_label(cell) == "ncs_detail" for cell in cells):
                add_state("saw_detail_header")
            if _row_declares_no_ncs_mapping(cells):
                add_state("declared_no_mapping")
                add_evidence(line)
                continue
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell) == "ncs_detail"), -1)
            if label_index >= 0:
                add_state("saw_detail_header")
                pipe_detail_index = label_index
                value_cells = cells[label_index + 1 :]
                if value_cells:
                    for value in value_cells:
                        note_detail_value(value, line)
                else:
                    add_evidence(line)
                continue
            if pipe_detail_index is not None and not any(_section_for_label(cell) for cell in cells):
                value = cells[pipe_detail_index] if pipe_detail_index < len(cells) else cells[-1]
                note_detail_value(value, line)
                continue
        match = re.search(
            r"(?:NCS\s*)?세분류(?:명|\(\s*직무(?:명)?\s*\))?\s*[:：]\s*(.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            add_state("saw_detail_header")
            value = match.group(1)
            note_detail_value(value, line)

    for raw_table in re.findall(r"<table[^>]*>(.*?)</table>", str(markdown or ""), flags=re.IGNORECASE | re.DOTALL):
        header_sections: dict[int, str] = {}
        classification_context = False
        for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw_table, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", raw_row, flags=re.IGNORECASE | re.DOTALL)
            ]
            if not any(cells):
                continue
            row_text = " ".join(cells)
            classification_context = classification_context or _row_contains_classification_marker(cells)
            if _row_has_ncs_classification_context(cells):
                add_state("saw_ncs_table")
                add_evidence(row_text)
            if any(_section_for_label(cell) == "ncs_detail" for cell in cells):
                add_state("saw_detail_header")
            if _row_declares_no_ncs_mapping(cells):
                add_state("declared_no_mapping")
                add_evidence(row_text)
                continue
            row_sections: dict[int, str] = {}
            for idx, cell in enumerate(cells):
                section = _section_for_label(cell)
                if not section:
                    continue
                target_idx = idx
                cell_key = _norm(cell)
                if section == "ncs_detail" and "소분류" in cell_key and "세분류" in cell_key:
                    target_idx = idx + 1
                row_sections[target_idx] = section
            if row_sections:
                header_sections = row_sections
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell) == "ncs_detail"), -1)
            if label_index < 0 and classification_context:
                label_index = next((i for i, cell in enumerate(cells) if _is_abbreviated_detail_label(cell)), -1)
            if label_index >= 0:
                add_state("saw_detail_header")
                value_cells = cells[label_index + 1 :]
                if value_cells:
                    for value in value_cells:
                        note_detail_value(value, row_text)
                else:
                    add_evidence(row_text)
                continue
            detail_value_indexes = [idx for idx, section in header_sections.items() if section == "ncs_detail"]
            if detail_value_indexes and not any(_section_for_label(cell) for cell in cells):
                add_state("saw_detail_header")
                max_header_idx = max(header_sections) if header_sections else -1
                shift = max(0, max_header_idx - (len(cells) - 1))
                for idx in detail_value_indexes:
                    cell_idx = idx - shift if idx >= len(cells) else idx
                    value = cells[cell_idx] if 0 <= cell_idx < len(cells) else ""
                    note_detail_value(value, row_text)

    if base_reason == "multi_role_healthcare_document_without_explicit_ncs_detail":
        matched_healthcare_markers = [
            marker
            for marker in (
                "간호직",
                "의료기술직",
                "약무직",
                "업무협력직",
                "임상교수",
                "임상병리",
                "영상의학",
                "의료사회복지",
                "의무기록",
            )
            if _norm(marker) and _norm(marker) in key
        ]
        if matched_healthcare_markers:
            add_state("multi_role_healthcare_markers_without_ncs_detail")
            add_state(f"healthcare_marker_count={len(matched_healthcare_markers)}")
            add_evidence("healthcare markers: " + ", ".join(matched_healthcare_markers[:8]))
            add_evidence(text)

    if base_reason == "recruitment_notice_not_job_description":
        add_state("recruitment_notice_markers_without_job_description")
        add_evidence(text)
    elif base_reason and not states:
        if base_reason == "translation_role_without_explicit_ncs_detail":
            add_state("translation_role_markers_without_ncs_detail")
        elif base_reason == "multi_role_healthcare_document_without_explicit_ncs_detail":
            add_state("multi_role_healthcare_markers_without_ncs_detail")
        elif base_reason == "job_document_without_explicit_ncs_detail":
            add_state("job_document_markers_without_ncs_classification")
        else:
            add_state(base_reason)
        add_evidence(text)

    if "declared_no_mapping" in states:
        reason = "no_ncs_mapping_declared"
    elif base_reason == "recruitment_notice_not_job_description":
        reason = base_reason
    elif "blank_or_dash_detail_cell" in states:
        reason = "ncs_detail_cell_blank_or_dash"
    elif filtered_reasons:
        reason = "ncs_detail_candidate_filtered"
    elif "saw_ncs_table" in states and "saw_detail_header" in states:
        reason = "ncs_detail_header_without_candidate"
    elif "saw_ncs_table" in states:
        reason = "ncs_table_without_detail_header"
    else:
        reason = base_reason

    return {
        "reason": reason,
        "state": "; ".join(states),
        "evidence": " | ".join(evidence)[:500],
        "filtered_candidate_reason": "; ".join(filtered_reasons),
        "saw_ncs_table": "saw_ncs_table" in states,
        "saw_detail_header": "saw_detail_header" in states,
        "blank_or_dash_detail_cell": "blank_or_dash_detail_cell" in states,
        "declared_no_mapping": "declared_no_mapping" in states,
    }


def _is_full_ncs_code(value: Any) -> bool:
    """Return whether *value* is only a complete NCS classification code.

    Public-institution job descriptions use several equivalent renderings,
    including ``01010101``, ``01-01-01-01`` and ``(01010101)``.  A two-digit
    table ordinal such as ``01.`` is intentionally not a full code.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"^NCS\s*", "", text, flags=re.IGNORECASE).strip()
    if not text or not re.fullmatch(r"[\d\s()[\]{}.,/_:\-]+", text):
        return False
    digit_count = len(re.sub(r"\D", "", text))
    return 6 <= digit_count <= 10


_OFFICIAL_NCS_SCLASS_CODES: frozenset[str] | None = None
_OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS: frozenset[tuple[str, str, str]] | None = None
_OFFICIAL_NCS_DETAIL_NAME_KEYS: frozenset[str] | None = None
_OFFICIAL_NCS_UNIT_NAME_KEYS: frozenset[str] | None = None
_OFFICIAL_NCS_UNIT_NAME_KEYS: frozenset[str] | None = None


def _official_ncs_sclass_codes() -> frozenset[str]:
    """Load the bundled six-digit NCS codes used to disambiguate joined text.

    A separator-free value such as ``010101프로젝트관리`` is otherwise
    indistinguishable from an ordinary digit-leading name.  Only a prefix
    whose first six digits exist in the official catalog is safe to remove.
    """

    global _OFFICIAL_NCS_SCLASS_CODES
    if _OFFICIAL_NCS_SCLASS_CODES is not None:
        return _OFFICIAL_NCS_SCLASS_CODES

    csv_path = Path(__file__).resolve().parents[2] / "ncs_sclass_codes_with_code_no.csv"
    codes: set[str] = set()
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("NCS_CODE_NO") or "").strip()
                if re.fullmatch(r"\d{6}", code):
                    codes.add(code)
    except (OSError, UnicodeError, csv.Error):
        # Fail closed for the ambiguous no-separator case. Separated code
        # prefixes continue to use the syntax-only behavior below.
        codes.clear()
    _OFFICIAL_NCS_SCLASS_CODES = frozenset(codes)
    return _OFFICIAL_NCS_SCLASS_CODES


def _official_ncs_sclass_hierarchy_keys() -> frozenset[tuple[str, str, str]]:
    """Load normalized 대/중/소분류 triples for exact hierarchy validation."""

    global _OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS
    if _OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS is not None:
        return _OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS
    csv_path = Path(__file__).resolve().parents[2] / "ncs_sclass_codes_with_code_no.csv"
    keys: set[tuple[str, str, str]] = set()
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                hierarchy = (
                    _norm(row.get("NCS_LCLAS_CDNM")),
                    _norm(row.get("NCS_MCLAS_CDNM")),
                    _norm(row.get("NCS_SCLAS_CDNM")),
                )
                if all(hierarchy):
                    keys.add(hierarchy)
    except (OSError, UnicodeError, csv.Error):
        keys.clear()
    _OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS = frozenset(keys)
    return _OFFICIAL_NCS_SCLASS_HIERARCHY_KEYS


def _official_ncs_detail_name_keys() -> frozenset[str]:
    """Load normalized current detail names for fail-closed cell splitting."""

    global _OFFICIAL_NCS_DETAIL_NAME_KEYS
    if _OFFICIAL_NCS_DETAIL_NAME_KEYS is not None:
        return _OFFICIAL_NCS_DETAIL_NAME_KEYS
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "ncs_detail_catalog.json"
    keys: set[str] = set()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        for row in payload.get("details") or []:
            if not isinstance(row, dict):
                continue
            key = _norm(row.get("name"))
            if key:
                keys.add(key)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        keys.clear()
    _OFFICIAL_NCS_DETAIL_NAME_KEYS = frozenset(keys)
    return _OFFICIAL_NCS_DETAIL_NAME_KEYS


def _official_ncs_unit_name_keys() -> frozenset[str]:
    """Load normalized current unit names for conservative cell splitting."""

    global _OFFICIAL_NCS_UNIT_NAME_KEYS
    if _OFFICIAL_NCS_UNIT_NAME_KEYS is not None:
        return _OFFICIAL_NCS_UNIT_NAME_KEYS
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "ncs_unit_catalog.json"
    keys: set[str] = set()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        for row in payload.get("units") or []:
            if not isinstance(row, dict):
                continue
            key = _norm(row.get("name"))
            if key:
                keys.add(key)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        keys.clear()
    _OFFICIAL_NCS_UNIT_NAME_KEYS = frozenset(keys)
    return _OFFICIAL_NCS_UNIT_NAME_KEYS


def _legacy_official_ncs_unit_name_keys() -> frozenset[str]:
    global _OFFICIAL_NCS_UNIT_NAME_KEYS
    if _OFFICIAL_NCS_UNIT_NAME_KEYS is not None:
        return _OFFICIAL_NCS_UNIT_NAME_KEYS
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "ncs_unit_catalog.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        _OFFICIAL_NCS_UNIT_NAME_KEYS = frozenset()
        return _OFFICIAL_NCS_UNIT_NAME_KEYS
    rows = payload.get("units") if isinstance(payload, dict) else []
    keys = {
        _norm(str(row.get("name") or ""))
        for row in rows
        if isinstance(row, dict) and _norm(str(row.get("name") or ""))
    }
    _OFFICIAL_NCS_UNIT_NAME_KEYS = frozenset(keys)
    return _OFFICIAL_NCS_UNIT_NAME_KEYS


def _is_official_joined_ncs_code(value: Any) -> bool:
    if not _is_full_ncs_code(value):
        return False
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = re.sub(r"^NCS\s*", "", normalized, flags=re.IGNORECASE).strip()
    digits = re.sub(r"\D", "", normalized)
    if len(digits) not in {6, 8, 10}:
        return False
    # Whitespace is safe inside a joined code only for the canonical
    # two-digit grouping (``01 01 01직무명``). This prevents the leading
    # digit of a real label such as ``3D프린터개발`` from being swallowed after
    # an already separated ``010101 `` prefix.
    if re.search(r"\s", normalized):
        groups = re.findall(r"\d+", normalized)
        if len(groups) < 2 or any(len(group) != 2 for group in groups):
            return False
    return digits[:6] in _official_ncs_sclass_codes()


def _leading_official_sclass_boundary(value: str) -> int | None:
    """Locate the end of a leading official six-digit base code."""

    text = str(value or "")
    digit_count = 0
    for index, char in enumerate(text):
        if char.isdecimal():
            digit_count += 1
        if digit_count != 6:
            continue
        prefix = text[: index + 1]
        if _is_official_joined_ncs_code(prefix):
            return index + 1
        return None
    return None


def _strip_full_ncs_code_prefix(value: str) -> str:
    """Remove a leading full NCS code without consuming the detail name."""

    # Preserve the name exactly (for example the official ``CO₂`` spelling).
    # ``_is_full_ncs_code`` applies NFKC only to the code fragment it checks.
    text = str(value or "").strip()
    if not text:
        return ""

    boundaries: list[tuple[int, int]] = [
        (match.start(), match.end())
        for match in re.finditer(r"\s+|[.．:：/,_\-]+", text)
    ]
    # Kordoc occasionally drops the space after a parenthesized code.
    boundaries.extend(
        (match.start(), match.start())
        for match in re.finditer(r"(?<=[)\]}）］】])(?=[A-Za-z가-힣])", text)
    )
    # Some PDF/HWP parsers concatenate a bare code and its label without any
    # separator.  This split is ambiguous, so accept it only when the leading
    # six-digit classification exists in the bundled official NCS catalog.
    joined_boundaries = {
        match.start()
        for match in re.finditer(r"(?<=\d)(?=[A-Za-z가-힣])", text)
    }
    base_boundary = _leading_official_sclass_boundary(text)
    if base_boundary is not None and re.search(
        r"[A-Za-z가-힣]", text[base_boundary:]
    ):
        joined_boundaries.add(base_boundary)
    boundaries.extend((position, position) for position in joined_boundaries)

    matches: list[tuple[int, int, str]] = []
    for start, end in boundaries:
        prefix = text[:start].strip()
        remainder = text[end:].strip()
        if not remainder or not re.search(r"[A-Za-z가-힣]", remainder):
            continue
        if _is_full_ncs_code(prefix) and (
            start not in joined_boundaries or _is_official_joined_ncs_code(prefix)
        ):
            digit_count = len(re.sub(r"\D", "", prefix))
            matches.append((digit_count, end, remainder))
    if not matches:
        return text
    # Prefer the longest valid code.  This keeps ``010101-01 직무명`` from
    # stopping after the first six digits and leaving ``01 직무명`` behind.
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return matches[0][2]


def _clean_detail_candidate_text(value: str) -> str:
    # Preserve official surface spelling such as ``CO₂``. Code recognition
    # normalizes only the candidate prefix in ``_is_full_ncs_code``.
    text = _clean_text(value, normalize_nfkc=False)
    text = re.sub(r"\s+", " ", text)
    if _is_full_ncs_code(text):
        return ""
    without_code = _strip_full_ncs_code_prefix(text)
    if without_code != text:
        text = without_code
    else:
        text = re.sub(r"^\d{1,2}(?:\s*[,，.．)）：:\-－]\s*|\s+)", "", text)
    if _is_full_ncs_code(text):
        return ""
    # A number at the end belongs to the next PDF table cell when Kordoc has
    # flattened adjacent numbered cells into one string (``총무 01.``).
    text = re.sub(r"\s+\d{1,2}\s*[.]\s*$", "", text)
    text = re.sub(r"\s*[\(（\[]\s*특화\s*분류\s*[\)）\]]\s*", "", text)
    text = re.sub(
        r"\s*[\(（\[]\s*\*?\s*NCS\s*미개발\s*분야\s*[\)）\]]\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^[,;/|]+", "", text)
    text = re.sub(r"[,;/|:：\\\-]+$", "", text)
    # Institutions commonly suffix a terminal official detail with ``등`` to
    # indicate a non-exhaustive list. Remove it only when the remaining text
    # is an exact current official detail, preserving arbitrary source labels.
    without_etc = re.sub(r"\s+등\s*$", "", text).strip()
    if without_etc != text and _norm(without_etc) in _official_ncs_detail_name_keys():
        text = without_etc
    # A rowspan/colspan conversion can append the next bullet list to the
    # terminal detail cell. Preserve only an official detail prefix before
    # that list; never promote the neighbouring duties as detail candidates.
    bullet_match = re.search(r"\s*[ㅇᄋ○◦]\s*", text)
    if bullet_match:
        prefix = text[: bullet_match.start()].strip(" ,;/|:：\\-")
        if _norm(prefix) in _official_ncs_detail_name_keys():
            text = prefix
    return _clean_text(text, normalize_nfkc=False)


def _legacy_strip_trailing_parenthetical_qualifier(value: Any) -> str:
    text = str(value or "").strip()
    if not text.endswith(")"):
        return text
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                prefix = text[:index].rstrip()
                return prefix or text
    return text


def _legacy_exact_detail_scope_matches(
    value: Any,
    detail_pool: list[Any],
) -> tuple[list[str], str]:
    direct_key = _scope_label_key(value)
    direct_matches = [
        str(detail or "").strip()
        for detail in detail_pool
        if direct_key and _scope_label_key(detail) == direct_key
    ]
    if direct_matches:
        return direct_matches, "embedded_exact_detail_hint"
    base_value = _legacy_strip_trailing_parenthetical_qualifier(value)
    base_key = _scope_label_key(base_value)
    base_matches = [
        str(detail or "").strip()
        for detail in detail_pool
        if base_key and _scope_label_key(detail) == base_key
    ]
    if base_matches and base_key != direct_key:
        return base_matches, "embedded_exact_base_detail_hint"
    return [], ""


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip(" ,;"))
            start = index + 1
    parts.append(text[start:].strip(" ,;"))
    return [part for part in parts if part]


def _legacy_split_numbered_item_by_safe_commas(
    text: str,
    *,
    ordinal: str,
) -> list[dict[str, str]]:
    parts = _split_top_level_commas(text)
    if len(parts) <= 1:
        return [{"text": text, "ordinal": ordinal}]
    if len(parts) > 4 or any(len(part) > 120 for part in parts):
        return [{"text": text, "ordinal": ordinal}]
    official_keys = _official_ncs_unit_name_keys()
    first_is_exact = _norm(parts[0]) in official_keys
    all_exact = all(_norm(part) in official_keys for part in parts)
    qualified_tail = all("(" in part and ")" in part for part in parts[1:])
    if not all_exact and not (first_is_exact and qualified_tail):
        return [{"text": text, "ordinal": ordinal}]
    return [{"text": part, "ordinal": ordinal} for part in parts]


def _expand_composite_detail_candidate(value: str) -> list[str]:
    text = _clean_detail_candidate_text(value)
    if not text:
        return []

    # A slash normally separates multiple detail labels, but it is also part
    # of official NCS names such as ``QM/QC관리``.  Keep acronym-to-acronym
    # slashes intact so an official label is not degraded into two false
    # candidates (``QM`` and ``QC관리``).
    protected_slash = "\ufff0"
    split_text = re.sub(r"(?<=[A-Za-z])/(?=[A-Za-z])", protected_slash, text)
    separated = [
        _clean_detail_candidate_text(part.replace(protected_slash, "/"))
        for part in re.split(r"\s*(?:[,，、;/|]+)\s*", split_text)
        if _clean_detail_candidate_text(part.replace(protected_slash, "/"))
    ]
    if len(separated) > 1 and all(_looks_like_detail_candidate(part) for part in separated):
        return separated

    numbered = [
        _clean_detail_candidate_text(part)
        for part in re.split(r"\s+(?=\d{1,2}\s*[,.)：:\-])", text)
        if _clean_detail_candidate_text(part)
    ]
    if len(numbered) > 1 and all(_looks_like_detail_candidate(part) for part in numbered):
        return numbered

    unified = re.sub(r"[‧･ㆍ•∙⋅・]", "·", text)
    parts = [_clean_detail_candidate_text(part) for part in unified.split("·")]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return [text]

    for suffix in ("조리",):
        if not any(part.endswith(suffix) or part == suffix for part in parts):
            continue
        expanded: list[str] = []
        for part in parts:
            if part == suffix:
                continue
            expanded.append(part if part.endswith(suffix) else f"{part}{suffix}")
        return expanded or [text]
    return [text]


def _official_numbered_detail_cell_segments(value: Any) -> list[str]:
    """Split a long numbered detail cell only when every segment is official.

    This recovers physical cells containing several current detail labels while
    rejecting malformed tables whose ``세분류`` column actually contains
    capability-unit names.
    """

    segments = _expand_composite_detail_candidate(str(value or ""))
    official_keys = _official_ncs_detail_name_keys()
    if len(segments) < 2 or not official_keys:
        return []
    if not all(_norm(segment) in official_keys for segment in segments):
        return []
    return segments


def _html_table_grid(raw_table: str) -> list[tuple[list[str], set[int]]]:
    """Expand simple Kordoc HTML tables into logical column coordinates.

    Kordoc preserves ``rowspan`` and ``colspan`` in its markdown HTML.  A
    regex that only reads visible cells shifts the NCS detail column whenever
    parent hierarchy cells span rows or columns, which can promote a
    ``소분류`` value to a false ``세분류`` candidate.  This small grid builder
    retains those coordinates without introducing a full HTML dependency.
    """

    rows: list[tuple[list[str], set[int]]] = []
    carry: dict[int, tuple[str, int]] = {}
    for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw_table, flags=re.IGNORECASE | re.DOTALL):
        logical: dict[int, str] = {column: value for column, (value, _remaining) in carry.items()}
        fresh_columns: set[int] = set()
        next_carry: dict[int, tuple[str, int]] = {
            column: (value, remaining - 1)
            for column, (value, remaining) in carry.items()
            if remaining > 1
        }
        column = 0
        raw_cells = re.findall(
            r"<t[dh](?P<attrs>[^>]*)>(?P<body>.*?)</t[dh]>",
            raw_row,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for attrs, body in raw_cells:
            while column in logical:
                column += 1
            text = _clean_text(
                html.unescape(re.sub(r"<[^>]+>", " ", body)),
                normalize_nfkc=False,
            )
            colspan_match = re.search(r"\bcolspan\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
            rowspan_match = re.search(r"\browspan\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
            colspan = max(1, int(colspan_match.group(1))) if colspan_match else 1
            rowspan = max(1, int(rowspan_match.group(1))) if rowspan_match else 1
            for offset in range(colspan):
                target = column + offset
                logical[target] = text
                if offset == 0:
                    fresh_columns.add(target)
                if rowspan > 1:
                    next_carry[target] = (text, rowspan - 1)
            column += colspan
        carry = next_carry
        if not logical:
            continue
        width = max(logical) + 1
        rows.append(([logical.get(index, "") for index in range(width)], fresh_columns))
    return rows


def _html_table_position_grid(raw_table: str) -> list[list[dict[str, Any]]]:
    """Return a rowspan/colspan-expanded HTML grid with source coordinates.

    ``_html_table_grid`` intentionally exposes only text because it predates
    the review UI's evidence contract.  Training and audit exports also need
    to know exactly which physical cell supplied a value.  Each logical slot
    therefore retains both its expanded coordinate and the origin cell that
    owns it.  A carried/colspan slot never pretends to be a second source.
    """

    rows: list[list[dict[str, Any]]] = []
    carry: dict[int, tuple[dict[str, Any], int]] = {}
    for row_index, raw_row in enumerate(
        re.findall(r"<tr[^>]*>(.*?)</tr>", raw_table, flags=re.IGNORECASE | re.DOTALL)
    ):
        logical: dict[int, dict[str, Any]] = {}
        next_carry: dict[int, tuple[dict[str, Any], int]] = {}
        for column, (origin, remaining) in carry.items():
            logical[column] = {
                **origin,
                "row": row_index,
                "column": column,
                "carried": True,
            }
            if remaining > 1:
                next_carry[column] = (origin, remaining - 1)

        column = 0
        raw_cells = re.findall(
            r"<t[dh](?P<attrs>[^>]*)>(?P<body>.*?)</t[dh]>",
            raw_row,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for source_column, (attrs, body) in enumerate(raw_cells):
            while column in logical:
                column += 1
            text = _clean_text(
                html.unescape(re.sub(r"<[^>]+>", " ", body)),
                normalize_nfkc=False,
            )
            colspan_match = re.search(r"\bcolspan\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
            rowspan_match = re.search(r"\browspan\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
            colspan = max(1, int(colspan_match.group(1))) if colspan_match else 1
            rowspan = max(1, int(rowspan_match.group(1))) if rowspan_match else 1
            origin = {
                "text": text,
                "origin_row": row_index,
                "origin_column": column,
                "source_column": source_column,
                "row_span": rowspan,
                "column_span": colspan,
            }
            for offset in range(colspan):
                target = column + offset
                logical[target] = {
                    **origin,
                    "row": row_index,
                    "column": target,
                    "carried": offset > 0,
                }
                if rowspan > 1:
                    next_carry[target] = (origin, rowspan - 1)
            column += colspan
        carry = next_carry
        if logical:
            width = max(logical) + 1
            rows.append(
                [
                    logical.get(
                        index,
                        {
                            "text": "",
                            "row": row_index,
                            "column": index,
                            "origin_row": row_index,
                            "origin_column": index,
                            "source_column": index,
                            "row_span": 1,
                            "column_span": 1,
                            "carried": False,
                        },
                    )
                    for index in range(width)
                ]
            )
    return rows


def _markdown_table_position_grids(markdown: str) -> list[list[list[dict[str, Any]]]]:
    """Return simple pipe tables with physical row/column coordinates."""

    tables: list[list[list[dict[str, Any]]]] = []
    current_rows: list[list[str]] = []

    def flush() -> None:
        nonlocal current_rows
        if len(current_rows) >= 2:
            tables.append(
                [
                    [
                        {
                            "text": value,
                            "row": row_index,
                            "column": column,
                            "origin_row": row_index,
                            "origin_column": column,
                            "source_column": column,
                            "row_span": 1,
                            "column_span": 1,
                            "carried": False,
                        }
                        for column, value in enumerate(values)
                    ]
                    for row_index, values in enumerate(current_rows)
                ]
            )
        current_rows = []

    for raw_line in str(markdown or "").splitlines():
        cells = _split_table_row(raw_line.strip())
        if not cells:
            flush()
            continue
        if _is_separator_row(cells):
            continue
        current_rows.append(cells)
    flush()
    return tables


def _has_any_norm(text: str, terms: tuple[str, ...]) -> bool:
    key = _norm(text)
    return any(_norm(term) in key for term in terms)


def _extract_contextual_ncs_detail_candidates(markdown: str) -> list[str]:
    text = _clean_text(markdown)
    if not text:
        return []

    candidates: list[str] = []
    # Some HWP-to-PDF conversions omit the first-page NCS classification table
    # but retain the institution-specific duty, KSA, and qualification rows.
    # This combination is deliberately narrow: it recovers four review
    # candidates only when the electrical-maintenance, legal-safety, and fire
    # facility evidence all survive together.
    aks_electric_facility_signature = (
        _has_any_norm(text, ("전기기기유지보수",))
        and _has_any_norm(text, ("수변전설비", "수전설비"))
        and _has_any_norm(text, ("전기안전관리자의 직무", "전기안전관리 법령"))
        and _has_any_norm(text, ("소방안전관리",))
        and _has_any_norm(text, ("소화활동설비", "피난설비", "소방시설 점검"))
    )
    if aks_electric_facility_signature:
        candidates.extend(
            [
                "전기기기유지보수",
                "전기설비운영",
                "전기안전관리",
                "소방안전관리",
            ]
        )

    if _has_any_norm(text, ("하수도 시설운영", "하수처리", "물재생센터")) and _has_any_norm(
        text,
        ("채수", "수질검사", "수질실험실", "수질분석", "시설운영"),
    ):
        candidates.append("하수처리시설운영관리")

    if _has_any_norm(text, ("한전KPS 영흥사업처", "영흥사업처")) and _has_any_norm(
        text,
        ("영흥 5호기", "계획예방정비공사"),
    ) and _has_any_norm(
        text,
        ("전기설비 정비", "발전설비"),
    ):
        candidates.append("화력발전설비운영")
    if _has_any_norm(text, ("노후상수관망정비사업소", "노후상수도 정비사업")) and _has_any_norm(
        text,
        ("누수탐사", "상수도 정비", "상수관망"),
    ) and _has_any_norm(
        text,
        ("공사감독", "안전관리", "사업관리"),
    ):
        candidates.append("상수관로시설운영관리")

    if _has_any_norm(text, ("의료보조(보건관리)", "의료보조 보건관리")) and _has_any_norm(
        text,
        ("보건교육", "보건교육 요구도", "교육훈련"),
    ) and _has_any_norm(
        text,
        ("보건관리계획수립평가", "사업장 건강증진", "산업안전보건법", "작업환경측정", "근골격계 질환예방관리"),
    ):
        candidates.extend(["보건교육", "산업보건관리"])

    return candidates


def _ncs_detail_absence_reason(markdown: str) -> str:
    text = _clean_text(markdown)
    key = _norm(text)
    if _row_declares_no_ncs_mapping([text]):
        return "no_ncs_mapping_declared"
    if "세분류" in key and "미개발" in key:
        return "no_ncs_mapping_declared"
    if _has_any_norm(key, ("통번역", "통·번역", "통역", "번역")):
        return "translation_role_without_explicit_ncs_detail"
    if _has_any_norm(key, ("공개채용공고", "채용공고문")) and _has_any_norm(
        key,
        ("입사지원서", "응시원서", "전형절차", "접수기간"),
    ):
        return "recruitment_notice_not_job_description"
    healthcare_role_markers = (
        "간호직",
        "의료기술직",
        "약무직",
        "업무협력직",
        "임상교수",
        "임상병리",
        "영상의학",
        "의료사회복지",
        "의무기록",
    )
    if _has_any_norm(key, ("병원", "의료기관")) and sum(_norm(marker) in key for marker in healthcare_role_markers) >= 3:
        return "multi_role_healthcare_document_without_explicit_ncs_detail"
    has_job_document_markers = _has_any_norm(
        key,
        (
            "직무소개서",
            "직무기술서",
            "직무설명자료",
            "직무수행내용",
            "업무내용",
            "직무요건",
            "필요지식",
            "필요기술",
        ),
    )
    # 기관 자체 업무 목록을 ``능력단위``라고 부르는 문서도 있다. 그 단어
    # 하나만으로는 공식 4단계 NCS 분류나 세분류가 제시된 것으로 보지 않는다.
    # A generic ``분류체계`` column and a footer URL such as www.ncs.go.kr do
    # not assert an NCS four-level classification.
    has_ncs_classification_markers = "세분류" in key or any(
        marker in key
        for marker in ("ncs분류체계", "ncs기반채용직무", "ncs기반직무")
    )
    if has_job_document_markers and not has_ncs_classification_markers:
        return "job_document_without_explicit_ncs_detail"
    return ""


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    values: list[str] = []
    for key in ("text", "content", "value", "markdown"):
        value = block.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("cells", "rows", "children", "blocks"):
        value = block.get(key)
        if isinstance(value, list):
            values.extend(_block_text(item) for item in value)
    return " ".join(value for value in values if value)


def _evidence(text: str, block: dict[str, Any] | None = None, line: int = 0) -> dict[str, Any]:
    block = block or {}
    page = block.get("pageNumber", block.get("page", 0))
    try:
        page = int(page or 0)
    except (TypeError, ValueError):
        page = 0
    result: dict[str, Any] = {"text": text, "page": page, "source": "kordoc"}
    if block.get("bbox") is not None:
        result["bbox"] = block.get("bbox")
    if line:
        result["line"] = line
    return result


_JOB_FIELD_LABELS = (
    "채용분야",
    "모집분야",
    "직무분야",
    "채용직무",
    "모집직무",
    "지원분야",
)


def _positive_cell_span(cell: Any, *names: str) -> int:
    if not isinstance(cell, dict):
        return 1
    for name in names:
        value = cell.get(name)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return min(parsed, 100)
    return 1


def _block_table_rows(block: dict[str, Any]) -> list[Any]:
    table = block.get("table") if isinstance(block.get("table"), dict) else block
    rows = table.get("rows")
    if isinstance(rows, list):
        return rows
    cells = table.get("cells")
    if isinstance(cells, list) and (not cells or isinstance(cells[0], (list, tuple))):
        return cells
    return []


def _block_table_position_grid(block: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Expand one Kordoc table block while retaining physical cell origins."""

    rows: list[list[dict[str, Any]]] = []
    carry: dict[int, tuple[dict[str, Any], int]] = {}
    for row_index, raw_row in enumerate(_block_table_rows(block)):
        row_cells = (
            raw_row
            if isinstance(raw_row, (list, tuple))
            else raw_row.get("cells", [])
            if isinstance(raw_row, dict)
            else []
        )
        logical: dict[int, dict[str, Any]] = {}
        next_carry: dict[int, tuple[dict[str, Any], int]] = {}
        for column, (origin, remaining) in carry.items():
            logical[column] = {
                **origin,
                "row": row_index,
                "column": column,
                "carried": True,
            }
            if remaining > 1:
                next_carry[column] = (origin, remaining - 1)

        # Kordoc sometimes emits empty placeholder cells for columns already
        # occupied by a rowspan. Consuming those placeholders again shifts all
        # real values to the right (including NCS hierarchy values). Skip only
        # the exact leading run covered by contiguous carried columns; genuine
        # blank cells elsewhere keep their physical position.
        carried_prefix = 0
        while carried_prefix in logical:
            carried_prefix += 1
        placeholder_count = 0
        for raw_cell in row_cells[:carried_prefix]:
            if (
                _clean_text(_block_text(raw_cell), normalize_nfkc=False)
                or _positive_cell_span(
                    raw_cell,
                    "colSpan",
                    "colspan",
                    "columnSpan",
                    "column_span",
                )
                != 1
                or _positive_cell_span(
                    raw_cell,
                    "rowSpan",
                    "rowspan",
                    "row_span",
                )
                != 1
            ):
                break
            placeholder_count += 1
        previous_width = len(rows[-1]) if rows else 0
        remaining_width = sum(
            _positive_cell_span(
                raw_cell,
                "colSpan",
                "colspan",
                "columnSpan",
                "column_span",
            )
            for raw_cell in row_cells[placeholder_count:]
        )
        if (
            placeholder_count != carried_prefix
            or not previous_width
            or len(logical) + remaining_width != previous_width
        ):
            placeholder_count = 0

        column = 0
        for source_column, raw_cell in enumerate(
            row_cells[placeholder_count:], start=placeholder_count
        ):
            while column in logical:
                column += 1
            text = _clean_text(_block_text(raw_cell), normalize_nfkc=False)
            colspan = _positive_cell_span(
                raw_cell,
                "colSpan",
                "colspan",
                "columnSpan",
                "column_span",
            )
            rowspan = _positive_cell_span(
                raw_cell,
                "rowSpan",
                "rowspan",
                "row_span",
            )
            origin = {
                "text": text,
                "origin_row": row_index,
                "origin_column": column,
                "source_column": source_column,
                "row_span": rowspan,
                "column_span": colspan,
            }
            if isinstance(raw_cell, dict) and raw_cell.get("bbox") is not None:
                origin["bbox"] = raw_cell.get("bbox")
            for offset in range(colspan):
                target = column + offset
                logical[target] = {
                    **origin,
                    "row": row_index,
                    "column": target,
                    "carried": offset > 0,
                }
                if rowspan > 1:
                    next_carry[target] = (origin, rowspan - 1)
            column += colspan
        carry = next_carry
        if logical:
            width = max(logical) + 1
            rows.append(
                [
                    logical.get(
                        index,
                        {
                            "text": "",
                            "row": row_index,
                            "column": index,
                            "origin_row": row_index,
                            "origin_column": index,
                            "source_column": index,
                            "row_span": 1,
                            "column_span": 1,
                            "carried": False,
                        },
                    )
                    for index in range(width)
                ]
            )
    return rows


def _origin_cells(row: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for cell in row:
        key = (int(cell.get("origin_row") or 0), int(cell.get("origin_column") or 0))
        if key in seen or not str(cell.get("text") or "").strip():
            continue
        seen.add(key)
        output.append(cell)
    return sorted(output, key=lambda item: int(item.get("origin_column") or 0))


def _table_value_cells_for_label(
    grid: list[list[dict[str, Any]]],
    label: dict[str, Any],
    *,
    section: str,
) -> tuple[list[dict[str, Any]], str]:
    """Resolve value cells for a row label or a column header.

    Same-row cells take precedence.  If the row contains only field headers,
    the first data row below the label's logical column is used.  This keeps
    extraction deterministic and prevents a column header from swallowing all
    later roles in a long table.
    """

    row_index = int(label.get("origin_row") or 0)
    start = int(label.get("origin_column") or 0)
    span = max(1, int(label.get("column_span") or 1))
    label_key = _norm(str(label.get("text") or ""))
    if section == "ncs_detail" and "소분류" in label_key and "세분류" in label_key:
        # A combined hierarchy header spans 대/중/소/세분류.  Only its final
        # logical column is the 세분류 coordinate.
        start = start + span - 1
        span = 1
    end = start + span

    same_row: list[dict[str, Any]] = []
    if 0 <= row_index < len(grid):
        origins = _origin_cells(grid[row_index])
        next_label_column = min(
            (
                int(cell.get("origin_column") or 0)
                for cell in origins
                if int(cell.get("origin_column") or 0) >= end
                and _section_for_label(str(cell.get("text") or ""))
            ),
            default=10_000,
        )
        for cell in origins:
            column = int(cell.get("origin_column") or 0)
            text = str(cell.get("text") or "").strip()
            if column < end or column >= next_label_column or not text:
                continue
            if _section_for_label(text):
                continue
            same_row.append(cell)
    if same_row:
        if section == "job_fields":
            return same_row[:1], "row_label_value"
        return same_row, "row_label_value"

    downward: list[dict[str, Any]] = []
    downward_seen: set[tuple[int, int]] = set()
    for target_row in range(row_index + 1, len(grid)):
        row = grid[target_row]
        overlapping: dict[tuple[int, int], dict[str, Any]] = {}
        saw_new_label = False
        for logical_column in range(start, end):
            if logical_column >= len(row):
                continue
            cell = row[logical_column]
            text = str(cell.get("text") or "").strip()
            if not text:
                continue
            origin_column = int(cell.get("origin_column") or 0)
            if origin_column < start or origin_column >= end:
                # A wide value belonging to an earlier row label can overlap a
                # later column header after colspan expansion.  Physical
                # overlap alone is not evidence that the value belongs to the
                # later header; only values whose origin starts inside the
                # header's logical span are safe to bind automatically.
                continue
            cell_section = _section_for_label(text)
            if cell_section:
                saw_new_label = True
                continue
            key = (int(cell.get("origin_row") or 0), int(cell.get("origin_column") or 0))
            overlapping[key] = cell
        if saw_new_label:
            break
        for key, cell in overlapping.items():
            if key in downward_seen:
                continue
            downward_seen.add(key)
            downward.append(cell)
    if downward:
        return downward, "column_header_value"
    return [], "unresolved"


def _table_label_records(
    grid: list[list[dict[str, Any]]],
    *,
    table_index: int,
    page: int,
    source: str,
    bbox: Any = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    declared_details: list[str] = []
    declared_detail_keys: set[str] = set()
    for candidate_row in grid:
        for candidate_label in _origin_cells(candidate_row):
            candidate_label_text = str(candidate_label.get("text") or "").strip()
            if _section_for_label(candidate_label_text) != "ncs_detail":
                continue
            candidate_cells, _ = _table_value_cells_for_label(
                grid,
                candidate_label,
                section="ncs_detail",
            )
            for candidate_cell in candidate_cells:
                for candidate in _expand_composite_detail_candidate(
                    str(candidate_cell.get("text") or "").strip()
                ):
                    candidate_key = _scope_label_key(candidate)
                    if (
                        candidate_key
                        and _looks_like_detail_candidate(candidate)
                        and candidate_key not in declared_detail_keys
                    ):
                        declared_detail_keys.add(candidate_key)
                        declared_details.append(candidate)
    for row in grid:
        for label in _origin_cells(row):
            label_text = str(label.get("text") or "").strip()
            section = _section_for_label(label_text)
            job_field_label = _norm(label_text) in {_norm(alias) for alias in _JOB_FIELD_LABELS}
            if not section and not job_field_label:
                continue
            values, layout = _table_value_cells_for_label(
                grid,
                label,
                section=section or "job_fields",
            )
            for value_cell in values:
                raw_value = str(value_cell.get("text") or "").strip()
                if not raw_value:
                    continue
                if section == "ncs_detail":
                    values_to_add = [
                        expanded
                        for expanded in _expand_composite_detail_candidate(raw_value)
                        if _looks_like_detail_candidate(expanded)
                    ]
                elif section == "ability_units":
                    ability_entries = _split_ability_unit_entries(
                        raw_value,
                        declared_details=declared_details,
                    )
                    values_to_add = [entry["text"] for entry in ability_entries]
                elif job_field_label:
                    values_to_add = [raw_value]
                else:
                    values_to_add = _split_items(raw_value, normalize_nfkc=section != "ncs_detail")
                for value_index, value in enumerate(values_to_add):
                    value_row = int(value_cell.get("origin_row") or 0)
                    row_context_cells = []
                    if 0 <= value_row < len(grid):
                        row_context_cells = [
                            {
                                "text": str(cell.get("text") or "").strip(),
                                "column": int(cell.get("origin_column") or 0),
                                "row_span": max(1, int(cell.get("row_span") or 1)),
                                "column_span": max(1, int(cell.get("column_span") or 1)),
                            }
                            for cell in _origin_cells(grid[value_row])
                            if str(cell.get("text") or "").strip()
                        ]
                    record = {
                        "section": section or "job_fields",
                        "text": value,
                        "raw_cell_text": raw_value,
                        "source": source,
                        "page": page,
                        "table_index": table_index,
                        "layout": layout,
                        "label": label_text,
                        "label_cell": {
                            "row": int(label.get("origin_row") or 0),
                            "column": int(label.get("origin_column") or 0),
                            "row_span": max(1, int(label.get("row_span") or 1)),
                            "column_span": max(1, int(label.get("column_span") or 1)),
                        },
                        "value_cell": {
                            "row": int(value_cell.get("origin_row") or 0),
                            "column": int(value_cell.get("origin_column") or 0),
                            "row_span": max(1, int(value_cell.get("row_span") or 1)),
                            "column_span": max(1, int(value_cell.get("column_span") or 1)),
                        },
                        "row_context_cells": row_context_cells,
                    }
                    if section == "ability_units" and value_index < len(ability_entries):
                        detail_hint = str(ability_entries[value_index].get("detail_hint") or "").strip()
                        if detail_hint:
                            record["embedded_ncs_detail"] = detail_hint
                        ordinal = str(ability_entries[value_index].get("ordinal") or "").strip()
                        if ordinal:
                            record["ability_unit_ordinal"] = ordinal
                    value_bbox = value_cell.get("bbox")
                    if value_bbox is not None:
                        record["bbox"] = value_bbox
                    elif bbox is not None:
                        record["table_bbox"] = bbox
                    records.append(record)
    return records


def _scope_positioned_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    job_fields = [record for record in records if record.get("section") == "job_fields"]
    details = [record for record in records if record.get("section") == "ncs_detail"]
    ability_units = [record for record in records if record.get("section") == "ability_units"]
    job_fields.sort(key=lambda item: int((item.get("value_cell") or {}).get("row") or 0))
    details.sort(key=lambda item: int((item.get("value_cell") or {}).get("row") or 0))

    def columns_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_cell = left.get("value_cell") if isinstance(left.get("value_cell"), dict) else {}
        right_cell = right.get("value_cell") if isinstance(right.get("value_cell"), dict) else {}
        left_start = int(left_cell.get("column") or 0)
        right_start = int(right_cell.get("column") or 0)
        left_end = left_start + max(1, int(left_cell.get("column_span") or 1))
        right_end = right_start + max(1, int(right_cell.get("column_span") or 1))
        return left_start < right_end and right_start < left_end

    for record in records:
        if record.get("section") in {"job_fields", "ncs_detail"}:
            continue
        value_row = int((record.get("value_cell") or {}).get("row") or 0)
        prior_roles = [
            item
            for item in job_fields
            if int((item.get("value_cell") or {}).get("row") or 0) <= value_row
        ]
        active_role = prior_roles[-1] if prior_roles else None
        if active_role:
            role_start = int((active_role.get("value_cell") or {}).get("row") or 0)
            later_role_rows = [
                int((item.get("value_cell") or {}).get("row") or 0)
                for item in job_fields
                if int((item.get("value_cell") or {}).get("row") or 0) > role_start
            ]
            role_end = min(later_role_rows) if later_role_rows else 10_000
            scoped_details = [
                item
                for item in details
                if role_start
                <= int((item.get("value_cell") or {}).get("row") or 0)
                < role_end
            ]
            scoped_roles = [str(active_role.get("text") or "").strip()]
        else:
            scoped_details = details
            scoped_roles = []
        # Horizontal matrices bind cells by their physical row, while the
        # common vertical NCS template binds each value column to the detail
        # heading above it. Prefer the same row when available, then narrow by
        # overlapping logical columns. A spanning cell that overlaps several
        # detail columns deliberately remains multi-detail/review-required.
        same_row_details = [
            item
            for item in scoped_details
            if int((item.get("value_cell") or {}).get("row") or 0) == value_row
        ]
        coordinate_candidates = same_row_details or scoped_details
        column_details = [
            item for item in coordinate_candidates if columns_overlap(record, item)
        ]
        if column_details:
            scoped_details = column_details
        elif same_row_details:
            scoped_details = same_row_details
        embedded_detail = str(record.get("embedded_ncs_detail") or "").strip()
        if embedded_detail:
            matched_names, match_source = _exact_detail_hint_matches(
                embedded_detail,
                [str(item.get("text") or "").strip() for item in details],
            )
            matched_keys = {_scope_label_key(name) for name in matched_names}
            embedded_matches = [
                item
                for item in details
                if _scope_label_key(item.get("text")) in matched_keys
            ]
            if embedded_matches:
                scoped_details = embedded_matches
                record["embedded_scope_source"] = match_source
        detail_names: list[str] = []
        for item in scoped_details:
            value = str(item.get("text") or "").strip()
            if value and _norm(value) not in {_norm(existing) for existing in detail_names}:
                detail_names.append(value)
        status = "single_detail" if len(detail_names) == 1 else "multi_detail" if detail_names else "unscoped"
        record["scope"] = {
            "job_fields": scoped_roles,
            "ncs_details": detail_names,
            "status": status,
            "review_required": status != "single_detail",
        }
        record["header_path"] = [
            *scoped_roles,
            *detail_names,
            str(record.get("label") or "").strip(),
        ]

    def unique_text(items: list[dict[str, Any]]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = str(item.get("text") or "").strip()
            key = _norm(value)
            if value and key and key not in seen:
                seen.add(key)
                output.append(value)
        return output

    detail_names = unique_text(details)
    table_scope = {
        "job_fields": unique_text(job_fields),
        "ncs_details": detail_names,
        "ability_units": unique_text(ability_units),
        "status": "single_detail" if len(detail_names) == 1 else "multi_detail" if detail_names else "unscoped",
        "review_required": len(detail_names) != 1,
    }
    return records, table_scope


def _iter_kordoc_table_blocks(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if str(value.get("type") or "").casefold() == "table":
            output.append(value)
            return
        for key in ("children", "blocks"):
            visit(value.get(key))

    visit(parsed.get("blocks") or [])
    return output


def _exact_detail_from_job_unit_hierarchy(value: Any) -> str:
    """Return the literal leaf from an explicit four-level 직무단위 path."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"^[\s\-*•○●□■▪▫◦ㅇ\uf000-\uf8ff]+", "", text)
    parts = [
        part.strip()
        for part in re.split(r"\s+(?:[-–—>＞])\s+", text)
        if part.strip()
    ]
    if len(parts) != 4 or any(len(part) > 50 for part in parts):
        return ""
    hierarchy_key = tuple(_norm(part) for part in parts[:3])
    if hierarchy_key not in _official_ncs_sclass_hierarchy_keys():
        return ""
    leaf = _clean_detail_candidate_text(parts[-1])
    return leaf if _looks_like_detail_candidate(leaf) else ""


def _leading_parenthetical(value: str) -> tuple[str, str]:
    """Return a balanced leading ``(detail)`` label and the remaining text."""

    text = re.sub(r"^(?:[-*•○●□■▪▫◦ㅇ¡]+\s*)+", "", str(value or "")).strip()
    if not text.startswith("("):
        return "", text
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[1:index].strip(), text[index + 1 :].strip()
    return "", text


def _legacy_split_numbered_ability_units_initial(
    value: str,
) -> list[dict[str, str]]:
    text = str(value or "").strip(" ,;；")
    if not text:
        return []
    marker = re.compile(
        r"(?<!\d)(?P<number>\d{1,2})\s*(?:[.)．:]\s*|\s+(?=[A-Za-z가-힣]))"
    )
    matches = list(marker.finditer(text))
    if not matches:
        # A short, unnumbered ability-unit cell commonly uses commas as cell
        # separators. Do not split long prose because clinical/institutional
        # descriptions also contain commas but are not official unit names.
        # Commas inside a balanced parenthetical example list are content, not
        # unit boundaries (for example ``사무용 프로그램(엑셀, 한글,
        # 파워포인트 등) 활용``).
        comma_parts: list[str] = []
        start = 0
        depth = 0
        for index, char in enumerate(text):
            if char in "([{（［｛":
                depth += 1
            elif char in ")]}）］｝" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                comma_parts.append(text[start:index].strip(" ,;；"))
                start = index + 1
        comma_parts.append(text[start:].strip(" ,;；"))
        if (
            len(comma_parts) > 1
            and len(text) <= 240
            and all(part and len(part) <= 80 for part in comma_parts)
        ):
            return [{"text": part, "ordinal": ""} for part in comma_parts]
        return [{"text": text, "ordinal": ""}]

    output: list[dict[str, str]] = []
    prefix = text[: matches[0].start()].strip(" ,;；")
    if prefix and len(prefix) <= 80:
        output.append({"text": prefix, "ordinal": ""})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = text[match.end() : end].strip(" ,;；")
        if item:
            ordinal = str(match.group("number") or "").zfill(2)
            # A source occasionally appends an institution-specific unit after
            # an official numbered unit in the same cell. Split this only when
            # the full string is not official, the first top-level comma part
            # is normalized-exact official, and the second part has an explicit
            # parenthetical definition. This preserves legitimate comma-bearing
            # unit names such as ``채혈,접수 업무``.
            comma_parts: list[str] = []
            start = 0
            depth = 0
            for char_index, char in enumerate(item):
                if char in "([{（［｛":
                    depth += 1
                elif char in ")]}）］｝" and depth:
                    depth -= 1
                elif char == "," and depth == 0:
                    comma_parts.append(item[start:char_index].strip(" ,;；"))
                    start = char_index + 1
            comma_parts.append(item[start:].strip(" ,;；"))
            official_unit_keys = _official_ncs_unit_name_keys()
            split_custom_pair = (
                len(comma_parts) == 2
                and _norm(item) not in official_unit_keys
                and _norm(comma_parts[0]) in official_unit_keys
                and len(comma_parts[1]) <= 100
                and re.fullmatch(r".+?\([^()]+\)", comma_parts[1]) is not None
            )
            if split_custom_pair:
                output.append({"text": comma_parts[0], "ordinal": ordinal})
                output.append({"text": comma_parts[1], "ordinal": ""})
            else:
                output.append({"text": item, "ordinal": ordinal})
    return output


def _legacy_split_ability_unit_entries_initial(value: str) -> list[dict[str, str]]:
    """Split grouped real-world ``요구능력단위`` cells with detail scope."""

    text = _clean_text(value, normalize_nfkc=False)
    if not text:
        return []
    # Kordoc preserves a visual line break between a ten-digit unit code and
    # its name in many HWP tables. Join only that syntax-proven pair before
    # newline chunking so the code is stripped together with its label instead
    # of becoming a spurious standalone ability item.
    text = re.sub(
        r"(?<!\d)(\d{10}(?:_[0-9A-Za-z]+)?\s*[.．:：]?)\s*\n+\s*(?=[A-Za-z가-힣])",
        r"\1 ",
        text,
    )
    numbered_markers = re.findall(
        r"(?<!\d)\d{1,2}\s*(?:[.)：:．]\s*|\s+(?=[A-Za-z가-힣]))",
        text,
    )
    if len(numbered_markers) >= 2:
        # PDF/HWP layout wraps can split a single official unit in the middle
        # of a word (``음청\n류 조리``). Numbered cells already have reliable
        # item boundaries, so preserve the newline as whitespace and let the
        # ordinal splitter decide. Parenthetical detail switches and bullet
        # markers remain explicit split points below.
        text = re.sub(r"\s*\n+\s*", " ", text)
    chunks = re.split(
        r"\n+|[;；]+|(?=\s*[•○●□■▪▫◦ㅇ¡]\s*(?:\(|\d{1,2}\s*[.)．:]))|(?=\s+\([^\n]{1,80}\)\s*\d{1,2}(?:[.)．:]|\s))",
        text,
    )
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    active_detail = ""
    for chunk in chunks:
        chunk = re.sub(r"^(?:[-*•○●□■▪▫◦ㅇ¡]+\s*)+", "", chunk).strip()
        if not chunk:
            continue
        detail_hint, body = _leading_parenthetical(chunk)
        if detail_hint:
            active_detail = detail_hint
            chunk = body
        for numbered_item in _split_numbered_ability_units(chunk):
            raw_item = str(numbered_item.get("text") or "")
            item = re.sub(r"^(?:[-*•○●□■▪▫◦ㅇ¡]+\s*)+", "", raw_item).strip(" ,;；")
            item = re.sub(r"(?:\s*[-*•○●□■▪▫◦¡])+\s*$", "", item).strip()
            item = _strip_full_ncs_code_prefix(item)
            if not item or item in {"-", "해당없음", "없음", "미개발"}:
                continue
            key = (_norm(active_detail), _norm(item))
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "text": item,
                    "detail_hint": active_detail,
                    "ordinal": str(numbered_item.get("ordinal") or ""),
                }
            )
    return output


def _deferred_table_grid_signature(grid: list[list[dict[str, Any]]]) -> str:
    return _table_grid_signature(grid)


# Legacy runtime overrides retained temporarily for source-history review. The
# production path uses the canonical helpers defined above.
def _legacy_table_value_cells_for_label(
    grid: list[list[dict[str, Any]]],
    label: dict[str, Any],
    *,
    section: str,
) -> tuple[list[dict[str, Any]], str]:
    row_index = int(label.get("origin_row") or 0)
    start = int(label.get("origin_column") or 0)
    span = max(1, int(label.get("column_span") or 1))
    label_key = _norm(str(label.get("text") or ""))
    if (
        section == "ncs_detail"
        and span > 1
        and "?뚮텇瑜?" in label_key
        and "?몃텇瑜?" in label_key
    ):
        start = start + span - 1
        span = 1
    end = start + span

    same_row: list[dict[str, Any]] = []
    if 0 <= row_index < len(grid):
        origins = _origin_cells(grid[row_index])
        next_label_column = min(
            (
                int(cell.get("origin_column") or 0)
                for cell in origins
                if int(cell.get("origin_column") or 0) >= end
                and _section_for_label(str(cell.get("text") or ""))
            ),
            default=10_000,
        )
        for cell in origins:
            column = int(cell.get("origin_column") or 0)
            text = str(cell.get("text") or "").strip()
            if column < end or column >= next_label_column or not text:
                continue
            if _section_for_label(text):
                continue
            same_row.append(cell)
    if same_row:
        return same_row, "row_label_value"

    downward: list[dict[str, Any]] = []
    downward_seen: set[tuple[int, int]] = set()
    for target_row in range(row_index + 1, len(grid)):
        row = grid[target_row]
        overlapping: dict[tuple[int, int], dict[str, Any]] = {}
        saw_new_label = False
        for logical_column in range(start, end):
            if logical_column >= len(row):
                continue
            cell = row[logical_column]
            text = str(cell.get("text") or "").strip()
            if not text:
                continue
            origin_column = int(cell.get("origin_column") or 0)
            if origin_column < start or origin_column >= end:
                continue
            cell_section = _section_for_label(text)
            if cell_section:
                saw_new_label = True
                continue
            key = (
                int(cell.get("origin_row") or 0),
                int(cell.get("origin_column") or 0),
            )
            overlapping[key] = cell
        if saw_new_label:
            break
        for key, cell in overlapping.items():
            if key in downward_seen:
                continue
            downward_seen.add(key)
            downward.append(cell)
    if downward:
        return downward, "column_header_value"
    return [], "unresolved"


def _legacy_split_numbered_item_by_safe_commas_override(
    text: str,
    *,
    ordinal: str,
) -> list[dict[str, str]]:
    parts = _split_top_level_commas(text)
    if len(parts) <= 1:
        return [{"text": text, "ordinal": ordinal}]
    if len(parts) > 4 or any(len(part) > 120 for part in parts):
        return [{"text": text, "ordinal": ordinal}]
    official_keys = _official_ncs_unit_name_keys()
    first_is_exact = _norm(parts[0]) in official_keys
    all_exact = all(_norm(part) in official_keys for part in parts)
    qualified_tail = all("(" in part and ")" in part for part in parts[1:])
    if all_exact:
        return [{"text": part, "ordinal": ordinal} for part in parts]
    if first_is_exact and qualified_tail:
        return [{"text": parts[0], "ordinal": ordinal}] + [
            {"text": part, "ordinal": ""} for part in parts[1:]
        ]
    return [{"text": text, "ordinal": ordinal}]


def _legacy_split_numbered_ability_units_override(
    value: str,
) -> list[dict[str, str]]:
    text = str(value or "").strip(" ,;")
    if not text:
        return []
    marker = re.compile(
        r"(?<!\d)(?P<number>\d{1,2})\s*(?:[.)]\s*|\s+(?=[A-Za-z가-힣]))"
    )
    matches = list(marker.finditer(text))
    if not matches:
        comma_parts = _split_top_level_commas(text)
        if (
            len(comma_parts) > 1
            and len(text) <= 240
            and all(part and len(part) <= 80 for part in comma_parts)
        ):
            return [{"text": part, "ordinal": ""} for part in comma_parts]
        return [{"text": text, "ordinal": ""}]

    output: list[dict[str, str]] = []
    prefix = text[: matches[0].start()].strip(" ,;")
    if prefix and len(prefix) <= 80:
        output.append({"text": prefix, "ordinal": ""})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = text[match.end() : end].strip(" ,;")
        if not item:
            continue
        ordinal = str(match.group("number") or "").zfill(2)
        output.extend(_split_numbered_item_by_safe_commas(item, ordinal=ordinal))
    return output


def _legacy_split_ability_unit_entries(value: str) -> list[dict[str, str]]:
    text = _clean_text(value, normalize_nfkc=False)
    if not text:
        return []
    text = re.sub(
        r"(?<!\d)(\d{10}(?:_[0-9A-Za-z]+)?\s*[.)])\s*\n+\s*(?=[A-Za-z가-힣])",
        r"\1 ",
        text,
    )
    numbered_markers = re.findall(
        r"(?<!\d)\d{1,2}\s*(?:[.)]\s*|\s+(?=[A-Za-z가-힣]))",
        text,
    )
    if len(numbered_markers) >= 2:
        text = re.sub(r"\s*\n+\s*", " ", text)
    bullet_class = re.escape(_BULLET_CHARS)
    generic_bullet_split = len(re.findall(rf"[{bullet_class}]", text)) >= 2
    bullet_pattern = (
        rf"(?=\s*[{bullet_class}]\s*(?:\(|\d{{1,2}}\s*[.)]|[A-Za-z가-힣]))"
        if generic_bullet_split
        else rf"(?=\s*[{bullet_class}]\s*(?:\(|\d{{1,2}}\s*[.)]))"
    )
    chunks = re.split(
        rf"\n+|[;；]+|{bullet_pattern}|(?=\s+\([^\n]{{1,80}}\)\s*\d{{1,2}}(?:[.)]|\s))",
        text,
    )
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    active_detail = ""
    for chunk in chunks:
        chunk = re.sub(
            rf"^(?:[-*{re.escape(_BULLET_CHARS)}]+\s*)+",
            "",
            chunk,
        ).strip()
        if not chunk:
            continue
        detail_hint, body = _leading_parenthetical(chunk)
        if detail_hint:
            active_detail = detail_hint
            chunk = body
        for numbered_item in _split_numbered_ability_units(chunk):
            raw_item = str(numbered_item.get("text") or "")
            item = re.sub(
                rf"^(?:[-*{re.escape(_BULLET_CHARS)}]+\s*)+",
                "",
                raw_item,
            ).strip(" ,;")
            item = re.sub(
                rf"(?:\s*[-*{re.escape(_BULLET_CHARS)}])+\s*$",
                "",
                item,
            ).strip()
            item = _strip_full_ncs_code_prefix(item)
            if not item or item in {"-", "?대떦?놁쓬", "?놁쓬", "誘멸컻諛?"}:
                continue
            ordinal = str(numbered_item.get("ordinal") or "").strip()
            key = (_norm(active_detail), _norm(item))
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "text": item,
                    "detail_hint": active_detail,
                    "ordinal": ordinal,
                }
            )
    return output


def _legacy_scope_positioned_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    job_fields = [record for record in records if record.get("section") == "job_fields"]
    details = [record for record in records if record.get("section") == "ncs_detail"]
    ability_units = [record for record in records if record.get("section") == "ability_units"]
    job_fields.sort(key=lambda item: int((item.get("value_cell") or {}).get("row") or 0))
    details.sort(key=lambda item: int((item.get("value_cell") or {}).get("row") or 0))

    def columns_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_cell = left.get("value_cell") if isinstance(left.get("value_cell"), dict) else {}
        right_cell = right.get("value_cell") if isinstance(right.get("value_cell"), dict) else {}
        left_start = int(left_cell.get("column") or 0)
        right_start = int(right_cell.get("column") or 0)
        left_end = left_start + max(1, int(left_cell.get("column_span") or 1))
        right_end = right_start + max(1, int(right_cell.get("column_span") or 1))
        return left_start < right_end and right_start < left_end

    for record in records:
        if record.get("section") in {"job_fields", "ncs_detail"}:
            continue
        value_row = int((record.get("value_cell") or {}).get("row") or 0)
        prior_roles = [
            item
            for item in job_fields
            if int((item.get("value_cell") or {}).get("row") or 0) <= value_row
        ]
        active_role = prior_roles[-1] if prior_roles else None
        if active_role:
            role_start = int((active_role.get("value_cell") or {}).get("row") or 0)
            later_role_rows = [
                int((item.get("value_cell") or {}).get("row") or 0)
                for item in job_fields
                if int((item.get("value_cell") or {}).get("row") or 0) > role_start
            ]
            role_end = min(later_role_rows) if later_role_rows else 10_000
            scoped_details = [
                item
                for item in details
                if role_start
                <= int((item.get("value_cell") or {}).get("row") or 0)
                < role_end
            ]
            scoped_roles = [str(active_role.get("text") or "").strip()]
        else:
            scoped_details = details
            scoped_roles = []
        same_row_details = [
            item
            for item in scoped_details
            if int((item.get("value_cell") or {}).get("row") or 0) == value_row
        ]
        coordinate_candidates = same_row_details or scoped_details
        column_details = [
            item for item in coordinate_candidates if columns_overlap(record, item)
        ]
        if column_details:
            scoped_details = column_details
        elif same_row_details:
            scoped_details = same_row_details
        embedded_matches, embedded_source = _legacy_exact_detail_scope_matches(
            record.get("embedded_ncs_detail"),
            [item.get("text") for item in details],
        )
        if embedded_matches:
            scoped_details = [
                item
                for item in details
                if _scope_label_key(item.get("text"))
                in {_scope_label_key(detail) for detail in embedded_matches}
            ]
        detail_names: list[str] = []
        seen_detail_keys: set[str] = set()
        for item in scoped_details:
            value = str(item.get("text") or "").strip()
            key = _scope_label_key(value)
            if value and key and key not in seen_detail_keys:
                seen_detail_keys.add(key)
                detail_names.append(value)
        status = (
            "single_detail"
            if len(detail_names) == 1
            else "multi_detail"
            if detail_names
            else "unscoped"
        )
        record_scope = {
            "job_fields": scoped_roles,
            "ncs_details": detail_names,
            "status": status,
            "review_required": status != "single_detail",
        }
        if embedded_matches:
            record_scope["source"] = embedded_source
        record["scope"] = record_scope
        record["header_path"] = [
            *scoped_roles,
            *detail_names,
            str(record.get("label") or "").strip(),
        ]

    def unique_text(items: list[dict[str, Any]]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = str(item.get("text") or "").strip()
            key = _scope_label_key(value)
            if value and key and key not in seen:
                seen.add(key)
                output.append(value)
        return output

    detail_names = unique_text(details)
    table_scope = {
        "job_fields": unique_text(job_fields),
        "ncs_details": detail_names,
        "ability_units": unique_text(ability_units),
        "status": "single_detail" if len(detail_names) == 1 else "multi_detail" if detail_names else "unscoped",
        "review_required": len(detail_names) != 1,
    }
    return records, table_scope


def _table_grid_signature(grid: list[list[dict[str, Any]]]) -> str:
    values: list[str] = []
    seen: set[tuple[int, int]] = set()
    for row in grid:
        for cell in _origin_cells(row):
            key = (int(cell.get("origin_row") or 0), int(cell.get("origin_column") or 0))
            if key in seen:
                continue
            seen.add(key)
            text = str(cell.get("text") or "").strip()
            if text:
                values.append(_norm(text))
    return "|".join(values)


def _base_extract_positioned_table_evidence(
    parsed: dict[str, Any],
    markdown: str,
    *,
    valid_detail_candidates: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract section values with auditable table coordinates and scopes."""

    positioned: list[dict[str, Any]] = []
    table_scopes: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    table_index = 0

    def keep_valid_details(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if valid_detail_candidates is None:
            return records
        valid_by_key = {_norm(value): value for value in valid_detail_candidates if _norm(value)}
        output: list[dict[str, Any]] = []
        for record in records:
            if record.get("section") != "ncs_detail":
                output.append(record)
                continue
            matched = valid_by_key.get(_norm(record.get("text")))
            if not matched:
                continue
            output.append({**record, "text": matched})
        return output

    for block in _iter_kordoc_table_blocks(parsed):
        grid = _block_table_position_grid(block)
        if not grid:
            continue
        signature = _table_grid_signature(grid)
        if signature:
            seen_signatures.add(signature)
        try:
            page = int(block.get("pageNumber", block.get("page", 0)) or 0)
        except (TypeError, ValueError):
            page = 0
        records = _table_label_records(
            grid,
            table_index=table_index,
            page=page,
            source="kordoc_table",
            bbox=block.get("bbox"),
        )
        records = keep_valid_details(records)
        records, scope = _scope_positioned_records(records)
        if records:
            positioned.extend(records)
            table_scopes.append(
                {
                    "table_index": table_index,
                    "source": "kordoc_table",
                    "page": page,
                    **scope,
                }
            )
        table_index += 1

    native_ability_keys = {
        _norm(record.get("text"))
        for record in positioned
        if record.get("section") == "ability_units"
        and record.get("source") == "kordoc_table"
        and _norm(record.get("text"))
    }

    def redundant_joined_html_ability(record: dict[str, Any]) -> bool:
        """Drop only an HTML fallback that exactly rejoins native unit rows."""

        if record.get("section") != "ability_units" or not native_ability_keys:
            return False
        value = str(record.get("text") or "").strip()
        bullet_class = re.escape(_BULLET_CHARS)
        parts = [
            re.sub(rf"^(?:[-*{bullet_class}]+\s*)+", "", part).strip()
            for part in re.split(rf"\s+[{bullet_class}]\s+", value)
        ]
        parts = [part for part in parts if part]
        return len(parts) >= 2 and all(_norm(part) in native_ability_keys for part in parts)

    for raw_table in re.findall(r"<table[^>]*>(.*?)</table>", markdown, flags=re.IGNORECASE | re.DOTALL):
        grid = _html_table_position_grid(raw_table)
        if not grid:
            continue
        signature = _table_grid_signature(grid)
        if signature and signature in seen_signatures:
            continue
        records = _table_label_records(
            grid,
            table_index=table_index,
            page=0,
            source="kordoc_html_table",
        )
        records = keep_valid_details(records)
        records, scope = _scope_positioned_records(records)
        records = [record for record in records if not redundant_joined_html_ability(record)]
        if records:
            positioned.extend(records)
            table_scopes.append(
                {
                    "table_index": table_index,
                    "source": "kordoc_html_table",
                    "page": 0,
                    **scope,
                }
            )
        table_index += 1

    for grid in _markdown_table_position_grids(markdown):
        signature = _table_grid_signature(grid)
        if signature and signature in seen_signatures:
            continue
        records = _table_label_records(
            grid,
            table_index=table_index,
            page=0,
            source="kordoc_markdown_table",
        )
        records = keep_valid_details(records)
        records, scope = _scope_positioned_records(records)
        if records:
            positioned.extend(records)
            table_scopes.append(
                {
                    "table_index": table_index,
                    "source": "kordoc_markdown_table",
                    "page": 0,
                    **scope,
                }
            )
        table_index += 1

    return positioned, table_scopes


def _inline_ncs_detail_value(value: Any) -> str | None:
    """Extract a value from an explicit plain-text NCS detail label.

    Table extraction already recognizes every ``ncs_detail`` alias, while the
    plain-text fallback historically recognized only ``세분류:``.  Keep this
    path exact and colon-delimited so prose containing the word ``세분류`` is
    never promoted as a candidate.
    """

    text = str(value or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^(?:[-*•·‧○◦▪□■]\s*)+", "", text)
    text = re.sub(r"^(?:\d{1,2}[.)]\s*)", "", text)
    match = re.match(r"^(.*?)\s*[:：]\s*(.*)$", text, flags=re.DOTALL)
    if match and _section_for_label(match.group(1)) == "ncs_detail":
        # Match the label through normalized keys, but return the original
        # value surface so compatibility characters in an official name are
        # not rewritten (for example ``CO₂`` -> ``CO2``).
        return match.group(2).strip()
    return None


def _extract_ncs_detail_candidates(markdown: str) -> list[str]:
    candidates: list[str] = []
    pipe_detail_index: int | None = None
    for line in markdown.splitlines():
        cells = _split_table_row(line)
        if cells:
            if _is_separator_row(cells):
                continue
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell) == "ncs_detail"), -1)
            if label_index >= 0:
                pipe_detail_index = label_index
                value_cells = cells[label_index + 1 :]
                if len(value_cells) > 1:
                    for value in value_cells:
                        if _is_detail_value_stop_label(value):
                            break
                        candidates.extend(_split_items(value, normalize_nfkc=False))
                else:
                    value = " ".join(value_cells)
                    value = re.sub(r"(?<!^)\s+(?=\d+\s*\.\s*)", "\n", value)
                    candidates.extend(_split_items(value, normalize_nfkc=False))
                continue
            if pipe_detail_index is not None and not any(_section_for_label(cell) for cell in cells):
                value = cells[pipe_detail_index] if pipe_detail_index < len(cells) else cells[-1]
                official_segments = _official_numbered_detail_cell_segments(value)
                if official_segments:
                    candidates.extend(official_segments)
                elif _looks_like_detail_candidate(value):
                    candidates.extend(_split_items(value, normalize_nfkc=False))
                continue
        inline_value = _inline_ncs_detail_value(line)
        if inline_value is not None:
            candidates.extend(_split_items(inline_value, normalize_nfkc=False))
    # Kordoc may retain an HTML table in markdown when colspan/rowspan is
    # meaningful. Parse the label/value rows as a second, lossless path.
    for raw_table in re.findall(r"<table[^>]*>(.*?)</table>", markdown, flags=re.IGNORECASE | re.DOTALL):
        detail_index: int | None = None
        header_sections: dict[int, str] = {}
        classification_context = False
        for cells, fresh_columns in _html_table_grid(raw_table):
            if not any(cells):
                continue
            # Some institution templates put a non-NCS "general job
            # information" hierarchy immediately before the real NCS
            # hierarchy in the same table. Its terminal column is also named
            # 세분류, but it must not be mixed into the NCS declaration.
            if any(
                _norm(cell) in {_norm("일반직무정보"), _norm("일반 직무 정보")}
                for cell in cells
            ):
                continue
            # Other templates flatten all four hierarchy levels into one
            # source cell: ``(대분류)... - ... - (세분류)01.환경미화``.
            # Require explicit NCS context in the same physical row. A nearby
            # general-job hierarchy can use the same parenthesized labels but
            # is not an NCS declaration. Stop before the next structure label
            # or slash so ability units and notes cannot become detail names.
            embedded_details: list[str] = []
            if (
                _row_has_ncs_classification_context(cells)
                and not _row_declares_no_ncs_mapping(cells)
            ):
                for cell in cells:
                    match = re.search(
                        r"[\(（]\s*세분류\s*[\)）]\s*(.+?)"
                        r"(?=\s*(?:[/|]|[\(（]\s*(?:능력단위|비고|대분류|중분류|소분류|세분류)\s*[\)）]|$))",
                        str(cell or ""),
                        flags=re.IGNORECASE,
                    )
                    if not match:
                        continue
                    embedded_details.extend(
                        _split_items(match.group(1), normalize_nfkc=False)
                    )
            if embedded_details:
                candidates.extend(embedded_details)
                classification_context = True
                continue
            classification_context = classification_context or _row_contains_classification_marker(cells)
            # A value such as ``정책연구 (*NCS 미개발 분야)`` is both an
            # explicit source-declared terminal label and a no-current-mapping
            # declaration. Keep the label as review evidence; catalog
            # classification will still leave it uncoded. Generic no-mapping
            # prose is rejected later by the detail-candidate filters.
            if _row_declares_no_ncs_mapping(cells):
                known_detail_indexes = [
                    index
                    for index, section in header_sections.items()
                    if section == "ncs_detail"
                ]
                preserves_explicit_custom_label = any(
                    index < len(cells)
                    and "미개발" in _norm(cells[index])
                    and bool(
                        _clean_detail_candidate_text(cells[index])
                    )
                    for index in known_detail_indexes
                )
                if not preserves_explicit_custom_label:
                    continue
            row_sections: dict[int, str] = {}
            combined_hierarchy_indexes: list[int] = []
            for idx, cell in enumerate(cells):
                section = _section_for_label(cell)
                if not section:
                    continue
                target_idx = idx
                key = _norm(cell)
                if section == "ncs_detail" and "소분류" in key and "세분류" in key:
                    # A combined hierarchy header is often one colspan cell
                    # containing ``대분류 중분류 소분류 세분류``.  The detail
                    # coordinate is the final logical column of that span.
                    combined_hierarchy_indexes.append(idx)
                    continue
                row_sections[target_idx] = section
            if combined_hierarchy_indexes:
                row_sections[max(combined_hierarchy_indexes)] = "ncs_detail"
            if row_sections:
                header_sections = row_sections
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell) == "ncs_detail"), -1)
            if label_index < 0 and classification_context:
                label_index = next((i for i, cell in enumerate(cells) if _is_abbreviated_detail_label(cell)), -1)
                if label_index >= 0:
                    row_sections[label_index] = "ncs_detail"
                    header_sections = row_sections
            if label_index >= 0:
                detail_index = next((idx for idx, section in row_sections.items() if section == "ncs_detail"), label_index)
                label_key = _norm(cells[label_index])
                is_hierarchy_header = "소분류" in label_key and "세분류" in label_key
                if not is_hierarchy_header:
                    # Only a physically new cell can be the value for a fresh
                    # ``세분류`` label.  Some PDF converters collapse all four
                    # hierarchy levels into one value cell with ``rowspan=4``.
                    # On the final label row that carried cell is not a real
                    # detail value; accepting it promotes 대/중/소분류 text and
                    # layout fragments as false details.
                    value_cells = [
                        cells[index]
                        for index in sorted(fresh_columns)
                        if index > label_index
                        and index < len(cells)
                        and cells[index]
                        and _norm(cells[index]) != _norm(cells[label_index])
                    ]
                    if len(value_cells) > 1:
                        for value in value_cells:
                            if _is_detail_value_stop_label(value):
                                break
                            candidates.extend(_split_items(value, normalize_nfkc=False))
                    else:
                        value = " ".join(value_cells)
                        value = re.sub(r"(?<!^)\s+(?=\d+\s*\.\s*)", "\n", value)
                        candidates.extend(_split_items(value, normalize_nfkc=False))
                continue
            if detail_index is None:
                continue
            if any(_section_for_label(cell) for cell in cells):
                break
            fresh_cells = [cells[index] for index in sorted(fresh_columns) if index < len(cells)]
            if any(_is_non_ncs_table_label(cell) for cell in fresh_cells) and not _row_contains_classification_marker(
                fresh_cells
            ):
                break
            detail_value_indexes = [idx for idx, section in header_sections.items() if section == "ncs_detail"]
            if detail_value_indexes:
                max_header_idx = max(header_sections) if header_sections else -1
                shift = max(0, max_header_idx - (len(cells) - 1))
                for idx in detail_value_indexes:
                    cell_idx = idx - shift if idx >= len(cells) else idx
                    if cell_idx not in fresh_columns:
                        # Some institutions intentionally merge only the
                        # adjacent 소분류/세분류 value columns. Accept that
                        # two-column terminal cell, but reject a whole-row
                        # colspan whose origin is several hierarchy columns
                        # earlier (the common OCR false-positive shape).
                        if not (
                            cell_idx > 0
                            and cell_idx - 1 in fresh_columns
                            and cells[cell_idx - 1] == cells[cell_idx]
                        ):
                            continue
                    value = cells[cell_idx] if 0 <= cell_idx < len(cells) else ""
                    official_segments = _official_numbered_detail_cell_segments(value)
                    if official_segments:
                        candidates.extend(official_segments)
                    else:
                        cleaned_value = _clean_detail_candidate_text(value)
                        if _looks_like_detail_candidate(cleaned_value):
                            candidates.extend(
                                _split_items(cleaned_value, normalize_nfkc=False)
                            )
                continue
            value = cells[detail_index] if detail_index < len(cells) else cells[-1]
            official_segments = _official_numbered_detail_cell_segments(value)
            if official_segments:
                candidates.extend(official_segments)
            elif _looks_like_detail_candidate(value):
                candidates.extend(_split_items(value, normalize_nfkc=False))
    seen: set[str] = set()
    clean_candidates = []
    for item in candidates:
        for text in _expand_composite_detail_candidate(item):
            if not _looks_like_detail_candidate(text):
                continue
            key = _norm(text)
            if key in seen:
                continue
            seen.add(key)
            clean_candidates.append(text)
    return clean_candidates


def _plain_classification_detail_candidates(markdown: str) -> list[str]:
    """Read numbered official details from a plain-text NCS hierarchy block."""

    output: list[str] = []
    seen: set[str] = set()
    active = False
    inside_html_table = False
    html_detail_table = False
    official_keys = _official_ncs_detail_name_keys()
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("<table"):
            inside_html_table = True
            html_detail_table = False
        if inside_html_table:
            visible = _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", line)))
            if "세분류" in _norm(visible):
                html_detail_table = True
            if "</table" in lowered:
                inside_html_table = False
                active = html_detail_table
            continue
        heading = re.sub(r"^#{1,6}\s*", "", line)
        heading = re.sub(r"^(?:[-*•·‧○◦▪□■]\s*)+", "", heading)
        heading_key = _norm(heading)
        if heading_key in {_norm("NCS 분류체계"), _norm("분류체계") }:
            active = True
            continue
        if not active:
            continue
        if line.startswith("#"):
            active = False
            continue
        matches = list(re.finditer(r"(?<!\d)\d{1,2}\s*[.．]\s*", line))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            value = _clean_detail_candidate_text(line[match.end() : end])
            key = _norm(value)
            if key and key in official_keys and key not in seen:
                seen.add(key)
                output.append(value)
    return output


def _flattened_numbered_ability_records(
    markdown: str,
    detail_candidates: list[str],
) -> list[dict[str, Any]]:
    """Recover a single-cell OCR row that still has explicit unit evidence."""

    details_by_key = {
        _scope_label_key(detail): detail
        for detail in detail_candidates
        if _scope_label_key(detail)
    }
    output: list[dict[str, Any]] = []
    for table_index, raw_table in enumerate(
        re.findall(r"<table[^>]*>(.*?)</table>", markdown, flags=re.IGNORECASE | re.DOTALL)
    ):
        for row_index, (cells, fresh_columns) in enumerate(_html_table_grid(raw_table)):
            for column in sorted(fresh_columns):
                if column >= len(cells):
                    continue
                raw_text = str(cells[column] or "").strip()
                key = _norm(raw_text)
                numbered = list(
                    re.finditer(r"(?<!\d)\d{1,2}\s*[.．]\s*", raw_text)
                )
                if not ("능력" in key and "단위" in key and len(numbered) >= 3):
                    continue
                hint_matches: list[str] = []
                for hint in re.findall(r"[\(（]([^)）]{1,80})[)）]", raw_text):
                    matched = details_by_key.get(_scope_label_key(hint))
                    if matched and matched not in hint_matches:
                        hint_matches.append(matched)
                if len(hint_matches) != 1:
                    continue
                body = raw_text[numbered[0].start() :]
                body = re.sub(r"\b(?:능력|단위)\b", " ", body)
                entries = _split_ability_unit_entries(
                    f"({hint_matches[0]}) {body}"
                )
                for entry in entries:
                    text = str(entry.get("text") or "").strip()
                    if not text:
                        continue
                    output.append(
                        {
                            "section": "ability_units",
                            "text": text,
                            "raw_cell_text": raw_text,
                            "source": "kordoc_html_flattened_cell",
                            "page": 0,
                            "table_index": table_index,
                            "layout": "flattened_cell_embedded_exact_detail",
                            "label": "능력 단위",
                            "label_cell": {
                                "row": row_index,
                                "column": column,
                                "row_span": 1,
                                "column_span": 1,
                            },
                            "value_cell": {
                                "row": row_index,
                                "column": column,
                                "row_span": 1,
                                "column_span": 1,
                            },
                            "row_context_cells": [
                                {
                                    "text": raw_text,
                                    "column": column,
                                    "row_span": 1,
                                    "column_span": 1,
                                }
                            ],
                            "embedded_ncs_detail": hint_matches[0],
                            "ability_unit_ordinal": str(entry.get("ordinal") or ""),
                            "scope": {
                                "job_fields": [],
                                "ncs_details": [hint_matches[0]],
                                "status": "single_detail",
                                "review_required": False,
                                "source": "embedded_exact_detail_hint",
                            },
                            "header_path": [hint_matches[0], "능력 단위"],
                        }
                    )
    return output


def _dedup_detail_candidates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in values:
        for text in _expand_composite_detail_candidate(item):
            if not _looks_like_detail_candidate(text):
                continue
            key = _norm(text)
            if key in seen:
                continue
            seen.add(key)
            output.append(text)
    return output


def _row_has_detail_evidence_context(line: str) -> bool:
    cells = _split_table_row(line)
    if cells:
        if _row_has_ncs_classification_context(cells):
            return True
        if any(_section_for_label(cell) == "ncs_detail" for cell in cells):
            return True
    key = _norm(line)
    return bool(key and "ncs" in key and "?몃텇瑜?" in key)


def _line_expands_to_detail(line: str, detail: str) -> bool:
    detail_key = _norm(detail)
    if not detail_key:
        return False
    cells = _split_table_row(line) or [_clean_text(line)]
    for cell in cells:
        expanded = _expand_composite_detail_candidate(cell)
        if any(_norm(item) == detail_key for item in expanded):
            return True
    return False


def _contextual_evidence_snippet(markdown: str) -> str:
    scored: list[tuple[int, int, str]] = []
    fallback: list[tuple[int, str]] = []
    for line_no, raw_line in enumerate(str(markdown or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_table_row(line)
        if cells and _is_separator_row(cells):
            continue
        snippet = _clean_text(line)
        if not snippet:
            continue
        fallback.append((line_no, snippet))
        score = 0
        if cells and _row_has_ncs_classification_context(cells):
            score += 4
        if cells:
            sections = {_section_for_label(cell) for cell in cells}
            if sections.intersection({"duties", "knowledge", "skills", "attitudes"}):
                score += 3
            if sections.intersection({"ncs_detail"}):
                score += 2
        if any(marker in _norm(snippet) for marker in ("吏곷Т", "?낅Т", "?섑뻾", "?꾩슂", "洹쇰Т")):
            score += 1
        if score:
            scored.append((score, line_no, snippet))
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return " | ".join(snippet for _score, _line_no, snippet in scored[:3])[:240]
    return " | ".join(snippet for _line_no, snippet in fallback[:3])[:240]


def _detail_candidate_evidence(
    detail_candidates: list[str],
    sections: dict[str, list[dict[str, Any]]],
    markdown: str,
    detail_source: str,
) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    lines = list(enumerate(str(markdown or "").splitlines(), start=1))
    for detail in detail_candidates:
        text = str(detail or "").strip()
        key = _norm(text)
        if not text or not key:
            continue
        evidence: dict[str, Any] = {
            "detail": text,
            "source": detail_source or "unknown",
            "mapping_state": (
                "source_declared_self_developed"
                if any(
                    _norm(marker) in key
                    for marker in ("자체개발", "기관자체개발", "NCS 미개발")
                )
                else "requires_official_validation"
            ),
            "snippet": "",
            "page": 0,
            "line": 0,
        }
        for item in sections.get("ncs_detail", []):
            item_text = str(item.get("text") or "").strip()
            if key and (key in _norm(item_text) or _line_expands_to_detail(item_text, text)):
                evidence.update(
                    {
                        "source": str(item.get("source") or "kordoc"),
                        "snippet": item_text[:240],
                        "page": int(item.get("page") or 0),
                        "line": int(item.get("line") or 0),
                    }
                )
                for coordinate_key in (
                    "table_index",
                    "layout",
                    "label_cell",
                    "value_cell",
                    "header_path",
                    "scope",
                    "bbox",
                    "table_bbox",
                ):
                    if item.get(coordinate_key) is not None:
                        evidence[coordinate_key] = item.get(coordinate_key)
                break
        if not evidence["snippet"]:
            matching_lines: list[tuple[int, str, bool]] = []
            for line_no, line in lines:
                if key and (key in _norm(line) or _line_expands_to_detail(line, text)):
                    matching_lines.append((line_no, line, _row_has_detail_evidence_context(line)))
            for line_no, line, _preferred in sorted(matching_lines, key=lambda item: (not item[2], item[0])):
                evidence.update(
                    {
                        "source": "markdown",
                        "snippet": _clean_text(line)[:240],
                        "line": line_no,
                    }
                )
                break
        if not evidence["snippet"] and detail_source == "contextual":
            snippet = _contextual_evidence_snippet(markdown)
            if snippet:
                evidence.update(
                    {
                        "source": "contextual",
                        "snippet": snippet,
                    }
                )
            else:
                evidence["source"] = "contextual"
                evidence["snippet"] = text
        if any(
            _norm(marker) in _norm(evidence.get("snippet"))
            for marker in ("자체개발", "기관자체개발", "NCS 미개발")
        ):
            evidence["mapping_state"] = "source_declared_self_developed"
        evidence_rows.append(evidence)
    return evidence_rows


def _loads_kordoc_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(text[start:])
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _kordoc_timeout_seconds() -> int:
    timeout_raw = os.getenv("KORDOC_TIMEOUT_SEC", "120")
    try:
        return max(10, min(240, int(timeout_raw)))
    except ValueError:
        return 120


def _stamp_kordoc_result(result: dict[str, Any]) -> dict[str, Any]:
    """Mark a successfully parsed document without exposing bridge internals."""

    stamped = dict(result)
    stamped["parser"] = "kordoc"
    stamped["parser_version"] = _KORDOC_PARSER_VERSION
    return stamped


def _safe_bridge_url() -> str:
    """Build the private bridge URL only from trusted deployment settings."""

    explicit = os.getenv("KORDOC_BRIDGE_URL", "").strip()
    candidate = explicit
    if not candidate:
        vercel_host = os.getenv("VERCEL_URL", "").strip()
        if vercel_host:
            candidate = f"https://{vercel_host.rstrip('/')}{_KORDOC_BRIDGE_PATH}"
    if not candidate:
        return ""

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    hostname = str(parts.hostname or "").strip().casefold()
    local_host = hostname in {"127.0.0.1", "localhost", "::1"}
    if parts.scheme not in ({"http", "https"} if local_host else {"https"}):
        return ""
    if not hostname or parts.username or parts.password or parts.query or parts.fragment:
        return ""
    if parts.path.rstrip("/") != _KORDOC_BRIDGE_PATH:
        return ""
    if not local_host and not re.fullmatch(r"[a-z0-9.-]+", hostname):
        return ""
    return urlunsplit((parts.scheme, parts.netloc, _KORDOC_BRIDGE_PATH, "", ""))


def _normalized_bridge_secret() -> str:
    """Return a strong ASCII-only shared secret safe for an HTTP header."""

    value = os.getenv("KORDOC_BRIDGE_SECRET", "").strip().lstrip("\ufeff").strip()
    return value if re.fullmatch(r"[\x20-\x7e]{32,}", value) else ""


def _bridge_signature_headers(
    data: bytes,
    *,
    encoded_filename: str,
    ocr: bool,
) -> dict[str, str]:
    """Sign one bridge request without sharing the private key with Node."""

    encoded_private_key = (
        os.getenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", "").strip().lstrip("\ufeff").strip()
    )
    if not encoded_private_key or not encoded_private_key.isascii():
        return {}
    try:
        padding = "=" * (-len(encoded_private_key) % 4)
        private_key_bytes = base64.urlsafe_b64decode(encoded_private_key + padding)
        if len(private_key_bytes) != 32:
            raise ValueError("invalid Ed25519 private key length")
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        expected_public_key = base64.urlsafe_b64decode(
            _KORDOC_BRIDGE_ED25519_PUBLIC_KEY_RAW
            + "=" * (-len(_KORDOC_BRIDGE_ED25519_PUBLIC_KEY_RAW) % 4)
        )
        derived_public_key = private_key.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        if len(expected_public_key) != 32 or not hmac.compare_digest(
            derived_public_key,
            expected_public_key,
        ):
            raise ValueError("Ed25519 private key does not match bridge public key")
    except (TypeError, ValueError) as exc:
        raise KordocParseError("Kordoc runtime is unavailable") from exc

    timestamp = str(int(time.time()))
    body_sha256 = hashlib.sha256(data).hexdigest()
    ocr_flag = "1" if ocr else "0"
    message = "\n".join((timestamp, body_sha256, encoded_filename, ocr_flag)).encode("ascii")
    signature = base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii").rstrip("=")
    return {
        "x-ncscope-kordoc-timestamp": timestamp,
        "x-ncscope-kordoc-body-sha256": body_sha256,
        "x-ncscope-kordoc-signature": signature,
    }


def kordoc_bridge_configuration_status() -> dict[str, bool]:
    """Validate serverless bridge URL/auth without exposing secret material."""

    bridge_configured = bool(_safe_bridge_url())
    encoded_private_key = (
        os.getenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", "")
        .strip()
        .lstrip("\ufeff")
        .strip()
    )
    bridge_auth_configured = False
    if encoded_private_key:
        try:
            # Reuse the request signer so readiness and the real bridge call
            # enforce exactly the same Ed25519 decoding/key-length contract.
            bridge_auth_configured = bool(
                _bridge_signature_headers(
                    b"",
                    encoded_filename="",
                    ocr=False,
                )
            )
        except KordocParseError:
            bridge_auth_configured = False
    else:
        bridge_secret = _normalized_bridge_secret()
        bridge_auth_configured = bool(bridge_secret)
    return {
        "bridge_configured": bridge_configured,
        "bridge_auth_configured": bridge_auth_configured,
    }


def _parse_with_remote_kordoc(
    data: bytes,
    *,
    filename: str,
    ocr: bool,
    timeout: int,
) -> dict[str, Any]:
    """Call the authenticated Kordoc function in this Vercel deployment."""

    if len(data) > _KORDOC_BRIDGE_MAX_BYTES:
        raise KordocParseError("document exceeds the Kordoc bridge upload limit")
    bridge_url = _safe_bridge_url()
    if not bridge_url:
        raise KordocParseError("Kordoc runtime is unavailable")

    safe_filename = str(filename or "").replace("\r", " ").replace("\n", " ")[:240]
    encoded_filename = base64.urlsafe_b64encode(safe_filename.encode("utf-8")).decode("ascii").rstrip("=")
    signature_headers = _bridge_signature_headers(
        data,
        encoded_filename=encoded_filename,
        ocr=ocr,
    )
    bridge_secret = _normalized_bridge_secret()
    if not signature_headers and not bridge_secret:
        raise KordocParseError("Kordoc runtime is unavailable")
    headers = {
        "content-type": "application/octet-stream",
        "accept": "application/json",
        "x-ncscope-filename-b64": encoded_filename,
        "x-ncscope-ocr": "1" if ocr else "0",
    }
    if signature_headers:
        headers.update(signature_headers)
    elif bridge_secret:
        headers["x-ncscope-kordoc-secret"] = bridge_secret
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(10.0, float(timeout))),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(bridge_url, content=data, headers=headers)
    except httpx.TimeoutException as exc:
        raise KordocParseError("Kordoc bridge timed out") from exc
    except (httpx.HTTPError, UnicodeError) as exc:
        raise KordocParseError("Kordoc bridge is unavailable") from exc

    if response.status_code != 200:
        rejection = str(response.headers.get("x-ncscope-bridge-rejection") or "")
        if not rejection:
            try:
                rejection_payload = response.json()
            except (TypeError, ValueError):
                rejection_payload = {}
            if isinstance(rejection_payload, dict):
                rejection = str(rejection_payload.get("reason_code") or "")
        safe_rejection = rejection if re.fullmatch(r"[a-z_]{1,40}", rejection) else "unspecified"
        logger.warning(
            "kordoc_bridge_rejected status=%s auth_mode=%s reason=%s",
            response.status_code,
            "ed25519" if signature_headers else "shared_secret",
            safe_rejection,
        )
        # Do not copy a serverless exception body into the Python response.
        raise KordocParseError("Kordoc bridge rejected the document")
    if len(response.content) > 4 * 1024 * 1024:
        raise KordocParseError("Kordoc bridge response exceeded the safe limit")
    try:
        result = response.json()
    except (TypeError, ValueError) as exc:
        raise KordocParseError("Kordoc bridge returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise KordocParseError("Kordoc bridge could not parse the document")
    if str(result.get("parser") or "") != "kordoc":
        raise KordocParseError("Kordoc bridge returned invalid provenance")
    return _stamp_kordoc_result(result)


def _parse_with_local_kordoc(
    data: bytes,
    *,
    filename: str,
    ocr: bool,
    timeout: int,
) -> dict[str, Any]:
    node = shutil.which("node") or shutil.which("node.exe")
    script = Path(__file__).resolve().parents[2] / "scripts" / "kordoc_parse.mjs"
    if not node:
        raise _LocalKordocUnavailable("local Kordoc runtime is unavailable")
    if not script.exists():
        raise _LocalKordocUnavailable("local Kordoc bridge is unavailable")

    payload = {
        "filename": filename,
        "dataBase64": base64.b64encode(data).decode("ascii"),
        "ocr": bool(ocr),
    }
    try:
        completed = subprocess.run(
            [node, str(script)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(script.parents[1]),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KordocParseError(f"Kordoc parsing timed out after {timeout}s") from exc
    except OSError as exc:
        raise _LocalKordocUnavailable("local Kordoc process could not start") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-1200:]
        unavailable_markers = (
            "err_module_not_found",
            "cannot find package",
            "cannot find module",
            "module not found",
        )
        if any(marker in detail.casefold() for marker in unavailable_markers):
            raise _LocalKordocUnavailable("local Kordoc module is unavailable")
        raise KordocParseError("Kordoc could not parse the document")
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        result = _loads_kordoc_json(raw)
    except json.JSONDecodeError as exc:
        raise KordocParseError("Kordoc returned invalid JSON") from exc
    if not result.get("success", True):
        raise KordocParseError("Kordoc could not parse the document")
    return _stamp_kordoc_result(result)


def parse_with_kordoc(data: bytes, filename: str = "", ocr: bool = False) -> dict[str, Any]:
    """Parse with local Kordoc first, using the Vercel bridge only if unavailable."""

    if not data:
        raise KordocParseError("uploaded document is empty")
    timeout = clamp_timeout_to_request_budget(
        _kordoc_timeout_seconds(),
        reserve_sec=5.0,
        minimum_sec=1.0,
    )
    try:
        return _parse_with_local_kordoc(
            data,
            filename=filename,
            ocr=ocr,
            timeout=timeout,
        )
    except _LocalKordocUnavailable:
        if not (os.getenv("KORDOC_BRIDGE_URL", "").strip() or os.getenv("VERCEL_URL", "").strip()):
            raise KordocParseError("Kordoc runtime is unavailable") from None
        return _parse_with_remote_kordoc(
            data,
            filename=filename,
            ocr=ocr,
            timeout=timeout,
        )


def _parser_provenance(parsed: dict[str, Any]) -> dict[str, str]:
    """Return a whitelisted parser label for public review payloads."""

    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    raw_parser = str(parsed.get("parser") or "").strip().casefold().replace("-", "_")
    fallback = str(metadata.get("fallback") or "").strip().casefold().replace("-", "_")
    fallback_map = {
        "pdf_text": "pdf_text_fallback",
        "hwp_text": "hwp_text_fallback",
        "hwpx_text": "hwpx_text_fallback",
    }
    parser = fallback_map.get(fallback, raw_parser)
    if parser not in _SAFE_PARSER_NAMES:
        parser = "unknown"
    provenance = {"parser": parser}
    version = str(parsed.get("parser_version") or "").strip()
    if parser == "kordoc" and re.fullmatch(r"\d+\.\d+\.\d+", version):
        provenance["parser_version"] = version
    return provenance


def structure_job_description(parsed: dict[str, Any], filename: str = "") -> dict[str, Any]:
    markdown = str(parsed.get("markdown") or "")
    provenance = _parser_provenance(parsed)
    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in _SECTION_ALIASES}
    section_text_keys: dict[str, set[str]] = {key: set() for key in _SECTION_ALIASES}
    section_items_by_key: dict[str, dict[str, dict[str, Any]]] = {
        key: {} for key in _SECTION_ALIASES
    }
    current: str | None = None
    active_ability_detail = ""
    # The filename is useful evidence when a sparse/partially parsed document
    # still declares itself to be a 직무기술서. It must never create an NCS
    # candidate, but it lets the review UI distinguish "no explicit detail"
    # from an unclassified empty state.
    diagnostic_lines: list[str] = [value for value in (filename, markdown) if value]

    def add(section: str, text: str, block: dict[str, Any] | None = None, line: int = 0) -> None:
        nonlocal active_ability_detail
        if section == "ability_units":
            ability_text = re.sub(r"\s*\|\s*", " ", str(text or ""))
            entries = _split_ability_unit_entries(ability_text)
            items = []
            for entry in entries:
                detail_hint = str(entry.get("detail_hint") or "").strip()
                if detail_hint:
                    active_ability_detail = detail_hint
                items.append(
                    (
                        str(entry.get("text") or "").strip(),
                        active_ability_detail,
                        str(entry.get("ordinal") or "").strip(),
                    )
                )
        else:
            active_ability_detail = ""
            items = [
                (item, "", "")
                for item in _split_items(
                    text, normalize_nfkc=section != "ncs_detail"
                )
            ]
        for item, detail_hint, ordinal in items:
            if not item:
                continue
            item_key = _norm(item) or item
            if item_key in section_text_keys[section]:
                continue
            evidence = _evidence(item, block=block, line=line)
            if detail_hint:
                evidence["embedded_ncs_detail"] = detail_hint
            if ordinal:
                evidence["ability_unit_ordinal"] = ordinal
            sections[section].append(evidence)
            section_text_keys[section].add(item_key)
            section_items_by_key[section][item_key] = evidence

    lines = markdown.splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        lowered_line = line.lower()
        if lowered_line.startswith("<table"):
            current = None
            active_ability_detail = ""
            continue
        if lowered_line.startswith(("<tr", "</table", "</tr")):
            continue
        cleaned_line = _clean_text(line)
        if re.fullmatch(r"(?:페이지\s*)?\d+(?:\s*/\s*\d+)?", cleaned_line, flags=re.IGNORECASE):
            continue
        if (
            cleaned_line.startswith("※")
            and "본 직무수행 내용" in cleaned_line
            and "NCS" in cleaned_line.upper()
        ):
            current = None
            continue
        cells = _split_table_row(line)
        if cells:
            if _is_separator_row(cells):
                continue
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell)), -1)
            if label_index >= 0:
                current = _section_for_label(cells[label_index])
                if current and len(cells) > label_index + 1:
                    add(current, " ".join(cells[label_index + 1 :]), line=line_no)
                continue
        heading_text = re.sub(r"^#{1,6}\s*", "", line)
        heading_text = re.sub(
            r"^(?:[-*•○●□■▪▫◦ㅇ¡]+\s*)+",
            "",
            heading_text,
        )
        heading_text = re.sub(r"^(?:\d+[.)]|[가-힣][.)])\s*", "", heading_text)
        transitions = _split_inline_section_transitions(heading_text)
        if transitions:
            prefix, segments = transitions
            if prefix:
                prefixed = _section_prefix_for_text(prefix)
                if prefixed:
                    current, remainder = prefixed
                    if remainder:
                        add(current, remainder, line=line_no)
                elif current:
                    add(current, prefix, line=line_no)
            for section, remainder in segments:
                current = section
                if remainder:
                    add(section, remainder, line=line_no)
            continue
        prefixed = _section_prefix_for_text(heading_text)
        if prefixed:
            current, remainder = prefixed
            if remainder:
                add(current, remainder, line=line_no)
            continue
        heading = _section_for_label(heading_text)
        if heading:
            current = heading
            remainder = re.sub(
                r"^.*?(?:수행업무|직무수행내용|주요업무|담당업무|직무내용|수행내용|담당직무|"
                r"지원자격|자격요건|응시자격|필수자격|자격기준|지원요건|응시요건|우대사항|우대조건|"
                r"가점사항|우대요건|필요지식|필요기술|직무수행태도|수행태도|직업기초능력|세분류)\s*[:：-]?\s*",
                "",
                line,
            )
            if _norm(remainder) != _norm(line):
                add(heading, remainder, line=line_no)
            continue
        if line.startswith("#"):
            current = None
            continue
        if current:
            add(current, line, line=line_no)

    # Some Kordoc versions expose table blocks more faithfully than markdown.
    def visit(block: Any) -> None:
        if not isinstance(block, dict):
            return
        block_type = str(block.get("type") or "").lower()
        if block_type == "table":
            table = block.get("table") if isinstance(block.get("table"), dict) else block
            rows = table.get("cells") if isinstance(table.get("cells"), list) else table.get("rows") or []
            if isinstance(rows, list):
                classification_context = False
                for row in rows:
                    row_cells = row if isinstance(row, list) else row.get("cells", []) if isinstance(row, dict) else []
                    values = [
                        _clean_text(_block_text(cell), normalize_nfkc=False)
                        for cell in row_cells
                    ]
                    if any(values):
                        diagnostic_lines.append("| " + " | ".join(values) + " |")
                    if values and _norm(values[0]) == _norm("직무단위"):
                        for raw_value in values[1:]:
                            hierarchy_detail = _exact_detail_from_job_unit_hierarchy(
                                raw_value
                            )
                            if hierarchy_detail:
                                add("ncs_detail", hierarchy_detail, block=block)
                    classification_context = classification_context or _row_contains_classification_marker(values)
                    if _row_declares_no_ncs_mapping(values):
                        continue
                    label_index = next((i for i, cell in enumerate(values) if _section_for_label(cell)), -1)
                    abbreviated_detail = False
                    if label_index < 0 and classification_context:
                        label_index = next(
                            (i for i, cell in enumerate(values) if _is_abbreviated_detail_label(cell)),
                            -1,
                        )
                        abbreviated_detail = label_index >= 0
                    if label_index >= 0:
                        section = "ncs_detail" if abbreviated_detail else _section_for_label(values[label_index])
                        if section:
                            if section == "ncs_detail":
                                # Preserve Kordoc's table-cell boundary. Joining
                                # numbered detail cells shifts the next ordinal
                                # onto the previous label and splits multiline
                                # parenthesized labels into false candidates.
                                for value in values[label_index + 1 :]:
                                    if _is_detail_value_stop_label(value):
                                        break
                                    add(section, re.sub(r"\s+", " ", value), block=block)
                            else:
                                add(section, " ".join(values[label_index + 1 :]), block=block)
        for child_key in ("children", "blocks", "rows", "cells"):
            children = block.get(child_key)
            if isinstance(children, list):
                for child in children:
                    visit(child)

    for block in parsed.get("blocks") or []:
        visit(block)

    # Keep the established, conservative detail recognizer authoritative. The
    # coordinate extractor may locate many label/value pairs, but it must not
    # promote a nearby duty, ability unit, or an explicit "no mapping" row to
    # an NCS detail merely because it occupies the expected table column.
    explicit_detail_candidates = _dedup_detail_candidates(
        [
            *_extract_ncs_detail_candidates(markdown),
            *_plain_classification_detail_candidates(markdown),
            *(item["text"] for item in sections["ncs_detail"] if not item.get("line")),
        ]
    )
    positioned_items, table_scopes = _extract_positioned_table_evidence(
        parsed,
        markdown,
        valid_detail_candidates=explicit_detail_candidates,
    )
    positioned_ability_keys = {
        (
            _norm(item.get("text")),
            _scope_label_key(item.get("embedded_ncs_detail")),
        )
        for item in positioned_items
        if item.get("section") == "ability_units"
    }
    for recovered in _flattened_numbered_ability_records(
        markdown,
        explicit_detail_candidates,
    ):
        recovered_key = (
            _norm(recovered.get("text")),
            _scope_label_key(recovered.get("embedded_ncs_detail")),
        )
        if recovered_key in positioned_ability_keys:
            continue
        positioned_items.append(recovered)
        positioned_ability_keys.add(recovered_key)
    for positioned in positioned_items:
        section = str(positioned.get("section") or "")
        if section not in sections:
            continue
        text = str(positioned.get("text") or "").strip()
        if not text:
            continue
        text_key = _norm(text) or text
        existing = section_items_by_key[section].get(text_key)
        positional_evidence = {
            key: positioned[key]
            for key in (
                "source",
                "page",
                "table_index",
                "layout",
                "label",
                "label_cell",
                "value_cell",
                "raw_cell_text",
                "embedded_ncs_detail",
                "header_path",
                "scope",
                "bbox",
                "table_bbox",
            )
            if positioned.get(key) is not None
        }
        if existing is not None:
            # Enrich the established observation with coordinates without
            # changing its parser provenance. Existing clients and review
            # rules distinguish the original ``kordoc`` source from the
            # coordinate-only table projection.
            for key, value in positional_evidence.items():
                if key in {"source", "page"}:
                    continue
                if key not in existing or existing.get(key) is None or existing.get(key) == "":
                    existing[key] = value
            if not existing.get("page") and positional_evidence.get("page"):
                existing["page"] = positional_evidence["page"]
        else:
            evidence = {"text": text, **positional_evidence}
            sections[section].append(evidence)
            section_text_keys[section].add(text_key)
            section_items_by_key[section][text_key] = evidence

    detail_candidates = list(explicit_detail_candidates)
    detail_source = "explicit" if detail_candidates else ""
    if not detail_candidates:
        detail_candidates = _extract_contextual_ncs_detail_candidates(markdown)
        detail_source = "contextual" if detail_candidates else ""
    detail_candidate_evidence = _detail_candidate_evidence(detail_candidates, sections, markdown, detail_source)
    self_developed_details = [
        str(row.get("detail") or "").strip()
        for row in detail_candidate_evidence
        if row.get("mapping_state") == "source_declared_self_developed"
        and str(row.get("detail") or "").strip()
    ]
    absence_diagnostics = {} if detail_candidates else _ncs_detail_absence_diagnostics("\n".join(diagnostic_lines))
    if not detail_candidates:
        warning_codes = {
            str(warning.get("code") or "").strip()
            for warning in (parsed.get("warnings") or [])
            if isinstance(warning, dict)
        }
        if "NEEDS_OCR" in warning_codes and "OCR_APPLIED" not in warning_codes:
            absence_diagnostics = {
                **absence_diagnostics,
                "reason": "ocr_required_extraction_failure",
                "state": "ocr_required_without_ocr_output",
                "evidence": "Kordoc warning NEEDS_OCR without OCR_APPLIED",
            }
        elif not str(markdown or "").strip() and not absence_diagnostics.get("reason"):
            absence_diagnostics = {
                **absence_diagnostics,
                "reason": "empty_document_extraction_failure",
                "state": "empty_parser_output",
                "evidence": "Kordoc returned empty markdown",
            }

    # Prefer coordinate-backed ability-unit cells over the earlier flattened
    # markdown observation. A Kordoc table can contain several unit cells; the
    # flattened section reader may also retain their joined surface as one
    # extra value, which is not safe for an exact NCS unit lookup.
    positioned_ability_units: list[str] = []
    positioned_ability_unit_keys: set[str] = set()
    ability_units_by_detail: dict[str, list[str]] = {}
    ability_unit_keys_by_detail: dict[str, set[str]] = {}
    for item in positioned_items:
        if item.get("section") != "ability_units":
            continue
        value = str(item.get("text") or "").strip()
        value_key = _norm(value)
        if value and value_key and value_key not in positioned_ability_unit_keys:
            positioned_ability_units.append(value)
            positioned_ability_unit_keys.add(value_key)
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        scoped_details = [
            str(detail or "").strip()
            for detail in (scope.get("ncs_details") or [])
            if str(detail or "").strip()
        ]
        embedded_matches, embedded_scope_source = _exact_detail_hint_matches(
            item.get("embedded_ncs_detail"),
            detail_candidates,
        )
        if embedded_matches:
            # An explicit ``(detail)`` prefix in the source cell is stronger
            # evidence than a positional scope inherited from a malformed or
            # flattened table.  Require normalized-exact membership in the
            # document's extracted detail set; never use fuzzy matching.
            positional_details = list(scoped_details)
            scoped_details = embedded_matches
            scope_conflict = {
                _scope_label_key(detail) for detail in positional_details
            } != {_scope_label_key(detail) for detail in embedded_matches}
            item["scope"] = {
                **scope,
                "ncs_details": embedded_matches,
                "status": "single_detail" if len(embedded_matches) == 1 else "multi_detail",
                "review_required": len(embedded_matches) != 1,
                "source": embedded_scope_source,
                "positional_ncs_details": positional_details,
                "positional_scope_conflict": scope_conflict and bool(positional_details),
            }
        else:
            contextual_matches: list[str] = []
            label_column = int((item.get("label_cell") or {}).get("column") or 0)
            for context_cell in item.get("row_context_cells") or []:
                if not isinstance(context_cell, dict):
                    continue
                if int(context_cell.get("column") or 0) >= label_column:
                    continue
                context_label = _clean_detail_candidate_text(
                    str(context_cell.get("text") or "")
                )
                context_key = _scope_label_key(context_label)
                for detail in detail_candidates:
                    if (
                        context_key
                        and _scope_label_key(detail) == context_key
                        and detail not in contextual_matches
                    ):
                        contextual_matches.append(detail)
            if contextual_matches:
                positional_details = list(scoped_details)
                scoped_details = contextual_matches
                item["scope"] = {
                    **scope,
                    "ncs_details": contextual_matches,
                    "status": "single_detail"
                    if len(contextual_matches) == 1
                    else "multi_detail",
                    "review_required": len(contextual_matches) != 1,
                    "source": "coordinate_row_exact_detail_context",
                    "positional_ncs_details": positional_details,
                    "positional_scope_conflict": bool(positional_details),
                }
        if len(scoped_details) != 1:
            continue
        detail_key = scoped_details[0]
        bucket = ability_units_by_detail.setdefault(detail_key, [])
        bucket_keys = ability_unit_keys_by_detail.setdefault(detail_key, set())
        if value and value_key not in bucket_keys:
            bucket.append(value)
            bucket_keys.add(value_key)
    ability_unit_names: list[str] = []
    ability_name_keys: set[str] = set()
    for value in [
        *positioned_ability_units,
        *(str(item.get("text") or "") for item in sections["ability_units"]),
    ]:
        value = str(value or "").strip()
        value_key = _norm(value)
        if not value or not value_key or value_key in ability_name_keys:
            continue
        ability_name_keys.add(value_key)
        ability_unit_names.append(value)
    for item in sections["ability_units"]:
        value = str(item.get("text") or "").strip()
        value_key = _norm(value)
        embedded_matches, _embedded_scope_source = _exact_detail_hint_matches(
            item.get("embedded_ncs_detail"),
            detail_candidates,
        )
        if len(embedded_matches) != 1:
            continue
        detail_key = embedded_matches[0]
        bucket = ability_units_by_detail.setdefault(detail_key, [])
        bucket_keys = ability_unit_keys_by_detail.setdefault(detail_key, set())
        if value and value_key not in bucket_keys:
            bucket.append(value)
            bucket_keys.add(value_key)
    return {
        "filename": filename,
        **provenance,
        "review_required": True,
        "sections": sections,
        "fields": {
            "duties": [item["text"] for item in sections["duties"]],
            "qualifications": [item["text"] for item in sections["qualifications"]],
            "preferences": [item["text"] for item in sections["preferences"]],
            "knowledge": [item["text"] for item in sections["knowledge"]],
            "skills": [item["text"] for item in sections["skills"]],
            "attitudes": [item["text"] for item in sections["attitudes"]],
            "basic_competencies": [item["text"] for item in sections["basic_competencies"]],
            "ability_units": ability_unit_names,
            "ability_units_by_detail": ability_units_by_detail,
            "ncs_detail_candidates": detail_candidates,
            "ncs_detail_source": detail_source,
            "ncs_detail_candidate_evidence": detail_candidate_evidence,
            "ncs_self_developed_detail_candidates": self_developed_details,
            "ncs_detail_absence_reason": "" if detail_candidates else str(absence_diagnostics.get("reason") or ""),
            "ncs_detail_absence_state": "" if detail_candidates else str(absence_diagnostics.get("state") or ""),
            "ncs_detail_absence_evidence": "" if detail_candidates else str(absence_diagnostics.get("evidence") or ""),
            "ncs_detail_absence_filtered_candidate_reason": ""
            if detail_candidates
            else str(absence_diagnostics.get("filtered_candidate_reason") or ""),
            "ncs_detail_absence_saw_ncs_table": bool(absence_diagnostics.get("saw_ncs_table")) if not detail_candidates else False,
            "ncs_detail_absence_saw_detail_header": bool(absence_diagnostics.get("saw_detail_header"))
            if not detail_candidates
            else False,
            "ncs_detail_absence_blank_or_dash_detail_cell": bool(absence_diagnostics.get("blank_or_dash_detail_cell"))
            if not detail_candidates
            else False,
            "ncs_detail_absence_declared_no_mapping": bool(absence_diagnostics.get("declared_no_mapping"))
            if not detail_candidates
            else False,
            "positioned_items": positioned_items,
            "table_scopes": table_scopes,
            "table_coordinate_contract": {
                "index_base": 0,
                "coordinates": "logical_grid_after_rowspan_colspan_expansion",
                "origin_coordinates_preserved": True,
                "automatic_scope_requires_single_detail": True,
            },
        },
        "document": {
            "metadata": parsed.get("metadata") or {},
            "outline": parsed.get("outline") or [],
            "warnings": parsed.get("warnings") or [],
            "qualitySummary": parsed.get("qualitySummary"),
            "pageQuality": parsed.get("pageQuality") or [],
            "markdown": markdown,
        },
    }


_BULLET_CHARS = "•○●□■▪▫◦ㅇ⚪"
_FINAL_UNIT_ROWS_BY_CODE: dict[str, dict[str, Any]] | None = None
_ORIGINAL_EXTRACT_POSITIONED_TABLE_EVIDENCE = _base_extract_positioned_table_evidence


def _final_unit_rows_by_code() -> dict[str, dict[str, Any]]:
    global _FINAL_UNIT_ROWS_BY_CODE
    if _FINAL_UNIT_ROWS_BY_CODE is not None:
        return _FINAL_UNIT_ROWS_BY_CODE
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "ncs_unit_catalog.json"
    rows: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _FINAL_UNIT_ROWS_BY_CODE = rows
        return _FINAL_UNIT_ROWS_BY_CODE
    ambiguous_bases: set[str] = set()
    for row in payload.get("units") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if code:
            rows[code] = row
        base_code = str(row.get("base_code") or "").strip()
        if not re.fullmatch(r"\d{10}", base_code) or base_code in ambiguous_bases:
            continue
        existing = rows.get(base_code)
        if existing and str(existing.get("code") or "") != code:
            rows.pop(base_code, None)
            ambiguous_bases.add(base_code)
        else:
            rows[base_code] = row
    _FINAL_UNIT_ROWS_BY_CODE = rows
    return _FINAL_UNIT_ROWS_BY_CODE


def _final_clean_ability_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[\s\-*•○●□■▪▫◦ㅇ⚪]+", "", text)
    text = re.sub(r"[\s\-*•○●□■▪▫◦ㅇ⚪]+$", "", text)
    text = re.sub(r"(?<=[가-힣A-Za-z])[ㆍᆞ](?=[가-힣A-Za-z])", "", text)
    text = re.sub(r"\s+[·ㆍᆞ]$", "", text)
    text = re.sub(r"\s+등$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_numbered_item_by_safe_commas(
    text: str,
    *,
    ordinal: str,
) -> list[dict[str, str]]:
    parts = [_final_clean_ability_text(part) for part in _split_top_level_commas(text)]
    parts = [part for part in parts if part]
    if len(parts) <= 1:
        return [{"text": _final_clean_ability_text(text), "ordinal": ordinal}]
    if len(parts) > 4 or any(len(part) > 120 for part in parts):
        return [{"text": _final_clean_ability_text(text), "ordinal": ordinal}]
    official_keys = _official_ncs_unit_name_keys()
    first_is_exact = _norm(parts[0]) in official_keys
    all_exact = all(_norm(part) in official_keys for part in parts)
    qualified_tail = all("(" in part and ")" in part for part in parts[1:])
    if all_exact:
        return [{"text": part, "ordinal": ordinal} for part in parts]
    if first_is_exact and qualified_tail:
        return [{"text": parts[0], "ordinal": ordinal}] + [
            {"text": part, "ordinal": ""} for part in parts[1:]
        ]
    return [{"text": _final_clean_ability_text(text), "ordinal": ordinal}]


def _split_numbered_ability_units(value: str) -> list[dict[str, str]]:
    text = _final_clean_ability_text(str(value or "").strip(" ,;"))
    if not text:
        return []
    marker = re.compile(
        r"(?<!\d)(?P<number>\d{1,2})\s*(?:[.)]\s*|\s+(?=[A-Za-z가-힣]))"
    )
    matches = list(marker.finditer(text))
    if not matches:
        comma_parts = [_final_clean_ability_text(part) for part in _split_top_level_commas(text)]
        comma_parts = [part for part in comma_parts if part]
        if (
            len(comma_parts) > 1
            and len(text) <= 240
            and all(len(part) <= 80 for part in comma_parts)
        ):
            return [{"text": part, "ordinal": ""} for part in comma_parts]
        return [{"text": text, "ordinal": ""}]

    output: list[dict[str, str]] = []
    prefix = _final_clean_ability_text(text[: matches[0].start()].strip(" ,;"))
    if prefix and len(prefix) <= 80:
        output.append({"text": prefix, "ordinal": ""})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = _final_clean_ability_text(text[match.end() : end].strip(" ,;"))
        if not item:
            continue
        ordinal = str(match.group("number") or "").zfill(2)
        output.extend(_split_numbered_item_by_safe_commas(item, ordinal=ordinal))
    return output


def _legacy_split_ability_unit_entries_final_v1(value: str) -> list[dict[str, str]]:
    text = _clean_text(value, normalize_nfkc=False)
    if not text:
        return []
    text = re.sub(
        r"(?<!\d)(\d{10}(?:_[0-9A-Za-z]+)?\s*[.)])\s*\n+\s*(?=[A-Za-z가-힣])",
        r"\1 ",
        text,
    )
    if len(
        re.findall(
            r"(?<!\d)\d{1,2}\s*(?:[.)]\s*|\s+(?=[A-Za-z가-힣]))",
            text,
        )
    ) >= 2:
        text = re.sub(r"\s*\n+\s*", " ", text)
    bullet_class = re.escape(_BULLET_CHARS)
    chunks = re.split(
        rf"\n+|[;；]+|(?=\s*[{bullet_class}]\s*(?:\(|\d{{1,2}}\s*[.)]|[A-Za-z가-힣]))|(?=\s+\([^\n]{{1,80}}\)\s*\d{{1,2}}(?:[.)]|\s))",
        text,
    )
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    active_detail = ""
    for chunk in chunks:
        chunk = re.sub(rf"^(?:[-*{bullet_class}]+\s*)+", "", chunk).strip()
        if not chunk:
            continue
        detail_hint, body = _leading_parenthetical(chunk)
        if detail_hint:
            active_detail = detail_hint
            chunk = body
        for numbered_item in _split_numbered_ability_units(chunk):
            item = _final_clean_ability_text(
                _strip_full_ncs_code_prefix(str(numbered_item.get("text") or ""))
            )
            if not item or item in {"-", "?대떦?놁쓬", "?놁쓬", "誘멸컻諛?"}:
                continue
            key = (_norm(active_detail), _norm(item))
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "text": item,
                    "detail_hint": active_detail,
                    "ordinal": str(numbered_item.get("ordinal") or "").strip(),
                }
            )
    return output


def _recover_code_anchored_ability_records(
    parsed: dict[str, Any],
    markdown: str,
    *,
    valid_detail_candidates: list[str] | None = None,
) -> list[dict[str, Any]]:
    by_code = _final_unit_rows_by_code()
    valid_detail_keys = {
        _scope_label_key(detail)
        for detail in valid_detail_candidates or []
        if _scope_label_key(detail)
    }
    if not by_code or not valid_detail_keys:
        return []
    recovered: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    native_identities: set[tuple[str, str]] = set()
    training_label_keys = {_norm("필요교육"), _norm("직무교육과정")}
    for table_index, block in enumerate(_iter_kordoc_table_blocks(parsed)):
        rows = _block_table_rows(block)
        page = int(block.get("pageNumber", block.get("page", 0)) or 0)
        table_texts = [
            _clean_text(_block_text(cell), normalize_nfkc=False)
            for raw_row in rows
            for cell in (
                raw_row
                if isinstance(raw_row, (list, tuple))
                else raw_row.get("cells", [])
                if isinstance(raw_row, dict)
                else []
            )
        ]
        if not any(_norm(text) in training_label_keys for text in table_texts):
            continue
        for row_index, raw_row in enumerate(rows):
            cells = (
                raw_row
                if isinstance(raw_row, (list, tuple))
                else raw_row.get("cells", [])
                if isinstance(raw_row, dict)
                else []
            )
            texts = [
                _clean_text(_block_text(cell), normalize_nfkc=False)
                for cell in cells
            ]
            if not any(texts):
                continue
            for cell_index, text in enumerate(texts):
                code_match = re.search(
                    r"(?<!\d)(?P<base>\d{10})(?:(?:_)?\d{2}v\d+)?(?!\w)",
                    text,
                    flags=re.IGNORECASE,
                )
                if not code_match:
                    continue
                unit = by_code.get(code_match.group(0)) or by_code.get(
                    code_match.group("base")
                )
                if not unit:
                    continue
                unit_name = str(unit.get("name") or "").strip()
                detail_name = str(unit.get("detail_name") or "").strip()
                detail_key = _scope_label_key(detail_name)
                if detail_key not in valid_detail_keys:
                    continue
                candidate_indexes = list(
                    range(cell_index + 1, min(len(texts), cell_index + 3))
                ) or list(range(len(texts)))
                matched_name = ""
                matched_name_cell_index = -1
                for candidate_cell_index in candidate_indexes:
                    candidate = texts[candidate_cell_index]
                    candidate_name = _final_clean_ability_text(
                        re.sub(r"^\d{1,2}[.)]\s*", "", candidate).strip()
                    )
                    if _norm(candidate_name) == _norm(unit_name):
                        matched_name = candidate_name
                        matched_name_cell_index = candidate_cell_index
                        break
                if not matched_name or matched_name_cell_index < 0:
                    continue
                record_key = (page, table_index, str(unit.get("code") or ""))
                if record_key in seen:
                    continue
                seen.add(record_key)
                native_identities.add((detail_key, str(unit.get("code") or "")))
                recovered.append(
                    {
                        "section": "ability_units",
                        "text": unit_name,
                        "raw_cell_text": matched_name,
                        "source": "kordoc_code_anchored_training_recovery",
                        "coordinate_source": "kordoc_table",
                        "page": page,
                        "table_index": table_index,
                        "layout": "code_anchored_training_row",
                        "label": "직무교육과정",
                        "embedded_ncs_detail": detail_name,
                        "source_unit_code": code_match.group(0),
                        "resolved_unit_code": str(unit.get("code") or ""),
                        "ability_unit_ordinal": re.sub(r"^(\d{1,2}).*$", r"\1", matched_name)
                        if re.match(r"^\d{1,2}[.)]", matched_name)
                        else "",
                        "label_cell": {
                            "row": row_index,
                            "column": max(0, cell_index - 1),
                            "row_span": 1,
                            "column_span": 1,
                        },
                        "value_cell": {
                            "row": row_index,
                            "column": matched_name_cell_index,
                            "row_span": 1,
                            "column_span": 1,
                        },
                        "row_context_cells": [
                            {
                                "text": cell_text,
                                "column": idx,
                                "row_span": 1,
                                "column_span": 1,
                            }
                            for idx, cell_text in enumerate(texts)
                            if cell_text
                        ],
                        "scope": {
                            "job_fields": [],
                            "ncs_details": [detail_name] if detail_name else [],
                            "status": "single_detail" if detail_name else "unscoped",
                            "review_required": not bool(detail_name),
                            "source": "code_anchored_training_row",
                        },
                        "header_path": [detail_name, "직무교육과정"] if detail_name else ["직무교육과정"],
                    }
                )
    table_pattern = re.compile(r"(?is)<table\b[^>]*>(?P<body>.*?)</table>")
    row_pattern = re.compile(r"(?is)<tr\b[^>]*>(?P<body>.*?)</tr>")
    cell_pattern = re.compile(r"(?is)<t[dh]\b[^>]*>(?P<body>.*?)</t[dh]>")
    base_table_index = len(list(_iter_kordoc_table_blocks(parsed)))
    training_html_tables: list[str] = []
    for table_match in table_pattern.finditer(markdown or ""):
        table_cells = [
            _clean_text(html.unescape(match.group("body")), normalize_nfkc=False)
            for match in cell_pattern.finditer(table_match.group("body"))
        ]
        if any(_norm(value) in training_label_keys for value in table_cells):
            training_html_tables.append(table_match.group("body"))
    training_rows = [
        (base_table_index + table_offset, row_index, row_match)
        for table_offset, training_html in enumerate(training_html_tables)
        for row_index, row_match in enumerate(row_pattern.finditer(training_html))
    ]
    for table_index, row_index, row_match in training_rows:
        cells = [
            _clean_text(html.unescape(cell_match.group("body")), normalize_nfkc=False)
            for cell_match in cell_pattern.finditer(row_match.group("body"))
        ]
        if not any(cells):
            continue
        for cell_index, text in enumerate(cells):
            code_match = re.search(
                r"(?<!\d)(?P<base>\d{10})(?:(?:_)?\d{2}v\d+)?(?!\w)",
                text,
                flags=re.IGNORECASE,
            )
            if not code_match:
                continue
            unit = by_code.get(code_match.group(0)) or by_code.get(
                code_match.group("base")
            )
            if not unit:
                continue
            unit_name = str(unit.get("name") or "").strip()
            detail_name = str(unit.get("detail_name") or "").strip()
            detail_key = _scope_label_key(detail_name)
            if (
                detail_key not in valid_detail_keys
                or (detail_key, str(unit.get("code") or "")) in native_identities
            ):
                continue
            candidate_indexes = list(
                range(cell_index + 1, min(len(cells), cell_index + 3))
            ) or list(range(len(cells)))
            matched_name = ""
            matched_name_cell_index = -1
            for candidate_cell_index in candidate_indexes:
                candidate = cells[candidate_cell_index]
                candidate_name = _final_clean_ability_text(
                    re.sub(r"^\d{1,2}[.)]\s*", "", candidate).strip()
                )
                if _norm(candidate_name) == _norm(unit_name):
                    matched_name = candidate_name
                    matched_name_cell_index = candidate_cell_index
                    break
            if not matched_name or matched_name_cell_index < 0:
                continue
            record_key = (0, table_index, str(unit.get("code") or ""))
            if record_key in seen:
                continue
            seen.add(record_key)
            recovered.append(
                {
                    "section": "ability_units",
                    "text": unit_name,
                    "raw_cell_text": matched_name,
                    "source": "markdown_code_anchored_training_row",
                    "page": 0,
                    "table_index": table_index,
                    "layout": "markdown_code_anchored_training_row",
                    "label": "직무교육과정",
                    "embedded_ncs_detail": detail_name,
                    "source_unit_code": code_match.group(0),
                    "resolved_unit_code": str(unit.get("code") or ""),
                    "ability_unit_ordinal": re.sub(r"^(\d{1,2}).*$", r"\1", matched_name)
                    if re.match(r"^\d{1,2}[.)]", matched_name)
                    else "",
                    "label_cell": {
                        "row": row_index,
                        "column": max(0, cell_index - 1),
                        "row_span": 1,
                        "column_span": 1,
                    },
                    "value_cell": {
                        "row": row_index,
                        "column": matched_name_cell_index,
                        "row_span": 1,
                        "column_span": 1,
                    },
                    "row_context_cells": [
                        {
                            "text": cell_text,
                            "column": idx,
                            "row_span": 1,
                            "column_span": 1,
                        }
                        for idx, cell_text in enumerate(cells)
                        if cell_text
                    ],
                    "scope": {
                        "job_fields": [],
                        "ncs_details": [detail_name] if detail_name else [],
                        "status": "single_detail" if detail_name else "unscoped",
                        "review_required": not bool(detail_name),
                        "source": "markdown_code_anchored_training_row",
                    },
                    "header_path": [detail_name, "직무교육과정"] if detail_name else ["직무교육과정"],
                }
            )
    return recovered


def _extract_positioned_table_evidence(
    parsed: dict[str, Any],
    markdown: str,
    *,
    valid_detail_candidates: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positioned, table_scopes = _ORIGINAL_EXTRACT_POSITIONED_TABLE_EVIDENCE(
        parsed,
        markdown,
        valid_detail_candidates=valid_detail_candidates,
    )
    seen = {
        (
            str(item.get("section") or ""),
            _norm(item.get("text")),
            int(item.get("page") or 0),
            int(item.get("table_index") or 0),
        )
        for item in positioned
    }
    for item in _recover_code_anchored_ability_records(
        parsed,
        markdown,
        valid_detail_candidates=valid_detail_candidates,
    ):
        key = (
            str(item.get("section") or ""),
            _norm(item.get("text")),
            int(item.get("page") or 0),
            int(item.get("table_index") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        positioned.append(item)
    return positioned, table_scopes


def _split_ability_unit_entries(
    value: str,
    *,
    declared_details: list[str] | None = None,
) -> list[dict[str, str]]:
    """Split an ability-unit cell using only source-explicit structure.

    Newlines/bullets are item boundaries. A leading parenthetical is a detail
    scope only when it exactly matches a detail declared in the same table;
    otherwise it is retained as a subgroup prefix. A spaced dash separates a
    source heading from descriptive metadata, so only its left side is emitted.
    """

    text = _clean_text(value, normalize_nfkc=False)
    if not text:
        return []
    text = re.sub(
        r"(?<!\d)(\d{10}(?:_[0-9A-Za-z]+)?\s*[.)])\s*\n+\s*(?=[A-Za-z가-힣])",
        r"\1 ",
        text,
    )
    bullet_class = re.escape(_BULLET_CHARS)
    declared_by_key = {
        _scope_label_key(detail): str(detail or "").strip()
        for detail in declared_details or []
        if _scope_label_key(detail)
    }
    official_detail_keys = _official_ncs_detail_name_keys()

    # A lone internal bullet in prose is not an item boundary. Explicit
    # leading bullets and parenthetical detail switches remain structural.
    bullet_matches = list(re.finditer(rf"[{bullet_class}]", text))
    leading_bullet = bool(re.match(rf"^\s*[{bullet_class}]", text))
    explicit_switch = bool(
        re.search(rf"[{bullet_class}]\s*\([^\n()]{{1,80}}\)", text)
    )
    if len(bullet_matches) == 1 and not leading_bullet and not explicit_switch:
        detail_hint, body = _leading_parenthetical(text)
        item = _final_clean_ability_text(_strip_full_ncs_code_prefix(body))
        return (
            [{"text": item, "detail_hint": detail_hint, "ordinal": ""}]
            if item
            else []
        )
    if len(
        re.findall(
            r"(?<!\d)\d{1,2}\s*(?:[.)]\s*|\s+(?=[A-Za-z가-힣]))",
            text,
        )
    ) >= 2:
        text = re.sub(r"\s*\n+\s*", " ", text)
    generic_bullet_split = len(bullet_matches) >= 2 or leading_bullet
    bullet_pattern = (
        rf"(?=\s*[{bullet_class}]\s*(?:\(|\d{{1,2}}\s*[.)]|[A-Za-z가-힣]))"
        if generic_bullet_split
        else rf"(?=\s*[{bullet_class}]\s*\()"
    )
    chunks = re.split(
        rf"\n+|[;；]+|{bullet_pattern}|(?=\s+\([^\n]{{1,80}}\)\s*\d{{1,2}}(?:[.)]|\s))",
        text,
    )
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    active_detail = ""
    for chunk in chunks:
        chunk = re.sub(rf"^(?:[-*{bullet_class}]+\s*)+", "", chunk).strip()
        if not chunk:
            continue
        detail_hint, body = _leading_parenthetical(chunk)
        subgroup = ""
        if detail_hint:
            detail_key = _scope_label_key(detail_hint)
            base_detail_key = _scope_label_key(
                _legacy_strip_trailing_parenthetical_qualifier(detail_hint)
            )
            is_exact_detail_scope = bool(
                declared_by_key.get(detail_key)
                or declared_by_key.get(base_detail_key)
                or _norm(detail_hint) in official_detail_keys
                or _norm(_legacy_strip_trailing_parenthetical_qualifier(detail_hint))
                in official_detail_keys
            )
            if is_exact_detail_scope:
                active_detail = detail_hint
            elif declared_details is not None:
                subgroup = detail_hint
                active_detail = ""
            else:
                active_detail = detail_hint
            chunk = body

        dash_parts = re.split(r"\s+[–—]\s+", chunk, maxsplit=1)
        if len(dash_parts) == 2 and dash_parts[0].strip():
            numbered_items = [
                {"text": part, "ordinal": ""}
                for part in _split_top_level_commas(dash_parts[0])
            ]
        else:
            numbered_items = _split_numbered_ability_units(chunk)

        for numbered_item in numbered_items:
            item = _final_clean_ability_text(
                _strip_full_ncs_code_prefix(str(numbered_item.get("text") or ""))
            )
            if not item or item in {"-", "?대떦?놁쓬", "?놁쓬", "誘멸컻諛?"}:
                continue
            if subgroup:
                item = f"{subgroup}: {item}"
            key = (_norm(active_detail), _norm(item))
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "text": item,
                    "detail_hint": active_detail,
                    "ordinal": str(numbered_item.get("ordinal") or "").strip(),
                }
            )
    return output


def _looks_like_new_notice_section(line: str) -> bool:
    text = _clean_text(line)
    if not text:
        return False
    if text.startswith("#"):
        return True
    text = re.sub(r"^#{1,6}\s*", "", text).strip()
    if re.match(r"^(?:#{1,6}\s*)?(?:\d+[.)]|[가-힣][.)]|[IVX]+[.)])\s*\S{2,30}\s*$", text):
        return True
    key = _norm(text)
    headings = {
        "채용분야",
        "채용인원",
        "근무조건",
        "보수",
        "전형절차",
        "접수기간",
        "제출서류",
        "합격자발표",
        "임용",
        "기타사항",
        "문의처",
    }
    return key in {_norm(x) for x in headings}


def _extract_notice_windows(markdown: str, aliases: tuple[str, ...], max_lines: int = 9, max_chars: int = 2200) -> list[str]:
    lines = [_clean_text(line) for line in str(markdown or "").splitlines()]
    lines = [line for line in lines if line]
    out: list[str] = []
    seen: set[str] = set()
    alias_keys = [_norm(alias) for alias in aliases]
    for idx, line in enumerate(lines):
        line_key = _norm(line)
        if not line_key or not any(alias_key and alias_key in line_key for alias_key in alias_keys):
            continue
        window: list[str] = [line]
        for next_line in lines[idx + 1 : idx + max_lines]:
            if _looks_like_new_notice_section(next_line) and len(window) > 1:
                break
            window.append(next_line)
        value = "\n".join(window)
        value = value[:max_chars].strip()
        key = _norm(value)
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:4]


def _strip_notice_marker(line: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", _clean_text(line)).strip()
    text = re.sub(r"^(?:[-*•·‧○◦▪□■\uf000-\uf8ff]\s*)+", "", text).strip()
    text = re.sub(r"^(?:[가-힣]\.|\d+[.)]|[IVX]+[.)])\s*", "", text, flags=re.IGNORECASE).strip()
    return text


def _looks_like_interview_section_start(line: str) -> bool:
    text = _strip_notice_marker(line)
    key = _norm(text)
    if not key or "면접" not in key:
        return False
    if "면접전형시" in key or "면접시" in key:
        return False
    if "예정" in key and "평가" not in key and "심사" not in key and "기준" not in key:
        return False
    section_keys = (
        "면접전형",
        "면접시험",
        "면접심사",
        "면접평가",
        "면접평가기준",
        "면접평가항목",
    )
    return any(key.startswith(section_key) for section_key in section_keys)


def _extract_interview_notice_windows(markdown: str, max_chars: int = 1800) -> list[str]:
    lines = [_clean_text(line) for line in str(markdown or "").splitlines()]
    lines = [line for line in lines if line]
    out: list[str] = []
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        if not _looks_like_interview_section_start(line):
            continue
        window: list[str] = [line]
        for next_line in lines[idx + 1 :]:
            if _looks_like_new_notice_section(next_line) and len(window) > 1:
                break
            if _looks_like_interview_section_start(next_line) and len(window) > 1:
                break
            window.append(next_line)
            if len("\n".join(window)) >= max_chars:
                break
        value = "\n".join(window)[:max_chars].strip()
        key = _norm(value)
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:3]


def structure_job_notice(parsed: dict[str, Any], filename: str = "") -> dict[str, Any]:
    """Return reviewable duty/evaluation text candidates from a broader job notice.

    A notice usually does not contain a clean NCS classification table.  The goal
    is therefore not to auto-confirm anything, but to pre-fill the human review
    fields with the most relevant duty/evaluation windows.
    """

    markdown = str(parsed.get("markdown") or "")
    jd_like = structure_job_description(parsed, filename=filename)
    provenance = _parser_provenance(parsed)
    fields = jd_like.get("fields", {}) if isinstance(jd_like.get("fields"), dict) else {}

    duty_candidates = list(fields.get("duties") or []) + _extract_notice_windows(
        markdown, _NOTICE_REVIEW_ALIASES["duty_text"]
    )
    interview_candidates = _extract_interview_notice_windows(markdown)
    evaluation_candidates = interview_candidates or _extract_notice_windows(
        markdown, _NOTICE_REVIEW_ALIASES["evaluation_text"]
    )
    qualification_candidates = list(fields.get("qualifications") or []) + _extract_notice_windows(
        markdown, _NOTICE_REVIEW_ALIASES["qualification_text"]
    )
    preference_candidates = list(fields.get("preferences") or []) + _extract_notice_windows(
        markdown, _NOTICE_REVIEW_ALIASES["preference_text"]
    )

    def dedup_join(values: list[str], max_chars: int = 3000) -> str:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _clean_text(value)
            key = _norm(text)
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
        return "\n".join(out)[:max_chars].strip()

    return {
        "filename": filename,
        **provenance,
        "review_required": True,
        "fields": {
            "duty_text": dedup_join(duty_candidates),
            "evaluation_text": dedup_join(evaluation_candidates, max_chars=2200),
            "qualification_text": dedup_join(qualification_candidates, max_chars=1800),
            "preference_text": dedup_join(preference_candidates, max_chars=1800),
        },
        "candidates": {
            "duty_text": duty_candidates[:6],
            "evaluation_text": evaluation_candidates[:6],
            "qualification_text": qualification_candidates[:6],
            "preference_text": preference_candidates[:6],
        },
        "document": {
            "metadata": parsed.get("metadata") or {},
            "outline": parsed.get("outline") or [],
            "warnings": parsed.get("warnings") or [],
            "qualitySummary": parsed.get("qualitySummary"),
            "pageQuality": parsed.get("pageQuality") or [],
            "markdown": markdown,
        },
    }
