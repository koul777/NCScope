from __future__ import annotations

import asyncio
import csv
import functools
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.init_db import init_db
from app.repository import create_posting as repo_create_posting
from app.repository import fetch_posting_for_report, get_posting as repo_get_posting
from app.repository import list_postings as repo_list_postings
from app.repository import recommend_postings as repo_recommend_postings
from app.repository import record_audit_log
from app.repository import save_match_result
from app.schemas import AiInterviewRequest, AiInterviewResponse, PostingCreate, ReportCreate, ReportOut
from app.services.ai_strategy import build_strategy_with_openai, rank_postings_with_openai
from app.services.external_api import fetch_ncs, fetch_ncs_highschool_course, fetch_public_inst, fetch_recruitment
from app.services.kordoc_parser import KordocParseError, parse_with_kordoc, structure_job_description, structure_job_notice
from app.services.ncs_mcp_client import NcsMcpError, ncs_mcp_status, search_units_by_detail, suggest_units_by_text
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
FAST_KSA_UNITS = 6          # KSA 수집 능력단위 6개 (기본 6개 면접기법 커버)
FAST_KSA_FACTORS_PER_UNIT = 2  # 단위당 KSA 2개 (총 6개)
SUPPORTED_INTERVIEW_METHODS = (
    "경험면접",
    "상황면접",
    "발표면접",
    "토론면접",
    "인바스켓면접",
    "직무지식면접",
)
OPTIONAL_INTERVIEW_METHODS = (
    "창의적 문제해결력면접",
)
QUALITY_INTERVIEW_METHODS = SUPPORTED_INTERVIEW_METHODS + OPTIONAL_INTERVIEW_METHODS
MODEL_PRESERVED_QUESTION_SOURCES = {
    "model",
    "model_main_template_followups",
    "model_main_repaired_followups",
}
_BLIND_HIRING_CUE_RE = re.compile(
    r"(가족|부모|형제|배우자|자녀|나이|연령|출신\s*학교|학교명|학벌|출신\s*지역|출신지역|고향|"
    r"생년\s*월일|출생\s*(?:연도|년도|일|지)|몇\s*살|만\s*\d+\s*세|"
    r"혼인|결혼|기혼|미혼|결혼\s*여부|혼인\s*상태|임신|출산|자녀\s*계획|출산\s*계획|"
    r"외모|용모|(?:키|신장)\s*(?:가|는|를|와|및|/|,|:|：|\d)|체중|성별|종교|정치\s*성향|"
    r"병역|군필|미필|군\s*복무|복무\s*기간|전역|혈액형)"
)


def _contains_blind_hiring_cue(value: Any) -> bool:
    return bool(_BLIND_HIRING_CUE_RE.search(str(value or "")))


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


def _norm_detail_coverage_key(value: Any) -> str:
    return re.sub(r"[\s\-\_/|(),.·・]+", "", str(value or "")).strip().lower()


def _detail_lookup_coverage(
    lookup_terms: list[str],
    ncs_items: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    requested: dict[str, str] = {}
    for term in lookup_terms or []:
        text = str(term or "").strip()
        key = _norm_detail_coverage_key(text)
        if text and key and key not in requested:
            requested[key] = text
    if not requested:
        return [], []

    covered_keys: set[str] = set()
    for row in ncs_items or []:
        if not isinstance(row, dict):
            continue
        for field in ("matchedDetailName", "reviewed_detail", "confirmed_detail", "ncs_detail", "ncsSubdCdnm"):
            key = _norm_detail_coverage_key(row.get(field))
            if key in requested:
                covered_keys.add(key)

    matched = [term for key, term in requested.items() if key in covered_keys]
    unmatched = [term for key, term in requested.items() if key not in covered_keys]
    return matched, unmatched


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


def _merge_review_text(*values: Any, max_chars: int = 3000) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        parts = value if isinstance(value, list) else re.split(r"\n+", str(value or ""))
        for part in parts:
            text = str(part or "").strip()
            key = re.sub(r"\s+", "", text).lower()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
    return "\n".join(out)[:max_chars].strip()


def _parse_question_plan_json(raw: str, reviewed_detail_terms: list[str]) -> dict[str, Any]:
    fallback_terms = _parse_sclass_terms("\n".join(str(term).strip() for term in (reviewed_detail_terms or []) if str(term).strip()))
    default_items = [
        {"detail": term, "enabled": True, "main_count": 3, "follow_up_count": 3}
        for term in fallback_terms
    ]
    if not str(raw or "").strip():
        items = default_items
    else:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"question_plan_json is invalid: {exc}") from exc
        candidates = parsed.get("items") if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []
        items = []
        for row in candidates or []:
            if not isinstance(row, dict):
                continue
            detail = str(row.get("detail") or row.get("name") or row.get("ncs_detail") or "").strip()
            if not detail:
                continue
            enabled = row.get("enabled", True)
            enabled_bool = not (enabled is False or str(enabled).strip().lower() in {"0", "false", "no", "n"})
            try:
                main_count = int(row.get("main_count", row.get("question_count", 3)) or 0)
            except Exception:
                main_count = 3
            try:
                follow_up_count = int(row.get("follow_up_count", row.get("followups", 3)) or 0)
            except Exception:
                follow_up_count = 3
            items.append(
                {
                    "detail": detail,
                    "enabled": enabled_bool,
                    "main_count": max(0, min(10, main_count)),
                    "follow_up_count": max(0, min(5, follow_up_count)),
                }
            )

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        key = _norm_sclass_key(str(item.get("detail", "")))
        if not key or key in seen:
            continue
        seen.add(key)
        main_count = max(0, min(10, int(item.get("main_count", 0) or 0)))
        normalized.append(
            {
                "detail": str(item.get("detail", "")).strip(),
                "enabled": bool(item.get("enabled", True)) and main_count > 0,
                "main_count": main_count,
                "follow_up_count": max(0, min(5, int(item.get("follow_up_count", 3) or 0))),
            }
        )
    selected = [item for item in normalized if item["enabled"]]
    if not selected and fallback_terms:
        selected = default_items
        normalized = default_items
    total_main = sum(int(item.get("main_count", 0) or 0) for item in selected)
    total_main = max(1, min(40, total_main)) if selected else 0
    selected_terms = [str(item.get("detail", "")).strip() for item in selected if str(item.get("detail", "")).strip()]
    follow_up_count = max([int(item.get("follow_up_count", 3) or 0) for item in selected] or [3])
    question_sequence: list[dict[str, Any]] = []
    for item in selected:
        for _ in range(max(0, int(item.get("main_count", 0) or 0))):
            question_sequence.append(
                {
                    "detail": str(item.get("detail", "")).strip(),
                    "follow_up_count": max(0, min(5, int(item.get("follow_up_count", 3) or 0))),
                }
            )
    return {
        "items": normalized,
        "selected_items": selected,
        "selected_terms": selected_terms,
        "question_sequence": question_sequence[:40],
        "total_main_count": total_main,
        "follow_up_count": max(0, min(5, follow_up_count)),
    }


def _restrict_question_plan_to_terms(question_plan: dict[str, Any], allowed_terms: list[str]) -> dict[str, Any]:
    allowed: list[str] = []
    seen_allowed: set[str] = set()
    for term in allowed_terms or []:
        text = str(term or "").strip()
        key = _norm_sclass_key(text)
        if text and key and key not in seen_allowed:
            seen_allowed.add(key)
            allowed.append(text)
    if not allowed:
        return question_plan
    allowed_keys = {_norm_sclass_key(term) for term in allowed}
    kept = [
        dict(item)
        for item in (question_plan.get("items") or [])
        if isinstance(item, dict) and _norm_sclass_key(str(item.get("detail") or "")) in allowed_keys
    ]
    if kept:
        return _parse_question_plan_json(json.dumps({"items": kept}, ensure_ascii=False), allowed)
    follow_up_count = max(3, min(5, int(question_plan.get("follow_up_count", 3) or 3)))
    return _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {"detail": term, "enabled": True, "main_count": 3, "follow_up_count": follow_up_count}
                    for term in allowed
                ]
            },
            ensure_ascii=False,
        ),
        allowed,
    )


