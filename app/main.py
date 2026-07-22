from __future__ import annotations

import asyncio
import csv
import functools
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.init_db import init_db
from app.repository import create_posting as repo_create_posting
from app.repository import fetch_posting_for_report, get_posting as repo_get_posting
from app.repository import list_postings as repo_list_postings
from app.repository import recommend_postings as repo_recommend_postings
from app.repository import save_match_result
from app.schemas import AiInterviewRequest, AiInterviewResponse, PostingCreate, ReportCreate, ReportOut
from app.services.ai_strategy import build_strategy_with_openai, rank_postings_with_openai
from app.services.external_api import fetch_ncs, fetch_ncs_highschool_course, fetch_public_inst, fetch_recruitment
from app.services.kordoc_parser import KordocParseError, parse_with_kordoc, structure_job_description, structure_job_notice
from app.services.ncs_mcp_client import NcsMcpError, ncs_mcp_status, search_units_by_detail
from app.services.jd_strategy import (
    ai_pick_sclass_from_csv,
    ai_extract_ncs_cl_codes,
    ai_extract_sclass_candidates,
    build_notice_context_from_jd,
    build_ncs_context_pack,
    build_strategy_with_rule_fallback,
    build_strategy_with_openai as build_jd_strategy_with_openai,
    extract_detail_categories_from_jd,
    extract_small_categories_from_jd,
    infer_sclass_candidates_reverse_dictionary,
    infer_sclass_candidates_from_text_catalog,
    lookup_ncs_codes_by_sclass,
    extract_subcategory_text,
    extract_pdf_text,
    extract_focus_terms_from_pdf_vision,
    fetch_ncs_ksa_by_units,
    fetch_ncs_ksa_by_sclass_code,
    fetch_ncs_units_hrdk_by_cl_codes,
    fetch_ncs_units_hrdk_by_keywords,
    fetch_ncs_units_hrdk_by_sclass_names,
    fetch_ncs_units_hrdk_by_verified_sclass,
    fetch_ncs_units_v18_by_sclass,
    generate_interview_questions_by_ncs_code,
    generate_personalized_interview_questions,
    generate_diverse_interview_questions,
    rank_ksa_factors_by_query,
    infer_keywords_from_subcategory_ai,
    is_similar_question_text,
    normalize_question_dedup_key,
    review_ocr_terms_with_openai,
    rerank_ncs_matches,
    resolve_sclass_candidates_with_catalog,
    verify_sclass_candidates_with_ncs_api,
)
from app.services.ncs import map_ncs
from app.services.auto_runner import start_auto_runner
from app.services.queue_manager import QueueManager
from app.services.sclass_pipeline import (
    extract_pdf_text_fallback,
    extract_sclass_from_pdf_bytes,
    extract_sclass_from_text,
    resolve_sclass_candidates_bundle,
)
from app.services.sync_workers import sync_ncs_units, sync_public_institutions
from app.settings import settings

app = FastAPI(title="NCScope", version="0.1.0")
queue = QueueManager(max_retries=2)


# Middleware: Disable caching for all question generation endpoints
@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    """Ensure no caching for dynamic question generation APIs"""
    response = await call_next(request)

    if "questions" in request.url.path or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"

    return response
BASE_DIR = Path(__file__).resolve().parent
UI_INDEX = BASE_DIR / "static" / "index.html"
_ALIO_CACHE: dict[str, dict] = {}
NCS_SCLASS_CSV = BASE_DIR.parent / "ncs_sclass_codes_with_code_no.csv"
_SCLASS_OPTIONS_CACHE: list[dict] | None = None
_QUESTION_HISTORY_LOCK = threading.Lock()
_QUESTION_HISTORY_BY_CODE: dict[str, list[str]] = {}
_QUESTION_HISTORY_LIMIT = 300
# 면접 질문 생성 최적 고정값 (10개 기준)
FAST_NCS_TOP_K = 4          # NCS 매칭 상위 4개
FAST_KSA_UNITS = 2          # KSA 수집 능력단위 2개 (타임아웃 방지)
FAST_KSA_FACTORS_PER_UNIT = 2  # 단위당 KSA 2개 (총 6개)


def _clamp_runtime_knobs(
    ncs_top_k: int | str | None,
    ksa_units: int | str | None,
    ksa_factors_per_unit: int | str | None,
) -> tuple[int, int, int]:
    def _to_int(v: int | str | None, default: int) -> int:
        try:
            return int(str(v).strip())
        except Exception:
            return default

    top_k = max(1, min(8, _to_int(ncs_top_k, FAST_NCS_TOP_K)))
    units = max(1, min(6, _to_int(ksa_units, FAST_KSA_UNITS)))
    factors = max(1, min(4, _to_int(ksa_factors_per_unit, FAST_KSA_FACTORS_PER_UNIT)))
    return top_k, units, factors


def _clamp_sclass_limit(value: int | str | None, default: int = 4) -> int:
    try:
        v = int(str(value).strip())
    except Exception:
        v = int(default)
    return max(1, min(6, v))


def _to_float_or(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return float(default)


def _clamp_int(value: int | str | None, default: int, lo: int, hi: int) -> int:
    try:
        v = int(str(value).strip())
    except Exception:
        v = int(default)
    return max(int(lo), min(int(hi), v))


def _norm_sclass_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _parse_sclass_terms(raw: str | None) -> list[str]:
    parts = re.split(r"[\n,;/|]+", str(raw or ""))
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = str(part).strip()
        if not term:
            continue
        key = _norm_sclass_key(term)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def _merge_sclass_terms(
    base_terms: list[str],
    add_terms: list[str] | None = None,
    remove_terms: list[str] | None = None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in (base_terms or []) + (add_terms or []):
        key = _norm_sclass_key(term)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(term).strip())

    remove_keys = {_norm_sclass_key(x) for x in (remove_terms or []) if _norm_sclass_key(x)}
    if not remove_keys:
        return out
    return [x for x in out if _norm_sclass_key(x) not in remove_keys]


def _repeat_count_from_weight(weight: float, default: int = 1, max_repeat: int = 6) -> int:
    try:
        v = int(round(float(weight)))
    except Exception:
        v = int(default)
    return max(1, min(int(max_repeat), v))


def _build_priority_notice_text(
    notice_text: str,
    duty_text: str = "",
    evaluation_text: str = "",
) -> str:
    notice = str(notice_text or "").strip()
    duty = str(duty_text or "").strip()
    evaluation = str(evaluation_text or "").strip()

    parts: list[str] = []
    if duty:
        parts.append(f"[담당업무-우선]\n{duty[:2500]}")
    if evaluation:
        parts.append(f"[면접평가항목-우선]\n{evaluation[:1800]}")
    if notice:
        parts.append(f"[공고문-보조]\n{notice[:2500]}")
    return "\n\n".join(parts).strip()


def _build_priority_query_text(
    base_text: str,
    duty_text: str = "",
    evaluation_text: str = "",
) -> str:
    base = str(base_text or "").strip()[:5000]
    duty = str(duty_text or "").strip()[:2500]
    evaluation = str(evaluation_text or "").strip()[:1500]

    base_w = _to_float_or(os.getenv("JD_BASE_TEXT_WEIGHT", "1.0"), 1.0)
    duty_w = _to_float_or(os.getenv("DUTY_TEXT_WEIGHT", "3.0"), 3.0)
    eval_w = _to_float_or(os.getenv("EVALUATION_TEXT_WEIGHT", "2.5"), 2.5)

    base_rep = _repeat_count_from_weight(base_w, default=1, max_repeat=4)
    duty_rep = _repeat_count_from_weight(duty_w, default=3, max_repeat=6)
    eval_rep = _repeat_count_from_weight(eval_w, default=2, max_repeat=6)

    chunks: list[str] = []
    if duty:
        chunks.extend([f"[담당업무]{duty}"] * duty_rep)
    if evaluation:
        chunks.extend([f"[면접평가항목]{evaluation}"] * eval_rep)
    if base:
        chunks.extend([f"[기본텍스트]{base}"] * base_rep)
    return "\n".join(chunks).strip()


def _collect_ksa_candidate_units(
    primary_units: list[dict[str, Any]] | None,
    secondary_units: list[dict[str, Any]] | None = None,
    max_units: int = 12,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    for bucket in ((primary_units or []), (secondary_units or [])):
        for row in bucket:
            if not isinstance(row, dict):
                continue
            code = str(row.get("ncsClCd", "")).strip()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            try:
                score = float(row.get("score", 1.0) or 1.0)
            except Exception:
                score = 1.0
            out.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": str(row.get("compeUnitName", "")).strip(),
                    "compeUnitLevel": str(row.get("compeUnitLevel", "")).strip(),
                    "ncsSubdCdnm": str(row.get("ncsSubdCdnm", "")).strip(),
                    "compeUnitDef": str(row.get("compeUnitDef", "")).strip(),
                    "score": score,
                    "matched_keywords": list(row.get("matched_keywords", []) or []),
                }
            )
            if len(out) >= max(1, int(max_units or 12)):
                return out
    return out


