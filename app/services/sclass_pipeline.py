from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from app.services.jd_strategy import (
    ai_pick_sclass_from_csv,
    extract_small_categories_from_jd,
    infer_sclass_candidates_from_text_catalog,
    infer_sclass_candidates_reverse_dictionary,
    lookup_ncs_codes_by_sclass,
)
from app.services.ncs_category_extractor import extract_small_category

_START_ANCHORS = ("분류체계", "ncs분류체계", "직무분류체계", "직무분류")
_END_ANCHORS = ("직무수행내용", "직무수행", "주요업무", "능력단위", "필요지식", "필요기술")
_STRUCTURAL_TERMS = {
    "대분류",
    "중분류",
    "소분류",
    "세분류",
    "분류체계",
    "ncs분류체계",
    "직무분류체계",
    "능력단위",
    "직무수행내용",
    "직무수행",
    "주요업무",
    "필요지식",
    "필요기술",
}
_STRUCTURAL_KEYS = {re.sub(r"\s+", "", str(t or "").strip()).lower() for t in _STRUCTURAL_TERMS}
_LABEL_VALUE_RE = re.compile(r"(대분류|중분류|소분류|세분류|능력단위)\s*[\]\)\:：\-\|》>]?\s*(.*)")
_ONLY_LABEL_RE = re.compile(r"^\s*(대분류|중분류|소분류|세분류|능력단위)\s*$")
_NON_NCS_RE = re.compile(r"(NCS\s*미개발|자체개발)", re.IGNORECASE)


def _norm_key(v: str) -> str:
    n = re.sub(r"\s+", "", str(v or "").strip()).lower()
    return re.sub(r"[·‧･ㆍ•∙⋅\-\_/|(),.\[\]{}:;]", "", n)


def _compact(v: str) -> str:
    return re.sub(r"\s+", "", str(v or "").strip()).lower()


def _dedup_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        t = str(v or "").strip()
        if not t:
            continue
        k = _norm_key(t)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _label_key(v: str) -> str:
    t = re.sub(r"\s+", "", str(v or "").strip()).lower()
    t = re.sub(r"[·‧･ㆍ•∙⋅\-\_/|(),.\[\]{}:;]", "", t)
    # Keep only Hangul/ASCII letters/digits for stable matching.
    return re.sub(r"[^0-9a-z\uac00-\ud7a3]", "", t)


def _recover_split_sclass_terms(
    typed_candidates: list[dict[str, Any]],
    ncs_cats: list[str],
) -> list[str]:
    """Recover labels split across lines/tokens, e.g. 인사∙ + 조직 -> 인사·조직."""
    official_by_key: dict[str, str] = {}
    for cat in (ncs_cats or []):
        key = _label_key(cat)
        if key and key not in official_by_key:
            official_by_key[key] = str(cat).strip()
    if not official_by_key:
        return []

    tokens: list[str] = []
    for c in (typed_candidates or []):
        level = str(c.get("level_hint", "")).strip()
        if level not in {"sclass", "unknown", "custom_non_ncs"}:
            continue
        lbl = str(c.get("label", "")).strip()
        if lbl:
            tokens.append(lbl)
    if len(tokens) < 2:
        return []

    recovered: list[str] = []
    for i in range(len(tokens)):
        for w in (3, 2):
            if i + w > len(tokens):
                continue
            merged = "".join(tokens[i : i + w])
            k = _label_key(merged)
            if not k:
                continue
            official = official_by_key.get(k)
            if official:
                recovered.append(official)
                break
    return _dedup_keep_order(recovered)


def _clamp_sclass_limit(value: int | str | None, default: int = 4) -> int:
    try:
        v = int(str(value).strip())
    except Exception:
        v = int(default)
    return max(1, v)


