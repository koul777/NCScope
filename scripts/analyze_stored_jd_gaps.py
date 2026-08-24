from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from contextlib import nullcontext
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ncs_mcp_client import (  # noqa: E402
    search_units_by_detail,
    suggest_units_by_text,
    use_ncs_mcp_request_session,
)
from scripts.detail_gap_classifier import (  # noqa: E402
    classify_unmatched_detail_gap,
    explicit_detail_alias_target,
    normalize_detail_key,
    source_label_classification,
)


DEFAULT_BENCHMARK_PATH = (
    ROOT
    / "tmp"
    / "stored_jd_benchmark"
    / "stored_jd_benchmark_20260824_223509.csv"
)
DEFAULT_DETAIL_CATALOG_PATH = ROOT / "app" / "data" / "ncs_detail_catalog.json"
DEFAULT_UNIT_CATALOG_PATH = ROOT / "app" / "data" / "ncs_unit_catalog.json"


def normalize_key(value: Any) -> str:
    return normalize_detail_key(value)


def _catalog_rows(path: Path, collection_name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"catalog root must be an object: {path}")
    rows = payload.get(collection_name)
    if not isinstance(rows, list):
        raise ValueError(f"catalog {path} has no {collection_name!r} list")
    return payload, [row for row in rows if isinstance(row, dict)]


def load_catalog_index(
    detail_catalog_path: Path = DEFAULT_DETAIL_CATALOG_PATH,
    unit_catalog_path: Path = DEFAULT_UNIT_CATALOG_PATH,
) -> dict[str, Any]:
    """Build exact normalized indexes from the immutable catalog artifacts."""

    detail_payload, details = _catalog_rows(Path(detail_catalog_path), "details")
    unit_payload, units = _catalog_rows(Path(unit_catalog_path), "units")
    details_by_key: dict[str, list[dict[str, Any]]] = {}
    units_by_name_key: dict[str, list[dict[str, Any]]] = {}
    for row in details:
        key = normalize_key(row.get("name"))
        if key:
            details_by_key.setdefault(key, []).append(row)
    for row in units:
        key = normalize_key(row.get("name"))
        if key:
            units_by_name_key.setdefault(key, []).append(row)
    return {
        "details": details,
        "units": units,
        "details_by_key": details_by_key,
        "units_by_name_key": units_by_name_key,
        "detail_catalog_path": str(Path(detail_catalog_path)),
        "unit_catalog_path": str(Path(unit_catalog_path)),
        "detail_catalog_count": len(details),
        "unit_catalog_count": len(units),
        "detail_catalog_declared_count": detail_payload.get("classification_count"),
        "unit_catalog_declared_count": unit_payload.get("unit_count"),
    }


def _split_values(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(";"):
        text = raw.strip()
        key = normalize_key(text)
        if text and key and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _unique_join(values: list[str], separator: str = "; ") -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = normalize_key(text)
        if text and key and key not in seen:
            seen.add(key)
            output.append(text)
    return separator.join(output)


def _candidate_detail(row: dict[str, Any]) -> str:
    return str(
        row.get("canonicalDetailName")
        or row.get("ncsSubdCdnm")
        or row.get("resolvedDetailName")
        or ""
    ).strip()


def _candidate_path(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("ncsLclasCdnm") or "").strip(),
        str(row.get("ncsMclasCdnm") or "").strip(),
        str(row.get("ncsSclasCdnm") or "").strip(),
        _candidate_detail(row),
    ]
    return " > ".join(part for part in parts if part)


def _best_similarity(detail: str, suggestions: list[dict[str, Any]]) -> tuple[float, str]:
    source_key = normalize_key(detail)
    best_score = 0.0
    best_name = ""
    for row in suggestions:
        name = _candidate_detail(row)
        target_key = normalize_key(name)
        if not target_key:
            continue
        score = SequenceMatcher(None, source_key, target_key).ratio()
        if score > best_score:
            best_score = score
            best_name = name
    return round(best_score, 4), best_name


