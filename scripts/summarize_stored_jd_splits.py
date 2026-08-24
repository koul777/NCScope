from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _int(row: dict[str, str], field: str) -> int:
    return int(row.get(field) or 0)


def _norm(value: Any) -> str:
    return re.sub(
        r"[\s\-_/|(),.\u00b7\u30fb]+", "", str(value or "")
    ).strip().lower()


def _row_label(row: dict[str, str]) -> str:
    return str(
        row.get("filename") or row.get("sha256") or "unknown-row"
    ).strip()


def _parse_json_list(
    row: dict[str, str],
    field: str,
) -> tuple[list[Any] | None, str | None]:
    raw: Any = row.get(field)
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
    row: dict[str, str],
    field: str,
) -> tuple[int | None, str | None]:
    raw: Any = row.get(field)
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


def _candidate_counts(
    row: dict[str, str],
) -> tuple[int, int, list[str]]:
    """Return evidence-backed row candidate counts and validity reasons.

    Component count addition is intentionally forbidden here: the same
    normalized source label can occur in more than one review state.
    """

    reasons: list[str] = []
    ability_units = _int(row, "ability_unit_count")
    catalog_exact_count = _int(row, "ability_catalog_exact_count")
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
        if states_error:
            reasons.append(states_error)
        elif any(not isinstance(state, dict) for state in states or []):
            reasons.append(
                f"{_row_label(row)}: ability_mapping_states contains a non-object"
            )
        else:
            typed_states = [state for state in states or [] if isinstance(state, dict)]
            invalid_state_names = [
                state
                for state in typed_states
                if not str(state.get("mappingState") or "").strip()
                or not _norm(state.get("sourceName"))
            ]
            if not typed_states:
                reasons.append(
                    f"{_row_label(row)}: ability_mapping_states is empty for "
                    "an ability row"
                )
            elif invalid_state_names:
                reasons.append(
                    f"{_row_label(row)}: ability mapping state missing state or "
                    "sourceName"
                )
            elif scope_count is None and not scope_error:
                scope_states = {
                    "official_exact_source_scoped",
                    "official_exact_derived_scope_review_required",
                    "official_exact_scope_conflict",
                }
                scope_count = len(
                    {
                        _norm(state.get("sourceName"))
                        for state in typed_states
                        if state.get("mappingState") in scope_states
                        and _norm(state.get("sourceName"))
                    }
                )

            if code_count is None and not code_error:
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
                else:
                    catalog_exact = {
                        _norm(state.get("sourceName"))
                        for state in typed_states
                        if (
                            state.get("catalogExact") is True
                            or str(state.get("mappingState") or "").startswith(
                                "official_exact_"
                            )
                        )
                        and _norm(state.get("sourceName"))
                    }
                    source_scoped = {
                        _norm(state.get("sourceName"))
                        for state in typed_states
                        if state.get("mappingState")
                        == "official_exact_source_scoped"
                        and _norm(state.get("sourceName"))
                    }
                    convergence_evidence: set[str] = set()
                    for suggestion in convergence or []:
                        if not isinstance(suggestion, dict):
                            continue
                        evidence = suggestion.get("evidence")
                        if not isinstance(evidence, list):
                            reasons.append(
                                f"{_row_label(row)}: convergence evidence is not a list"
                            )
                            continue
                        if any(not isinstance(item, dict) for item in evidence):
                            reasons.append(
                                f"{_row_label(row)}: convergence evidence contains "
                                "a non-object"
                            )
                            continue
                        convergence_evidence.update(
                            _norm(item.get("sourceAbilityUnitName"))
                            for item in evidence
                            if _norm(item.get("sourceAbilityUnitName"))
                        )
                    safe_convergence = (
                        convergence_evidence & catalog_exact
                    ) - source_scoped
                    code_count = len(source_scoped | safe_convergence)

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


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    parsed = [row for row in rows if row.get("status") != "parse_error"]
    detail_docs = [row for row in parsed if _int(row, "detail_count") > 0]
    official_detail_docs = [
        row
        for row in detail_docs
        if _int(row, "official_validation_detail_count") > 0
    ]
    official_details = sum(
        _int(row, "official_validation_detail_count")
        for row in official_detail_docs
    )
    detail_total = sum(_int(row, "detail_count") for row in detail_docs)
    self_developed_details = sum(
        _int(row, "self_developed_detail_count") for row in detail_docs
    )
    detail_exact = sum(_int(row, "detail_exact_count") for row in detail_docs)
    detail_docs_all_exact = sum(
        _int(row, "detail_exact_count") == _int(row, "detail_count")
        for row in detail_docs
    )
    detail_catalog_exact = sum(
        _int(row, "detail_catalog_exact_count") for row in detail_docs
    )
    detail_catalog_unmapped = sum(
        _int(row, "detail_catalog_unmapped_count") for row in detail_docs
    )
    detail_catalog_ambiguous = sum(
        _int(row, "detail_catalog_ambiguous_count") for row in detail_docs
    )
    detail_doc_exact = sum(
        _int(row, "detail_exact_count")
        == _int(row, "official_validation_detail_count")
        for row in official_detail_docs
    )
    current_official_detail_docs = [
        row
        for row in detail_docs
        if _int(row, "detail_catalog_exact_count")
        + _int(row, "detail_catalog_ambiguous_count")
        > 0
    ]
    current_official_detail_docs_all_exact = sum(
        _int(row, "detail_catalog_ambiguous_count") == 0
        for row in current_official_detail_docs
    )
    detail_mapping_states = (
        detail_catalog_exact
        + detail_catalog_unmapped
        + detail_catalog_ambiguous
        + self_developed_details
    )
    ability_units = sum(_int(row, "ability_unit_count") for row in parsed)
    ability_scoped = sum(
        _int(row, "ability_scoped_count") for row in parsed
    )
    ability_exact = sum(_int(row, "ability_exact_count") for row in parsed)
    ability_catalog_exact = sum(
        _int(row, "ability_catalog_exact_count") for row in parsed
    )
    ability_catalog_unmapped = sum(
        _int(row, "ability_catalog_unmapped_count") for row in parsed
    )
    ability_official_source_scoped = sum(
        _int(row, "ability_official_source_scoped_count") for row in parsed
    )
    ability_official_derived_scope = sum(
        _int(row, "ability_official_derived_scope_count") for row in parsed
    )
    ability_official_converged_scope = sum(
        _int(row, "ability_official_converged_scope_count") for row in parsed
    )
    ability_official_scope_conflict = sum(
        _int(row, "ability_official_scope_conflict_count") for row in parsed
    )
    ability_official_ambiguous = sum(
        _int(row, "ability_official_ambiguous_count") for row in parsed
    )
    candidate_rows = [_candidate_counts(row) for row in parsed]
    ability_official_scope_candidates = sum(row[0] for row in candidate_rows)
    ability_official_code_candidates = sum(row[1] for row in candidate_rows)
    metric_invalid_reasons = list(
        dict.fromkeys(reason for row in candidate_rows for reason in row[2])
    )
    for row in parsed:
        if (
            _int(row, "ability_catalog_exact_count")
            + _int(row, "ability_catalog_unmapped_count")
            != _int(row, "ability_unit_count")
        ):
            metric_invalid_reasons.append(
                f"{_row_label(row)}: ability mapping-state coverage is not exact"
            )
    metric_invalid_reasons = list(dict.fromkeys(metric_invalid_reasons))
    ksa_probe = {
        code.strip()
        for row in rows
        for code in str(row.get("ksa_probe_codes") or "").split(";")
        if code.strip()
    }
    ksa_available = {
        code.strip()
        for row in rows
        for code in str(row.get("ksa_available_codes") or "").split(";")
        if code.strip()
    }
    return {
        "metric_validity": {
            "valid": not metric_invalid_reasons,
            "invalid_reasons": metric_invalid_reasons,
        },
        "unique_contents": len(rows),
        "parse_success_pct": _pct(len(parsed), len(rows)),
        "documents_with_detail": len(detail_docs),
        "documents_all_details_exact": detail_docs_all_exact,
        "documents_all_details_exact_pct": _pct(
            detail_docs_all_exact,
            len(detail_docs),
        ),
        "detail_candidates": detail_total,
        "official_validation_detail_candidates": official_details,
        "source_declared_self_developed_detail_candidates": self_developed_details,
        "detail_exact": detail_exact,
        "detail_exact_pct": _pct(detail_exact, detail_total),
        "official_validation_detail_exact_pct": _pct(
            detail_exact, official_details
        ),
        "documents_with_official_validation_detail": len(official_detail_docs),
        "documents_all_official_details_exact": detail_doc_exact,
        "documents_all_official_details_exact_pct": _pct(
            detail_doc_exact, len(official_detail_docs)
        ),
        "detail_catalog_exact_candidates": detail_catalog_exact,
        "detail_catalog_unmapped_candidates": detail_catalog_unmapped,
        "detail_catalog_ambiguous_candidates": detail_catalog_ambiguous,
        "current_official_detail_recognition_pct": _pct(
            detail_catalog_exact,
            detail_catalog_exact + detail_catalog_ambiguous,
        ),
        "detail_mapping_state_coverage_pct": _pct(
            detail_mapping_states,
            detail_total,
        ),
        "documents_with_current_official_detail": len(
            current_official_detail_docs
        ),
        "documents_all_current_official_details_exact": (
            current_official_detail_docs_all_exact
        ),
        "documents_all_current_official_details_exact_pct": _pct(
            current_official_detail_docs_all_exact,
            len(current_official_detail_docs),
        ),
        "ability_units": ability_units,
        "ability_units_scoped": ability_scoped,
        "ability_scoped_pct": _pct(ability_scoped, ability_units),
        "ability_units_exact": ability_exact,
        "ability_exact_of_scoped_pct": _pct(ability_exact, ability_scoped),
        "ability_end_to_end_pct": _pct(ability_exact, ability_units),
        "ability_catalog_exact_candidates": ability_catalog_exact,
        "ability_catalog_unmapped_candidates": ability_catalog_unmapped,
        "ability_mapping_state_coverage_pct": _pct(
            ability_catalog_exact + ability_catalog_unmapped,
            ability_units,
        ),
        "ability_official_source_scoped": ability_official_source_scoped,
        "ability_official_derived_scope_review_required": (
            ability_official_derived_scope
        ),
        "ability_official_converged_scope_review_required": (
            ability_official_converged_scope
        ),
        "ability_official_scope_conflicts": ability_official_scope_conflict,
        "ability_official_ambiguous": ability_official_ambiguous,
        "ability_official_scope_candidate_count": (
            ability_official_scope_candidates
        ),
        "ability_official_scope_candidate_pct": _pct(
            ability_official_scope_candidates,
            ability_catalog_exact,
        ),
        "ability_official_code_candidate_count": (
            ability_official_code_candidates
        ),
        "ability_official_code_candidate_pct": _pct(
            ability_official_code_candidates,
            ability_catalog_exact,
        ),
        "ksa_probe_unit_codes": len(ksa_probe),
        "ksa_available_unit_codes": len(ksa_available),
        "ksa_available_pct": _pct(len(ksa_available), len(ksa_probe)),
    }