def _to_float_or(value: float | str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return float(default)


def _merge_sclass_candidates(
    primary: list[dict[str, Any]] | None,
    secondary: list[dict[str, Any]] | None,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str, str, str] | tuple[str, str], int] = {}

    def _key(row: dict[str, Any]) -> tuple[str, str, str, str] | tuple[str, str]:
        code_key = (
            str(row.get("ncs_code_no", "")).strip(),
            str(row.get("ncs_lclass_code", "")).strip(),
            str(row.get("ncs_mclass_code", "")).strip(),
            str(row.get("ncs_sclass_code", "")).strip(),
        )
        if any(code_key):
            return code_key
        return ("name", str(row.get("sclass_name", "")).strip())

    def _conf(row: dict[str, Any]) -> float:
        try:
            return float(row.get("confidence", 0.0) or 0.0)
        except Exception:
            return 0.0

    for bucket in ((primary or []), (secondary or [])):
        for row in bucket:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            key = _key(r)
            if key in index_by_key:
                i = index_by_key[key]
                if _conf(r) > _conf(out[i]):
                    out[i] = r
                continue
            index_by_key[key] = len(out)
            out.append(r)
            if len(out) >= max_items:
                return out
    return out


def _select_verified_sclass_candidates(
    candidates: list[dict[str, Any]] | None,
    max_keep: int = 4,
    min_keep: int = 1,
    score_margin: float = 0.18,
    min_confidence: float = 0.62,
) -> list[dict[str, Any]]:
    rows = [dict(x) for x in (candidates or []) if isinstance(x, dict)]
    if not rows:
        return []

    def _conf(v: dict[str, Any]) -> float:
        try:
            return float(v.get("confidence", 0.0) or 0.0)
        except Exception:
            return 0.0

    rows.sort(key=_conf, reverse=True)

    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("ncs_code_no", "")).strip(),
            str(row.get("ncs_lclass_code", "")).strip(),
            str(row.get("ncs_mclass_code", "")).strip(),
            str(row.get("ncs_sclass_code", "")).strip(),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)
    if not deduped:
        return []

    keep_limit = _clamp_sclass_limit(max_keep, default=4)
    min_keep = max(1, min(keep_limit, int(min_keep)))

    top_conf = _conf(deduped[0])
    threshold = max(float(min_confidence), top_conf - float(score_margin))

    selected: list[dict[str, Any]] = []
    for row in deduped:
        if _conf(row) >= threshold:
            selected.append(row)
        if len(selected) >= keep_limit:
            break

    if len(selected) < min_keep:
        for row in deduped:
            if row in selected:
                continue
            selected.append(row)
            if len(selected) >= min_keep:
                break
    return selected[:keep_limit]