def collect_gap_occurrences(
    benchmark_rows: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in benchmark_rows:
        self_developed_keys = {
            normalize_key(value)
            for value in _split_values(row.get("self_developed_details"))
        }
        for detail in _split_values(row.get("detail_unmatched")):
            key = normalize_key(detail)
            item = grouped.setdefault(
                key,
                {
                    "detail": detail,
                    "occurrences": 0,
                    "files": [],
                    "suffixes": [],
                    "declared_self_developed": False,
                    "self_developed_evidence_files": [],
                },
            )
            item["occurrences"] += 1
            item["files"].append(str(row.get("filename") or "").strip())
            item["suffixes"].append(str(row.get("suffix") or "").strip())
            if key in self_developed_keys:
                item["declared_self_developed"] = True
                item["self_developed_evidence_files"].append(
                    str(row.get("filename") or "").strip()
                )
    return grouped


def diagnose_detail(
    detail: str,
    *,
    suggestion_limit: int,
    catalog_index: dict[str, Any] | None = None,
    declared_self_developed: bool = False,
    query_mcp: bool = True,
) -> dict[str, Any]:
    catalog_index = catalog_index or load_catalog_index()
    exact_units = search_units_by_detail([detail], max_units=200) if query_mcp else []
    suggestions = (
        suggest_units_by_text([detail], max_units=suggestion_limit) if query_mcp else []
    )
    detail_key = normalize_key(detail)
    canonical_matches = [
        row for row in suggestions if normalize_key(_candidate_detail(row)) == detail_key
    ]
    suggested_unit_name_matches = [
        row
        for row in suggestions
        if normalize_key(row.get("compeUnitName") or row.get("unit_name")) == detail_key
    ]
    catalog_detail_matches = list(
        catalog_index["details_by_key"].get(detail_key, [])
    )
    alias_target = explicit_detail_alias_target(detail)
    explicit_alias_matches = (
        list(catalog_index["details_by_key"].get(normalize_key(alias_target), []))
        if alias_target
        else []
    )
    catalog_unit_matches = list(
        catalog_index["units_by_name_key"].get(detail_key, [])
    )
    source = source_label_classification(
        detail,
        declared_self_developed=declared_self_developed,
    )
    gap = classify_unmatched_detail_gap(
        detail,
        suggestions=suggestions,
        canonical_detail_matches=canonical_matches,
        unit_name_matches=catalog_unit_matches,
        official_detail_matches=catalog_detail_matches,
        explicit_alias_matches=explicit_alias_matches,
        declared_self_developed=declared_self_developed,
        catalog_checked=True,
    )
    if catalog_detail_matches:
        catalog_status = "official_detail_normalized_exact"
        resolved_catalog_rows = catalog_detail_matches
    elif explicit_alias_matches:
        catalog_status = "verified_explicit_detail_alias"
        resolved_catalog_rows = explicit_alias_matches
    elif catalog_unit_matches:
        catalog_status = "capability_unit_name_exact_only"
        resolved_catalog_rows = []
    else:
        catalog_status = "official_detail_catalog_absent"
        resolved_catalog_rows = []
    resolved_codes = {
        str(row.get("code") or "").strip()
        for row in resolved_catalog_rows
        if str(row.get("code") or "").strip()
    }
    automatic_allowed = bool(
        len(resolved_codes) == 1
        and gap["review_action"]
        in {
            "accept_normalized_official_detail",
            "accept_verified_explicit_alias",
        }
    )
    if automatic_allowed:
        automatic_basis = gap["match_diagnostic"]
    else:
        automatic_basis = "none"
    if canonical_matches:
        suggestion_status = "normalized_name_suggestion_catalog_absent_review"
    elif suggestions:
        suggestion_status = "semantic_suggestion_review_required"
    else:
        suggestion_status = "no_mcp_suggestion"
    similarity, best_name = _best_similarity(detail, suggestions)
    return {
        **gap,
        **source,
        "catalog_status": catalog_status,
        "catalog_detail_codes": _unique_join(
            [str(row.get("code") or "") for row in resolved_catalog_rows]
        ),
        "catalog_detail_names": _unique_join(
            [str(row.get("name") or "") for row in resolved_catalog_rows]
        ),
        "explicit_alias_target": alias_target,
        "catalog_unit_match_codes": _unique_join(
            [str(row.get("code") or "") for row in catalog_unit_matches]
        ),
        "catalog_unit_match_names": _unique_join(
            [str(row.get("name") or "") for row in catalog_unit_matches]
        ),
        "catalog_unit_parent_details": _unique_join(
            [str(row.get("detail_name") or "") for row in catalog_unit_matches]
        ),
        "current_exact_unit_count": len(exact_units),
        "current_exact_detail_names": _unique_join(
            [_candidate_detail(row) for row in exact_units]
        ),
        "suggestion_detail_names": _unique_join(
            [_candidate_detail(row) for row in suggestions]
        ),
        "suggestion_paths": _unique_join(
            [_candidate_path(row) for row in suggestions], separator=" | "
        ),
        "suggested_unit_name_exact_count": len(suggested_unit_name_matches),
        "suggestion_status": suggestion_status,
        "best_suggestion_name": best_name,
        "best_name_similarity": similarity,
        "automatic_acceptance_basis": automatic_basis,
        "automatic_official_code_allowed": automatic_allowed,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "detail",
        "normalized_key",
        "occurrences",
        "file_count",
        "files",
        "suffixes",
        "source_label_type",
        "source_type_evidence",
        "declared_self_developed",
        "self_developed_evidence_files",
        "catalog_status",
        "catalog_detail_codes",
        "catalog_detail_names",
        "explicit_alias_target",
        "catalog_unit_match_codes",
        "catalog_unit_match_names",
        "catalog_unit_parent_details",
        "match_diagnostic",
        "review_action",
        "review_reason",
        "current_exact_unit_count",
        "current_exact_detail_names",
        "suggestion_detail_names",
        "suggestion_paths",
        "suggested_unit_name_exact_count",
        "suggestion_status",
        "best_suggestion_name",
        "best_name_similarity",
        "automatic_acceptance_basis",
        "automatic_official_code_allowed",
        "decision",
        "corrected_detail",
        "corrected_ncs_code",
        "reviewer_id",
        "reviewed_at",
        "rationale",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_diagnostic = Counter(str(row.get("match_diagnostic") or "") for row in rows)
    occurrences_by_diagnostic: Counter[str] = Counter()
    by_catalog_status = Counter(str(row.get("catalog_status") or "") for row in rows)
    occurrences_by_catalog_status: Counter[str] = Counter()
    by_source_type = Counter(str(row.get("source_label_type") or "") for row in rows)
    occurrences_by_source_type: Counter[str] = Counter()
    by_suggestion_status = Counter(str(row.get("suggestion_status") or "") for row in rows)
    for row in rows:
        occurrences = int(row.get("occurrences") or 0)
        occurrences_by_diagnostic[str(row.get("match_diagnostic") or "")] += occurrences
        occurrences_by_catalog_status[str(row.get("catalog_status") or "")] += occurrences
        occurrences_by_source_type[str(row.get("source_label_type") or "")] += occurrences
    resolved = [row for row in rows if row.get("automatic_official_code_allowed")]
    return {
        "unique_gap_labels": len(rows),
        "gap_occurrences": sum(int(row.get("occurrences") or 0) for row in rows),
        "current_exact_resolved_unique": len(resolved),
        "current_exact_resolved_occurrences": sum(
            int(row.get("occurrences") or 0) for row in resolved
        ),
        "automatic_catalog_resolved_unique": len(resolved),
        "automatic_catalog_resolved_occurrences": sum(
            int(row.get("occurrences") or 0) for row in resolved
        ),
        "manual_review_unique": len(rows) - len(resolved),
        "diagnostic_unique_counts": dict(by_diagnostic),
        "diagnostic_occurrence_counts": dict(occurrences_by_diagnostic),
        "catalog_status_unique_counts": dict(by_catalog_status),
        "catalog_status_occurrence_counts": dict(occurrences_by_catalog_status),
        "source_label_type_unique_counts": dict(by_source_type),
        "source_label_type_occurrence_counts": dict(occurrences_by_source_type),
        "suggestion_status_unique_counts": dict(by_suggestion_status),
        "status_update_allowed": False,
        "db_writes": False,
        "approval_claim": False,
    }


def _write_markdown(
    path: Path,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Stored JD detail-gap review",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"- Unique unmatched labels: {summary['unique_gap_labels']}",
        f"- Unmatched occurrences: {summary['gap_occurrences']}",
        f"- Automatically accepted by catalog evidence: {summary['automatic_catalog_resolved_unique']} unique / {summary['automatic_catalog_resolved_occurrences']} occurrences",
        f"- Still requiring review: {summary['manual_review_unique']} unique labels",
        f"- Detail catalog rows: {summary.get('detail_catalog_count', 'unknown')}",
        f"- Capability-unit catalog rows: {summary.get('unit_catalog_count', 'unknown')}",
        "- This artifact is review-only; it performs no DB writes and grants no official/human-reviewed status.",
        "- Similarity and semantic suggestions never grant automatic acceptance.",
        "",
        "## Diagnostic counts",
        "",
    ]
    for name, count in sorted(summary["diagnostic_unique_counts"].items()):
        occurrence_count = summary["diagnostic_occurrence_counts"].get(name, 0)
        lines.append(f"- `{name}`: {count} unique / {occurrence_count} occurrences")
    lines.extend(["", "## Catalog evidence counts", ""])
    for name, count in sorted(summary["catalog_status_unique_counts"].items()):
        occurrence_count = summary["catalog_status_occurrence_counts"].get(name, 0)
        lines.append(f"- `{name}`: {count} unique / {occurrence_count} occurrences")
    lines.extend(
        [
            "",
            "## Review queue",
            "",
            "| label | occurrences | catalog | source type | diagnostic | suggestion status | best suggestion | similarity | auto-code |",
            "| --- | ---: | --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in sorted(
        rows,
        key=lambda item: (-int(item["occurrences"]), item["detail"]),
    ):
        lines.append(
            f"| {str(row['detail']).replace('|', '/')} | {row['occurrences']} | "
            f"{row['catalog_status']} | {row['source_label_type']} | "
            f"{row['match_diagnostic']} | {row['suggestion_status']} | "
            f"{str(row['best_suggestion_name']).replace('|', '/')} | "
            f"{row['best_name_similarity']} | {row['automatic_official_code_allowed']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=(
            "Recheck unmatched stored-JD detail labels and export a blank "
            "human-review sheet."
        )
    )
    parser.add_argument(
        "--benchmark-csv",
        default=str(DEFAULT_BENCHMARK_PATH),
    )
    parser.add_argument("--detail-catalog", default=str(DEFAULT_DETAIL_CATALOG_PATH))
    parser.add_argument("--unit-catalog", default=str(DEFAULT_UNIT_CATALOG_PATH))
    parser.add_argument("--out-dir", default="tmp/stored_jd_gap_analysis")
    parser.add_argument("--suggestion-limit", type=int, default=8)
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Deprecated explicit form of the default offline catalog-evidence mode.",
    )
    parser.add_argument(
        "--include-mcp-suggestions",
        action="store_true",
        help=(
            "Query the configured MCP for review-only semantic suggestions; "
            "these never change catalog acceptance."
        ),
    )
    args = parser.parse_args()
    query_mcp = bool(args.include_mcp_suggestions and not args.catalog_only)

    benchmark_path = Path(args.benchmark_csv)
    with benchmark_path.open("r", encoding="utf-8-sig", newline="") as handle:
        benchmark_rows = list(csv.DictReader(handle))
    grouped = collect_gap_occurrences(benchmark_rows)
    catalog_index = load_catalog_index(
        Path(args.detail_catalog),
        Path(args.unit_catalog),
    )
    rows: list[dict[str, Any]] = []
    session = use_ncs_mcp_request_session() if query_mcp else nullcontext()
    with session:
        ordered = sorted(
            grouped.values(),
            key=lambda value: (-int(value["occurrences"]), value["detail"]),
        )
        for index, item in enumerate(ordered, start=1):
            detail = str(item["detail"])
            diagnostic = diagnose_detail(
                detail,
                suggestion_limit=max(1, args.suggestion_limit),
                catalog_index=catalog_index,
                declared_self_developed=bool(item["declared_self_developed"]),
                query_mcp=query_mcp,
            )
            row = {
                "detail": detail,
                "normalized_key": normalize_key(detail),
                "occurrences": int(item["occurrences"]),
                "file_count": len({value for value in item["files"] if value}),
                "files": _unique_join(item["files"]),
                "suffixes": _unique_join(item["suffixes"]),
                "declared_self_developed": bool(item["declared_self_developed"]),
                "self_developed_evidence_files": _unique_join(
                    item["self_developed_evidence_files"]
                ),
                **diagnostic,
                "decision": "",
                "corrected_detail": "",
                "corrected_ncs_code": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "rationale": "",
            }
            rows.append(row)
            print(
                f"[{index}/{len(grouped)}] {row['match_diagnostic']} {detail}",
                flush=True,
            )

    summary = _summary(rows)
    summary.update(
        {
            "benchmark_path": str(benchmark_path),
            "detail_catalog_path": catalog_index["detail_catalog_path"],
            "unit_catalog_path": catalog_index["unit_catalog_path"],
            "detail_catalog_count": catalog_index["detail_catalog_count"],
            "unit_catalog_count": catalog_index["unit_catalog_count"],
            "mcp_queries_enabled": query_mcp,
        }
    )
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"jd_detail_gap_review_{stamp}.csv"
    json_path = output_dir / f"jd_detail_gap_summary_{stamp}.json"
    md_path = output_dir / f"jd_detail_gap_review_{stamp}.md"
    _write_csv(csv_path, rows)
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(md_path, summary, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"review_csv={csv_path}")
    print(f"summary={json_path}")
    print(f"report={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
