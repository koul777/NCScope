"""Kordoc document parsing and JD section extraction.

Kordoc is a Node package, while the application is FastAPI/Python.  The small
JSON bridge keeps the two runtimes independent and lets the review API expose
the original block/page evidence before NCS lookup is started.
"""

from __future__ import annotations

import base64
import csv
import html
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any


class KordocParseError(RuntimeError):
    """Raised when Kordoc cannot parse an uploaded document."""


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
    return re.sub(r"[\s:：·•\-_/()\[\]{}]+", "", text).lower()


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
    for part in parts:
        item = re.sub(r"^(?:[-*•○●□■\xa1]|\d+[.)]|[가-힣][.)])\s*", "", part.strip())
        if item and item not in output:
            output.append(item)
    return output


def _section_for_label(label: str) -> str | None:
    key = _norm(label)
    if not key:
        return None
    for section, aliases in _SECTION_ALIASES.items():
        if key in {_norm(alias) for alias in aliases}:
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

    return _norm(value) in {_norm("세"), _norm("세분")}


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
    non_values = {
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
    }
    if not key or key in {_norm(x) for x in non_values}:
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
    basic_competency_keys = (
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
    if sum(_norm(label) in key for label in basic_competency_keys) >= 2:
        return False
    if "미개발" in key:
        return False
    if _section_for_label(text) and _section_for_label(text) != "ncs_detail":
        return False
    compact = re.sub(r"\s+", "", text)
    if re.search(r"[○●□■※]", text):
        return False
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
        match = re.search(r"세분류\s*[:：]\s*(.*)$", line)
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

    if base_reason and not states:
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
    text = re.sub(r"^[,;/|]+", "", text)
    text = re.sub(r"[,;/|:：\\\-]+$", "", text)
    return _clean_text(text, normalize_nfkc=False)


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
    has_ncs_classification_markers = _has_any_norm(key, ("ncs", "세분류", "분류체계"))
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
                if _looks_like_detail_candidate(value):
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
            classification_context = classification_context or _row_contains_classification_marker(cells)
            if _row_declares_no_ncs_mapping(cells):
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
                    value = cells[cell_idx] if 0 <= cell_idx < len(cells) else ""
                    if _looks_like_detail_candidate(value):
                        candidates.extend(_split_items(value, normalize_nfkc=False))
                continue
            value = cells[detail_index] if detail_index < len(cells) else cells[-1]
            if _looks_like_detail_candidate(value):
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


def parse_with_kordoc(data: bytes, filename: str = "", ocr: bool = False) -> dict[str, Any]:
    if not data:
        raise KordocParseError("uploaded document is empty")
    node = shutil.which("node") or shutil.which("node.exe")
    script = Path(__file__).resolve().parents[2] / "scripts" / "kordoc_parse.mjs"
    if not node:
        raise KordocParseError("Node.js is required for Kordoc parsing")
    if not script.exists():
        raise KordocParseError(f"Kordoc bridge not found: {script}")

    payload = {
        "filename": filename,
        "dataBase64": base64.b64encode(data).decode("ascii"),
        "ocr": bool(ocr),
    }
    timeout_raw = os.getenv("KORDOC_TIMEOUT_SEC", "120")
    try:
        timeout = max(10, int(timeout_raw))
    except ValueError:
        timeout = 120
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
        raise KordocParseError(f"Kordoc process could not start: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-1200:]
        raise KordocParseError(detail or f"Kordoc exited with code {completed.returncode}")
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        result = _loads_kordoc_json(raw)
    except json.JSONDecodeError as exc:
        raise KordocParseError(f"Kordoc returned invalid JSON: {raw[-500:]}") from exc
    if not result.get("success", True):
        raise KordocParseError(str(result.get("error") or "Kordoc failed to parse the document"))
    return result


def structure_job_description(parsed: dict[str, Any], filename: str = "") -> dict[str, Any]:
    markdown = str(parsed.get("markdown") or "")
    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in _SECTION_ALIASES}
    current: str | None = None
    # The filename is useful evidence when a sparse/partially parsed document
    # still declares itself to be a 직무기술서. It must never create an NCS
    # candidate, but it lets the review UI distinguish "no explicit detail"
    # from an unclassified empty state.
    diagnostic_lines: list[str] = [value for value in (filename, markdown) if value]

    def add(section: str, text: str, block: dict[str, Any] | None = None, line: int = 0) -> None:
        for item in _split_items(text, normalize_nfkc=section != "ncs_detail"):
            if not item:
                continue
            if any(_norm(existing.get("text")) == _norm(item) for existing in sections[section]):
                continue
            sections[section].append(_evidence(item, block=block, line=line))

    lines = markdown.splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
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

    detail_candidates = _dedup_detail_candidates(
        [
            *_extract_ncs_detail_candidates(markdown),
            *(item["text"] for item in sections["ncs_detail"] if not item.get("line")),
        ]
    )
    detail_source = "explicit" if detail_candidates else ""
    if not detail_candidates:
        detail_candidates = _extract_contextual_ncs_detail_candidates(markdown)
        detail_source = "contextual" if detail_candidates else ""
    detail_candidate_evidence = _detail_candidate_evidence(detail_candidates, sections, markdown, detail_source)
    absence_diagnostics = {} if detail_candidates else _ncs_detail_absence_diagnostics("\n".join(diagnostic_lines))
    return {
        "filename": filename,
        "parser": "kordoc",
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
            "ncs_detail_candidates": detail_candidates,
            "ncs_detail_source": detail_source,
            "ncs_detail_candidate_evidence": detail_candidate_evidence,
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
        "parser": "kordoc",
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