def _expand_direct_with_same_family_mentions(
    direct_candidates: list[dict[str, Any]] | None,
    reverse_candidates: list[dict[str, Any]] | None,
    jd_text: str,
    max_add: int = 2,
) -> list[dict[str, Any]]:
    direct = [dict(x) for x in (direct_candidates or []) if isinstance(x, dict)]
    if len(direct) != 1:
        return direct

    base = direct[0]
    base_l = str(base.get("ncs_lclass_code", "")).strip()
    base_m = str(base.get("ncs_mclass_code", "")).strip()
    base_s = str(base.get("ncs_sclass_code", "")).strip()
    if not (base_l and base_m):
        return direct

    norm_text = re.sub(r"\s+", "", str(jd_text or ""))
    if not norm_text:
        return direct

    seen: set[tuple[str, str, str]] = {(base_l, base_m, base_s)}
    add_count = 0
    for row in (reverse_candidates or []):
        if not isinstance(row, dict):
            continue
        l_cd = str(row.get("ncs_lclass_code", "")).strip()
        m_cd = str(row.get("ncs_mclass_code", "")).strip()
        s_cd = str(row.get("ncs_sclass_code", "")).strip()
        if (l_cd, m_cd) != (base_l, base_m):
            continue
        if (l_cd, m_cd, s_cd) in seen:
            continue

        name = str(row.get("sclass_name", "")).strip()
        if not name or re.sub(r"\s+", "", name) not in norm_text:
            continue
        try:
            conf = float(row.get("confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        if conf < 0.60:
            continue

        seen.add((l_cd, m_cd, s_cd))
        direct.append(dict(row))
        add_count += 1
        if add_count >= max(1, int(max_add)):
            break
    return direct


def resolve_sclass_candidates_bundle(
    jd_text: str,
    small_categories: list[str],
    manual_terms: list[str] | None = None,
    subcategory_text: str = "",
    doc_name: str = "",
    show_all_from_small_categories: bool = True,
    enable_ai_fallback: bool = True,
    verified_sclass_limit: int = 4,
    verified_min_keep: int = 1,
    score_margin: float = 0.18,
    min_confidence: float = 0.62,
) -> dict[str, list[dict[str, Any]]]:
    manual = [str(x).strip() for x in (manual_terms or []) if str(x).strip()]
    base_terms = [str(x).strip() for x in (small_categories or []) if str(x).strip()]

    reverse_sclass_candidates = infer_sclass_candidates_reverse_dictionary(
        jd_text=jd_text,
        hint_terms=(base_terms + manual),
        doc_name=doc_name,
        max_items=8,
    )

    direct_sclass_candidates_raw = lookup_ncs_codes_by_sclass(base_terms)
    direct_sclass_candidates = _expand_direct_with_same_family_mentions(
        direct_candidates=direct_sclass_candidates_raw,
        reverse_candidates=reverse_sclass_candidates,
        jd_text=jd_text,
        max_add=2,
    )

    csv_sclass_candidates = _merge_sclass_candidates(
        primary=direct_sclass_candidates,
        secondary=reverse_sclass_candidates,
        max_items=8,
    )
    if not csv_sclass_candidates:
        csv_sclass_candidates = infer_sclass_candidates_from_text_catalog(
            jd_text=jd_text,
            max_items=8,
            hint_terms=(base_terms + manual),
        )
    if not csv_sclass_candidates and enable_ai_fallback:
        csv_sclass_candidates = ai_pick_sclass_from_csv(
            small_categories=base_terms,
            subcategory_text=subcategory_text,
            jd_text=jd_text,
            max_items=8,
        )

    if show_all_from_small_categories and direct_sclass_candidates_raw:
        verified_sclass = list(direct_sclass_candidates_raw)
    else:
        single_direct_lock = bool(not manual and len(direct_sclass_candidates) == 1)
        family_expanded_lock = bool(
            not manual
            and len(base_terms) <= 1
            and len(direct_sclass_candidates) > 1
        )
        if family_expanded_lock:
            verified_source_candidates = list(direct_sclass_candidates)
            effective_limit = min(verified_sclass_limit, max(1, len(direct_sclass_candidates)))
            effective_margin = 0.45
            effective_min_conf = 0.60
        elif single_direct_lock:
            verified_source_candidates = csv_sclass_candidates
            effective_limit = 1
            effective_margin = 0.0
            effective_min_conf = 0.95
        else:
            verified_source_candidates = csv_sclass_candidates
            effective_limit = verified_sclass_limit
            effective_margin = score_margin
            effective_min_conf = min_confidence

        verified_sclass = _select_verified_sclass_candidates(
            candidates=verified_source_candidates,
            max_keep=effective_limit,
            min_keep=verified_min_keep,
            score_margin=effective_margin,
            min_confidence=effective_min_conf,
        )

    return {
        "reverse_sclass_candidates": reverse_sclass_candidates,
        "direct_sclass_candidates_raw": direct_sclass_candidates_raw,
        "direct_sclass_candidates": direct_sclass_candidates,
        "csv_sclass_candidates": csv_sclass_candidates,
        "verified_sclass": verified_sclass,
    }


def _load_ncs_small_categories(csv_path: Path | None = None) -> list[str]:
    path = csv_path or (Path(__file__).resolve().parent.parent.parent / "ncs_sclass_codes_with_code_no.csv")
    out: list[str] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = str(row.get("NCS_SCLAS_CDNM", "")).strip()
            if name:
                out.append(name)
    return _dedup_keep_order(out)


def _split_candidate_blob(raw: str) -> list[tuple[str, bool]]:
    blob = str(raw or "")
    if not blob.strip():
        return []
    blob = blob.replace("\uFF0C", ",")
    blob = re.sub(r"(?<!^)(?=\d{1,2}\s*[\.\)]\s*[\uAC00-\uD7A3A-Za-z])", "\n", blob)
    blob = re.sub(r"(?<!^)(?=\d{1,2}\s*-\s*[\uAC00-\uD7A3A-Za-z])", "\n", blob)
    # "01 법무" or "일반사무01법무" 형태를 분리한다.
    blob = re.sub(r"(?<!^)(?=\d{1,2}\s+[\uAC00-\uD7A3A-Za-z])", "\n", blob)
    blob = re.sub(r"(?<=[\uAC00-\uD7A3A-Za-z])(?=\d{1,2}\s*[\.\)\-]?\s*[\uAC00-\uD7A3A-Za-z])", "\n", blob)

    out: list[tuple[str, bool]] = []
    for part in re.split(r"[\n,;/|]+", blob):
        p = str(part).strip()
        if not p:
            continue
        non_ncs = bool(_NON_NCS_RE.search(p))
        p = _NON_NCS_RE.sub("", p)
        p = re.sub(r"^\d{1,2}\s*[\.\)\-]?\s*", "", p)
        p = re.sub(r"^[\-\:\]\)\.]+", "", p).strip()
        p = re.sub(r"\s+", "", p)
        if not p or p.isdigit():
            continue
        out.append((p, non_ncs))
    return out


def _level_hint(label: str) -> str:
    c = _compact(label)
    if "소분류" in c:
        return "sclass"
    if "세분류" in c:
        return "subd"
    if "대분류" in c or "중분류" in c or "능력단위" in c:
        return "ignore"
    return "unknown"


def _is_structural_token(token: str) -> bool:
    return _compact(token) in _STRUCTURAL_KEYS


def _add_candidate(
    bucket: list[dict[str, Any]],
    raw: str,
    hint: str,
    page: int,
    source: str,
) -> None:
    if hint == "ignore":
        return
    for token, non_ncs in _split_candidate_blob(raw):
        if _is_structural_token(token):
            continue
        level = "custom_non_ncs" if non_ncs else hint
        bucket.append(
            {
                "label": token,
                "raw": token,
                "level_hint": level,
                "page": int(page),
                "source": source,
            }
        )


def _extract_scope_rows(line_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not line_rows:
        return []
    start = None
    for i, row in enumerate(line_rows):
        comp = _compact(str(row.get("line", "")))
        if any(a in comp for a in _START_ANCHORS):
            start = i
            break
    # "분류체계"가 깨져 분리된 문서에서는 대/중/소/세분류 라벨 블록을 시작점으로 사용한다.
    if start is None:
        for i in range(len(line_rows)):
            window = [_compact(str((line_rows[i + k] if i + k < len(line_rows) else {}).get("line", ""))) for k in range(0, 8)]
            hit_count = 0
            for t in ("대분류", "중분류", "소분류", "세분류"):
                if any(t in w for w in window):
                    hit_count += 1
            if hit_count >= 2:
                start = i
                break
    if start is None:
        return line_rows[:220]
    end = len(line_rows)
    upper = min(len(line_rows), start + 260)
    for j in range(start + 1, upper):
        comp = _compact(str(line_rows[j].get("line", "")))
        if any(a in comp for a in _END_ANCHORS):
            end = j
            break
    return line_rows[start:end]


def _extract_pdf_content(
    pdf_bytes: bytes,
    max_pages: int = 2,
) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber not installed") from e

    line_rows: list[dict[str, Any]] = []
    all_tables: list[tuple[int, list[list[str]]]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = list(pdf.pages[: max(1, int(max_pages))])
        for page_no, page in enumerate(pages, start=1):
            text = page.extract_text() or ""
            for ln in [x.strip() for x in text.splitlines() if x and x.strip()]:
                line_rows.append({"page": page_no, "line": ln})

            for tbl in (page.extract_tables() or []):
                if not tbl:
                    continue
                cleaned_tbl: list[list[str]] = []
                for row in tbl:
                    cleaned_tbl.append([str(c).strip() if c else "" for c in (row or [])])
                all_tables.append((page_no, cleaned_tbl))

    scope_rows = _extract_scope_rows(line_rows)
    scope_pages = sorted({int(r.get("page", 0) or 0) for r in scope_rows if int(r.get("page", 0) or 0) > 0})
    if not scope_pages:
        scope_pages = [1]
    scope_tables = [(pg, tbl) for pg, tbl in all_tables if pg in set(scope_pages)]

    return {
        "all_lines": [str(r.get("line", "")) for r in line_rows],
        "all_tables": all_tables,
        "scope_rows": scope_rows,
        "scope_tables": scope_tables,
    }


def extract_pdf_text_fallback(pdf_bytes: bytes, max_pages: int = 6) -> str:
    """Fallback PDF text extractor used when primary extractor returns empty text."""
    parsed = _extract_pdf_content(pdf_bytes, max_pages=max_pages)
    lines = [str(x).strip() for x in parsed.get("all_lines", []) if str(x).strip()]
    return "\n".join(lines).strip()


def _collect_scope_candidates(
    scope_rows: list[dict[str, Any]],
    scope_tables: list[tuple[int, list[list[str]]]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # 1) table-first: restore classification columns first.
    for page_no, tbl in scope_tables:
        # A) header-based column restore (when header row clearly contains hierarchy labels)
        header_row = -1
        header_map: dict[str, int] = {}
        scan_limit = min(len(tbl), 8)
        for ridx in range(scan_limit):
            row = tbl[ridx]
            local: dict[str, int] = {}
            for cidx, cell in enumerate(row):
                comp = _compact(cell)
                if "소분류" in comp and "세분류" not in comp:
                    local["sclass"] = cidx
                elif "세분류" in comp:
                    local["subd"] = cidx
                elif "대분류" in comp:
                    local["lclass"] = cidx
                elif "중분류" in comp:
                    local["mclass"] = cidx
            if "sclass" in local and (len(local) >= 3 or ("subd" in local and len(local) >= 2)):
                header_row = ridx
                header_map = local
                break
        if header_row >= 0:
            for row in tbl[max(0, header_row + 1) :]:
                s_col = int(header_map.get("sclass", -1))
                d_col = int(header_map.get("subd", -1))
                if s_col >= 0 and s_col < len(row):
                    _add_candidate(out, row[s_col], "sclass", page_no, "table_sclass_column")
                if d_col >= 0 and d_col < len(row):
                    _add_candidate(out, row[d_col], "subd", page_no, "table_subd_column")

        # B) row-level parse: a row that starts with "소분류" can contain multiple values across columns
        for ridx, row in enumerate(tbl[:12]):
            row_hint = ""
            label_cols: set[int] = set()
            for cidx, cell in enumerate(row):
                cell_text = str(cell or "").strip()
                if not cell_text:
                    continue
                hint = _level_hint(cell_text)
                if hint in {"sclass", "subd", "ignore"} and _is_structural_token(cell_text):
                    label_cols.add(cidx)
                    if hint in {"sclass", "subd"}:
                        row_hint = hint

            if row_hint in {"sclass", "subd"}:
                for cidx, cell in enumerate(row):
                    if cidx in label_cols:
                        continue
                    txt = str(cell or "").strip()
                    if not txt or _is_structural_token(txt):
                        continue
                    _add_candidate(out, txt, row_hint, page_no, "table_row_level")

            # C) inline "소분류: 값" cell parsing
            for cell in row:
                m = _LABEL_VALUE_RE.search(str(cell or ""))
                if not m:
                    continue
                label, rhs = m.group(1), m.group(2)
                hint = _level_hint(label)
                if hint not in {"sclass", "subd"}:
                    continue
                if not rhs.strip():
                    continue
                _add_candidate(out, rhs, hint, page_no, "table_label_value")

    # 2) line parser with next-line window.
    pending_hint = ""
    pending_page = 0
    pending_window = 0
    for row in scope_rows:
        page_no = int(row.get("page", 0) or 0)
        line = str(row.get("line", "")).strip()
        if not line:
            continue

        m = _LABEL_VALUE_RE.search(line)
        if m:
            label, rhs = m.group(1), m.group(2).strip()
            hint = _level_hint(label)
            if hint in {"sclass", "subd"} and rhs:
                _add_candidate(out, rhs, hint, page_no, "line_label_value")
                pending_hint = ""
                pending_window = 0
            elif hint in {"sclass", "subd"}:
                pending_hint = hint
                pending_page = page_no
                pending_window = 2
            else:
                pending_hint = ""
                pending_window = 0
            continue

        if _ONLY_LABEL_RE.match(line):
            hint = _level_hint(line)
            if hint in {"sclass", "subd"}:
                pending_hint = hint
                pending_page = page_no
                pending_window = 2
            else:
                pending_hint = ""
                pending_window = 0
            continue

        if pending_hint and pending_window > 0:
            _add_candidate(out, line, pending_hint, pending_page or page_no, "line_next_window")
            pending_window -= 1
            if pending_window <= 0:
                pending_hint = ""

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for c in out:
        key = (_norm_key(c.get("label", "")), str(c.get("level_hint", "")), int(c.get("page", 0) or 0))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _match_page_for_label(label: str, candidates: list[dict[str, Any]]) -> int:
    key = _norm_key(label)
    for c in candidates:
        if _norm_key(str(c.get("label", ""))) == key:
            return int(c.get("page", 0) or 0)
    return 0


def _extract_from_text_scope(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    for ln in [x.strip() for x in str(text or "").splitlines() if x and x.strip()]:
        rows.append({"page": 1, "line": ln})
    scope_rows = _extract_scope_rows(rows)
    scope_text_lines = [str(r.get("line", "")) for r in scope_rows]
    return scope_rows, scope_text_lines


def extract_sclass_from_pdf_bytes(pdf_bytes: bytes, filename: str = "") -> dict[str, Any]:
    ncs_cats = _load_ncs_small_categories()
    parsed = _extract_pdf_content(pdf_bytes, max_pages=6)
    scope_rows = parsed["scope_rows"]
    scope_tables = parsed["scope_tables"]
    all_lines = parsed["all_lines"]
    all_tables = parsed["all_tables"]
    scope_lines = [str(r.get("line", "")) for r in scope_rows]
    scope_text = "\n".join(scope_lines)

    typed_candidates = _collect_scope_candidates(scope_rows, scope_tables)
    candidate_terms = [
        str(c.get("label", "")).strip()
        for c in typed_candidates
        if str(c.get("level_hint", "")) in {"sclass", "unknown", "custom_non_ncs"}
    ]

    det_scope = extract_small_category(scope_lines, [tbl for _, tbl in scope_tables], ncs_cats)
    for c in det_scope.get("topk", []):
        lbl = str((c or {}).get("label", "")).strip()
        if lbl and float((c or {}).get("score", 0) or 0) >= 4:
            candidate_terms.append(lbl)
    candidate_terms.extend(_recover_split_sclass_terms(typed_candidates, ncs_cats))

    # Always merge structural parser output.
    # It can recover line-broken labels like "인사∙\\n조직" -> "인사·조직",
    # "일반\\n사무" -> "일반사무" that table/regex candidates may split.
    candidate_terms.extend(extract_small_categories_from_jd(scope_text))

    if not candidate_terms:
        det_full = extract_small_category(all_lines, [tbl for _, tbl in all_tables], ncs_cats)
        for c in det_full.get("topk", []):
            lbl = str((c or {}).get("label", "")).strip()
            if lbl and float((c or {}).get("score", 0) or 0) >= 5:
                candidate_terms.append(lbl)

    small_categories = _dedup_keep_order(candidate_terms)
    verify_text = scope_text if scope_text.strip() else "\n".join(all_lines)
    bundle = resolve_sclass_candidates_bundle(
        jd_text=verify_text,
        small_categories=small_categories,
        manual_terms=[],
        subcategory_text="",
        doc_name=filename,
        show_all_from_small_categories=True,
        enable_ai_fallback=False,
        verified_sclass_limit=max(1, len(small_categories)),
        verified_min_keep=1,
        score_margin=0.18,
        min_confidence=0.62,
    )

    verified = bundle["verified_sclass"]
    matched = _dedup_keep_order([str(x.get("sclass_name", "")).strip() for x in verified if str(x.get("sclass_name", "")).strip()])
    if not matched:
        direct = lookup_ncs_codes_by_sclass(small_categories)
        matched = _dedup_keep_order([str(x.get("sclass_name", "")).strip() for x in direct if str(x.get("sclass_name", "")).strip()])

    matched_keys = {_norm_key(x) for x in matched}
    unmatched_detail: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for c in typed_candidates:
        raw = str(c.get("raw", "")).strip()
        if not raw:
            continue
        k = _norm_key(raw)
        if not k or k in matched_keys:
            continue
        level = str(c.get("level_hint", "unknown"))
        if level == "subd":
            rejected.append(
                {
                    "raw": raw,
                    "page": int(c.get("page", 0) or 0),
                    "reason": "subd_context",
                    "source": str(c.get("source", "")),
                }
            )
            continue
        reason = "non_ncs_or_custom" if level == "custom_non_ncs" else "not_verified"
        unmatched_detail.append(
            {
                "raw": raw,
                "page": int(c.get("page", 0) or 0),
                "reason": reason,
                "source": str(c.get("source", "")),
            }
        )

    unmatched = _dedup_keep_order([str(x.get("raw", "")).strip() for x in unmatched_detail if str(x.get("raw", "")).strip()])

    matched_detail: list[dict[str, Any]] = []
    for row in verified:
        label = str(row.get("sclass_name", "")).strip()
        if not label:
            continue
        matched_detail.append(
            {
                "label": label,
                "page": _match_page_for_label(label, typed_candidates),
                "source": str(row.get("evidence", "")),
                "raw": label,
                "confidence": float(row.get("confidence", 0.0) or 0.0),
            }
        )

    return {
        "matched": matched,
        "unmatched": unmatched,
        "ncs_cats": ncs_cats,
        "matched_detail": matched_detail,
        "unmatched_detail": unmatched_detail,
        "rejected": rejected,
    }


def extract_sclass_from_text(text: str, filename: str = "") -> dict[str, Any]:
    ncs_cats = _load_ncs_small_categories()
    scope_rows, scope_lines = _extract_from_text_scope(text)
    typed_candidates = _collect_scope_candidates(scope_rows, [])
    candidate_terms = [
        str(c.get("label", "")).strip()
        for c in typed_candidates
        if str(c.get("level_hint", "")) in {"sclass", "unknown", "custom_non_ncs"}
    ]

    det_scope = extract_small_category(scope_lines, [], ncs_cats)
    for c in det_scope.get("topk", []):
        lbl = str((c or {}).get("label", "")).strip()
        if lbl and float((c or {}).get("score", 0) or 0) >= 4:
            candidate_terms.append(lbl)
    candidate_terms.extend(_recover_split_sclass_terms(typed_candidates, ncs_cats))

    # Always merge structural parser output for the same reason as PDF path.
    candidate_terms.extend(extract_small_categories_from_jd(text))

    small_categories = _dedup_keep_order(candidate_terms)
    bundle = resolve_sclass_candidates_bundle(
        jd_text="\n".join(scope_lines) or text,
        small_categories=small_categories,
        manual_terms=[],
        subcategory_text="",
        doc_name=filename,
        show_all_from_small_categories=True,
        enable_ai_fallback=False,
        verified_sclass_limit=max(1, len(small_categories)),
        verified_min_keep=1,
        score_margin=0.18,
        min_confidence=0.62,
    )
    verified = bundle["verified_sclass"]
    matched = _dedup_keep_order([str(x.get("sclass_name", "")).strip() for x in verified if str(x.get("sclass_name", "")).strip()])
    if not matched:
        direct = lookup_ncs_codes_by_sclass(small_categories)
        matched = _dedup_keep_order([str(x.get("sclass_name", "")).strip() for x in direct if str(x.get("sclass_name", "")).strip()])

    matched_keys = {_norm_key(x) for x in matched}
    unmatched_detail: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for c in typed_candidates:
        raw = str(c.get("raw", "")).strip()
        if not raw:
            continue
        k = _norm_key(raw)
        if not k or k in matched_keys:
            continue
        level = str(c.get("level_hint", "unknown"))
        if level == "subd":
            rejected.append(
                {
                    "raw": raw,
                    "page": 1,
                    "reason": "subd_context",
                    "source": str(c.get("source", "")),
                }
            )
            continue
        reason = "non_ncs_or_custom" if level == "custom_non_ncs" else "not_verified"
        unmatched_detail.append(
            {
                "raw": raw,
                "page": 1,
                "reason": reason,
                "source": str(c.get("source", "")),
            }
        )

    unmatched = _dedup_keep_order([str(x.get("raw", "")).strip() for x in unmatched_detail if str(x.get("raw", "")).strip()])
    matched_detail = [
        {
            "label": str(row.get("sclass_name", "")).strip(),
            "page": 1,
            "source": str(row.get("evidence", "")),
            "raw": str(row.get("sclass_name", "")).strip(),
            "confidence": float(row.get("confidence", 0.0) or 0.0),
        }
        for row in verified
        if str(row.get("sclass_name", "")).strip()
    ]

    return {
        "matched": matched,
        "unmatched": unmatched,
        "ncs_cats": ncs_cats,
        "matched_detail": matched_detail,
        "unmatched_detail": unmatched_detail,
        "rejected": rejected,
    }
