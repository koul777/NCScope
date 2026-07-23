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
        "주요직무",
        "주요 직무",
        "직무개요",
        "직무 개요",
        "담당 예정 업무",
        "담당예정업무",
        "채용직무",
        "채용 직무",
        "직무분야",
        "직무 분야",
        "주요 수행업무",
        "주요 담당업무",
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
        "공통자격",
        "공통 자격",
        "응시요건",
        "응시 요건",
        "필수요건",
        "필수 요건",
        "자격사항",
        "자격 사항",
        "자격조건",
        "자격 조건",
        "채용자격",
        "채용 자격",
    ),
    "preferences": (
        "우대사항",
        "우대조건",
        "가점사항",
        "우대요건",
        "우대 사항",
        "우대 조건",
        "가점 사항",
        "우대 요건",
        "우대",
        "가산점",
        "가점",
        "우대가점",
        "우대 가점",
        "우대자격",
        "우대 자격",
        "우대조건 및 가점",
        "우대 조건 및 가점",
        "우대요건 및 가점",
        "우대 요건 및 가점",
    ),
    "evaluation": (
        "면접평가",
        "면접 평가",
        "면접평가항목",
        "면접 평가항목",
        "면접평가기준",
        "면접 평가기준",
        "면접전형",
        "면접 전형",
        "면접항목",
        "면접 항목",
        "면접심사",
        "면접 심사",
        "평가요소",
        "평가 요소",
        "면접방법",
        "면접 방법",
        "면접내용",
        "면접 내용",
    ),
    "knowledge": ("필요지식", "지식"),
    "skills": ("필요기술", "기술"),
    "attitudes": ("직무수행태도", "수행태도", "태도"),
    "basic_competencies": ("직업기초능력", "기초능력"),
    "ncs_detail": ("세분류", "NCS세분류", "NCS 세분류"),
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


def _clean_evaluation_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |\t\r\n:：-•")


def _is_evaluation_header_item(value: str) -> bool:
    key = _norm(value)
    return key in {
        "항목",
        "비고",
        "항목비고",
        "전형사항평가기준",
        "전형사항",
        "평가기준",
        "구분",
        "전형방법",
        "전형구분",
        "평가방법",
        "평가방법배점",
        "배점",
    }


