from __future__ import annotations

import json
import os
import base64
import re
import time
import math
import csv
import hashlib
import sqlite3
import subprocess
import tempfile
import zlib
import uuid
import shutil
from urllib.parse import quote
from collections import Counter
from difflib import SequenceMatcher
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.services.external_api import fetch_ncs
from app.services.ncs import map_ncs
from app.services.openai_http import (
    check_openai_connectivity_with_retries,
    post_chat_completions_with_retries,
)
from app.services.question_generation import _generate_questions_with_openai_from_ncs
from app.services.ncs_mcp_client import NcsMcpError, get_ksa_by_units
from app.settings import settings


MOJIBAKE_ALIAS: dict[str, str] = {
    "珥앸Т": "총무",
    "珥앸Т?": "총무",
    "?먯궛愿由?": "자산관리",
    "?먯궛愿由": "자산관리",
    "?먯궛": "자산",
    "?뚭퀎쨌媛먯궗": "회계감사",
    "?뚭퀎": "회계",
    "?щТ?됱젙": "사무행정",
    "?щТ": "사무",
    "遺꾨쪟泥닿퀎": "분류체계",
    "?몃텇瑜?": "세분류",
    "?뚮텇瑜?": "소분류",
    "吏곷Т?섑뻾": "직무수행",
    "?λ젰?⑥쐞": "능력단위",
    "?꾩슂吏??": "필요지식",
    "?꾩슂湲곗닠": "필요기술",
    "臾몄꽌": "문서",
    "?됱젙": "행정",
}


def _count_hangul(text: str) -> int:
    return sum(1 for c in text if "\uac00" <= c <= "\ud7a3")


def _safe_tmp_root() -> str:
    root = os.path.join(os.getcwd(), ".tmp")
    os.makedirs(root, exist_ok=True)
    return root


