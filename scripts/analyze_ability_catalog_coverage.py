from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _split(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(";"):
        text = raw.strip()
        key = _norm(text)
        if text and key and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def analyze(
    benchmark_rows: list[dict[str, str]],
    *,
    detail_catalog_path: Path,
    unit_catalog_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    detail_payload = json.loads(detail_catalog_path.read_text(encoding="utf-8"))
    unit_payload = json.loads(unit_catalog_path.read_text(encoding="utf-8"))

    detail_codes_by_name: dict[str, set[str]] = {}
    detail_name_by_code: dict[str, str] = {}
    for detail in detail_payload.get("details", []):
        if not isinstance(detail, dict):
            continue
        code = str(detail.get("code") or "").strip()
        name = str(detail.get("name") or "").strip()
        key = _norm(name)
        if code and name and key:
            detail_codes_by_name.setdefault(key, set()).add(code)
            detail_name_by_code[code] = name

    units_by_name: dict[str, list[dict[str, str]]] = {}
    for unit in unit_payload.get("units", []):
        if not isinstance(unit, dict):
            continue
        name = str(unit.get("name") or "").strip()
        key = _norm(name)
        if key:
            units_by_name.setdefault(key, []).append(
                {
                    "code": str(unit.get("code") or "").strip(),
                    "detail_code": str(unit.get("detail_code") or "").strip(),
                    "detail_name": str(unit.get("detail_name") or "").strip(),
                }
            )

    occurrence_rows: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    unique_names_by_state: dict[str, set[str]] = {}
    for row in benchmark_rows:
        source_details = _split(row.get("details"))
        selected_detail_codes = {
            code
            for detail in source_details
            for code in detail_codes_by_name.get(_norm(detail), set())
        }
        current_unmatched = {_norm(value) for value in _split(row.get("ability_unmatched"))}
        current_ambiguous = {_norm(value) for value in _split(row.get("ability_ambiguous"))}
        for ability_name in _split(row.get("ability_units")):
            key = _norm(ability_name)
            catalog_matches = units_by_name.get(key, [])
            all_prefixes = {
                item["detail_code"]
                for item in catalog_matches
                if item.get("detail_code")
            }
            selected_prefixes = all_prefixes.intersection(selected_detail_codes)
            if not catalog_matches:
                state = "not_in_current_official_catalog"
            elif len(selected_prefixes) == 1:
                state = "official_exact_in_selected_detail"
            elif len(selected_prefixes) > 1:
                state = "official_exact_ambiguous_in_selected_details"
            elif len(all_prefixes) == 1:
                state = "official_exact_unique_other_or_unresolved_detail"
            else:
                state = "official_exact_ambiguous_global"
            state_counts[state] += 1
            unique_names_by_state.setdefault(state, set()).add(key)
            occurrence_rows.append(
                {
                    "filename": row.get("filename", ""),
                    "source_details": source_details,
                    "selected_detail_codes": sorted(selected_detail_codes),
                    "ability_name": ability_name,
                    "state": state,
                    "catalog_detail_codes": sorted(all_prefixes),
                    "catalog_detail_names": [
                        detail_name_by_code.get(code, "")
                        for code in sorted(all_prefixes)
                    ],
                    "catalog_unit_codes": sorted(
                        {
                            item["code"]
                            for item in catalog_matches
                            if item.get("code")
                        }
                    ),
                    "current_unmatched": key in current_unmatched,
                    "current_ambiguous": key in current_ambiguous,
                }
            )

    total = len(occurrence_rows)
    official = sum(
        count
        for state, count in state_counts.items()
        if state.startswith("official_exact_")
    )
    safe_in_scope = state_counts["official_exact_in_selected_detail"]
    top_nonofficial = Counter(
        row["ability_name"]
        for row in occurrence_rows
        if row["state"] == "not_in_current_official_catalog"
    ).most_common(50)
    summary = {
        "benchmark_rows": len(benchmark_rows),
        "catalog_units": int(unit_payload.get("unit_count") or 0),
        "ability_occurrences_from_csv": total,
        "official_exact_name_occurrences": official,
        "official_exact_name_pct": round(100 * official / total, 2) if total else 0.0,
        "official_exact_in_selected_detail_occurrences": safe_in_scope,
        "official_exact_in_selected_detail_pct": round(100 * safe_in_scope / total, 2)
        if total
        else 0.0,
        "state_occurrence_counts": dict(state_counts),
        "state_unique_name_counts": {
            state: len(names) for state, names in sorted(unique_names_by_state.items())
        },
        "top_not_in_current_official_catalog": [
            {"name": name, "occurrences": count} for name, count in top_nonofficial
        ],
        "metric_note": (
            "Source-label coverage diagnostic only; pending-review source labels "
            "are not a human-verified gold set."
        ),
    }
    return summary, occurrence_rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=(
            "Classify stored-JD ability labels against the complete lightweight "
            "official NCS unit catalog without semantic guessing."
        )
    )
    parser.add_argument("--benchmark-csv", required=True)
    parser.add_argument(
        "--detail-catalog", default="app/data/ncs_detail_catalog.json"
    )
    parser.add_argument("--unit-catalog", default="app/data/ncs_unit_catalog.json")
    parser.add_argument("--out-dir", default="tmp/stored_jd_ability_catalog")
    args = parser.parse_args()

    with Path(args.benchmark_csv).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        benchmark_rows = list(csv.DictReader(handle))
    summary, rows = analyze(
        benchmark_rows,
        detail_catalog_path=Path(args.detail_catalog),
        unit_catalog_path=Path(args.unit_catalog),
    )

    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"ability_catalog_coverage_{stamp}.json"
    csv_path = output_dir / f"ability_catalog_occurrences_{stamp}.csv"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = [
        "filename",
        "source_details",
        "selected_detail_codes",
        "ability_name",
        "state",
        "catalog_detail_codes",
        "catalog_detail_names",
        "catalog_unit_codes",
        "current_unmatched",
        "current_ambiguous",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row[key], ensure_ascii=False)
                    if isinstance(row.get(key), list)
                    else row.get(key, "")
                    for key in fields
                }
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary={json_path}")
    print(f"rows={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
