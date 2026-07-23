from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.main import (  # noqa: E402
    SUPPORTED_INTERVIEW_METHODS,
    _adjust_generated_questions,
    _attach_ksa_evidence_to_strategy,
    _parse_question_plan_json,
    _select_units_for_question_plan,
)
from app.services.kordoc_parser import KordocParseError, structure_job_description  # noqa: E402
from app.services.ncs_mcp_client import NcsMcpError, get_ksa_by_units, search_units_by_detail, suggest_units_by_text  # noqa: E402
from app.services.jd_strategy import build_strategy_with_openai as build_jd_strategy_with_openai  # noqa: E402
from benchmark_alio_jd import detail_member_map, parse_benchmark_document  # noqa: E402


BENCHMARK_MODES = {"template", "model", "auto"}


def _idx_from_path(path: Path) -> str:
    match = re.match(r"(?P<idx>\d+)_", path.name)
    return match.group("idx") if match else ""


def iter_cached_attachments(cache_dir: Path, limit: int) -> list[Path]:
    files = [
        path
        for path in sorted(cache_dir.iterdir(), key=lambda p: p.name)
        if path.is_file() and re.match(r"\d+_\d+_", path.name)
    ]
    files.sort(key=lambda path: (int(_idx_from_path(path) or 0), path.name), reverse=True)
    return files[: max(1, int(limit))]


def allocate_question_counts(details: list[str], total: int) -> list[tuple[str, int]]:
    clean = [str(detail or "").strip() for detail in details if str(detail or "").strip()]
    if not clean or total <= 0:
        return []
    selected = clean[: min(len(clean), total)]
    counts = {detail: 1 for detail in selected}
    remaining = total - len(selected)
    pos = 0
    while remaining > 0:
        counts[selected[pos % len(selected)]] += 1
        remaining -= 1
        pos += 1
    return [(detail, counts[detail]) for detail in selected]


def build_question_plan(details: list[str], total: int, follow_up_count: int) -> dict[str, Any]:
    items = [
        {
            "detail": detail,
            "enabled": True,
            "main_count": count,
            "follow_up_count": follow_up_count,
        }
        for detail, count in allocate_question_counts(details, total)
    ]
    return _parse_question_plan_json(json.dumps({"items": items}, ensure_ascii=False), details)


def _normalize_benchmark_mode(mode: str) -> str:
    value = str(mode or "template").strip().lower()
    return value if value in BENCHMARK_MODES else "template"


def _resolve_benchmark_mode(mode: str, openai_api_key: str) -> str:
    requested = _normalize_benchmark_mode(mode)
    if requested == "auto":
        return "model" if str(openai_api_key or "").strip() else "template"
    return requested


def _join_field_text(value: Any, max_chars: int = 2000) -> str:
    if isinstance(value, list):
        text = "\n".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _build_benchmark_strategy(
    *,
    parsed: dict[str, Any],
    fields: dict[str, Any],
    plan: dict[str, Any],
    units: list[dict[str, Any]],
    ksa: list[dict[str, Any]],
    follow_up_count: int,
    benchmark_mode: str,
    openai_api_key: str,
) -> tuple[dict[str, Any], str]:
    resolved_mode = _resolve_benchmark_mode(benchmark_mode, openai_api_key)
    if resolved_mode == "model":
        strategy = build_jd_strategy_with_openai(
            jd_text=str(parsed.get("markdown") or ""),
            notice_text="",
            strengths="",
            region="",
            ncs_matches=units,
            ncs_ksa=ksa,
            ncs_context={"benchmark": "alio_question_quality", "benchmark_mode": resolved_mode},
            duty_text=_join_field_text(fields.get("duties"), max_chars=2200),
            evaluation_text=_join_field_text(fields.get("evaluation_text"), max_chars=1600),
            desired_job="",
            api_key_override=openai_api_key,
            target_count_override=int(plan.get("total_main_count") or 0) or None,
            follow_up_count=follow_up_count,
            question_plan=plan,
            interview_methods=list(SUPPORTED_INTERVIEW_METHODS),
        )
        if not isinstance(strategy, dict):
            strategy = {
                "interview_questions": [],
                "question_generation_policy": "model_benchmark_invalid_response",
                "error": "model_generation_failed: response was not an object",
            }
    else:
        strategy = {
            "interview_questions": [],
            "question_generation_policy": "template_benchmark_no_model",
        }

    strategy["benchmark_mode"] = _normalize_benchmark_mode(benchmark_mode)
    strategy["resolved_benchmark_mode"] = resolved_mode
    strategy = _adjust_generated_questions(
        strategy,
        plan,
        list(SUPPORTED_INTERVIEW_METHODS),
        ncs_matches=units,
        ncs_ksa=ksa,
    )
    return _attach_ksa_evidence_to_strategy(strategy, ksa), resolved_mode