def _parse_interview_methods(raw: str) -> list[str]:
    allowed = {
        "behavior": "경험면접",
        "behavioral": "경험면접",
        "행동형": "경험면접",
        "행동관찰면접": "경험면접",
        "행동관찰": "경험면접",
        "경험형": "경험면접",
        "경험면접": "경험면접",
        "experience": "경험면접",
        "situation": "상황면접",
        "situational": "상황면접",
        "상황형": "상황면접",
        "상황면접": "상황면접",
        "presentation": "발표면접",
        "pt": "발표면접",
        "pt면접": "발표면접",
        "발표": "발표면접",
        "발표형": "발표면접",
        "발표면접": "발표면접",
        "discussion": "토론면접",
        "debate": "토론면접",
        "토론": "토론면접",
        "토론형": "토론면접",
        "토론면접": "토론면접",
        "토의": "토론면접",
        "토의형": "토론면접",
        "토의면접": "토론면접",
        "inbasket": "인바스켓면접",
        "in-basket": "인바스켓면접",
        "인바스켓": "인바스켓면접",
        "인바스켓형": "인바스켓면접",
        "인바스켓면접": "인바스켓면접",
        "job_knowledge": "직무지식면접",
        "knowledge": "직무지식면접",
        "직무지식": "직무지식면접",
        "직무지식형": "직무지식면접",
        "직무지식면접": "직무지식면접",
        "지식": "직무지식면접",
        "지식형": "직무지식면접",
        "지식면접": "직무지식면접",
        "creative": "창의적 문제해결력면접",
        "creative_problem_solving": "창의적 문제해결력면접",
        "problem_solving": "창의적 문제해결력면접",
        "창의": "창의적 문제해결력면접",
        "창의형": "창의적 문제해결력면접",
        "창의적문제해결": "창의적 문제해결력면접",
        "창의적문제해결력": "창의적 문제해결력면접",
        "창의적문제해결력면접": "창의적 문제해결력면접",
        "창의적 문제해결력": "창의적 문제해결력면접",
        "창의적 문제해결력면접": "창의적 문제해결력면접",
    }
    text = str(raw or "").strip()
    values: list[str] = []
    if text:
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            values = [str(x).strip() for x in parsed]
        elif isinstance(parsed, dict):
            values = [str(x).strip() for x in (parsed.get("methods") or [])]
        else:
            values = [part.strip() for part in re.split(r"[\n,;/|]+", text) if part.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        mapped = allowed.get(value) or allowed.get(value.lower()) or allowed.get(_norm_sclass_key(value))
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out or list(SUPPORTED_INTERVIEW_METHODS)


def _group_interview_questions_for_response(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for q in questions or []:
        comp = str((q or {}).get("competency", "")).strip() or "핵심 직무"
        code = str((q or {}).get("ncsClCd", "")).strip()
        grouped.setdefault((comp, code), []).append(
            {
                "question": str((q or {}).get("question", "")).strip(),
                "follow_ups": list((q or {}).get("follow_ups", []) or []),
                "evaluation_points": list((q or {}).get("evaluation_points", []) or []),
                "method": str((q or {}).get("method") or (q or {}).get("type") or "").strip(),
            }
        )
    return [
        {"competency": comp, "ncsClCd": code, "questions": qset}
        for (comp, code), qset in grouped.items()
    ]


def _clean_question_text(value: Any, max_chars: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars].strip() if text else ""


def _ksa_terms_for_question(
    ncs_ksa: list[dict[str, Any]] | None,
    ncs_code: str,
    fallback_terms: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    if not str(ncs_code or "").strip():
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _clean_question_text(value, max_chars=60)
        key = _ksa_key(text)
        if _contains_blind_hiring_cue(text):
            return
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)

    for row in ncs_ksa or []:
        if isinstance(row, dict) and ncs_code and str(row.get("ncsClCd", "")).strip() == ncs_code:
            add(row.get("factorName"))
            if len(out) >= limit:
                return out[:limit]
    if out:
        return out[:limit]
    for term in fallback_terms or []:
        add(term)
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]


def _infer_model_focus_from_official_ksa(
    ncs_ksa: list[dict[str, Any]] | None,
    ncs_code: str,
    question: str,
    follow_ups: list[str],
) -> str:
    code = str(ncs_code or "").strip()
    if not code:
        return ""
    compact_text = re.sub(r"\s+", "", "\n".join([str(question or ""), *follow_ups])).lower()
    if not compact_text:
        return ""
    candidates: list[str] = []
    seen: set[str] = set()
    for row in ncs_ksa or []:
        if not isinstance(row, dict) or str(row.get("ncsClCd", "")).strip() != code:
            continue
        factor = _clean_question_text(row.get("factorName"), max_chars=60)
        key = _ksa_key(factor)
        if not factor or not key or key in seen or _contains_blind_hiring_cue(factor):
            continue
        seen.add(key)
        candidates.append(factor)
    for factor in sorted(candidates, key=len, reverse=True):
        if re.sub(r"\s+", "", factor).lower() in compact_text:
            return factor
    return ""


def _pick_unit_for_detail(
    target_detail: str,
    offset: int,
    ncs_matches: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    rows = [x for x in (ncs_matches or []) if isinstance(x, dict)]
    if not rows:
        return {}
    detail_key = _norm_sclass_key(target_detail)
    exact: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    if detail_key:
        for row in rows:
            authoritative_detail_keys = {
                _norm_sclass_key(str(row.get("matchedDetailName", ""))),
                _norm_sclass_key(str(row.get("reviewed_detail", ""))),
                _norm_sclass_key(str(row.get("confirmed_detail", ""))),
                _norm_sclass_key(str(row.get("ncs_detail", ""))),
                _norm_sclass_key(str(row.get("ncsSubdCdnm", ""))),
            }
            authoritative_detail_keys.discard("")
            if detail_key in authoritative_detail_keys:
                exact.append(row)
                continue
            sclass = _norm_sclass_key(str(row.get("ncsSclasCdnm", "")))
            matched = [
                _norm_sclass_key(x)
                for x in (row.get("matched_keywords") or [])
                if str(x).strip()
            ] if isinstance(row.get("matched_keywords"), list) else []
            if sclass == detail_key or detail_key in matched:
                fallback.append(row)
    pool = exact or fallback or rows
    return dict(pool[offset % len(pool)])


def _method_evaluation_points(method: str, ksa_terms: list[str]) -> list[str]:
    guide = {
        "경험면접": ["구체적 상황 설명", "본인 역할과 행동", "판단 근거와 협업", "결과 지표와 학습"],
        "상황면접": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식", "이해관계자 대응"],
        "발표면접": ["자료 분석력", "논리적 구조화", "대안의 실행가능성", "실행계획 구체성", "성과지표 설계", "질의응답 대응"],
        "토론면접": ["입장발표 근거", "경청과 상호작용", "갈등 조정", "최종 합의안 도출", "반대 의견 처리"],
        "인바스켓면접": ["우선순위 판단", "문서·요청 분류", "보고·위임·직접처리 판단", "시간관리", "리스크 대응", "기록·후속점검"],
        "직무지식면접": ["절차·기준 이해", "직무지식 적용", "예외상황 판단", "산출물 품질", "오류 예방"],
        "창의적 문제해결력면접": ["미래예측과 문제 정의", "창의적 사고와 대안 도출", "검증 방법", "실현가능성", "의사결정과 실행계획", "리스크 보완"],
    }
    points = list(guide.get(method, guide["경험면접"]))
    ksa_points: list[str] = []
    for term in ksa_terms:
        if _contains_blind_hiring_cue(term):
            continue
        point = f"{term} 적용 근거"
        if point not in points and point not in ksa_points:
            ksa_points.append(point)
        if len(ksa_points) >= 2:
            break
    if ksa_points:
        points = points[: max(0, 6 - len(ksa_points))] + ksa_points
    return points[:6]


def _domain_context_pack(detail: str, subject: str, focus: str, comp_def: str) -> dict[str, str]:
    source = " ".join(str(x or "") for x in (detail, subject, focus, comp_def))
    key = re.sub(r"\s+", "", source).lower()
    default = {
        "evidence": "실적자료, 민원·오류 사례, 업무 기준",
        "situation": "자료 오류, 일정 지연, 이해관계자 요청",
        "inbasket": "긴급 민원, 상급자 보고 요청, 자료 오류 정정, 일정 충돌 문서",
        "debate": "관련 기준을 강화하는 입장과 운영 효율을 우선하는 입장",
        "stakeholders": "상급자, 협업 부서, 민원인",
    }
    packs: list[tuple[tuple[str, ...], dict[str, str]]] = [
        (
            ("조리", "식음료", "메뉴", "식재료", "스톡", "영업장"),
            {
                "evidence": "메뉴별 판매량, 식재료 재고표, 위생점검 결과, 고객 불만 사례",
                "situation": "식재료 수급 차질, 위생 기준 이슈, 조리 일정 지연",
                "inbasket": "식재료 재고표, 위생점검 요청, 고객 불만 접수, 조리 일정 변경 문서",
                "debate": "위생·품질 기준을 강화하는 입장과 조리 속도·영업 효율을 우선하는 입장",
                "stakeholders": "조리장, 홀 담당자, 위생 담당자, 고객",
            },
        ),
        (
            ("청소", "환경미화", "미화", "품질검증"),
            {
                "evidence": "구역별 청소 점검표, 민원 접수 내역, 오염도 확인 사진, 작업 배치표",
                "situation": "청소 범위 변경, 반복 민원, 안전사고 위험 구역 발견",
                "inbasket": "구역별 작업지시서, 민원 접수 문서, 안전주의 요청, 인력 배치 변경 문서",
                "debate": "청소 품질 기준을 강화하는 입장과 제한된 인력·시간 내 처리 효율을 우선하는 입장",
                "stakeholders": "현장 관리자, 이용자, 협업 근무자, 안전 담당자",
            },
        ),
        (
            ("화물", "운송", "운임", "화주", "배차", "차량"),
            {
                "evidence": "운송 의뢰서, 배차표, 운임 산정 자료, 화주 요청 변경 내역",
                "situation": "화주 요청 변경, 배차 지연, 운임 산정 오류",
                "inbasket": "운송 의뢰서, 배차 변경 요청, 화주 불만 접수, 운임 검토 문서",
                "debate": "운송 기준과 안전·정확성을 강화하는 입장과 납기·비용 효율을 우선하는 입장",
                "stakeholders": "화주, 운전 담당자, 배차 담당자, 협력 운송사",
            },
        ),
        (
            ("사회복지", "사례관리", "대상자", "욕구", "상담"),
            {
                "evidence": "초기상담 기록, 욕구 사정표, 서비스 연계 현황, 대상자 동의 기록",
                "situation": "대상자 욕구 변경, 보호자 요청, 서비스 연계 일정 지연",
                "inbasket": "상담 기록지, 서비스 연계 요청서, 대상자 긴급 연락, 기관 회신 문서",
                "debate": "대상자 자기결정권을 우선하는 입장과 기관 자원 배분 기준을 우선하는 입장",
                "stakeholders": "대상자, 보호자, 사례관리자, 연계기관 담당자",
            },
        ),
        (
            ("병원", "간호", "요양", "보건", "환자", "진료", "의료", "산업보건"),
            {
                "evidence": "환자 안내 기록, 보건교육 계획서, 건강상담 기록, 안전보건 점검 결과",
                "situation": "환자·근로자 문의 증가, 교육 일정 변경, 건강정보 기록 오류",
                "inbasket": "교육 일정표, 상담 기록지, 점검 요청 문서, 긴급 안내 요청",
                "debate": "건강·안전 기준을 엄격히 적용하는 입장과 현장 수용성·업무 연속성을 우선하는 입장",
                "stakeholders": "대상자, 의료진, 안전보건 담당자, 부서 관리자",
            },
        ),
        (
            ("정보기술", "it프로젝트", "it비즈니스", "시스템", "장애티켓", "sla"),
            {
                "evidence": "요구사항 정의서, 장애 티켓, SLA 현황, 비용편익분석표, 일정·리스크 등록부",
                "situation": "요구사항 변경, 장애 재발, SLA 위반 위험",
                "inbasket": "변경 요청서, 장애 재발 보고, 사용자 문의, 일정 조정 문서",
                "debate": "IT 거버넌스와 표준 준수를 우선하는 입장과 현업 요청 처리 속도를 우선하는 입장",
                "stakeholders": "현업 부서, IT PM, 개발 담당자, 운영 담당자, 공급업체, 보안 담당자",
            },
        ),
        (
            ("화력발전", "원자력발전", "발전설비", "전기제어", "환경설비", "보호계전기", "원자로"),
            {
                "evidence": "운전일지, 설비 알람 로그, 정비이력, 절차서, 작업허가서, 장애 티켓",
                "situation": "설비 이상 징후, 절차서와 현장 상태 불일치, 환경·안전 기준 접근",
                "inbasket": "교대조 인수인계 문서, 정비 우선순위 요청, 장애 재발 보고, 변경관리 승인 문서",
                "debate": "안전보수성을 우선하는 입장과 가동률·사업 일정 준수를 우선하는 입장",
                "stakeholders": "운전원, 정비팀, 안전품질팀, 환경팀, 규제기관, 현업부서",
            },
        ),
        (
            ("하수", "상수", "관로", "수질", "누수", "블록시스템"),
            {
                "evidence": "수질측정값, 설비 운전 로그, 관망도, 유량·수압 기록, 안전점검표",
                "situation": "수질 경보, 계측기 이상값, 현장 작업 일정 충돌",
                "inbasket": "약품투입 기록 정정 요청, 계측기 교정 요청, 누수 신고, 법정보고자료 제출 문서",
                "debate": "수질·안전 기준을 보수적으로 적용하는 입장과 처리 효율·주민 불편 최소화를 우선하는 입장",
                "stakeholders": "주민, 관제실, 수질 담당자, 현장 작업자, 지자체, 감독기관",
            },
        ),
        (
            ("객실", "유원", "스포츠", "레저", "어트랙션", "체크인", "체크 인"),
            {
                "evidence": "예약·운영 현황, 시설 점검표, 안전관리 기록, 이용객 민원 내역",
                "situation": "예약 변경, 시설 점검 지연, 이용객 안전 안내 필요 상황",
                "inbasket": "예약 현황표, 시설 점검 요청, 이용객 민원, 안전 안내 문서",
                "debate": "고객 안전·서비스 기준을 강화하는 입장과 회전율·운영 효율을 우선하는 입장",
                "stakeholders": "이용객, 프론트 담당자, 현장 운영자, 시설 담당자, 안전 담당자",
            },
        ),
        (
            ("발전", "설비", "정비", "관로", "하수", "상수", "시설", "건설", "건축", "해체"),
            {
                "evidence": "설비 점검 기록, 도면, 작업허가서, 이상 징후 로그, 안전점검 결과",
                "situation": "설비 이상 징후, 작업 일정 충돌, 안전 기준 미충족 가능성",
                "inbasket": "점검 기록표, 작업허가 요청, 장애 신고, 안전조치 확인 문서",
                "debate": "안전·품질 기준을 강화하는 입장과 공정 일정·운영 연속성을 우선하는 입장",
                "stakeholders": "현장 작업자, 안전 담당자, 설비 운영자, 협력업체",
            },
        ),
        (
            ("보안", "경비", "순찰"),
            {
                "evidence": "순찰 기록, 출입 통제 로그, 경비 배치표, 이상 상황 보고서",
                "situation": "출입 통제 예외 요청, 순찰 공백 가능성, 이상 상황 신고",
                "inbasket": "순찰 기록표, 출입 승인 요청, 이상 상황 보고서, 근무 배치 변경 문서",
                "debate": "보안 통제 기준을 강화하는 입장과 방문객 편의·운영 효율을 우선하는 입장",
                "stakeholders": "방문객, 현장 경비원, 시설 담당자, 보안 책임자",
            },
        ),
        (
            ("객실", "유원", "스포츠", "시설운영", "레저"),
            {
                "evidence": "이용객 민원 내역, 시설 점검표, 예약·운영 현황, 안전관리 기록",
                "situation": "이용객 불만, 시설 점검 지연, 안전 안내 필요 상황",
                "inbasket": "예약 현황표, 시설 점검 요청, 이용객 민원, 안전 안내 문서",
                "debate": "고객 안전·서비스 기준을 강화하는 입장과 회전율·운영 효율을 우선하는 입장",
                "stakeholders": "이용객, 현장 운영자, 시설 담당자, 안전 담당자",
            },
        ),
        (
            ("사무", "총무", "문서", "부동산", "행정", "프로젝트", "정보기술", "it"),
            {
                "evidence": "요구사항 목록, 결재 문서, 회의록, 일정표, 이해관계자 요청 내역",
                "situation": "요구사항 변경, 결재 지연, 문서 기준 불일치",
                "inbasket": "결재 대기 문서, 회의 요청, 요구사항 변경 메일, 마감 임박 업무 목록",
                "debate": "문서·절차 기준을 강화하는 입장과 처리 속도·협업 효율을 우선하는 입장",
                "stakeholders": "요청 부서, 결재권자, 협업 담당자, 외부 관계자",
            },
        ),
    ]
    for keywords, pack in packs:
        if any(keyword.lower() in key for keyword in keywords):
            return {**default, **pack}
    return default


def _has_korean_final_consonant(text: str) -> bool:
    cleaned = re.sub(r"[\s\]\)\}\"'.,!?…:;]+$", "", str(text or ""))
    for ch in reversed(cleaned):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            return ((code - 0xAC00) % 28) != 0
    return False


def _with_josa(text: str, final_consonant_josa: str, no_final_consonant_josa: str) -> str:
    value = str(text or "").strip()
    return value + (final_consonant_josa if _has_korean_final_consonant(value) else no_final_consonant_josa)


def _quoted_with_josa(text: str, final_consonant_josa: str, no_final_consonant_josa: str) -> str:
    value = str(text or "").strip()
    return f"'{value}'" + (
        final_consonant_josa if _has_korean_final_consonant(value) else no_final_consonant_josa
    )


def _question_for_method(
    method: str,
    subject: str,
    focus: str,
    detail: str,
    comp_def: str,
) -> str:
    if subject and detail and _norm_sclass_key(subject) != _norm_sclass_key(detail):
        label = f"{detail} 세분류의 {subject}"
    else:
        label = subject or detail or "해당 직무"
    focus = focus or "핵심 수행기준"
    definition_hint = f" ({_clean_question_text(comp_def, max_chars=70)})" if comp_def else ""
    context = _domain_context_pack(detail=detail, subject=subject, focus=focus, comp_def=comp_def)
    if method == "발표면접":
        return (
            f"[발표과제] {label} 업무에서 {_quoted_with_josa(focus, '과', '와')} 관련된 {_with_josa(context['evidence'], '이', '가')} 주어졌다고 가정하고 "
            "준비시간 20분 후 현황 문제를 진단하고 개선안을 5분 발표해 주세요. "
            f"발표에는 현황 진단, 원인 분석, 대안 2가지, 실행 우선순위, 성과지표, 5분 질의응답 답변을 포함하세요{definition_hint}."
        )
    if method == "토론면접":
        return (
            f"[토론과제] {label} 업무에서 '{focus}' 관련 {_with_josa(context['debate'], '이', '가')} 충돌합니다. "
            "토론시간 20분 동안 1분 입장발표 후 반대 의견의 타당한 부분을 확인하고, 본인의 초기 입장과 근거, 쟁점 조정 방식, 최종 합의안 도출 방식을 제시해 주세요."
        )
    if method == "인바스켓면접":
        return (
            f"[인바스켓과제] 제한시간 30분 안에 {label} 관련 {_with_josa(context['inbasket'], '이', '가')} 동시에 들어왔습니다. "
            f"{_quoted_with_josa(focus, '을', '를')} 기준으로 처리 우선순위와 상급자 보고, 위임, 직접처리 판단 및 첫 15분 행동을 제시해 주세요."
        )
    if method == "상황면접":
        return (
            f"{label} 업무 중 {_quoted_with_josa(focus, '과', '와')} 관련해 {_with_josa(context['situation'], '이', '가')} 동시에 발생한 상황입니다. "
            "어떤 기준으로 판단하고 위험요인을 어떻게 통제하며, 사실 확인부터 보고와 실행까지 어떤 순서로 행동하시겠습니까?"
        )
    if method == "직무지식면접":
        return (
            f"{label}에서 {_quoted_with_josa(focus, '과', '와')} 관련해 확인해야 할 절차, 기준, 관련 근거, 산출물을 설명하고 "
            "실제 업무에 적용할 때의 예외상황, 품질 점검 방법, 오류 예방 유의점을 말씀해 주세요."
        )
    if method == "창의적 문제해결력면접":
        return (
            f"[창의적 문제해결력과제] {label} 업무에서 {_quoted_with_josa(focus, '과', '와')} 관련된 복합 문제가 발생했습니다. "
            f"미래예측 관점에서 주어진 {_with_josa(context['evidence'], '을', '를')} 바탕으로 핵심 문제를 정의하고, 원인 가설, 창의적 대안 2가지, 검증 방법, "
            "실현가능성, 의사결정 기준, 실행계획과 성과지표 및 리스크 보완책을 제시해 주세요."
        )
    return (
        f"{label} 수행 과정에서 {_quoted_with_josa(focus, '을', '를')} 적용해 문제를 해결하거나 성과를 낸 경험을 말씀해 주세요. "
        "당시 상황과 과제, 본인 역할, 선택한 행동, 결과 지표와 학습을 포함해 설명해 주세요."
    )


def _followups_for_method(method: str, subject: str, focus: str, count: int) -> list[str]:
    if count <= 0:
        return []
    label = subject or "해당 업무"
    focus = focus or "핵심 수행기준"
    context = _domain_context_pack(detail="", subject=subject, focus=focus, comp_def="")
    banks = {
        "경험면접": [
            "당시 상황과 본인이 맡은 역할을 구체적으로 설명해 주세요.",
            f"{_quoted_with_josa(focus, '을', '를')} 적용하기 위해 실제로 취한 행동은 무엇이었습니까?",
            "다른 선택지와 비교해 그 행동을 선택한 기준은 무엇이었습니까?",
            "성과를 어떤 기준이나 지표로 확인했습니까?",
            "같은 상황이 다시 주어진다면 어떤 점을 개선하시겠습니까?",
        ],
        "상황면접": [
            "판단 전에 먼저 확인해야 할 사실과 기준은 무엇입니까?",
            f"{_quoted_with_josa(focus, '과', '와')} 관련해 그 행동을 선택한 이유와 예상되는 위험요인은 무엇입니까?",
            f"{context['stakeholders']} 등 이해관계자에게 어떤 순서와 방식으로 설명하시겠습니까?",
            "결과가 기대와 다르게 나오면 어떤 후속 조치를 하시겠습니까?",
            "같은 문제가 반복되지 않도록 어떤 예방 장치를 두시겠습니까?",
        ],
        "발표면접": [
            f"'{focus}' 쟁점을 발표에서 진단할 때 핵심 근거 자료는 무엇입니까?",
            "대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
            "면접위원이 반대 의견을 제시한다면 어떤 근거로 답변하시겠습니까?",
            "실행 일정, 필요 자원, 성과지표를 어떻게 설정하겠습니까?",
            f"{label} 현장에 적용할 때 가장 큰 리스크와 보완책은 무엇입니까?",
        ],
        "토론면접": [
            f"'{focus}' 쟁점에서 본인의 초기 입장을 뒷받침하는 핵심 근거는 무엇입니까?",
            "반대 의견 중 수용할 수 있는 부분과 수용하기 어려운 부분은 무엇입니까?",
            "논의가 감정적 대립으로 흐를 때 어떻게 조정하시겠습니까?",
            "최종 합의안에 반드시 포함되어야 할 기준은 무엇입니까?",
            "토론 이후 실행 책임과 후속 점검은 어떻게 정리하시겠습니까?",
        ],
        "인바스켓면접": [
            f"'{focus}' 기준으로 여러 문서와 요청을 어떻게 분류하겠습니까?",
            "가장 먼저 처리할 항목과 보류할 항목을 각각 무엇으로 보겠습니까?",
            f"{context['stakeholders']} 중 누구에게 보고, 위임, 직접 처리할지 어떻게 선택하겠습니까?",
            "마감 지연이나 민원 확대 가능성은 어떻게 통제하겠습니까?",
            "30분 이후 후속 확인과 기록은 어떻게 남기겠습니까?",
        ],
        "직무지식면접": [
            f"{_quoted_with_josa(focus, '과', '와')} 관련해 반드시 확인해야 할 기준이나 규정은 무엇입니까?",
            "그 기준을 실제 업무에 적용할 때 자주 발생하는 예외상황은 무엇입니까?",
            "관련 산출물의 품질을 어떻게 점검하겠습니까?",
            "잘못 적용했을 때 발생할 수 있는 리스크와 보완책은 무엇입니까?",
            "신규 담당자에게 이 절차를 설명한다면 어떤 순서로 교육하겠습니까?",
        ],
        "창의적 문제해결력면접": [
            "핵심 문제정의를 위해 먼저 확인할 사실과 기준은 무엇입니까?",
            f"{_quoted_with_josa(focus, '과', '와')} 관련한 원인 가설은 어떻게 세우고 검증하겠습니까?",
            "대안 중 실행 우선순위를 높게 둘 방안과 그 이유는 무엇입니까?",
            "선택한 대안의 리스크와 보완책은 어떻게 정리하겠습니까?",
            "성과지표와 후속 점검 기준은 무엇으로 설정하겠습니까?",
        ],
    }
    bank = banks.get(method, banks["경험면접"])
    return bank[: max(0, min(5, count))]


def _clean_question_items(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_question_text(value, max_chars=140)
        key = normalize_question_dedup_key(text)
        if not text or not key or key in seen:
            continue
        if _contains_blind_hiring_cue(text):
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _merge_question_items(primary: list[str], fallback: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in primary + fallback:
        text = _clean_question_text(value, max_chars=140)
        key = normalize_question_dedup_key(text)
        if not text or not key or key in seen:
            continue
        if _contains_blind_hiring_cue(text):
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _compact_contains_term(text: str, term: str) -> bool:
    compact_text = re.sub(r"\s+", "", str(text or "")).lower()
    compact_term = re.sub(r"\s+", "", str(term or "")).lower()
    return bool(compact_text and compact_term and compact_term in compact_text)


def _follow_up_job_context(q: dict[str, Any], focus: str = "") -> str:
    focus = _clean_question_text(focus, max_chars=60)
    for key in (
        "competency",
        "compeUnitName",
        "required_job_context",
        "ncs_detail",
        "ncsSubdCdnm",
        "matchedDetailName",
        "ncsSclasCdnm",
    ):
        context = _clean_question_text(q.get(key), max_chars=60)
        if not context:
            continue
        if focus and _compact_contains_term(context, focus):
            continue
        if focus and _compact_contains_term(focus, context):
            continue
        return context
    return ""


def _inject_focus_into_follow_up(method: str, focus: str, follow_up: str, job_context: str = "") -> str:
    text = _clean_question_text(follow_up, max_chars=140)
    focus = _clean_question_text(focus, max_chars=60)
    if not text or not focus or _ksa_factor_relevant_to_text(focus, text):
        return text
    context = _clean_question_text(job_context, max_chars=60)
    context_part = ""
    if context and not _compact_contains_term(text, context) and not _compact_contains_term(focus, context):
        context_part = f" {context} 상황에서" if method == "상황면접" else f" {context} 업무에서"
    prefix_by_method = {
        "경험면접": f"{_quoted_with_josa(focus, '을', '를')} 적용하는 과정에서{context_part} 본인 행동과 선택 이유를 중심으로",
        "상황면접": f"{_quoted_with_josa(focus, '과', '와')} 관련해{context_part}",
        "발표면접": f"{_quoted_with_josa(focus, '을', '를')} 발표 쟁점으로 볼 때{context_part}",
        "토론면접": f"{_quoted_with_josa(focus, '을', '를')} 토론 쟁점으로 볼 때{context_part}",
        "인바스켓면접": f"{_quoted_with_josa(focus, '을', '를')} 처리 기준으로 삼을 때{context_part}",
        "직무지식면접": f"{_quoted_with_josa(focus, '과', '와')} 관련한 기준으로{context_part}",
        "창의적 문제해결력면접": f"{_quoted_with_josa(focus, '과', '와')} 관련한 원인과 대안 관점에서{context_part}",
    }
    prefix = prefix_by_method.get(method, f"{_quoted_with_josa(focus, '과', '와')} 관련해{context_part}")
    body_limit = max(35, 138 - len(prefix))
    body = _clean_question_text(text, max_chars=body_limit)
    return _clean_question_text(f"{prefix} {body}", max_chars=140)


def _follow_ups_non_focus_shape_ok(method: str, follow_ups: list[str]) -> bool:
    clean = [str(item or "").strip() for item in follow_ups if str(item or "").strip()]
    if len(clean) < 3:
        return False
    if _contains_blind_hiring_cue("\n".join(clean)):
        return False
    keys = [normalize_question_dedup_key(item) for item in clean]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        return False

    compact_items = [re.sub(r"\s+", "", item) for item in clean]
    anchors = _FOLLOW_UP_METHOD_ANCHORS.get(method, ())
    anchor_hits = {
        anchor
        for anchor in anchors
        if any(anchor in compact for compact in compact_items)
    }
    if len(anchor_hits) < 2:
        return False

    open_prompt_hits = sum(
        1
        for item in clean[:3]
        if re.search(r"(무엇|어떤|어떻게|얼마|어땠|어떠|왜|기준|이유|설명|말씀|제시|확인|선택|평가|점검|정리)", item)
    )
    return open_prompt_hits >= 3


def _repair_model_followups_with_focus(
    method: str,
    q: dict[str, Any],
    follow_ups: list[str],
    limit: int,
) -> list[str]:
    focus = _clean_question_text(q.get("question_focus"), max_chars=60)
    count = max(0, min(5, int(limit or 0)))
    clean = _merge_question_items(follow_ups, [], count)
    if count < 3 or len(clean) < 3 or not focus:
        return []
    if _follow_ups_quality_ok(method, q, clean):
        return []

    job_context = _follow_up_job_context(q, focus)
    preferred = min(_FOLLOW_UP_FOCUS_SLOT_INDEX.get(method, 1), len(clean) - 1)
    candidate_indices = list(dict.fromkeys([preferred, 1 if len(clean) > 1 else 0, 0, 2 if len(clean) > 2 else 0]))
    for target_index in candidate_indices:
        repaired = list(clean)
        repaired[target_index] = _inject_focus_into_follow_up(method, focus, repaired[target_index], job_context)
        if repaired[target_index] == clean[target_index]:
            continue
        if _contains_blind_hiring_cue("\n".join(repaired)):
            continue
        if _follow_ups_quality_ok(method, q, repaired):
            return repaired
    return []


def _adjust_generated_questions(
    strategy: dict[str, Any],
    question_plan: dict[str, Any],
    interview_methods: list[str],
    ncs_matches: list[dict[str, Any]] | None = None,
    ncs_ksa: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        return strategy
    questions = strategy.get("interview_questions")
    if not isinstance(questions, list):
        questions = []
    target_total = int(question_plan.get("total_main_count", 0) or 0)
    default_follow_count = max(0, min(5, int(question_plan.get("follow_up_count", 3) or 0)))
    methods = interview_methods or list(SUPPORTED_INTERVIEW_METHODS)
    sequence = [item for item in (question_plan.get("question_sequence") or []) if isinstance(item, dict)]
    if sequence:
        target_total = len(sequence)
    elif target_total <= 0:
        target_total = len([q for q in questions if isinstance(q, dict)])

    source_questions = [dict(q) for q in questions if isinstance(q, dict)]
    while len(source_questions) < target_total:
        source_questions.append({})
    source_questions = source_questions[:target_total] if target_total > 0 else source_questions

    adjusted: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    detail_offsets: dict[str, int] = {}
    for idx, row in enumerate(source_questions):
        item = dict(row)
        planned = sequence[idx] if idx < len(sequence) else {}
        target_detail = str(planned.get("detail", "")).strip()
        detail_key = _norm_sclass_key(target_detail)
        offset = detail_offsets.get(detail_key, 0)
        unit = _pick_unit_for_detail(target_detail, offset, ncs_matches)
        detail_offsets[detail_key] = offset + 1

        if unit:
            item["ncsClCd"] = str(unit.get("ncsClCd", "")).strip() or str(item.get("ncsClCd", "")).strip()
            item["competency"] = str(unit.get("compeUnitName", "")).strip() or str(item.get("competency", "")).strip()
            item["compeUnitDef"] = str(unit.get("compeUnitDef", "")).strip() or str(item.get("compeUnitDef", "")).strip()
            item["ncsSubdCdnm"] = str(unit.get("ncsSubdCdnm", "")).strip() or str(item.get("ncsSubdCdnm", "")).strip()
            item["ncsSclasCdnm"] = str(unit.get("ncsSclasCdnm", "")).strip() or str(item.get("ncsSclasCdnm", "")).strip()
            item["matchedDetailName"] = str(unit.get("matchedDetailName", "")).strip() or target_detail
            item["ncs_detail"] = (
                target_detail
                or str(unit.get("matchedDetailName", "")).strip()
                or str(unit.get("ncsSubdCdnm", "")).strip()
                or str(unit.get("ncsSclasCdnm", "")).strip()
                or str(item.get("ncs_detail", "")).strip()
            )
        elif target_detail:
            item["ncs_detail"] = target_detail
            if not str(item.get("competency", "")).strip():
                item["competency"] = target_detail

        row_follow_count = max(0, min(5, int(planned.get("follow_up_count", default_follow_count) or 0)))
        method = methods[idx % len(methods)]
        item["method"] = method
        item["type"] = method

        ncs_code = str(item.get("ncsClCd", "")).strip()
        subject = str(item.get("competency", "")).strip() or target_detail or "해당 직무"
        existing_refs = list(item.get("ksa_refs", []) or []) if isinstance(item.get("ksa_refs"), list) else []
        ksa_terms = _ksa_terms_for_question(
            ncs_ksa=ncs_ksa,
            ncs_code=ncs_code,
            fallback_terms=existing_refs,
        )
        raw_question = str(item.get("question", "")).strip()
        item["model_question_raw"] = raw_question
        raw_followups = _clean_question_items(item.get("follow_ups"), limit=5)
        if not raw_followups and str(item.get("follow_up", "")).strip():
            raw_followups = _clean_question_items([item.get("follow_up")], limit=1)
        inferred_focus = _infer_model_focus_from_official_ksa(ncs_ksa, ncs_code, raw_question, raw_followups)
        focus = (
            inferred_focus
            or (ksa_terms[idx % len(ksa_terms)] if ksa_terms else "")
            or _clean_question_text(item.get("competency") or target_detail or "핵심 수행기준")
        )
        item["question_focus"] = focus
        normalized_model_question = _normalize_model_task_marker(method, raw_question)
        normalized_model_question = _normalize_model_job_context(method, item, normalized_model_question)
        raw_evaluation_points = _clean_question_items(item.get("evaluation_points"), limit=6)
        item["model_followups_raw"] = raw_followups
        item["model_evaluation_points_raw"] = raw_evaluation_points
        raw_merged = "\n".join([raw_question, *raw_followups, *raw_evaluation_points])
        main_replacement_reasons: list[str] = []
        followup_replacement_reasons: list[str] = []
        raw_followups_final = _merge_question_items(raw_followups, [], row_follow_count)
        repaired_followups: list[str] = []
        if not raw_question:
            main_replacement_reasons.append("no_model_question")
        else:
            if _contains_blind_hiring_cue(raw_merged):
                main_replacement_reasons.append("blind_hiring_cue")
            if not _method_shape_ok(method, normalized_model_question):
                main_replacement_reasons.append("main_question_method_shape")
            if not _main_question_task_marker_ok(method, normalized_model_question):
                main_replacement_reasons.append("main_question_official_sample_shape")
            context_item = dict(item)
            context_item["ksa_refs"] = ksa_terms or existing_refs
            if not _follow_ups_quality_ok(method, context_item, raw_followups_final):
                if not main_replacement_reasons:
                    repaired_followups = _repair_model_followups_with_focus(
                        method=method,
                        q=context_item,
                        follow_ups=raw_followups,
                        limit=row_follow_count,
                    )
                if repaired_followups:
                    followup_replacement_reasons.append("follow_up_focus_injected")
                else:
                    followup_replacement_reasons.append("follow_up_quality")
        use_model_question = bool(raw_question and not main_replacement_reasons)
        use_raw_model_followups = bool(use_model_question and not followup_replacement_reasons)
        use_repaired_model_followups = bool(
            use_model_question
            and repaired_followups
            and followup_replacement_reasons == ["follow_up_focus_injected"]
        )
        model_replacement_reasons = [*main_replacement_reasons, *followup_replacement_reasons]

        template_question = _question_for_method(
            method=method,
            subject=subject,
            focus=focus,
            detail=target_detail,
            comp_def=str(item.get("compeUnitDef", "")).strip(),
        )
        template_followups = _followups_for_method(
            method=method,
            subject=subject,
            focus=focus,
            count=row_follow_count,
        )
        method_eval_points = _method_evaluation_points(method, ksa_terms)

        item["question"] = normalized_model_question if use_model_question else template_question
        if use_model_question and use_raw_model_followups:
            item["question_source"] = "model"
        elif use_model_question and use_repaired_model_followups:
            item["question_source"] = "model_main_repaired_followups"
        elif use_model_question:
            item["question_source"] = "model_main_template_followups"
        else:
            item["question_source"] = "template_fallback"
        item["model_question_preserved"] = bool(use_model_question)
        item["model_replacement_reasons"] = [] if use_model_question and use_raw_model_followups else model_replacement_reasons
        if use_raw_model_followups:
            item["follow_ups"] = raw_followups_final
        elif use_repaired_model_followups:
            item["follow_ups"] = _merge_question_items(repaired_followups, template_followups, row_follow_count)
        else:
            item["follow_ups"] = template_followups
        item["follow_up"] = item["follow_ups"][0] if item["follow_ups"] else ""
        item["evaluation_points"] = (
            _merge_question_items(raw_evaluation_points, method_eval_points, 6)
            if use_model_question
            else method_eval_points
        )
        adjusted.append(item)
        fallback_rows.append(
            {
                "question": template_question,
                "follow_ups": template_followups,
                "evaluation_points": method_eval_points,
            }
        )

    probe_strategy = dict(strategy)
    probe_strategy["interview_questions"] = [dict(q) for q in adjusted]
    probe_strategy["question_plan_used"] = question_plan
    probe_strategy = _attach_ksa_evidence_to_strategy(probe_strategy, ncs_ksa)
    probe_items = {
        int(item.get("index") or 0): item
        for item in (probe_strategy.get("question_quality_report") or {}).get("items", [])
        if isinstance(item, dict)
    }
    for pos, item in enumerate(adjusted):
        source = str(item.get("question_source") or "").strip()
        if source not in MODEL_PRESERVED_QUESTION_SOURCES:
            continue
        probe_item = probe_items.get(pos + 1) or {}
        if probe_item.get("ready") is True:
            continue
        fallback = fallback_rows[pos] if pos < len(fallback_rows) else {}
        existing_reasons = [
            str(reason).strip()
            for reason in (item.get("model_replacement_reasons") or [])
            if str(reason).strip()
        ] if isinstance(item.get("model_replacement_reasons"), list) else []
        quality_reasons = [
            f"quality_gate_{issue}"
            for issue in (probe_item.get("issues") or [])
            if str(issue).strip()
        ] if isinstance(probe_item.get("issues"), list) else []
        item["question"] = str(fallback.get("question") or item.get("question") or "").strip()
        item["question_source"] = "template_fallback"
        item["model_question_preserved"] = False
        item["model_replacement_reasons"] = list(dict.fromkeys([*existing_reasons, *quality_reasons]))
        item["follow_ups"] = list(fallback.get("follow_ups") or [])
        item["follow_up"] = item["follow_ups"][0] if item["follow_ups"] else ""
        item["evaluation_points"] = list(fallback.get("evaluation_points") or [])
    strategy["interview_questions"] = adjusted
    strategy["interview_by_competency"] = _group_interview_questions_for_response(adjusted)
    strategy["question_plan_used"] = question_plan
    strategy["interview_methods_used"] = methods
    strategy["question_customization_policy"] = "model_preserve_with_guidebook_template_fallback_followup_gate"
    return strategy


def _repeat_count_from_weight(weight: float, default: int = 1, max_repeat: int = 6) -> int:
    try:
        v = int(round(float(weight)))
    except Exception:
        v = int(default)
    return max(1, min(int(max_repeat), v))


def _build_priority_notice_text(
    notice_text: str,
    duty_text: str = "",
    qualification_text: str = "",
    preference_text: str = "",
    evaluation_text: str = "",
) -> str:
    notice = str(notice_text or "").strip()
    duty = str(duty_text or "").strip()
    qualification = str(qualification_text or "").strip()
    preference = str(preference_text or "").strip()
    evaluation = str(evaluation_text or "").strip()

    parts: list[str] = []
    if duty:
        parts.append(f"[담당업무-우선]\n{duty[:2500]}")
    if qualification:
        parts.append(f"[지원자격-우선]\n{qualification[:1800]}")
    if preference:
        parts.append(f"[우대사항-우선]\n{preference[:1800]}")
    if evaluation:
        parts.append(f"[면접평가항목-우선]\n{evaluation[:1800]}")
    if notice:
        parts.append(f"[공고문-보조]\n{notice[:2500]}")
    return "\n\n".join(parts).strip()


def _build_priority_query_text(
    base_text: str,
    duty_text: str = "",
    qualification_text: str = "",
    preference_text: str = "",
    evaluation_text: str = "",
) -> str:
    base = str(base_text or "").strip()[:5000]
    duty = str(duty_text or "").strip()[:2500]
    qualification = str(qualification_text or "").strip()[:1600]
    preference = str(preference_text or "").strip()[:1600]
    evaluation = str(evaluation_text or "").strip()[:1500]

    base_w = _to_float_or(os.getenv("JD_BASE_TEXT_WEIGHT", "1.0"), 1.0)
    duty_w = _to_float_or(os.getenv("DUTY_TEXT_WEIGHT", "3.0"), 3.0)
    qualification_w = _to_float_or(os.getenv("QUALIFICATION_TEXT_WEIGHT", "1.4"), 1.4)
    preference_w = _to_float_or(os.getenv("PREFERENCE_TEXT_WEIGHT", "1.2"), 1.2)
    eval_w = _to_float_or(os.getenv("EVALUATION_TEXT_WEIGHT", "2.5"), 2.5)

    base_rep = _repeat_count_from_weight(base_w, default=1, max_repeat=4)
    duty_rep = _repeat_count_from_weight(duty_w, default=3, max_repeat=6)
    qualification_rep = _repeat_count_from_weight(qualification_w, default=1, max_repeat=3)
    preference_rep = _repeat_count_from_weight(preference_w, default=1, max_repeat=3)
    eval_rep = _repeat_count_from_weight(eval_w, default=2, max_repeat=6)

    chunks: list[str] = []
    if duty:
        chunks.extend([f"[담당업무]{duty}"] * duty_rep)
    if qualification:
        chunks.extend([f"[지원자격]{qualification}"] * qualification_rep)
    if preference:
        chunks.extend([f"[우대사항]{preference}"] * preference_rep)
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
        raise HTTPException(status_code=502, detail=f"로컬 NCS DB KSA 조회 실패(NCS_MCP): {exc}") from exc


def _dedupe_units_by_code(units: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in units or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ncsClCd", "")).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(dict(row))
    return out


def _select_units_for_question_plan(
    question_plan: dict[str, Any],
    ncs_matches: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not isinstance(question_plan, dict):
        return []
    sequence = [item for item in (question_plan.get("question_sequence") or []) if isinstance(item, dict)]
    if not sequence:
        return _dedupe_units_by_code(ncs_matches)

    selected: list[dict[str, Any]] = []
    detail_offsets: dict[str, int] = {}
    for planned in sequence:
        target_detail = str(planned.get("detail", "")).strip()
        detail_key = _norm_sclass_key(target_detail)
        offset = detail_offsets.get(detail_key, 0)
        unit = _pick_unit_for_detail(target_detail, offset, ncs_matches)
        detail_offsets[detail_key] = offset + 1
        if unit:
            selected.append(unit)
    return _dedupe_units_by_code(selected)


def _merge_ksa_rows(existing: list[dict[str, Any]] | None, added: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in (existing or []) + (added or []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("ncsClCd", "")).strip()
        factor = str(row.get("factorName", "")).strip()
        key = (code, _ksa_key(factor))
        if not code or not factor or key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _supplement_ksa_for_question_plan(
    question_plan: dict[str, Any],
    ncs_matches: list[dict[str, Any]] | None,
    ncs_ksa: list[dict[str, Any]] | None,
    max_factors_per_unit: int,
) -> list[dict[str, Any]]:
    selected_units = _select_units_for_question_plan(question_plan, ncs_matches)
    if not selected_units:
        return list(ncs_ksa or [])
    covered_codes = {
        str(row.get("ncsClCd", "")).strip()
        for row in (ncs_ksa or [])
        if isinstance(row, dict) and str(row.get("ncsClCd", "")).strip() and str(row.get("factorName", "")).strip()
    }
    missing_units = [
        unit
        for unit in selected_units
        if str(unit.get("ncsClCd", "")).strip() and str(unit.get("ncsClCd", "")).strip() not in covered_codes
    ]
    if not missing_units:
        return list(ncs_ksa or [])
    fetched = _fetch_ncs_ksa_or_502(
        ncs_matches=missing_units,
        max_units=len(missing_units),
        max_factors_per_unit=max_factors_per_unit,
    )
    return _merge_ksa_rows(ncs_ksa, fetched)


def _require_ncs_mcp_url() -> str:
    endpoint = settings.ncs_mcp_endpoint()
    if endpoint:
        return endpoint
    raise HTTPException(
        status_code=503,
        detail=(
            "NCS_MCP_URL is required for NCScope. Start NCS_MCP, the read-only "
            "local NCS DB search server, with the compact serving DB and set NCS_MCP_URL."
        ),
    )


def _require_legacy_ncs_api_enabled() -> None:
    if not settings.enable_legacy_ncs_api():
        raise HTTPException(
            status_code=410,
            detail="legacy NCS API endpoints are disabled; use NCS_MCP_URL-backed local NCS DB endpoints",
        )


def _check_upload_size(data: bytes, label: str) -> None:
    max_bytes = settings.max_upload_bytes()
    if len(data or b"") > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds MAX_UPLOAD_MB ({max_bytes // (1024 * 1024)} MB)",
        )


_ARCHIVE_MEMBER_LIMIT = 12
_SUPPORTED_ARCHIVE_DOC_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
_REVIEW_SESSION_TTL_SEC = 4 * 60 * 60
_REVIEW_SESSION_MAX = 500
_REVIEW_SESSION_LOCK = threading.Lock()
_REVIEW_SESSION_BY_ID: dict[str, dict[str, Any]] = {}


def _suffix_of(name: str) -> str:
    return Path(str(name or "").replace("\\", "/")).suffix.lower()


def _safe_member_label(name: str) -> str:
    value = str(name or "").replace("\\", "/").split("/")[-1].strip()
    value = re.sub(r"[\r\n\t]+", " ", value)
    return value[:160] or "archive_member"


def _parse_single_document_upload(data: bytes, filename: str, label: str) -> dict[str, Any]:
    name = str(filename or "")
    suffix = _suffix_of(name)
    if suffix == ".txt":
        return {"markdown": data.decode("utf-8", errors="ignore"), "metadata": {"filename": name}}
    try:
        return parse_with_kordoc(
            data,
            filename=name,
            ocr=os.getenv("KORDOC_OCR", "true").strip().lower() in {"1", "true", "yes", "y"},
        )
    except KordocParseError as exc:
        if suffix == ".pdf":
            text = extract_pdf_text(data)
            if not text.strip():
                try:
                    text = extract_pdf_text_fallback(data, max_pages=6)
                except Exception:
                    text = ""
            if text.strip():
                return {
                    "markdown": text,
                    "metadata": {"filename": name, "fallback": "pdf-text"},
                    "warnings": [f"Kordoc parse failed; used PDF text fallback: {exc}"],
                }
        raise HTTPException(status_code=422, detail=f"{label} could not be parsed by Kordoc: {exc}") from exc


def _parse_upload_document(data: bytes, filename: str, label: str) -> dict[str, Any]:
    """Parse one upload or a ZIP of supported documents into one markdown payload."""

    name = str(filename or "")
    if _suffix_of(name) != ".zip":
        return _parse_single_document_upload(data, name, label)

    max_bytes = settings.max_upload_bytes()
    members: list[dict[str, str]] = []
    chunks: list[str] = []
    warnings: list[str] = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_name = info.filename
                suffix = _suffix_of(member_name)
                if suffix not in _SUPPORTED_ARCHIVE_DOC_SUFFIXES:
                    continue
                total_uncompressed += int(info.file_size or 0)
                if total_uncompressed > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{label} archive contents exceed MAX_UPLOAD_MB ({max_bytes // (1024 * 1024)} MB)",
                    )
                if len(members) >= _ARCHIVE_MEMBER_LIMIT:
                    warnings.append(f"archive member limit reached: {_ARCHIVE_MEMBER_LIMIT}")
                    break
                member_label = _safe_member_label(member_name)
                if info.flag_bits & 0x1:
                    warnings.append(f"{member_label}: encrypted ZIP member is not supported")
                    continue
                try:
                    member_bytes = archive.read(info)
                except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                    warnings.append(f"{member_label}: ZIP member could not be read: {exc}")
                    continue
                try:
                    parsed = _parse_single_document_upload(member_bytes, member_label, label)
                except HTTPException as exc:
                    warnings.append(f"{member_label}: {exc.detail}")
                    continue
                markdown = str(parsed.get("markdown") or "").strip()
                if not markdown:
                    warnings.append(f"{member_label}: empty parse result")
                    continue
                members.append({"filename": member_label, "suffix": suffix})
                chunks.append(f"# ZIP member: {member_label}\n\n{markdown}")
                warnings.extend(str(x) for x in (parsed.get("warnings") or []) if str(x).strip())
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail=f"{label} is not a readable ZIP archive") from exc

    if not chunks:
        raise HTTPException(
            status_code=422,
            detail=f"{label} ZIP contains no parseable PDF/HWP/HWPX/DOCX/TXT/image job-description files",
        )
    return {
        "markdown": "\n\n---\n\n".join(chunks),
        "metadata": {"filename": name, "archive": True, "members": members},
        "warnings": warnings,
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _request_ip_hash(request: Request | None) -> str:
    host = ""
    try:
        host = str((request.client.host if request and request.client else "") or "").strip()
    except Exception:
        host = ""
    if not host:
        return ""
    return _sha256_text(host)


def _record_audit_event(
    request: Request | None,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
) -> None:
    try:
        record_audit_log(
            actor_id="anonymous",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_hash=_request_ip_hash(request),
        )
    except Exception:
        return


def _prune_review_sessions(now: float | None = None) -> None:
    current = float(now if now is not None else time.time())
    expired = [
        session_id
        for session_id, session in _REVIEW_SESSION_BY_ID.items()
        if current - float(session.get("created_at", 0.0) or 0.0) > _REVIEW_SESSION_TTL_SEC
    ]
    for session_id in expired:
        _REVIEW_SESSION_BY_ID.pop(session_id, None)
    if len(_REVIEW_SESSION_BY_ID) <= _REVIEW_SESSION_MAX:
        return
    oldest = sorted(
        _REVIEW_SESSION_BY_ID.items(),
        key=lambda item: float(item[1].get("created_at", 0.0) or 0.0),
    )
    for session_id, _ in oldest[: max(0, len(_REVIEW_SESSION_BY_ID) - _REVIEW_SESSION_MAX)]:
        _REVIEW_SESSION_BY_ID.pop(session_id, None)


def _public_review_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["id"],
        "document_sha256": session["document_sha256"],
        "markdown_sha256": session["markdown_sha256"],
        "filename": session.get("filename", ""),
        "created_at": session["created_at"],
        "expires_at": session["created_at"] + _REVIEW_SESSION_TTL_SEC,
    }


def _create_review_session(upload_bytes: bytes, structured: dict[str, Any], filename: str) -> dict[str, Any]:
    document = structured.get("document") if isinstance(structured.get("document"), dict) else {}
    markdown = str(document.get("markdown") or "")
    session = {
        "id": secrets.token_urlsafe(24),
        "filename": str(filename or ""),
        "created_at": time.time(),
        "document_sha256": _sha256_bytes(upload_bytes),
        "markdown_sha256": _sha256_text(markdown),
        "markdown": markdown,
    }
    with _REVIEW_SESSION_LOCK:
        _prune_review_sessions(session["created_at"])
        _REVIEW_SESSION_BY_ID[session["id"]] = session
    return _public_review_session(session)


def _validate_review_session(review_payload: dict[str, Any], upload_bytes: bytes) -> dict[str, Any]:
    review_session_payload = review_payload.get("review_session")
    if not isinstance(review_session_payload, dict):
        review_session_payload = {}
    session_id = str(
        review_payload.get("review_session_id")
        or review_session_payload.get("id")
        or ""
    ).strip()
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="jd_review_json.review_session_id is required; call /api/jd/parse-review before generation",
        )
    with _REVIEW_SESSION_LOCK:
        _prune_review_sessions()
        session = dict(_REVIEW_SESSION_BY_ID.get(session_id) or {})
    if not session:
        raise HTTPException(status_code=409, detail="jd_review_json.review_session_id is expired or unknown")
    if session.get("document_sha256") != _sha256_bytes(upload_bytes):
        raise HTTPException(status_code=409, detail="jd_review_json review session does not match uploaded jd_file")
    payload_document = review_payload.get("document") if isinstance(review_payload.get("document"), dict) else {}
    payload_markdown = str(payload_document.get("markdown") or "")
    if payload_markdown and _sha256_text(payload_markdown) != session.get("markdown_sha256"):
        raise HTTPException(status_code=400, detail="jd_review_json.document.markdown does not match server parse session")
    return session


def _sanitize_request_openai_key(value: str | None) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) > 300 or any(ch.isspace() for ch in key):
        raise HTTPException(status_code=400, detail="openai_api_key is invalid")
    return key


def _ksa_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _clean_ksa_evidence_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "ncsClCd": str(row.get("ncsClCd", "")).strip(),
        "compeUnitName": str(row.get("compeUnitName", "")).strip(),
        "factorName": str(row.get("factorName", "")).strip(),
        "factorLevel": str(row.get("factorLevel", "")).strip(),
        "factorSource": str(row.get("factorSource", "")).strip(),
        "ksaStatus": str(row.get("ksaStatus", "")).strip(),
    }


_METHOD_MAIN_QUESTION_REQUIRED_TERMS: dict[str, tuple[str, ...]] = {
    "경험면접": ("경험", "상황", "본인", "행동", "결과"),
    "상황면접": ("상황", "판단", "기준", "순서", "위험"),
    "발표면접": ("준비시간", "발표", "진단", "대안", "실행", "성과지표", "질의응답"),
    "토론면접": ("토론시간", "입장발표", "토론", "충돌", "입장", "반대", "합의"),
    "인바스켓면접": ("인바스켓", "제한시간", "문서", "우선순위", "보고", "위임", "직접처리"),
    "직무지식면접": ("절차", "기준", "산출물", "예외상황"),
    "창의적 문제해결력면접": ("창의적", "미래예측", "문제", "정의", "대안", "검증", "실현가능성", "의사결정", "실행"),
}

_METHOD_EVALUATION_ANCHORS: dict[str, tuple[str, ...]] = {
    "경험면접": ("구체적상황", "본인역할", "행동", "결과", "성과", "학습", "판단근거"),
    "상황면접": ("사실확인", "판단기준", "행동순서", "위험요인", "이해관계자", "첫조치"),
    "발표면접": ("자료분석", "논리적구조화", "대안", "실행계획", "성과지표", "질의응답"),
    "토론면접": ("초기입장", "근거", "경청", "상호작용", "갈등조정", "합의안", "반대의견"),
    "인바스켓면접": ("우선순위", "문서", "요청분류", "보고", "위임", "직접처리", "시간관리", "리스크", "후속점검"),
    "직무지식면접": ("절차", "기준", "직무지식", "예외상황", "산출물", "품질", "오류예방"),
    "창의적 문제해결력면접": ("미래예측", "문제정의", "창의적사고", "원인가설", "대안", "검증", "실현가능성", "의사결정", "실행계획", "성과지표", "리스크"),
}

_FOLLOW_UP_METHOD_ANCHORS: dict[str, tuple[str, ...]] = {
    "경험면접": ("상황", "역할", "행동", "선택", "기준", "이유", "성과", "개선", "학습", "교훈"),
    "상황면접": ("확인", "기준", "이유", "위험", "이해관계자", "순서", "후속", "예방"),
    "발표면접": ("근거자료", "대안", "우선순위", "반대의견", "답변", "질의응답", "일정", "자원", "성과지표", "리스크"),
    "토론면접": ("초기입장", "입장발표", "근거", "반대의견", "수용", "조정", "합의안", "실행책임", "후속점검"),
    "인바스켓면접": ("문서", "요청", "분류", "우선순위", "먼저처리", "첫조치", "기준", "행동", "조치", "보류", "보고", "위임", "직접처리", "통제", "기록"),
    "직무지식면접": ("기준", "규정", "예외상황", "산출물", "품질", "리스크", "보완책", "교육", "순서"),
    "창의적 문제해결력면접": ("미래예측", "문제정의", "문제", "정의", "가설", "검증", "대안", "실현가능성", "의사결정", "실행계획", "우선순위", "리스크", "보완책", "성과지표", "후속점검"),
}

_FOLLOW_UP_FOCUS_SLOT_INDEX: dict[str, int] = {
    "경험면접": 1,
    "상황면접": 1,
    "발표면접": 0,
    "토론면접": 0,
    "인바스켓면접": 0,
    "직무지식면접": 0,
    "창의적 문제해결력면접": 1,
}

_ASSESSABLE_EVALUATION_TERMS = (
    "상황",
    "역할",
    "행동",
    "결과",
    "성과",
    "문제",
    "정의",
    "가설",
    "판단",
    "기준",
    "근거",
    "위험",
    "대응",
    "분석",
    "구조",
    "대안",
    "실행",
    "지표",
    "소통",
    "합의",
    "조정",
    "분류",
    "우선순위",
    "보고",
    "위임",
    "처리",
    "시간",
    "절차",
    "산출물",
    "품질",
    "예외",
    "점검",
    "자료",
    "문서",
    "요청",
    "계획",
    "오류",
    "검증",
    "리스크",
    "보완",
    "기록",
    "검토",
    "확인",
    "적용",
    "파악",
)

_VAGUE_EVALUATION_POINT_KEYS = {
    "성실성",
    "태도",
    "열정",
    "자신감",
    "인성",
    "적극성",
    "책임감",
    "표현력",
}

_JOB_CONTEXT_STOPWORDS = {
    "해당",
    "직무",
    "업무",
    "관련",
    "기준",
    "핵심",
    "수행",
    "상황",
    "질문",
    "설명",
    "제시",
    "과정",
    "본인",
    "결과",
    "경험",
    "절차",
    "적용",
    "근거",
    "능력",
    "단위",
    "관리",
}

_KSA_RELEVANCE_STOPWORDS = _JOB_CONTEXT_STOPWORDS | {
    "지식",
    "기술",
    "능력",
    "태도",
    "자세",
    "의지",
    "관련",
    "해당",
    "정확히",
    "성실하고",
    "꼼꼼한",
    "바른",
    "적극적",
    "객관적",
}

_UNRESOLVED_KSA_PLACEHOLDER_RE = re.compile(r"(?<![A-Za-z])KSA(?![A-Za-z])", re.IGNORECASE)


_OFFICIAL_SAMPLE_FORMAT_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "경험면접": {
        "task_any": ("경험", "사례"),
        "task_all": ("상황", "본인", "행동", "결과"),
        "eval_any": ("본인역할과행동", "성과와학습", "구체적상황설명"),
    },
    "상황면접": {
        "task_any": ("상황",),
        "task_all": ("판단", "기준", "순서", "위험"),
        "eval_any": ("판단기준", "위험요인인식", "이해관계자대응"),
    },
    "발표면접": {
        "task_any": ("[발표과제]", "발표과제"),
        "task_all": ("준비시간", "발표", "진단", "대안", "실행", "성과지표", "질의응답"),
        "eval_any": ("자료분석력", "논리적구조화", "대안의실행가능성", "질의응답대응"),
    },
    "토론면접": {
        "task_any": ("[토론과제]", "토론과제"),
        "task_all": ("토론시간", "입장발표", "충돌", "입장", "반대", "합의"),
        "eval_any": ("입장발표근거", "경청과상호작용", "갈등조정", "최종합의안도출"),
    },
    "인바스켓면접": {
        "task_any": ("[인바스켓과제]", "인바스켓과제"),
        "task_all": ("제한시간", "문서", "우선순위", "보고", "위임", "직접처리"),
        "eval_any": ("우선순위판단", "문서·요청분류", "시간관리"),
    },
    "직무지식면접": {
        "task_any": ("절차", "기준"),
        "task_all": ("절차", "기준", "산출물", "예외상황"),
        "eval_any": ("절차·기준이해", "직무지식적용", "산출물품질"),
    },
    "창의적 문제해결력면접": {
        "task_any": ("[창의적문제해결력과제]", "창의적문제해결력과제", "창의적문제해결력"),
        "task_all": ("미래예측", "문제", "정의", "대안", "검증", "실현가능성", "의사결정", "실행"),
        "eval_any": ("미래예측과문제정의", "창의적사고와대안도출", "검증방법", "실현가능성", "의사결정과실행계획"),
    },
}


def _method_shape_ok(method: str, text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    required = _METHOD_MAIN_QUESTION_REQUIRED_TERMS.get(method)
    if not required:
        return False
    return all(term in compact for term in required)


def _main_question_task_marker_ok(method: str, text: str) -> bool:
    marker_by_method = {
        "발표면접": ("[발표과제]", "발표과제"),
        "토론면접": ("[토론과제]", "토론과제"),
        "인바스켓면접": ("[인바스켓과제]", "인바스켓과제"),
        "창의적 문제해결력면접": ("[창의적 문제해결력과제]", "창의적문제해결력과제"),
    }
    markers = marker_by_method.get(method)
    if not markers:
        return True
    compact = re.sub(r"\s+", "", str(text or ""))
    return any(re.sub(r"\s+", "", marker) in compact for marker in markers)


def _normalize_model_task_marker(method: str, text: str) -> str:
    question = str(text or "").strip()
    if not question or _main_question_task_marker_ok(method, question):
        return question

    compact = re.sub(r"\s+", "", question)
    if method == "발표면접":
        prefix = "[발표과제]"
        if "준비시간" not in compact:
            prefix += " 준비시간 20분 후"
    elif method == "토론면접":
        prefix = "[토론과제]"
        if "토론시간" not in compact:
            prefix += " 토론시간 20분 동안"
        if "입장발표" not in compact:
            prefix += " 1분 입장발표 후"
    elif method == "인바스켓면접":
        prefix = "[인바스켓과제]"
        if "제한시간" not in compact:
            prefix += " 제한시간 안에"
    elif method == "창의적 문제해결력면접":
        prefix = "[창의적 문제해결력과제]"
    else:
        prefix = ""
    if not prefix:
        return question
    if not _method_shape_ok(method, question) and not _method_shape_ok(method, f"{prefix} {question}"):
        return question
    return f"{prefix} {question}"


def _normalize_model_job_context(method: str, q: dict[str, Any], text: str) -> str:
    question = str(text or "").strip()
    if not question:
        return ""
    probe = dict(q)
    probe["model_question_preserved"] = True
    if _main_question_job_context_ok(probe, question):
        return question
    terms = _primary_job_context_terms(probe)
    context = _clean_question_text(terms[0] if terms else "", max_chars=60)
    if not context or _compact_contains_term(question, context):
        return question

    marker = ""
    body = question
    for candidate_marker in (
        "[발표과제]",
        "[토론과제]",
        "[인바스켓과제]",
        "[창의적 문제해결력과제]",
    ):
        if body.startswith(candidate_marker):
            marker = candidate_marker
            body = body[len(candidate_marker):].strip()
            break

    if marker:
        candidate = f"{marker} {context} 업무에서 {body}".strip()
    elif method == "상황면접":
        candidate = f"{context} 업무 중 {question}"
    else:
        candidate = f"{context}에서 {question}"

    if _contains_blind_hiring_cue(candidate):
        return question
    if not _method_shape_ok(method, candidate):
        return question
    if not _main_question_task_marker_ok(method, candidate):
        return question
    if not _main_question_job_context_ok(probe, candidate):
        return question
    return candidate


def _evaluation_points_quality_ok(method: str, evaluation_points: list[str]) -> bool:
    if len(evaluation_points) < 4:
        return False
    compact_points = [re.sub(r"\s+", "", str(point or "")) for point in evaluation_points]
    if any(point in _VAGUE_EVALUATION_POINT_KEYS for point in compact_points):
        return False
    anchors = _METHOD_EVALUATION_ANCHORS.get(method, ())
    anchor_hits = {
        anchor
        for anchor in anchors
        if any(anchor in compact for compact in compact_points)
    }
    foreign_anchor_counts = {
        other_method: sum(
            1
            for anchor in other_anchors
            if any(anchor in compact for compact in compact_points)
        )
        for other_method, other_anchors in _METHOD_EVALUATION_ANCHORS.items()
        if other_method != method
    }
    if any(count >= 2 and count >= len(anchor_hits) for count in foreign_anchor_counts.values()):
        return False
    assessable_count = sum(
        1
        for compact in compact_points
        if any(term in compact for term in _ASSESSABLE_EVALUATION_TERMS)
    )
    return len(anchor_hits) >= 2 and assessable_count >= 3


def _job_context_terms(q: dict[str, Any]) -> list[str]:
    raw_values: list[str] = [
        str(q.get("competency") or ""),
        str(q.get("ncs_detail") or ""),
        str(q.get("ncsSubdCdnm") or ""),
        str(q.get("ncsSclasCdnm") or ""),
        str(q.get("question_focus") or ""),
    ]
    if isinstance(q.get("ksa_refs"), list):
        raw_values.extend(str(x or "") for x in q.get("ksa_refs") or [])
    terms: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", value):
            token = token.strip()
            key = token.lower()
            if key in _JOB_CONTEXT_STOPWORDS or len(token) < 2:
                continue
            if key not in seen:
                seen.add(key)
                terms.append(token)
    return terms[:8]


def _job_specific_context_ok(q: dict[str, Any], question: str, follow_ups: list[str]) -> bool:
    terms = _job_context_terms(q)
    if not terms:
        return False
    compact_text = re.sub(r"\s+", "", "\n".join([question, *follow_ups])).lower()
    hits = [term for term in terms if re.sub(r"\s+", "", term).lower() in compact_text]
    required = 1 if len(terms) == 1 else 2
    return len(hits) >= required


def _primary_job_context_terms(q: dict[str, Any]) -> list[str]:
    raw_values = [
        str(q.get("competency") or ""),
        str(q.get("ncs_detail") or ""),
        str(q.get("ncsSubdCdnm") or ""),
        str(q.get("ncsSclasCdnm") or ""),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        clean = _clean_question_text(value, max_chars=80)
        candidates = [clean]
        candidates.extend(re.findall(r"[가-힣A-Za-z0-9]{2,}", clean))
        for token in candidates:
            key = re.sub(r"\s+", "", str(token or "")).lower()
            if not key or key in _JOB_CONTEXT_STOPWORDS or len(key) < 2:
                continue
            if key not in seen:
                seen.add(key)
                terms.append(token)
    return terms[:8]


def _main_question_job_context_ok(q: dict[str, Any], question: str) -> bool:
    if not bool(q.get("model_question_preserved")):
        return True
    terms = _primary_job_context_terms(q)
    if not terms:
        return True
    compact_question = re.sub(r"\s+", "", str(question or "")).lower()
    return any(re.sub(r"\s+", "", term).lower() in compact_question for term in terms)


def _follow_ups_quality_ok(method: str, q: dict[str, Any], follow_ups: list[str]) -> bool:
    clean = [str(item or "").strip() for item in follow_ups if str(item or "").strip()]
    if len(clean) < 3:
        return False
    keys = [normalize_question_dedup_key(item) for item in clean]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        return False

    compact_items = [re.sub(r"\s+", "", item) for item in clean]
    merged = "\n".join(compact_items).lower()
    anchors = _FOLLOW_UP_METHOD_ANCHORS.get(method, ())
    anchor_hits = {
        anchor
        for anchor in anchors
        if any(anchor in compact for compact in compact_items)
    }
    if len(anchor_hits) < 2:
        return False

    open_prompt_hits = sum(
        1
        for item in clean[:3]
        if re.search(r"(무엇|어떤|어떻게|얼마|어땠|어떠|왜|기준|이유|설명|말씀|제시|확인|선택|평가|점검|정리)", item)
    )
    if open_prompt_hits < 3:
        return False

    context_terms = _job_context_terms(q)
    if context_terms:
        context_hits = [
            term
            for term in context_terms
            if re.sub(r"\s+", "", term).lower() in merged
        ]
        if not context_hits:
            return False
    focus = str(q.get("question_focus") or "").strip()
    if focus and not _ksa_factor_relevant_to_text(focus, "\n".join(clean)):
        return False
    return True


def _ksa_factor_relevant_to_text(factor_name: str, text: str) -> bool:
    factor = str(factor_name or "").strip()
    if not factor:
        return False
    compact_factor = re.sub(r"\s+", "", factor).lower()
    compact_text = re.sub(r"\s+", "", str(text or "")).lower()
    if compact_factor and compact_factor in compact_text:
        return True

    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", factor):
        key = token.lower()
        if key in _KSA_RELEVANCE_STOPWORDS:
            continue
        if key not in seen:
            seen.add(key)
            tokens.append(token)
    if not tokens:
        return False
    hits = [
        token
        for token in tokens
        if re.sub(r"\s+", "", token).lower() in compact_text
    ]
    required = 1 if len(tokens) == 1 else 2
    return len(hits) >= required


def _ksa_evidence_relevance_ok(
    question: str,
    follow_ups: list[str],
    evaluation_points: list[str],
    q: dict[str, Any],
    matching_ksa_evidence: list[dict[str, Any]],
) -> bool:
    if not matching_ksa_evidence:
        return False
    evidence_text = "\n".join(
        [
            str(question or ""),
            *[str(x or "") for x in follow_ups],
            *[str(x or "") for x in evaluation_points],
            str(q.get("question_focus") or ""),
        ]
    )
    if _UNRESOLVED_KSA_PLACEHOLDER_RE.search(evidence_text):
        return False
    return any(
        _ksa_factor_relevant_to_text(str(row.get("factorName") or ""), evidence_text)
        for row in matching_ksa_evidence
        if isinstance(row, dict)
    )


def _official_sample_format_ok(
    method: str,
    question: str,
    follow_ups: list[str],
    evaluation_points: list[str],
) -> bool:
    task_text = re.sub(r"\s+", "", "\n".join([str(question or ""), *follow_ups]))
    eval_text = re.sub(r"\s+", "", "\n".join(evaluation_points))
    rule = _OFFICIAL_SAMPLE_FORMAT_RULES.get(method)
    if not rule:
        return False
    return (
        any(term in task_text for term in rule["task_any"])
        and all(term in task_text for term in rule["task_all"])
        and any(term in eval_text for term in rule["eval_any"])
    )


def _attach_question_quality_report(strategy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        strategy = {}
    questions = strategy.get("interview_questions")
    if not isinstance(questions, list):
        strategy["question_quality_report"] = {
            "policy": "main_question_method_shape_ksa_official_sample_eval_followup_job_context_gate_v6",
            "passed": False,
            "summary": {
                "question_count": 0,
                "expected_question_count": 0,
                "count_matches_plan": False,
                "average_score": 0.0,
                "ready_count": 0,
                "needs_review_count": 0,
            },
            "items": [],
        }
        return strategy

    plan = strategy.get("question_plan_used") if isinstance(strategy.get("question_plan_used"), dict) else {}
    try:
        expected_count = int(plan.get("total_main_count") or 0)
    except Exception:
        expected_count = 0

    question_keys = [
        normalize_question_dedup_key(str((q or {}).get("question") or ""))
        for q in questions
        if isinstance(q, dict)
    ]
    duplicate_keys = {key for key in question_keys if key and question_keys.count(key) > 1}

    items: list[dict[str, Any]] = []
    ready_count = 0
    for idx, raw in enumerate(questions, start=1):
        q = raw if isinstance(raw, dict) else {}
        method = str(q.get("type") or q.get("method") or "").strip()
        question = str(q.get("question") or "").strip()
        follow_ups = [str(x).strip() for x in (q.get("follow_ups") or []) if str(x).strip()] if isinstance(q.get("follow_ups"), list) else []
        evaluation_points = [
            str(x).strip()
            for x in (q.get("evaluation_points") or [])
            if str(x).strip()
        ] if isinstance(q.get("evaluation_points"), list) else []
        ksa_refs = [str(x).strip() for x in (q.get("ksa_refs") or []) if str(x).strip()] if isinstance(q.get("ksa_refs"), list) else []
        ksa_evidence = q.get("ksa_evidence") if isinstance(q.get("ksa_evidence"), list) else []
        ncs_code = str(q.get("ncsClCd") or "").strip()
        matching_ksa_evidence = [
            row
            for row in ksa_evidence
            if isinstance(row, dict) and str(row.get("ncsClCd") or "").strip() == ncs_code
        ] if ncs_code else []
        merged = "\n".join([question, *follow_ups, *evaluation_points, *ksa_refs])
        q_key = normalize_question_dedup_key(question)
        has_specific_context = not any(marker in question for marker in ("해당 직무", "핵심 수행기준"))
        checks = {
            "supported_method": method in QUALITY_INTERVIEW_METHODS,
            "method_shape": _method_shape_ok(method, merged),
            "main_question_method_shape": _method_shape_ok(method, question),
            "main_question_job_context": _main_question_job_context_ok(q, question),
            "follow_up_depth": len(follow_ups) >= 3,
            "follow_up_quality": _follow_ups_quality_ok(method, q, follow_ups),
            "evaluation_points": len(evaluation_points) >= 4,
            "evaluation_points_quality": _evaluation_points_quality_ok(method, evaluation_points),
            "ncs_grounded": bool(ncs_code and str(q.get("competency") or "").strip()),
            "detail_grounded": bool(str(q.get("ncs_detail") or q.get("ncsSclasCdnm") or "").strip()),
            "ksa_grounded": _ksa_evidence_relevance_ok(question, follow_ups, evaluation_points, q, matching_ksa_evidence),
            "official_sample_format": _official_sample_format_ok(method, question, follow_ups, evaluation_points),
            "blind_hiring_safe": not _contains_blind_hiring_cue(merged),
            "unique_question": bool(q_key and q_key not in duplicate_keys),
            "specific_context": has_specific_context,
            "job_specific_context": _job_specific_context_ok(q, question, follow_ups),
        }
        issues = [name for name, passed in checks.items() if not passed]
        score = round(sum(1 for passed in checks.values() if passed) / max(1, len(checks)), 2)
        ready = not issues
        if ready:
            ready_count += 1
        items.append(
            {
                "index": idx,
                "type": method,
                "competency": str(q.get("competency") or "").strip(),
                "ncsClCd": str(q.get("ncsClCd") or "").strip(),
                "ncs_detail": str(q.get("ncs_detail") or q.get("ncsSclasCdnm") or "").strip(),
                "score": score,
                "ready": ready,
                "checks": checks,
                "issues": issues,
            }
        )

    avg = round(sum(float(item.get("score") or 0.0) for item in items) / max(1, len(items)), 2)
    count_matches_plan = expected_count <= 0 or len(items) == expected_count
    passed = bool(count_matches_plan and items and ready_count == len(items))
    strategy["question_quality_report"] = {
        "policy": "main_question_method_shape_ksa_official_sample_eval_followup_job_context_gate_v6",
        "passed": passed,
        "summary": {
            "question_count": len(items),
            "expected_question_count": expected_count,
            "count_matches_plan": count_matches_plan,
            "average_score": avg,
            "ready_count": ready_count,
            "needs_review_count": len(items) - ready_count,
        },
        "items": items,
    }
    return strategy


def _attach_ksa_evidence_to_strategy(strategy: dict[str, Any], ncs_ksa: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        strategy = {}
    questions = strategy.get("interview_questions")
    if not isinstance(questions, list):
        return strategy

    evidence_rows: list[dict[str, str]] = []
    seen_rows: set[tuple[str, str]] = set()
    for raw in ncs_ksa or []:
        if not isinstance(raw, dict):
            continue
        row = _clean_ksa_evidence_row(raw)
        if not row["factorName"]:
            continue
        key = (row["ncsClCd"], _ksa_key(row["factorName"]))
        if key in seen_rows:
            continue
        seen_rows.add(key)
        evidence_rows.append(row)
    if not evidence_rows:
        return _attach_question_quality_report(strategy)

    def _pick_for_question(question: dict[str, Any]) -> list[dict[str, str]]:
        code = str(question.get("ncsClCd", "")).strip()
        refs = [str(x).strip() for x in (question.get("ksa_refs") or []) if str(x).strip()] if isinstance(question.get("ksa_refs"), list) else []
        focus_ref = str(question.get("question_focus") or "").strip()
        if focus_ref:
            refs = [focus_ref, *[ref for ref in refs if _ksa_key(ref) != _ksa_key(focus_ref)]]
        ref_keys = [_ksa_key(x) for x in refs]
        picked: list[dict[str, str]] = []
        picked_keys: set[tuple[str, str]] = set()

        def add(row: dict[str, str]) -> None:
            if len(picked) >= 4:
                return
            key = (row["ncsClCd"], _ksa_key(row["factorName"]))
            if key in picked_keys:
                return
            picked_keys.add(key)
            picked.append(row)

        preferred = [row for row in evidence_rows if code and row["ncsClCd"] == code]
        if not code or not preferred:
            return []
        fallback = preferred

        for ref_key in ref_keys:
            if not ref_key:
                continue
            for row in fallback:
                factor_key = _ksa_key(row["factorName"])
                if ref_key in factor_key or factor_key in ref_key:
                    add(row)
            if len(picked) >= 2:
                break
        for row in fallback:
            add(row)
            if len(picked) >= 3:
                break
        return picked[:3]

    enriched: list[dict[str, Any]] = []
    for item in questions:
        if not isinstance(item, dict):
            enriched.append(item)
            continue
        q = dict(item)
        evidence = _pick_for_question(q)
        if evidence:
            existing_refs = [
                str(x).strip()
                for x in (q.get("ksa_refs") or [])
                if str(x).strip()
            ] if isinstance(q.get("ksa_refs"), list) else []
            for row in evidence:
                factor = row.get("factorName", "")
                if factor and factor not in existing_refs:
                    existing_refs.append(factor)
            q["ksa_refs"] = existing_refs[:4]
            q["ksa_evidence"] = evidence
        enriched.append(q)
    strategy["interview_questions"] = enriched
    strategy["question_evidence_policy"] = "ncs_mcp_ksa_attached_by_code_and_ref"
    return _attach_question_quality_report(strategy)


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
        source = "ncs-mcp"
        message = ""
        if not items:
            items = suggest_units_by_text([term], max_units=min(limit, 50))
            source = "ncs-mcp-suggest"
            message = "Exact detail-class match was not found. Review suggested NCS units manually."
    except NcsMcpError as exc:
        raise HTTPException(status_code=502, detail=f"로컬 NCS DB 조회 실패(NCS_MCP): {exc}") from exc
    return {"count": len(items), "items": items, "source": source, "message": message}


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
            "message": "NCS_MCP local NCS DB server is ready" if ok else "NCS_MCP local NCS DB server is not ready",
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
async def parse_jd_review_endpoint(request: Request, jd_file: UploadFile = File(...)) -> dict:
    """Parse a JD with Kordoc and return editable human-review fields."""

    data = await jd_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    _check_upload_size(data, "jd_file")
    parsed = _parse_upload_document(data, jd_file.filename or "", "jd_file")
    structured = structure_job_description(parsed, filename=jd_file.filename or "")
    review_session = _create_review_session(data, structured, jd_file.filename or "")
    _record_audit_event(
        request,
        action="jd_parse_review",
        resource_type="jd_review_session",
        resource_id=review_session["id"],
    )
    structured["review_session_id"] = review_session["id"]
    structured["review_session"] = review_session
    return structured


@app.post("/api/notice/parse-review")
async def parse_notice_review_endpoint(notice_file: UploadFile = File(...)) -> dict:
    """Parse a job notice and return editable duty/evaluation text candidates."""

    data = await notice_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="notice_file is empty")
    _check_upload_size(data, "notice_file")
    filename = notice_file.filename or ""
    parsed = _parse_upload_document(data, filename, "notice_file")
    return structure_job_notice(parsed, filename=filename)


@app.post("/api/jd/strategy/upload")
async def jd_strategy_upload(
    request: Request,
    jd_file: UploadFile = File(...),
    notice_file: UploadFile | None = File(default=None),
    strengths: str = Form(default=""),
    openai_api_key: str = Form(default=""),
    manual_sclass: str = Form(default=""),
    manual_sclass_add: str = Form(default=""),
    manual_sclass_remove: str = Form(default=""),
    duty_text: str = Form(default=""),
    qualification_text: str = Form(default=""),
    preference_text: str = Form(default=""),
    evaluation_text: str = Form(default=""),
    question_plan_json: str = Form(default=""),
    interview_methods_json: str = Form(default=""),
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
        parsed = _parse_upload_document(data, upload.filename or "", label)
        text = str(parsed.get("markdown") or "")
        if text.strip():
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
    review_session: dict[str, Any] | None = None
    if review_payload.get("review_confirmed") is True:
        review_session = _validate_review_session(review_payload, jd_bytes)
    reviewed_markdown = str((review_session or {}).get("markdown") or "").strip()
    if reviewed_markdown:
        jd_text = reviewed_markdown
    duty_text_clean = _merge_review_text(
        duty_text,
        reviewed_fields.get("duties") or [],
        max_chars=3000,
    )
    qualification_text_clean = _merge_review_text(
        qualification_text,
        reviewed_fields.get("qualifications") or [],
        max_chars=2400,
    )
    preference_text_clean = _merge_review_text(
        preference_text,
        reviewed_fields.get("preferences") or [],
        max_chars=2400,
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
        qualification_text=qualification_text_clean,
        preference_text=preference_text_clean,
        evaluation_text=evaluation_text_clean,
    )
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
    question_plan = _parse_question_plan_json(question_plan_json, reviewed_detail_terms)
    interview_methods = _parse_interview_methods(interview_methods_json)
    if question_plan["selected_terms"]:
        reviewed_detail_terms = list(question_plan["selected_terms"])
    if not mcp_only and not reviewed_detail_terms:
        reviewed_detail_terms = extract_detail_categories_from_jd(jd_text)

    ncs_items: list[dict[str, Any]] = []
    # The confirmed review payload is the gate for the authoritative local NCS DB lookup through NCS_MCP.
    # lookup. It prevents an unreviewed OCR label from driving KSA selection.
    if mcp_only:
        if review_payload.get("review_confirmed") is not True:
            raise HTTPException(
                status_code=400,
                detail="jd_review_json.review_confirmed must be true before local NCS DB lookup",
            )
        lookup_terms = reviewed_detail_terms
        if not lookup_terms:
            raise HTTPException(
                status_code=422,
                detail="reviewed NCS detail candidates are required for local NCS DB lookup",
            )
        try:
            ncs_items = search_units_by_detail(
                lookup_terms,
                max_units=max(20, run_top_k * 12),
            )
        except NcsMcpError as exc:
            raise HTTPException(status_code=502, detail=f"로컬 NCS DB 조회 실패(NCS_MCP): {exc}") from exc
        if not ncs_items:
            try:
                suggested_units = suggest_units_by_text(lookup_terms, max_units=12)
            except NcsMcpError:
                suggested_units = []
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Local NCS DB server(NCS_MCP) returned no exact competency units for the reviewed detail-class terms.",
                    "lookup_terms": lookup_terms[:8],
                    "suggested_ncs_units": suggested_units,
                    "next_step": (
                        "If the JD uses an institution-specific or out-of-DB label, switch to manual text mode, "
                        "review the suggested NCS units, select the closest official units, and generate questions."
                    ),
                },
            )
        matched_lookup_terms, unmatched_lookup_terms = _detail_lookup_coverage(lookup_terms, ncs_items)
        if unmatched_lookup_terms:
            try:
                suggested_units = suggest_units_by_text(unmatched_lookup_terms, max_units=12)
            except NcsMcpError:
                suggested_units = []
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Local NCS DB server(NCS_MCP) returned only partial exact coverage for the reviewed detail-class terms.",
                    "lookup_terms": lookup_terms[:8],
                    "matched_detail_terms": matched_lookup_terms[:8],
                    "unmatched_detail_terms": unmatched_lookup_terms[:8],
                    "suggested_ncs_units": suggested_units,
                    "next_step": (
                        "Resolve unmatched detail-class terms before generation. Use manual text mode only after a human "
                        "selects the closest official NCS units; non-authoritative suggestions do not count as exact coverage."
                    ),
                },
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
            ncs_error = f"로컬 NCS DB 조회 실패(NCS_MCP): {exc}"

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
        qualification_text=qualification_text_clean,
        preference_text=preference_text_clean,
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
            detail=f"Local NCS DB units were found through NCS_MCP, but no NCS matches survived ranking: {ncs_query_terms[:8]}",
        )

    # NCS 평가요소를 수집해 OpenAI 입력에 함께 전달한다.
    # 전체 KSA 후보를 넓게 수집한 뒤, JD 핵심 + 담당업무 텍스트 기준 TF-IDF로 상위만 선별한다.
    ncs_ksa: list[dict[str, Any]] = []
    ncs_ksa_candidates: list[dict[str, Any]] = []
    ksa_query_text = _build_priority_query_text(
        base_text=jd_text,
        duty_text=duty_text_clean,
        qualification_text=qualification_text_clean,
        preference_text=preference_text_clean,
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
        ncs_ksa = _supplement_ksa_for_question_plan(
            question_plan=question_plan,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
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
                target_count_override=question_plan["total_main_count"],
                follow_up_count=question_plan["follow_up_count"],
                question_plan=question_plan,
                interview_methods=interview_methods,
            ),
        )
        strategy = _adjust_generated_questions(
            strategy,
            question_plan,
            interview_methods,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
        )
    except Exception as e:
        strategy = build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message=f"model_generation_failed: {e}",
            target_count=question_plan["total_main_count"] or 24,
        )
        strategy = _adjust_generated_questions(
            strategy,
            question_plan,
            interview_methods,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
        )
    strategy = _attach_ksa_evidence_to_strategy(strategy, ncs_ksa)

    if review_session:
        _record_audit_event(
            request,
            action="jd_strategy_generate",
            resource_type="jd_review_session",
            resource_id=str(review_session.get("id") or ""),
        )

    return {
        "filename": jd_file.filename,
        "notice_filename": notice_file.filename if notice_file else "",
        "jd_text_preview": jd_text[:1200],
        "notice_text_preview": notice_text[:1200],
        "notice_context_preview": notice_context[:1200],
        "duty_text_preview": duty_text_clean[:1200],
        "qualification_text_preview": qualification_text_clean[:1200],
        "preference_text_preview": preference_text_clean[:1200],
        "evaluation_text_preview": evaluation_text_clean[:1200],
        "jd_review_confirmed": review_payload.get("review_confirmed") is True,
        "jd_review_session_id": (review_session or {}).get("id", ""),
        "jd_review_document_sha256": (review_session or {}).get("document_sha256", ""),
        "jd_review": (
            {
                "review_confirmed": review_payload.get("review_confirmed") is True,
                "review_session_id": (review_session or {}).get("id", review_payload.get("review_session_id", "")),
                "fields": reviewed_fields,
            }
            if review_payload
            else None
        ),
        "question_plan": question_plan,
        "interview_methods": interview_methods,
        "operational_notice": (
            "NCScope output is a KSA-grounded structured-interview draft. "
            "A human reviewer must confirm final questions against blind-hiring rules and institution-specific evaluation standards."
        ),
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
async def generate_questions_from_text(request: Request, payload: dict) -> dict:
    notice_text = str(payload.get("notice_text", "")).strip()
    duty_text = str(payload.get("duty_text", "")).strip()
    evaluation_text = str(payload.get("evaluation_text", "")).strip()
    request_openai_api_key = _sanitize_request_openai_key(payload.get("openai_api_key", ""))
    selected_ncs = payload.get("selected_ncs", [])
    raw_interview_methods = payload.get("interview_methods_json", payload.get("interview_methods", ""))
    if not isinstance(raw_interview_methods, str):
        raw_interview_methods = json.dumps(raw_interview_methods, ensure_ascii=False)
    interview_methods = _parse_interview_methods(raw_interview_methods)
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

    plan_terms = []
    seen_plan_terms: set[str] = set()
    for row in ncs_matches:
        term = str(row.get("ncsSubdCdnm") or row.get("compeUnitName") or "").strip()
        key = _norm_sclass_key(term)
        if term and key and key not in seen_plan_terms:
            seen_plan_terms.add(key)
            plan_terms.append(term)
    raw_question_plan = payload.get("question_plan_json", payload.get("question_plan", ""))
    if not isinstance(raw_question_plan, str):
        raw_question_plan = json.dumps(raw_question_plan, ensure_ascii=False)
    question_plan = _parse_question_plan_json(raw_question_plan, plan_terms)
    question_plan = _restrict_question_plan_to_terms(question_plan, plan_terms)

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
    ncs_ksa = _supplement_ksa_for_question_plan(
        question_plan=question_plan,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
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
                target_count_override=question_plan["total_main_count"] or None,
                follow_up_count=question_plan["follow_up_count"],
                question_plan=question_plan,
                interview_methods=interview_methods,
            ),
        )
        strategy = _adjust_generated_questions(
            strategy,
            question_plan,
            interview_methods,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
        )
    except Exception as e:
        strategy = build_strategy_with_rule_fallback(
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            error_message=f"model_generation_failed: {e}",
            target_count=question_plan["total_main_count"] or 24,
        )
        strategy = _adjust_generated_questions(
            strategy,
            question_plan,
            interview_methods,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
        )
    strategy = _attach_ksa_evidence_to_strategy(strategy, ncs_ksa)

    _record_audit_event(
        request,
        action="manual_ncs_generate",
        resource_type="selected_ncs",
        resource_id=_sha256_text(",".join(str(x.get("ncsClCd", "")) for x in ncs_matches)),
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
        "question_plan": question_plan,
        "interview_methods": interview_methods,
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
