from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "ncs_recruitment_goldset"
WORKFLOW_VERSION = "ncs_recruitment_human_goldset_v1"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

REVIEWER_FIELDS = [
    "item_id",
    "split",
    "document_sha256",
    "local_document_path",
    "source_url",
    "reviewer_slot",
    "reviewer_id",
    "reviewed_at_utc",
    "review_status",
    "mapping_state",
    "detail_names_json",
    "detail_codes_json",
    "evidence_json",
    "confidence",
    "notes",
]

ADJUDICATION_FIELDS = [
    "item_id",
    "split",
    "document_sha256",
    "local_document_path",
    "reviewer_a_status",
    "reviewer_a_mapping_state",
    "reviewer_a_detail_names_json",
    "reviewer_a_detail_codes_json",
    "reviewer_b_status",
    "reviewer_b_mapping_state",
    "reviewer_b_detail_names_json",
    "reviewer_b_detail_codes_json",
    "agreement_status",
    "adjudication_status",
    "adjudicator_id",
    "adjudicated_at_utc",
    "final_mapping_state",
    "final_detail_names_json",
    "final_detail_codes_json",
    "final_evidence_json",
    "adjudication_rationale",
]

MAPPING_STATES = (
    "official_current",
    "legacy_or_nonstandard",
    "self_developed",
    "not_stated",
    "ambiguous",
    "unreadable",
)


class GoldsetPreparationError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: Any, *, field: str = "sha256") -> str:
    digest = str(value or "").strip().lower()
    if not HEX64_RE.fullmatch(digest):
        raise GoldsetPreparationError(
            f"{field} must be a 64-character lowercase hexadecimal digest"
        )
    return digest


def deterministic_split(document_sha256: str, *, holdout_modulus: int = 5) -> str:
    """Assign content-identical documents to one stable, evaluation-only split."""

    digest = require_sha256(document_sha256, field="document_sha256")
    if holdout_modulus < 2:
        raise GoldsetPreparationError("holdout_modulus must be at least 2")
    return (
        "gold_holdout"
        if int(digest[:16], 16) % holdout_modulus == 0
        else "gold_validation"
    )


def validate_local_output_dir(output_dir: Path, *, root: Path | None = None) -> Path:
    """Refuse an output location that could accidentally become a tracked artifact."""

    resolved = output_dir.resolve()
    root = ROOT if root is None else root
    allowed = [(root / "tmp").resolve(), (root / ".tmp").resolve()]
    if not any(resolved == parent or resolved.is_relative_to(parent) for parent in allowed):
        raise GoldsetPreparationError(
            "goldset artifacts may only be written below the repository tmp/ or .tmp/ directory"
        )
    return resolved


def _load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("records") or payload.get("rows") or payload.get("cases")
        else:
            rows = None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise GoldsetPreparationError(f"{path}: expected a JSON row list")
        return [dict(row) for row in rows]
    raise GoldsetPreparationError(f"{path}: source index must be CSV or JSON")


def load_source_index(path: Path) -> list[dict[str, Any]]:
    """Load a private local index and normalize document paths relative to that index."""

    output: list[dict[str, Any]] = []
    for raw in _load_rows(path):
        row = dict(raw)
        raw_path = str(
            row.get("local_document_path")
            or row.get("document_path")
            or row.get("path")
            or ""
        ).strip()
        if raw_path:
            document_path = Path(raw_path)
            if not document_path.is_absolute():
                document_path = path.parent / document_path
            row["local_document_path"] = str(document_path.resolve())
        output.append(row)
    return output


def _extract_hashes(value: Any) -> set[str]:
    output: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in {
                "sha256",
                "document_sha256",
                "content_sha256",
                "source_sha256",
            }:
                candidate = str(item or "").strip().lower()
                if HEX64_RE.fullmatch(candidate):
                    output.add(candidate)
            output.update(_extract_hashes(item))
    elif isinstance(value, list):
        for item in value:
            output.update(_extract_hashes(item))
    return output