def _split_evaluation_items(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    cells = _split_table_row(raw)
    if not cells and "|" in raw:
        cells = [_clean_evaluation_text(part) for part in raw.strip("|").split("|")]
    if cells:
        cells = [_clean_evaluation_text(cell) for cell in cells if _clean_evaluation_text(cell)]
        if not cells or _is_separator_row(cells):
            return []
        if all(_is_evaluation_header_item(cell) for cell in cells):
            return []
        item = " | ".join(cell for cell in cells if not _is_evaluation_header_item(cell))
    else:
        item = _clean_evaluation_text(raw)
    item = re.sub(r"^(?:[-*•○●□■\xa1]|\d+[.)]|[가-힣][.)])\s*", "", item.strip())
    if not item or _is_evaluation_header_item(item):
        return []
    return [item]


def _section_for_label(label: str) -> str | None:
    key = _norm(label)
    if not key:
        return None
    for section, aliases in _SECTION_ALIASES.items():
        if key in {_norm(alias) for alias in aliases}:
            return section
    return None


_INTERVIEW_STAGE_PATTERNS = (
    "면접",
    "구술",
    "발표면접",
    "토론면접",
    "직무면접",
    "인성면접",
    "pt면접",
    "pt 면접",
)

_NON_INTERVIEW_STAGE_PATTERNS = (
    "서류전형",
    "서류 전형",
    "서류심사",
    "서류 심사",
    "필기전형",
    "필기 전형",
    "필기시험",
    "필기 시험",
    "인적성",
    "인성검사",
    "적성검사",
    "직업기초능력평가",
    "직업기초능력 평가",
    "논술",
)

_GENERIC_EVALUATION_LABELS = (
    "평가항목",
    "평가 항목",
    "평가기준",
    "평가 기준",
    "심사기준",
    "심사 기준",
    "심사방법",
    "심사 방법",
    "전형사항",
    "전형 사항",
)


def _stage_from_line(line: str) -> str | None:
    key = _norm(line)
    if not key:
        return None
    if any(_norm(pattern) in key for pattern in _INTERVIEW_STAGE_PATTERNS):
        return "interview"
    if any(_norm(pattern) in key for pattern in _NON_INTERVIEW_STAGE_PATTERNS):
        return "non_interview"
    return None


def _generic_evaluation_label(line: str) -> bool:
    key = _norm(line)
    return bool(key) and any(_norm(pattern) in key for pattern in _GENERIC_EVALUATION_LABELS)


def _looks_like_schedule_or_admin_line(line: str) -> bool:
    text = _clean_text(line)
    key = _norm(text)
    if not key:
        return False
    strong_admin_markers = (
        "전형일정",
        "원서접수",
        "접수기간",
        "접수방법",
        "제출서류",
        "제출 서류",
        "응시원서",
        "응시 원서",
        "입사지원서",
        "입사 지원서",
        "직무수행계획서",
        "직무 수행 계획서",
        "임용시기",
        "임용제청",
        "채용예정일",
        "채용 예정일",
        "합격자발표",
        "합격자 발표",
        "합격자",
        "불합격자",
        "개별통지",
        "개별 통지",
        "개별통보",
        "개별 통보",
        "문자개별통보",
        "문자 개별통보",
        "변경될수있",
        "변경될 수 있",
        "일반가점",
        "일반 가점",
        "가점사항",
        "가점 사항",
        "우대사항",
        "우대 사항",
        "취업지원대상자",
        "취업 지원 대상자",
        "장애인",
        "채용심의위원회",
        "채용점검위원회",
        "채용서류반환",
        "채용 서류 반환",
        "반환청구서",
        "반환 청구서",
        "첨부서류",
        "첨부 서류",
    )
    if any(_norm(marker) in key for marker in strong_admin_markers):
        return True
    if re.search(r"(?:20\d{2}\s*년|20\d{2}\s*[.\-/]\s*\d{1,2}|초순|중순|하순|예정)", text):
        interview_markers = ("면접심사", "면접평가", "직무역량면접", "인성면접", "실무면접")
        return not any(_norm(marker) in key for marker in interview_markers)
    return False


def _should_break_evaluation_section(line: str) -> bool:
    text = _clean_text(line)
    key = _norm(text)
    if not key:
        return False
    if _looks_like_schedule_or_admin_line(text):
        return True
    break_markers = (
        "첨부",
        "붙임",
        "별첨",
        "서식",
        "유의사항",
        "기타사항",
        "문의처",
        "공고문",
        "채용개요",
        "근로조건",
        "보수",
        "근무장소",
        "근무기간",
        "복무",
        "응시자격",
        "지원자격",
        "자격요건",
    )
    if any(_norm(marker) in key for marker in break_markers):
        return True
    if re.fullmatch(r"(?:\d+\s*)?부\.?", text) or text in {"끝", "끝."}:
        return True
    if re.fullmatch(r"\d+[.)]?\s*.+?\d+\s*부\.?(?:\s*끝\.?)?", text):
        return True
    return False


def _should_skip_evaluation_row_without_closing(line: str) -> bool:
    text = _clean_text(line)
    key = _norm(text)
    if not key:
        return False
    skip_markers = (
        "우대사항",
        "우대 사항",
        "가점",
        "가산",
        "취업지원대상자",
        "취업 지원 대상자",
        "장애인",
    )
    if not any(_norm(marker) in key for marker in skip_markers):
        return False
    stripped = text.lstrip()
    if stripped.startswith("#") or re.match(r"^\d+[.)]\s*", stripped):
        return False
    return True


def _looks_like_attachment_or_tail_heading(line: str) -> bool:
    text = _clean_text(line)
    key = _norm(text)
    if not key:
        return False
    if re.fullmatch(r"(?:#+\s*)?(?:붙임|첨부|별첨)\s*\d*", text):
        return True
    tail_markers = (
        "붙임",
        "첨부",
        "별첨",
        "서식",
        "채용서류반환청구서",
        "채용 서류 반환 청구서",
        "개인정보수집이용동의서",
        "개인정보 수집 이용 동의서",
    )
    return any(_norm(marker) in key for marker in tail_markers)


def _section_value_from_inline_label(line: str) -> tuple[str, str] | None:
    text = _clean_text(line)
    if not text:
        return None
    for section, aliases in _SECTION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            pattern = rf"^\s*{re.escape(alias)}\s*[:：\-–—]\s*(.+?)\s*$"
            match = re.match(pattern, text, flags=re.IGNORECASE)
            if match:
                value = _clean_text(match.group(1))
                if value:
                    return section, value
    return None


def _looks_like_detail_candidate(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    key = _norm(text)
    if not key or key in {"대분류", "중분류", "소분류", "세분류", "분류체계"}:
        return False
    if _section_for_label(text) and _section_for_label(text) != "ncs_detail":
        return False
    if len(text) > 40:
        return False
    return bool(re.search(r"[가-힣A-Za-z]", text))


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
                value = " ".join(cells[label_index + 1 :])
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
                value = " ".join(cells[label_index + 1 :])
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
        text = _clean_text(item)
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
    stage_context: str | None = None

    def add(section: str, text: str, block: dict[str, Any] | None = None, line: int = 0) -> None:
        items = _split_evaluation_items(text) if section == "evaluation" else _split_items(text)
        for item in items:
            if not item:
                continue
            if section == "evaluation" and stage_context == "non_interview":
                continue
            if section == "evaluation" and _stage_from_line(item) == "non_interview":
                continue
            if section == "evaluation" and _looks_like_schedule_or_admin_line(item):
                continue
            if any(_norm(existing.get("text")) == _norm(item) for existing in sections[section]):
                continue
            sections[section].append(_evidence(item, block=block, line=line))

    lines = markdown.splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if _looks_like_attachment_or_tail_heading(line):
            current = None
            stage_context = None
            continue
        line_stage = _stage_from_line(line)
        if line_stage:
            stage_context = line_stage
            if stage_context == "non_interview" and current == "evaluation":
                current = None
        if current == "evaluation" and _should_break_evaluation_section(line):
            if _should_skip_evaluation_row_without_closing(line):
                continue
            current = None
            continue
        cells = _split_table_row(line)
        if cells:
            if _is_separator_row(cells):
                continue
            label_index = next((i for i, cell in enumerate(cells) if _section_for_label(cell)), -1)
            if label_index >= 0:
                current = _section_for_label(cells[label_index])
                if current == "evaluation" and stage_context == "non_interview":
                    current = None
                    continue
                if current == "evaluation" and _should_break_evaluation_section(line):
                    current = None
                    continue
                if current and len(cells) > label_index + 1:
                    add(current, " ".join(cells[label_index + 1 :]), line=line_no)
                continue
        inline = _section_value_from_inline_label(line)
        if inline:
            current, value = inline
            if current == "evaluation" and stage_context == "non_interview":
                current = None
                continue
            if current == "evaluation" and _looks_like_schedule_or_admin_line(line):
                current = None
                continue
            add(current, value, line=line_no)
            continue
        heading_text = re.sub(r"^#{1,6}\s*", "", line)
        heading_text = re.sub(r"^(?:\d+[.)]|[가-힣][.)])\s*", "", heading_text)
        heading = _section_for_label(heading_text)
        if not heading and _generic_evaluation_label(heading_text) and (
            stage_context == "interview" or _stage_from_line(heading_text) == "interview"
        ):
            heading = "evaluation"
        if heading:
            current = heading
            if current == "evaluation" and stage_context == "non_interview":
                current = None
                continue
            if current == "evaluation" and _should_break_evaluation_section(line):
                if not (_generic_evaluation_label(heading_text) and _stage_from_line(line) == "interview"):
                    current = None
                    continue
            remainder = re.sub(
                r"^.*?(?:수행업무|직무수행내용|주요업무|담당업무|직무내용|수행내용|담당직무|"
                r"주요직무|직무개요|담당\s*예정\s*업무|채용직무|직무분야|주요\s*수행업무|주요\s*담당업무|"
                r"지원자격|자격요건|응시자격|필수자격|자격기준|지원요건|응시요건|공통자격|필수요건|자격사항|자격조건|채용자격|"
                r"우대사항|우대조건|가점사항|우대요건|우대|가산점|가점|우대가점|우대자격|"
                r"평가항목|평가기준|면접평가|면접평가항목|면접평가기준|면접전형|면접항목|면접심사|심사기준|평가요소|면접방법|면접내용|심사방법|"
                r"필요지식|필요기술|직무수행태도|수행태도|직업기초능력|세분류)\s*[:：-]?\s*",
                "",
                line,
            )
            if _norm(remainder) != _norm(line) and not (
                heading == "evaluation" and _should_break_evaluation_section(remainder)
            ):
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
                            row_text = " | ".join(value for value in values if value)
                            if section == "evaluation" and _should_break_evaluation_section(row_text):
                                continue
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
            "evaluation": [item["text"] for item in sections["evaluation"]],
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