def _fetch_ncs_ksa_or_502(
    ncs_matches: list[dict[str, Any]],
    max_units: int,
    max_factors_per_unit: int,
) -> list[dict[str, Any]]:
    try:
        return fetch_ncs_ksa_by_units(
            ncs_matches=ncs_matches,
            max_units=max_units,
            max_factors_per_unit=max_factors_per_unit,
        )
    except NcsMcpError as exc:
        raise HTTPException(status_code=502, detail=f"NCS MCP KSA lookup failed: {exc}") from exc


def _require_ncs_mcp_url() -> str:
    endpoint = settings.ncs_mcp_endpoint()
    if endpoint:
        return endpoint
    raise HTTPException(
        status_code=503,
        detail=(
            "NCS_MCP_URL is required for NCScope. Start the read-only NCS_MCP "
            "server with the compact serving DB and set NCS_MCP_URL."
        ),
    )


def _require_legacy_ncs_api_enabled() -> None:
    if not settings.enable_legacy_ncs_api():
        raise HTTPException(
            status_code=410,
            detail="legacy NCS API endpoints are disabled; use NCS_MCP_URL-backed endpoints",
        )


def _check_upload_size(data: bytes, label: str) -> None:
    max_bytes = settings.max_upload_bytes()
    if len(data or b"") > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds MAX_UPLOAD_MB ({max_bytes // (1024 * 1024)} MB)",
        )


def _sanitize_request_openai_key(value: str | None) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) > 300 or any(ch.isspace() for ch in key):
        raise HTTPException(status_code=400, detail="openai_api_key is invalid")
    return key


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


def _history_key(ncs_code: str, competency_name: str = "") -> str:
    code = normalize_question_dedup_key(ncs_code)
    comp = normalize_question_dedup_key(competency_name)
    return f"{code}|{comp}"


def _load_question_history(ncs_code: str, competency_name: str = "") -> list[str]:
    key = _history_key(ncs_code, competency_name)
    with _QUESTION_HISTORY_LOCK:
        return list(_QUESTION_HISTORY_BY_CODE.get(key, []))


def _save_question_history(
    ncs_code: str,
    competency_name: str,
    new_questions: list[str],
) -> None:
    if not new_questions:
        return
    key = _history_key(ncs_code, competency_name)
    with _QUESTION_HISTORY_LOCK:
        bucket = _QUESTION_HISTORY_BY_CODE.get(key, [])
        bucket.extend([q for q in new_questions if str(q or "").strip()])
        if len(bucket) > _QUESTION_HISTORY_LIMIT:
            bucket = bucket[-_QUESTION_HISTORY_LIMIT:]
        _QUESTION_HISTORY_BY_CODE[key] = bucket


@app.on_event("startup")
def _startup() -> None:
    init_db()
    start_auto_runner()


@app.get("/health")
def health() -> dict:
    mcp = ncs_mcp_status()
    mcp_ready = bool(mcp.get("configured") and mcp.get("reachable") and mcp.get("ksaAvailable"))
    return {
        "status": "ok" if mcp_ready else "degraded",
        "keys": {
            "public_inst": bool(settings.public_inst_key()),
            "ncs": bool(settings.ncs_key()),
            "openai": bool(settings.openai_key()),
        },
        "ncs_source": "remote-mcp",
        "ncs_mcp": mcp,
    }


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(UI_INDEX)


