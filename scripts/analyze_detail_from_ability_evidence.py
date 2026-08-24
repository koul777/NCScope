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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import _reviewed_ability_unit_names  # noqa: E402
def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _split(value: Any) -> list[str]:
    return _reviewed_ability_unit_names(str(value or "").split(";"))


def _detail_prefix(row: dict[str, Any]) -> str:
    code = str(
        row.get("ncsClCd") or row.get("unit_code") or row.get("code") or ""
    ).strip()
    match = re.match(r"^(\d{8})\d{2}(?:_|$)", code)
    return match.group(1) if match else ""


def collect_evidence(
    benchmark_rows: list[dict[str, str]],
    *,
    minimum_distinct_units: int = 2,
    unit_catalog_path: Path | None = None,
) -> list[dict[str, Any]]:
    catalog = json.loads(
        (ROOT / "app" / "data" / "ncs_detail_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    detail_name_by_code = {
        str(row.get("code") or ""): str(row.get("name") or "")
        for row in catalog.get("details", [])
        if isinstance(row, dict)
    }
    unit_catalog = json.loads(
        (unit_catalog_path or ROOT / "app" / "data" / "ncs_unit_catalog.json")
        .read_text(encoding="utf-8")
    )
    units_by_name_key: dict[str, list[dict[str, Any]]] = {}
    for unit in unit_catalog.get("units", []):
        if not isinstance(unit, dict):
            continue
        key = _norm(unit.get("name"))
        if key:
            units_by_name_key.setdefault(key, []).append(unit)
    output: list[dict[str, Any]] = []

    for row in benchmark_rows:
        if int(row.get("detail_count") or 0) != 1:
            continue
        if int(row.get("detail_unmatched_count") or 0) != 1:
            continue
        unit_names = _split(row.get("ability_units"))
        if not unit_names:
            continue

        unit_evidence: list[dict[str, Any]] = []
        prefix_votes: Counter[str] = Counter()
        for unit_name in unit_names:
            key = _norm(unit_name)
            exact_rows = [
                candidate
                for candidate in units_by_name_key.get(key, [])
                if isinstance(candidate, dict)
                and _detail_prefix(candidate)
            ]
            prefixes = sorted({_detail_prefix(candidate) for candidate in exact_rows})
            # One unit name can occur in several details. It becomes evidence
            # only if its own official code prefix is unique.
            if len(prefixes) != 1:
                continue
            prefix = prefixes[0]
            prefix_votes[prefix] += 1
            unit_evidence.append(
                {
                    "unit_name": unit_name,
                    "detail_code": prefix,
                    "official_detail": detail_name_by_code.get(prefix, ""),
                    "unit_codes": sorted(
                        {
                            str(candidate.get("code") or "")
                            for candidate in exact_rows
                            if _detail_prefix(candidate) == prefix
                        }
                    ),
                }
            )

        converged_code = ""
        if len(prefix_votes) == 1:
            candidate_code, votes = next(iter(prefix_votes.items()))
            if votes >= minimum_distinct_units and detail_name_by_code.get(
                candidate_code
            ):
                converged_code = candidate_code
        output.append(
            {
                "filename": row.get("filename", ""),
                "source_detail": row.get("detail_unmatched", ""),
                "ability_unit_count": len(unit_names),
                "exact_unique_unit_evidence_count": len(unit_evidence),
                "prefix_votes": dict(sorted(prefix_votes.items())),
                "converged_detail_code": converged_code,
                "converged_detail_name": detail_name_by_code.get(
                    converged_code, ""
                ),
                "evidence": unit_evidence,
                "automatic_status_change_allowed": False,
                "review_decision": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "rationale": "",
            }
        )
    return output


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose unmatched single-detail rows using exact official "
            "ability-unit code-prefix convergence."
        )
    )
    parser.add_argument("--benchmark-csv", required=True)
    parser.add_argument("--out-dir", default="tmp/stored_jd_ability_detail_evidence")
    parser.add_argument("--minimum-distinct-units", type=int, default=2)
    parser.add_argument(
        "--unit-catalog",
        default="app/data/ncs_unit_catalog.json",
    )
    args = parser.parse_args()

    with Path(args.benchmark_csv).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        benchmark_rows = list(csv.DictReader(handle))
    rows = collect_evidence(
        benchmark_rows,
        minimum_distinct_units=max(2, args.minimum_distinct_units),
        unit_catalog_path=Path(args.unit_catalog),
    )

    converged = [row for row in rows if row["converged_detail_code"]]
    summary = {
        "eligible_single_unmatched_detail_documents": len(rows),
        "converged_documents": len(converged),
        "converged_source_labels": dict(
            Counter(str(row["source_detail"]) for row in converged)
        ),
        "automatic_status_changes": 0,
        "human_review_required": True,
    }
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"ability_detail_evidence_{stamp}.json"
    output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in converged:
        print(
            f"{row['source_detail']} -> {row['converged_detail_code']} "
            f"{row['converged_detail_name']} "
            f"({row['exact_unique_unit_evidence_count']} units)"
        )
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
