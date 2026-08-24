from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import (  # noqa: E402
    _lock_units_to_reviewed_ability_units,
    _recover_code_scoped_reviewed_ability_units,
    _recover_ordinal_scoped_reviewed_ability_units,
    _reviewed_ability_unit_names,
    _reviewed_ability_unit_ordinals,
    _scope_reviewed_ability_units_by_exact_detail_membership,
)
from app.services.kordoc_parser import KordocParseError, structure_job_description  # noqa: E402
from app.services.ncs_mcp_client import (  # noqa: E402
    NcsMcpError,
    classify_official_ability_unit_names,
    classify_official_detail_names,
    derive_detail_candidates_from_exact_ability_scopes,
    exact_official_units_by_name,
    get_ksa_by_units,
    search_units_by_detail,
    suggest_units_by_text,
    use_ncs_mcp_request_session,
)
from benchmark_alio_jd import parse_benchmark_document  # noqa: E402


SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".zip"}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _pct(numerator: int, denominator: int) -> float:
    return round((100.0 * numerator / denominator), 2) if denominator else 0.0


def _unique_strings(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        key = _norm(text)
        if text and key and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _ability_candidate_key_sets(
    ability_states: list[dict[str, Any]],
    convergence_suggestions: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Build deduplicated scope/code candidate sets from exact evidence only."""

    def state_keys(*states: str) -> set[str]:
        accepted = set(states)
        return {
            _norm(state.get("sourceName"))
            for state in ability_states
            if state.get("mappingState") in accepted
            and _norm(state.get("sourceName"))
        }

    catalog_exact = {
        _norm(state.get("sourceName"))
        for state in ability_states
        if (
            state.get("catalogExact")
            or str(state.get("mappingState") or "").startswith(
                "official_exact_"
            )
        )
        and _norm(state.get("sourceName"))
    }
    source_scoped = state_keys("official_exact_source_scoped")
    derived_scope = state_keys("official_exact_derived_scope_review_required")
    scope_conflict = state_keys("official_exact_scope_conflict")
    ambiguous = state_keys(
        "official_exact_detail_ambiguous",
        "official_exact_code_ambiguous",
    )
    unmapped = state_keys("not_in_current_official_catalog")
    convergence_evidence = {
        _norm(evidence.get("sourceAbilityUnitName"))
        for suggestion in convergence_suggestions
        for evidence in (suggestion.get("evidence") or [])
        if isinstance(evidence, dict)
        and _norm(evidence.get("sourceAbilityUnitName"))
    }
    # Convergence is safe only when its evidence is also one of this row's
    # exact official ability labels. Source-scoped labels are already counted.
    safe_convergence = (convergence_evidence & catalog_exact) - source_scoped
    scope_candidates = source_scoped | derived_scope | scope_conflict
    code_candidates = source_scoped | safe_convergence
    return {
        "catalog_exact": catalog_exact,
        "unmapped": unmapped,
        "source_scoped": source_scoped,
        "derived_scope": derived_scope,
        "scope_conflict": scope_conflict,
        "ambiguous": ambiguous,
        "safe_convergence": safe_convergence,
        "scope_candidates": scope_candidates,
        "code_candidates": code_candidates,
    }


def _row_label(row: dict[str, Any]) -> str:
    return str(
        row.get("filename") or row.get("sha256") or "unknown-row"
    ).strip()


def _parse_json_list(
    row: dict[str, Any],
    field: str,
) -> tuple[list[Any] | None, str | None]:
    raw = row.get(field)
    if isinstance(raw, list):
        return raw, None
    if raw is None or raw == "":
        return None, f"{_row_label(row)}: missing {field}"
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, f"{_row_label(row)}: invalid {field} JSON"
    if not isinstance(parsed, list):
        return None, f"{_row_label(row)}: {field} is not a JSON list"
    return parsed, None


def _persisted_candidate_count(
    row: dict[str, Any],
    field: str,
) -> tuple[int | None, str | None]:
    raw = row.get(field)
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, bool):
        return None, f"{_row_label(row)}: invalid {field}"
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{_row_label(row)}: invalid {field}"
    if value < 0:
        return None, f"{_row_label(row)}: negative {field}"
    return value, None


def _row_candidate_counts(
    row: dict[str, Any],
) -> tuple[int, int, list[str]]:
    """Resolve candidate counts without adding overlapping state counts."""

    reasons: list[str] = []
    ability_units = int(row.get("ability_unit_count") or 0)
    catalog_exact_count = int(row.get("ability_catalog_exact_count") or 0)
    scope_count, scope_error = _persisted_candidate_count(
        row, "ability_official_scope_candidate_count"
    )
    code_count, code_error = _persisted_candidate_count(
        row, "ability_official_code_candidate_count"
    )
    reasons.extend(error for error in (scope_error, code_error) if error)

    if ability_units == 0:
        scope_count = 0 if scope_count is None else scope_count
        code_count = 0 if code_count is None else code_count
    elif (scope_count is None and not scope_error) or (
        code_count is None and not code_error
    ):
        states, states_error = _parse_json_list(row, "ability_mapping_states")
        typed_states: list[dict[str, Any]] = []
        if states_error:
            reasons.append(states_error)
        elif any(not isinstance(state, dict) for state in states or []):
            reasons.append(
                f"{_row_label(row)}: ability_mapping_states contains a non-object"
            )
        else:
            typed_states = [state for state in states or [] if isinstance(state, dict)]
            if not typed_states:
                reasons.append(
                    f"{_row_label(row)}: ability_mapping_states is empty for "
                    "an ability row"
                )
            elif any(
                not str(state.get("mappingState") or "").strip()
                or not _norm(state.get("sourceName"))
                for state in typed_states
            ):
                reasons.append(
                    f"{_row_label(row)}: ability mapping state missing state or "
                    "sourceName"
                )
            elif scope_count is None and not scope_error:
                scope_count = len(
                    _ability_candidate_key_sets(typed_states, [])["scope_candidates"]
                )

        if code_count is None and not code_error and typed_states:
            convergence, convergence_error = _parse_json_list(
                row, "detail_convergence_suggestions"
            )
            if convergence_error:
                reasons.append(convergence_error)
            elif any(
                not isinstance(suggestion, dict)
                for suggestion in convergence or []
            ):
                reasons.append(
                    f"{_row_label(row)}: detail_convergence_suggestions "
                    "contains a non-object"
                )
            elif any(
                not isinstance(suggestion.get("evidence"), list)
                or any(
                    not isinstance(item, dict)
                    for item in suggestion.get("evidence") or []
                )
                for suggestion in convergence or []
                if isinstance(suggestion, dict)
            ):
                reasons.append(
                    f"{_row_label(row)}: invalid convergence evidence"
                )
            else:
                code_count = len(
                    _ability_candidate_key_sets(
                        typed_states,
                        [
                            suggestion
                            for suggestion in convergence or []
                            if isinstance(suggestion, dict)
                        ],
                    )["code_candidates"]
                )

    scope_count = 0 if scope_count is None else scope_count
    code_count = 0 if code_count is None else code_count
    for field, value in (
        ("ability_official_scope_candidate_count", scope_count),
        ("ability_official_code_candidate_count", code_count),
    ):
        if value > catalog_exact_count:
            reasons.append(
                f"{_row_label(row)}: {field} exceeds "
                "ability_catalog_exact_count"
            )
    return scope_count, code_count, list(dict.fromkeys(reasons))


def _scoped_ability_units(fields: dict[str, Any], details: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    raw_mapping = fields.get("ability_units_by_detail")
    raw_mapping = raw_mapping if isinstance(raw_mapping, dict) else {}
    candidates_by_unit: dict[str, dict[str, Any]] = {}
    detail_by_key = {_norm(detail): detail for detail in details if _norm(detail)}
    for raw_detail, raw_units in raw_mapping.items():
        detail = detail_by_key.get(_norm(raw_detail))
        if not detail:
            continue
        units = _reviewed_ability_unit_names(raw_units)
        for unit in units:
            key = _norm(unit)
            if not key:
                continue
            entry = candidates_by_unit.setdefault(
                key,
                {"name": unit, "details": set()},
            )
            entry["details"].add(detail)

    all_units = _reviewed_ability_unit_names(fields.get("ability_units") or [])
    if len(details) == 1 and not candidates_by_unit and all_units:
        return {details[0]: all_units}, []

    mapping: dict[str, list[str]] = {}
    ambiguous: list[str] = []
    for unit in all_units:
        entry = candidates_by_unit.get(_norm(unit))
        scopes = entry.get("details") if entry else set()
        if not isinstance(scopes, set) or len(scopes) != 1:
            ambiguous.append(unit)
            continue
        detail = next(iter(scopes))
        mapping.setdefault(detail, []).append(unit)
    return mapping, ambiguous


def _parse_file(path: Path, max_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise RuntimeError(f"file exceeds limit: {len(data)} > {max_bytes}")
    # Match the production upload path: image-only PDF pages must use Kordoc's
    # built-in OCR instead of being counted as a successful empty parse.
    parsed = parse_benchmark_document(
        data,
        filename=path.name,
        max_bytes=max_bytes,
        ocr=True,
    )
    structured = structure_job_description(parsed, filename=path.name)
    return parsed, structured


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "seq",
        "filename",
        "sha256",
        "duplicate_content",
        "suffix",
        "bytes",
        "parse_ms",
        "status",
        "parser",
        "markdown_chars",
        "archive_members",
        "detail_count",
        "details",
        "official_validation_detail_count",
        "self_developed_detail_count",
        "self_developed_details",
        "detail_exact_count",
        "detail_unmatched_count",
        "detail_unmatched",
        "detail_catalog_exact_count",
        "detail_catalog_unmapped_count",
        "detail_catalog_ambiguous_count",
        "detail_mapping_states",
        "ability_unit_count",
        "ability_units",
        "ability_scoped_count",
        "ability_exact_count",
        "ability_unmatched_count",
        "ability_unmatched",
        "ability_ambiguous_count",
        "ability_ambiguous",
        "ability_catalog_exact_count",
        "ability_catalog_unmapped_count",
        "ability_official_source_scoped_count",
        "ability_official_derived_scope_count",
        "ability_official_converged_scope_count",
        "ability_official_scope_conflict_count",
        "ability_official_ambiguous_count",
        "ability_official_scope_candidate_count",
        "ability_official_code_candidate_count",
        "ability_mapping_states",
        "ability_source_scope_mapping",
        "detail_convergence_suggestion_count",
        "detail_convergence_suggestions",
        "positioned_item_count",
        "coordinate_ability_count",
        "ncs_table_seen",
        "absence_reason",
        "declared_no_mapping",
        "ksa_probe_codes",
        "ksa_available_codes",
        "pipeline_ready",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def _summary(rows: list[dict[str, Any]], unique_hashes: int) -> dict[str, Any]:
    total = len(rows)
    parsed = [row for row in rows if row.get("status") != "parse_error"]
    detail_docs = [row for row in parsed if int(row.get("detail_count") or 0) > 0]
    detail_total = sum(int(row.get("detail_count") or 0) for row in detail_docs)
    official_detail_total = sum(
        int(row.get("official_validation_detail_count") or 0)
        for row in detail_docs
    )
    self_developed_detail_total = sum(
        int(row.get("self_developed_detail_count") or 0)
        for row in detail_docs
    )
    detail_exact = sum(int(row.get("detail_exact_count") or 0) for row in detail_docs)
    detail_catalog_exact = sum(
        int(row.get("detail_catalog_exact_count") or 0) for row in detail_docs
    )
    detail_catalog_unmapped = sum(
        int(row.get("detail_catalog_unmapped_count") or 0) for row in detail_docs
    )
    detail_catalog_ambiguous = sum(
        int(row.get("detail_catalog_ambiguous_count") or 0) for row in detail_docs
    )
    all_detail_exact_docs = [
        row
        for row in detail_docs
        if int(row.get("detail_exact_count") or 0) == int(row.get("detail_count") or 0)
    ]
    official_detail_docs = [
        row
        for row in detail_docs
        if int(row.get("official_validation_detail_count") or 0) > 0
    ]
    all_official_detail_exact_docs = [
        row
        for row in official_detail_docs
        if int(row.get("detail_exact_count") or 0)
        == int(row.get("official_validation_detail_count") or 0)
    ]
    current_official_detail_docs = [
        row
        for row in detail_docs
        if int(row.get("detail_catalog_exact_count") or 0)
        + int(row.get("detail_catalog_ambiguous_count") or 0)
        > 0
    ]
    all_current_official_detail_exact_docs = [
        row
        for row in current_official_detail_docs
        if int(row.get("detail_catalog_ambiguous_count") or 0) == 0
    ]
    detail_classified = (
        detail_catalog_exact
        + detail_catalog_unmapped
        + detail_catalog_ambiguous
        + self_developed_detail_total
    )
    ability_docs = [row for row in parsed if int(row.get("ability_unit_count") or 0) > 0]
    ability_total = sum(int(row.get("ability_unit_count") or 0) for row in ability_docs)
    ability_scoped = sum(int(row.get("ability_scoped_count") or 0) for row in ability_docs)
    ability_exact = sum(int(row.get("ability_exact_count") or 0) for row in ability_docs)
    ability_unmatched = sum(int(row.get("ability_unmatched_count") or 0) for row in ability_docs)
    ability_ambiguous = sum(int(row.get("ability_ambiguous_count") or 0) for row in ability_docs)
    ability_catalog_exact = sum(
        int(row.get("ability_catalog_exact_count") or 0) for row in ability_docs
    )
    ability_catalog_unmapped = sum(
        int(row.get("ability_catalog_unmapped_count") or 0) for row in ability_docs
    )
    ability_official_source_scoped = sum(
        int(row.get("ability_official_source_scoped_count") or 0)
        for row in ability_docs
    )
    ability_official_derived_scope = sum(
        int(row.get("ability_official_derived_scope_count") or 0)
        for row in ability_docs
    )
    ability_official_converged_scope = sum(
        int(row.get("ability_official_converged_scope_count") or 0)
        for row in ability_docs
    )
    ability_official_scope_conflict = sum(
        int(row.get("ability_official_scope_conflict_count") or 0)
        for row in ability_docs
    )
    ability_official_ambiguous = sum(
        int(row.get("ability_official_ambiguous_count") or 0)
        for row in ability_docs
    )
    candidate_rows = [_row_candidate_counts(row) for row in ability_docs]
    ability_official_scope_candidates = sum(row[0] for row in candidate_rows)
    ability_official_code_candidates = sum(row[1] for row in candidate_rows)
    metric_invalid_reasons = list(
        dict.fromkeys(reason for row in candidate_rows for reason in row[2])
    )
    for row in ability_docs:
        if (
            int(row.get("ability_catalog_exact_count") or 0)
            + int(row.get("ability_catalog_unmapped_count") or 0)
            != int(row.get("ability_unit_count") or 0)
        ):
            metric_invalid_reasons.append(
                f"{_row_label(row)}: ability mapping-state coverage is not exact"
            )
    metric_invalid_reasons = list(dict.fromkeys(metric_invalid_reasons))
    detail_convergence_suggestions = sum(
        int(row.get("detail_convergence_suggestion_count") or 0)
        for row in parsed
    )
    ksa_probe_codes = {
        code
        for row in rows
        for code in str(row.get("ksa_probe_codes") or "").split(";")
        if code.strip()
    }
    ksa_available_codes = {
        code
        for row in rows
        for code in str(row.get("ksa_available_codes") or "").split(";")
        if code.strip()
    }
    ready = [row for row in rows if bool(row.get("pipeline_ready"))]
    declared_no_mapping = [row for row in parsed if bool(row.get("declared_no_mapping"))]
    no_explicit_detail = [
        row
        for row in parsed
        if not int(row.get("detail_count") or 0) and not bool(row.get("declared_no_mapping"))
    ]
    return {
        "metric_validity": {
            "valid": not metric_invalid_reasons,
            "invalid_reasons": metric_invalid_reasons,
        },
        "files": total,
        "unique_contents": unique_hashes,
        "duplicate_files": total - unique_hashes,
        "parse_success": len(parsed),
        "parse_success_pct": _pct(len(parsed), total),
        "parse_errors": total - len(parsed),
        "documents_with_detail": len(detail_docs),
        "documents_with_official_validation_detail": len(official_detail_docs),
        "documents_all_details_exact": len(all_detail_exact_docs),
        "documents_all_details_exact_pct": _pct(len(all_detail_exact_docs), len(detail_docs)),
        "detail_candidates": detail_total,
        "official_validation_detail_candidates": official_detail_total,
        "source_declared_self_developed_detail_candidates": self_developed_detail_total,
        "detail_exact": detail_exact,
        "detail_exact_pct": _pct(detail_exact, detail_total),
        "detail_catalog_exact_candidates": detail_catalog_exact,
        "detail_catalog_unmapped_candidates": detail_catalog_unmapped,
        "detail_catalog_ambiguous_candidates": detail_catalog_ambiguous,
        "current_official_detail_recognition_pct": _pct(
            detail_catalog_exact, detail_catalog_exact + detail_catalog_ambiguous
        ),
        "detail_mapping_state_coverage_pct": _pct(
            detail_classified, detail_total
        ),
        "documents_with_current_official_detail": len(
            current_official_detail_docs
        ),
        "documents_all_current_official_details_exact": len(
            all_current_official_detail_exact_docs
        ),
        "documents_all_current_official_details_exact_pct": _pct(
            len(all_current_official_detail_exact_docs),
            len(current_official_detail_docs),
        ),
        "official_validation_detail_exact_pct": _pct(
            detail_exact, official_detail_total
        ),
        "documents_all_official_details_exact": len(all_official_detail_exact_docs),
        "documents_all_official_details_exact_pct": _pct(
            len(all_official_detail_exact_docs), len(official_detail_docs)
        ),
        "documents_with_ability_units": len(ability_docs),
        "ability_units": ability_total,
        "ability_units_scoped": ability_scoped,
        "ability_scoped_pct": _pct(ability_scoped, ability_total),
        "ability_units_exact": ability_exact,
        "ability_exact_of_scoped_pct": _pct(ability_exact, ability_scoped),
        "ability_end_to_end_pct": _pct(ability_exact, ability_total),
        "ability_units_unmatched": ability_unmatched,
        "ability_units_ambiguous": ability_ambiguous,
        "ability_catalog_exact_candidates": ability_catalog_exact,
        "ability_catalog_unmapped_candidates": ability_catalog_unmapped,
        "ability_mapping_state_coverage_pct": _pct(
            ability_catalog_exact + ability_catalog_unmapped,
            ability_total,
        ),
        "ability_official_source_scoped": ability_official_source_scoped,
        "ability_official_derived_scope_review_required": ability_official_derived_scope,
        "ability_official_converged_scope_review_required": ability_official_converged_scope,
        "ability_official_scope_conflicts": ability_official_scope_conflict,
        "ability_official_ambiguous": ability_official_ambiguous,
        "ability_official_scope_candidate_count": ability_official_scope_candidates,
        "ability_official_scope_candidate_pct": _pct(
            ability_official_scope_candidates,
            ability_catalog_exact,
        ),
        "ability_official_code_candidate_count": ability_official_code_candidates,
        "ability_official_code_candidate_pct": _pct(
            ability_official_code_candidates, ability_catalog_exact
        ),
        "ksa_probe_unit_codes": len(ksa_probe_codes),
        "ksa_available_unit_codes": len(ksa_available_codes),
        "ksa_available_pct": _pct(len(ksa_available_codes), len(ksa_probe_codes)),
        "strict_pipeline_ready_documents": len(ready),
        "strict_pipeline_ready_of_detail_docs_pct": _pct(len(ready), len(detail_docs)),
        "declared_no_mapping_documents": len(declared_no_mapping),
        "documents_without_extracted_detail": len(no_explicit_detail),
        "review_required_detail_convergence_suggestions": detail_convergence_suggestions,
        "status_counts": dict(Counter(str(row.get("status") or "") for row in rows)),
        "suffix_counts": dict(Counter(str(row.get("suffix") or "") for row in rows)),
    }


def _write_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    failures = [row for row in rows if not bool(row.get("pipeline_ready"))]
    lines = [
        "# Stored JD corpus benchmark",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## Summary",
        "",
        f"- Files: {summary['files']} ({summary['unique_contents']} unique contents, {summary['duplicate_files']} duplicate files)",
        f"- Parse success: {summary['parse_success']}/{summary['files']} ({summary['parse_success_pct']}%)",
        f"- Documents with extracted detail: {summary['documents_with_detail']}",
        f"- Documents whose extracted details all matched MCP exactly: {summary['documents_all_details_exact']}/{summary['documents_with_detail']} ({summary['documents_all_details_exact_pct']}%)",
        f"- Extracted detail exact match: {summary['detail_exact']}/{summary['detail_candidates']} ({summary['detail_exact_pct']}%)",
        f"- Current official detail labels recognized by the complete catalog: {summary['detail_catalog_exact_candidates']} (unmapped/legacy/custom: {summary['detail_catalog_unmapped_candidates']}, ambiguous current names: {summary['detail_catalog_ambiguous_candidates']})",
        f"- Detail mapping-state coverage: {summary['detail_mapping_state_coverage_pct']}%; documents with every current official detail exact: {summary['documents_all_current_official_details_exact']}/{summary['documents_with_current_official_detail']} ({summary['documents_all_current_official_details_exact_pct']}%)",
        f"- Official-validation detail exact match (self-developed labels separated): {summary['detail_exact']}/{summary['official_validation_detail_candidates']} ({summary['official_validation_detail_exact_pct']}%)",
        f"- Source-declared self-developed detail labels: {summary['source_declared_self_developed_detail_candidates']}",
        f"- Documents whose official-validation details all matched: {summary['documents_all_official_details_exact']}/{summary['documents_with_official_validation_detail']} ({summary['documents_all_official_details_exact_pct']}%)",
        f"- Documents with extracted ability units: {summary['documents_with_ability_units']}",
        f"- Ability-unit scope resolution: {summary['ability_units_scoped']}/{summary['ability_units']} ({summary['ability_scoped_pct']}%)",
        f"- Ability-unit normalized exact match among scoped values: {summary['ability_units_exact']}/{summary['ability_units_scoped']} ({summary['ability_exact_of_scoped_pct']}%)",
        f"- Ability-unit end-to-end exact resolution: {summary['ability_units_exact']}/{summary['ability_units']} ({summary['ability_end_to_end_pct']}%)",
        f"- Ability-unit unmatched: {summary['ability_units_unmatched']}",
        f"- Ability-unit ambiguous/unscoped: {summary['ability_units_ambiguous']}",
        f"- Current official ability labels in full catalog: {summary['ability_catalog_exact_candidates']} (not in current catalog: {summary['ability_catalog_unmapped_candidates']})",
        f"- Ability mapping-state coverage: {summary['ability_mapping_state_coverage_pct']}%",
        f"- Official ability scope attribution: {summary['ability_official_scope_candidate_count']}/{summary['ability_catalog_exact_candidates']} ({summary['ability_official_scope_candidate_pct']}%; source-compatible: {summary['ability_official_source_scoped']}, unique derived/review-required: {summary['ability_official_derived_scope_review_required']}, explicit source/catalog conflicts: {summary['ability_official_scope_conflicts']}, ambiguous: {summary['ability_official_ambiguous']})",
        f"- Official ability code candidates (source-scoped + safe exact-unit convergence): {summary['ability_official_code_candidate_count']}/{summary['ability_catalog_exact_candidates']} ({summary['ability_official_code_candidate_pct']}%)",
        f"- Official KSA availability: {summary['ksa_available_unit_codes']}/{summary['ksa_probe_unit_codes']} unique probed unit codes ({summary['ksa_available_pct']}%)",
        f"- Strict pipeline-ready documents: {summary['strict_pipeline_ready_documents']}/{summary['documents_with_detail']} detail-bearing documents ({summary['strict_pipeline_ready_of_detail_docs_pct']}%)",
        f"- Declared no-NCS-mapping documents: {summary['declared_no_mapping_documents']}",
        f"- Parsed documents without extracted detail (excluding declared no mapping): {summary['documents_without_extracted_detail']}",
        "",
        "Strict pipeline-ready means: parsing succeeded; at least one explicit detail was extracted; every extracted detail matched the official MCP exactly; every extracted ability unit was scoped and matched exactly; and all probed official unit codes returned KSA.",
        "",
        "## Non-ready documents",
        "",
        "| file | status | details | exact | ability units | ability exact | ambiguous | error |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in failures:
        error = str(row.get("error") or row.get("absence_reason") or "").replace("|", "/")[:160]
        lines.append(
            f"| {str(row.get('filename') or '').replace('|', '/')} | {row.get('status')} | "
            f"{row.get('detail_count')} | {row.get('detail_exact_count')} | {row.get('ability_unit_count')} | "
            f"{row.get('ability_exact_count')} | {row.get('ability_ambiguous_count')} | {error} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark every stored real-world JD through the production parser and NCS MCP contract.")
    parser.add_argument("--input-dir", default="tmp/alio_jd_200_mcp")
    parser.add_argument("--report-dir", default="tmp/stored_jd_benchmark")
    parser.add_argument("--max-file-mb", type=int, default=25)
    parser.add_argument("--skip-ksa", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        raise SystemExit(f"no supported files in {input_dir}")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = max(1, int(args.max_file_mb)) * 1024 * 1024

    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    hash_counts = Counter(hashes.values())
    detail_cache: dict[str, list[dict[str, Any]]] = {}
    ability_suggestion_cache: dict[str, list[dict[str, Any]]] = {}
    unit_by_code: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    with use_ncs_mcp_request_session():
        for seq, path in enumerate(files, start=1):
            started = time.perf_counter()
            row: dict[str, Any] = {
                "seq": seq,
                "filename": path.name,
                "sha256": hashes[path],
                "duplicate_content": hash_counts[hashes[path]] > 1,
                "suffix": path.suffix.lower(),
                "bytes": path.stat().st_size,
                "parse_ms": 0,
                "status": "unknown",
                "parser": "",
                "markdown_chars": 0,
                "archive_members": 0,
                "detail_count": 0,
                "details": "",
                "official_validation_detail_count": 0,
                "self_developed_detail_count": 0,
                "self_developed_details": "",
                "detail_exact_count": 0,
                "detail_unmatched_count": 0,
                "detail_unmatched": "",
                "detail_catalog_exact_count": 0,
                "detail_catalog_unmapped_count": 0,
                "detail_catalog_ambiguous_count": 0,
                "detail_mapping_states": "",
                "ability_unit_count": 0,
                "ability_units": "",
                "ability_scoped_count": 0,
                "ability_exact_count": 0,
                "ability_unmatched_count": 0,
                "ability_unmatched": "",
                "ability_ambiguous_count": 0,
                "ability_ambiguous": "",
                "ability_catalog_exact_count": 0,
                "ability_catalog_unmapped_count": 0,
                "ability_official_source_scoped_count": 0,
                "ability_official_derived_scope_count": 0,
                "ability_official_converged_scope_count": 0,
                "ability_official_scope_conflict_count": 0,
                "ability_official_ambiguous_count": 0,
                "ability_official_scope_candidate_count": 0,
                "ability_official_code_candidate_count": 0,
                "ability_mapping_states": "",
                "ability_source_scope_mapping": "",
                "detail_convergence_suggestion_count": 0,
                "detail_convergence_suggestions": "",
                "positioned_item_count": 0,
                "coordinate_ability_count": 0,
                "ncs_table_seen": False,
                "absence_reason": "",
                "declared_no_mapping": False,
                "ksa_probe_codes": "",
                "ksa_available_codes": "",
                "pipeline_ready": False,
                "error": "",
                "_probe_codes": [],
            }
            try:
                parsed, structured = _parse_file(path, max_bytes=max_bytes)
                row["parse_ms"] = int((time.perf_counter() - started) * 1000)
                row["parser"] = str(parsed.get("parser") or (parsed.get("metadata") or {}).get("parser") or "")
                row["markdown_chars"] = len(str(parsed.get("markdown") or ""))
                row["archive_members"] = len((parsed.get("metadata") or {}).get("members") or [])
                fields = structured.get("fields") if isinstance(structured.get("fields"), dict) else {}
                details = _unique_strings(fields.get("ncs_detail_candidates") or [])
                self_developed_details = _unique_strings(
                    fields.get("ncs_self_developed_detail_candidates") or []
                )
                self_developed_keys = {
                    _norm(detail) for detail in self_developed_details if _norm(detail)
                }
                ability_units = _reviewed_ability_unit_names(fields.get("ability_units") or [])
                detail_states = classify_official_detail_names(
                    details,
                    self_developed_names=self_developed_details,
                )
                ability_states = classify_official_ability_unit_names(
                    ability_units,
                    selected_detail_names=details,
                )
                ability_mapping, ambiguous_units = _scoped_ability_units(fields, details)
                raw_ability_mapping = (
                    fields.get("ability_units_by_detail")
                    if isinstance(fields.get("ability_units_by_detail"), dict)
                    else {}
                )
                convergence_mapping = {
                    str(detail): _reviewed_ability_unit_names(units)
                    for detail, units in raw_ability_mapping.items()
                    if str(detail or "").strip()
                }
                if not convergence_mapping and ability_units:
                    convergence_mapping = {"": ability_units}
                convergence_suggestions = (
                    derive_detail_candidates_from_exact_ability_scopes(
                        convergence_mapping
                    )
                )
                ability_candidate_keys = _ability_candidate_key_sets(
                    ability_states,
                    convergence_suggestions,
                )
                ability_ordinals = _reviewed_ability_unit_ordinals(fields)
                positioned = [item for item in (fields.get("positioned_items") or []) if isinstance(item, dict)]

                official_units_by_detail: dict[str, list[dict[str, Any]]] = {}
                for detail in details:
                    if _norm(detail) in self_developed_keys:
                        official_units_by_detail[detail] = []
                        continue
                    key = _norm(detail)
                    if key not in detail_cache:
                        detail_cache[key] = search_units_by_detail(
                            [detail], max_units=200
                        )
                    official_units_by_detail[detail] = detail_cache[key]
                inferred_mapping, ambiguous_units = (
                    _scope_reviewed_ability_units_by_exact_detail_membership(
                        official_units_by_detail,
                        ambiguous_units,
                    )
                )
                for detail, inferred_units in inferred_mapping.items():
                    ability_mapping.setdefault(detail, []).extend(inferred_units)

                row["detail_count"] = len(details)
                row["details"] = "; ".join(details)
                row["detail_catalog_exact_count"] = sum(
                    1
                    for state in detail_states
                    if state.get("mappingState") == "official_current_exact"
                )
                row["detail_catalog_unmapped_count"] = sum(
                    1
                    for state in detail_states
                    if state.get("mappingState")
                    == "not_in_current_official_catalog"
                )
                row["detail_catalog_ambiguous_count"] = sum(
                    1
                    for state in detail_states
                    if state.get("mappingState")
                    == "official_current_name_ambiguous"
                )
                row["detail_mapping_states"] = json.dumps(
                    [
                        {
                            "sourceName": state.get("sourceName", ""),
                            "mappingState": state.get("mappingState", ""),
                            "catalogExact": bool(state.get("catalogExact")),
                            "officialDetailCodes": state.get(
                                "officialDetailCodes", []
                            ),
                        }
                        for state in detail_states
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                row["self_developed_detail_count"] = len(self_developed_details)
                row["self_developed_details"] = "; ".join(self_developed_details)
                row["official_validation_detail_count"] = sum(
                    1 for detail in details if _norm(detail) not in self_developed_keys
                )
                row["ability_unit_count"] = len(ability_units)
                row["ability_units"] = "; ".join(ability_units)
                row["ability_scoped_count"] = len(
                    {
                        _norm(unit)
                        for units in ability_mapping.values()
                        for unit in units
                        if _norm(unit)
                    }
                )
                row["ability_ambiguous_count"] = len(ambiguous_units)
                row["ability_ambiguous"] = "; ".join(ambiguous_units)
                row["ability_catalog_exact_count"] = len(
                    ability_candidate_keys["catalog_exact"]
                )
                row["ability_catalog_unmapped_count"] = len(
                    ability_candidate_keys["unmapped"]
                )
                row["ability_official_source_scoped_count"] = len(
                    ability_candidate_keys["source_scoped"]
                )
                row["ability_official_derived_scope_count"] = len(
                    ability_candidate_keys["derived_scope"]
                )
                row["ability_official_converged_scope_count"] = len(
                    ability_candidate_keys["safe_convergence"]
                )
                row["ability_official_scope_conflict_count"] = len(
                    ability_candidate_keys["scope_conflict"]
                )
                row["ability_official_ambiguous_count"] = len(
                    ability_candidate_keys["ambiguous"]
                )
                row["ability_official_scope_candidate_count"] = len(
                    ability_candidate_keys["scope_candidates"]
                )
                row["ability_official_code_candidate_count"] = len(
                    ability_candidate_keys["code_candidates"]
                )
                row["ability_mapping_states"] = json.dumps(
                    [
                        {
                            "sourceName": state.get("sourceName", ""),
                            "mappingState": state.get("mappingState", ""),
                            "catalogExact": bool(state.get("catalogExact")),
                            "candidateDetailCodes": state.get(
                                "candidateDetailCodes", []
                            ),
                            "resolvedUnitCodes": state.get(
                                "resolvedUnitCodes", []
                            ),
                        }
                        for state in ability_states
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                row["ability_source_scope_mapping"] = json.dumps(
                    {
                        str(detail): _reviewed_ability_unit_names(units)
                        for detail, units in raw_ability_mapping.items()
                        if str(detail or "").strip()
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                row["detail_convergence_suggestion_count"] = len(
                    convergence_suggestions
                )
                row["detail_convergence_suggestions"] = json.dumps(
                    convergence_suggestions,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                row["positioned_item_count"] = len(positioned)
                row["coordinate_ability_count"] = sum(1 for item in positioned if item.get("section") == "ability_units")
                row["ncs_table_seen"] = bool(fields.get("ncs_detail_absence_saw_ncs_table")) or any(
                    item.get("section") in {"ncs_detail", "ability_units"} for item in positioned
                )
                row["absence_reason"] = str(fields.get("ncs_detail_absence_reason") or "")
                row["declared_no_mapping"] = bool(fields.get("ncs_detail_absence_declared_no_mapping"))

                unmatched_details: list[str] = []
                unmatched_abilities: list[str] = []
                exact_details = 0
                exact_abilities = 0
                probe_codes: list[str] = []
                for detail in details:
                    if _norm(detail) in self_developed_keys:
                        continue
                    official_units = official_units_by_detail[detail]
                    if official_units:
                        exact_details += 1
                    else:
                        unmatched_details.append(detail)
                    for unit in official_units:
                        code = str(unit.get("ncsClCd") or "").strip()
                        if code:
                            unit_by_code.setdefault(code, unit)

                    reviewed_units = ability_mapping.get(detail, [])
                    if reviewed_units:
                        locked, missing = _lock_units_to_reviewed_ability_units(official_units, reviewed_units)
                        if missing:
                            ordinal_recovered, missing = (
                                _recover_ordinal_scoped_reviewed_ability_units(
                                    official_units,
                                    missing,
                                    ability_ordinals,
                                    already_locked=locked,
                                )
                            )
                            locked.extend(ordinal_recovered)
                        if missing:
                            catalog_recovered, missing = (
                                _recover_code_scoped_reviewed_ability_units(
                                    official_units,
                                    missing,
                                    exact_official_units_by_name(missing),
                                    already_locked=locked,
                                )
                            )
                            locked.extend(catalog_recovered)
                        if missing:
                            recovery_candidates: list[dict[str, Any]] = []
                            for missing_name in missing:
                                missing_key = _norm(missing_name)
                                if missing_key not in ability_suggestion_cache:
                                    ability_suggestion_cache[missing_key] = suggest_units_by_text(
                                        [missing_name],
                                        max_units=20,
                                    )
                                recovery_candidates.extend(ability_suggestion_cache[missing_key])
                            recovered, missing = _recover_code_scoped_reviewed_ability_units(
                                official_units,
                                missing,
                                recovery_candidates,
                                already_locked=locked,
                            )
                            locked.extend(recovered)
                        exact_abilities += len(reviewed_units) - len(missing)
                        unmatched_abilities.extend(missing)
                        for unit in locked:
                            code = str(unit.get("ncsClCd") or "").strip()
                            if code:
                                unit_by_code.setdefault(code, unit)
                                if code not in probe_codes:
                                    probe_codes.append(code)
                    elif official_units:
                        code = str(official_units[0].get("ncsClCd") or "").strip()
                        if code and code not in probe_codes:
                            probe_codes.append(code)

                # Probe every source-compatible official code candidate.
                for state in ability_states:
                    if state.get("mappingState") != "official_exact_source_scoped":
                        continue
                    source_name = str(state.get("sourceName") or "").strip()
                    resolved_codes = {
                        str(code or "").strip()
                        for code in (state.get("resolvedUnitCodes") or [])
                        if str(code or "").strip()
                    }
                    for candidate in exact_official_units_by_name([source_name]):
                        code = str(candidate.get("ncsClCd") or "").strip()
                        if code not in resolved_codes:
                            continue
                        unit_by_code.setdefault(code, candidate)
                        if code not in probe_codes:
                            probe_codes.append(code)
                # Multi-unit convergence is still review-required, but every
                # evidence code is exact and must prove KSA availability.
                for suggestion in convergence_suggestions:
                    for evidence in suggestion.get("evidence") or []:
                        if not isinstance(evidence, dict):
                            continue
                        source_name = str(
                            evidence.get("sourceAbilityUnitName") or ""
                        ).strip()
                        if (
                            _norm(source_name)
                            not in ability_candidate_keys["safe_convergence"]
                        ):
                            continue
                        allowed_codes = {
                            str(code or "").strip()
                            for code in (evidence.get("officialUnitCodes") or [])
                            if str(code or "").strip()
                        }
                        for candidate in exact_official_units_by_name(
                            [source_name]
                        ):
                            code = str(candidate.get("ncsClCd") or "").strip()
                            if code not in allowed_codes:
                                continue
                            unit_by_code.setdefault(code, candidate)
                            if code not in probe_codes:
                                probe_codes.append(code)

                row["detail_exact_count"] = exact_details
                row["detail_unmatched_count"] = len(unmatched_details)
                row["detail_unmatched"] = "; ".join(unmatched_details)
                row["ability_exact_count"] = exact_abilities
                row["ability_unmatched_count"] = len(unmatched_abilities)
                row["ability_unmatched"] = "; ".join(unmatched_abilities)
                row["_probe_codes"] = probe_codes
                row["ksa_probe_codes"] = ";".join(probe_codes)

                if not details:
                    row["status"] = "declared_no_mapping" if row["declared_no_mapping"] else "no_detail"
                elif unmatched_details:
                    row["status"] = "detail_mismatch"
                elif ambiguous_units:
                    row["status"] = "ability_ambiguous"
                elif unmatched_abilities:
                    row["status"] = "ability_mismatch"
                elif self_developed_details:
                    row["status"] = "official_exact_with_self_developed"
                else:
                    row["status"] = "mcp_exact"
            except (KordocParseError, NcsMcpError, RuntimeError, OSError, ValueError) as exc:
                row["parse_ms"] = int((time.perf_counter() - started) * 1000)
                row["status"] = "parse_error"
                row["error"] = str(exc)[:500]
            rows.append(row)
            print(f"[{seq}/{len(files)}] {row['status']} {path.name}", flush=True)

        available_codes: set[str] = set()
        probe_units = [unit_by_code[code] for code in dict.fromkeys(
            code for row in rows for code in row.get("_probe_codes", []) if code in unit_by_code
        )]
        if probe_units and not args.skip_ksa:
            ksa_rows = get_ksa_by_units(probe_units, max_factors_per_unit=1)
            available_codes = {
                str(item.get("ncsClCd") or "").strip()
                for item in ksa_rows
                if str(item.get("ncsClCd") or "").strip()
            }

    for row in rows:
        probe_codes = list(row.pop("_probe_codes", []))
        available = [code for code in probe_codes if code in available_codes]
        row["ksa_available_codes"] = ";".join(available)
        ksa_ok = bool(args.skip_ksa) or len(available) == len(probe_codes)
        row["pipeline_ready"] = bool(
            row.get("status") == "mcp_exact"
            and int(row.get("detail_count") or 0) > 0
            and ksa_ok
        )
        if row.get("status") == "mcp_exact" and not ksa_ok:
            row["status"] = "ksa_missing"

    summary = _summary(rows, unique_hashes=len(hash_counts))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"stored_jd_benchmark_{stamp}.csv"
    json_path = report_dir / f"stored_jd_benchmark_{stamp}.json"
    md_path = report_dir / f"stored_jd_benchmark_{stamp}.md"
    _write_csv(csv_path, rows)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, summary, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report={md_path}")
    print(f"csv={csv_path}")
    print(f"summary={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