@app.get("/api/ncs/sclass/options")
def ncs_sclass_options() -> dict:
    global _SCLASS_OPTIONS_CACHE
    if _SCLASS_OPTIONS_CACHE is not None:
        return {"count": len(_SCLASS_OPTIONS_CACHE), "items": _SCLASS_OPTIONS_CACHE}

    if not NCS_SCLASS_CSV.exists():
        raise HTTPException(status_code=404, detail=f"csv not found: {NCS_SCLASS_CSV}")

    by_name: dict[str, dict] = {}
    try:
        with NCS_SCLASS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = str(row.get("NCS_SCLAS_CDNM", "")).strip()
                if not name or name in by_name:
                    continue
                by_name[name] = {
                    "name": name,
                    "ncs_code_no": str(row.get("NCS_CODE_NO", "")).strip(),
                    "lclass_code": str(row.get("NCS_LCLAS_CD", "")).strip(),
                    "mclass_code": str(row.get("NCS_MCLAS_CD", "")).strip(),
                    "sclass_code": str(row.get("NCS_SCLAS_CD", "")).strip(),
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read csv: {e}") from e

    items = [by_name[k] for k in sorted(by_name.keys())]
    _SCLASS_OPTIONS_CACHE = items
    return {"count": len(items), "items": items}


def _find_sclass_code_tuple(sclass_name: str) -> dict[str, str] | None:
    def _norm_key(v: str) -> str:
        n = re.sub(r"\s+", "", str(v or "").strip()).lower()
        return re.sub(r"[·･ㆍ•∙⋅\-\_/|(),.\[\]{}]", "", n)

    name = str(sclass_name or "").strip()
    name_key = _norm_key(name)
    if not name or not NCS_SCLASS_CSV.exists():
        return None
    try:
        with NCS_SCLASS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_name = str(row.get("NCS_SCLAS_CDNM", "")).strip()
                if row_name != name and _norm_key(row_name) != name_key:
                    continue
                l_cd = str(row.get("NCS_LCLAS_CD", "")).strip()
                m_cd = str(row.get("NCS_MCLAS_CD", "")).strip()
                s_cd = str(row.get("NCS_SCLAS_CD", "")).strip()
                if l_cd and m_cd and s_cd:
                    return {
                        "ncs_lclass_code": l_cd,
                        "ncs_mclass_code": m_cd,
                        "ncs_sclass_code": s_cd,
                    }
    except Exception:
        return None
    return None


@app.get("/api/ncs/units/options")
def ncs_unit_options(
    q: str = Query(default="", description="NCS detail classification search text"),
    limit: int = Query(default=300, ge=1, le=1000),
) -> dict:
    _require_ncs_mcp_url()
    term = str(q or "").strip()
    if not term:
        return {"count": 0, "items": [], "source": "ncs-mcp", "message": "Enter a confirmed NCS detail classification."}
    try:
        items = search_units_by_detail([term], max_units=limit)
    except NcsMcpError as exc:
        raise HTTPException(status_code=502, detail=f"NCS MCP lookup failed: {exc}") from exc
    return {"count": len(items), "items": items}


@app.get("/api/ncs/sclass/ksa")
def ncs_sclass_ksa(
    sclass_name: str = Query(default="", alias="sclassName", description="소분류명(예: 총무)"),
    ncs_lclass_code: str = Query(default="", alias="ncsLclasCd"),
    ncs_mclass_code: str = Query(default="", alias="ncsMclasCd"),
    ncs_sclass_code: str = Query(default="", alias="ncsSclasCd"),
    max_units: int = Query(default=80, ge=1, le=200),
) -> dict:
    _require_legacy_ncs_api_enabled()
    l_cd = str(ncs_lclass_code or "").strip()
    m_cd = str(ncs_mclass_code or "").strip()
    s_cd = str(ncs_sclass_code or "").strip()
    s_nm = str(sclass_name or "").strip()

    # If caller passed only 소분류명, resolve code tuple from local catalog CSV.
    if not (l_cd and m_cd and s_cd) and s_nm:
        row = _find_sclass_code_tuple(s_nm)
        if row:
            l_cd = row["ncs_lclass_code"]
            m_cd = row["ncs_mclass_code"]
            s_cd = row["ncs_sclass_code"]

    if not (l_cd and m_cd and s_cd):
        raise HTTPException(
            status_code=400,
            detail="ncsLclasCd/ncsMclasCd/ncsSclasCd or sclassName is required",
        )

    try:
        result = fetch_ncs_ksa_by_sclass_code(
            ncs_lclass_code=l_cd,
            ncs_mclass_code=m_cd,
            ncs_sclass_code=s_cd,
            sclass_name=s_nm,
            max_units=max_units,
        )
        units = result.get("units", [])
        ksa = result.get("ksa", [])
        return {
            "query": {
                "sclassName": s_nm,
                "ncsLclasCd": l_cd,
                "ncsMclasCd": m_cd,
                "ncsSclasCd": s_cd,
            },
            "counts": {"units": len(units), "ksa": len(ksa)},
            "units": units,
            "ksa": ksa,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"sclass ksa fetch failed: {e}") from e


@app.get("/api/ops/metrics")
def ops_metrics() -> dict:
    return {"queue": queue.stats()}


def _require_admin(x_admin_token: str | None) -> None:
    if not settings.enable_admin_endpoints():
        raise HTTPException(status_code=403, detail="admin endpoints are disabled")
    expected = settings.admin_token()
    if not expected:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN is required")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


@app.post("/api/admin/sync/public-inst")
def admin_sync_public_inst(
    max_pages: int = Query(default=5, ge=1, le=100),
    num_of_rows: int = Query(default=100, ge=1, le=1000),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    try:
        return sync_public_institutions(max_pages=max_pages, num_of_rows=num_of_rows)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"sync failed: {e}") from e


@app.post("/api/admin/sync/ncs")
def admin_sync_ncs(
    path: str = Query(..., description="NCS API relative path for units"),
    pages: int = Query(default=20, ge=1, le=500),
    num_of_rows: int = Query(default=100, ge=1, le=1000),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    _require_legacy_ncs_api_enabled()
    try:
        return sync_ncs_units(path=path, pages=pages, num_of_rows=num_of_rows)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"sync failed: {e}") from e


@app.get("/api/integrations/public-inst/{resource}")
def public_inst_proxy(
    resource: str,
    page_no: int = Query(default=1, ge=1),
    num_of_rows: int = Query(default=20, ge=1, le=100),
    data_type: str = Query(default="json", pattern="^(json|xml|JSON|XML)$"),
) -> dict:
    try:
        return fetch_public_inst(resource=resource, page_no=page_no, num_of_rows=num_of_rows, data_type=data_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"external call failed: {e}") from e


@app.get("/api/integrations/ncs")
def ncs_proxy(
    path: str = Query(..., description="NCS API relative path"),
    page_no: int | None = Query(default=None, ge=1),
    num_of_rows: int | None = Query(default=None, ge=1, le=1000),
    data_type: str | None = Query(default=None, alias="type"),
    ncs_job_cd: str | None = Query(default=None),
    ncs_cl_cd: str | None = Query(default=None),
) -> dict:
    _require_legacy_ncs_api_enabled()
    query: dict = {}
    if page_no is not None:
        query["pageNo"] = page_no
    if num_of_rows is not None:
        query["numOfRows"] = num_of_rows
    if data_type:
        query["type"] = data_type
    if ncs_job_cd:
        query["ncsJobCd"] = ncs_job_cd
    if ncs_cl_cd:
        query["ncsClCd"] = ncs_cl_cd
    try:
        return fetch_ncs(path=path, query=query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"external call failed: {e}") from e


@app.get("/api/integrations/ncs/highschool")
def ncs_highschool_proxy(
    mcd_nm: str = Query(..., alias="mcdNm", description="고교 교과목 명"),
    targ_yy: str = Query(..., alias="targYy", description="개정년도 (2015/2018)"),
    cd_name: str | None = Query(default=None, alias="cdName", description="고교 능력단위명(옵션)"),
    return_type: str = Query(default="xml", alias="returnType", pattern="^(xml|json|XML|JSON)$"),
) -> dict:
    _require_legacy_ncs_api_enabled()
    try:
        return fetch_ncs_highschool_course(
            mcd_nm=mcd_nm,
            targ_yy=targ_yy,
            cd_name=cd_name,
            return_type=return_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"external call failed: {e}") from e


@app.get("/api/ncs/diagnose")
def ncs_diagnose(sample_job_cd: str = Query(default="02020101")) -> dict:
    try:
        _ = sample_job_cd
        status = ncs_mcp_status()
        ok = bool(status.get("reachable") and status.get("ksaAvailable"))
        return {
            "provider": "ncs-mcp",
            "ok": ok,
            "ncs_mcp": status,
            "message": "NCS MCP is ready" if ok else "NCS MCP is not ready",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"diagnose failed: {e}") from e


def _extract_result_items(data: dict) -> list[dict]:
    if isinstance(data.get("result"), list):
        return data["result"]
    body = (data.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item")
    if items is None:
        return []
    if isinstance(items, list):
        return items
    return [items]


def _valid_option_text(v: str) -> bool:
    if not v:
        return False
    bad_markers = ["???", "占", "챙"]
    if any(b in v for b in bad_markers):
        return False
    return True


@app.get("/api/alio/recommend")
def alio_recommend(
    desired_job: str = Query(..., min_length=2),
    desired_region: str = Query(default=""),
    strengths: str = Query(..., min_length=5),
    pages: int = Query(default=2, ge=1, le=5),
    per_page: int = Query(default=100, ge=10, le=300),
) -> dict:
    candidates: list[dict] = []
    # 1) Try recruitment API
    try:
        for page in range(1, pages + 1):
            resp = fetch_recruitment("list", page_no=page, num_of_rows=per_page, data_type="json")
            for row in _extract_result_items(resp.get("data", {})):
                pid = str(row.get("recrtPbancTtlPc", "")) + "_" + str(row.get("instCd", ""))
                candidates.append(
                    {
                        "posting_id": pid,
                        "title": row.get("recrtPbancTtl", "") or row.get("title", ""),
                        "institution_name": row.get("instNm", "") or row.get("institutionName", ""),
                        "region": row.get("workRgnNm", "") or row.get("ctpvNm", ""),
                        "r6000": row.get("ncsLclasCd", "") or "R6000_MANAGEMENT",
                        "description": row.get("recrtPbancCn", "") or row.get("dutyCn", ""),
                        "jd_text": row.get("dutyCn", "") or row.get("jobDc", ""),
                    }
                )
            if not _extract_result_items(resp.get("data", {})):
                break
    except Exception:
        candidates = []

    # 2) Fallback to DB postings if recruitment API not available
    if not candidates:
        local = repo_recommend_postings(desired_job=desired_job, desired_region=desired_region, limit=30)
        for p in local:
            details = repo_get_posting(p["posting_id"]) or {}
            req_text = " ".join([r.get("item", "") for r in details.get("requirements_top", [])])
            candidates.append(
                {
                    "posting_id": p["posting_id"],
                    "title": p["title"],
                    "institution_name": p.get("institution_name", ""),
                    "region": p.get("region_code", ""),
                    "r6000": p.get("r6000", "R6000_MANAGEMENT"),
                    "description": req_text,
                    "jd_text": req_text,
                }
            )

    ranked = rank_postings_with_openai(desired_job, desired_region, strengths, candidates)
    result_items = []
    for item in ranked:
        pid = str(item.get("posting_id"))
        src = next((c for c in candidates if str(c.get("posting_id")) == pid), None)
        if src:
            _ALIO_CACHE[pid] = src
        result_items.append(item)
    return {"count": len(result_items), "items": result_items}


@app.get("/api/alio/options")
def alio_options(
    pages: int = Query(default=1, ge=1, le=3),
    per_page: int = Query(default=50, ge=10, le=150),
) -> dict:
    regions: set[str] = set()
    jobs: set[str] = set()

    try:
        for page in range(1, pages + 1):
            resp = fetch_public_inst("list", page_no=page, num_of_rows=per_page, data_type="json", timeout_sec=4.0)
            rows = _extract_result_items(resp.get("data", {}))
            if not rows:
                break
            for r in rows:
                ctpv = str(r.get("ctpvNm", "")).strip()
                if _valid_option_text(ctpv):
                    regions.add(ctpv)
    except Exception:
        pass

    try:
        for page in range(1, pages + 1):
            resp = fetch_recruitment("list", page_no=page, num_of_rows=per_page, data_type="json", timeout_sec=4.0)
            rows = _extract_result_items(resp.get("data", {}))
            if not rows:
                break
            for r in rows:
                title = str(r.get("recrtPbancTtl", "") or r.get("title", "")).strip()
                ncs_name = str(r.get("ncsLclasNm", "")).strip()
                if _valid_option_text(title):
                    jobs.add(title)
                if _valid_option_text(ncs_name):
                    jobs.add(ncs_name)
                region = str(r.get("workRgnNm", "") or r.get("ctpvNm", "")).strip()
                if _valid_option_text(region):
                    regions.add(region)
    except Exception:
        pass

    if not jobs:
        for p in repo_list_postings()[:100]:
            t = str(p.get("title", "")).strip()
            if _valid_option_text(t):
                jobs.add(t)
    if not regions:
        regions.update(["서울", "부산", "대구", "인천", "광주", "대전", "울산", "경기", "강원"])

    return {"jobs": sorted(jobs)[:200], "regions": sorted(regions)[:100]}


@app.post("/api/alio/strategy")
def alio_strategy(payload: dict) -> dict:
    desired_job = str(payload.get("desired_job", "")).strip()
    desired_region = str(payload.get("desired_region", "")).strip()
    strengths = str(payload.get("strengths", "")).strip()
    posting_id = str(payload.get("posting_id", "")).strip()
    if not (desired_job and strengths and posting_id):
        raise HTTPException(status_code=400, detail="desired_job, strengths, posting_id are required")

    posting = _ALIO_CACHE.get(posting_id)
    if not posting:
        # Try local posting id fallback
        try:
            details = repo_get_posting(int(posting_id))
        except Exception:
            details = None
        if not details:
            raise HTTPException(status_code=404, detail="selected posting not found in cache")
        posting = {
            "posting_id": posting_id,
            "title": details.get("title", ""),
            "institution_name": details.get("institution_name", ""),
            "region": details.get("codes", {}).get("R3000", ""),
            "r6000": details.get("codes", {}).get("R6000", "R6000_MANAGEMENT"),
            "jd_text": " ".join([r.get("item", "") for r in details.get("requirements_top", [])]),
        }

    jd_text = posting.get("jd_text") or posting.get("description") or posting.get("title", "")
    strategy = build_strategy_with_openai(
        desired_job=desired_job,
        desired_region=desired_region,
        strengths=strengths,
        posting=posting,
        jd_text=jd_text,
        r6000=posting.get("r6000", "R6000_MANAGEMENT"),
    )
    return {"posting": posting, "strategy": strategy}


@app.get("/api/ncs/sclass-list")
def get_ncs_sclass_list() -> dict:
    """NCS 소분류 283개 목록 반환 (자동완성용)."""
    cats: list[str] = []
    if NCS_SCLASS_CSV.exists():
        with open(NCS_SCLASS_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                n = row.get("NCS_SCLAS_CDNM", "").strip()
                if n and n not in cats:
                    cats.append(n)
    return {"ncs_cats": cats}


@app.post("/api/jd/extract-sclass")
async def extract_sclass_endpoint(jd_file: UploadFile = File(...)) -> dict:
    """
    PDF 직무기술서를 받아 소분류 목록을 추출해서 반환.
    matched  : NCS 282개 사전에 있는 공식 소분류
    unmatched: 사전에 없는 자체 명칭 (NCS 미개발 등)
    """
    name = (jd_file.filename or "").lower()
    data = await jd_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    if name.endswith(".pdf"):
        try:
            return extract_sclass_from_pdf_bytes(data, filename=(jd_file.filename or ""))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    elif name.endswith(".txt"):
        text = data.decode("utf-8", errors="ignore")
        return extract_sclass_from_text(text, filename=(jd_file.filename or ""))
    else:
        raise HTTPException(status_code=400, detail="only .pdf or .txt supported")


@app.post("/api/jd/parse-review")
async def parse_jd_review_endpoint(jd_file: UploadFile = File(...)) -> dict:
    """Parse a JD with Kordoc and return editable human-review fields."""

    data = await jd_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    _check_upload_size(data, "jd_file")
    try:
        parsed = parse_with_kordoc(
            data,
            filename=jd_file.filename or "",
            ocr=os.getenv("KORDOC_OCR", "true").strip().lower() in {"1", "true", "yes", "y"},
        )
        return structure_job_description(parsed, filename=jd_file.filename or "")
    except KordocParseError as exc:
        raise HTTPException(status_code=422, detail=f"Kordoc parse failed: {exc}") from exc


@app.post("/api/notice/parse-review")
async def parse_notice_review_endpoint(notice_file: UploadFile = File(...)) -> dict:
    """Parse a job notice and return editable duty/evaluation text candidates."""

    data = await notice_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="notice_file is empty")
    _check_upload_size(data, "notice_file")
    filename = notice_file.filename or ""
    name = filename.lower()
    try:
        if name.endswith(".txt"):
            parsed = {"markdown": data.decode("utf-8", errors="ignore")}
        else:
            parsed = parse_with_kordoc(
                data,
                filename=filename,
                ocr=os.getenv("KORDOC_OCR", "true").strip().lower() in {"1", "true", "yes", "y"},
            )
        return structure_job_notice(parsed, filename=filename)
    except KordocParseError as exc:
        raise HTTPException(status_code=422, detail=f"Kordoc notice parse failed: {exc}") from exc


@app.post("/api/jd/strategy/upload")
async def jd_strategy_upload(
    jd_file: UploadFile = File(...),
    notice_file: UploadFile | None = File(default=None),
    strengths: str = Form(default=""),
    openai_api_key: str = Form(default=""),
    manual_sclass: str = Form(default=""),
    manual_sclass_add: str = Form(default=""),
    manual_sclass_remove: str = Form(default=""),
    duty_text: str = Form(default=""),
    evaluation_text: str = Form(default=""),
    jd_review_json: str = Form(default=""),
) -> dict:
    # 최적값 고정 (사용자 노출 제거)
    run_top_k, run_ksa_units, run_ksa_factors = FAST_NCS_TOP_K, FAST_KSA_UNITS, FAST_KSA_FACTORS_PER_UNIT
    request_openai_api_key = _sanitize_request_openai_key(openai_api_key)

    async def _read_text(upload: UploadFile | None, label: str) -> tuple[str, bytes, str]:
        if not upload:
            return "", b"", ""
        name = (upload.filename or "").lower()
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"{label} is empty")
        _check_upload_size(data, label)
        if name.endswith(".txt"):
            return data.decode("utf-8", errors="ignore"), data, name
        try:
            parsed = parse_with_kordoc(
                data,
                filename=upload.filename or "",
                ocr=os.getenv("KORDOC_OCR", "true").strip().lower() in {"1", "true", "yes", "y"},
            )
            text = str(parsed.get("markdown") or "")
            if text.strip():
                return text, data, name
        except KordocParseError:
            if not name.endswith(".pdf"):
                raise HTTPException(status_code=422, detail=f"{label} could not be parsed by Kordoc")
        if name.endswith(".pdf"):
            text = extract_pdf_text(data)
            if not text.strip():
                try:
                    text = extract_pdf_text_fallback(data, max_pages=6)
                except Exception:
                    text = ""
            return text, data, name
        raise HTTPException(status_code=400, detail=f"{label} could not be parsed")

    jd_text, jd_bytes, jd_name = await _read_text(jd_file, "jd_file")
    notice_text, _, _ = await _read_text(notice_file, "notice_file")
    review_payload: dict[str, Any] = {}
    if jd_review_json.strip():
        try:
            candidate = json.loads(jd_review_json)
            if isinstance(candidate, dict):
                review_payload = candidate
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"jd_review_json is invalid: {exc}") from exc

    reviewed_fields = review_payload.get("fields") if isinstance(review_payload.get("fields"), dict) else {}
    reviewed_markdown = str((review_payload.get("document") or {}).get("markdown", "")).strip()
    if reviewed_markdown:
        jd_text = reviewed_markdown
    duty_text_clean = str(duty_text or "").strip()
    if not duty_text_clean:
        duty_text_clean = "\n".join(str(x).strip() for x in (reviewed_fields.get("duties") or []) if str(x).strip())
    qualification_text_clean = "\n".join(
        str(x).strip() for x in (reviewed_fields.get("qualifications") or []) if str(x).strip()
    )
    evaluation_text_clean = str(evaluation_text or "").strip()
    manual_sclass_final_terms = _parse_sclass_terms(manual_sclass)
    manual_sclass_add_terms = _parse_sclass_terms(manual_sclass_add)
    manual_sclass_remove_terms = _parse_sclass_terms(manual_sclass_remove)

    if not jd_text.strip():
        fallback_terms = manual_sclass_final_terms or manual_sclass_add_terms
        if fallback_terms:
            jd_text = "소분류: " + ", ".join(fallback_terms)
        else:
            raise HTTPException(status_code=400, detail="no readable text in jd_file")

    vision_terms: list[str] = []
    use_vision_ocr = (str(__import__("os").getenv("ENABLE_VISION_OCR", "false")).strip().lower() in {"1", "true", "yes", "y"})
    # Vision OCR은 텍스트 추출이 실패했을 때만 실행 (텍스트가 있으면 오히려 NCS 매칭 오염 가능)
    if use_vision_ocr and jd_name.endswith(".pdf") and len(jd_text.strip()) < 50:
        vision_terms = extract_focus_terms_from_pdf_vision(jd_bytes, max_pages=2)
    prompt_notice_text = _build_priority_notice_text(
        notice_text=notice_text,
        duty_text=duty_text_clean,
        evaluation_text=evaluation_text_clean,
    )
    if qualification_text_clean:
        prompt_notice_text = (
            f"{prompt_notice_text}\n지원자격:\n{qualification_text_clean}"
        ).strip()
    notice_context = build_notice_context_from_jd(jd_text=jd_text, notice_text=prompt_notice_text, max_chars=5000)

    _require_ncs_mcp_url()
    mcp_only = True
    ncs_source = "ncs-mcp"
    ncs_error = ""

    jd_for_match = jd_text
    if vision_terms:
        jd_for_match = " ".join(vision_terms)

    subcategory_text = extract_subcategory_text(jd_text) if jd_text.strip() else " ".join(vision_terms)
    extracted_small_categories = extract_small_categories_from_jd(jd_text) if jd_text.strip() else []

    # 소분류 확정 규칙:
    # 1) 문서 추출 결과를 기본으로 사용
    # 2) 수기 추가가 있으면 append
    # 3) 수기 삭제가 있으면 제거
    # 4) 레거시 호환: add/remove가 없고 manual_sclass가 있으면 최종 확정 목록으로 간주
    if manual_sclass_add_terms or manual_sclass_remove_terms:
        small_categories = _merge_sclass_terms(
            base_terms=extracted_small_categories,
            add_terms=manual_sclass_add_terms,
            remove_terms=manual_sclass_remove_terms,
        )
    elif manual_sclass_final_terms:
        small_categories = _merge_sclass_terms(
            base_terms=[],
            add_terms=manual_sclass_final_terms,
            remove_terms=[],
        )
    else:
        small_categories = list(extracted_small_categories)

    manual_terms = list(manual_sclass_final_terms or manual_sclass_add_terms)
    if small_categories:
        subcategory_text = f"소분류 후보: {', '.join(small_categories)}\n{subcategory_text}".strip()
    core_small_categories = small_categories[:6]
    inferred_keywords: list[str] = []
    reviewed_keywords: list[str] = []
    ai_sclass_candidates: list[dict] = []
    ai_ncs_code_candidates: list[dict] = []

    # 1) seed 구성: extract_small_categories_from_jd() 결과 + 소분류 텍스트 토큰 + vision 키워드
    import re as _re
    seeds: list[str] = []
    sub_tokens = _re.findall(r"[\uAC00-\uD7A3]{2,12}", subcategory_text or "")
    raw_tokens = _re.findall(r"[\uAC00-\uD7A3]{2,12}", jd_text or "")
    for term in (small_categories + sub_tokens + raw_tokens[:40] + vision_terms):
        t = str(term).strip()
        if t and t not in seeds:
            seeds.append(t)

    show_all_from_small_categories = bool(
        os.getenv("NCS_SHOW_ALL_FROM_SMALL_CATEGORIES", "true").strip().lower() in {"1", "true", "yes", "y"}
    )
    sclass_bundle = resolve_sclass_candidates_bundle(
        jd_text=jd_text,
        small_categories=small_categories,
        manual_terms=manual_terms,
        subcategory_text=subcategory_text,
        doc_name=jd_name,
        show_all_from_small_categories=show_all_from_small_categories,
        enable_ai_fallback=True,
        verified_sclass_limit=_clamp_sclass_limit(os.getenv("NCS_VERIFIED_SCLASS_LIMIT", "4"), default=4),
        verified_min_keep=_clamp_sclass_limit(os.getenv("NCS_VERIFIED_SCLASS_MIN_KEEP", "1"), default=1),
        score_margin=_to_float_or(os.getenv("NCS_VERIFIED_SCORE_MARGIN", "0.18"), 0.18),
        min_confidence=_to_float_or(os.getenv("NCS_VERIFIED_MIN_CONFIDENCE", "0.62"), 0.62),
    )
    reverse_sclass_candidates: list[dict] = sclass_bundle["reverse_sclass_candidates"]
    direct_sclass_candidates_raw: list[dict] = sclass_bundle["direct_sclass_candidates_raw"]
    csv_sclass_candidates: list[dict] = sclass_bundle["csv_sclass_candidates"]
    verified_sclass: list[dict] = sclass_bundle["verified_sclass"]

    # CSV 실패 시 keywords 기반 fallback (NCS API 호출)
    if not verified_sclass and seeds:
        inferred_keywords = infer_keywords_from_subcategory_ai(subcategory_text=subcategory_text, jd_text=jd_for_match)
        reviewed_keywords = review_ocr_terms_with_openai(terms=(inferred_keywords or seeds[:12]), jd_text=jd_for_match)

    ncs_query_terms = [str(v.get("sclass_name", "")).strip() for v in verified_sclass if str(v.get("sclass_name", "")).strip()]
    if not ncs_query_terms:
        ncs_query_terms = [t for t in (reviewed_keywords or inferred_keywords or seeds[:8]) if t]

    reviewed_detail_terms = [
        str(value).strip()
        for value in (reviewed_fields.get("ncs_detail_candidates") or [])
        if str(value).strip()
    ]
    if not mcp_only and not reviewed_detail_terms:
        reviewed_detail_terms = extract_detail_categories_from_jd(jd_text)

    ncs_items: list[dict[str, Any]] = []
    # The confirmed review payload is the gate for the authoritative NCS MCP
    # lookup. It prevents an unreviewed OCR label from driving KSA selection.
    if mcp_only:
        if review_payload.get("review_confirmed") is not True:
            raise HTTPException(
                status_code=400,
                detail="jd_review_json.review_confirmed must be true before NCS MCP lookup",
            )
        lookup_terms = reviewed_detail_terms
        if not lookup_terms:
            raise HTTPException(
                status_code=422,
                detail="reviewed NCS detail candidates are required for MCP lookup",
            )
        try:
            ncs_items = search_units_by_detail(
                lookup_terms,
                max_units=max(20, run_top_k * 12),
            )
        except NcsMcpError as exc:
            raise HTTPException(status_code=502, detail=f"NCS MCP lookup failed: {exc}") from exc
        if not ncs_items:
            raise HTTPException(
                status_code=422,
                detail=f"NCS MCP returned no competency units for reviewed detail terms: {lookup_terms[:8]}",
            )
        ncs_query_terms = lookup_terms
    elif review_payload.get("review_confirmed") and reviewed_detail_terms:
        try:
            ncs_items = search_units_by_detail(
                reviewed_detail_terms,
                max_units=max(20, run_top_k * 12),
            )
            if ncs_items:
                ncs_source = "ncs-mcp"
                ncs_query_terms = reviewed_detail_terms
        except NcsMcpError as exc:
            ncs_error = f"NCS MCP 조회 실패: {exc}"

    # 4) 코드 기반 조회 후, 키워드 기반으로 순차 fallback
    max_sclass_verified = _clamp_sclass_limit(os.getenv("NCS_API_MAX_SCLASS_VERIFIED", "4"), default=4)
    max_sclass_name = _clamp_sclass_limit(os.getenv("NCS_API_MAX_SCLASS_NAME", "4"), default=4)
    if not mcp_only and verified_sclass:
        fetch_limit = min(max_sclass_verified, max(1, len(verified_sclass)))
        ncs_items = fetch_ncs_units_hrdk_by_verified_sclass(verified_sclass, max_sclass=fetch_limit)
        if ncs_items:
            ncs_source = "api-hrdk-sclass-verified"

    if not mcp_only and not ncs_items and ncs_query_terms:
        fetch_limit = min(max_sclass_name, max(1, len(ncs_query_terms)))
        ncs_items = fetch_ncs_units_hrdk_by_sclass_names(ncs_query_terms, max_sclass=fetch_limit)
        if ncs_items:
            ncs_source = "api-hrdk-sclass-name"

    if not mcp_only and not ncs_items and ncs_query_terms:
        ncs_items = fetch_ncs_units_hrdk_by_keywords(ncs_query_terms, max_items=60)
        if ncs_items:
            ncs_source = "api-hrdk-keyword"

    if not mcp_only and not ncs_items and seeds:
        ai_ncs_code_candidates = ai_extract_ncs_cl_codes(seed_terms=seeds[:18], jd_text=jd_for_match, max_items=8)
        if ai_ncs_code_candidates:
            ncs_items = fetch_ncs_units_hrdk_by_cl_codes(ai_ncs_code_candidates, max_items=40)
            if ncs_items:
                ncs_source = "api-hrdk-clcode"

    if not mcp_only and not ncs_items:
        ncs_source = "fallback-local-map"
        ncs_error = "외부 NCS 조회가 불안정하여 로컬 매핑으로 대체했습니다."

    ncs_matches = []
    unit_rank_query_text = _build_priority_query_text(
        base_text=jd_for_match,
        duty_text=duty_text_clean,
        evaluation_text=evaluation_text_clean,
    )
    if ncs_items and ncs_source in {"ncs-mcp", "api-hrdk-code-first", "api-hrdk-clcode", "api-hrdk-keyword", "api-hrdk-sclass-verified", "api-hrdk-sclass-name"}:
        ncs_matches, rerank_mode = rerank_ncs_matches(
            jd_text=unit_rank_query_text or jd_for_match,
            ncs_items=ncs_items,
            top_k=run_top_k,
            preferred_sclass=ncs_query_terms,
            openai_api_key=request_openai_api_key,
        )
        if ncs_matches:
            ncs_source = f"{ncs_source}+ai-rerank" if rerank_mode == "ai" else f"{ncs_source}+rerank"
        else:
            # rank 결과가 비어도 상위 원본을 안전 fallback으로 사용
            for it in ncs_items[:8]:
                ncs_matches.append(
                    {
                        "ncsClCd": str(it.get("ncsClCd", "")).strip(),
                        "compeUnitName": str(it.get("compeUnitName", "")).strip(),
                        "compeUnitLevel": str(it.get("compeUnitLevel", "")).strip(),
                        "ncsSubdCdnm": str(it.get("ncsSubdCdnm", "")).strip(),
                        "compeUnitDef": str(it.get("compeUnitDef", "")).strip(),
                        "score": float(it.get("score", 0.5) or 0.5),
                        "matched_keywords": list(it.get("matched_keywords", []) or []),
                    }
                )
    elif ncs_items and ncs_query_terms:
        def _norm(v: str) -> str:
            return (v or "").replace(" ", "").strip().lower()

        def _canon(v: str) -> str:
            n = _norm(v)
            if n.endswith("사") and len(n) >= 3:
                return n[:-1]
            return n

        soc_norm = {_canon(x) for x in ncs_query_terms}
        matched = []
        for it in ncs_items:
            sclas = _canon(str(it.get("ncsSclasCdnm", "")))
            exact_soc = bool(sclas and sclas in soc_norm)
            if exact_soc:
                matched.append(
                    {
                        "ncsClCd": it.get("ncsClCd", ""),
                        "compeUnitName": it.get("compeUnitName", ""),
                        "compeUnitLevel": it.get("compeUnitLevel", ""),
                        "ncsSubdCdnm": it.get("ncsSubdCdnm", ""),
                        "compeUnitDef": it.get("compeUnitDef", ""),
                        "score": 9.999,
                        "matched_keywords": [it.get("ncsSclasCdnm", "")],
                    }
                )
        seen = set()
        dedup = []
        for m in matched:
            code = str(m.get("ncsClCd", "")).strip()
            if not code or code in seen:
                continue
            dedup.append(m)
            seen.add(code)
            if len(dedup) >= 5:
                break
        ncs_matches = dedup
        if ncs_matches:
            ncs_source = "api-soclass"

    if not mcp_only and not ncs_matches:
        # 마지막 fallback: 내부 샘플 매퍼로 최소 매핑 확보
        local_items = map_ncs(category="R6000_MANAGEMENT", text=jd_for_match, top_k=8)
        for it in (local_items or [])[:8]:
            code = str(it.get("ncsClCd", "")).strip()
            if not code:
                continue
            ncs_matches.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": str(it.get("compeUnitName", "")).strip(),
                    "compeUnitLevel": str(it.get("compeUnitLevel", "")).strip(),
                    "ncsSubdCdnm": str(it.get("ncsSubdCdnm", "")).strip(),
                    "compeUnitDef": str(it.get("compeUnitDef", "")).strip(),
                    "score": float(it.get("score", 0.3) or 0.3),
                    "matched_keywords": list(it.get("matched_keywords", []) or []),
                }
            )
        if ncs_matches:
            ncs_source = "fallback-local-map+rerank"
            ncs_error = "외부 NCS 매핑 실패로 로컬 매퍼를 사용했습니다."
        elif not ncs_error:
            ncs_error = f"NCS 매핑 결과가 없어 JD 기반 질문으로 대체합니다. query={ncs_query_terms[:8]}"
    if mcp_only and not ncs_matches:
        raise HTTPException(
            status_code=422,
            detail=f"NCS MCP units were found, but no NCS matches survived ranking: {ncs_query_terms[:8]}",
        )

    # NCS 평가요소를 수집해 OpenAI 입력에 함께 전달한다.
    # 전체 KSA 후보를 넓게 수집한 뒤, JD 핵심 + 담당업무 텍스트 기준 TF-IDF로 상위만 선별한다.
    ncs_ksa: list[dict[str, Any]] = []
    ncs_ksa_candidates: list[dict[str, Any]] = []
    ksa_query_text = _build_priority_query_text(
        base_text=jd_text,
        duty_text=duty_text_clean,
        evaluation_text=evaluation_text_clean,
    )
    if ncs_matches:
        ksa_rank_top_n = _clamp_int(os.getenv("KSA_RANK_TOP_N", "12"), default=12, lo=6, hi=20)
        ksa_rank_per_unit = _clamp_int(os.getenv("KSA_RANK_PER_UNIT_LIMIT", "2"), default=2, lo=1, hi=4)
        ksa_rank_units = _clamp_int(os.getenv("KSA_RANK_MAX_UNITS", "12"), default=12, lo=2, hi=30)
        ksa_candidate_per_unit = _clamp_int(os.getenv("KSA_CANDIDATE_PER_UNIT", "12"), default=12, lo=3, hi=24)
        ksa_sim_weight = _to_float_or(os.getenv("KSA_SIMILARITY_WEIGHT", "0.75"), 0.75)
        ksa_unit_weight = _to_float_or(os.getenv("KSA_UNIT_WEIGHT", "0.25"), 0.25)

        ksa_units = _collect_ksa_candidate_units(
            primary_units=ncs_matches,
            secondary_units=ncs_items,
            max_units=ksa_rank_units,
        )
        if not ksa_units:
            ksa_units = _collect_ksa_candidate_units(
                primary_units=ncs_matches[:run_top_k],
                secondary_units=None,
                max_units=max(1, run_top_k),
            )

        ncs_ksa_candidates = _fetch_ncs_ksa_or_502(
            ncs_matches=ksa_units,
            max_units=len(ksa_units),
            max_factors_per_unit=ksa_candidate_per_unit,
        )
        unit_scores: dict[str, float] = {}
        for x in (ksa_units or []):
            code = str(x.get("ncsClCd", "")).strip()
            if not code:
                continue
            try:
                unit_scores[code] = float(x.get("score", 1.0) or 1.0)
            except Exception:
                unit_scores[code] = 1.0
        ncs_ksa = rank_ksa_factors_by_query(
            ksa_rows=ncs_ksa_candidates,
            query_text=ksa_query_text,
            unit_scores=unit_scores,
            target_count=ksa_rank_top_n,
            per_unit_limit=ksa_rank_per_unit,
            similarity_weight=ksa_sim_weight,
            unit_weight=ksa_unit_weight,
            ngram_min=2,
            ngram_max=4,
        )
        if not ncs_ksa:
            ncs_ksa = _fetch_ncs_ksa_or_502(
                ncs_matches=ncs_matches[:run_top_k],
                max_units=min(run_ksa_units, len(ncs_matches)),
                max_factors_per_unit=run_ksa_factors,
            )
    ncs_factor_sources = sorted(
        {
            str(x.get("factorSource", "")).strip()
            for x in (ncs_ksa or [])
            if str(x.get("factorSource", "")).strip()
        }
    )
    ncs_context = build_ncs_context_pack(
        jd_text=jd_for_match,
        notice_text=notice_context,
        ncs_items=ncs_items,
        ncs_matches=ncs_matches,
    )
    enable_ai_refine = bool(inferred_keywords or reviewed_keywords or ai_ncs_code_candidates)

    try:
        loop = asyncio.get_event_loop()
        strategy = await loop.run_in_executor(
            None,
            functools.partial(
                build_jd_strategy_with_openai,
                jd_text=jd_text,
                notice_text=notice_context,
                strengths=strengths,
                region="",
                ncs_matches=ncs_matches,
                ncs_ksa=ncs_ksa,
                ncs_context=ncs_context,
                duty_text=duty_text_clean,
                evaluation_text=evaluation_text_clean,
                desired_job="",
                api_key_override=request_openai_api_key,
            ),
        )
    except Exception as e:
        strategy = build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message=f"model_generation_failed: {e}",
            target_count=24,
        )

    return {
        "filename": jd_file.filename,
        "notice_filename": notice_file.filename if notice_file else "",
        "jd_text_preview": jd_text[:1200],
        "notice_text_preview": notice_text[:1200],
        "notice_context_preview": notice_context[:1200],
        "duty_text_preview": duty_text_clean[:1200],
        "qualification_text_preview": qualification_text_clean[:1200],
        "evaluation_text_preview": evaluation_text_clean[:1200],
        "jd_review_confirmed": review_payload.get("review_confirmed") is True,
        "jd_review": review_payload if review_payload else None,
        "profile_used": bool((strengths or "").strip()),
        "ncs_source": ncs_source,
        "ncs_error": ncs_error,
        "openai_key_source": "request" if request_openai_api_key else ("env" if settings.openai_key() else "missing"),
        "extracted_focus_terms": vision_terms,
        "subcategory_text_preview": subcategory_text[:800],
        "small_categories_extracted": extracted_small_categories,
        "small_categories": small_categories,
        "core_small_categories": core_small_categories,
        "inferred_keywords": inferred_keywords,
        "reviewed_keywords": reviewed_keywords,
        "pipeline_mode": ("direct-ncs" if not enable_ai_refine else "ai-refine+ncs"),
        "manual_sclass": manual_terms,
        "manual_sclass_add": manual_sclass_add_terms,
        "manual_sclass_remove": manual_sclass_remove_terms,
        "manual_sclass_final": manual_sclass_final_terms,
        "ai_sclass_candidates": ai_sclass_candidates,
        "csv_sclass_candidates": csv_sclass_candidates,
        "ai_ncs_code_candidates": ai_ncs_code_candidates,
        "verified_sclass": verified_sclass,
        "ncs_code_nos": [str(x.get("ncs_code_no", "")) for x in verified_sclass if str(x.get("ncs_code_no", ""))],
        "ncs_matches": ncs_matches,
        "ncs_ksa": ncs_ksa,
        "ncs_ksa_candidate_count": len(ncs_ksa_candidates),
        "ncs_factor_sources": ncs_factor_sources,
        "runtime_knobs": {
            "ncs_top_k": run_top_k,
            "ksa_units": run_ksa_units,
            "ksa_factors_per_unit": run_ksa_factors,
        },
        "ncs_context": ncs_context,
        "strategy": strategy,
    }


