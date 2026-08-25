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

from scripts.ncs_recruitment_split import (
    SPLIT_KEY,
    compute_split_groups,
    deterministic_split as shared_deterministic_split,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "ncs_recruitment_goldset"
WORKFLOW_VERSION = "ncs_recruitment_human_goldset_v2"
SEED_INTEGRITY_VERSION = "ncs_recruitment_seed_integrity_v2"
CANDIDATE_EXCLUSION_AUDIT_VERSION = (
    "ncs_recruitment_candidate_exclusion_audit_v1"
)
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

REVIEWER_FIELDS = [
    "item_id",
    "document_sha256",
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
    """Assign one sealed document/posting group to a stable evaluation split."""

    digest = require_sha256(document_sha256, field="document_sha256")
    if holdout_modulus < 2:
        raise GoldsetPreparationError("holdout_modulus must be at least 2")
    return shared_deterministic_split(digest, holdout_modulus=holdout_modulus)


def _assign_split_groups(
    records: list[dict[str, Any]],
    *,
    holdout_modulus: int,
) -> None:
    """Keep every document connected by posting ID in the same split."""

    try:
        assignments = compute_split_groups(
            records,
            holdout_modulus=holdout_modulus,
        )
    except ValueError as exc:
        raise GoldsetPreparationError(str(exc)) from exc
    for record in records:
        record.update(assignments[str(record["document_sha256"])])


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


def _posting_ids(value: Any, *, plural: bool) -> set[str]:
    if not plural:
        if not isinstance(value, str) or not value.strip():
            raise GoldsetPreparationError(
                "posting_id must be a nonblank string"
            )
        return {value.strip()}

    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GoldsetPreparationError(
                "posting_ids must be a valid JSON array of nonblank strings"
            ) from exc
    if not isinstance(decoded, list) or not decoded:
        raise GoldsetPreparationError(
            "posting_ids must be a non-empty JSON array of nonblank strings"
        )
    normalized: list[str] = []
    for item in decoded:
        if not isinstance(item, str) or not item.strip():
            raise GoldsetPreparationError(
                "posting_ids must contain only nonblank strings"
            )
        normalized.append(item.strip())
    if len(normalized) != len(set(normalized)):
        raise GoldsetPreparationError("posting_ids must not contain duplicates")
    return set(normalized)


def _extract_tuning_identities(
    value: Any,
    *,
    inherited_posting_ids: Iterable[str] = (),
) -> tuple[set[str], dict[str, set[str]]]:
    hashes: set[str] = set()
    posting_ids_by_hash: dict[str, set[str]] = {}
    if isinstance(value, dict):
        active_posting_ids = set(inherited_posting_ids)
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in {"posting_id", "posting_ids"}:
                active_posting_ids.update(
                    _posting_ids(item, plural=normalized_key == "posting_ids")
                )
        for key, item in value.items():
            if str(key).casefold() in {
                "sha256",
                "document_sha256",
                "content_sha256",
                "source_sha256",
            }:
                candidate = str(item or "").strip().lower()
                if HEX64_RE.fullmatch(candidate):
                    hashes.add(candidate)
                    posting_ids_by_hash.setdefault(candidate, set()).update(
                        active_posting_ids
                    )
        for item in value.values():
            child_hashes, child_postings = _extract_tuning_identities(
                item,
                inherited_posting_ids=active_posting_ids,
            )
            hashes.update(child_hashes)
            for digest, posting_ids in child_postings.items():
                posting_ids_by_hash.setdefault(digest, set()).update(posting_ids)
    elif isinstance(value, list):
        for item in value:
            child_hashes, child_postings = _extract_tuning_identities(
                item,
                inherited_posting_ids=inherited_posting_ids,
            )
            hashes.update(child_hashes)
            for digest, posting_ids in child_postings.items():
                posting_ids_by_hash.setdefault(digest, set()).update(posting_ids)
    return hashes, posting_ids_by_hash


def load_tuning_identities(
    paths: Iterable[Path],
) -> tuple[set[str], dict[str, set[str]]]:
    hashes: set[str] = set()
    posting_ids_by_hash: dict[str, set[str]] = {}
    for path in paths:
        if path.suffix.lower() == ".csv":
            value: Any = _load_rows(path)
        elif path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            value = None
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                candidate = line.strip().lower()
                if HEX64_RE.fullmatch(candidate):
                    hashes.add(candidate)
        if value is None:
            continue
        path_hashes, path_postings = _extract_tuning_identities(value)
        hashes.update(path_hashes)
        for digest, posting_ids in path_postings.items():
            posting_ids_by_hash.setdefault(digest, set()).update(posting_ids)
    return hashes, posting_ids_by_hash


def load_tuning_hashes(paths: Iterable[Path]) -> set[str]:
    return load_tuning_identities(paths)[0]


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
        posting_id = str(row.get("posting_id") or "").strip()
        if not posting_id:
            raise GoldsetPreparationError(
                f"benchmark case {case_id} must have a nonblank posting_id"
            )
        if case_id in output:
            raise GoldsetPreparationError(f"duplicate benchmark case_id: {case_id}")
        output[case_id] = {**row, "posting_id": posting_id}
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
        "document_sha256": str(record["document_sha256"]),
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

    _assign_split_groups(records, holdout_modulus=holdout_modulus)
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
        "split_key": SPLIT_KEY,
        "split_group_count": len(
            {str(record["split_group_sha256"]) for record in records}
        ),
        "posting_id_cross_split_overlap_count": 0,
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


def exclude_tuning_overlap_candidates(
    benchmark_payload: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    tuning_hashes: Iterable[str],
    tuning_posting_ids_by_hash: Mapping[str, Iterable[str]] | None = None,
    source_input_sha256: Mapping[str, Any] | None = None,
    verify_files: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Drop explicitly identified tuning documents before blind review seeding.

    ``build_workflow`` remains fail-closed by default. This helper is an explicit,
    auditable pre-filter for a live collection window that contains a small number
    of known tuning documents. It removes every case sharing an excluded content
    digest from both the benchmark envelope and the private source index. Tuning
    documents outside the candidate window must carry posting IDs so their sibling
    attachments can be linked into the same exclusion component.
    """

    benchmark_cases = _benchmark_cases(benchmark_payload)
    normalized_tuning_hashes = {
        require_sha256(value, field="tuning document sha256")
        for value in tuning_hashes
    }
    normalized_tuning_postings: dict[str, set[str]] = {}
    for raw_digest, raw_posting_ids in (
        tuning_posting_ids_by_hash or {}
    ).items():
        digest = require_sha256(raw_digest, field="tuning document sha256")
        if isinstance(raw_posting_ids, str):
            raise GoldsetPreparationError(
                "tuning_posting_ids_by_hash values must be iterables of strings"
            )
        try:
            posting_id_values = list(raw_posting_ids)
        except TypeError as exc:
            raise GoldsetPreparationError(
                "tuning_posting_ids_by_hash values must be iterables of strings"
            ) from exc
        if any(
            not isinstance(value, str) or not value.strip()
            for value in posting_id_values
        ):
            raise GoldsetPreparationError(
                "tuning posting IDs must contain only nonblank strings"
            )
        normalized_values = [value.strip() for value in posting_id_values]
        if len(normalized_values) != len(set(normalized_values)):
            raise GoldsetPreparationError(
                "tuning posting IDs must not contain duplicates"
            )
        posting_ids = set(normalized_values)
        normalized_tuning_postings.setdefault(digest, set()).update(posting_ids)
        normalized_tuning_hashes.add(digest)
    normalized_tuning_identities = [
        {
            "document_sha256": digest,
            "posting_ids": sorted(normalized_tuning_postings.get(digest, set())),
        }
        for digest in sorted(normalized_tuning_hashes)
    ]
    normalized_source_inputs: dict[str, Any] = {}
    if source_input_sha256 is not None:
        normalized_source_inputs = {
            "benchmark_json": require_sha256(
                source_input_sha256.get("benchmark_json"),
                field="source_input_sha256.benchmark_json",
            ),
            "source_index": require_sha256(
                source_input_sha256.get("source_index"),
                field="source_input_sha256.source_index",
            ),
        }
        tuning_manifest_hashes = source_input_sha256.get("tuning_manifests")
        if not isinstance(tuning_manifest_hashes, list):
            raise GoldsetPreparationError(
                "source_input_sha256.tuning_manifests must be a list"
            )
        normalized_source_inputs["tuning_manifests"] = [
            require_sha256(
                value,
                field=f"source_input_sha256.tuning_manifests[{index}]",
            )
            for index, value in enumerate(tuning_manifest_hashes)
        ]
    normalized_rows: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for row in source_rows:
        normalized = _normalize_source_row(
            row,
            benchmark_case_ids=set(benchmark_cases),
            verify_files=verify_files,
        )
        case_id = normalized["case_id"]
        if case_id in seen_case_ids:
            raise GoldsetPreparationError(f"duplicate source-index case_id: {case_id}")
        seen_case_ids.add(case_id)
        normalized_rows.append(normalized)
    missing = sorted(set(benchmark_cases) - seen_case_ids)
    if missing:
        raise GoldsetPreparationError(
            f"source index is missing {len(missing)} benchmark case(s)"
        )

    component_inputs: dict[str, dict[str, Any]] = {}
    for row in normalized_rows:
        digest = row["document_sha256"]
        component = component_inputs.setdefault(
            digest,
            {"document_sha256": digest, "posting_ids": set()},
        )
        posting_id = str(
            benchmark_cases[row["case_id"]].get("posting_id") or ""
        ).strip()
        if posting_id:
            component["posting_ids"].add(posting_id)
    source_document_hashes = set(component_inputs)
    out_of_window_tuning_hashes = normalized_tuning_hashes - source_document_hashes
    unidentified_out_of_window_hashes = sorted(
        digest
        for digest in out_of_window_tuning_hashes
        if not normalized_tuning_postings.get(digest)
    )
    if unidentified_out_of_window_hashes:
        raise GoldsetPreparationError(
            "tuning documents outside the candidate corpus require posting_id "
            "or posting_ids for component-wide exclusion "
            f"({len(unidentified_out_of_window_hashes)} missing)"
        )
    for digest in normalized_tuning_hashes:
        component = component_inputs.setdefault(
            digest,
            {"document_sha256": digest, "posting_ids": set()},
        )
        component["posting_ids"].update(
            normalized_tuning_postings.get(digest, set())
        )
    split_inputs = [
        {
            "document_sha256": digest,
            "posting_ids": sorted(component["posting_ids"]),
        }
        for digest, component in sorted(component_inputs.items())
    ]
    try:
        component_assignments = compute_split_groups(
            split_inputs,
            holdout_modulus=5,
        )
    except ValueError as exc:
        raise GoldsetPreparationError(str(exc)) from exc
    excluded_group_hashes = {
        component_assignments[digest]["split_group_sha256"]
        for digest in normalized_tuning_hashes.intersection(component_assignments)
    }
    excluded_document_hashes = {
        digest
        for digest, assignment in component_assignments.items()
        if assignment["split_group_sha256"] in excluded_group_hashes
    }
    excluded_rows = [
        row
        for row in normalized_rows
        if row["document_sha256"] in excluded_document_hashes
    ]
    excluded_case_ids = {row["case_id"] for row in excluded_rows}
    remaining_rows = [
        row for row in normalized_rows if row["case_id"] not in excluded_case_ids
    ]
    if not remaining_rows:
        raise GoldsetPreparationError("tuning-overlap exclusion removed every candidate")

    filtered_payload = dict(benchmark_payload)
    filtered_payload["cases"] = [
        dict(row)
        for row in benchmark_payload.get("cases", [])
        if isinstance(row, dict)
        and str(row.get("case_id") or "").strip() not in excluded_case_ids
    ]
    remaining_document_hashes = {row["document_sha256"] for row in remaining_rows}
    audit = {
        "policy": "component_wide_known_tuning_exclusion_before_blind_review",
        "original_case_count": len(normalized_rows),
        "remaining_case_count": len(remaining_rows),
        "excluded_case_count": len(excluded_rows),
        "original_unique_document_count": len(source_document_hashes),
        "remaining_unique_document_count": len(remaining_document_hashes),
        "excluded_unique_document_count": len(
            {row["document_sha256"] for row in excluded_rows}
        ),
        "excluded_case_ids": sorted(excluded_case_ids),
        "excluded_document_sha256": sorted(
            {row["document_sha256"] for row in excluded_rows}
        ),
        "excluded_split_group_sha256": sorted(excluded_group_hashes),
        "excluded_posting_ids": sorted(
            {
                str(benchmark_cases[row["case_id"]].get("posting_id") or "").strip()
                for row in excluded_rows
                if str(
                    benchmark_cases[row["case_id"]].get("posting_id") or ""
                ).strip()
            }
        ),
        "tuning_hash_count_checked": len(normalized_tuning_hashes),
        "tuning_identities": normalized_tuning_identities,
        "tuning_document_set_sha256": sha256_bytes(
            canonical_json_bytes(sorted(normalized_tuning_hashes))
        ),
        "tuning_identity_ledger_sha256": sha256_bytes(
            canonical_json_bytes(normalized_tuning_identities)
        ),
        "tuning_document_with_manifest_posting_id_count": sum(
            bool(normalized_tuning_postings.get(digest))
            for digest in normalized_tuning_hashes
        ),
        "tuning_manifest_posting_id_count": len(
            {
                posting_id
                for digest in normalized_tuning_hashes
                for posting_id in normalized_tuning_postings.get(digest, set())
            }
        ),
        "out_of_window_tuning_document_count": len(out_of_window_tuning_hashes),
        "component_graph_document_count": len(component_inputs),
        "input_artifacts_attested": source_input_sha256 is not None,
        "input_artifact_sha256": normalized_source_inputs,
        "remaining_benchmark_payload_sha256": sha256_bytes(
            canonical_json_bytes(filtered_payload)
        ),
        "remaining_source_index_sha256": sha256_bytes(
            canonical_json_bytes(remaining_rows)
        ),
    }
    audit["audit_sha256"] = sha256_bytes(canonical_json_bytes(audit))
    return filtered_payload, remaining_rows, audit


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
    try:
        expected_assignments = compute_split_groups(
            records,
            holdout_modulus=holdout_modulus,
        )
    except ValueError as exc:
        raise GoldsetPreparationError(str(exc)) from exc
    all_case_ids: list[str] = []
    record_by_id: dict[str, Mapping[str, Any]] = {}
    for row, digest in zip(records, digests):
        require_sha256(digest, field="document_sha256")
        item_id = str(row.get("item_id") or "")
        if item_id != f"nrg-{digest}":
            raise GoldsetPreparationError("manifest item ID/content digest mismatch")
        split_group_digest = require_sha256(
            row.get("split_group_sha256"),
            field="split_group_sha256",
        )
        expected_assignment = expected_assignments[digest]
        if (
            split_group_digest != expected_assignment["split_group_sha256"]
            or row.get("split") != expected_assignment["split"]
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

    posting_assignment: dict[str, tuple[str, str]] = {}
    for row in records:
        assignment = (str(row["split_group_sha256"]), str(row["split"]))
        for posting_id in row.get("posting_ids") or []:
            normalized = str(posting_id or "").strip()
            if not normalized:
                continue
            previous = posting_assignment.setdefault(normalized, assignment)
            if previous != assignment:
                raise GoldsetPreparationError(
                    "posting ID leaked across validation/holdout split groups"
                )

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
            expected_fields = (
                ("document_sha256",)
                if name.startswith("reviewer_")
                else ("split", "document_sha256", "local_document_path")
            )
            for field in expected_fields:
                if str(row.get(field) or "") != str(record.get(field) or ""):
                    raise GoldsetPreparationError(
                        f"{name} {field} does not match manifest"
                    )
            if name.startswith("reviewer_"):
                if list(row) != REVIEWER_FIELDS:
                    raise GoldsetPreparationError(
                        f"{name} template schema does not match the blind reviewer schema"
                    )
                if set(row).intersection(
                    {"split", "local_document_path", "source_url"}
                ):
                    raise GoldsetPreparationError(
                        f"{name} template exposes reviewer-forbidden metadata"
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
    if summary.get("workflow_version") != WORKFLOW_VERSION:
        raise GoldsetPreparationError("workflow version mismatch")
    if summary.get("split_key") != SPLIT_KEY:
        raise GoldsetPreparationError("split key mismatch")
    if summary.get("automatic_predictions_are_gold") is not False:
        raise GoldsetPreparationError("automatic predictions must never be marked gold")
    if summary.get("is_gold") is not False:
        raise GoldsetPreparationError("an unreviewed workflow must not be marked gold")
    if int(summary.get("benchmark_case_count") or -1) != len(all_case_ids):
        raise GoldsetPreparationError("benchmark case count integrity mismatch")
    if int(summary.get("unique_document_count") or -1) != len(records):
        raise GoldsetPreparationError("unique document count integrity mismatch")
    if int(summary.get("split_group_count") or -1) != len(
        {str(row["split_group_sha256"]) for row in records}
    ):
        raise GoldsetPreparationError("split group count integrity mismatch")
    if summary.get("posting_id_cross_split_overlap_count") != 0:
        raise GoldsetPreparationError("posting split leakage summary is invalid")
    expected_digest = sha256_bytes(canonical_json_bytes(records))
    if summary.get("records_sha256") != expected_digest:
        raise GoldsetPreparationError("manifest records integrity digest mismatch")


def _write_csv(path: Path, fields: list[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_workflow(
    workflow: Mapping[str, Any],
    output_dir: Path,
    *,
    exclusion_audit: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    validate_workflow(workflow)
    output_dir = validate_local_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "goldset_review_manifest.local.json"
    reviewer_a_path = output_dir / "reviewer_a.local.csv"
    reviewer_b_path = output_dir / "reviewer_b.local.csv"
    adjudication_path = output_dir / "adjudication.local.csv"
    exclusion_path = output_dir / "do_not_tune.local.csv"
    integrity_path = output_dir / "integrity.local.json"
    candidate_exclusions_path = output_dir / "candidate_exclusions.local.json"

    manifest_summary = dict(workflow["summary"])
    final_exclusion_audit: dict[str, Any] | None = None
    exclusion_binding: dict[str, Any] = {
        "applied": False,
        "version": CANDIDATE_EXCLUSION_AUDIT_VERSION,
        "audit_sha256": None,
    }
    if exclusion_audit is not None:
        sealed_audit = dict(exclusion_audit)
        declared_audit_sha256 = require_sha256(
            sealed_audit.pop("audit_sha256", None),
            field="candidate exclusion audit_sha256",
        )
        if sha256_bytes(canonical_json_bytes(sealed_audit)) != declared_audit_sha256:
            raise GoldsetPreparationError("candidate exclusion audit digest mismatch")
        if sealed_audit.get("input_artifacts_attested") is not True:
            raise GoldsetPreparationError(
                "candidate exclusion audit must attest its source inputs"
            )
        sealed_audit["audit_version"] = CANDIDATE_EXCLUSION_AUDIT_VERSION
        sealed_audit["remaining_records_sha256"] = workflow["summary"][
            "records_sha256"
        ]
        final_audit_sha256 = sha256_bytes(canonical_json_bytes(sealed_audit))
        final_exclusion_audit = {
            **sealed_audit,
            "audit_sha256": final_audit_sha256,
        }
        exclusion_binding = {
            "applied": True,
            "version": CANDIDATE_EXCLUSION_AUDIT_VERSION,
            "audit_sha256": final_audit_sha256,
        }
        manifest_summary["benchmark_payload_sha256"] = sealed_audit[
            "remaining_benchmark_payload_sha256"
        ]
    manifest_summary["candidate_exclusion_audit"] = exclusion_binding

    manifest_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": manifest_summary,
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
    if final_exclusion_audit is not None:
        candidate_exclusions_path.write_text(
            json.dumps(final_exclusion_audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifact_paths["candidate_exclusions"] = candidate_exclusions_path
    integrity = {
        "seed_integrity_version": SEED_INTEGRITY_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "records_sha256": workflow["summary"]["records_sha256"],
        "artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(artifact_paths.items())
        },
        "artifact_count": len(artifact_paths),
        "local_only": True,
        "automatic_predictions_are_gold": False,
        "candidate_exclusion_audit": exclusion_binding,
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
        help=(
            "CSV/JSON/text file containing tuning document SHA-256 values; repeatable. "
            "CSV/JSON rows should include posting_id or posting_ids when the tuning "
            "document may be outside the candidate window"
        ),
    )
    parser.add_argument(
        "--exclude-tuning-overlap",
        action="store_true",
        help=(
            "explicitly remove known tuning-document digest overlaps from both "
            "benchmark cases and source index, writing a local exclusion audit"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    benchmark_path = Path(args.benchmark_json).resolve()
    source_index_path = Path(args.source_index).resolve()
    output_dir = validate_local_output_dir(Path(args.output_dir))
    tuning_manifest_paths = [
        Path(value).resolve() for value in args.tuning_manifest
    ]
    if tuning_manifest_paths and not args.exclude_tuning_overlap:
        raise GoldsetPreparationError(
            "--tuning-manifest requires --exclude-tuning-overlap so posting "
            "components and the exclusion audit cannot be bypassed"
        )
    source_input_sha256 = {
        "benchmark_json": sha256_file(benchmark_path),
        "source_index": sha256_file(source_index_path),
        "tuning_manifests": sorted(
            sha256_file(path) for path in tuning_manifest_paths
        ),
    }
    benchmark_payload = json.loads(benchmark_path.read_text(encoding="utf-8-sig"))
    if not isinstance(benchmark_payload, dict):
        raise GoldsetPreparationError("benchmark JSON must be an object")
    source_rows = load_source_index(source_index_path)
    tuning_hashes, tuning_posting_ids_by_hash = load_tuning_identities(
        tuning_manifest_paths
    )
    exclusion_audit: dict[str, Any] | None = None
    if args.exclude_tuning_overlap:
        if not tuning_manifest_paths or not tuning_hashes:
            raise GoldsetPreparationError(
                "--exclude-tuning-overlap requires a non-empty tuning manifest"
            )
        benchmark_payload, source_rows, exclusion_audit = (
            exclude_tuning_overlap_candidates(
                benchmark_payload,
                source_rows,
                tuning_hashes=tuning_hashes,
                tuning_posting_ids_by_hash=tuning_posting_ids_by_hash,
                source_input_sha256=source_input_sha256,
                verify_files=True,
            )
        )
    workflow = build_workflow(
        benchmark_payload,
        source_rows,
        holdout_modulus=int(args.holdout_modulus),
        tuning_hashes=tuning_hashes,
        verify_files=True,
    )
    current_source_input_sha256 = {
        "benchmark_json": sha256_file(benchmark_path),
        "source_index": sha256_file(source_index_path),
        "tuning_manifests": sorted(
            sha256_file(path) for path in tuning_manifest_paths
        ),
    }
    if current_source_input_sha256 != source_input_sha256:
        raise GoldsetPreparationError(
            "goldset input artifacts changed during preparation"
        )
    paths = write_workflow(
        workflow,
        output_dir,
        exclusion_audit=exclusion_audit,
    )
    print(json.dumps(workflow["summary"], ensure_ascii=False, indent=2))
    for name, path in paths.items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