def load_tuning_hashes(paths: Iterable[Path]) -> set[str]:
    output: set[str] = set()
    for path in paths:
        if path.suffix.lower() == ".csv":
            output.update(_extract_hashes(_load_rows(path)))
        elif path.suffix.lower() == ".json":
            output.update(
                _extract_hashes(json.loads(path.read_text(encoding="utf-8-sig")))
            )
        else:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                candidate = line.strip().lower()
                if HEX64_RE.fullmatch(candidate):
                    output.add(candidate)
    return output


def _benchmark_cases(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise GoldsetPreparationError("benchmark JSON must contain a cases list")
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise GoldsetPreparationError("benchmark cases must be objects")
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            raise GoldsetPreparationError("every benchmark case must have case_id")
        if case_id in output:
            raise GoldsetPreparationError(f"duplicate benchmark case_id: {case_id}")
        output[case_id] = dict(row)
    if not output:
        raise GoldsetPreparationError("benchmark contains no cases")
    return output


def _normalize_source_row(
    row: Mapping[str, Any],
    *,
    benchmark_case_ids: set[str],
    verify_files: bool,
) -> dict[str, Any]:
    case_id = str(row.get("case_id") or "").strip()
    if not case_id or case_id not in benchmark_case_ids:
        raise GoldsetPreparationError(f"source index has unknown case_id: {case_id or '<blank>'}")

    local_path = str(row.get("local_document_path") or "").strip()
    if not local_path:
        raise GoldsetPreparationError(f"{case_id}: local_document_path is required")
    path = Path(local_path).resolve()

    declared_digest = str(
        row.get("document_sha256") or row.get("sha256") or ""
    ).strip().lower()
    if verify_files:
        if not path.is_file():
            raise GoldsetPreparationError(f"{case_id}: local document does not exist: {path}")
        actual_digest = sha256_file(path)
        if declared_digest and require_sha256(
            declared_digest, field="document_sha256"
        ) != actual_digest:
            raise GoldsetPreparationError(f"{case_id}: document SHA-256 mismatch")
        document_digest = actual_digest
    else:
        document_digest = require_sha256(
            declared_digest, field="document_sha256"
        )

    return {
        "case_id": case_id,
        "document_sha256": document_digest,
        "local_document_path": str(path),
        "original_filename": str(row.get("original_filename") or path.name),
        "posting_title": str(row.get("posting_title") or ""),
        "source_url": str(row.get("source_url") or ""),
    }


def _reviewer_row(record: Mapping[str, Any], slot: str) -> dict[str, str]:
    return {
        "item_id": str(record["item_id"]),
        "split": str(record["split"]),
        "document_sha256": str(record["document_sha256"]),
        "local_document_path": str(record["local_document_path"]),
        "source_url": str(record.get("source_url") or ""),
        "reviewer_slot": slot,
        "reviewer_id": "",
        "reviewed_at_utc": "",
        "review_status": "pending_independent_review",
        "mapping_state": "",
        "detail_names_json": "",
        "detail_codes_json": "",
        "evidence_json": "",
        "confidence": "",
        "notes": "",
    }


def _adjudication_row(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "item_id": str(record["item_id"]),
        "split": str(record["split"]),
        "document_sha256": str(record["document_sha256"]),
        "local_document_path": str(record["local_document_path"]),
        "reviewer_a_status": "",
        "reviewer_a_mapping_state": "",
        "reviewer_a_detail_names_json": "",
        "reviewer_a_detail_codes_json": "",
        "reviewer_b_status": "",
        "reviewer_b_mapping_state": "",
        "reviewer_b_detail_names_json": "",
        "reviewer_b_detail_codes_json": "",
        "agreement_status": "pending_two_reviews",
        "adjudication_status": "pending_two_reviews",
        "adjudicator_id": "",
        "adjudicated_at_utc": "",
        "final_mapping_state": "",
        "final_detail_names_json": "",
        "final_detail_codes_json": "",
        "final_evidence_json": "",
        "adjudication_rationale": "",
    }


def build_workflow(
    benchmark_payload: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    holdout_modulus: int = 5,
    tuning_hashes: Iterable[str] = (),
    verify_files: bool = True,
) -> dict[str, Any]:
    """Build a double-blind human-review seed without creating any gold answers."""

    if holdout_modulus < 2:
        raise GoldsetPreparationError("holdout_modulus must be at least 2")
    benchmark_cases = _benchmark_cases(benchmark_payload)
    normalized_rows: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for row in source_rows:
        normalized = _normalize_source_row(
            row,
            benchmark_case_ids=set(benchmark_cases),
            verify_files=verify_files,
        )
        if normalized["case_id"] in seen_case_ids:
            raise GoldsetPreparationError(
                f"duplicate source-index case_id: {normalized['case_id']}"
            )
        seen_case_ids.add(normalized["case_id"])
        normalized_rows.append(normalized)

    missing = sorted(set(benchmark_cases) - seen_case_ids)
    if missing:
        raise GoldsetPreparationError(
            f"source index is missing {len(missing)} benchmark case(s)"
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized_rows:
        grouped.setdefault(row["document_sha256"], []).append(row)

    normalized_tuning_hashes = {
        require_sha256(value, field="tuning document sha256") for value in tuning_hashes
    }
    overlap = sorted(set(grouped) & normalized_tuning_hashes)
    if overlap:
        raise GoldsetPreparationError(
            f"goldset/tuning leakage detected for {len(overlap)} content digest(s)"
        )

    records: list[dict[str, Any]] = []
    for document_digest, group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda row: row["case_id"])
        representative = ordered[0]
        benchmark_group = [benchmark_cases[row["case_id"]] for row in ordered]
        records.append(
            {
                "item_id": f"nrg-{document_digest}",
                "document_sha256": document_digest,
                "split": deterministic_split(
                    document_digest, holdout_modulus=holdout_modulus
                ),
                "usage_policy": "evaluation_only_no_training_or_rule_tuning",
                "annotation_status": "pending_two_independent_human_reviews",
                "is_gold": False,
                "case_ids": [row["case_id"] for row in ordered],
                "posting_ids": sorted(
                    {
                        str(case.get("posting_id") or "")
                        for case in benchmark_group
                        if str(case.get("posting_id") or "")
                    }
                ),
                "benchmark_statuses": sorted(
                    {
                        str(case.get("status") or "not_recorded")
                        for case in benchmark_group
                    }
                ),
                "local_document_path": representative["local_document_path"],
                "original_filename": representative["original_filename"],
                "posting_title": representative["posting_title"],
                "source_url": representative["source_url"],
                "duplicate_case_count": len(ordered),
            }
        )

    records_digest = sha256_bytes(canonical_json_bytes(records))
    reviewer_a = [_reviewer_row(record, "A") for record in records]
    reviewer_b = [_reviewer_row(record, "B") for record in records]
    adjudication = [_adjudication_row(record) for record in records]
    split_counts = Counter(str(record["split"]) for record in records)
    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "benchmark_payload_sha256": sha256_bytes(
            canonical_json_bytes(benchmark_payload)
        ),
        "benchmark_case_count": len(benchmark_cases),
        "unique_document_count": len(records),
        "duplicate_case_count": len(benchmark_cases) - len(records),
        "split_counts": dict(sorted(split_counts.items())),
        "holdout_modulus": holdout_modulus,
        "split_key": "document_sha256",
        "usage_policy": "evaluation_only_no_training_or_rule_tuning",
        "human_review_policy": "two_independent_reviewers_then_adjudication",
        "automatic_predictions_are_gold": False,
        "is_gold": False,
        "tuning_hash_count_checked": len(normalized_tuning_hashes),
        "tuning_overlap_count": 0,
        "records_sha256": records_digest,
    }
    workflow = {
        "summary": summary,
        "records": records,
        "reviewer_a": reviewer_a,
        "reviewer_b": reviewer_b,
        "adjudication": adjudication,
    }
    validate_workflow(workflow)
    return workflow