@app.post("/api/questions/generate-from-text")
async def generate_questions_from_text(payload: dict) -> dict:
    notice_text = str(payload.get("notice_text", "")).strip()
    duty_text = str(payload.get("duty_text", "")).strip()
    evaluation_text = str(payload.get("evaluation_text", "")).strip()
    request_openai_api_key = _sanitize_request_openai_key(payload.get("openai_api_key", ""))
    selected_ncs = payload.get("selected_ncs", [])
    knobs = payload.get("runtime_knobs", {}) if isinstance(payload.get("runtime_knobs", {}), dict) else {}
    run_top_k, run_ksa_units, run_ksa_factors = _clamp_runtime_knobs(
        ncs_top_k=knobs.get("ncs_top_k"),
        ksa_units=knobs.get("ksa_units"),
        ksa_factors_per_unit=knobs.get("ksa_factors_per_unit"),
    )
    if not notice_text:
        raise HTTPException(status_code=400, detail="notice_text is required")
    if not isinstance(selected_ncs, list) or not selected_ncs:
        raise HTTPException(status_code=400, detail="selected_ncs is required")
    _require_ncs_mcp_url()

    ncs_matches: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for row in selected_ncs:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ncsClCd", "")).strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        ncs_matches.append(
            {
                "ncsClCd": code,
                "compeUnitName": str(row.get("compeUnitName", "")).strip() or f"NCS-{code}",
                "compeUnitLevel": str(row.get("compeUnitLevel", "")).strip(),
                "ncsSubdCdnm": str(row.get("ncsSubdCdnm", "")).strip(),
                "compeUnitDef": str(row.get("compeUnitDef", "")).strip(),
                "score": 1.0,
                "matched_keywords": [code],
            }
        )
    if not ncs_matches:
        raise HTTPException(status_code=400, detail="selected_ncs has no valid ncsClCd")

    prompt_notice_text = _build_priority_notice_text(
        notice_text=notice_text,
        duty_text=duty_text,
        evaluation_text=evaluation_text,
    )

    # NCS 평가요소를 수집해 OpenAI 입력에 함께 전달한다.
    ksa_rank_top_n = _clamp_int(os.getenv("KSA_RANK_TOP_N", "12"), default=12, lo=6, hi=20)
    ksa_rank_per_unit = _clamp_int(os.getenv("KSA_RANK_PER_UNIT_LIMIT", "2"), default=2, lo=1, hi=4)
    ksa_rank_units = _clamp_int(os.getenv("KSA_RANK_MAX_UNITS", "12"), default=12, lo=2, hi=30)
    ksa_candidate_per_unit = _clamp_int(os.getenv("KSA_CANDIDATE_PER_UNIT", "12"), default=12, lo=3, hi=24)
    ksa_sim_weight = _to_float_or(os.getenv("KSA_SIMILARITY_WEIGHT", "0.75"), 0.75)
    ksa_unit_weight = _to_float_or(os.getenv("KSA_UNIT_WEIGHT", "0.25"), 0.25)

    ksa_units = _collect_ksa_candidate_units(
        primary_units=ncs_matches,
        secondary_units=None,
        max_units=min(ksa_rank_units, len(ncs_matches)),
    )
    ncs_ksa_candidates = _fetch_ncs_ksa_or_502(
        ncs_matches=ksa_units,
        max_units=len(ksa_units),
        max_factors_per_unit=ksa_candidate_per_unit,
    )
    unit_scores = {str(x.get("ncsClCd", "")).strip(): 1.0 for x in (ksa_units or []) if str(x.get("ncsClCd", "")).strip()}
    ksa_query_text = _build_priority_query_text(
        base_text=notice_text,
        duty_text=duty_text,
        evaluation_text=evaluation_text,
    )
    ncs_ksa = rank_ksa_factors_by_query(
        ksa_rows=ncs_ksa_candidates,
        query_text=ksa_query_text,
        unit_scores=unit_scores,
        target_count=ksa_rank_top_n,
        per_unit_limit=ksa_rank_per_unit,
        similarity_weight=ksa_sim_weight,
        unit_weight=ksa_unit_weight,
        ngram_min=2,
        ngram_max=4,
    )
    if not ncs_ksa:
        ncs_ksa = _fetch_ncs_ksa_or_502(
            ncs_matches=ncs_matches[:run_top_k],
            max_units=min(run_ksa_units, len(ncs_matches)),
            max_factors_per_unit=run_ksa_factors,
        )
    ncs_factor_sources = sorted(
        {
            str(x.get("factorSource", "")).strip()
            for x in (ncs_ksa or [])
            if str(x.get("factorSource", "")).strip()
        }
    )
    ncs_context = build_ncs_context_pack(
        jd_text=notice_text,
        notice_text=prompt_notice_text,
        ncs_items=ncs_matches,
        ncs_matches=ncs_matches,
    )

    try:
        loop = asyncio.get_event_loop()
        strategy = await loop.run_in_executor(
            None,
            functools.partial(
                build_jd_strategy_with_openai,
                jd_text=notice_text,
                notice_text=prompt_notice_text,
                strengths="",
                region="",
                ncs_matches=ncs_matches,
                ncs_ksa=ncs_ksa,
                ncs_context=ncs_context,
                duty_text=duty_text,
                evaluation_text=evaluation_text,
                desired_job="",
                api_key_override=request_openai_api_key,
            ),
        )
    except Exception as e:
        strategy = build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message=f"model_generation_failed: {e}",
            target_count=24,
        )

    return {
        "input_mode": "manual_text+ncs_click",
        "filename": "",
        "notice_filename": "직무내용 직접입력",
        "jd_text_preview": notice_text[:1200],
        "notice_text_preview": notice_text[:1200],
        "notice_context_preview": notice_text[:1200],
        "duty_text_preview": duty_text[:1200],
        "evaluation_text_preview": evaluation_text[:1200],
        "profile_used": False,
        "ncs_source": "manual-selected",
        "ncs_error": "",
        "openai_key_source": "request" if request_openai_api_key else ("env" if settings.openai_key() else "missing"),
        "extracted_focus_terms": [],
        "subcategory_text_preview": "",
        "small_categories": [],
        "core_small_categories": [],
        "inferred_keywords": [],
        "reviewed_keywords": [],
        "pipeline_mode": "manual-ncs-select",
        "manual_sclass": [],
        "ai_sclass_candidates": [],
        "csv_sclass_candidates": [],
        "ai_ncs_code_candidates": [],
        "verified_sclass": [],
        "ncs_code_nos": [],
        "ncs_matches": ncs_matches,
        "ncs_ksa": ncs_ksa,
        "ncs_ksa_candidate_count": len(ncs_ksa_candidates),
        "ncs_factor_sources": ncs_factor_sources,
        "runtime_knobs": {
            "ncs_top_k": run_top_k,
            "ksa_units": run_ksa_units,
            "ksa_factors_per_unit": run_ksa_factors,
        },
        "ncs_context": ncs_context,
        "strategy": strategy,
    }


