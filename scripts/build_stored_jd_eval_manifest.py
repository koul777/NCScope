from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def deterministic_split(sha256: str, *, holdout_modulus: int = 5) -> str:
    """Return a stable content-hash split; duplicate files never leak across it."""

    digest = str(sha256 or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    if holdout_modulus < 2:
        raise ValueError("holdout_modulus must be at least 2")
    return "holdout" if int(digest[:8], 16) % holdout_modulus == 0 else "development"


def build_manifest_rows(
    benchmark_rows: list[dict[str, str]],
    *,
    holdout_modulus: int = 5,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in benchmark_rows:
        digest = str(row.get("sha256") or "").strip().lower()
        grouped.setdefault(digest, []).append(row)

    output: list[dict[str, Any]] = []
    for digest, group in sorted(grouped.items()):
        split = deterministic_split(digest, holdout_modulus=holdout_modulus)
        ordered = sorted(group, key=lambda row: str(row.get("filename") or ""))
        source = ordered[0]
        output.append(
            {
                "item_id": f"jd-{digest[:16]}",
                "sha256": digest,
                "split": split,
                "representative_filename": source.get("filename", ""),
                "all_filenames": "; ".join(
                    str(row.get("filename") or "") for row in ordered
                ),
                "file_count": len(group),
                "suffix": source.get("suffix", ""),
                "observed_status": source.get("status", ""),
                "observed_details": source.get("details", ""),
                "observed_detail_count": source.get("detail_count", ""),
                "observed_detail_exact_count": source.get(
                    "detail_exact_count", ""
                ),
                "observed_ability_units": source.get("ability_units", ""),
                "observed_ability_unit_count": source.get(
                    "ability_unit_count", ""
                ),
                "observed_ability_scoped_count": source.get(
                    "ability_scoped_count", ""
                ),
                "observed_ability_exact_count": source.get(
                    "ability_exact_count", ""
                ),
                "annotation_status": "pending_human_review",
                "expected_mapping_state": "",
                "expected_details": "",
                "expected_detail_codes": "",
                "expected_ability_units_by_detail_json": "",
                "expected_ability_unit_codes": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "review_decision": "",
                "review_rationale": "",
            }
        )
    return output


FIELDNAMES = [
    "item_id",
    "sha256",
    "split",
    "representative_filename",
    "all_filenames",
    "file_count",
    "suffix",
    "observed_status",
    "observed_details",
    "observed_detail_count",
    "observed_detail_exact_count",
    "observed_ability_units",
    "observed_ability_unit_count",
    "observed_ability_scoped_count",
    "observed_ability_exact_count",
    "annotation_status",
    "expected_mapping_state",
    "expected_details",
    "expected_detail_codes",
    "expected_ability_units_by_detail_json",
    "expected_ability_unit_codes",
    "reviewer_id",
    "reviewed_at",
    "review_decision",
    "review_rationale",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse duplicate stored-JD benchmark rows and create a stable "
            "development/holdout annotation manifest."
        )
    )
    parser.add_argument("benchmark_csv")
    parser.add_argument("--output-dir", default="tmp/stored_jd_eval_seed")
    parser.add_argument("--holdout-modulus", type=int, default=5)
    args = parser.parse_args()

    benchmark_path = Path(args.benchmark_csv)
    with benchmark_path.open("r", encoding="utf-8-sig", newline="") as handle:
        benchmark_rows = list(csv.DictReader(handle))
    rows = build_manifest_rows(
        benchmark_rows,
        holdout_modulus=args.holdout_modulus,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"stored_jd_eval_manifest_{stamp}.csv"
    json_path = output_dir / f"stored_jd_eval_manifest_{stamp}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    split_counts = Counter(str(row["split"]) for row in rows)
    summary = {
        "source_benchmark": str(benchmark_path),
        "source_files": len(benchmark_rows),
        "unique_contents": len(rows),
        "duplicate_files": len(benchmark_rows) - len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "holdout_modulus": args.holdout_modulus,
        "split_key": "sha256",
        "annotation_status": "pending_human_review",
        "is_gold": False,
        "leakage_check": len({row["sha256"] for row in rows}) == len(rows),
    }
    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"csv={csv_path}")
    print(f"json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
