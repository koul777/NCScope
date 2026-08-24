from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".zip"}
DEFAULT_QUOTAS = {".hwp": 17, ".pdf": 14, ".hwpx": 1, ".zip": 4}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_excluded_hashes(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") or payload.get("blind_records") or []
        if not isinstance(records, list):
            raise ValueError(f"{path}: records must be a list")
        for record in records:
            if isinstance(record, dict):
                digest = str(record.get("sha256") or "").strip().lower()
                if len(digest) == 64:
                    excluded.add(digest)
    return excluded


def select_records(
    corpus_dir: Path,
    *,
    seed: str,
    quotas: dict[str, int],
    excluded_hashes: set[str],
    excluded_filenames: set[str],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        digest = file_sha256(path)
        if digest in excluded_hashes or path.name in excluded_filenames:
            continue
        unique.setdefault(
            digest,
            {
                "sha256": digest,
                "source_file": path.name,
                "relative_path": path.relative_to(corpus_dir).as_posix(),
                "suffix": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
            },
        )

    by_suffix: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in unique.values():
        record["selection_key"] = hashlib.sha256(
            f"{seed}\0{record['sha256']}".encode()
        ).hexdigest()
        by_suffix[record["suffix"]].append(record)
    for records in by_suffix.values():
        records.sort(key=lambda item: (item["selection_key"], item["sha256"]))

    selected: list[dict[str, Any]] = []
    for suffix, quota in quotas.items():
        available = by_suffix.get(suffix, [])
        if len(available) < quota:
            raise ValueError(
                f"not enough {suffix} records: requested {quota}, available {len(available)}"
            )
        selected.extend(available[:quota])
    selected.sort(key=lambda item: (item["selection_key"], item["sha256"]))
    for record in selected:
        record.pop("selection_key", None)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a source-only, parser-output-blind stored-JD holdout."
    )
    parser.add_argument("--corpus-dir", default="tmp/alio_jd_200_mcp")
    parser.add_argument(
        "--seed",
        default="stored-jd-final-blind-v2-20260825",
    )
    parser.add_argument("--exclude-reference", action="append", default=[])
    parser.add_argument("--exclude-filename", action="append", default=[])
    parser.add_argument("--batch-count", type=int, default=3)
    parser.add_argument(
        "--output",
        default="tmp/stored_jd_final_blind/final_blind_manifest.json",
    )
    args = parser.parse_args()
    if args.batch_count < 1:
        raise ValueError("batch-count must be positive")

    records = select_records(
        Path(args.corpus_dir),
        seed=args.seed,
        quotas=DEFAULT_QUOTAS,
        excluded_hashes=load_excluded_hashes(
            [Path(value) for value in args.exclude_reference]
        ),
        excluded_filenames=set(args.exclude_filename),
    )
    batches = [[] for _ in range(args.batch_count)]
    for index, record in enumerate(records):
        batches[index % args.batch_count].append(record)
    payload = {
        "schema_version": 1,
        "selection_method": "seeded_sha256_by_suffix_without_parser_output",
        "seed": args.seed,
        "quotas": DEFAULT_QUOTAS,
        "record_count": len(records),
        "records_sha256": canonical_sha256(records),
        "excluded_reference_files": [
            Path(value).as_posix() for value in args.exclude_reference
        ],
        "excluded_filenames": sorted(set(args.exclude_filename)),
        "records": records,
        "batches": [
            {
                "batch": chr(ord("a") + index),
                "record_count": len(batch),
                "records_sha256": canonical_sha256(batch),
                "records": batch,
            }
            for index, batch in enumerate(batches)
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key not in {"records", "batches"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    for batch in payload["batches"]:
        print(
            f"batch_{batch['batch']}={batch['record_count']} "
            f"sha256={batch['records_sha256']}"
        )
    print(f"manifest={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