@app.post("/api/questions/generate-personalized")
def generate_questions_personalized(
    ncs_code: str = Query(..., description="NCS competency code (e.g., 02020302)"),
    competency_name: str = Query("", description="NCS competency unit name (optional)"),
    job_posting: str = Query("", description="Job posting text (company, position, requirements)"),
    user_profile: str = Query("", description="User profile text (experience, skills, background)"),
    target_count: int = Query(12, description="Number of question templates (default 12)"),
) -> dict:
    """Generate personalized interview question templates based on job posting and user profile.

    IMPROVEMENT: Questions are TEMPLATES that incorporate job posting and user profile content.
    All questions are examples meant to guide actual interview preparation.

    Query Parameters:
        ncs_code: NCS competency code (required, e.g., '02020302')
        competency_name: Optional competency name for better context
        job_posting: Job posting/recruitment info text (company, position, requirements)
        user_profile: User profile/resume text (experience, skills, achievements)
        target_count: Number of question templates (1-12, default 12)

    Returns:
        - ncs_code: Input NCS code
        - competency_name: Competency unit name
        - company_from_posting: Extracted company/organization name
        - questions: Array of personalized question templates
        - note: Reminder that these are templates for adaptation
    """
    try:
        if not ncs_code or not ncs_code.strip():
            raise HTTPException(status_code=400, detail="ncs_code is required")

        if len(ncs_code.strip()) < 4:
            raise HTTPException(status_code=400, detail="ncs_code format invalid (e.g., 02020302)")

        if target_count < 1 or target_count > 12:
            target_count = min(max(target_count, 1), 12)

        # Generate personalized questions
        result = generate_personalized_interview_questions(
            ncs_code=ncs_code.strip(),
            competency_name=competency_name.strip() or "",
            job_posting=job_posting.strip() or "",
            user_profile=user_profile.strip() or "",
            target_count=target_count,
        )

        return {
            "status": "success",
            "data": result,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Question generation failed: {str(e)[:200]}"
        )