def _safe_tmp_dir() -> str:
    path = os.path.join(_safe_tmp_root(), f"run_{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=True)
    return path


def _repair_mojibake(text: str) -> str:
    """Try to recover UTF-8 text that was decoded as latin-1/cp1252."""
    if not text:
        return text
    candidates = [text]
    for enc in ("latin-1", "cp1252"):
        try:
            repaired = text.encode(enc, errors="ignore").decode("utf-8", errors="ignore")
            if repaired:
                candidates.append(repaired)
        except Exception:
            pass
    best = max(candidates, key=_count_hangul)
    for broken, fixed in MOJIBAKE_ALIAS.items():
        best = best.replace(broken, fixed)
    return best


def extract_pdf_text(file_bytes: bytes) -> str:
    # 1) Preferred extractor: Python313 + pdfminer.
    py313 = r"C:\Python313\python.exe"
    if os.path.exists(py313):
        helper = (
            "from pdfminer.high_level import extract_text\n"
            "import sys\n"
            "t = extract_text(sys.argv[1]) or ''\n"
            "sys.stdout.buffer.write(t.encode('utf-8', 'ignore'))\n"
        )
        try:
            td = _safe_tmp_dir()
            try:
                pdf_path = os.path.join(td, "in.pdf")
                script_path = os.path.join(td, "extract_pdfminer.py")
                with open(pdf_path, "wb") as f:
                    f.write(file_bytes)
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(helper)
                p = subprocess.run(
                    [py313, script_path, pdf_path],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=40,
                    check=False,
                )
                if p.returncode == 0:
                    text = _repair_mojibake((p.stdout or "").strip())
                    # Even if Korean glyph mapping is partially broken, this output is
                    # usually far better than raw binary stream fallback.
                    if len(text) >= 120:
                        return text
            finally:
                shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass

    # 2) Best-effort standard-library fallback.
    content = file_bytes
    chunks: list[str] = []
    for match in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", content, flags=re.S):
        raw = match.group(1)
        stream_data = raw
        for _ in range(2):
            try:
                stream_data = zlib.decompress(stream_data)
                break
            except Exception:
                pass
        text = stream_data.decode("latin-1", errors="ignore")
        for token in re.findall(r"\(([^()]*)\)\s*T[Jj]", text):
            token = token.replace(r"\n", " ").replace(r"\r", " ").replace(r"\t", " ")
            token = token.replace(r"\(", "(").replace(r"\)", ")")
            if token.strip():
                chunks.append(token.strip())
    merged = _repair_mojibake("\n".join(chunks).strip())
    if merged:
        return merged
    # If stream parsing yields nothing, try offline OCR fallback (Windows OCR).
    if os.getenv("ENABLE_WINDOWS_OCR", "true").strip().lower() in {"1", "true", "yes", "y"}:
        try:
            ocr_pages = int(str(os.getenv("WINDOWS_OCR_MAX_PAGES", "2")).strip())
        except Exception:
            ocr_pages = 2
        ocr_text = _extract_pdf_text_via_windows_ocr(file_bytes=file_bytes, max_pages=max(1, min(3, ocr_pages)))
        if len(str(ocr_text or "").strip()) >= 10:
            return _repair_mojibake(ocr_text)

    # No readable text (image-only or unsupported encoding).
    return ""


def _parse_items(content_type: str, body: str) -> list[dict[str, Any]]:
    """
    Parse items from NCS API response (JSON or XML).

    Handles both formats:
    - JSON: response.body.items.item (single dict or list)
    - XML: <item> elements within <response>
    """
    if not body:
        return []

    # Determine format from content-type
    is_json = "json" in content_type.lower()

    if is_json:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []

        # Navigate JSON structure: response.body.items.item
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item")

        if items is None:
            return []

        # Convert single dict to list
        if isinstance(items, dict):
            return [items]
        elif isinstance(items, list):
            return items
        else:
            return []
    else:
        # XML parsing
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []

        # Extract all <item> elements
        items = []
        for item_elem in root.findall(".//item"):
            item_dict: dict[str, Any] = {}
            for child in item_elem:
                tag = child.tag
                text = (child.text or "").strip()
                item_dict[tag] = text
            if item_dict:
                items.append(item_dict)

        return items


def _render_pdf_pages_png_py313(file_bytes: bytes, max_pages: int = 2) -> list[bytes]:
    """PDF 페이지를 PNG로 렌더링. 현재 환경의 fitz 우선, 없으면 Python313 서브프로세스로 폴백."""
    # 1) 현재 Python 환경에 fitz(PyMuPDF)가 있으면 직접 사용 (가장 빠름)
    try:
        import fitz  # type: ignore
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        out: list[bytes] = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            out.append(pix.tobytes("png"))
        doc.close()
        return out
    except ImportError:
        pass
    except Exception:
        # Current interpreter may not have working fitz; fall through to py313.
        pass

    # 2) fitz 없으면 Python313 서브프로세스로 폴백
    py313 = r"C:\Python313\python.exe"
    if not os.path.exists(py313):
        return []
    try:
        td = _safe_tmp_dir()
        try:
            pdf_path = os.path.join(td, "in.pdf")
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            with open(pdf_path, "wb") as f:
                f.write(file_bytes)
            script = os.path.join(td, "render_pdf.py")
            code = (
                "import fitz, os, sys\n"
                "pdf_path, out_dir, max_pages = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
                "doc = fitz.open(pdf_path)\n"
                "for i, page in enumerate(doc):\n"
                "    if i >= max_pages:\n"
                "        break\n"
                "    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)\n"
                "    pix.save(os.path.join(out_dir, f'page_{i+1}.png'))\n"
            )
            with open(script, "w", encoding="utf-8") as f:
                f.write(code)
            p = subprocess.run(
                [py313, script, pdf_path, out_dir, str(max_pages)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=45,
                check=False,
            )
            if p.returncode != 0:
                return []
            out2: list[bytes] = []
            for name in sorted(os.listdir(out_dir)):
                if not name.lower().endswith(".png"):
                    continue
                with open(os.path.join(out_dir, name), "rb") as f:
                    out2.append(f.read())
            return out2
        finally:
            shutil.rmtree(td, ignore_errors=True)
    except Exception:
        return []


def extract_focus_terms_from_pdf_vision(file_bytes: bytes, max_pages: int = 2) -> list[str]:
    """
    Use OpenAI vision to extract role keywords when PDF text layer is broken.
    Returns canonical Korean terms suitable for NCS matching.
    """
    api_key = settings.openai_key()
    if not api_key:
        return []
    images = _render_pdf_pages_png_py313(file_bytes=file_bytes, max_pages=max_pages)
    if not images:
        return []

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "업로드된 직무기술서 이미지에서 직무 분류와 능력단위를 읽고, "
                "NCS 매핑용 핵심 키워드만 JSON으로 추출하세요. "
                "반드시 한국어 명사 키워드만 반환하세요. "
                "예: 총무, 자산관리, 사무행정, 회계감사, 회계처리, 문서관리, 계약관리, 구매관리.\n"
                "형식: {\"focus_terms\":[\"...\"]}"
            ),
        }
    ]
    for img in images:
        data_url = "data:image/png;base64," + base64.b64encode(img).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    vision_model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "model": vision_model,
        "messages": [
            {"role": "system", "content": "너는 직무기술서 분석기다. JSON만 출력한다."},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        data = post_chat_completions_with_retries(
            payload=payload,
            api_key=api_key,
            timeout_sec=60.0,
        )
        obj = json.loads(data["choices"][0]["message"]["content"])
        terms = obj.get("focus_terms", [])
        if not isinstance(terms, list):
            return []
        clean = []
        seen = set()
        for t in terms:
            t = str(t).strip()
            if len(t) < 2:
                continue
            if t not in seen:
                seen.add(t)
                clean.append(t)
        return clean[:20]
    except Exception:
        return []


def _tokenize(text: str) -> list[str]:
    text = _repair_mojibake(text)
    words = re.findall(r"[\uac00-\ud7a3A-Za-z0-9]{2,}", text)
    stop = {
        "및",
        "관련",
        "업무",
        "직무",
        "공공기관",
        "수행",
        "경험",
        "기술",
        "기반",
        "활용",
        "가능",
        "이해",
        "등",
    }
    return [w.lower() for w in words if w.lower() not in stop]


def _extract_focus_terms(jd_text: str) -> list[str]:
    raw_text = jd_text
    jd_text = _repair_mojibake(jd_text)
    lines = [ln.strip() for ln in jd_text.splitlines() if ln.strip()]

    terms: list[str] = []
    focus_labels = ["세분류", "소분류", "능력단위", "직무수행 내용", "필요지식", "필요기술"]
    for ln in lines:
        if any(label in ln for label in focus_labels):
            terms.extend(re.findall(r"[\uac00-\ud7a3]{2,}", ln))

    strong_seeds = [
        "총무",
        "자산관리",
        "사무행정",
        "회계",
        "회계감사",
        "문서관리",
        "행정지원",
        "재무회계",
        "구매",
        "비품",
        "재물조사",
        "전표",
        "결산",
    ]
    low = jd_text.lower()
    for s in strong_seeds:
        if s.lower() in low:
            terms.append(s)

    # Handle broken-text PDFs by direct alias detection.
    for broken, fixed in MOJIBAKE_ALIAS.items():
        if broken in jd_text and len(fixed) >= 2:
            terms.append(fixed)

    # Pattern-level rescue for broken Korean glyph mappings from HWP-origin PDFs.
    rescue_rules = [
        (["珥앸Т"], "총무"),
        (["먯궛", "鍮꾪뭹", "援щℓ", "臾쇳뭹", "재물조사"], "자산관리"),
        (["됱젙", "행정", "?щТ?됱젙"], "사무행정"),
        (["뚭퀎", "회계", "?꾪몴", "결산"], "회계처리"),
        (["媛먯궗", "감사"], "회계감사"),
        (["臾몄꽌", "문서"], "문서관리"),
    ]
    combined = f"{raw_text}\n{jd_text}"
    for needles, fixed in rescue_rules:
        if any(n in combined for n in needles):
            terms.append(fixed)

    dedup: list[str] = []
    seen = set()
    for t in terms:
        t = t.strip()
        if len(t) < 2:
            continue
        if t not in seen:
            dedup.append(t)
            seen.add(t)
    return dedup[:25]


def _dedup_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for v in values:
        t = str(v or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _compact_line(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _sclass_norm_key(v: str) -> str:
    """Normalize sclass-like labels to a strict matching key."""
    n = _norm_text(v or "")
    if not n:
        return ""
    # absorb spacing/punctuation variants: "정보기술 운영" == "정보기술운영"
    return re.sub(r"[·‧･ㆍ•∙⋅\-\_/|(),.\[\]{}]", "", n)


def _collect_classification_lines(jd_text: str, max_lines: int = 90) -> list[str]:
    lines = [ln.strip() for ln in (jd_text or "").splitlines() if ln.strip()]
    if not lines:
        return []

    header_terms = ("분류체계", "대분류", "중분류", "소분류", "세분류")
    stop_terms = (
        "직무수행",
        "능력단위",
        "필요지식",
        "필요기술",
        "담당업무",
    )

    header_idx = [i for i, ln in enumerate(lines) if any(t in _compact_line(ln) for t in header_terms)]
    start = max(0, min(header_idx) - 1) if header_idx else 0
    # Some PDFs place "기관/주요사업" above 분류 헤더. Do not early-stop before header block ends.
    header_floor = max(header_idx) if header_idx else start
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if i <= header_floor:
            continue
        if any(t in _compact_line(lines[i]) for t in stop_terms):
            end = i
            break
    return lines[start : min(end, start + max_lines)]


_CODE_NAME_PAIR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[.)]\s*([^\d]+?)(?=(?<!\d)\d{1,2}\s*[.)]|$)")


def _clean_category_value(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ,:;|-")
    return t


def _infer_column_major_counts(total: int, levels: int = 4) -> list[int] | None:
    if total <= 0 or levels <= 0:
        return None
    if levels != 4:
        return None
    if total < 3:
        return None
    if total <= 4:
        # [대,중,소,세]가 1개씩 혹은 일부만 보이는 단순 케이스
        return [1, 1, 1, max(1, total - 3)]

    best: list[int] | None = None
    best_cost = 10**9
    target = [1, 2, 3, 4]
    for c1 in range(1, total - 2):
        for c2 in range(c1, total - c1 - 1):
            for c3 in range(c2, total - c1 - c2):
                c4 = total - (c1 + c2 + c3)
                if c4 < c3 or c4 < 1:
                    continue
                cand = [c1, c2, c3, c4]
                cost = sum((cand[i] - target[i]) ** 2 for i in range(4))
                if cost < best_cost:
                    best_cost = cost
                    best = cand
    return best


def _extract_sclass_by_header_position(lines: list[str]) -> list[str]:
    """소분류 추출 - 두 가지 레이아웃 처리.

    1. 가로형: 소분류 라벨 오른쪽에 값 존재
       예) "소분류  총무" / "소분류: 총무, 일반사무"

    2. 세로형(헤더 블록): 대분류/중분류/소분류/세분류가 각 줄에 하나씩 연속으로 나열되고
       이후 값들이 따라오는 구조. 특히 첫 데이터 줄에 2쌍(중분류+소분류)이 오는 경우 처리.
       예) [대분류] [중분류] [소분류] [세분류] ... [06. 산업안전 01. 산업안전관리] [01. 기계 02. 전기 ...]
    """
    HEADERS = ["대분류", "중분류", "소분류", "세분류"]
    blocked = set(HEADERS) | {"분류체계"}

    # --- 가로형: 소분류 라벨 오른쪽에 값 ---
    for ln in lines:
        compact = re.sub(r"\s+", " ", ln).strip()
        m = re.match(r"소\s*분\s*류\s*[：:　\s]+(.+)", compact)
        if m:
            rest = m.group(1).strip()
            names = [_clean_category_value(n) for _, n in _CODE_NAME_PAIR_RE.findall(rest)]
            if not names:
                names = [_clean_category_value(p) for p in re.split(r"[,/|·]", rest)]
            names = [n for n in names if n and n not in blocked]
            if names:
                return _dedup_keep_order(names)

    # --- 세로형: 4개 헤더가 연속으로 있는 블록 감지 ---
    header_seq: list[int] = []  # line indices of 대중소세 in order
    for i, ln in enumerate(lines):
        compact = re.sub(r"\s+", "", ln)
        if compact in HEADERS:
            expected_idx = len(header_seq)
            if compact == HEADERS[expected_idx]:
                header_seq.append(i)
                if len(header_seq) == 4:
                    break
            else:
                header_seq = []  # 순서 깨지면 리셋

    if len(header_seq) < 3:  # 소분류까지만 있어도 처리
        return []

    block_end = header_seq[-1]
    value_lines = lines[block_end + 1:]
    if not value_lines:
        return []

    # 값 라인에서 첫 번째 코드-이름 쌍 라인 탐색
    for vln in value_lines:
        pairs = [_clean_category_value(n) for _, n in _CODE_NAME_PAIR_RE.findall(vln)]
        pairs = [p for p in pairs if p and p not in blocked and not p.isdigit()]
        if not pairs:
            continue

        n = len(pairs)
        if n == 2:
            # 중분류 + 소분류가 한 줄에 → 두 번째가 소분류
            return [pairs[1]] if pairs[1] else []
        elif n >= 4:
            # 대/중/소/세 한 줄에 → 세 번째가 소분류 (row-major는 기존 로직에 맡김)
            return []
        elif n == 1:
            # 열 단위/혼합 레이아웃에서는 다음 라인에 소분류가 이어질 수 있다.
            continue
        elif n == 3:
            # 중/소/세 또는 대/중/소 한 줄 → 두 번째가 소분류일 가능성
            return [pairs[1]] if pairs[1] else []

    return []


def _extract_small_categories_by_code_pairs(lines: list[str]) -> list[str]:
    if not lines:
        return []

    pair_rows: list[list[str]] = []
    flat_names: list[str] = []
    max_pairs_in_line = 0

    for ln in lines:
        pairs = [_clean_category_value(name) for _, name in _CODE_NAME_PAIR_RE.findall(ln)]
        pairs = [p for p in pairs if p and not p.isdigit()]
        if not pairs:
            continue
        max_pairs_in_line = max(max_pairs_in_line, len(pairs))
        pair_rows.append(pairs)
        flat_names.extend(pairs)

    if not flat_names:
        return []

    out: list[str] = []
    # 1) 열(컬럼) 단위로 텍스트가 쏟아지는 문서: 각 라인에 코드-값 1개
    if max_pairs_in_line == 1:
        counts = _infer_column_major_counts(total=len(flat_names), levels=4)
        if counts:
            start = counts[0] + counts[1]
            length = counts[2]
            out.extend(flat_names[start : start + length])
        elif len(flat_names) >= 3:
            out.append(flat_names[2])
        # 1-b) 일부 PDF는 [대,중,소,세,소,세,...] 순으로 압축되어 추출된다.
        #      이 경우 소분류는 index 2부터 2칸 간격으로 등장한다.
        interleaved = [flat_names[i] for i in range(2, len(flat_names), 2)]
        if interleaved:
            alias_index = _build_sclass_exact_alias_index()
            if alias_index:
                interleaved_valid: list[str] = []
                for v in interleaved:
                    key = _sclass_norm_key(v)
                    if key and key in alias_index:
                        interleaved_valid.append(v)
                # 기존 결과보다 유효 소분류가 더 많으면 interleaved 결과를 우선.
                if len(_dedup_keep_order(interleaved_valid)) > len(_dedup_keep_order(out)):
                    out = interleaved_valid
    else:
        # 2) 행 단위 표 문서: 한 줄에 대/중/소/세가 동시에 존재하거나 줄바꿈으로 일부 분리
        row_acc: list[str] = []
        for row in pair_rows:
            row_acc.extend(row)
            if len(row_acc) >= 3:
                out.append(row_acc[2])
            if len(row_acc) >= 4:
                row_acc = []

    cleaned = []
    blocked = {"대분류", "중분류", "소분류", "세분류", "분류체계"}
    for c in out:
        v = _clean_category_value(c)
        if not v or v in blocked:
            continue
        cleaned.append(v)
    return _dedup_keep_order(cleaned)


def _decide_sclass_anchor_scan_mode(lines: list[str], small_idx: int) -> str:
    """Decide scan direction around 소분류 anchor.

    Rule requested by user:
    - if headers look horizontal around 소분류 -> scan downward
    - if headers look vertical (중분류 above, 세분류 below close) -> scan rightward
    """
    compact = [_compact_line(ln) for ln in lines]
    mids = [i for i, ln in enumerate(compact) if "중분류" in ln]
    details = [i for i, ln in enumerate(compact) if "세분류" in ln]

    near_mid = min(mids, key=lambda x: abs(x - small_idx)) if mids else None
    near_detail = min(details, key=lambda x: abs(x - small_idx)) if details else None

    if near_mid is not None and near_detail is not None:
        up_dist = small_idx - near_mid
        down_dist = near_detail - small_idx
        # Vertical stack around 소분류 (중분류/세분류 above/below) -> scan right.
        if up_dist > 0 and down_dist > 0 and up_dist <= 3 and down_dist <= 3:
            return "right"
    # Default: horizontal header table -> scan downward by rows.
    return "down"


def _extract_anchor_line_terms(line: str) -> list[str]:
    line = str(line or "").strip()
    if not line:
        return []
    pairs = [_clean_category_value(n) for _, n in _CODE_NAME_PAIR_RE.findall(line)]
    pairs = [p for p in pairs if p and not p.isdigit()]
    if pairs:
        return pairs
    # Handle plain forms like "01 법무" (without dot/paren).
    cleaned_src = re.sub(r"^[•·▪◦\-\*]+\s*", "", line)
    cleaned_src = re.sub(r"^\d{1,2}\s*[.)]?\s+", "", cleaned_src).strip()
    cleaned = _clean_category_value(cleaned_src)
    if not cleaned:
        return []
    return [cleaned]


def _build_sclass_exact_alias_index(cache_ttl_sec: int = 60 * 30) -> dict[str, dict[str, Any]]:
    cache_key = "_sclass_exact_alias_index_cache"
    now = time.time()
    cached = globals().get(cache_key)
    if isinstance(cached, dict) and cached.get("items"):
        if (now - float(cached.get("ts", 0.0))) < cache_ttl_sec:
            return dict(cached.get("items", {}))

    catalog = load_sclass_catalog_from_csv()
    if not catalog:
        return {}
    synonym_pack = load_sclass_synonym_dictionary()
    synonym_by_code = synonym_pack.get("by_code_no", {})
    synonym_by_name = synonym_pack.get("by_name", {})

    index: dict[str, dict[str, Any]] = {}
    for row in catalog:
        code_no = str(row.get("ncs_code_no", "")).strip()
        name = str(row.get("ncs_sclass_name", "")).strip()
        if not (code_no and name):
            continue
        official_key = _sclass_norm_key(name)
        if official_key:
            index[official_key] = {"row": row, "official": True}
        aliases = _build_sclass_aliases(
            sclass_name=name,
            code_no=code_no,
            synonym_by_code=synonym_by_code,
            synonym_by_name=synonym_by_name,
        )
        for alias in aliases:
            k = _sclass_norm_key(alias)
            if not k:
                continue
            if k == official_key:
                continue
            # Keep first alias mapping; official key always overrides.
            if k not in index:
                index[k] = {"row": row, "official": False}

    globals()[cache_key] = {"ts": now, "items": index}
    return dict(index)


def _build_mclass_to_sclass_keys_index(cache_ttl_sec: int = 60 * 30) -> dict[str, set[str]]:
    """Build normalized middle-class -> small-class key index from local catalog."""
    cache_key = "_mclass_to_sclass_keys_index_cache"
    now = time.time()
    cached = globals().get(cache_key)
    if isinstance(cached, dict) and cached.get("items"):
        if (now - float(cached.get("ts", 0.0))) < cache_ttl_sec:
            cached_items = cached.get("items", {})
            if isinstance(cached_items, dict):
                return {str(k): set(v or set()) for k, v in cached_items.items()}

    catalog = load_sclass_catalog_from_csv()
    out: dict[str, set[str]] = {}
    for row in catalog:
        m_name = str(row.get("ncs_mclass_name", "")).strip()
        s_name = str(row.get("ncs_sclass_name", "")).strip()
        m_key = _sclass_norm_key(m_name)
        s_key = _sclass_norm_key(s_name)
        if not (m_key and s_key):
            continue
        out.setdefault(m_key, set()).add(s_key)

    globals()[cache_key] = {"ts": now, "items": out}
    return {str(k): set(v or set()) for k, v in out.items()}


def _extract_small_categories_by_vertical_blocks(
    lines: list[str],
    max_items: int = 15,
) -> list[str]:
    """Extract small categories for vertical-broken tables around 소분류.

    Pattern:
    - 중분류/소분류/세분류 headers are stacked vertically
    - values are read as sequential lines where 중분류 block and 소분류 rows are mixed

    Strategy:
    - split rows into middle-class blocks
    - within each block, keep only increasing code sequence (01 -> 02 -> 03 ...)
      as 소분류 candidates, and stop at reset (detail section starts)
    """
    if not lines:
        return []

    compact = [_compact_line(ln) for ln in lines]
    anchor_idxs = [i for i, ln in enumerate(compact) if "소분류" in ln]
    if not anchor_idxs:
        return []

    alias_index = _build_sclass_exact_alias_index()
    if not alias_index:
        return []
    mclass_index = _build_mclass_to_sclass_keys_index()

    mids = [i for i, ln in enumerate(compact) if "중분류" in ln]
    details = [i for i, ln in enumerate(compact) if "세분류" in ln]
    if not mids or not details:
        return []

    stop_terms = (
        "직무수행",
        "능력단위",
        "필요지식",
        "필요기술",
        "전형방법",
        "일반요건",
        "교육요건",
        "기타요건",
        "직무수행내용",
        "내용",
    )

    def _row_pairs(src: str) -> list[tuple[int, str]]:
        vals: list[tuple[int, str]] = []
        for code, name in _CODE_NAME_PAIR_RE.findall(src):
            cleaned = _clean_category_value(name)
            if not cleaned:
                continue
            try:
                num = int(str(code).strip())
            except Exception:
                continue
            vals.append((num, cleaned))
        return vals

    def _is_marker_row(pairs: list[tuple[int, str]]) -> tuple[bool, str, bool]:
        if not pairs:
            return False, "", False
        first_name = pairs[0][1]
        first_key = _sclass_norm_key(first_name)
        if first_key and first_key in mclass_index:
            return True, first_key, True
        if not first_key or first_key not in alias_index:
            return True, first_key, False
        return False, first_key, False

    out: list[str] = []
    seen: set[str] = set()

    for idx in anchor_idxs[:2]:
        near_mid = min(mids, key=lambda x: abs(x - idx))
        near_detail = min(details, key=lambda x: abs(x - idx))
        up_dist = idx - near_mid
        down_dist = near_detail - idx
        # Vertical header condition only.
        if not (up_dist > 0 and down_dist > 0 and up_dist <= 3 and down_dist <= 3):
            continue

        row_data: list[tuple[int, list[tuple[int, str]]]] = []
        for j in range(near_detail + 1, len(lines)):
            cj = compact[j]
            if any(t in cj for t in stop_terms):
                break
            pairs = _row_pairs(lines[j])
            if pairs:
                row_data.append((j, pairs))
        if not row_data:
            continue

        r = 0
        while r < len(row_data):
            _, pairs = row_data[r]
            marker, marker_key, marker_known = _is_marker_row(pairs)
            if not marker:
                r += 1
                continue

            allowed_keys = mclass_index.get(marker_key, set()) if marker_known else set()
            prev_num = 0
            block_terms: list[str] = []

            def _consume_pair(num: int, name: str) -> bool:
                nonlocal prev_num
                key = _sclass_norm_key(name)
                if not key or key not in alias_index:
                    return False
                if allowed_keys and key not in allowed_keys:
                    if "서무" not in _compact_line(name):
                        return False
                if prev_num and num <= prev_num:
                    return True
                prev_num = num
                disp = re.sub(r"[·‧･ㆍ•∙⋅]", "", _clean_category_value(name))
                if disp:
                    block_terms.append(disp)
                return False

            reset = False
            for num, name in pairs[1:]:
                reset = _consume_pair(num, name)
                if reset:
                    break

            r += 1
            while r < len(row_data) and not reset:
                _, next_pairs = row_data[r]
                next_marker, _, _ = _is_marker_row(next_pairs)
                if next_marker:
                    break
                for num, name in next_pairs:
                    reset = _consume_pair(num, name)
                    if reset:
                        break
                r += 1

            block_terms = _dedup_keep_order(block_terms)
            accepted = block_terms if marker_known else (block_terms if len(block_terms) >= 2 else [])
            for term in accepted:
                if term in seen:
                    continue
                seen.add(term)
                out.append(term)
                if len(out) >= max_items:
                    return out[:max_items]

    return out[:max_items]


def _extract_small_categories_by_anchor_direction(
    lines: list[str],
    down_scan_lines: int = 12,
    right_scan_lines: int = 8,
    max_items: int = 15,
) -> list[str]:
    """Anchor-based small-category extraction using scan direction heuristics."""
    if not lines:
        return []

    compact = [_compact_line(ln) for ln in lines]
    anchor_idxs = [i for i, ln in enumerate(compact) if "소분류" in ln]
    if not anchor_idxs:
        return []

    stop_down = (
        "세분류",
        "직무수행",
        "능력단위",
        "필요지식",
        "필요기술",
        "전형방법",
        "일반요건",
        "교육요건",
        "기타요건",
        "직무수행내용",
        "내용",
    )
    stop_right = (
        "중분류",
        "세분류",
        "직무수행",
        "능력단위",
        "필요지식",
        "필요기술",
        "전형방법",
        "일반요건",
        "교육요건",
        "기타요건",
    )
    header_only = ("대분류", "중분류", "소분류", "세분류", "분류체계", "채용분야", "구분")
    blocked = {"대분류", "중분류", "소분류", "세분류", "분류체계", "구분", "직무", "내용"}

    raw_terms: list[tuple[str, bool]] = []
    for idx in anchor_idxs[:2]:
        mode = _decide_sclass_anchor_scan_mode(lines, idx)
        max_scan = down_scan_lines if mode == "down" else right_scan_lines
        stop_terms = stop_down if mode == "down" else stop_right

        line = lines[idx]
        m = re.search(r"소\s*분\s*류\s*[：:\s]+\s*(.+)$", re.sub(r"\s+", " ", line).strip())
        if m:
            raw_terms.extend([(x, True) for x in _extract_anchor_line_terms(m.group(1))])

        scanned = 0
        for j in range(idx + 1, len(lines)):
            c = compact[j]
            if scanned == 0 and any(h in c for h in header_only):
                continue
            if any(t in c for t in stop_terms):
                # In vertical-header layouts, immediate stop token can appear before data rows.
                if mode == "right" and scanned == 0:
                    continue
                break
            line_terms = _extract_anchor_line_terms(lines[j])
            line_explicit_small = False
            # Row pattern in some PDFs:
            #   "01. 소분류  02. 세분류" (same physical row, collapsed into one line)
            # In this case keep the first pair for 소분류 scanning.
            pair_terms = [_clean_category_value(n) for _, n in _CODE_NAME_PAIR_RE.findall(lines[j])]
            pair_terms = [p for p in pair_terms if p and not p.isdigit()]
            if mode == "down" and len(pair_terms) == 2 and scanned <= 1:
                line_terms = [pair_terms[0]]
                line_explicit_small = True
            raw_terms.extend([(x, line_explicit_small) for x in line_terms])
            # Recover split code-name rows:
            # "02.인사∙" + "조직" -> "02.인사∙조직"
            # "03.일반" + "사무"   -> "03.일반사무"
            if j + 1 < len(lines):
                cur_comp = _compact_line(lines[j])
                nxt_comp = _compact_line(lines[j + 1])
                if (
                    re.match(r"^\d{1,2}[.)]?[가-힣A-Za-z·‧∙ㆍ･]+$", cur_comp)
                    and re.match(r"^[가-힣A-Za-z]{1,8}$", nxt_comp)
                ):
                    merged_line = f"{cur_comp}{nxt_comp}"
                    merged_terms = _extract_anchor_line_terms(merged_line)
                    merged_explicit_small = False
                    merged_pairs = [_clean_category_value(n) for _, n in _CODE_NAME_PAIR_RE.findall(merged_line)]
                    merged_pairs = [p for p in merged_pairs if p and not p.isdigit()]
                    if mode == "down" and len(merged_pairs) == 2 and scanned <= 1:
                        merged_terms = [merged_pairs[0]]
                        merged_explicit_small = True
                    raw_terms.extend([(x, merged_explicit_small) for x in merged_terms])
            scanned += 1
            if scanned >= max_scan:
                break

    alias_index = _build_sclass_exact_alias_index()
    if not alias_index:
        return []

    out: list[str] = []
    seen = set()
    extras_unmapped: list[str] = []
    for term, is_explicit_small in raw_terms:
        cleaned = _clean_category_value(term)
        if not cleaned or cleaned in blocked:
            continue
        key = _sclass_norm_key(cleaned)
        if not key:
            continue
        entry = alias_index.get(key)
        if not entry:
            # Keep only labels that came from explicit "소분류 슬롯" rows.
            if not is_explicit_small:
                continue
            disp = re.sub(r"[·‧･ㆍ•∙⋅]", "", cleaned)
            if len(_compact_line(disp)) >= 2 and disp not in blocked:
                extras_unmapped.append(disp)
            continue
        # Composite labels like "재무·회계" are often 중분류 labels in tables.
        # Do not map those through alias-only paths (prevents 회계 과매칭).
        if not bool(entry.get("official")) and bool(re.search(r"[·･ㆍ•∙⋅/|]", cleaned)):
            continue
        row = entry.get("row") or {}
        name = str(row.get("ncs_sclass_name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max_items:
            break
    merged = _dedup_keep_order(out + extras_unmapped)
    return merged[:max_items]


def extract_subcategory_text(jd_text: str) -> str:
    """
    Extract text around '소분류' (preferred) / '세분류' row from JD.
    Works with both normal and partially-broken glyph text.
    """
    src = _repair_mojibake(jd_text)
    lines = _collect_classification_lines(src, max_lines=70)
    if not lines:
        lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    if not lines:
        return ""

    # 소분류 후보를 상단에 붙여 후속 키워드 추론 품질을 높인다.
    smalls = _extract_small_categories_by_anchor_direction(lines, max_items=8)
    if not smalls:
        smalls = _extract_small_categories_by_code_pairs(lines)
    out = list(lines[:30])
    if smalls:
        out.insert(0, "소분류 후보: " + ", ".join(smalls))
    return "\n".join(out)[:1200]



def _load_ncs_small_categories() -> set[str]:
    """Load NCS small categories from CSV cache.

    Reads from ncs_sclass_codes_with_code_no.csv which contains all official
    NCS small category names (소분류). Uses caching for performance.
    """
    cache_key = "_ncs_small_categories_cache"
    if cache_key in globals():
        return globals()[cache_key]

    categories = set()
    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "ncs_sclass_codes_with_code_no.csv")

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row and "NCS_SCLAS_CDNM" in row:
                    sclass = row["NCS_SCLAS_CDNM"].strip()
                    if sclass:
                        categories.add(sclass)
    except Exception:
        # Fallback to hardcoded list if CSV not found
        pass

    # If empty, use fallback list
    if not categories:
        categories = {
            "총무", "자산관리", "사무행정", "회계감사", "회계처리",
            "문서관리", "계약관리", "구매관리", "물품관리", "재물조사",
            "비품관리", "행정지원", "일반사무", "예산관리", "세무회계",
            "인사관리", "기초회계", "경영", "경영기획", "사업계획",
            "간호", "임상병리", "방사선", "물리치료", "작업치료",
        }
    categories.update(
        {
            "교육",
            "정보처리",
            "건축",
            "자동차",
            "마케팅",
            "학사운영",
            "학교교육",
            "경비·경호",
        }
    )

    globals()[cache_key] = categories
    return categories


def lookup_ncs_codes_by_sclass(sclass_names: list[str]) -> list[dict]:
    """소분류 이름 목록으로 CSV에서 NCS 코드 정보를 직접 조회한다 (AI 불필요).

    Returns:
        list of {sclass_name, ncs_code_no, lclas_cd, lclas_nm, mclas_cd, mclas_nm, sclas_cd}
        매칭 안된 항목은 제외.
    """
    cache_key = "_ncs_sclass_rows_cache"
    if cache_key not in globals():
        rows: list[dict] = []
        csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "ncs_sclass_codes_with_code_no.csv")
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                with open(csv_path, "r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    rows = [row for row in reader if row]
                if rows:
                    break
            except Exception:
                rows = []
        globals()[cache_key] = rows

    all_rows: list[dict] = globals()[cache_key]
    # BOM 처리된 첫 번째 컬럼 키 정규화
    code_no_key = next((k for k in (all_rows[0].keys() if all_rows else []) if "NCS_CODE_NO" in k), "NCS_CODE_NO")

    # 소분류명 → 행 인덱스 (정규화 exact 우선, 없으면 정규화 포함 관계)
    results: list[dict] = []
    seen_query: set[str] = set()
    seen_codes: set[tuple[str, str, str, str]] = set()

    # Pre-normalize CSV rows for robust matching.
    prepared_rows: list[tuple[dict, str, str]] = []
    for r in all_rows:
        raw_nm = str(r.get("NCS_SCLAS_CDNM", "")).strip()
        if not raw_nm:
            continue
        prepared_rows.append((r, raw_nm, _sclass_norm_key(raw_nm)))

    synonym_pack = load_sclass_synonym_dictionary()
    synonym_by_code = synonym_pack.get("by_code_no", {})
    synonym_by_name = synonym_pack.get("by_name", {})

    alias_rows: dict[str, tuple[dict, str, str]] = {}
    for r, raw_nm, raw_norm in prepared_rows:
        code_no = str(r.get(code_no_key, "")).strip()
        aliases = _build_sclass_aliases(
            sclass_name=raw_nm,
            code_no=code_no,
            synonym_by_code=synonym_by_code,
            synonym_by_name=synonym_by_name,
        )
        aliases.add(raw_nm)
        for alias in aliases:
            ak = _sclass_norm_key(alias)
            if not ak:
                continue
            prev = alias_rows.get(ak)
            if prev is None or ak == raw_norm:
                alias_rows[ak] = (r, raw_nm, raw_norm)

    for name in sclass_names:
        name = str(name or "").strip()
        q_key = _sclass_norm_key(name)
        if not q_key or q_key in seen_query:
            continue
        seen_query.add(q_key)

        # 1) exact by normalized key (official name + alias dictionary)
        match_item = alias_rows.get(q_key)
        # 2) near-exact contain fallback with overlap guard (avoid 과매칭:
        #    "경영회계사무" -> "회계", "총무인사" -> "총무")
        if match_item is None:
            match_item = next(
                (
                    it for it in prepared_rows
                    if (
                        (q_key in it[2] or it[2] in q_key)
                        and min(len(q_key), len(it[2])) >= 4
                        and (min(len(q_key), len(it[2])) / max(len(q_key), len(it[2]))) >= 0.8
                    )
                ),
                None,
            )
        # 3) raw exact fallback
        if match_item is None:
            match_item = next((it for it in prepared_rows if it[1] == name), None)

        if match_item is None:
            continue

        match = match_item[0]
        canonical_name = match_item[1]
        code_tuple = (
            str(match.get(code_no_key, "")).strip(),
            str(match.get("NCS_LCLAS_CD", "")).strip(),
            str(match.get("NCS_MCLAS_CD", "")).strip(),
            str(match.get("NCS_SCLAS_CD", "")).strip(),
        )
        if code_tuple in seen_codes:
            continue
        seen_codes.add(code_tuple)

        exact_norm = (match_item[2] == q_key)
        results.append({
            # fetch_ncs_units_hrdk_by_verified_sclass 호환 스키마
            "sclass_name": canonical_name,
            "ncs_code_no": match.get(code_no_key, ""),
            "ncs_lclass_code": match.get("NCS_LCLAS_CD", ""),
            "ncs_lclass_name": match.get("NCS_LCLAS_CDNM", ""),
            "ncs_mclass_code": match.get("NCS_MCLAS_CD", ""),
            "ncs_mclass_name": match.get("NCS_MCLAS_CDNM", ""),
            "ncs_sclass_code": match.get("NCS_SCLAS_CD", ""),
            "confidence": 1.0 if exact_norm else 0.85,
            "evidence": "csv-direct-sclass-match",
        })
    return results


def infer_sclass_candidates_from_text_catalog(
    jd_text: str,
    max_items: int = 5,
    hint_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Infer sclass candidates by direct text matching against CSV catalog.

    Strategy:
    - exact/contain matching by normalized sclass name count in jd_text
    - optional hint alias mapping for domain terms (e.g., 일반행정 -> 일반사무)
    - returns schema compatible with fetch_ncs_units_hrdk_by_verified_sclass
    """
    txt = _repair_mojibake(str(jd_text or ""))
    if not txt.strip():
        return []
    norm_txt = _norm_text(txt)
    if not norm_txt:
        return []

    catalog = load_sclass_catalog_from_csv()
    if not catalog:
        return []

    scored: list[tuple[float, dict[str, str], str]] = []
    for row in catalog:
        s_nm = str(row.get("ncs_sclass_name", "")).strip()
        s_norm = _norm_text(s_nm)
        if not s_norm or len(s_norm) < 2:
            continue
        cnt = norm_txt.count(s_norm)
        if cnt <= 0:
            continue
        scored.append((float(cnt), row, f"text-catalog-count:{cnt}"))

    # Hint aliases for terms that may not exist verbatim in catalog labels.
    aliases = {
        "일반행정": "일반사무",
        "학사운영": "평생교육운영",
        "학사": "평생교육운영",
        "경비경호": "경비·경호",
        "경비": "경비·경호",
        "경호": "경비·경호",
    }
    for h in (hint_terms or []):
        hn = _norm_text(str(h or ""))
        if not hn:
            continue
        target = aliases.get(hn, "")
        if not target:
            continue
        tnorm = _norm_text(target)
        for row in catalog:
            s_nm = str(row.get("ncs_sclass_name", "")).strip()
            if _norm_text(s_nm) == tnorm:
                scored.append((0.95, row, f"hint-alias:{h}->{target}"))
                break

    if not scored:
        return []

    scored.sort(key=lambda x: (x[0], len(str(x[1].get("ncs_sclass_name", "")))), reverse=True)
    out: list[dict[str, Any]] = []
    seen = set()
    for score, row, ev in scored:
        key = (
            str(row.get("ncs_code_no", "")).strip(),
            str(row.get("ncs_lclass_code", "")).strip(),
            str(row.get("ncs_mclass_code", "")).strip(),
            str(row.get("ncs_sclass_code", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "sclass_name": str(row.get("ncs_sclass_name", "")).strip(),
                "ncs_sclass_code": key[3],
                "ncs_lclass_code": key[1],
                "ncs_mclass_code": key[2],
                "ncs_code_no": key[0],
                "confidence": float(min(1.0, max(0.62, score / 3.0 if score > 1 else score))),
                "evidence": ev,
            }
        )
        if len(out) >= max_items:
            break
    return out


_DEFAULT_SCLASS_ALIASES_BY_CODE: dict[str, list[str]] = {
    "020203": ["일반행정", "일반서무", "행정직", "행정지원직", "행정지원", "사무행정", "일반사무", "행정사무"],
    "020201": ["총무", "총무업무", "총무관리", "총무행정"],
    "020302": ["회계", "회계처리", "회계실무", "회계업무"],
    "020301": ["재무", "재무관리", "재무기획"],
    "040202": ["학사운영", "학사", "교육운영", "평생교육운영"],
    "110101": ["경비", "경호", "경비경호", "경비·경호", "시설경비"],
    "230601": ["산업안전", "안전관리", "산업안전관리", "안전보건"],
}

_REVERSE_ANCHOR_TERMS: tuple[str, ...] = ("소분류", "세분류", "ncs소분류", "ncs세분류")
_REVERSE_SECTION_TERMS: tuple[str, ...] = ("분류체계", "ncs분류", "직무분류", "능력단위")


def _build_sclass_aliases(
    sclass_name: str,
    code_no: str,
    synonym_by_code: dict[str, list[str]],
    synonym_by_name: dict[str, list[str]],
) -> set[str]:
    aliases: set[str] = set()
    name = str(sclass_name or "").strip()
    if not name:
        return aliases

    aliases.add(name)
    aliases.add(name.replace("·", ""))
    aliases.add(name.replace("·", " ").strip())
    aliases.add(name.replace("/", " ").strip())
    aliases.update(_DEFAULT_SCLASS_ALIASES_BY_CODE.get(str(code_no or "").strip(), []))
    aliases.update(synonym_by_code.get(str(code_no or "").strip(), []))
    aliases.update(synonym_by_name.get(_norm_text(name), []))
    return {a.strip() for a in aliases if str(a or "").strip()}


def _build_reverse_line_context(
    text: str,
    near_anchor_window: int = 8,
    near_section_window: int = 2,
) -> tuple[list[str], set[int], set[int]]:
    raw_lines = [str(ln).strip() for ln in str(text or "").splitlines() if str(ln).strip()]
    norm_lines = [_norm_text(ln) for ln in raw_lines]

    anchor_indices = [i for i, ln in enumerate(norm_lines) if any(term in ln for term in _REVERSE_ANCHOR_TERMS)]
    section_indices = [i for i, ln in enumerate(norm_lines) if any(term in ln for term in _REVERSE_SECTION_TERMS)]

    anchor_near_set: set[int] = set()
    section_near_set: set[int] = set()

    for idx in anchor_indices:
        # Bias anchor context to the lines below the "소분류" row.
        for off in range(-1, near_anchor_window + 1):
            pos = idx + off
            if 0 <= pos < len(norm_lines):
                anchor_near_set.add(pos)

    for idx in section_indices:
        for off in range(-near_section_window, near_section_window + 1):
            pos = idx + off
            if 0 <= pos < len(norm_lines):
                section_near_set.add(pos)

    return norm_lines, anchor_near_set, section_near_set


def infer_sclass_candidates_reverse_dictionary(
    jd_text: str,
    hint_terms: list[str] | None = None,
    doc_name: str = "",
    max_items: int = 8,
) -> list[dict[str, Any]]:
    """Dictionary-first reverse recognition for sclass.

    Instead of extracting arbitrary words first, this scans a predefined sclass
    dictionary (official names + aliases) against document text and ranks
    candidates by weighted hit score.
    """
    txt = _repair_mojibake(str(jd_text or ""))
    if not txt.strip():
        return []
    norm_txt = _norm_text(txt)
    norm_doc = _norm_text(doc_name or "")
    if not norm_txt:
        return []

    catalog = load_sclass_catalog_from_csv()
    if not catalog:
        return []

    synonym_pack = load_sclass_synonym_dictionary()
    synonym_by_code = synonym_pack.get("by_code_no", {})
    synonym_by_name = synonym_pack.get("by_name", {})

    norm_hints = {_norm_text(t) for t in (hint_terms or []) if _norm_text(t)}
    norm_lines, anchor_near_set, section_near_set = _build_reverse_line_context(txt)
    scored: list[tuple[float, dict[str, str], str]] = []
    for row in catalog:
        code_no = str(row.get("ncs_code_no", "")).strip()
        name = str(row.get("ncs_sclass_name", "")).strip()
        if not (code_no and name):
            continue

        aliases = _build_sclass_aliases(
            sclass_name=name,
            code_no=code_no,
            synonym_by_code=synonym_by_code,
            synonym_by_name=synonym_by_name,
        )

        hit_score = 0.0
        hit_count = 0
        anchor_hits = 0
        section_hits = 0
        official_norm = _norm_text(name)
        for a in aliases:
            na = _norm_text(a)
            if len(na) < 2:
                continue
            if na not in norm_txt:
                continue

            for i, ln in enumerate(norm_lines):
                cnt = ln.count(na)
                if cnt <= 0:
                    continue
                hit_count += cnt
                # exact official name gets higher weight than alias.
                w = 1.2 if na == official_norm else 0.9
                if i in anchor_near_set:
                    w += 0.9
                    anchor_hits += cnt
                elif i in section_near_set:
                    w += 0.5
                    section_hits += cnt
                hit_score += float(cnt) * w

        if hit_count <= 0:
            continue

        # hints and file-name matches help for ambiguous docs.
        term_bonus = 0.0
        for a in aliases:
            na = _norm_text(a)
            if na in norm_hints:
                term_bonus += 0.8
            if na and na in norm_doc:
                term_bonus += 0.6
        total = hit_score + term_bonus
        scored.append(
            (
                total,
                row,
                (
                    "reverse-dict:"
                    f"hit={hit_count},anchor={anchor_hits},section={section_hits},bonus={round(term_bonus,2)}"
                ),
            )
        )

    if not scored:
        return []

    scored.sort(key=lambda x: (x[0], len(str(x[1].get("ncs_sclass_name", "")))), reverse=True)
    out: list[dict[str, Any]] = []
    seen = set()
    for score, row, evidence in scored:
        key = (
            str(row.get("ncs_code_no", "")).strip(),
            str(row.get("ncs_lclass_code", "")).strip(),
            str(row.get("ncs_mclass_code", "")).strip(),
            str(row.get("ncs_sclass_code", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "sclass_name": str(row.get("ncs_sclass_name", "")).strip(),
                "ncs_sclass_code": key[3],
                "ncs_lclass_code": key[1],
                "ncs_mclass_code": key[2],
                "ncs_code_no": key[0],
                "confidence": float(min(1.0, max(0.62, score / 4.5))),
                "evidence": evidence,
            }
        )
        if len(out) >= max_items:
            break
    return out


def extract_small_categories_from_jd(jd_text: str) -> list[str]:
    """Extract NCS small-category labels from JD text (robust for OCR/noisy text).

    Improvements:
    - Increased line processing from 5 to 20 lines
    - Better tokenization with variable length support
    - Comma-separated category handling
    - NCS dataset validation
    - Improved stop word filtering
    """
    raw = jd_text or ""
    repaired = _repair_mojibake(raw)
    src = raw if re.search(r"[가-힣]", raw) else repaired

    # 1-a) 헤더 위치 기반 추출 (세로형/가로형 레이아웃 직접 처리)
    focus_lines = _collect_classification_lines(src, max_lines=90)
    known_categories = _load_ncs_small_categories()
    for idx, line in enumerate(focus_lines):
        if "소분류" not in line:
            continue
        plain_terms: list[str] = []
        for candidate_line in focus_lines[idx + 1 : idx + 20]:
            if any(header in candidate_line for header in ("대분류", "중분류", "세분류", "직무수행")):
                break
            term = _clean_category_value(candidate_line)
            if term in known_categories:
                plain_terms.append(term)
        if len(plain_terms) >= 2:
            return _dedup_keep_order(plain_terms)[:15]

    # 1-a0) 세로/가로 레이아웃이 크게 깨진 표 전용 복원
    vertical_blocks = _extract_small_categories_by_vertical_blocks(focus_lines, max_items=15)
    positional = _extract_sclass_by_header_position(focus_lines)
    # 1-b) 표 구조(가로/세로/혼합)에서 코드-명칭 패턴으로 소분류 열 복원
    structural = _extract_small_categories_by_code_pairs(focus_lines)
    # 1-c) 소분류 앵커 주변 방향성 스캔
    anchored = _extract_small_categories_by_anchor_direction(focus_lines, max_items=15)

    # 1-d) 후보들 중 실제 소분류 매핑이 가장 좋은 집합을 선택
    # vertical_blocks는 표 붕괴가 강한 경우에만 사용(그 외에는 과추출 위험).
    candidates_pool: list[tuple[str, list[str], int]] = []
    if len(_dedup_keep_order(vertical_blocks)) >= 4:
        candidates_pool.append(("vertical_blocks", vertical_blocks, 4))
    # 일반 케이스는 anchored를 structural보다 우선 순위로 둔다.
    candidates_pool.extend(
        [
            ("anchored", anchored, 3),
            ("structural", structural, 2),
            ("positional", positional, 1),
        ]
    )
    best_source = ""
    best_terms: list[str] = []
    best_key = (-1, -1, -1)
    mclass_keys = set(_build_mclass_to_sclass_keys_index().keys())
    for source, terms, priority in candidates_pool:
        uniq_terms = _dedup_keep_order(terms)[:15]
        if not uniq_terms:
            continue
        mapped = lookup_ncs_codes_by_sclass(uniq_terms)
        mapped_names = {str(x.get("sclass_name", "")).strip() for x in mapped if str(x.get("sclass_name", "")).strip()}
        mclass_noise = sum(1 for n in mapped_names if _sclass_norm_key(n) in mclass_keys)
        effective_mapped = max(0, len(mapped_names) - mclass_noise)
        key = (effective_mapped, len(mapped_names), priority)
        if key > best_key:
            best_key = key
            best_terms = uniq_terms
            best_source = source
    if best_terms:
        if best_source == "vertical_blocks":
            # Keep parsed table labels as-is for broken layouts (user expectation).
            return _dedup_keep_order(best_terms)[:15]
        # If mapped names cover most terms, return mapped canonical names for consistency.
        mapped = lookup_ncs_codes_by_sclass(best_terms)
        mapped_names = _dedup_keep_order([str(x.get("sclass_name", "")).strip() for x in mapped if str(x.get("sclass_name", "")).strip()])
        if mapped_names:
            mapped_keys = {_sclass_norm_key(x) for x in mapped_names if _sclass_norm_key(x)}
            extras: list[str] = []
            for t in best_terms:
                tt = _clean_category_value(t)
                if not tt:
                    continue
                preserve_explicit = _sclass_norm_key(tt) in {"학사운영"}
                if _sclass_norm_key(tt) in mapped_keys and not preserve_explicit:
                    continue
                # Keep explicit unmapped labels read from the 소분류 region.
                disp = re.sub(r"[·‧･ㆍ•∙⋅]", "", tt)
                if disp:
                    extras.append(disp)
            merged = _dedup_keep_order(mapped_names + extras)
            for explicit in _dedup_keep_order(structural + vertical_blocks):
                explicit_clean = _clean_category_value(explicit)
                if _sclass_norm_key(explicit_clean) in {"학사운영"} and explicit_clean not in merged:
                    merged.append(explicit_clean)
            return merged[:15]
        return best_terms[:15]

    # 2) 구조 복원이 실패하면 단어 기반 백업 추출
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    combined = "\n".join(focus_lines) if focus_lines else src[:2000]
    tokens = re.findall("[\uAC00-\uD7A3]{1,20}", combined)

    stop = {
        "소분류",
        "세분류",
        "대분류",
        "중분류",
        "분류체계",
        "직무수행",
        "직무수행내용",
        "능력단위",
        "필요지식",
        "필요기술",
        "채용분야",
        "직업기초능력",
        "관련자격",
        "관련전공과목",
        "기간제계약직",
        "휴직대체",
        "참고사이트",
        "비고",
    }
    out: list[str] = []
    seen = set()
    for tok in tokens:
        t = tok.strip()
        if not t or t in stop:
            continue
        if t in known_categories:
            if t not in seen:
                seen.add(t)
                out.append(t)
            continue
        best_match = None
        best_match_len = 0
        for known in known_categories:
            if t in known and len(t) >= 2 and len(known) > best_match_len:
                best_match = known
                best_match_len = len(known)
            elif known in t and len(known) >= 2 and len(known) > best_match_len:
                best_match = known
                best_match_len = len(known)
        if best_match and best_match not in seen:
            seen.add(best_match)
            out.append(best_match)
        if len(out) >= 15:
            break

    # 3) 마지막 안전장치: 소분류 라벨 뒤 텍스트 직접 파싱
    if not out:
        marker_patterns = [r"소\s*분\s*류", "소분류"]
        for line in focus_lines or lines:
            if any(re.search(p, line, re.IGNORECASE) for p in marker_patterns):
                parts = re.split(r"[:：]\s*", line, maxsplit=1)
                if len(parts) > 1:
                    vals = [_clean_category_value(x) for x in re.split(r"[,/|]", parts[1])]
                    out.extend([v for v in vals if v and v not in stop])
                break

    return _dedup_keep_order(out)[:15]


def extract_detail_categories_from_jd(jd_text: str) -> list[str]:
    """
    Extract 세분류 labels (e.g., 총무/자산관리/사무행정/회계감사) from JD text.
    """
    src = _repair_mojibake(jd_text)
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    candidate = src
    for i, ln in enumerate(lines):
        if "세분류" in ln or "?몃텇瑜?" in ln:
            candidate = " ".join(lines[i : min(i + 4, len(lines))])
            break

    known = [
        "총무",
        "자산관리",
        "사무행정",
        "회계감사",
        "회계처리",
        "문서관리",
        "계약관리",
        "구매관리",
        "물품관리",
        "재물조사",
        "비품관리",
        "행정지원",
    ]
    alias_hits = {
        "珥앸Т": "총무",
        "먯궛愿由?": "자산관리",
        "?щТ?됱젙": "사무행정",
        "?뚭퀎쨌媛먯궗": "회계감사",
        "?뚭퀎": "회계처리",
    }

    out: list[str] = []
    combined = f"{candidate}\n{src}"
    for k in known:
        if k in combined and k not in out:
            out.append(k)
    for broken, fixed in alias_hits.items():
        if broken in combined and fixed not in out:
            out.append(fixed)

    priority = {
        "총무": 0,
        "자산관리": 1,
        "사무행정": 2,
        "회계감사": 3,
        "회계처리": 4,
    }
    out = sorted(set(out), key=lambda x: priority.get(x, 99))
    return out[:10]


def infer_keywords_from_subcategory_ai(subcategory_text: str, jd_text: str) -> list[str]:
    """
    Infer NCS keywords from 소분류/JD context (local-fast path).

    Notes:
    - No job-family-specific hardcoded priority is applied by default.
    - Priority can be injected externally via env `NCS_KEYWORD_PRIORITY`.
    """
    sub = _repair_mojibake(subcategory_text or "")
    jd = _repair_mojibake(jd_text or "")
    combined = f"{sub}\n{jd}"
    out: list[str] = []

    def _push(term: str) -> None:
        t = str(term or "").strip()
        t = re.sub(r"\s+", " ", t).strip(" ,:;|/-")
        if t in {"대분류", "중분류", "소분류", "세분류", "분류체계"}:
            return
        if len(t) < 2:
            return
        if t not in out:
            out.append(t)

    # 1) Parse explicit subcategory lines first.
    for ln in [x.strip() for x in sub.splitlines() if x.strip()]:
        compact = re.sub(r"\s+", "", ln)
        if "소분류후보" in compact or "소분류" in compact or "세분류" in compact:
            right = re.split(r"[:：]", ln, maxsplit=1)
            rhs = right[1] if len(right) > 1 else ln
            for seg in re.split(r"[,/|]", rhs):
                for tok in re.findall(r"[가-힣A-Za-z0-9()+\-]{2,30}", seg):
                    _push(tok)
            if len(out) >= 20:
                break

    # 2) Recover readable terms from mojibake aliases without fixed rank.
    for broken, fixed in MOJIBAKE_ALIAS.items():
        if broken in combined or fixed in combined:
            _push(fixed)

    # 3) Add frequent Korean tokens from text.
    token_stop = {
        "분류체계",
        "대분류",
        "중분류",
        "소분류",
        "세분류",
        "능력단위",
        "직무",
        "업무",
        "수행",
        "관련",
        "채용",
        "기준",
        "필요지식",
        "필요기술",
    }
    freq_tokens = [t for t in re.findall(r"[\uac00-\ud7a3]{2,16}", combined) if t not in token_stop]
    for tok, _ in Counter(freq_tokens).most_common(60):
        _push(tok)
        if len(out) >= 20:
            break

    # 4) Prefer exact CSV small-category matches, but avoid fuzzy broad matching.
    catalog = load_sclass_catalog_from_csv()
    if catalog and out:
        norm_set = {_norm_text(x) for x in out if _norm_text(x)}
        exact_csv_hits: list[str] = []
        for row in catalog:
            name = str(row.get("ncs_sclass_name", "")).strip()
            if not name:
                continue
            if _norm_text(name) in norm_set and name not in exact_csv_hits:
                exact_csv_hits.append(name)
        out = _dedup_keep_order(exact_csv_hits + out)

    # 5) Optional external priority injection (comma-separated).
    env_priority_raw = os.getenv("NCS_KEYWORD_PRIORITY", "").strip()
    if env_priority_raw:
        priority = [x.strip() for x in env_priority_raw.split(",") if x.strip()]
        prioritized = [x for x in priority if x in out]
        tail = [x for x in out if x not in prioritized]
        out = prioritized + tail

    return out[:12]


def build_local_question_pack(jd_text: str, strengths: str, ncs_matches: list[dict[str, Any]]) -> dict[str, Any]:
    interview_questions = _generate_questions_with_openai_from_ncs(
        jd_text=jd_text,
        strengths=strengths,
        ncs_matches=ncs_matches,
        target_count=20,
        mode="local_pack",
    )
    by_comp = _build_interview_by_competency_from_questions(interview_questions)
    return {"interview_by_competency": by_comp, "interview_questions": interview_questions}


def _build_ksa_driven_question_pack(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    strengths: str = "",
) -> list[dict[str, Any]]:
    interview_questions = _generate_questions_with_openai_from_ncs(
        jd_text="",
        strengths=strengths,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        target_count=min(max(len(ncs_matches or []) * 4, 8), 32),
        mode="ksa_driven",
    )
    return _build_interview_by_competency_from_questions(interview_questions)


def generate_personalized_interview_questions(
    ncs_code: str,
    competency_name: str = "",
    job_posting: str = "",
    user_profile: str = "",
    target_count: int = 12,
) -> dict[str, Any]:
    comp_name = competency_name or f"NCS-{ncs_code}"
    generated = _generate_questions_with_openai_from_ncs(
        jd_text=job_posting,
        strengths=user_profile,
        ncs_matches=[{"ncsClCd": ncs_code, "compeUnitName": comp_name}],
        target_count=min(max(target_count, 1), 20),
        mode="personalized",
        extra_context=f"user_profile={user_profile[:3000]}",
    )
    return {
        "ncs_code": ncs_code,
        "competency_name": comp_name,
        "generation_mode": "ai_personalized_ncs",
        "company_from_posting": "",
        "requirements_from_posting": "",
        "skills_from_profile": "",
        "questions": [
            {"question": q.get("question", ""), "question_type": q.get("type", "면접질문")}
            for q in generated
        ],
        "question_count": len(generated),
        "note": "NCS 컨텍스트 기반 생성형 AI 자율 생성 결과입니다.",
    }


def generate_diverse_interview_questions(
    ncs_code: str,
    competency_name: str = "",
    job_posting: str = "",
    target_count: int = 6,
) -> dict[str, Any]:
    comp_name = competency_name or f"NCS-{ncs_code}"
    ncs_matches = [{"ncsClCd": ncs_code, "compeUnitName": comp_name}]
    ncs_ksa = fetch_ncs_ksa_by_units(ncs_matches=ncs_matches, max_units=1, max_factors_per_unit=6)
    generated = _generate_questions_with_openai_from_ncs(
        jd_text=job_posting,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        target_count=min(max(target_count, 1), 6),
        mode="diverse",
    )
    questions_list: list[dict[str, Any]] = []
    for i, q in enumerate(generated, 1):
        raw_fu = q.get("follow_ups")
        if isinstance(raw_fu, list):
            follow_ups = [str(x).strip() for x in raw_fu if str(x).strip()]
        else:
            one = str(q.get("follow_up", "")).strip()
            follow_ups = [one] if one else []
        questions_list.append(
            {
                "number": i,
                "type": str(q.get("type", "면접질문")).strip() or "면접질문",
                "competency": str(q.get("competency", comp_name)).strip() or comp_name,
                "ncs_code": ncs_code,
                "question": str(q.get("question", "")).strip(),
                "follow_ups": follow_ups,
                "follow_up": (follow_ups[0] if follow_ups else ""),
                "eval_points": list(q.get("evaluation_points", []) or []),
                "ksa_refs": list(q.get("ksa_refs", []) or []),
            }
        )
    return {
        "ncs_code": ncs_code,
        "competency_name": comp_name,
        "generation_mode": "ai_autonomous_ncs",
        "questions": questions_list,
        "question_count": len(questions_list),
        "note": "NCS 컨텍스트 기반 생성형 AI 자율 생성 결과입니다.",
    }


def generate_interview_questions_by_ncs_code(
    ncs_code: str,
    competency_name: str = "",
    target_count: int = 10,
    include_followups: bool = True,
) -> dict[str, Any]:
    code = str(ncs_code or "").strip()
    comp_name = competency_name or f"NCS-{code}"
    ncs_matches: list[dict[str, Any]] = [{"ncsClCd": code, "compeUnitName": comp_name}]
    is_sclass_mode = bool(code.isdigit() and len(code) == 6)
    sclass_units: list[dict[str, Any]] = []

    # If code has a valid 6-digit prefix, enrich context using local indexed NCS rows.
    code6 = re.sub(r"[^0-9]", "", code)[:6]
    if len(code6) == 6:
        sclass_units = fetch_ncs_units_hrdk_by_sclass_code(
            ncs_lclass_code=code6[:2],
            ncs_mclass_code=code6[2:4],
            ncs_sclass_code=code6[4:6],
            sclass_name="",
        )
        if is_sclass_mode:
            # Input is sclass code_no (e.g., 020201): multi-unit context.
            ncs_matches = sclass_units[: max(8, min(12, len(sclass_units)))] if sclass_units else ncs_matches
            if sclass_units:
                comp_name = str(sclass_units[0].get("ncsSclasCdnm", "")).strip() or comp_name
        else:
            picked = None
            for u in sclass_units:
                if str(u.get("ncsClCd", "")).strip() == code:
                    picked = u
                    break
            if not picked and sclass_units:
                picked = sclass_units[0]
            if picked:
                comp_name = str(picked.get("compeUnitName", "")).strip() or comp_name
                ncs_matches = [picked]

    desired_count = min(max(target_count, 1), 25)
    allow_template_fallback = str(os.getenv("NCS_ALLOW_TEMPLATE_FALLBACK", "false")).strip().lower() in {"1", "true", "yes", "y"}
    try:
        ai_topup_attempts = int(str(os.getenv("NCS_AI_TOPUP_ATTEMPTS", "4")).strip() or "4")
    except Exception:
        ai_topup_attempts = 2
    ai_topup_attempts = max(0, min(5, ai_topup_attempts))
    used_template_fallback = False
    ncs_ksa = fetch_ncs_ksa_by_units(
        ncs_matches=ncs_matches,
        max_units=min(max(1, len(ncs_matches)), 8),
        max_factors_per_unit=6,
    )

    generated_raw = _generate_questions_with_openai_from_ncs(
        jd_text="",
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        target_count=desired_count,
        mode="ncs_code_only",
    )
    generated: list[dict[str, Any]] = []
    seen_question_keys: set[str] = set()

    def _merge_generated(rows: list[dict[str, Any]] | None) -> int:
        added = 0
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            q_text = str(row.get("question", "")).strip()
            q_key = normalize_question_dedup_key(q_text)
            if not q_key or q_key in seen_question_keys:
                continue
            seen_question_keys.add(q_key)
            r = dict(row)
            r["ncsClCd"] = str(r.get("ncsClCd", "")).strip() or (
                str(ncs_matches[0].get("ncsClCd", "")).strip() if ncs_matches else code
            )
            generated.append(r)
            added += 1
            if len(generated) >= desired_count:
                break
        return added

    _merge_generated(generated_raw)

    for _ in range(ai_topup_attempts):
        if len(generated) >= desired_count:
            break
        remaining = desired_count - len(generated)
        existing_questions = [str(x.get("question", "")).strip() for x in generated if str(x.get("question", "")).strip()]
        dedup_hint = ""
        if existing_questions:
            dedup_hint = "[중복 금지 - 이미 생성된 질문]\n" + "\n".join(f"- {q}" for q in existing_questions[:12])
        extra_raw = _generate_questions_with_openai_from_ncs(
            jd_text="",
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            target_count=min(desired_count, remaining + 2),
            mode="ncs_code_only",
            extra_context=dedup_hint,
        )
        _merge_generated(extra_raw)

    # Sclass mode rule:
    # 1) one main question per unit first
    # 2) only if units < desired_count, then allow duplicates to fill.
    if is_sclass_mode and sclass_units and allow_template_fallback:
        ordered_units = [u for u in sclass_units if str(u.get("ncsClCd", "")).strip()]
        unique_units: list[dict[str, Any]] = []
        seen_units: set[str] = set()
        for u in ordered_units:
            uc = str(u.get("ncsClCd", "")).strip()
            if uc in seen_units:
                continue
            seen_units.add(uc)
            unique_units.append(u)
        required_unique = min(desired_count, len(unique_units))

        by_code: dict[str, list[dict[str, Any]]] = {}
        for g in generated:
            gc = str(g.get("ncsClCd", "")).strip()
            by_code.setdefault(gc, []).append(g)

        distributed: list[dict[str, Any]] = []
        for u in unique_units[:required_unique]:
            uc = str(u.get("ncsClCd", "")).strip()
            picked = None
            if by_code.get(uc):
                picked = by_code[uc].pop(0)
            if not picked and allow_template_fallback:
                unit_ksa_rows = fetch_ncs_ksa_by_units(ncs_matches=[u], max_units=1, max_factors_per_unit=3)
                unit_ksa = [str(x.get("factorName", "")).strip() for x in unit_ksa_rows if str(x.get("factorName", "")).strip()]
                if len(unit_ksa) < 2:
                    unit_ksa = unit_ksa + ["업무 우선순위 설정", "이해관계자 협업"]
                k1, k2 = unit_ksa[0], unit_ksa[1]
                used_template_fallback = True
                picked = {
                    "question": f"{str(u.get('compeUnitName', '')).strip()} 수행 경험에서 '{k1}'과(와) '{k2}'를 어떻게 적용했는지 구체적으로 설명해 주세요.",
                    "type": "경험",
                    "ncsClCd": uc,
                    "evaluation_points": [
                        "상황 맥락을 구조적으로 설명하는가",
                        "핵심 의사결정 근거가 명확한가",
                        "실행 과정과 협업 방식이 구체적인가",
                        "성과와 학습 포인트를 수치 또는 사실로 제시하는가",
                    ],
                    "ksa_refs": [k1, k2],
                    "follow_ups": [
                        "그 상황에서 본인이 맡은 구체적인 역할과 판단 근거를 말씀해 주세요.",
                        "그 과정에서 가장 어려웠던 부분은 무엇이고, 어떻게 해결하셨나요?",
                        "그 결과는 어땠고, 돌이켜보면 어떤 점을 다르게 하시겠습니까?",
                    ],
                }
            if picked:
                distributed.append(picked)

        # Fill remainder only when unique units are fewer than desired count.
        if len(unique_units) < desired_count:
            leftovers: list[dict[str, Any]] = []
            for arr in by_code.values():
                leftovers.extend(arr)
            for g in leftovers:
                if len(distributed) >= desired_count:
                    break
                distributed.append(g)

        generated = distributed[:desired_count]

    if len(generated) < desired_count and allow_template_fallback:
        used_template_fallback = True
        ksa_pool = [str(x.get("factorName", "")).strip() for x in (ncs_ksa or []) if str(x.get("factorName", "")).strip()]
        if not ksa_pool:
            ksa_pool = ["업무 우선순위 설정", "이해관계자 협업", "성과 점검 및 개선"]
        existing = {normalize_question_dedup_key(str(x.get("question", ""))) for x in generated}
        idx = 0
        while len(generated) < desired_count and idx < desired_count * 4:
            k1 = ksa_pool[idx % len(ksa_pool)]
            k2 = ksa_pool[(idx + 1) % len(ksa_pool)]
            qtext = f"{comp_name} 수행 과정에서 '{k1}'과(와) '{k2}'를 적용한 실제 경험을 구체적으로 설명해 주세요."
            key = normalize_question_dedup_key(qtext)
            idx += 1
            if not key or key in existing:
                continue
            existing.add(key)
            generated.append(
                {
                    "question": qtext,
                    "type": "경험",
                    "ncsClCd": str(ncs_matches[0].get("ncsClCd", "")).strip() if ncs_matches else code,
                    "evaluation_points": [
                        "상황 맥락을 구조적으로 설명하는가",
                        "핵심 의사결정 근거가 명확한가",
                        "실행 과정과 협업 방식이 구체적인가",
                        "성과와 학습 포인트를 수치 또는 사실로 제시하는가",
                    ],
                    "ksa_refs": [k1, k2],
                    "follow_ups": [
                        "그 상황에서 본인이 맡은 구체적인 역할과 판단 근거를 말씀해 주세요.",
                        "그 과정에서 가장 어려웠던 부분은 무엇이고, 어떻게 해결하셨나요?",
                        "그 결과는 어땠고, 돌이켜보면 어떤 점을 다르게 하시겠습니까?",
                    ],
                }
            )

    generated = _apply_entry_level_policy_to_questions(generated)

    main_questions = [
        {
            "question": str(q.get("question", "")).strip(),
            "evaluation_points": list(q.get("evaluation_points", []) or []),
            "question_type": str(q.get("type", "면접질문")).strip() or "면접질문",
            "ncsClCd": str(q.get("ncsClCd", "")).strip(),
            "ksa_refs": list(q.get("ksa_refs", []) or []),
            "follow_ups": list(q.get("follow_ups", []) or []),
        }
        for q in generated
    ]
    follow_up_questions: list[dict[str, Any]] = []
    if include_followups:
        for i, q in enumerate(generated):
            raw_fu = q.get("follow_ups")
            if isinstance(raw_fu, list):
                follow_ups = [str(x).strip() for x in raw_fu if str(x).strip()]
            else:
                one = str(q.get("follow_up", "")).strip()
                follow_ups = [one] if one else []
            if len(follow_ups) < 3:
                fallback_fus = [
                    "당시 상황과 본인 역할을 구체적으로 설명해 주세요.",
                    "가장 어려웠던 지점과 대응 방식을 말씀해 주세요.",
                    "결과와 배운 점, 다음 개선 계획을 말씀해 주세요.",
                ]
                for f in fallback_fus:
                    if len(follow_ups) >= 3:
                        break
                    follow_ups.append(f)
            for j, fu in enumerate(follow_ups[:3], start=1):
                follow_up_questions.append(
                    {
                        "follow_up": fu,
                        "for_question_index": i,
                        "step": j,
                        "purpose": "심층 확인",
                    }
                )

    generation_mode = "ai_autonomous_ncs_code_only"
    if used_template_fallback:
        generation_mode = "hybrid_ai_with_template_fallback"
    elif not main_questions:
        generation_mode = "ai_generation_empty_no_fallback"

    result = {
        "ncs_code": code,
        "competency_name": comp_name,
        "generation_mode": generation_mode,
        "main_questions": main_questions,
        "follow_up_questions": follow_up_questions,
        "question_count": len(main_questions),
        "follow_up_count": len(follow_up_questions),
        "total_count": len(main_questions) + len(follow_up_questions),
        "template_fallback_used": used_template_fallback,
    }
    if not main_questions:
        result["error"] = "ai_generation_empty"
    return result


def _is_generic_interview_set(items: list[dict[str, Any]]) -> bool:
    return False


def _build_flat_interview_questions(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    ncs_context: dict[str, Any] | None = None,
    strengths: str = "",
    target_count: int = 50,
    run_seed: int | None = None,
) -> list[dict[str, Any]]:
    _ = run_seed
    return _generate_questions_with_openai_from_ncs(
        jd_text="",
        strengths=strengths,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        ncs_context=ncs_context,
        target_count=target_count,
        mode="fallback_flat",
    )


def build_flat_interview_questions_fallback(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    ncs_context: dict[str, Any] | None = None,
    strengths: str = "",
    target_count: int = 50,
) -> list[dict[str, Any]]:
    return _build_flat_interview_questions(
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        ncs_context=ncs_context,
        strengths=strengths,
        target_count=target_count,
        run_seed=None,
    )


_QUESTION_SIMILARITY_STOPWORDS: set[str] = {
    "그리고",
    "또한",
    "또는",
    "대해",
    "관련",
    "경우",
    "업무",
    "직무",
    "상황",
    "질문",
    "설명",
    "해주세요",
    "주십시오",
    "무엇",
    "어떻게",
    "이유",
    "기준",
    "경험",
    "있나요",
    "있다면",
}


_ENTRY_LEVEL_TRIGGER_RE = re.compile(
    r"(수행\s*경험|경험이\s*있다면|해본\s*경험|참여했던|담당했던|실무에서|업무를\s*수행|수립한\s*경험|운영한\s*경험)"
)
_ENTRY_LEVEL_ALREADY_RE = re.compile(r"(유사\s*사례|가정\s*상황|가정해|가정하여|가정하고)")


def _needs_entry_level_softening(question: str) -> bool:
    q = str(question or "").strip()
    if not q:
        return False
    if _ENTRY_LEVEL_ALREADY_RE.search(q):
        return False
    return bool(_ENTRY_LEVEL_TRIGGER_RE.search(q))


def _soften_entry_level_question(question: str) -> str:
    q = str(question or "").strip()
    if not q:
        return q
    if not _needs_entry_level_softening(q):
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


def _apply_entry_level_policy_to_questions(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in (items or []):
        if not isinstance(row, dict):
            continue
        r = dict(row)
        r["question"] = _soften_entry_level_question(str(r.get("question", "")).strip())

        fus = r.get("follow_ups")
        if isinstance(fus, list):
            r["follow_ups"] = [_soften_entry_level_question(str(x).strip()) for x in fus if str(x).strip()]
            if r["follow_ups"]:
                r["follow_up"] = r["follow_ups"][0]
        else:
            one = str(r.get("follow_up", "")).strip()
            if one:
                r["follow_up"] = _soften_entry_level_question(one)
        out.append(r)
    return out


def normalize_question_dedup_key(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"\[[^\]]+\]", " ", raw)
    raw = re.sub(r"[^0-9a-z가-힣 ]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _question_token_set(text: str) -> set[str]:
    key = normalize_question_dedup_key(text)
    if not key:
        return set()
    return {
        tok for tok in key.split()
        if len(tok) >= 2 and tok not in _QUESTION_SIMILARITY_STOPWORDS
    }


def _char_ngram_set(text: str, size: int = 3) -> set[str]:
    raw = normalize_question_dedup_key(text).replace(" ", "")
    if len(raw) < size:
        return set()
    return {raw[i:i + size] for i in range(len(raw) - size + 1)}


def is_similar_question_text(
    text_a: str,
    text_b: str,
    seq_ratio_threshold: float = 0.92,
    jaccard_threshold: float = 0.80,
    min_token_overlap: int = 4,
) -> bool:
    a = normalize_question_dedup_key(text_a)
    b = normalize_question_dedup_key(text_b)
    if not a or not b:
        return False
    if a == b:
        return True

    seq_ratio = SequenceMatcher(None, a, b).ratio()
    if seq_ratio >= seq_ratio_threshold:
        return True

    a_tokens = _question_token_set(a)
    b_tokens = _question_token_set(b)
    if not a_tokens or not b_tokens:
        return False

    if a_tokens and b_tokens:
        inter = a_tokens.intersection(b_tokens)
        if inter:
            min_size = min(len(a_tokens), len(b_tokens))
            if min_size > 0 and (len(inter) / min_size) >= 0.62 and len(inter) >= 5:
                return True
        if len(inter) >= min_token_overlap:
            union = a_tokens.union(b_tokens)
            if union and (len(inter) / len(union)) >= jaccard_threshold:
                return True

    a_ngrams = _char_ngram_set(a, size=3)
    b_ngrams = _char_ngram_set(b, size=3)
    if not a_ngrams or not b_ngrams:
        return False
    n_union = a_ngrams.union(b_ngrams)
    if not n_union:
        return False
    return (len(a_ngrams.intersection(b_ngrams)) / len(n_union)) >= 0.68


def _normalize_question_key(q: dict[str, Any]) -> str:
    text_key = normalize_question_dedup_key(str((q or {}).get("question", "")))
    if text_key:
        return text_key
    # follow_ups(배열) 또는 follow_up(레거시 문자열) 모두 지원
    fus = (q or {}).get("follow_ups")
    fallback = fus[0] if isinstance(fus, list) and fus else str((q or {}).get("follow_up", ""))
    return normalize_question_dedup_key(fallback)


def _ensure_diverse_question_set(
    generated: list[dict[str, Any]] | None,
    fallback_pool: list[dict[str, Any]],
    target_count: int = 50,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_questions: list[str] = []
    for src in (generated or []) + (fallback_pool or []):
        key = _normalize_question_key(src)
        if not key or key in seen:
            continue
        q_text = str(src.get("question", "")).strip()
        if any(is_similar_question_text(q_text, prev) for prev in seen_questions):
            continue
        seen.add(key)
        seen_questions.append(q_text)
        # follow_ups(배열) 우선 사용, 없으면 follow_up(문자열) 호환
        raw_fu = src.get("follow_ups")
        if isinstance(raw_fu, list) and raw_fu:
            follow_ups = [str(f).strip() for f in raw_fu if str(f).strip()]
        else:
            single = str(src.get("follow_up", "")).strip()
            follow_ups = [single] if single else []
        merged.append(
            {
                "type": str(src.get("type", "상황면접")).strip() or "상황면접",
                "competency": str(src.get("competency", "")).strip(),
                "ncsClCd": str(src.get("ncsClCd", "")).strip(),
                "question": str(src.get("question", "")).strip(),
                "follow_ups": follow_ups,
                "evaluation_points": list(src.get("evaluation_points", []) or []),
            }
        )
        if len(merged) >= target_count:
            break
    return merged[:target_count]


def _build_interview_by_competency_from_questions(
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for q in (questions or []):
        comp = str((q or {}).get("competency", "")).strip() or "핵심 직무"
        code = str((q or {}).get("ncsClCd", "")).strip()
        key = (comp, code)
        grouped.setdefault(key, []).append(
            {
                "question": str((q or {}).get("question", "")).strip(),
                "follow_ups": list((q or {}).get("follow_ups", []) or []),
                "evaluation_points": list((q or {}).get("evaluation_points", []) or []),
            }
        )
    out: list[dict[str, Any]] = []
    for (comp, code), qset in grouped.items():
        out.append({"competency": comp, "ncsClCd": code, "questions": qset})
    return out


def rank_ncs_matches_by_jd(
    jd_text: str,
    ncs_items: list[dict[str, Any]],
    top_k: int = 8,
    preferred_sclass: list[str] | None = None,
    per_sclass_limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Stage-1 unit ranking:
    - Query(JD + duty/evaluation context) vs unit text TF-IDF(char n-gram) cosine
    - Blend with source score
    - Diversify by sclass to avoid one broad sclass dominating top-k
    """
    query_text = _repair_mojibake(jd_text or "")
    if not query_text.strip():
        return []

    rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for it in (ncs_items or []):
        if not isinstance(it, dict):
            continue
        code = str(it.get("ncsClCd", "")).strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        rows.append(dict(it))
    if not rows:
        return []

    keep_n = max(1, int(top_k or 8))

    sim_w = 0.88
    src_w = 0.12
    try:
        sim_w = float(str(os.getenv("NCS_UNIT_SIMILARITY_WEIGHT", "0.88")).strip())
    except Exception:
        sim_w = 0.88
    try:
        src_w = float(str(os.getenv("NCS_UNIT_SOURCE_WEIGHT", "0.12")).strip())
    except Exception:
        src_w = 0.12
    sim_w = max(0.0, sim_w)
    src_w = max(0.0, src_w)
    if sim_w <= 0 and src_w <= 0:
        sim_w = 1.0
    total_w = sim_w + src_w
    sim_w = sim_w / total_w
    src_w = src_w / total_w

    min_similarity = 0.02
    try:
        min_similarity = float(str(os.getenv("NCS_UNIT_MIN_SIMILARITY", "0.02")).strip())
    except Exception:
        min_similarity = 0.02
    min_similarity = max(0.0, min(0.5, min_similarity))

    preferred_keys = {
        _sclass_norm_key(x)
        for x in (preferred_sclass or [])
        if _sclass_norm_key(str(x or ""))
    }

    # Normalize source scores.
    raw_scores: list[float] = []
    for row in rows:
        try:
            raw_scores.append(float(row.get("score", 0.0) or 0.0))
        except Exception:
            raw_scores.append(0.0)
    score_min = min(raw_scores) if raw_scores else 0.0
    score_max = max(raw_scores) if raw_scores else 1.0

    def _norm_source_score(v: float) -> float:
        if score_max > score_min:
            return (v - score_min) / (score_max - score_min)
        if 0.0 <= v <= 1.0:
            return v
        return 1.0 if v > 0 else 0.0

    query_tf = _char_ngram_tf(query_text, ngram_min=2, ngram_max=4)
    doc_tfs: list[Counter[str]] = []
    doc_texts: list[str] = []
    for row in rows:
        doc_text = _repair_mojibake(
            " ".join(
                [
                    str(row.get("ncsSclasCdnm", "")).strip(),
                    str(row.get("ncsSubdCdnm", "")).strip(),
                    str(row.get("compeUnitName", "")).strip(),
                    str(row.get("compeUnitDef", "")).strip(),
                ]
            )
        )
        doc_texts.append(doc_text)
        doc_tfs.append(_char_ngram_tf(doc_text, ngram_min=2, ngram_max=4))

    similarity_scores: list[float] = [0.0] * len(rows)
    if query_tf and any(doc_tfs):
        df: Counter[str] = Counter()
        for tf in doc_tfs:
            df.update(tf.keys())

        doc_count = max(1, len(doc_tfs))
        idf = {term: (math.log((doc_count + 1) / (freq + 1)) + 1.0) for term, freq in df.items()}

        query_w: dict[str, float] = {}
        for term, cnt in query_tf.items():
            if term not in idf:
                continue
            query_w[term] = (1.0 + math.log(max(1, cnt))) * idf[term]
        query_norm = math.sqrt(sum(v * v for v in query_w.values())) if query_w else 0.0

        if query_norm > 0:
            for i, tf in enumerate(doc_tfs):
                if not tf:
                    continue
                dot = 0.0
                doc_norm_sq = 0.0
                for term, cnt in tf.items():
                    weight = (1.0 + math.log(max(1, cnt))) * idf.get(term, 0.0)
                    if weight <= 0:
                        continue
                    doc_norm_sq += weight * weight
                    qv = query_w.get(term)
                    if qv:
                        dot += qv * weight
                doc_norm = math.sqrt(doc_norm_sq)
                if doc_norm > 0 and dot > 0:
                    similarity_scores[i] = dot / (query_norm * doc_norm)

    # Human-readable hit keywords for diagnostics/UI.
    focus_terms = [t for t in _extract_focus_terms(query_text) if str(t or "").strip()][:100]
    if not focus_terms:
        focus_terms = re.findall(r"[\uAC00-\uD7A3]{2,12}", query_text)[:100]

    scored_rows: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        code = str(row.get("ncsClCd", "")).strip()
        sclass_nm = str(row.get("ncsSclasCdnm", "")).strip()
        sclass_key = _sclass_norm_key(sclass_nm)
        sim = float(similarity_scores[i] or 0.0)
        try:
            src_raw = float(row.get("score", 0.0) or 0.0)
        except Exception:
            src_raw = 0.0
        src_norm = _norm_source_score(src_raw)
        final_score = (sim_w * sim) + (src_w * src_norm)
        # Penalize candidates outside preferred sclass set when that set exists.
        if preferred_keys and sclass_key and sclass_key not in preferred_keys:
            final_score -= 0.15

        doc_text = doc_texts[i]
        hit = [k for k in focus_terms if k and k in doc_text][:8]
        scored_rows.append(
            {
                "ncsClCd": code,
                "compeUnitName": str(row.get("compeUnitName", "")).strip(),
                "compeUnitLevel": str(row.get("compeUnitLevel", "")).strip(),
                "ncsSclasCdnm": sclass_nm,
                "ncsSubdCdnm": str(row.get("ncsSubdCdnm", "")).strip(),
                "compeUnitDef": str(row.get("compeUnitDef", "")).strip(),
                "score": round(final_score, 6),
                "matched_keywords": hit,
                "similarityScore": round(sim, 6),
                "sourceScore": round(src_norm, 6),
                "__sclass_key": sclass_key,
            }
        )

    scored_rows.sort(
        key=lambda x: (
            float(x.get("score", 0.0) or 0.0),
            float(x.get("similarityScore", 0.0) or 0.0),
            float(x.get("sourceScore", 0.0) or 0.0),
        ),
        reverse=True,
    )

    if per_sclass_limit is None:
        bucket_count = len(preferred_keys) if preferred_keys else len(
            {str(x.get("__sclass_key", "")).strip() for x in scored_rows if str(x.get("__sclass_key", "")).strip()}
        )
        bucket_count = max(1, bucket_count)
        if bucket_count <= 1:
            effective_cap = keep_n
        else:
            effective_cap = max(1, min(4, int(math.ceil(keep_n / bucket_count))))
    else:
        try:
            effective_cap = max(1, int(per_sclass_limit))
        except Exception:
            effective_cap = 2

    selected: list[dict[str, Any]] = []
    seen_selected: set[str] = set()
    per_sclass_count: dict[str, int] = {}

    def _try_add(row: dict[str, Any], enforce_cap: bool = True) -> bool:
        code = str(row.get("ncsClCd", "")).strip()
        if not code or code in seen_selected:
            return False
        s_key = str(row.get("__sclass_key", "")).strip() or "__none__"
        if enforce_cap and per_sclass_count.get(s_key, 0) >= effective_cap:
            return False
        copied = dict(row)
        copied.pop("__sclass_key", None)
        selected.append(copied)
        seen_selected.add(code)
        per_sclass_count[s_key] = per_sclass_count.get(s_key, 0) + 1
        return True

    # Pass 1: high-similarity rows first.
    for row in scored_rows:
        if float(row.get("similarityScore", 0.0) or 0.0) < min_similarity:
            continue
        _try_add(row, enforce_cap=True)
        if len(selected) >= keep_n:
            return selected[:keep_n]

    # Pass 2: fill with remaining rows, still respecting diversity cap.
    for row in scored_rows:
        _try_add(row, enforce_cap=True)
        if len(selected) >= keep_n:
            return selected[:keep_n]

    # Pass 3: if still short, relax diversity cap.
    for row in scored_rows:
        _try_add(row, enforce_cap=False)
        if len(selected) >= keep_n:
            break
    return selected[:keep_n]


def _parse_ai_rerank_codes(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []

    if "```" in raw:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
        if m:
            raw = m.group(1).strip()

    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return []

    if not isinstance(obj, dict):
        return []

    arr = obj.get("ordered_codes") or obj.get("ranked_codes") or obj.get("codes") or []
    if not isinstance(arr, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for v in arr:
        code = re.sub(r"[^\d]", "", str(v or "").strip())
        if len(code) < 6 or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _ai_rerank_ncs_matches(
    jd_text: str,
    ranked_items: list[dict[str, Any]],
    top_k: int = 8,
    api_key_override: str = "",
) -> list[dict[str, Any]]:
    enabled = os.getenv("ENABLE_AI_RERANK", "true").strip().lower() in {"1", "true", "yes", "y"}
    if not enabled:
        return []

    api_key = str(api_key_override or "").strip() or settings.openai_key()
    if not api_key or len(ranked_items) < 2:
        return []

    net_ok, _ = _check_openai_connectivity(api_key=api_key, ttl_sec=60)
    if not net_ok:
        return []

    model = (
        os.getenv("OPENAI_RERANK_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "gpt-4o-mini"
    )

    candidates = []
    for it in ranked_items[:20]:
        candidates.append(
            {
                "ncsClCd": str(it.get("ncsClCd", "")).strip(),
                "compeUnitName": str(it.get("compeUnitName", "")).strip(),
                "ncsSubdCdnm": str(it.get("ncsSubdCdnm", "")).strip(),
                "compeUnitDef": str(it.get("compeUnitDef", "")).strip()[:240],
                "keyword_score": float(it.get("score", 0.0) or 0.0),
            }
        )

    if len(candidates) < 2:
        return []

    payload = {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "너는 NCS 매칭 재정렬기다. 반드시 JSON만 출력한다. "
                    "스키마: {\"ordered_codes\":[\"...\"]}"
                ),
            },
            {
                "role": "user",
                "content": (
                    "직무기술서와 후보 NCS를 보고 적합한 순서대로 ncsClCd를 정렬하세요.\n"
                    f"JD:\n{_repair_mojibake(jd_text or '')[:1800]}\n\n"
                    f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
                    f"반드시 최대 {max(1, int(top_k or 8))}개 코드만 ordered_codes에 넣으세요."
                ),
            },
        ],
    }

    try:
        data = post_chat_completions_with_retries(
            payload=payload,
            api_key=api_key,
            timeout_sec=15.0,
            max_attempts=2,
        )
        content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", ""))
    except Exception:
        return []

    ordered_codes = _parse_ai_rerank_codes(content)
    if not ordered_codes:
        return []

    def _digits(value: Any) -> str:
        return re.sub(r"[^\d]", "", str(value or "").strip())

    by_code: dict[str, dict[str, Any]] = {}
    for it in ranked_items:
        code = str(it.get("ncsClCd", "")).strip()
        if not code:
            continue
        by_code[code] = it
        d_code = _digits(code)
        if d_code:
            by_code[d_code] = it

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code in ordered_codes:
        item = by_code.get(code) or by_code.get(_digits(code))
        raw_code = str((item or {}).get("ncsClCd", "")).strip()
        if not item or not raw_code or raw_code in seen:
            continue
        merged = dict(item)
        merged["rerank_method"] = "ai"
        out.append(merged)
        seen.add(raw_code)
        if len(out) >= top_k:
            break

    for item in ranked_items:
        code = str(item.get("ncsClCd", "")).strip()
        if not code or code in seen:
            continue
        merged = dict(item)
        merged["rerank_method"] = "keyword"
        out.append(merged)
        seen.add(code)
        if len(out) >= top_k:
            break
    return out


def _ocr_image_with_windows_ocr(image_bytes: bytes, lang: str = "ko") -> str:
    """OCR a PNG/JPEG image via built-in Windows OCR (offline, no API key)."""
    if os.name != "nt" or not image_bytes:
        return ""
    try:
        td = _safe_tmp_dir()
        try:
            img_path = os.path.join(td, "in.png")
            ps_path = os.path.join(td, "ocr.ps1")
            with open(img_path, "wb") as f:
                f.write(image_bytes)

            ps_code = (
                "param([string]$ImagePath,[string]$Lang='ko')\n"
                "$ErrorActionPreference='Stop'\n"
                "Add-Type -AssemblyName System.Runtime.WindowsRuntime\n"
                "$null=[Windows.Globalization.Language, Windows, ContentType=WindowsRuntime]\n"
                "$null=[Windows.Media.Ocr.OcrEngine, Windows, ContentType=WindowsRuntime]\n"
                "$null=[Windows.Graphics.Imaging.BitmapDecoder, Windows, ContentType=WindowsRuntime]\n"
                "$null=[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows, ContentType=WindowsRuntime]\n"
                "$null=[Windows.Storage.Streams.DataWriter, Windows, ContentType=WindowsRuntime]\n"
                "$bytes=[System.IO.File]::ReadAllBytes($ImagePath)\n"
                "$stream=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream\n"
                "$writer=New-Object Windows.Storage.Streams.DataWriter($stream)\n"
                "$writer.WriteBytes($bytes)\n"
                "[System.WindowsRuntimeSystemExtensions]::AsTask($writer.StoreAsync()).Result | Out-Null\n"
                "$writer.DetachStream() | Out-Null\n"
                "$writer.Dispose()\n"
                "$stream.Seek(0)\n"
                "$decoder=[System.WindowsRuntimeSystemExtensions]::AsTask([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)).Result\n"
                "$bitmap=[System.WindowsRuntimeSystemExtensions]::AsTask($decoder.GetSoftwareBitmapAsync()).Result\n"
                "$engine=$null\n"
                "if ($Lang) { try { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language($Lang))) } catch {} }\n"
                "if ($null -eq $engine) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }\n"
                "if ($null -eq $engine) { Write-Output ''; exit 0 }\n"
                "$result=[System.WindowsRuntimeSystemExtensions]::AsTask($engine.RecognizeAsync($bitmap)).Result\n"
                "Write-Output ($result.Text)\n"
            )
            with open(ps_path, "w", encoding="utf-8") as f:
                f.write(ps_code)

            p = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    ps_path,
                    img_path,
                    str(lang or "ko"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=60,
                check=False,
            )
            if p.returncode != 0:
                return ""
            return _repair_mojibake((p.stdout or "").strip())
        finally:
            shutil.rmtree(td, ignore_errors=True)
    except Exception:
        return ""


def _extract_pdf_text_via_windows_ocr(file_bytes: bytes, max_pages: int = 2) -> str:
    images = _render_pdf_pages_png_py313(file_bytes=file_bytes, max_pages=max_pages)
    if not images:
        return ""
    parts: list[str] = []
    for img in images:
        txt = _ocr_image_with_windows_ocr(img, lang=os.getenv("WINDOWS_OCR_LANG", "ko").strip() or "ko")
        if txt:
            parts.append(txt)
    return "\n".join(parts).strip()


def rerank_ncs_matches(
    jd_text: str,
    ncs_items: list[dict[str, Any]],
    top_k: int = 8,
    preferred_sclass: list[str] | None = None,
    openai_api_key: str = "",
) -> tuple[list[dict[str, Any]], str]:
    rank_pool_k = max(top_k, 12)
    diversity_cap: int | None = None
    if preferred_sclass:
        pref_keys = {
            _sclass_norm_key(x)
            for x in preferred_sclass
            if _sclass_norm_key(str(x or ""))
        }
        if pref_keys:
            diversity_cap = max(1, int(math.ceil(max(1, int(top_k or 1)) / len(pref_keys))))
    try:
        ranked = rank_ncs_matches_by_jd(
            jd_text=jd_text,
            ncs_items=ncs_items,
            top_k=rank_pool_k,
            preferred_sclass=preferred_sclass,
            per_sclass_limit=diversity_cap,
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        ranked = rank_ncs_matches_by_jd(
            jd_text=jd_text,
            ncs_items=ncs_items,
            top_k=rank_pool_k,
        )
    if not ranked:
        return [], "keyword"

    ai_ranked = _ai_rerank_ncs_matches(
        jd_text=jd_text,
        ranked_items=ranked,
        top_k=top_k,
        api_key_override=openai_api_key,
    )
    if ai_ranked:
        return ai_ranked[:top_k], "ai"

    out: list[dict[str, Any]] = []
    for it in ranked[:top_k]:
        row = dict(it)
        row["rerank_method"] = "keyword"
        out.append(row)
    return out, "keyword"


def _build_rule_based_questions_from_ncs(
    ncs_matches: list[dict[str, Any]] | None,
    ncs_ksa: list[dict[str, Any]] | None = None,
    target_count: int = 24,
) -> list[dict[str, Any]]:
    return []


def build_strategy_with_rule_fallback(
    ncs_matches: list[dict[str, Any]] | None,
    ncs_ksa: list[dict[str, Any]] | None = None,
    error_message: str = "",
    target_count: int = 24,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "interview_questions": [],
        "interview_by_competency": [],
        "ncs_link": [
            {
                "ncsClCd": str(x.get("ncsClCd", "")).strip(),
                "compeUnitName": str(x.get("compeUnitName", "")).strip(),
                "why": "NCS 매핑 결과",
            }
            for x in (ncs_matches or [])[:6]
        ],
        "question_generation_policy": "model_only_no_template_fallback",
    }
    if error_message:
        obj["error"] = error_message
    return obj


def _check_openai_connectivity(api_key: str, ttl_sec: int = 60) -> tuple[bool, str]:
    enabled = os.getenv("OPENAI_NET_CHECK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "y"}
    if not enabled:
        return True, "disabled"

    try:
        ttl_sec = int(str(os.getenv("OPENAI_NET_CHECK_TTL_SEC", str(ttl_sec))).strip())
    except Exception:
        ttl_sec = int(ttl_sec)
    ttl_sec = max(0, ttl_sec)

    key_fingerprint = hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]
    now = time.time()
    if (
        _OPENAI_NET_CACHE.get("key") == key_fingerprint
        and (now - float(_OPENAI_NET_CACHE.get("ts", 0.0))) < ttl_sec
    ):
        return bool(_OPENAI_NET_CACHE.get("ok", True)), str(_OPENAI_NET_CACHE.get("msg", ""))

    msg = ""
    ok = False
    connect_timeout = 5.0
    read_timeout = 15.0
    write_timeout = 5.0
    pool_timeout = 2.5
    try:
        connect_timeout = max(0.5, float(str(os.getenv("OPENAI_NET_CHECK_CONNECT_TIMEOUT_SEC", "5.0")).strip()))
    except Exception:
        pass
    try:
        read_timeout = max(1.0, float(str(os.getenv("OPENAI_NET_CHECK_READ_TIMEOUT_SEC", "15.0")).strip()))
    except Exception:
        pass
    try:
        write_timeout = max(1.0, float(str(os.getenv("OPENAI_NET_CHECK_WRITE_TIMEOUT_SEC", "5.0")).strip()))
    except Exception:
        pass
    try:
        pool_timeout = max(0.5, float(str(os.getenv("OPENAI_NET_CHECK_POOL_TIMEOUT_SEC", "2.5")).strip()))
    except Exception:
        pass

    try:
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )
        ok, msg = check_openai_connectivity_with_retries(
            api_key=api_key,
            timeout=timeout,
        )
    except Exception as e:
        ok = False
        msg = str(e)

    _OPENAI_NET_CACHE["ts"] = now
    _OPENAI_NET_CACHE["key"] = key_fingerprint
    _OPENAI_NET_CACHE["ok"] = ok
    _OPENAI_NET_CACHE["msg"] = msg
    return ok, msg


def build_strategy_with_openai(
    jd_text: str,
    notice_text: str,
    strengths: str,
    region: str,
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    ncs_context: dict[str, Any] | None = None,
    duty_text: str = "",
    evaluation_text: str = "",
    desired_job: str = "",
    api_key_override: str = "",
) -> dict[str, Any]:
    api_key = str(api_key_override or "").strip() or settings.openai_key()
    target_count = max(5, min(40, int(os.getenv("INTERVIEW_TARGET_COUNT", "10") or "10")))
    retry_target_count = max(5, min(10, target_count))
    primary_model = (os.getenv("OPENAI_STRATEGY_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()
    retry_model = (os.getenv("OPENAI_STRATEGY_RETRY_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()
    force_fallback = (os.getenv("OPENAI_FORCE_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "y"})
    if not api_key:
        return build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message="model_generation_failed: OPENAI_API_KEY is not set",
            target_count=target_count,
        )
    if force_fallback:
        return build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message="model_generation_skipped: OPENAI_FORCE_FALLBACK is enabled (template fallback disabled)",
            target_count=target_count,
        )

    strict_net_check = os.getenv("OPENAI_NET_CHECK_STRICT", "false").strip().lower() in {"1", "true", "yes", "y"}
    net_ok, net_msg = _check_openai_connectivity(api_key=api_key, ttl_sec=60)
    precheck_warning = ""
    if not net_ok:
        detail = f"openai_network_unreachable ({net_msg})" if net_msg else "openai_network_unreachable"
        if strict_net_check:
            return build_strategy_with_rule_fallback(
                ncs_matches=ncs_matches,
                ncs_ksa=ncs_ksa,
                error_message=f"model_generation_skipped: {detail}",
                target_count=target_count,
            )
        precheck_warning = detail

    strengths = (strengths or "").strip()
    duty_text = (duty_text or "").strip()
    evaluation_text = (evaluation_text or "").strip()
    has_priority_context = bool(duty_text or evaluation_text)
    priority_rules = ""
    if has_priority_context:
        priority_rules = (
            "[우선 반영 규칙]\n"
            "- 담당업무/면접평가항목 텍스트를 JD 일반문맥보다 우선 반영하세요.\n"
            "- 각 질문과 꼬리질문, 평가포인트에는 담당업무 또는 평가항목의 핵심 표현을 직접 포함하세요.\n"
            "- 위 두 입력과 무관한 일반론 질문은 생성하지 마세요.\n"
            f"[담당업무-최우선]{duty_text[:2200]}\n"
            f"[면접평가항목-최우선]{evaluation_text[:1600]}\n\n"
        )
    profile_mode = (
        "개인특성 모드: 개인 강점/경험을 반드시 질문에 반영하세요."
        if strengths
        else "개인특성 모드: 개인 강점 입력이 없으므로 JD/NCS 기준으로만 생성하세요."
    )

    # STRUCTURED_INTERVIEW_GUIDE.md 핵심 섹션만 추출 (토큰 절약)
    _guide_path = os.path.join(os.path.dirname(__file__), "..", "..", "STRUCTURED_INTERVIEW_GUIDE.md")
    _guide_summary = ""
    try:
        with open(_guide_path, "r", encoding="utf-8") as _f:
            _guide_full = _f.read()
        # "## 2. 핵심 원칙" ~ "## 3." 구간만 추출
        import re as _re2
        # 3-1~3-5 질문 유형별 작성 기법 섹션만 추출 (가장 실용적인 부분)
        _m = _re2.search(r"(## 3\. 질문 유형별 작성 기법.*?)(?=## \d\.|\Z)", _guide_full, _re2.DOTALL)
        _guide_summary = _m.group(1).strip()[:1400] if _m else _guide_full[:600]
    except Exception:
        _guide_summary = (
            "원칙: 주질문1개+꼬리질문3개(사례구체화/어려움대처/결과교훈). "
            "type=경험50%/상황30%/직무지식20%. STAR프레임. 개방형 단일의도."
        )

    prompt = (
        "JSON만 출력하세요.\n"
        "목표: NCS 능력단위 기반 구조화 면접 질문 생성\n"
        "언어: 모든 문자열은 한국어\n"
        "출력 스키마: {"
        '"interview_questions":[{"type":"경험|상황|직무지식","competency":"능력단위명","ncsClCd":"코드","question":"주질문(1개)","follow_ups":["꼬리질문1","꼬리질문2","꼬리질문3"],"evaluation_points":["평가항목1","평가항목2","평가항목3","평가항목4"]}],'
        '"ncs_link":[{"ncsClCd":"...","compeUnitName":"...","why":"..."}]'
        "}\n\n"
        "[구조화 면접 원칙]\n"
        f"{_guide_summary}\n\n"
        f"{priority_rules}"
        "생성 규칙:\n"
        f"- interview_questions {target_count}개 생성\n"
        "- 각 항목: 주질문 1개 + follow_ups 꼬리질문 정확히 3개\n"
        "- type 비율: 경험 40% / 상황 30% / 직무지식 20% / 가치·태도 10%\n"
        "- 지원자가 해당 업무를 직접 맡아보지 않았을 수 있음을 전제로, 유사 경험 또는 가정형 답변이 가능하도록 질문할 것\n"
        "\n"
        "[주질문 작성 필수 기준]\n"
        "1. 경험형: '~한 경험 중 가장 도전적이었던 사례를 말씀해 주세요.' — 막연한 질문 금지, 직무 맥락 포함\n"
        "2. 상황형: 실제 직무에서 발생 가능한 구체적 시나리오를 제시 후 대처를 질문\n"
        "   예) '예산 외 지출을 상사가 구두로 지시한 경우 어떻게 하시겠습니까?'\n"
        "   예) '담당 비품이 분실됐는데 책임자를 특정하기 어려운 상황이라면?'\n"
        "3. 직무지식형: 절차·법규·기준을 묻고 실제 적용 경험까지 연결\n"
        "   예) '물품관리법상 불용처분 절차를 설명하고, 적용 경험이 있다면 함께 말씀해 주세요.'\n"
        "4. 가치·태도형: 규정과 현실이 충돌하는 상황에서의 판단 기준을 확인\n"
        "\n"
        "[꼬리질문 작성 기준]\n"
        "- 꼬리물기 구조: 주질문 → 꼬리1 → 꼬리2 → 꼬리3, 앞 답변을 전제로 더 깊이 파고드는 질문\n"
        "- 꼬리1·2·3은 각각 evaluation_points의 서로 다른 항목을 검증\n"
        "- 같은 내용을 반복하거나 독립적인 질문 나열 금지\n"
        "- 주질문은 개방형 단일 의도, '네/아니오'로 답할 수 없는 문장\n"
        "- 각 질문은 compeUnitDef(능력단위 정의) 직접 반영\n"
        "- evaluation_points는 NCS 수행준거 기반 4~6개\n"
        "- 동일 패턴('~경험을 말씀해 주세요' 반복) 금지 — 질문마다 다른 도입부 사용\n"
        f"[생성시드]{int(time.time())}\n"
        f"{profile_mode}\n"
        f"[희망직무]{desired_job}\n"
        f"[희망지역]{region}\n"
        f"[개인강점]{strengths}\n"
        f"[공고문]{notice_text[:1500]}\n"
        f"[직무기술서]{jd_text[:1500]}\n"
        f"[매칭NCS]{json.dumps((ncs_matches or [])[:5], ensure_ascii=False)}\n"
        f"[NCS평가요소]{json.dumps((ncs_ksa or [])[:15], ensure_ascii=False)}\n"
    )

    payload = {
        "model": primary_model,
        "messages": [
            {"role": "system", "content": "너는 공공기관 면접 코치다. 반드시 한국어 JSON만 출력한다."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    timeout_sec = float(os.getenv("OPENAI_STRATEGY_TIMEOUT_SEC", "60") or "60")
    model_error = ""
    recovered_with_slim_retry = False
    obj: dict[str, Any] = {}

    def _request_json(local_payload: dict[str, Any], local_timeout: float) -> dict[str, Any]:
        data = post_chat_completions_with_retries(
            payload=local_payload,
            api_key=api_key,
            timeout_sec=local_timeout,
        )
        parsed = json.loads(data["choices"][0]["message"]["content"])
        if not isinstance(parsed, dict):
            raise ValueError("model_response_not_object")
        return parsed

    try:
        obj = _request_json(payload, timeout_sec)
    except Exception as e:
        # First failure: retry with slimmer, compeUnitDef-focused prompt.
        model_error = str(e)
        slim_priority = ""
        if has_priority_context:
            slim_priority = (
                f"[priority_duty]{duty_text[:1500]}\n"
                f"[priority_eval]{evaluation_text[:1200]}\n"
            )
        slim_prompt = (
            "JSON만 출력하세요.\n"
            "목표: NCS 능력단위 기반 구조화 면접 질문 생성\n"
            "언어: 한국어\n"
            "스키마: {"
            '"interview_questions":[{"type":"경험|상황|직무지식","competency":"...","ncsClCd":"...","question":"주질문1개","follow_ups":["꼬리질문1","꼬리질문2","꼬리질문3"],"evaluation_points":["..."]}],'
            '"ncs_link":[{"ncsClCd":"...","compeUnitName":"...","why":"..."}]'
            "}\n"
            "규칙:\n"
            f"- interview_questions {retry_target_count}개 생성\n"
            "- 각 항목: 주질문 1개 + follow_ups 꼬리질문 3개 (꼬리물기: 앞 답변을 받아 더 깊이 파고드는 구조, 서로 다른 평가항목 검증)\n"
            "- type: '경험' 50% / '상황' 30% / '직무지식' 20%\n"
            "- 각 질문은 compeUnitDef(능력단위 정의) 직접 반영\n"
            "- evaluation_points는 NCS 수행준거 기반 4~6개\n"
            "- 지원자가 직접 수행한 경험이 없을 수 있으므로, 유사 사례/가정형 답변이 가능하도록 질문할 것\n"
            f"{slim_priority}"
            f"[ncs_matches]{json.dumps((ncs_matches or [])[:5], ensure_ascii=False)}\n"
            f"[ncs_factors]{json.dumps((ncs_ksa or [])[:20], ensure_ascii=False)}\n"
            f"[notice_core]{notice_text[:1200]}\n"
            f"[jd_core]{jd_text[:1200]}\n"
        )
        slim_payload = {
            "model": retry_model,
            "messages": [
                {"role": "system", "content": "너는 능력단위 정의 기반 면접 질문 생성기다. 한국어 JSON만 출력한다."},
                {"role": "user", "content": slim_prompt},
            ],
            "temperature": 0.6,
            "max_tokens": 1800,
            "response_format": {"type": "json_object"},
        }
        try:
            obj = _request_json(slim_payload, min(timeout_sec, 50.0))
            recovered_with_slim_retry = True
        except Exception as e2:
            model_error = f"{e}; retry_failed: {e2}"
            obj = {}

    obj["ncs_candidates_raw"] = ncs_matches
    obj["ncs_ksa_used"] = ncs_ksa or []
    obj["ncs_context_used"] = ncs_context or {}
    q_list = obj.get("interview_questions")
    if not isinstance(q_list, list):
        q_list = []
    q_list = [q for q in q_list if isinstance(q, dict)]

    generated_has_content = any(str((q or {}).get("question", "")).strip() for q in q_list)
    target_total = target_count if not generated_has_content else min(target_count, len(q_list))
    obj["interview_questions"] = _ensure_diverse_question_set(
        generated=q_list,
        fallback_pool=[],
        target_count=max(1, target_total),
    )
    obj["interview_questions"] = _apply_entry_level_policy_to_questions(obj["interview_questions"])
    obj["interview_by_competency"] = _build_interview_by_competency_from_questions(obj["interview_questions"])
    if "ncs_link" not in obj or not isinstance(obj.get("ncs_link"), list):
        obj["ncs_link"] = [
            {
                "ncsClCd": str(x.get("ncsClCd", "")).strip(),
                "compeUnitName": str(x.get("compeUnitName", "")).strip(),
                "why": "NCS 기반 자동 매핑",
            }
            for x in (ncs_matches or [])[:6]
        ]

    if generated_has_content:
        obj["question_generation_policy"] = (
            "model_autonomous_with_ncs_factor_context_slim_retry"
            if recovered_with_slim_retry
            else "model_autonomous_with_ncs_factor_context_and_competency_definition"
        )
    else:
        obj["question_generation_policy"] = "model_only_no_template_fallback"
    if model_error:
        obj["error"] = f"model_generation_failed: {model_error}"
    if precheck_warning and not model_error:
        obj["warning"] = f"openai_precheck_warning: {precheck_warning}"
    return obj


# ---------------------------------------------------------------------------
# Recovered NCS pipeline helpers
# ---------------------------------------------------------------------------
_SCLASS_CSV_CACHE: dict[str, Any] = {"ts": 0.0, "items": [], "path": ""}
_SCLASS_SYNONYM_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "items": {"by_code_no": {}, "by_name": {}},
    "path": "",
}
_KSA_FACTOR_CACHE_BY_CODE: dict[str, list[dict[str, str]]] = {}
_NCS_XLSX_CACHE: dict[str, Any] = {"ts": 0.0, "items": [], "path": "", "map": {}}
_NCS_LOCAL_DB_STATE: dict[str, Any] = {"ready": False, "db_path": "", "xlsx_path": "", "xlsx_mtime": 0.0}
if "_OPENAI_NET_CACHE" not in globals():
    _OPENAI_NET_CACHE: dict[str, Any] = {"ts": 0.0, "ok": True, "msg": ""}


def _default_sclass_csv_path() -> str:
    here = os.path.abspath(__file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "ncs_sclass_codes_with_code_no.csv")


def _default_sclass_synonym_path() -> str:
    here = os.path.abspath(__file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "app", "data", "ncs_sclass_synonyms.json")


def _default_ncs_xlsx_path() -> str:
    here = os.path.abspath(__file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "NCS_DB.xlsx")


def _default_app_db_path() -> str:
    here = os.path.abspath(__file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "ncscope.db")


def _connect_local_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30.0)
    con.row_factory = sqlite3.Row
    return con


def ensure_ncs_local_index(db_path: str | None = None, xlsx_path: str | None = None) -> bool:
    dbp = db_path or os.getenv("APP_DB_PATH", "").strip() or _default_app_db_path()
    xlsx = xlsx_path or os.getenv("NCS_DB_XLSX_PATH", "").strip() or _default_ncs_xlsx_path()
    if not os.path.exists(dbp) or not os.path.exists(xlsx):
        return False

    xlsx_mtime = os.path.getmtime(xlsx)
    if (
        _NCS_LOCAL_DB_STATE.get("ready")
        and _NCS_LOCAL_DB_STATE.get("db_path") == dbp
        and _NCS_LOCAL_DB_STATE.get("xlsx_path") == xlsx
        and float(_NCS_LOCAL_DB_STATE.get("xlsx_mtime", 0.0)) == float(xlsx_mtime)
    ):
        return True

    try:
        import openpyxl  # type: ignore
    except Exception:
        return False

    try:
        con = _connect_local_db(dbp)
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ncs_local_rows (
                ncs_cl_cd TEXT NOT NULL,
                code6 TEXT NOT NULL,
                compe_unit_name TEXT,
                compe_unit_level TEXT,
                ncs_lclass_code TEXT,
                ncs_lclass_name TEXT,
                ncs_mclass_code TEXT,
                ncs_mclass_name TEXT,
                ncs_sclass_code TEXT,
                ncs_sclass_name TEXT,
                ncs_subd_code TEXT,
                ncs_subd_name TEXT,
                unit_elem_name TEXT,
                unit_criteria TEXT,
                ksa_type_name TEXT,
                ksa_text TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ncs_local_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cur.execute("SELECT value FROM ncs_local_meta WHERE key='xlsx_mtime'")
        row = cur.fetchone()
        current = float(row["value"]) if row and row["value"] else 0.0
        cur.execute("SELECT COUNT(*) AS cnt FROM ncs_local_rows")
        cnt = int((cur.fetchone() or {"cnt": 0})["cnt"])
        if cnt > 0 and current == float(xlsx_mtime):
            con.close()
            _NCS_LOCAL_DB_STATE.update(
                {"ready": True, "db_path": dbp, "xlsx_path": xlsx, "xlsx_mtime": float(xlsx_mtime)}
            )
            return True

        cur.execute("DELETE FROM ncs_local_rows")
        con.commit()

        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        insert_sql = (
            "INSERT INTO ncs_local_rows ("
            "ncs_cl_cd, code6, compe_unit_name, compe_unit_level, "
            "ncs_lclass_code, ncs_lclass_name, ncs_mclass_code, ncs_mclass_name, "
            "ncs_sclass_code, ncs_sclass_name, ncs_subd_code, ncs_subd_name, "
            "unit_elem_name, unit_criteria, ksa_type_name, ksa_text"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        batch: list[tuple[str, ...]] = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
                if i == 1 or not r or len(r) < 19:
                    continue
                l_cd = _normalize_code(r[0], 2)
                m_cd = _normalize_code(r[2], 2)
                s_cd = _normalize_code(r[4], 2)
                ncs_cl_cd = _normalize_code(r[8]).strip()
                if not ncs_cl_cd:
                    continue
                code6 = (f"{l_cd}{m_cd}{s_cd}" if (l_cd and m_cd and s_cd) else ncs_cl_cd[:6]).strip()
                if not code6:
                    continue
                batch.append(
                    (
                        ncs_cl_cd,
                        code6,
                        _repair_mojibake(str(r[9] or "").strip()),
                        _normalize_code(r[10]).strip(),
                        l_cd,
                        _repair_mojibake(str(r[1] or "").strip()),
                        m_cd,
                        _repair_mojibake(str(r[3] or "").strip()),
                        s_cd,
                        _repair_mojibake(str(r[5] or "").strip()),
                        _normalize_code(r[6], 2).strip(),
                        _repair_mojibake(str(r[7] or "").strip()),
                        _repair_mojibake(str(r[12] or "").strip()),
                        _repair_mojibake(str(r[14] or "").strip()),
                        _repair_mojibake(str(r[16] or "").strip()),
                        _repair_mojibake(str(r[18] or "").strip()),
                    )
                )
                if len(batch) >= 2000:
                    cur.executemany(insert_sql, batch)
                    con.commit()
                    batch = []
        if batch:
            cur.executemany(insert_sql, batch)
            con.commit()
        wb.close()

        cur.execute("CREATE INDEX IF NOT EXISTS idx_ncs_local_code6 ON ncs_local_rows(code6)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ncs_local_clcd ON ncs_local_rows(ncs_cl_cd)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ncs_local_code6_clcd ON ncs_local_rows(code6, ncs_cl_cd)")
        cur.execute("INSERT OR REPLACE INTO ncs_local_meta(key, value) VALUES('xlsx_mtime', ?)", (str(float(xlsx_mtime)),))
        con.commit()
        con.close()
    except Exception:
        return False

    _NCS_LOCAL_DB_STATE.update({"ready": True, "db_path": dbp, "xlsx_path": xlsx, "xlsx_mtime": float(xlsx_mtime)})
    return True


def get_ncs_local_index_status(db_path: str | None = None, xlsx_path: str | None = None) -> dict[str, Any]:
    dbp = db_path or os.getenv("APP_DB_PATH", "").strip() or _default_app_db_path()
    xlsx = xlsx_path or os.getenv("NCS_DB_XLSX_PATH", "").strip() or _default_ncs_xlsx_path()
    xlsx_exists = os.path.exists(xlsx)
    db_exists = os.path.exists(dbp)
    xlsx_mtime = os.path.getmtime(xlsx) if xlsx_exists else 0.0
    row_count = 0
    meta_mtime = 0.0
    indexed = False

    if db_exists:
        try:
            con = _connect_local_db(dbp)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ncs_local_rows'")
            has_rows_tbl = cur.fetchone() is not None
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ncs_local_meta'")
            has_meta_tbl = cur.fetchone() is not None
            if has_rows_tbl:
                cur.execute("SELECT COUNT(*) AS cnt FROM ncs_local_rows")
                row_count = int((cur.fetchone() or {"cnt": 0})["cnt"])
            if has_meta_tbl:
                cur.execute("SELECT value FROM ncs_local_meta WHERE key='xlsx_mtime'")
                row = cur.fetchone()
                if row and row["value"]:
                    meta_mtime = float(row["value"])
            con.close()
            indexed = bool(row_count > 0 and xlsx_exists and float(meta_mtime) == float(xlsx_mtime))
        except Exception:
            indexed = False

    return {
        "indexed": indexed,
        "db_path": dbp,
        "xlsx_path": xlsx,
        "db_exists": db_exists,
        "xlsx_exists": xlsx_exists,
        "row_count": row_count,
        "xlsx_mtime": xlsx_mtime,
        "indexed_mtime": meta_mtime,
    }


def _normalize_code(v: Any, width: int = 0) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit() and width > 0:
        return s.zfill(width)
    return s


def load_ncs_rows_from_xlsx(xlsx_path: str | None = None, cache_ttl_sec: int = 60 * 30) -> list[dict[str, str]]:
    path = xlsx_path or os.getenv("NCS_DB_XLSX_PATH", "").strip() or _default_ncs_xlsx_path()
    now = time.time()
    if _NCS_XLSX_CACHE.get("items") and _NCS_XLSX_CACHE.get("path") == path:
        if (now - float(_NCS_XLSX_CACHE.get("ts", 0.0))) < cache_ttl_sec:
            return list(_NCS_XLSX_CACHE["items"])
    if not os.path.exists(path):
        return []

    try:
        import openpyxl  # type: ignore
    except Exception:
        return []

    out: list[dict[str, str]] = []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if idx == 1:
                    continue
                if not row or len(row) < 19:
                    continue
                l_cd = _normalize_code(row[0], 2)
                l_nm = _repair_mojibake(str(row[1] or "").strip())
                m_cd = _normalize_code(row[2], 2)
                m_nm = _repair_mojibake(str(row[3] or "").strip())
                s_cd = _normalize_code(row[4], 2)
                s_nm = _repair_mojibake(str(row[5] or "").strip())
                subd_cd = _normalize_code(row[6], 2)
                subd_nm = _repair_mojibake(str(row[7] or "").strip())
                ncs_cl_cd = _normalize_code(row[8])
                compe_unit_name = _repair_mojibake(str(row[9] or "").strip())
                compe_unit_level = _normalize_code(row[10])
                unit_elem_name = _repair_mojibake(str(row[12] or "").strip())   # M
                unit_criteria = _repair_mojibake(str(row[14] or "").strip())    # O
                ksa_type_name = _repair_mojibake(str(row[16] or "").strip())    # Q
                ksa_text = _repair_mojibake(str(row[18] or "").strip())          # S
                if not ncs_cl_cd:
                    continue
                code_no = f"{l_cd}{m_cd}{s_cd}" if (l_cd and m_cd and s_cd) else ncs_cl_cd[:6]
                out.append(
                    {
                        "ncs_code_no": code_no,
                        "ncs_cl_cd": ncs_cl_cd,
                        "compe_unit_name": compe_unit_name,
                        "compe_unit_level": compe_unit_level,
                        "ncs_lclass_code": l_cd,
                        "ncs_lclass_name": l_nm,
                        "ncs_mclass_code": m_cd,
                        "ncs_mclass_name": m_nm,
                        "ncs_sclass_code": s_cd,
                        "ncs_sclass_name": s_nm,
                        "ncs_subd_code": subd_cd,
                        "ncs_subd_name": subd_nm,
                        "unit_elem_name": unit_elem_name,
                        "unit_criteria": unit_criteria,
                        "ksa_type_name": ksa_type_name,
                        "ksa_text": ksa_text,
                    }
                )
        wb.close()
    except Exception:
        return []

    _NCS_XLSX_CACHE["ts"] = now
    _NCS_XLSX_CACHE["path"] = path
    _NCS_XLSX_CACHE["items"] = out
    return list(out)


def _units_from_local_xlsx_by_sclass(
    ncs_lclass_code: str,
    ncs_mclass_code: str,
    ncs_sclass_code: str,
    sclass_name: str = "",
    max_items: int = 300,
) -> list[dict[str, Any]]:
    l_cd = str(ncs_lclass_code or "").strip()
    m_cd = str(ncs_mclass_code or "").strip()
    s_cd = str(ncs_sclass_code or "").strip()
    code_no = f"{l_cd}{m_cd}{s_cd}"
    if not (l_cd and m_cd and s_cd):
        return []

    db_path = os.getenv("APP_DB_PATH", "").strip() or _default_app_db_path()
    if ensure_ncs_local_index(db_path=db_path):
        try:
            con = _connect_local_db(db_path)
            cur = con.cursor()
            cur.execute(
                """
                SELECT
                    ncs_cl_cd,
                    compe_unit_name,
                    compe_unit_level,
                    ncs_sclass_name,
                    ncs_subd_code,
                    ncs_subd_name,
                    unit_criteria
                FROM ncs_local_rows
                WHERE code6 = ?
                GROUP BY ncs_cl_cd
                ORDER BY ncs_cl_cd
                LIMIT ?
                """,
                (code_no, int(max_items or 300)),
            )
            rows = cur.fetchall()
            con.close()
            out_db: list[dict[str, Any]] = []
            for r in rows:
                out_db.append(
                    {
                        "ncsClCd": str(r["ncs_cl_cd"] or "").strip(),
                        "compeUnitName": str(r["compe_unit_name"] or "").strip(),
                        "compeUnitLevel": str(r["compe_unit_level"] or "").strip(),
                        "ncsLclasCd": l_cd,
                        "ncsMclasCd": m_cd,
                        "ncsSclasCd": s_cd,
                        "ncsSclasCdnm": str(r["ncs_sclass_name"] or "").strip() or str(sclass_name or "").strip(),
                        "ncsSubdCd": str(r["ncs_subd_code"] or "").strip(),
                        "ncsSubdCdnm": str(r["ncs_subd_name"] or "").strip(),
                        "compeUnitDef": str(r["unit_criteria"] or "").strip(),
                        "score": 1.0,
                        "matched_keywords": [str(r["ncs_sclass_name"] or "").strip() or str(sclass_name or "").strip() or code_no],
                    }
                )
            if out_db:
                return out_db
        except Exception:
            pass

    cache_key = f"units:{code_no}"
    cache_map = _NCS_XLSX_CACHE.setdefault("map", {})
    if cache_key in cache_map:
        return list(cache_map.get(cache_key, []))

    path = os.getenv("NCS_DB_XLSX_PATH", "").strip() or _default_ncs_xlsx_path()
    if not os.path.exists(path):
        return []
    try:
        import openpyxl  # type: ignore
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    s_nm_fallback = str(sclass_name or "").strip()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if idx == 1 or not row or len(row) < 19:
                    continue
                ncs_cl_cd = _normalize_code(row[8]).strip()
                if not ncs_cl_cd or len(ncs_cl_cd) < 6 or ncs_cl_cd[:6] != code_no:
                    continue
                if ncs_cl_cd in seen:
                    continue
                seen.add(ncs_cl_cd)
                out.append(
                    {
                        "ncsClCd": ncs_cl_cd,
                        "compeUnitName": _repair_mojibake(str(row[9] or "").strip()),
                        "compeUnitLevel": _normalize_code(row[10]).strip(),
                        "ncsLclasCd": l_cd,
                        "ncsMclasCd": m_cd,
                        "ncsSclasCd": s_cd,
                        "ncsSclasCdnm": _repair_mojibake(str(row[5] or "").strip()) or s_nm_fallback,
                        "ncsSubdCd": _normalize_code(row[6], 2).strip(),
                        "ncsSubdCdnm": _repair_mojibake(str(row[7] or "").strip()),
                        "compeUnitDef": _repair_mojibake(str(row[14] or "").strip()),
                        "score": 1.0,
                        "matched_keywords": [_repair_mojibake(str(row[5] or "").strip()) or s_nm_fallback or code_no],
                    }
                )
                if len(out) >= max_items:
                    wb.close()
                    cache_map[cache_key] = list(out)
                    return out
        wb.close()
    except Exception:
        return []
    cache_map[cache_key] = list(out)
    return out


def _ksa_from_local_xlsx_by_code(ncs_cl_cd: str, limit: int = 20) -> list[dict[str, str]]:
    code = str(ncs_cl_cd or "").strip()
    if not code:
        return []

    db_path = os.getenv("APP_DB_PATH", "").strip() or _default_app_db_path()
    if ensure_ncs_local_index(db_path=db_path):
        try:
            con = _connect_local_db(db_path)
            cur = con.cursor()
            cur.execute(
                """
                SELECT
                    compe_unit_name,
                    unit_elem_name,
                    unit_criteria,
                    ksa_type_name,
                    ksa_text
                FROM ncs_local_rows
                WHERE ncs_cl_cd = ?
                """,
                (code,),
            )
            rows = cur.fetchall()
            con.close()
            out_db: list[dict[str, str]] = []
            seen_db: set[str] = set()
            for r in rows:
                ksa_text = str(r["ksa_text"] or "").strip()
                fallback = str(r["unit_elem_name"] or "").strip() or str(r["unit_criteria"] or "").strip()
                factor = ksa_text or fallback
                if not factor:
                    continue
                k = re.sub(r"\s+", "", factor)
                if k in seen_db:
                    continue
                seen_db.add(k)
                out_db.append(
                    {
                        "factorName": factor[:120],
                        "factorLevel": "",
                        "compeUnitName": str(r["compe_unit_name"] or "").strip(),
                        "factorSource": "xlsx-qs" if ksa_text else "xlsx-unit",
                    }
                )
                if len(out_db) >= max(1, int(limit or 20)):
                    break
            if out_db:
                return out_db
        except Exception:
            pass

    cache_key = f"ksa:{code}:{int(limit or 20)}"
    cache_map = _NCS_XLSX_CACHE.setdefault("map", {})
    if cache_key in cache_map:
        return list(cache_map.get(cache_key, []))

    path = os.getenv("NCS_DB_XLSX_PATH", "").strip() or _default_ncs_xlsx_path()
    if not os.path.exists(path):
        return []
    try:
        import openpyxl  # type: ignore
    except Exception:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if idx == 1 or not row or len(row) < 19:
                    continue
                row_code = _normalize_code(row[8]).strip()
                if row_code != code:
                    continue
                ksa_text = _repair_mojibake(str(row[18] or "").strip())      # S
                fallback = _repair_mojibake(str(row[12] or "").strip()) or _repair_mojibake(str(row[14] or "").strip())
                factor = ksa_text or fallback
                if not factor:
                    continue
                key = re.sub(r"\s+", "", factor)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "factorName": factor[:120],
                        "factorLevel": "",
                        "compeUnitName": _repair_mojibake(str(row[9] or "").strip()),
                        "factorSource": "xlsx-qs" if ksa_text else "xlsx-unit",
                    }
                )
                if len(out) >= limit:
                    wb.close()
                    cache_map[cache_key] = list(out)
                    return out
        wb.close()
    except Exception:
        return []
    cache_map[cache_key] = list(out)
    return out


def _norm_text(v: str) -> str:
    return re.sub(r"\s+", "", _repair_mojibake(str(v or "")).strip()).lower()


def load_sclass_catalog_from_csv(csv_path: str | None = None, cache_ttl_sec: int = 60 * 30) -> list[dict[str, str]]:
    path = csv_path or os.getenv("NCS_SCLASS_CSV_PATH", "").strip() or _default_sclass_csv_path()
    now = time.time()
    if _SCLASS_CSV_CACHE.get("items") and _SCLASS_CSV_CACHE.get("path") == path:
        if (now - float(_SCLASS_CSV_CACHE.get("ts", 0.0))) < cache_ttl_sec:
            return list(_SCLASS_CSV_CACHE["items"])

    if not os.path.exists(path):
        return []

    out: list[dict[str, str]] = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code_no = str((row or {}).get("NCS_CODE_NO", "")).strip()
                l_cd = str((row or {}).get("NCS_LCLAS_CD", "")).strip()
                m_cd = str((row or {}).get("NCS_MCLAS_CD", "")).strip()
                s_cd = str((row or {}).get("NCS_SCLAS_CD", "")).strip()
                s_nm = _repair_mojibake(str((row or {}).get("NCS_SCLAS_CDNM", "")).strip())
                l_nm = _repair_mojibake(str((row or {}).get("NCS_LCLAS_CDNM", "")).strip())
                m_nm = _repair_mojibake(str((row or {}).get("NCS_MCLAS_CDNM", "")).strip())
                if not (code_no and l_cd and m_cd and s_cd and s_nm):
                    continue
                key = (code_no, l_cd, m_cd, s_cd)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "ncs_code_no": code_no,
                        "ncs_lclass_code": l_cd,
                        "ncs_lclass_name": l_nm,
                        "ncs_mclass_code": m_cd,
                        "ncs_mclass_name": m_nm,
                        "ncs_sclass_code": s_cd,
                        "ncs_sclass_name": s_nm,
                    }
                )
    except Exception:
        return []

    _SCLASS_CSV_CACHE["ts"] = now
    _SCLASS_CSV_CACHE["path"] = path
    _SCLASS_CSV_CACHE["items"] = out
    return list(out)


def load_sclass_synonym_dictionary(
    dict_path: str | None = None,
    cache_ttl_sec: int = 60 * 30,
) -> dict[str, dict[str, list[str]]]:
    path = (
        dict_path
        or os.getenv("NCS_SCLASS_SYNONYM_PATH", "").strip()
        or _default_sclass_synonym_path()
    )
    now = time.time()

    if _SCLASS_SYNONYM_CACHE.get("path") == path and _SCLASS_SYNONYM_CACHE.get("items"):
        if (now - float(_SCLASS_SYNONYM_CACHE.get("ts", 0.0))) < cache_ttl_sec:
            cached = _SCLASS_SYNONYM_CACHE.get("items", {})
            return {
                "by_code_no": dict(cached.get("by_code_no", {})),
                "by_name": dict(cached.get("by_name", {})),
            }

    default_pack = {"by_code_no": {}, "by_name": {}}
    if not os.path.exists(path):
        return default_pack

    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return default_pack

    by_code_raw = obj.get("by_code_no", {}) if isinstance(obj, dict) else {}
    by_name_raw = obj.get("by_name", {}) if isinstance(obj, dict) else {}

    by_code_no: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}

    if isinstance(by_code_raw, dict):
        for code_no, vals in by_code_raw.items():
            code = str(code_no or "").strip()
            if not code or not isinstance(vals, list):
                continue
            terms = [str(v).strip() for v in vals if str(v or "").strip()]
            if terms:
                by_code_no[code] = terms

    if isinstance(by_name_raw, dict):
        for name, vals in by_name_raw.items():
            nm = _norm_text(str(name or ""))
            if not nm or not isinstance(vals, list):
                continue
            terms = [str(v).strip() for v in vals if str(v or "").strip()]
            if terms:
                by_name[nm] = terms

    pack = {"by_code_no": by_code_no, "by_name": by_name}
    _SCLASS_SYNONYM_CACHE["ts"] = now
    _SCLASS_SYNONYM_CACHE["path"] = path
    _SCLASS_SYNONYM_CACHE["items"] = pack
    return {
        "by_code_no": dict(pack["by_code_no"]),
        "by_name": dict(pack["by_name"]),
    }


def ai_pick_sclass_from_csv(
    small_categories: list[str],
    subcategory_text: str,
    jd_text: str,
    max_items: int = 6,
    csv_path: str | None = None,
) -> list[dict[str, Any]]:
    catalog = load_sclass_catalog_from_csv(csv_path=csv_path)
    if not catalog:
        return []

    terms: list[str] = []
    for t in (small_categories or []):
        s = _repair_mojibake(str(t or "")).strip()
        if s and s not in terms:
            terms.append(s)
    for t in re.findall(r"[\uac00-\ud7a3]{2,12}", _repair_mojibake(subcategory_text or "")):
        if t and t not in terms:
            terms.append(t)
    for t in re.findall(r"[\uac00-\ud7a3]{2,12}", _repair_mojibake(jd_text or ""))[:50]:
        if t and t not in terms:
            terms.append(t)

    term_norm = {_norm_text(t) for t in terms if _norm_text(t)}
    if not term_norm:
        return []

    out: list[dict[str, Any]] = []
    seen = set()
    for row in catalog:
        s_nm = str(row.get("ncs_sclass_name", "")).strip()
        s_n = _norm_text(s_nm)
        if not s_n:
            continue
        exact = s_n in term_norm
        partial = any((s_n in t or t in s_n) for t in term_norm)
        if not (exact or partial):
            continue
        key = (
            str(row.get("ncs_code_no", "")).strip(),
            str(row.get("ncs_lclass_code", "")).strip(),
            str(row.get("ncs_mclass_code", "")).strip(),
            str(row.get("ncs_sclass_code", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "sclass_name": s_nm,
                "ncs_sclass_code": str(row.get("ncs_sclass_code", "")).strip(),
                "ncs_lclass_code": str(row.get("ncs_lclass_code", "")).strip(),
                "ncs_mclass_code": str(row.get("ncs_mclass_code", "")).strip(),
                "ncs_code_no": str(row.get("ncs_code_no", "")).strip(),
                "confidence": 1.0 if exact else 0.8,
                "evidence": "csv-ncs_sclass_cdnm-match",
            }
        )
        if len(out) >= max_items:
            break
    return out


def _hrdk_base_url() -> str:
    base = os.getenv("NCS_HRDK_BASE_URL", "https://apis.data.go.kr/B490007/hrdkapi").strip()
    return base.rstrip("/")


def _try_get_json(url: str, params: dict[str, Any], timeout: float = 12.0) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, params=params)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _extract_hrdk_items(obj: dict[str, Any]) -> list[dict[str, Any]]:
    body = obj.get("body") or ((obj.get("response") or {}).get("body") or {})
    items = (body.get("items") or {}).get("item")
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        return [items]
    return []


def _hrdk_call(op: str, extra: dict[str, Any]) -> list[dict[str, Any]]:
    base = _hrdk_base_url()
    key = settings.ncs_key()
    if not key:
        return []

    page_no = (os.getenv("NCS_PAGE_NO", "1") or "1").strip()
    num_of_rows = (os.getenv("NCS_NUM_OF_ROWS", "10") or "10").strip()
    usg_yn = (os.getenv("NCS_USG_YN", "N") or "N").strip().upper()
    ncs_degr = (os.getenv("NCS_DEGR", "22") or "22").strip()

    key_vars = [key]
    enc = quote(key, safe="")
    if enc != key:
        key_vars.append(enc)

    for key_name in ("serviceKey", "ServiceKey"):
        for kval in key_vars:
            params = {
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "USG_YN": usg_yn,
                "NCS_DEGR": ncs_degr,
                "returnType": "json",
                key_name: kval,
            }
            params.update(extra or {})
            obj = _try_get_json(f"{base}/{op}", params=params, timeout=8.0)
            if not obj:
                continue
            header = obj.get("header") or ((obj.get("response") or {}).get("header") or {})
            rc = str(header.get("resultCode", "")).strip()
            if rc and rc not in {"200", "00", "03"}:
                continue
            rows = _extract_hrdk_items(obj)
            if rows:
                return rows
    return []


def fetch_ncs_units_hrdk_by_sclass_code(
    ncs_lclass_code: str,
    ncs_mclass_code: str,
    ncs_sclass_code: str,
    sclass_name: str = "",
) -> list[dict[str, Any]]:
    l_cd = str(ncs_lclass_code or "").strip()
    m_cd = str(ncs_mclass_code or "").strip()
    s_cd = str(ncs_sclass_code or "").strip()
    s_nm = str(sclass_name or "").strip()
    if not (l_cd and m_cd and s_cd):
        return []

    # Local-first: NCS_DB.xlsx (I column prefix by code_no, e.g., 020201xxxx)
    local_rows = _units_from_local_xlsx_by_sclass(
        ncs_lclass_code=l_cd,
        ncs_mclass_code=m_cd,
        ncs_sclass_code=s_cd,
        sclass_name=s_nm,
    )
    if local_rows:
        return local_rows

    # NCS003 validation
    s_rows = _hrdk_call(
        "NCS003",
        {"NCS_LCLAS_CD": l_cd, "NCS_MCLAS_CD": m_cd, "NCS_SCLAS_CD": s_cd},
    )
    if not s_rows:
        return []
    if not s_nm:
        s_nm = str(s_rows[0].get("NCS_SCLAS_CDNM", "")).strip()

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    subd_rows = _hrdk_call(
        "NCS004",
        {"NCS_LCLAS_CD": l_cd, "NCS_MCLAS_CD": m_cd, "NCS_SCLAS_CD": s_cd},
    )
    for subd in subd_rows:
        subd_cd = str(subd.get("NCS_SUBD_CD", "")).strip()
        subd_nm = str(subd.get("NCS_SUBD_CDNM", "")).strip()
        if not subd_cd:
            continue
        units = _hrdk_call(
            "NCS005",
            {
                "NCS_LCLAS_CD": l_cd,
                "NCS_MCLAS_CD": m_cd,
                "NCS_SCLAS_CD": s_cd,
                "NCS_SUBD_CD": subd_cd,
            },
        )
        for u in units:
            cl = str(u.get("NCS_CL_CD", "")).strip()
            if not cl or cl in seen:
                continue
            seen.add(cl)
            out.append(
                {
                    "ncsClCd": cl,
                    "compeUnitName": str(u.get("COMPE_UNIT_NAME", "")).strip(),
                    "compeUnitLevel": str(u.get("COMPE_UNIT_LEVEL", "")).strip(),
                    "ncsLclasCd": l_cd,
                    "ncsMclasCd": m_cd,
                    "ncsSclasCd": s_cd,
                    "ncsSclasCdnm": str(u.get("NCS_SCLAS_CDNM", "")).strip() or s_nm,
                    "ncsSubdCd": subd_cd,
                    "ncsSubdCdnm": str(u.get("NCS_SUBD_CDNM", "")).strip() or subd_nm,
                    "compeUnitDef": str(u.get("COMPE_UNIT_DEF", "")).strip(),
                    "score": 1.0,
                    "matched_keywords": [s_nm or s_cd],
                }
            )
    return out


def fetch_ncs_units_hrdk_by_verified_sclass(verified_sclass: list[dict[str, Any]], max_sclass: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in (verified_sclass or [])[:max_sclass]:
        l_cd = str((v or {}).get("ncs_lclass_code", "")).strip()
        m_cd = str((v or {}).get("ncs_mclass_code", "")).strip()
        s_cd = str((v or {}).get("ncs_sclass_code", "")).strip()
        code_no = str((v or {}).get("ncs_code_no", "")).strip()
        if (not l_cd or not m_cd or not s_cd) and len(code_no) >= 6 and code_no.isdigit():
            l_cd, m_cd, s_cd = code_no[:2], code_no[2:4], code_no[4:6]
        s_nm = str((v or {}).get("sclass_name", "")).strip()
        items = fetch_ncs_units_hrdk_by_sclass_code(
            ncs_lclass_code=l_cd,
            ncs_mclass_code=m_cd,
            ncs_sclass_code=s_cd,
            sclass_name=s_nm,
        )
        for it in items:
            code = str(it.get("ncsClCd", "")).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(it)
    return out


def fetch_ncs_units_hrdk_by_sclass_names(sclass_names: list[str], max_sclass: int = 6) -> list[dict[str, Any]]:
    csv_hits = ai_pick_sclass_from_csv(
        small_categories=list(sclass_names or []),
        subcategory_text=" ".join(sclass_names or []),
        jd_text="",
        max_items=max_sclass,
    )
    out = fetch_ncs_units_hrdk_by_verified_sclass(csv_hits, max_sclass=max_sclass)
    if out:
        return out

    # Fallback: keyword search from NCS007 and then resolve tuple
    hits: list[dict[str, Any]] = []
    seen = set()
    for term in (sclass_names or [])[:max_sclass]:
        rows = _hrdk_call("NCS007", {"LVL": "4", "SWRD": term, "SNUM": "1", "ENUM": "100"})
        for r in rows:
            l_cd = str(r.get("NCS_LCLAS_CD", "")).strip()
            m_cd = str(r.get("NCS_MCLAS_CD", "")).strip()
            s_cd = str(r.get("NCS_SCLAS_CD", "")).strip()
            if not (l_cd and m_cd and s_cd):
                continue
            key = (l_cd, m_cd, s_cd)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "sclass_name": str(r.get("NCS_SCLAS_CDNM", "")).strip() or str(term).strip(),
                    "ncs_lclass_code": l_cd,
                    "ncs_mclass_code": m_cd,
                    "ncs_sclass_code": s_cd,
                    "confidence": 0.8,
                    "evidence": "ncs007-keyword",
                }
            )
    return fetch_ncs_units_hrdk_by_verified_sclass(hits, max_sclass=max_sclass)


def fetch_ncs_units_hrdk_by_keywords(keywords: list[str], max_items: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for kw in (keywords or [])[:10]:
        rows = _hrdk_call("NCS007", {"LVL": "6", "SWRD": str(kw).strip(), "SNUM": "1", "ENUM": "80"})
        for r in rows:
            cl = str(r.get("NCS_CL_CD", "")).strip()
            if not cl or cl in seen:
                continue
            seen.add(cl)
            out.append(
                {
                    "ncsClCd": cl,
                    "compeUnitName": str(r.get("COMPE_UNIT_NAME", "")).strip(),
                    "compeUnitLevel": str(r.get("COMPE_UNIT_LEVEL", "")).strip(),
                    "ncsSclasCdnm": str(r.get("NCS_SCLAS_CDNM", "")).strip(),
                    "ncsSubdCdnm": str(r.get("NCS_SUBD_CDNM", "")).strip(),
                    "compeUnitDef": str(r.get("COMPE_UNIT_DEF", "")).strip(),
                    "score": 0.7,
                    "matched_keywords": [str(kw).strip()],
                }
            )
            if len(out) >= max_items:
                return out
    return out


def fetch_ncs_units_hrdk_by_cl_codes(code_rows: list[dict[str, Any]], max_items: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for row in (code_rows or [])[: max_items * 2]:
        code = str(row.get("ncsClCd", row.get("ncs_cl_cd", ""))).strip()
        if not code or code in seen:
            continue
        rows = _hrdk_call("NCS007", {"LVL": "6", "SWRD": code, "SNUM": "1", "ENUM": "20"})
        picked = None
        for r in rows:
            if str(r.get("NCS_CL_CD", "")).strip() == code:
                picked = r
                break
        if not picked and rows:
            picked = rows[0]
        if not picked:
            continue
        seen.add(code)
        out.append(
            {
                "ncsClCd": code,
                "compeUnitName": str(picked.get("COMPE_UNIT_NAME", "")).strip(),
                "compeUnitLevel": str(picked.get("COMPE_UNIT_LEVEL", "")).strip(),
                "ncsSclasCdnm": str(picked.get("NCS_SCLAS_CDNM", "")).strip(),
                "ncsSubdCdnm": str(picked.get("NCS_SUBD_CDNM", "")).strip(),
                "compeUnitDef": str(picked.get("COMPE_UNIT_DEF", "")).strip(),
                "score": 0.9,
                "matched_keywords": [code],
            }
        )
        if len(out) >= max_items:
            break
    return out


def fetch_ncs_ksa_by_units(
    ncs_matches: list[dict[str, Any]],
    max_units: int = 5,
    max_factors_per_unit: int = 3,
    use_ncs007_fallback: bool | None = None,
) -> list[dict[str, Any]]:
    # When the prepared NCS_MCP service is configured, it is the authoritative
    # KSA source. Do not silently mix in the legacy XLSX/HRDK fallbacks in
    # production, because that hides serving DB or classification failures.
    if settings.ncs_mcp_endpoint():
        mcp_rows = get_ksa_by_units(
            list(ncs_matches or [])[: max(1, int(max_units or 5))],
            max_factors_per_unit=max(1, int(max_factors_per_unit or 3)),
        )
        if not mcp_rows:
            raise NcsMcpError("NCS MCP returned no official KSA rows")
        return mcp_rows

    def _derive_factors_from_definition(
        compe_unit_name: str,
        compe_unit_def: str,
        limit: int,
    ) -> list[dict[str, str]]:
        txt = _repair_mojibake(str(compe_unit_def or "")).strip()
        if not txt:
            return []

        stop = {
            "능력", "단위", "능력단위", "직무", "업무", "수행", "관련", "활동", "기준", "절차",
            "활용", "이해", "관리", "처리", "지원", "운영", "계획", "실행", "평가",
        }
        out_rows: list[dict[str, str]] = []
        seen_local: set[str] = set()

        # phrase candidates first, then token candidates.
        phrases = re.split(
            r"[.;:\n]|,\s*|(?:\s*및\s*)|(?:\s*또는\s*)|하고|하며|하여",
            txt,
        )
        for p in phrases:
            v = re.sub(r"\s+", " ", p).strip()
            if len(v) < 3:
                continue
            v = re.sub(r"^(하는|하여|위한|위해)\s+", "", v).strip()
            v = re.sub(r"\s*능력$", "", v).strip()
            v = re.sub(r"(하는|하며|하고|하여|한다|된다)$", "", v).strip()
            if not v:
                continue
            n = re.sub(r"\s+", "", v)
            if n in seen_local or n in stop:
                continue
            seen_local.add(n)
            out_rows.append(
                {
                    "factorName": v[:40],
                    "factorLevel": "",
                    "compeUnitName": compe_unit_name,
                    "factorSource": "definition",
                }
            )
            if len(out_rows) >= limit:
                return out_rows

        token_rows: list[str] = []
        for tok in re.findall(r"[\uac00-\ud7a3]{2,12}", txt):
            base = re.sub(r"(을|를|이|가|은|는|의|에|로|과|와)$", "", tok).strip()
            base = re.sub(r"(하고|하며|하여|하는|한다|된다)$", "", base).strip()
            if len(base) < 2 or base in stop:
                continue
            token_rows.append(base)
        for tok, _ in Counter(token_rows).most_common(40):
            if tok in seen_local:
                continue
            seen_local.add(tok)
            out_rows.append(
                {
                    "factorName": tok,
                    "factorLevel": "",
                    "compeUnitName": compe_unit_name,
                    "factorSource": "definition",
                }
            )
            if len(out_rows) >= limit:
                break
        return out_rows

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    max_units = max(1, int(max_units or 5))
    max_factors_per_unit = max(1, int(max_factors_per_unit or 3))
    use_ncs007 = True if use_ncs007_fallback is None else bool(use_ncs007_fallback)

    for m in (ncs_matches or [])[:max_units]:
        code = str(m.get("ncsClCd", "")).strip()
        if not code:
            continue
        compe_name = str(m.get("compeUnitName", "")).strip()
        compe_def = str(m.get("compeUnitDef", "")).strip()

        cached = _KSA_FACTOR_CACHE_BY_CODE.get(code)
        if cached is None:
            factor_rows: list[dict[str, str]] = []
            local_seen: set[str] = set()

            # 0) Local-first: NCS_DB.xlsx Q/S-based KSA rows for exact NCS_CL_CD.
            xlsx_rows = _ksa_from_local_xlsx_by_code(code, limit=max_factors_per_unit * 4)
            for r in xlsx_rows:
                fact = str(r.get("factorName", "")).strip()
                if not fact or fact in local_seen:
                    continue
                local_seen.add(fact)
                factor_rows.append(
                    {
                        "factorName": fact,
                        "factorLevel": str(r.get("factorLevel", "")).strip(),
                        "compeUnitName": str(r.get("compeUnitName", "")).strip() or compe_name,
                        "factorSource": str(r.get("factorSource", "")).strip() or "xlsx-qs",
                    }
                )
                if len(factor_rows) >= max_factors_per_unit * 4:
                    break

            # 1) Primary: NCS006 capability factors.
            if not factor_rows:
                for no in range(1, max(4, max_factors_per_unit * 2) + 1):
                    rows = _hrdk_call("NCS006", {"NCS_CL_CD": code, "COMPE_UNIT_FACTR_NO": str(no)})
                    for r in rows:
                        fact = str(r.get("COMPE_UNIT_FACTR_NAME", "")).strip()
                        if not fact or fact in local_seen:
                            continue
                        local_seen.add(fact)
                        factor_rows.append(
                            {
                                "factorName": fact,
                                "factorLevel": str(r.get("COMPE_UNIT_FACTR_LEVEL", "")).strip(),
                                "compeUnitName": str(r.get("COMPE_UNIT_NAME", "")).strip() or compe_name,
                                "factorSource": "ncs006",
                            }
                        )
                    if len(factor_rows) >= max_factors_per_unit * 4:
                        break

            # 2) Secondary: NCS007 keyword search by NCS code.
            if not factor_rows and use_ncs007:
                rows = _hrdk_call("NCS007", {"LVL": "6", "SWRD": code, "SNUM": "1", "ENUM": "120"})
                for r in rows:
                    if str(r.get("NCS_CL_CD", "")).strip() != code:
                        continue
                    fact = str(r.get("COMPE_UNIT_FACTR_NAME", "")).strip()
                    if not fact or fact in local_seen:
                        continue
                    local_seen.add(fact)
                    factor_rows.append(
                        {
                            "factorName": fact,
                            "factorLevel": str(r.get("COMPE_UNIT_FACTR_LEVEL", "")).strip(),
                            "compeUnitName": str(r.get("COMPE_UNIT_NAME", "")).strip() or compe_name,
                            "factorSource": "ncs007",
                        }
                    )
                    if len(factor_rows) >= max_factors_per_unit * 4:
                        break

            # 3) Last fallback: derive factor candidates from COMPE_UNIT_DEF text.
            if not factor_rows:
                factor_rows = _derive_factors_from_definition(
                    compe_unit_name=compe_name,
                    compe_unit_def=compe_def,
                    limit=max(max_factors_per_unit * 2, 4),
                )

            _KSA_FACTOR_CACHE_BY_CODE[code] = factor_rows
            cached = factor_rows

        unit_count = 0
        for f in (cached or []):
            fact = str(f.get("factorName", "")).strip()
            if not fact:
                continue
            key = (code, fact)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": str(f.get("compeUnitName", "")).strip() or compe_name,
                    "factorName": fact,
                    "factorLevel": str(f.get("factorLevel", "")).strip(),
                    "factorSource": str(f.get("factorSource", "")).strip() or "unknown",
                }
            )
            unit_count += 1
            if unit_count >= max_factors_per_unit:
                break
    return out


def _compact_text_for_tfidf(text: str) -> str:
    cleaned = _repair_mojibake(str(text or ""))
    cleaned = re.sub(r"\s+", "", cleaned).lower()
    return re.sub(r"[^0-9a-z\uac00-\ud7a3]", "", cleaned)


def _char_ngram_tf(text: str, ngram_min: int = 2, ngram_max: int = 4) -> Counter[str]:
    src = _compact_text_for_tfidf(text)
    if not src:
        return Counter()

    grams: list[str] = []
    lo = max(1, int(ngram_min or 2))
    hi = max(lo, int(ngram_max or 4))
    for n in range(lo, hi + 1):
        if len(src) < n:
            continue
        for i in range(0, len(src) - n + 1):
            grams.append(src[i : i + n])

    if not grams:
        grams = [src]
    return Counter(grams)


def rank_ksa_factors_by_query(
    ksa_rows: list[dict[str, Any]],
    query_text: str,
    unit_scores: dict[str, float] | None = None,
    target_count: int = 12,
    per_unit_limit: int = 2,
    similarity_weight: float = 0.75,
    unit_weight: float = 0.25,
    ngram_min: int = 2,
    ngram_max: int = 4,
) -> list[dict[str, Any]]:
    rows = [dict(x) for x in (ksa_rows or []) if isinstance(x, dict)]
    if not rows:
        return []

    keep_n = max(1, min(40, int(target_count or 12)))
    per_unit_cap = max(1, min(6, int(per_unit_limit or 2)))

    sim_w = max(0.0, float(similarity_weight or 0.0))
    unit_w = max(0.0, float(unit_weight or 0.0))
    if sim_w <= 0 and unit_w <= 0:
        sim_w = 1.0
    total_w = sim_w + unit_w
    sim_w = sim_w / total_w
    unit_w = unit_w / total_w

    unit_raw_scores: dict[str, float] = {}
    if unit_scores:
        for code, v in unit_scores.items():
            c = str(code or "").strip()
            if not c:
                continue
            try:
                unit_raw_scores[c] = float(v or 0.0)
            except Exception:
                unit_raw_scores[c] = 0.0

    deduped_rows: list[dict[str, Any]] = []
    seen_factors: set[tuple[str, str]] = set()
    for row in rows:
        code = str(row.get("ncsClCd", "")).strip()
        factor = str(row.get("factorName", "")).strip()
        if not code or not factor:
            continue
        dedup_key = (code, re.sub(r"\s+", "", factor).lower())
        if dedup_key in seen_factors:
            continue
        seen_factors.add(dedup_key)
        deduped_rows.append(row)
    if not deduped_rows:
        return []

    if not unit_raw_scores:
        for row in deduped_rows:
            code = str(row.get("ncsClCd", "")).strip()
            if not code:
                continue
            try:
                base_score = float(row.get("score", 1.0) or 1.0)
            except Exception:
                base_score = 1.0
            prev = unit_raw_scores.get(code)
            if prev is None or base_score > prev:
                unit_raw_scores[code] = base_score

    score_values = list(unit_raw_scores.values()) or [1.0]
    score_min = min(score_values)
    score_max = max(score_values)

    def _norm_unit_score(code: str) -> float:
        v = float(unit_raw_scores.get(code, 0.0))
        if score_max > score_min:
            return (v - score_min) / (score_max - score_min)
        if 0.0 <= v <= 1.0:
            return v
        return 1.0 if v > 0 else 0.0

    query_tf = _char_ngram_tf(query_text, ngram_min=ngram_min, ngram_max=ngram_max)
    doc_tfs: list[Counter[str]] = []
    for row in deduped_rows:
        text = f"{str(row.get('factorName', '')).strip()} {str(row.get('compeUnitName', '')).strip()}"
        doc_tfs.append(_char_ngram_tf(text, ngram_min=ngram_min, ngram_max=ngram_max))

    similarity_scores: list[float] = [0.0] * len(deduped_rows)
    if query_tf and any(doc_tfs):
        df: Counter[str] = Counter()
        for tf in doc_tfs:
            df.update(tf.keys())

        doc_count = max(1, len(doc_tfs))
        idf = {term: (math.log((doc_count + 1) / (freq + 1)) + 1.0) for term, freq in df.items()}

        query_w: dict[str, float] = {}
        for term, cnt in query_tf.items():
            if term not in idf:
                continue
            query_w[term] = (1.0 + math.log(max(1, cnt))) * idf[term]
        query_norm = math.sqrt(sum(v * v for v in query_w.values())) if query_w else 0.0

        if query_norm > 0:
            for i, tf in enumerate(doc_tfs):
                if not tf:
                    continue
                dot = 0.0
                doc_norm_sq = 0.0
                for term, cnt in tf.items():
                    weight = (1.0 + math.log(max(1, cnt))) * idf.get(term, 0.0)
                    if weight <= 0:
                        continue
                    doc_norm_sq += weight * weight
                    qv = query_w.get(term)
                    if qv:
                        dot += qv * weight
                doc_norm = math.sqrt(doc_norm_sq)
                if doc_norm > 0 and dot > 0:
                    similarity_scores[i] = dot / (query_norm * doc_norm)

    scored_rows: list[dict[str, Any]] = []
    for i, row in enumerate(deduped_rows):
        code = str(row.get("ncsClCd", "")).strip()
        sim = float(similarity_scores[i])
        unit_score_norm = _norm_unit_score(code)
        final_score = (sim_w * sim) + (unit_w * unit_score_norm)
        merged = dict(row)
        merged["__idx"] = i
        merged["similarityScore"] = round(sim, 6)
        merged["unitScore"] = round(unit_score_norm, 6)
        merged["finalScore"] = round(final_score, 6)
        scored_rows.append(merged)

    scored_rows.sort(
        key=lambda x: (
            float(x.get("finalScore", 0.0) or 0.0),
            float(x.get("similarityScore", 0.0) or 0.0),
            float(x.get("unitScore", 0.0) or 0.0),
            -int(x.get("__idx", 0) or 0),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    per_unit_count: dict[str, int] = {}
    for row in scored_rows:
        code = str(row.get("ncsClCd", "")).strip()
        if not code:
            continue
        if per_unit_count.get(code, 0) >= per_unit_cap:
            continue
        row.pop("__idx", None)
        selected.append(row)
        per_unit_count[code] = per_unit_count.get(code, 0) + 1
        if len(selected) >= keep_n:
            break
    return selected


def fetch_ncs_ksa_by_sclass_code(
    ncs_lclass_code: str,
    ncs_mclass_code: str,
    ncs_sclass_code: str,
    sclass_name: str = "",
    max_units: int = 80,
) -> dict[str, Any]:
    units = fetch_ncs_units_hrdk_by_sclass_code(
        ncs_lclass_code=ncs_lclass_code,
        ncs_mclass_code=ncs_mclass_code,
        ncs_sclass_code=ncs_sclass_code,
        sclass_name=sclass_name,
    )
    limited = units[: max(1, int(max_units or 80))]
    ksa = fetch_ncs_ksa_by_units(ncs_matches=limited, max_units=len(limited))
    return {"units": limited, "ksa": ksa}


def resolve_sclass_candidates_with_catalog(
    candidates: list[dict[str, Any]],
    fallback_terms: list[str] | None = None,
    max_terms: int = 8,
) -> list[dict[str, Any]]:
    catalog = load_sclass_catalog_from_csv()
    if not catalog:
        return []

    terms: list[str] = []
    for c in (candidates or []):
        nm = str((c or {}).get("sclass_name", "")).strip()
        if nm and nm not in terms:
            terms.append(nm)
    for t in (fallback_terms or []):
        s = str(t).strip()
        if s and s not in terms:
            terms.append(s)

    out: list[dict[str, Any]] = []
    seen = set()
    for term in terms[:max_terms]:
        q = _norm_text(term)
        best = None
        best_score = 0.0
        for row in catalog:
            nm = str(row.get("ncs_sclass_name", "")).strip()
            n = _norm_text(nm)
            if not n:
                continue
            if q == n:
                score = 1.0
            elif q and (q in n or n in q):
                score = 0.86
            else:
                score = SequenceMatcher(None, q, n).ratio()
            if score > best_score:
                best_score = score
                best = row
        if not best or best_score < 0.62:
            continue
        key = (
            str(best.get("ncs_lclass_code", "")).strip(),
            str(best.get("ncs_mclass_code", "")).strip(),
            str(best.get("ncs_sclass_code", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "sclass_name": str(best.get("ncs_sclass_name", "")).strip(),
                "ncs_sclass_code": key[2],
                "ncs_lclass_code": key[0],
                "ncs_mclass_code": key[1],
                "ncs_code_no": str(best.get("ncs_code_no", "")).strip(),
                "confidence": float(best_score),
                "evidence": f"catalog-fuzzy:{term}",
            }
        )
    return out


def verify_sclass_candidates_with_ncs_api(candidates: list[dict[str, Any]], max_terms: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for c in (candidates or [])[:max_terms]:
        l_cd = str((c or {}).get("ncs_lclass_code", "")).strip()
        m_cd = str((c or {}).get("ncs_mclass_code", "")).strip()
        s_cd = str((c or {}).get("ncs_sclass_code", "")).strip()
        code_no = str((c or {}).get("ncs_code_no", "")).strip()
        if (not l_cd or not m_cd or not s_cd) and len(code_no) >= 6 and code_no.isdigit():
            l_cd, m_cd, s_cd = code_no[:2], code_no[2:4], code_no[4:6]
        if not (l_cd and m_cd and s_cd):
            continue
        rows = _hrdk_call(
            "NCS003",
            {"NCS_LCLAS_CD": l_cd, "NCS_MCLAS_CD": m_cd, "NCS_SCLAS_CD": s_cd},
        )
        if not rows:
            continue
        key = (l_cd, m_cd, s_cd)
        if key in seen:
            continue
        seen.add(key)
        s_nm = str(rows[0].get("NCS_SCLAS_CDNM", "")).strip() or str((c or {}).get("sclass_name", "")).strip()
        out.append(
            {
                "sclass_name": s_nm,
                "ncs_sclass_code": s_cd,
                "ncs_lclass_code": l_cd,
                "ncs_mclass_code": m_cd,
                "ncs_code_no": code_no,
                "confidence": float((c or {}).get("confidence", 1.0) or 1.0),
                "evidence": "ncs003-verified",
            }
        )
    return out


def ai_extract_sclass_candidates(
    subcategory_text: str,
    jd_text: str,
    seed_terms: list[str] | None = None,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    return resolve_sclass_candidates_with_catalog(
        candidates=[{"sclass_name": t, "confidence": 0.7} for t in (seed_terms or []) if str(t).strip()],
        fallback_terms=re.findall(r"[\uac00-\ud7a3]{2,12}", _repair_mojibake(subcategory_text or "")),
        max_terms=max_items,
    )


def ai_extract_ncs_cl_codes(seed_terms: list[str], jd_text: str, max_items: int = 8) -> list[dict[str, Any]]:
    text = " ".join([str(x) for x in (seed_terms or [])]) + " " + str(jd_text or "")
    candidates = re.findall(r"\b\d{8,12}\b", text)
    out: list[dict[str, Any]] = []
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        out.append({"ncsClCd": c, "confidence": 0.8})
        if len(out) >= max_items:
            break
    return out


def review_ocr_terms_with_openai(terms: list[str], jd_text: str) -> list[str]:
    out: list[str] = []
    for t in (terms or []):
        s = _repair_mojibake(str(t or "")).strip()
        if not s or s in out:
            continue
        out.append(s)
        if len(out) >= 20:
            break
    return out


def build_notice_context_from_jd(jd_text: str, notice_text: str = "", max_chars: int = 5000) -> str:
    note = _repair_mojibake(str(notice_text or "")).strip()
    if not note:
        return ""
    return note[: max(200, int(max_chars or 5000))]


def build_ncs_context_pack(
    jd_text: str,
    notice_text: str,
    ncs_items: list[dict[str, Any]],
    ncs_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "jd_preview": _repair_mojibake(jd_text or "")[:1200],
        "notice_preview": _repair_mojibake(notice_text or "")[:1200],
        "ncs_item_count": len(ncs_items or []),
        "ncs_match_count": len(ncs_matches or []),
        "top_ncs": [
            {
                "ncsClCd": str(x.get("ncsClCd", "")).strip(),
                "compeUnitName": str(x.get("compeUnitName", "")).strip(),
                "compeUnitDef": str(x.get("compeUnitDef", "")).strip()[:500],
            }
            for x in (ncs_matches or [])[:8]
        ],
    }


def diagnose_ncs_hrdk() -> dict[str, Any]:
    key = settings.ncs_key()
    if not key:
        return {"ok": False, "message": "NCS key is missing.", "endpoint": _hrdk_base_url(), "samples": []}

    base = _hrdk_base_url()
    samples: list[dict[str, Any]] = []
    for key_name in ("serviceKey", "ServiceKey"):
        for kval in (key, quote(key, safe="")):
            params = {"pageNo": "1", "numOfRows": "3", "returnType": "json", key_name: kval}
            try:
                with httpx.Client(timeout=10.0) as client:
                    r = client.get(f"{base}/NCS001", params=params)
                row: dict[str, Any] = {"key_name": key_name, "status": r.status_code, "preview": (r.text or "")[:300]}
                if r.status_code == 200:
                    try:
                        obj = r.json()
                    except Exception:
                        obj = {}
                    rows = _extract_hrdk_items(obj) if obj else []
                    row["count"] = len(rows)
                    header = obj.get("header") or ((obj.get("response") or {}).get("header") or {})
                    row["resultCode"] = str(header.get("resultCode", ""))
                    row["resultMsg"] = str(header.get("resultMsg", ""))
                    if rows:
                        return {"ok": True, "message": "HRDK NCS API is reachable.", "endpoint": base, "samples": samples + [row]}
                samples.append(row)
            except Exception as e:
                samples.append({"key_name": key_name, "status": None, "error": str(e)})
    return {"ok": False, "message": "HRDK NCS API call failed.", "endpoint": base, "samples": samples}


def diagnose_ncs_v18_flow(sample_job_cd: str = "02020101") -> dict[str, Any]:
    return {
        "ok": False,
        "message": "V1.8 flow is not used in this pipeline. Use HRDK /NCS003~/NCS005 and optional /NCS006.",
        "steps": [{"sample_job_cd": sample_job_cd}],
    }


def fetch_ncs_units_v18_by_sclass(ncs_sclass_code: str, sclass_name: str = "", max_items: int = 50) -> list[dict[str, Any]]:
    _ = (ncs_sclass_code, sclass_name, max_items)
    return []