def validate_workflow(workflow: Mapping[str, Any]) -> None:
    records = workflow.get("records")
    reviewer_a = workflow.get("reviewer_a")
    reviewer_b = workflow.get("reviewer_b")
    adjudication = workflow.get("adjudication")
    if not all(isinstance(rows, list) for rows in (records, reviewer_a, reviewer_b, adjudication)):
        raise GoldsetPreparationError("workflow row collections must be lists")
    assert isinstance(records, list)
    assert isinstance(reviewer_a, list)
    assert isinstance(reviewer_b, list)
    assert isinstance(adjudication, list)
    if not (len(records) == len(reviewer_a) == len(reviewer_b) == len(adjudication)):
        raise GoldsetPreparationError("manifest/reviewer/adjudication row counts differ")

    item_ids = [str(row.get("item_id") or "") for row in records]
    digests = [str(row.get("document_sha256") or "") for row in records]
    if len(set(item_ids)) != len(item_ids) or any(not value for value in item_ids):
        raise GoldsetPreparationError("manifest item IDs are blank or duplicated")
    if len(set(digests)) != len(digests):
        raise GoldsetPreparationError("duplicate content leaked into multiple manifest rows")
    holdout_modulus = int((workflow.get("summary") or {}).get("holdout_modulus") or 0)
    all_case_ids: list[str] = []
    record_by_id: dict[str, Mapping[str, Any]] = {}
    for row, digest in zip(records, digests):
        require_sha256(digest, field="document_sha256")
        item_id = str(row.get("item_id") or "")
        if item_id != f"nrg-{digest}":
            raise GoldsetPreparationError("manifest item ID/content digest mismatch")
        if row.get("split") != deterministic_split(
            digest, holdout_modulus=holdout_modulus
        ):
            raise GoldsetPreparationError("manifest deterministic split mismatch")
        if row.get("is_gold") is not False:
            raise GoldsetPreparationError("unreviewed manifest record marked gold")
        case_ids = row.get("case_ids")
        if not isinstance(case_ids, list) or not case_ids:
            raise GoldsetPreparationError("manifest record must retain source case IDs")
        all_case_ids.extend(str(value) for value in case_ids)
        record_by_id[item_id] = row
    if len(set(all_case_ids)) != len(all_case_ids):
        raise GoldsetPreparationError("benchmark case leaked into multiple manifest records")

    expected_ids = set(item_ids)
    for name, rows in (
        ("reviewer_a", reviewer_a),
        ("reviewer_b", reviewer_b),
        ("adjudication", adjudication),
    ):
        if {str(row.get("item_id") or "") for row in rows} != expected_ids:
            raise GoldsetPreparationError(f"{name} item IDs do not match manifest")
        for row in rows:
            record = record_by_id[str(row.get("item_id") or "")]
            for field in (
                "split",
                "document_sha256",
                "local_document_path",
            ):
                if str(row.get(field) or "") != str(record.get(field) or ""):
                    raise GoldsetPreparationError(
                        f"{name} {field} does not match manifest"
                    )
    if {str(row.get("reviewer_slot")) for row in reviewer_a} != {"A"}:
        raise GoldsetPreparationError("reviewer A template has invalid reviewer_slot")
    if {str(row.get("reviewer_slot")) for row in reviewer_b} != {"B"}:
        raise GoldsetPreparationError("reviewer B template has invalid reviewer_slot")

    forbidden_fragments = ("observed", "predicted", "automatic", "source_detail")
    for rows in (reviewer_a, reviewer_b, adjudication):
        for row in rows:
            if any(
                fragment in str(key).casefold()
                for key in row
                for fragment in forbidden_fragments
            ):
                raise GoldsetPreparationError(
                    "human review templates must not expose automatic prediction fields"
                )
    for row in reviewer_a + reviewer_b:
        if any(
            str(row.get(field) or "")
            for field in ("mapping_state", "detail_names_json", "detail_codes_json")
        ):
            raise GoldsetPreparationError("reviewer answers must start blank")
    for row in adjudication:
        if any(
            str(row.get(field) or "")
            for field in (
                "final_mapping_state",
                "final_detail_names_json",
                "final_detail_codes_json",
            )
        ):
            raise GoldsetPreparationError("adjudicated gold answers must start blank")

    summary = workflow.get("summary") or {}
    if summary.get("automatic_predictions_are_gold") is not False:
        raise GoldsetPreparationError("automatic predictions must never be marked gold")
    if summary.get("is_gold") is not False:
        raise GoldsetPreparationError("an unreviewed workflow must not be marked gold")
    if int(summary.get("benchmark_case_count") or -1) != len(all_case_ids):
        raise GoldsetPreparationError("benchmark case count integrity mismatch")
    if int(summary.get("unique_document_count") or -1) != len(records):
        raise GoldsetPreparationError("unique document count integrity mismatch")
    expected_digest = sha256_bytes(canonical_json_bytes(records))
    if summary.get("records_sha256") != expected_digest:
        raise GoldsetPreparationError("manifest records integrity digest mismatch")