@app.post("/api/questions/generate-by-ncs-code")
def generate_questions_by_ncs_code(
    ncs_code: str = Query(..., description="NCS competency code (e.g., 02020302)"),
    competency_name: str = Query("", description="NCS competency unit name (optional)"),
    target_count: int = Query(10, description="Number of questions to generate (default 10)"),
    include_followups: bool = Query(True, description="Include follow-up questions (default True)"),
) -> dict:
    """Generate interview questions using only NCS code (no job description file required).

    IMPROVEMENT: New endpoint for generating diverse interview questions directly from NCS codes.
    Supports 4 question types: behavioral, situational, technical, development-oriented.

    Query Parameters:
        ncs_code: NCS competency code (required, e.g. '02020302')
        competency_name: Optional competency name for context
        target_count: Number of main questions (1-25, default 10)
        include_followups: Include follow-up questions (default True)

    Returns:
        - ncs_code: Input NCS code
        - competency_name: Competency unit name
        - main_questions: Array of behavioral/situational/technical questions
        - follow_up_questions: Array of follow-up questions for depth
        - total_count: Total question count
    """
    try:
        # Validate inputs
        if not ncs_code or not ncs_code.strip():
            raise HTTPException(status_code=400, detail="ncs_code is required")

        if len(ncs_code.strip()) < 4:
            raise HTTPException(status_code=400, detail="ncs_code format invalid (e.g., 02020302)")

        if target_count < 1 or target_count > 25:
            target_count = min(max(target_count, 1), 25)

        # Generate questions
        result = generate_interview_questions_by_ncs_code(
            ncs_code=ncs_code.strip(),
            competency_name=competency_name.strip() or "",
            target_count=target_count,
            include_followups=include_followups,
        )

        if str(result.get("generation_mode", "")).strip() == "ai_generation_empty_no_fallback":
            raise HTTPException(
                status_code=503,
                detail=(
                    "AI question generation failed without template fallback. "
                    "Check OpenAI network/socket permissions and retry."
                ),
            )

        return {
            "status": "success",
            "data": result,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Question generation failed: {str(e)[:200]}"
        )