def build_split_summary(
    benchmark_rows: list[dict[str, str]],
    manifest_rows: list[dict[str, str]],
) -> dict[str, Any]:
    manifest_by_hash = {
        str(row.get("sha256") or "").strip(): str(row.get("split") or "").strip()
        for row in manifest_rows
    }
    benchmark_by_hash: dict[str, dict[str, str]] = {}
    for row in benchmark_rows:
        digest = str(row.get("sha256") or "").strip()
        benchmark_by_hash.setdefault(digest, row)
    missing = sorted(set(benchmark_by_hash) - set(manifest_by_hash))
    extra = sorted(set(manifest_by_hash) - set(benchmark_by_hash))
    buckets: dict[str, list[dict[str, str]]] = {
        "development": [],
        "holdout": [],
    }
    for digest, row in benchmark_by_hash.items():
        split = manifest_by_hash.get(digest, "")
        if split in buckets:
            buckets[split].append(row)
    overall = summarize(list(benchmark_by_hash.values()))
    development = summarize(buckets["development"])
    holdout = summarize(buckets["holdout"])
    split_summaries = {
        "overall_unique": overall,
        "development": development,
        "holdout": holdout,
    }
    invalid_reasons = list(
        dict.fromkeys(
            f"{split}: {reason}"
            for split, summary in split_summaries.items()
            for reason in summary["metric_validity"]["invalid_reasons"]
        )
    )
    return {
        "evaluation_basis": (
            "operational exact-yield on pending-review labels; not gold accuracy"
        ),
        "release_acceptance": False,
        "release_acceptance_reason": "requires agent-reviewed holdout scorer",
        "duplicate_weighting": "one row per sha256 content",
        "candidate_metric_definitions": {
            "ability_scope_candidate": (
                "source_scoped + derived_scope_review_required + scope_conflict; "
                "deduplicated per benchmark row"
            ),
            "ability_code_candidate": (
                "source_scoped + safe exact-unit convergence; "
                "deduplicated per benchmark row"
            ),
        },
        "leakage_check": not missing
        and not extra
        and sum(len(rows) for rows in buckets.values()) == len(benchmark_by_hash),
        "missing_manifest_hashes": missing,
        "extra_manifest_hashes": extra,
        "metric_validity": {
            "valid": not invalid_reasons,
            "invalid_reasons": invalid_reasons,
        },
        **split_summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report duplicate-safe development and holdout NCS metrics."
    )
    parser.add_argument("benchmark_csv")
    parser.add_argument("manifest_csv")
    parser.add_argument("--output")
    args = parser.parse_args()
    with Path(args.benchmark_csv).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        benchmark_rows = list(csv.DictReader(handle))
    with Path(args.manifest_csv).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        manifest_rows = list(csv.DictReader(handle))
    result = build_split_summary(benchmark_rows, manifest_rows)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return (
        0
        if result["leakage_check"] and result["metric_validity"]["valid"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