def _dedup_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        if not isinstance(unit, dict):
            continue
        key = str(unit.get("ncsClCd") or unit.get("compeUnitName") or unit).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(unit)
    return out


def _norm_detail_key(value: Any) -> str:
    return re.sub(r"[\s\-\_/|(),.·・〮‧･ㆍ•∙⋅]+", "", str(value or "")).lower()


_MANUAL_REVIEW_SUGGESTIONS_BY_KEY: dict[str, str] = {
    _norm_detail_key("간호업무 보조"): (
        "manual-review-only: nearby 요양지원 units include "
        "0601010801_23v3 진료지원보조, 0601010802_23v3 물품전달, "
        "0601010803_23v3 환자이송지원, 0601010808_23v3 사고예방지원; "
        "do not count as exact coverage without human selection"
    ),
    _norm_detail_key("간호행정 보조"): (
        "manual-review-only: no exact local NCS hit; broad 병원행정 candidates are too weak for automatic coverage"
    ),
    _norm_detail_key("재원환자 관리"): (
        "false friend: element-level 재원환자 관리하기 belongs to 0601020110_16v2 진료비관리 under 병원행정; "
        "keep unresolved in clinical nursing context"
    ),
    _norm_detail_key("응급 환자 관리"): (
        "manual-review-only: source-like 0602020000_17v1 is not available in local MCP; "
        "응급환자 searches return rescue/industrial units, not nursing"
    ),
    _norm_detail_key("영상의학"): (
        "manual-review-only: no exact local/public NCS unit hit for human radiology context"
    ),
    _norm_detail_key("임상병리"): (
        "false friend: public NCS search returns animal/nonclinical pathology hits, not human clinical laboratory context"
    ),
    _norm_detail_key("간호조무"): (
        "manual-review-only: no exact local/public NCS hit; nearby 요양지원 or 병원행정 units require human selection"
    ),
    _norm_detail_key("간호수행"): (
        "manual-review-only: no exact local/public NCS hit for nursing-performance label"
    ),
    _norm_detail_key("간호행정관리"): (
        "manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage"
    ),
    _norm_detail_key("유지관리"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage; "
        "do not borrow broad maintenance suggestions automatically"
    ),
    _norm_detail_key("건축감리"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage"
    ),
    _norm_detail_key("문화・관광정책"): (
        "manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage"
    ),
}


def _manual_review_suggestions(details: list[str]) -> str:
    suggestions: list[str] = []
    for detail in details:
        term = str(detail or "").strip()
        suggestion = _MANUAL_REVIEW_SUGGESTIONS_BY_KEY.get(_norm_detail_key(term))
        if suggestion:
            suggestions.append(f"{term}: {suggestion}")
    return " | ".join(suggestions)


def _exact_units_by_detail(
    details: list[str],
    max_units_per_detail: int,
) -> tuple[list[str], list[str], list[dict[str, Any]], list[str]]:
    exact_details: list[str] = []
    unit_name_details: list[str] = []
    units: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for detail in details:
        term = str(detail or "").strip()
        if not term:
            continue
        found = search_units_by_detail([term], max_units=max_units_per_detail)
        if found:
            exact_details.append(term)
            units.extend(found)
        else:
            suggestions = suggest_units_by_text([term], max_units=max_units_per_detail)
            unit_matches = [
                row
                for row in suggestions
                if isinstance(row, dict)
                and (
                    row.get("isExactUnitNameMatch")
                    or _norm_detail_key(row.get("compeUnitName")) == _norm_detail_key(term)
                )
            ]
            if unit_matches:
                unit_name_details.append(term)
                for row in unit_matches:
                    copied = dict(row)
                    copied["source"] = "ncs-mcp-unit-name"
                    copied["matchedDetailName"] = term
                    copied["unitNameMatchedDetailLabel"] = term
                    units.append(copied)
            else:
                unmatched.append(term)
    return exact_details, unit_name_details, _dedup_units(units), unmatched