@app.get("/api/questions/templates")
def get_question_templates() -> dict:
    """Get available question types and templates.

    Returns information about supported question types, evaluation criteria, etc.
    """
    return {
        "question_types": [
            {
                "type": "행동기반",
                "description": "과거 경험에 기반한 행동 사례 질문",
                "count": 5,
                "key_focus": ["근거기반 판단", "문제해결", "실행력"]
            },
            {
                "type": "상황면접",
                "description": "특정 상황에서의 대응 방식을 묻는 질문",
                "count": 3,
                "key_focus": ["의사결정", "우선순위", "위기대응"]
            },
            {
                "type": "직무지식",
                "description": "직무 관련 지식과 학습 현황을 묻는 질문",
                "count": 2,
                "key_focus": ["전문성", "학습력", "이해도"]
            },
            {
                "type": "개발지향",
                "description": "개인 개발과 미래 계획에 관한 질문",
                "count": 2,
                "key_focus": ["성장의욕", "학습계획", "비전"]
            },
            {
                "type": "협업성",
                "description": "팀 협업과 상호작용에 관한 질문",
                "count": 2,
                "key_focus": ["의사소통", "협업", "갈등해결"]
            },
            {
                "type": "미래대비",
                "description": "향후 역할 수행 능력에 관한 질문",
                "count": 2,
                "key_focus": ["준비도", "전략성", "실행가능성"]
            },
            {
                "type": "성찰",
                "description": "경험 성찰과 성장에 관한 질문",
                "count": 2,
                "key_focus": ["자기인식", "학습", "개선"]
            }
        ],
        "total_templates": 18,
        "follow_up_templates": 8,
        "typical_question_count": 20,
        "estimated_interview_time_minutes": 60,
    }


