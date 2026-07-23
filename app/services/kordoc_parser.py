"""Kordoc document parsing and JD section extraction.

Kordoc is a Node package, while the application is FastAPI/Python.  The small
JSON bridge keeps the two runtimes independent and lets the review API expose
the original block/page evidence before NCS lookup is started.
"""

from __future__ import annotations

import base64
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
        "수행내용",
        "담당직무",
        "기관주요업무",
    ),
    "qualifications": (
        "지원자격",
        "자격요건",
        "응시자격",
        "필수자격",
        "자격기준",
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
    "skills": ("필요기술", "기술"),
    "attitudes": ("직무수행태도", "수행태도", "태도"),
    "basic_competencies": ("직업기초능력", "기초능력"),
    "ncs_detail": ("세분류", "NCS세분류", "NCS 세분류"),
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


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s:：·•\-_/()\[\]{}]+", "", text).lower()


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
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
    return [_clean_text(part) for part in raw.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r"[-: ]+", cell or "") for cell in cells)


def _split_items(text: str) -> list[str]:
    value = _clean_text(text)
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
    return None


def _looks_like_detail_candidate(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    key = _norm(text)
    non_values = {
        "대분류",
        "중분류",
        "소분류",
        "세분류",
        "분류체계",
        "ncs분류체계",
        "주요사업",
        "기관주요사업",
        "기관주요업무",
        "능력단위",
        "능력단위명",
        "능력단위코드",
        "직무수행내용",
        "필요지식",
        "필요기술",
        "직무수행태도",
        "관련자격",
    }
    if not key or key in {_norm(x) for x in non_values}:
        return False
    if _section_for_label(text) and _section_for_label(text) != "ncs_detail":
        return False
    if len(text) > 40:
        return False
    return bool(re.search(r"[가-힣A-Za-z]", text))


def _clean_detail_candidate_text(value: str) -> str:
    text = _clean_text(value)
    text = re.sub(r"\s*[\(（\[]\s*특화\s*분류\s*[\)）\]]\s*", "", text)
    text = re.sub(r"^[,;/|]+", "", text)
    text = re.sub(r"[,;/|:：\-]+$", "", text)
    return _clean_text(text)


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
                        candidates.extend(_split_items(value))
                else:
                    value = " ".join(value_cells)
                    value = re.sub(r"(?<!^)\s+(?=\d+\s*\.\s*)", "\n", value)
                    candidates.extend(_split_items(value))
                continue
            if pipe_detail_index is not None and not any(_section_for_label(cell) for cell in cells):
                value = cells[pipe_detail_index] if pipe_detail_index < len(cells) else cells[-1]
                if _looks_like_detail_candidate(value):
                    candidates.extend(_split_items(value))
                continue
        if "세분류" not in line:
            continue
        match = re.search(r"세분류\s*[:：]\s*(.+)$", line)
        if match:
            candidates.extend(_split_items(match.group(1)))
    # Kordoc may retain an HTML table in markdown when colspan/rowspan is
    # meaningful. Parse the label/value rows as a second, lossless path.
    for raw_table in re.findall(r"<table[^>]*>(.*?)</table>", markdown, flags=re.IGNORECASE | re.DOTALL):
        detail_index: int | None = None
        for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw_table, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", raw_row, flags=re.IGNORECASE | re.DOTALL)
            ]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell) == "ncs_detail"), -1)
            if label_index >= 0:
                detail_index = label_index
                value_cells = cells[label_index + 1 :]
                if len(value_cells) > 1:
                    for value in value_cells:
                        candidates.extend(_split_items(value))
                else:
                    value = " ".join(value_cells)
                    value = re.sub(r"(?<!^)\s+(?=\d+\s*\.\s*)", "\n", value)
                    candidates.extend(_split_items(value))
                continue
            if detail_index is None:
                continue
            if any(_section_for_label(cell) for cell in cells):
                break
            value = cells[detail_index] if detail_index < len(cells) else cells[-1]
            if _looks_like_detail_candidate(value):
                candidates.extend(_split_items(value))
    seen: set[str] = set()
    clean_candidates = []
    for item in candidates:
        text = _clean_detail_candidate_text(item)
        if not _looks_like_detail_candidate(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        clean_candidates.append(text)
    return clean_candidates


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

    def add(section: str, text: str, block: dict[str, Any] | None = None, line: int = 0) -> None:
        for item in _split_items(text):
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
                for row in rows:
                    row_cells = row if isinstance(row, list) else row.get("cells", []) if isinstance(row, dict) else []
                    values = [_clean_text(_block_text(cell)) for cell in row_cells]
                    label_index = next((i for i, cell in enumerate(values) if _section_for_label(cell)), -1)
                    if label_index >= 0:
                        section = _section_for_label(values[label_index])
                        if section:
                            add(section, " ".join(values[label_index + 1 :]), block=block)
        for child_key in ("children", "blocks", "rows", "cells"):
            children = block.get(child_key)
            if isinstance(children, list):
                for child in children:
                    visit(child)

    for block in parsed.get("blocks") or []:
        visit(block)

    detail_candidates = _extract_ncs_detail_candidates(markdown)
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