def evaluate_cached_document(
    path: Path,
    max_bytes: int,
    questions_per_doc: int,
    follow_up_count: int,
    max_details_per_doc: int,
    max_units_per_detail: int,
    ksa_units: int,
    ksa_factors_per_unit: int,
    benchmark_mode: str = "template",
    openai_api_key: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    idx = _idx_from_path(path)
    row: dict[str, Any] = {
        "idx": idx,
        "attachment": path.name,
        "status": "unknown",
        "benchmark_mode": _normalize_benchmark_mode(benchmark_mode),
        "resolved_benchmark_mode": _resolve_benchmark_mode(benchmark_mode, openai_api_key),
        "strategy_generation_policy": "",
        "strategy_error": "",
        "strategy_warning": "",
        "detail_count": 0,
        "detail_source": "",
        "exact_detail_count": 0,
        "unit_name_detail_count": 0,
        "unmatched_detail_count": 0,
        "skipped_detail_count": 0,
        "uncovered_detail_count": 0,
        "generated_questions": 0,
        "ready_questions": 0,
        "needs_review_questions": 0,
        "model_candidate_questions": 0,
        "model_questions": 0,
        "model_ready_questions": 0,
        "model_replaced_by_template_questions": 0,
        "template_inserted_questions": 0,
        "template_fallback_questions": 0,
        "template_fallback_ready_questions": 0,
        "average_score": 0.0,
        "coverage_adjusted_score": 0.0,
        "coverage_passed": False,
        "template_adjusted_passed": False,
        "strict_template_passed": False,
        "model_quality_passed": False,
        "passed": False,
        "details": "",
        "exact_details": "",
        "unit_name_details": "",
        "unmatched_details": "",
        "skipped_details": "",
        "manual_review_suggestions": "",
        "error": "",
    }
    question_rows: list[dict[str, Any]] = []
    try:
        parsed = parse_benchmark_document(path.read_bytes(), filename=path.name, max_bytes=max_bytes)
        structured = structure_job_description(parsed, filename=path.name)
        fields = structured.get("fields", {}) if isinstance(structured.get("fields"), dict) else {}
        row["detail_source"] = str(fields.get("ncs_detail_source") or "")
        details = list(fields.get("ncs_detail_candidates") or [])
        details = [str(detail).strip() for detail in details if str(detail).strip()]
        row["detail_count"] = len(details)
        row["details"] = "; ".join(details)
        if not details:
            row["status"] = "parsed_no_detail"
            return row, question_rows

        detail_members = detail_member_map(parsed, fallback_member=path.name, details=details)
        checked_details = details[:max_details_per_doc]
        skipped_details = details[max_details_per_doc:]
        exact_details, unit_name_details, units, unmatched = _exact_units_by_detail(checked_details, max_units_per_detail)
        uncovered_details = unmatched + skipped_details
        covered_details = exact_details + unit_name_details
        row["exact_detail_count"] = len(exact_details)
        row["unit_name_detail_count"] = len(unit_name_details)
        row["unmatched_detail_count"] = len(unmatched)
        row["skipped_detail_count"] = len(skipped_details)
        row["uncovered_detail_count"] = len(uncovered_details)
        row["exact_details"] = "; ".join(exact_details)
        row["unit_name_details"] = "; ".join(unit_name_details)
        row["unmatched_details"] = "; ".join(unmatched)
        row["skipped_details"] = "; ".join(skipped_details)
        row["manual_review_suggestions"] = _manual_review_suggestions(unmatched)
        if not covered_details or not units:
            row["status"] = "no_exact_units"
            return row, question_rows

        plan = build_question_plan(
            covered_details,
            total=questions_per_doc,
            follow_up_count=follow_up_count,
        )
        selected_units = _select_units_for_question_plan(plan, units)
        ksa_lookup_limit = max(int(ksa_units), len(selected_units))
        ksa_units_for_questions = selected_units[:ksa_lookup_limit]
        ksa = get_ksa_by_units(ksa_units_for_questions, max_factors_per_unit=ksa_factors_per_unit)
        strategy, resolved_mode = _build_benchmark_strategy(
            parsed=parsed,
            fields=fields,
            plan=plan,
            units=units,
            ksa=ksa,
            follow_up_count=follow_up_count,
            benchmark_mode=benchmark_mode,
            openai_api_key=openai_api_key,
        )
        row["resolved_benchmark_mode"] = resolved_mode
        row["strategy_generation_policy"] = str(strategy.get("question_generation_policy") or "")
        row["strategy_error"] = str(strategy.get("error") or "")
        row["strategy_warning"] = str(strategy.get("warning") or "")
        report = strategy.get("question_quality_report") if isinstance(strategy.get("question_quality_report"), dict) else {}
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        row["generated_questions"] = int(summary.get("question_count") or 0)
        row["ready_questions"] = int(summary.get("ready_count") or 0)
        row["needs_review_questions"] = int(summary.get("needs_review_count") or 0)
        row["average_score"] = float(summary.get("average_score") or 0.0)
        question_gate_passed = bool(report.get("passed"))
        coverage_complete = not uncovered_details and len(covered_details) == len(details)
        exact_coverage_complete = coverage_complete and not unit_name_details
        explicit_detail_source = row.get("detail_source") == "explicit"
        contextual_detail_source = row.get("detail_source") == "contextual"
        coverage_passed = bool(exact_coverage_complete and explicit_detail_source)

        questions = strategy.get("interview_questions") if isinstance(strategy.get("interview_questions"), list) else []
        report_items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
        ready_by_index = {
            int(item.get("index") or 0): bool(item.get("ready"))
            for item in report_items
            if str(item.get("index") or "").strip()
        }
        model_questions = 0
        model_ready_questions = 0
        model_candidate_questions = 0
        model_replaced_questions = 0
        template_inserted_questions = 0
        fallback_questions = 0
        fallback_ready_questions = 0
        for q_index, q_obj in enumerate(questions, start=1):
            if not isinstance(q_obj, dict):
                continue
            source = str(q_obj.get("question_source") or "unknown").strip() or "unknown"
            raw_model_question = str(q_obj.get("model_question_raw") or "").strip()
            had_model_candidate = bool(raw_model_question)
            model_candidate_questions += int(had_model_candidate)
            is_ready = bool(ready_by_index.get(q_index))
            if source == "model":
                model_questions += 1
                model_ready_questions += int(is_ready)
            elif source == "template_fallback":
                fallback_questions += 1
                fallback_ready_questions += int(is_ready)
                if had_model_candidate:
                    model_replaced_questions += 1
                else:
                    template_inserted_questions += 1

        template_adjusted_passed = bool(question_gate_passed)
        strict_template_passed = bool(template_adjusted_passed and coverage_passed)
        model_quality_passed = bool(
            question_gate_passed
            and row["generated_questions"] > 0
            and model_questions == row["generated_questions"]
        )
        row["model_candidate_questions"] = model_candidate_questions
        row["model_questions"] = model_questions
        row["model_ready_questions"] = model_ready_questions
        row["model_replaced_by_template_questions"] = model_replaced_questions
        row["template_inserted_questions"] = template_inserted_questions
        row["template_fallback_questions"] = fallback_questions
        row["template_fallback_ready_questions"] = fallback_ready_questions
        row["coverage_passed"] = coverage_passed
        row["template_adjusted_passed"] = template_adjusted_passed
        row["strict_template_passed"] = strict_template_passed
        row["model_quality_passed"] = model_quality_passed
        row["coverage_adjusted_score"] = row["average_score"] if strict_template_passed else 0.0
        row["passed"] = bool(model_quality_passed and coverage_passed)
        if row["passed"]:
            row["status"] = "ok_model"
        elif strict_template_passed:
            row["status"] = "template_ready"
        elif question_gate_passed and coverage_complete and contextual_detail_source:
            row["status"] = "template_ready_contextual_detail"
        elif question_gate_passed and coverage_complete and unit_name_details and explicit_detail_source:
            row["status"] = "template_ready_unit_name_resolved"
        elif question_gate_passed and not coverage_complete:
            row["status"] = "template_ready_partial_detail_coverage"
        else:
            row["status"] = "needs_review"

        for item in report.get("items") or []:
            if not isinstance(item, dict):
                continue
            detail = str(item.get("ncs_detail") or "").strip()
            q_index = int(item.get("index") or 0)
            question = ""
            question_source = ""
            canonical_detail = ""
            model_question_raw = ""
            model_question_preserved: Any = ""
            model_replacement_reasons: list[str] = []
            follow_ups: list[str] = []
            evaluation_points: list[str] = []
            ksa_refs: list[str] = []
            ksa_evidence: list[dict[str, Any]] = []
            questions = strategy.get("interview_questions") if isinstance(strategy.get("interview_questions"), list) else []
            if 1 <= q_index <= len(questions) and isinstance(questions[q_index - 1], dict):
                q_obj = questions[q_index - 1]
                question = str(q_obj.get("question") or "").strip()
                question_source = str(q_obj.get("question_source") or "").strip()
                canonical_detail = str(q_obj.get("ncsSubdCdnm") or "").strip()
                model_question_raw = str(q_obj.get("model_question_raw") or "").strip()
                model_question_preserved = q_obj.get("model_question_preserved", "")
                model_replacement_reasons = [
                    str(x).strip()
                    for x in (q_obj.get("model_replacement_reasons") or [])
                    if str(x).strip()
                ] if isinstance(q_obj.get("model_replacement_reasons"), list) else []
                follow_ups = [str(x).strip() for x in (q_obj.get("follow_ups") or []) if str(x).strip()] if isinstance(q_obj.get("follow_ups"), list) else []
                evaluation_points = [str(x).strip() for x in (q_obj.get("evaluation_points") or []) if str(x).strip()] if isinstance(q_obj.get("evaluation_points"), list) else []
                ksa_refs = [str(x).strip() for x in (q_obj.get("ksa_refs") or []) if str(x).strip()] if isinstance(q_obj.get("ksa_refs"), list) else []
                ksa_evidence = [x for x in (q_obj.get("ksa_evidence") or []) if isinstance(x, dict)] if isinstance(q_obj.get("ksa_evidence"), list) else []
            question_rows.append(
                {
                    "idx": idx,
                    "attachment": path.name,
                    "detail": detail,
                    "canonical_detail": canonical_detail,
                    "member": detail_members.get(re.sub(r"[\s·‧･ㆍ•∙⋅・\-\_/|(),.]+", "", detail).lower(), ""),
                    "question_index": item.get("index", ""),
                    "type": item.get("type", ""),
                    "competency": item.get("competency", ""),
                    "ncsClCd": item.get("ncsClCd", ""),
                    "question_source": question_source,
                    "model_question_raw": model_question_raw[:300],
                    "model_question_preserved": model_question_preserved,
                    "model_replacement_reasons": " | ".join(model_replacement_reasons),
                    "question": question[:300],
                    "follow_ups": " | ".join(follow_ups),
                    "evaluation_points": " | ".join(evaluation_points),
                    "ksa_refs": " | ".join(ksa_refs),
                    "ksa_evidence_count": len(ksa_evidence),
                    "score": item.get("score", ""),
                    "ready": item.get("ready", ""),
                    "issues": "; ".join(item.get("issues") or []),
                }
            )
        return row, question_rows
    except (KordocParseError, NcsMcpError, OSError, RuntimeError, ValueError) as exc:
        row["status"] = "error"
        row["error"] = str(exc)[:500]
        return row, question_rows


def write_quality_reports(rows: list[dict[str, Any]], question_rows: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = report_dir / f"alio_question_quality_{stamp}.md"
    csv_path = report_dir / f"alio_question_quality_{stamp}.csv"
    item_csv_path = report_dir / f"alio_question_quality_items_{stamp}.csv"

    fields = [
        "idx",
        "attachment",
        "status",
        "benchmark_mode",
        "resolved_benchmark_mode",
        "strategy_generation_policy",
        "strategy_error",
        "strategy_warning",
        "detail_count",
        "detail_source",
        "exact_detail_count",
        "unit_name_detail_count",
        "unmatched_detail_count",
        "skipped_detail_count",
        "uncovered_detail_count",
        "generated_questions",
        "ready_questions",
        "needs_review_questions",
        "model_candidate_questions",
        "model_questions",
        "model_ready_questions",
        "model_replaced_by_template_questions",
        "template_inserted_questions",
        "template_fallback_questions",
        "template_fallback_ready_questions",
        "average_score",
        "coverage_adjusted_score",
        "coverage_passed",
        "template_adjusted_passed",
        "strict_template_passed",
        "model_quality_passed",
        "passed",
        "details",
        "exact_details",
        "unit_name_details",
        "unmatched_details",
        "skipped_details",
        "manual_review_suggestions",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    item_fields = [
        "idx",
        "attachment",
        "detail",
        "canonical_detail",
        "member",
        "question_index",
        "type",
        "competency",
        "ncsClCd",
        "question_source",
        "model_question_raw",
        "model_question_preserved",
        "model_replacement_reasons",
        "question",
        "follow_ups",
        "evaluation_points",
        "ksa_refs",
        "ksa_evidence_count",
        "score",
        "ready",
        "issues",
    ]
    with item_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=item_fields)
        writer.writeheader()
        for row in question_rows:
            writer.writerow({field: row.get(field, "") for field in item_fields})

    evaluated_statuses = {
        "ok_model",
        "template_ready",
        "template_ready_unit_name_resolved",
        "template_ready_contextual_detail",
        "template_ready_partial_detail_coverage",
        "needs_review",
        # Legacy names retained so older report rows can still be summarized if passed in tests/tools.
        "ok",
        "ok_unit_name_resolved",
        "ok_contextual_detail",
        "partial_detail_coverage",
    }
    evaluated = sum(1 for row in rows if row.get("status") in evaluated_statuses)
    model_quality_passed = sum(1 for row in rows if row.get("model_quality_passed"))
    model_full_passed = sum(1 for row in rows if row.get("passed"))
    strict_template_passed = sum(1 for row in rows if row.get("strict_template_passed"))
    coverage_passed = sum(1 for row in rows if row.get("coverage_passed"))
    unit_name_resolved_ready = sum(
        1
        for row in rows
        if row.get("status") in {"template_ready_unit_name_resolved", "ok_unit_name_resolved"}
    )
    contextual_detail_ready = sum(
        1
        for row in rows
        if row.get("status") in {"template_ready_contextual_detail", "ok_contextual_detail"}
    )
    manual_suggestion_rows = sum(1 for row in rows if str(row.get("manual_review_suggestions") or "").strip())
    explicit_detail_rows = sum(1 for row in rows if row.get("detail_source") == "explicit")
    contextual_detail_rows = sum(1 for row in rows if row.get("detail_source") == "contextual")
    resolved_mode_counts: dict[str, int] = {}
    for row in rows:
        mode = str(row.get("resolved_benchmark_mode") or "unknown").strip() or "unknown"
        resolved_mode_counts[mode] = resolved_mode_counts.get(mode, 0) + 1
    total_questions = sum(int(row.get("generated_questions") or 0) for row in rows)
    ready_questions = sum(int(row.get("ready_questions") or 0) for row in rows)
    model_candidate_questions = sum(int(row.get("model_candidate_questions") or 0) for row in rows)
    model_questions = sum(int(row.get("model_questions") or 0) for row in rows)
    model_ready_questions = sum(int(row.get("model_ready_questions") or 0) for row in rows)
    model_replaced_questions = sum(int(row.get("model_replaced_by_template_questions") or 0) for row in rows)
    template_inserted_questions = sum(int(row.get("template_inserted_questions") or 0) for row in rows)
    fallback_questions = sum(int(row.get("template_fallback_questions") or 0) for row in rows)
    fallback_ready_questions = sum(int(row.get("template_fallback_ready_questions") or 0) for row in rows)
    method_stats: dict[str, dict[str, int]] = {}
    question_source_counts: dict[str, int] = {}
    replacement_reason_counts: dict[str, int] = {}
    for item in question_rows:
        method = str(item.get("type") or "unknown").strip() or "unknown"
        stats = method_stats.setdefault(method, {"total": 0, "ready": 0, "official_sample_format_fail": 0})
        stats["total"] += 1
        ready_value = item.get("ready")
        if ready_value is True or str(ready_value).lower() == "true":
            stats["ready"] += 1
        if "official_sample_format" in str(item.get("issues") or ""):
            stats["official_sample_format_fail"] += 1
        source = str(item.get("question_source") or "unknown").strip() or "unknown"
        question_source_counts[source] = question_source_counts.get(source, 0) + 1
        for reason in str(item.get("model_replacement_reasons") or "").split("|"):
            reason = reason.strip()
            if reason:
                replacement_reason_counts[reason] = replacement_reason_counts.get(reason, 0) + 1
    template_adjusted_avg = round(
        sum(float(row.get("average_score") or 0.0) for row in rows if row.get("status") in evaluated_statuses)
        / max(1, evaluated),
        2,
    )
    coverage_adjusted_avg = round(
        sum(float(row.get("coverage_adjusted_score") or 0.0) for row in rows if row.get("status") in evaluated_statuses)
        / max(1, evaluated),
        2,
    )
    lines = [
        f"# ALIO Question Quality - {stamp}",
        "",
        f"- Documents attempted: {len(rows)}",
        f"- Documents evaluated: {evaluated}",
        f"- Resolved benchmark modes: {', '.join(f'{key}={value}' for key, value in sorted(resolved_mode_counts.items()))}",
        f"- Documents strict source-explicit coverage + template-ready: {strict_template_passed}",
        f"- Documents passed model-origin quality gate: {model_quality_passed}",
        f"- Documents passed model-origin quality + strict coverage: {model_full_passed}",
        f"- Documents with strict source-explicit detail coverage: {coverage_passed}",
        f"- Documents question-ready with unit-name recovery: {unit_name_resolved_ready}",
        f"- Documents question-ready with contextual detail recovery: {contextual_detail_ready}",
        f"- Unit-name resolved detail labels: {sum(int(row.get('unit_name_detail_count') or 0) for row in rows)}",
        f"- Explicit detail-source documents: {explicit_detail_rows}",
        f"- Contextual detail-source documents: {contextual_detail_rows}",
        f"- Unmatched detail labels: {sum(int(row.get('unmatched_detail_count') or 0) for row in rows)}",
        f"- Skipped detail labels due to per-doc limit: {sum(int(row.get('skipped_detail_count') or 0) for row in rows)}",
        f"- Documents with manual-review suggestions: {manual_suggestion_rows}",
        f"- Evaluated questions after adjustment: {total_questions}",
        f"- Template-adjusted ready questions: {ready_questions}",
        f"- Model candidate questions received: {model_candidate_questions}",
        f"- Model-origin questions evaluated: {model_questions}",
        f"- Model-origin ready questions: {model_ready_questions}",
        f"- Model questions replaced by template: {model_replaced_questions}",
        f"- Template questions inserted without model candidate: {template_inserted_questions}",
        f"- Template fallback questions: {fallback_questions}",
        f"- Template fallback ready questions: {fallback_ready_questions}",
        f"- Average template-adjusted document score: {template_adjusted_avg}",
        f"- Average strict coverage-adjusted score: {coverage_adjusted_avg}",
        "",
        "> This report distinguishes template-fallback compliance from model-origin generation quality. "
        "If `Model-origin questions evaluated` is 0, method readiness below measures deterministic fallback templates, not LLM output.",
        "",
        "| idx | status | model pass | full pass | template pass | mode | source | details | exact | unit-name | unmatched | skipped | adjusted q | ready | model cand | model kept | model repl | inserted | fallback q | tpl score | strict score | unresolved details |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        unmatched = str(row.get("unmatched_details") or "").replace("|", "/")
        skipped = str(row.get("skipped_details") or "").replace("|", "/")
        unresolved = "; ".join(part for part in (unmatched, skipped) if part)
        lines.append(
            f"| {row.get('idx')} | {row.get('status')} | "
            f"{row.get('model_quality_passed') is True} | {row.get('passed') is True} | "
            f"{row.get('strict_template_passed') is True} | "
            f"{row.get('resolved_benchmark_mode') or ''} | "
            f"{row.get('detail_source') or ''} | "
            f"{row.get('detail_count') or 0} | "
            f"{row.get('exact_detail_count') or 0} | {row.get('unit_name_detail_count') or 0} | "
            f"{row.get('unmatched_detail_count') or 0} | {row.get('skipped_detail_count') or 0} | "
            f"{row.get('generated_questions') or 0} | "
            f"{row.get('ready_questions') or 0} | {row.get('model_candidate_questions') or 0} | "
            f"{row.get('model_questions') or 0} | {row.get('model_replaced_by_template_questions') or 0} | "
            f"{row.get('template_inserted_questions') or 0} | {row.get('template_fallback_questions') or 0} | "
            f"{row.get('average_score') or 0} | "
            f"{row.get('coverage_adjusted_score') or 0} | {unresolved} |"
        )
    suggestion_rows = [row for row in rows if str(row.get("manual_review_suggestions") or "").strip()]
    if method_stats:
        lines.extend(["", "## Method Quality", ""])
        lines.append("| method | questions | ready | ready rate | model | fallback | official sample format fails |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for method, stats in sorted(method_stats.items()):
            total = int(stats.get("total") or 0)
            ready = int(stats.get("ready") or 0)
            rate = round(ready / max(1, total), 2)
            model_total = sum(
                1
                for item in question_rows
                if str(item.get("type") or "").strip() == method
                and str(item.get("question_source") or "").strip() == "model"
            )
            fallback_total = sum(
                1
                for item in question_rows
                if str(item.get("type") or "").strip() == method
                and str(item.get("question_source") or "").strip() == "template_fallback"
            )
            lines.append(
                f"| {method} | {total} | {ready} | {rate} | {model_total} | {fallback_total} | "
                f"{stats.get('official_sample_format_fail') or 0} |"
            )
    if question_source_counts:
        lines.extend(["", "## Question Source", ""])
        lines.append("| source | questions |")
        lines.append("| --- | ---: |")
        for source, count in sorted(question_source_counts.items()):
            lines.append(f"| {source} | {count} |")
    if replacement_reason_counts:
        lines.extend(["", "## Model Fallback Reasons", ""])
        lines.append("| reason | questions |")
        lines.append("| --- | ---: |")
        for reason, count in sorted(replacement_reason_counts.items()):
            lines.append(f"| {reason} | {count} |")
    if suggestion_rows:
        lines.extend(["", "## Manual Review Suggestions", ""])
        for row in suggestion_rows:
            suggestions = str(row.get("manual_review_suggestions") or "").replace("|", "/")
            lines.append(f"- `{row.get('idx')}`: {suggestions}")
    lines.extend(["", f"CSV: `{csv_path}`", f"Question CSV: `{item_csv_path}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path, item_csv_path


def _metric_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except Exception:
        return 0


def quality_gate_failures(
    rows: list[dict[str, Any]],
    *,
    min_model_ready_rate: float | None = None,
    fail_on_model_replacements: bool = False,
    fail_on_template_insertions: bool = False,
) -> list[str]:
    model_candidate_questions = sum(_metric_int(row, "model_candidate_questions") for row in rows)
    model_ready_questions = sum(_metric_int(row, "model_ready_questions") for row in rows)
    model_replacements = sum(_metric_int(row, "model_replaced_by_template_questions") for row in rows)
    template_insertions = sum(_metric_int(row, "template_inserted_questions") for row in rows)

    failures: list[str] = []
    if min_model_ready_rate is not None:
        threshold = float(min_model_ready_rate)
        if model_candidate_questions <= 0:
            if threshold > 0:
                failures.append(f"no_model_candidate_questions for min_model_ready_rate {threshold:.2f}")
        else:
            rate = model_ready_questions / model_candidate_questions
            if rate < threshold:
                failures.append(
                    f"model_ready_rate {rate:.2f} below minimum {threshold:.2f} "
                    f"({model_ready_questions}/{model_candidate_questions} model candidate questions ready)"
                )
    if fail_on_model_replacements and model_replacements > 0:
        failures.append(f"model_replacements {model_replacements} > 0")
    if fail_on_template_insertions and template_insertions > 0:
        failures.append(f"template_insertions {template_insertions} > 0")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate generated question quality from cached ALIO JD attachments.")
    parser.add_argument("--cache-dir", default=".tmp/alio_jd_benchmark")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-download-mb", type=int, default=20)
    parser.add_argument("--questions-per-doc", type=int, default=6)
    parser.add_argument("--follow-up-count", type=int, default=3)
    parser.add_argument("--max-details-per-doc", type=int, default=20)
    parser.add_argument("--max-units-per-detail", type=int, default=12)
    parser.add_argument("--ksa-units", type=int, default=4)
    parser.add_argument("--ksa-factors-per-unit", type=int, default=6)
    parser.add_argument("--benchmark-mode", choices=sorted(BENCHMARK_MODES), default="template")
    parser.add_argument("--openai-api-key", default="")
    parser.add_argument("--min-model-ready-rate", type=float, default=None)
    parser.add_argument("--fail-on-model-replacements", action="store_true")
    parser.add_argument("--fail-on-template-insertions", action="store_true")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.exists():
        raise SystemExit(f"cache dir not found: {cache_dir}")
    openai_api_key = str(args.openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    if _normalize_benchmark_mode(args.benchmark_mode) == "model" and not openai_api_key:
        raise SystemExit("--benchmark-mode model requires --openai-api-key or OPENAI_API_KEY")
    if args.min_model_ready_rate is not None and not 0 <= float(args.min_model_ready_rate) <= 1:
        raise SystemExit("--min-model-ready-rate must be between 0 and 1")
    max_bytes = max(1, int(args.max_download_mb)) * 1024 * 1024
    rows: list[dict[str, Any]] = []
    question_rows: list[dict[str, Any]] = []
    for path in iter_cached_attachments(cache_dir, int(args.limit)):
        row, items = evaluate_cached_document(
            path=path,
            max_bytes=max_bytes,
            questions_per_doc=max(1, int(args.questions_per_doc)),
            follow_up_count=max(0, min(5, int(args.follow_up_count))),
            max_details_per_doc=max(1, int(args.max_details_per_doc)),
            max_units_per_detail=max(1, int(args.max_units_per_detail)),
            ksa_units=max(1, int(args.ksa_units)),
            ksa_factors_per_unit=max(1, int(args.ksa_factors_per_unit)),
            benchmark_mode=args.benchmark_mode,
            openai_api_key=openai_api_key,
        )
        rows.append(row)
        question_rows.extend(items)
        time.sleep(0.1)
    md_path, csv_path, item_csv_path = write_quality_reports(rows, question_rows, Path(args.report_dir))
    print(f"report={md_path}")
    print(f"csv={csv_path}")
    print(f"item_csv={item_csv_path}")
    print(f"rows={len(rows)}")
    failures = quality_gate_failures(
        rows,
        min_model_ready_rate=args.min_model_ready_rate,
        fail_on_model_replacements=bool(args.fail_on_model_replacements),
        fail_on_template_insertions=bool(args.fail_on_template_insertions),
    )
    for failure in failures:
        print(f"quality_gate_failure={failure}", file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