@app.post("/api/questions/generate-batch")
def generate_batch_diverse_questions(
    ncs_code: str = Query(..., description="NCS code (e.g., 0202010203_19v2)"),
    competency_name: str = Query("", description="Competency name"),
    batch_count: int = Query(20, description="Number of questions to generate (10-50, default 20)"),
) -> dict:
    """Generate batch of diverse interview questions (10-50 questions).

    IMPORTANT: AI generates completely different questions EVERY TIME - no caching!
    Each request = Fresh questions. No repetition ever.

    Format: #1, #2, #3... with question type, competency, NCS code, question, follow-up, eval points

    Args:
        ncs_code: NCS code (required)
        competency_name: Competency name (optional)
        batch_count: Total questions (10-50, default 20)

    Returns:
        Batch of diverse questions in numbered format
    """
    import uuid

    try:
        if not ncs_code or not ncs_code.strip():
            raise HTTPException(status_code=400, detail="ncs_code required")

        batch_count = min(max(batch_count, 10), 50)

        # Generate multiple rounds of diverse questions with strict deduplication
        final_questions = []
        seen_questions = set()
        seen_question_texts = []
        history_questions = _load_question_history(ncs_code=ncs_code.strip(), competency_name=competency_name.strip())
        history_keys = {
            normalize_question_dedup_key(q)
            for q in history_questions
            if normalize_question_dedup_key(q)
        }
        max_attempts = 50  # Prevent infinite loops
        attempt = 0

        while len(final_questions) < batch_count and attempt < max_attempts:
            attempt += 1
            result = generate_diverse_interview_questions(
                ncs_code=ncs_code.strip(),
                competency_name=competency_name.strip() or "",
                target_count=6,
            )

            for q in result["questions"]:
                if len(final_questions) >= batch_count:
                    break

                # Normalize question key and block exact duplicates.
                q_text = str(q.get("question", "")).strip()
                q_key = normalize_question_dedup_key(q_text)
                if not q_key:
                    continue

                if q_key in seen_questions or q_key in history_keys:
                    continue

                # Block near-duplicate questions with minor wording changes.
                if any(is_similar_question_text(q_text, prev) for prev in seen_question_texts):
                    continue
                if any(is_similar_question_text(q_text, prev) for prev in history_questions):
                    continue

                final_questions.append(q)
                seen_questions.add(q_key)
                seen_question_texts.append(q_text)
        for i, q in enumerate(final_questions, 1):
            q["number"] = i

        _save_question_history(
            ncs_code=ncs_code.strip(),
            competency_name=competency_name.strip(),
            new_questions=[str(q.get("question", "")).strip() for q in final_questions],
        )

        response_data = {
            "status": "success",
            "data": {
                "ncs_code": ncs_code,
                "competency_name": competency_name or f"NCS-{ncs_code}",
                "batch_count": len(final_questions),
                "questions": final_questions,
                "note": "각 질문은 AI가 생성한 고유한 질문입니다. 매 요청마다 다른 질문이 생성됩니다.",
            },
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            "request_id": str(uuid.uuid4()),  # Unique ID to prevent caching
        }

        # Return with NO-CACHE headers
        return JSONResponse(
            content=response_data,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Content-Type-Options": "nosniff",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)[:200]}")


@app.post("/api/questions/generate-diverse")
def generate_diverse_questions(
    ncs_code: str = Query(..., description="NCS competency code (e.g., 0202010102_19v2)"),
    competency_name: str = Query("", description="Competency unit name (optional)"),
    job_posting: str = Query("", description="Job posting text for context (optional)"),
    target_count: int = Query(6, description="Number of diverse question types (1-6, default 6)"),
) -> dict:
    """Generate 6 diverse interview question types.

    IMPROVEMENT: Creates highly varied questions with 6 different formats:
    1. STAR (행동기반) - Specific past success with STAR structure
    2. BEI (행동사건) - Most difficult moment experienced
    3. 케이스 (Case analysis) - Problem-solving scenario
    4. SJT (Situational) - Decision-making under pressure
    5. 압박면접 (Pressure) - Handling failure/criticism
    6. 비판적사건 (Critical incident) - Learning moment/awareness change

    Each question includes:
    - Question number and type
    - Main question
    - Follow-up question
    - Evaluation points (역량 평가 포인트)

    Query Parameters:
        ncs_code: NCS competency code (required, e.g., '0202010102_19v2')
        competency_name: Competency unit name (optional)
        job_posting: Job posting context text (optional)
        target_count: How many diverse types (1-6, default all 6)

    Returns:
        List of 6 diverse questions, each with different evaluation angle
    """
    try:
        if not ncs_code or not ncs_code.strip():
            raise HTTPException(status_code=400, detail="ncs_code is required")

        if target_count < 1 or target_count > 6:
            target_count = min(max(target_count, 1), 6)

        ncs_code_clean = ncs_code.strip()
        competency_name_clean = competency_name.strip()
        history_questions = _load_question_history(
            ncs_code=ncs_code_clean,
            competency_name=competency_name_clean,
        )
        history_keys = {
            normalize_question_dedup_key(q)
            for q in history_questions
            if normalize_question_dedup_key(q)
        }

        final_questions = []
        seen_keys = set()
        seen_texts = []
        max_attempts = 20
        attempt = 0
        raw_result = {
            "ncs_code": ncs_code_clean,
            "competency_name": competency_name_clean or f"NCS-{ncs_code_clean}",
            "generation_mode": "ai_powered_diverse",
        }

        while len(final_questions) < target_count and attempt < max_attempts:
            attempt += 1
            needed = min(6, max(target_count - len(final_questions), 1))
            raw_result = generate_diverse_interview_questions(
                ncs_code=ncs_code_clean,
                competency_name=competency_name_clean or "",
                job_posting=job_posting.strip() or "",
                target_count=needed,
            )

            for q in raw_result.get("questions", []):
                if len(final_questions) >= target_count:
                    break
                q_text = str(q.get("question", "")).strip()
                q_key = normalize_question_dedup_key(q_text)
                if not q_key:
                    continue
                if q_key in seen_keys or q_key in history_keys:
                    continue
                if any(is_similar_question_text(q_text, prev) for prev in seen_texts):
                    continue
                if any(is_similar_question_text(q_text, prev) for prev in history_questions):
                    continue
                final_questions.append(q)
                seen_keys.add(q_key)
                seen_texts.append(q_text)

        for i, q in enumerate(final_questions, 1):
            q["number"] = i

        _save_question_history(
            ncs_code=ncs_code_clean,
            competency_name=competency_name_clean,
            new_questions=[str(q.get("question", "")).strip() for q in final_questions],
        )

        result = {
            "ncs_code": raw_result.get("ncs_code", ncs_code_clean),
            "competency_name": raw_result.get("competency_name", competency_name_clean or f"NCS-{ncs_code_clean}"),
            "generation_mode": raw_result.get("generation_mode", "ai_powered_diverse"),
            "questions": final_questions,
            "question_count": len(final_questions),
            "note": "동일/유사 질문을 제거한 결과입니다. 매 요청마다 새 질문을 우선 생성합니다.",
        }

        return {
            "status": "success",
            "data": result,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Question generation failed: {str(e)[:200]}"
        )