def _write_csv(path: Path, fields: list[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_workflow(workflow: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    validate_workflow(workflow)
    output_dir = validate_local_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "goldset_review_manifest.local.json"
    reviewer_a_path = output_dir / "reviewer_a.local.csv"
    reviewer_b_path = output_dir / "reviewer_b.local.csv"
    adjudication_path = output_dir / "adjudication.local.csv"
    exclusion_path = output_dir / "do_not_tune.local.csv"
    integrity_path = output_dir / "integrity.local.json"

    manifest_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": workflow["summary"],
        "mapping_state_values": list(MAPPING_STATES),
        "records": workflow["records"],
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(reviewer_a_path, REVIEWER_FIELDS, workflow["reviewer_a"])
    _write_csv(reviewer_b_path, REVIEWER_FIELDS, workflow["reviewer_b"])
    _write_csv(adjudication_path, ADJUDICATION_FIELDS, workflow["adjudication"])
    _write_csv(
        exclusion_path,
        ["document_sha256", "usage_policy"],
        [
            {
                "document_sha256": row["document_sha256"],
                "usage_policy": "evaluation_only_no_training_or_rule_tuning",
            }
            for row in workflow["records"]
        ],
    )

    artifact_paths = {
        "manifest": manifest_path,
        "reviewer_a": reviewer_a_path,
        "reviewer_b": reviewer_b_path,
        "adjudication": adjudication_path,
        "do_not_tune": exclusion_path,
    }
    integrity = {
        "workflow_version": WORKFLOW_VERSION,
        "records_sha256": workflow["summary"]["records_sha256"],
        "artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(artifact_paths.items())
        },
        "artifact_count": len(artifact_paths),
        "local_only": True,
        "automatic_predictions_are_gold": False,
    }
    integrity_path.write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**artifact_paths, "integrity": integrity_path}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an evaluation-only, double-human-review goldset seed from "
            "benchmark_ncs_recruitment_live.py output and a private local source index."
        )
    )
    parser.add_argument("benchmark_json")
    parser.add_argument(
        "source_index",
        help=(
            "private CSV/JSON mapping case_id to local_document_path; optional columns: "
            "document_sha256, original_filename, posting_title, source_url"
        ),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--holdout-modulus", type=int, default=5)
    parser.add_argument(
        "--tuning-manifest",
        action="append",
        default=[],
        help="CSV/JSON/text file containing tuning document SHA-256 values; repeatable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    benchmark_path = Path(args.benchmark_json).resolve()
    source_index_path = Path(args.source_index).resolve()
    output_dir = validate_local_output_dir(Path(args.output_dir))
    benchmark_payload = json.loads(benchmark_path.read_text(encoding="utf-8-sig"))
    if not isinstance(benchmark_payload, dict):
        raise GoldsetPreparationError("benchmark JSON must be an object")
    source_rows = load_source_index(source_index_path)
    tuning_hashes = load_tuning_hashes(
        Path(value).resolve() for value in args.tuning_manifest
    )
    workflow = build_workflow(
        benchmark_payload,
        source_rows,
        holdout_modulus=int(args.holdout_modulus),
        tuning_hashes=tuning_hashes,
        verify_files=True,
    )
    paths = write_workflow(workflow, output_dir)
    print(json.dumps(workflow["summary"], ensure_ascii=False, indent=2))
    for name, path in paths.items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
