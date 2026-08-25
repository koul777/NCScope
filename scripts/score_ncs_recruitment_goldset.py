from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.ncs_recruitment_split import SPLIT_KEY, compute_split_groups
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FINAL_DIR = ROOT / "tmp" / "ncs_recruitment_goldset" / "final"
DEFAULT_SOURCE_DIR = ROOT / "tmp" / "ncs_recruitment_goldset" / "source_documents"
DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "ncs_recruitment_goldset" / "score"
REFERENCE_SCHEMA_VERSION = "ncs_recruitment_adjudicated_reference_v2"
SCORE_SCHEMA_VERSION = "ncs_recruitment_reference_score_v2"
SEED_INTEGRITY_VERSION = "ncs_recruitment_seed_integrity_v2"
CANDIDATE_EXCLUSION_AUDIT_VERSION = (
    "ncs_recruitment_candidate_exclusion_audit_v1"
)
USAGE_POLICY = "evaluation_only_no_training_or_rule_tuning"
AI_EVALUATION_BASIS = "independent_ai_agent_adjudicated_reference_not_human_gold"
HUMAN_EVALUATION_BASIS = "independent_human_double_review_adjudicated_gold"
HUMAN_ATTESTATION = "confirmed_independent_human_review"
HUMAN_ATTESTATION_VERSION = "independent-human-review-v1"
MAPPING_STATES = {
    "official_current",
    "legacy_or_nonstandard",
    "self_developed",
    "not_stated",
    "ambiguous",
    "unreadable",
}
SPLITS = ("gold_validation", "gold_holdout")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DETAIL_CODE_RE = re.compile(r"^[0-9]{8}$")

FINAL_CSV_FIELDS = [
    "item_id",
    "split",
    "document_sha256",
    "case_ids_json",
    "posting_ids_json",
    "split_group_sha256",
    "mapping_state",
    "detail_names_json",
    "detail_codes_json",
    "evidence_json",
    "resolution_type",
    "reviewer_a_id",
    "reviewer_b_id",
    "adjudicator_id",
    "evaluation_basis",
    "is_human_gold",
    "is_gold_accuracy",
    "usage_policy",
]

ERROR_CSV_FIELDS = [
    "item_id",
    "split",
    "document_sha256",
    "parse_status",
    "reference_mapping_state",
    "predicted_mapping_state",
    "reference_detail_names_json",
    "predicted_detail_names_json",
    "reference_detail_codes_json",
    "predicted_detail_codes_json",
    "reference_detail_pairs_json",
    "predicted_detail_pairs_json",
    "state_match",
    "name_exact",
    "code_exact",
    "pair_exact",
    "document_exact",
    "error_types_json",
    "error_message",
]

ParseFunction = Callable[[Path, bytes], Mapping[str, Any]]


class GoldsetScoringError(ValueError):
    """Raised when the scorer cannot prove an evaluation invariant."""


class GoldsetScoringInfrastructureError(GoldsetScoringError):
    """Raised when an external parser run is unavailable and the score is invalid."""


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


def local_runtime_attestation() -> dict[str, Any]:
    source_paths = {
        "app_main": ROOT / "app" / "main.py",
        "kordoc_parser": ROOT / "app" / "services" / "kordoc_parser.py",
        "ncs_mcp_client": ROOT / "app" / "services" / "ncs_mcp_client.py",
        "request_budget": ROOT / "app" / "services" / "request_budget.py",
        "official_detail_catalog": (
            ROOT / "app" / "data" / "ncs_detail_catalog.json"
        ),
        "kordoc_local_runner": ROOT / "scripts" / "kordoc_parse.mjs",
        "kordoc_serverless_bridge": ROOT / "api" / "kordoc-parse.js",
        "package_json": ROOT / "package.json",
        "package_lock": (
            ROOT / "app" / "data" / "node_package_lock_attestation.json"
        ),
        "vercel_config": (
            ROOT / "app" / "data" / "vercel_config_attestation.json"
        ),
    }
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock_attestation = json.loads(
        source_paths["package_lock"].read_text(encoding="utf-8")
    )
    if not isinstance(lock_attestation, dict):
        raise GoldsetScoringError("Kordoc package lock attestation is invalid")
    package_lock_git_text_sha256 = str(
        lock_attestation.get("package_lock_git_text_sha256") or ""
    ).strip()
    package_version = str(
        lock_attestation.get("package_version") or ""
    ).strip()
    package_integrity = str(
        lock_attestation.get("package_integrity") or ""
    ).strip()
    if (
        lock_attestation.get("schema_version")
        != "ncscope_node_lock_attestation_v1"
        or lock_attestation.get("package_name") != "kordoc"
        or not re.fullmatch(r"[0-9a-f]{64}", package_lock_git_text_sha256)
        or not re.fullmatch(r"\d+\.\d+\.\d+", package_version)
        or not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", package_integrity)
    ):
        raise GoldsetScoringError("Kordoc package lock attestation is invalid")
    vercel_config_attestation = json.loads(
        source_paths["vercel_config"].read_text(encoding="utf-8")
    )
    if not isinstance(vercel_config_attestation, dict):
        raise GoldsetScoringError("Vercel config attestation is invalid")
    vercel_config_git_text_sha256 = str(
        vercel_config_attestation.get("vercel_config_git_text_sha256") or ""
    ).strip()
    if (
        vercel_config_attestation.get("schema_version")
        != "ncscope_vercel_config_attestation_v1"
        or not re.fullmatch(r"[0-9a-f]{64}", vercel_config_git_text_sha256)
    ):
        raise GoldsetScoringError("Vercel config attestation is invalid")
    package_lock_text = (ROOT / "package-lock.json").read_text(encoding="utf-8")
    if sha256_bytes(package_lock_text.encode("utf-8")) != package_lock_git_text_sha256:
        raise GoldsetScoringError("Node package lock attestation is stale")
    package_lock = json.loads(package_lock_text)
    package_entry = (package_lock.get("packages") or {}).get("node_modules/kordoc")
    if not isinstance(package_entry, dict) or (
        str(package_entry.get("version") or "").strip() != package_version
        or str(package_entry.get("integrity") or "").strip()
        != package_integrity
    ):
        raise GoldsetScoringError("Kordoc package lock attestation is stale")
    vercel_config_text = (ROOT / "vercel.json").read_text(encoding="utf-8")
    if (
        sha256_bytes(vercel_config_text.encode("utf-8"))
        != vercel_config_git_text_sha256
    ):
        raise GoldsetScoringError("Vercel config attestation is stale")
    attestation = {
        "schema_version": "ncscope_evaluation_runtime_attestation_v2",
        "app_version": str(package.get("version") or "").strip(),
        "source_artifact_sha256": {
            name: sha256_file(path) for name, path in source_paths.items()
        },
        "parser_contract": {
            "schema_version": "ncscope_kordoc_build_contract_v1",
            "selection_policy": "local_node_then_authenticated_bridge_v1",
            "package_name": "kordoc",
            "package_version": package_version,
            "package_integrity": package_integrity,
            "offline_required": True,
        },
    }
    attestation["runtime_bundle_sha256"] = sha256_bytes(
        canonical_json_bytes(attestation)
    )
    return attestation


def local_scorer_source_artifact_sha256() -> dict[str, str]:
    return {
        "score_script": sha256_file(Path(__file__).resolve()),
        "split_contract": sha256_file(
            ROOT / "scripts" / "ncs_recruitment_split.py"
        ),
    }


def _require_sha256(value: Any, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if not HEX64_RE.fullmatch(digest):
        raise GoldsetScoringError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoldsetScoringError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise GoldsetScoringError(f"{label} must be a JSON object")
    return payload


def _read_csv(
    path: Path,
    *,
    label: str,
    fields: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    expected_fields = list(fields) if fields is not None else FINAL_CSV_FIELDS
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != expected_fields:
                raise GoldsetScoringError(
                    f"{label} columns do not match the required schema"
                )
            return [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise GoldsetScoringError(f"{label} is not readable CSV: {path}") from exc


def _json_array(value: Any, *, field: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise GoldsetScoringError(f"{field} must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise GoldsetScoringError(f"{field} must be a JSON array")
    return parsed


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _normalized_string_list(
    value: Any, *, field: str, sort_values: bool = True
) -> list[str]:
    if not isinstance(value, list):
        raise GoldsetScoringError(f"{field} must be a list")
    output = [_normalized_text(item) for item in value]
    if any(not item for item in output) or len(output) != len(set(output)):
        raise GoldsetScoringError(f"{field} contains a blank or duplicate value")
    return sorted(output) if sort_values else output


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def validate_local_output_dir(output_dir: Path, *, root: Path | None = None) -> Path:
    resolved = output_dir.resolve()
    repository_root = ROOT if root is None else root.resolve()
    allowed = [
        (repository_root / "tmp").resolve(),
        (repository_root / ".tmp").resolve(),
    ]
    if not any(
        resolved == parent or resolved.is_relative_to(parent) for parent in allowed
    ):
        raise GoldsetScoringError(
            "score artifacts may only be written below repository tmp/ or .tmp/"
        )
    return resolved


def _load_official_detail_pairs() -> dict[str, str]:
    path = ROOT / "app" / "data" / "ncs_detail_catalog.json"
    payload = _read_json_object(path, label="official detail catalog")
    rows = payload.get("details")
    if not isinstance(rows, list):
        raise GoldsetScoringError("official detail catalog has no details list")
    pairs: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("usage_yn") or "").upper() != "Y":
            continue
        code = str(row.get("code") or "").strip()
        name = _normalized_text(row.get("name"))
        if not DETAIL_CODE_RE.fullmatch(code) or not name:
            raise GoldsetScoringError("official detail catalog contains an invalid row")
        if code in pairs and pairs[code] != name:
            raise GoldsetScoringError("official detail catalog contains a duplicate code")
        pairs[code] = name
    if not pairs:
        raise GoldsetScoringError("official detail catalog is empty")
    return pairs


def _validate_reference_record(
    raw: Any,
    *,
    official_pairs: Mapping[str, str],
    item_ids: set[str],
    document_hashes: set[str],
    case_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GoldsetScoringError("reference records must be objects")
    item_id = str(raw.get("item_id") or "").strip()
    digest = _require_sha256(
        raw.get("document_sha256"), field=f"{item_id or '<blank>'}.document_sha256"
    )
    if not item_id or item_id != f"nrg-{digest}" or item_id in item_ids:
        raise GoldsetScoringError("reference item ID is blank, duplicate, or invalid")
    if digest in document_hashes:
        raise GoldsetScoringError("duplicate document digest in final reference")
    split = str(raw.get("split") or "")
    if split not in SPLITS:
        raise GoldsetScoringError(f"{item_id}: invalid validation/holdout split")
    split_group_sha256 = _require_sha256(
        raw.get("split_group_sha256"),
        field=f"{item_id}.split_group_sha256",
    )
    raw_posting_ids = raw.get("posting_ids")
    if not isinstance(raw_posting_ids, list):
        raise GoldsetScoringError(f"{item_id}: posting_ids must be a list")
    posting_ids = [str(value or "").strip() for value in raw_posting_ids]
    if any(not value for value in posting_ids) or len(posting_ids) != len(set(posting_ids)):
        raise GoldsetScoringError(f"{item_id}: posting_ids are blank or duplicated")
    state = str(raw.get("mapping_state") or "")
    if state not in MAPPING_STATES:
        raise GoldsetScoringError(f"{item_id}: invalid mapping_state")
    names = _normalized_string_list(
        raw.get("detail_names"),
        field=f"{item_id}.detail_names",
        sort_values=False,
    )
    codes = _normalized_string_list(
        raw.get("detail_codes"),
        field=f"{item_id}.detail_codes",
        sort_values=False,
    )
    if state == "official_current":
        if not names or len(names) != len(codes):
            raise GoldsetScoringError(
                f"{item_id}: official_current requires paired names and codes"
            )
        if any(official_pairs.get(code) != name for code, name in zip(codes, names)):
            raise GoldsetScoringError(
                f"{item_id}: official detail name/code pair is not current"
            )
    elif state in {"legacy_or_nonstandard", "self_developed"}:
        if not names or codes:
            raise GoldsetScoringError(
                f"{item_id}: {state} requires names and no official codes"
            )
    elif names or codes:
        raise GoldsetScoringError(
            f"{item_id}: {state} requires empty name/code sets"
        )
    raw_case_ids = raw.get("case_ids")
    if not isinstance(raw_case_ids, list) or not raw_case_ids:
        raise GoldsetScoringError(f"{item_id}: case_ids must be a non-empty list")
    normalized_case_ids = [str(value or "").strip() for value in raw_case_ids]
    if any(not value for value in normalized_case_ids):
        raise GoldsetScoringError(f"{item_id}: case_ids contains a blank value")
    if len(normalized_case_ids) != len(set(normalized_case_ids)):
        raise GoldsetScoringError(f"{item_id}: duplicate case_id")
    if case_ids.intersection(normalized_case_ids):
        raise GoldsetScoringError("case_id leaked across final reference records")
    if raw.get("usage_policy") != USAGE_POLICY:
        raise GoldsetScoringError(f"{item_id}: usage policy mismatch")
    for field in ("resolution_type", "reviewer_a_id", "reviewer_b_id"):
        if not str(raw.get(field) or "").strip():
            raise GoldsetScoringError(f"{item_id}: {field} is required")
    reviewer_a_id = str(raw["reviewer_a_id"]).strip()
    reviewer_b_id = str(raw["reviewer_b_id"]).strip()
    if reviewer_a_id == reviewer_b_id:
        raise GoldsetScoringError(f"{item_id}: reviewers must be distinct")
    resolution_type = str(raw["resolution_type"]).strip()
    adjudicator_id = str(raw.get("adjudicator_id") or "").strip()
    if resolution_type == "exact_two_reviewer_consensus":
        if adjudicator_id:
            raise GoldsetScoringError(
                f"{item_id}: exact consensus must not name an adjudicator"
            )
    elif resolution_type == "third_party_adjudication":
        if not adjudicator_id or adjudicator_id in {reviewer_a_id, reviewer_b_id}:
            raise GoldsetScoringError(
                f"{item_id}: third-party adjudication requires a distinct adjudicator"
            )
    else:
        raise GoldsetScoringError(f"{item_id}: invalid resolution_type")
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise GoldsetScoringError(f"{item_id}: evidence is required")
    item_ids.add(item_id)
    document_hashes.add(digest)
    case_ids.update(normalized_case_ids)
    return {
        **raw,
        "item_id": item_id,
        "document_sha256": digest,
        "split": split,
        "split_group_sha256": split_group_sha256,
        "posting_ids": posting_ids,
        "mapping_state": state,
        "detail_names": names,
        "detail_codes": codes,
        "case_ids": normalized_case_ids,
    }


def _validate_provenance(reference: Mapping[str, Any]) -> None:
    provenance = reference.get("review_provenance")
    if not isinstance(provenance, dict):
        raise GoldsetScoringError("reference review provenance is missing")
    reviewers: list[tuple[str, str]] = []
    for slot in ("reviewer_a", "reviewer_b"):
        row = provenance.get(slot)
        if not isinstance(row, dict):
            raise GoldsetScoringError(f"reference {slot} provenance is missing")
        reviewer_id = str(row.get("reviewer_id") or "").strip()
        reviewer_kind = str(row.get("reviewer_kind") or "").strip()
        source = str(row.get("provenance") or "").strip()
        if not reviewer_id or reviewer_kind not in {"human", "ai_agent"} or not source:
            raise GoldsetScoringError(f"reference {slot} provenance is invalid")
        reviewers.append((reviewer_id, reviewer_kind))
    if reviewers[0][0] == reviewers[1][0]:
        raise GoldsetScoringError("reference reviewers must be independent identities")

    evaluation_basis = reference.get("evaluation_basis")
    is_human = reference.get("is_human_gold")
    is_gold_accuracy = reference.get("is_gold_accuracy")
    is_gold = reference.get("is_gold")
    all_human = all(kind == "human" for _reviewer_id, kind in reviewers)
    adjudicator = provenance.get("adjudicator")
    if adjudicator is not None:
        if not isinstance(adjudicator, dict):
            raise GoldsetScoringError("reference adjudicator provenance is invalid")
        adjudicator_id = str(adjudicator.get("reviewer_id") or "").strip()
        adjudicator_kind = str(adjudicator.get("reviewer_kind") or "").strip()
        adjudicator_source = str(adjudicator.get("provenance") or "").strip()
        if (
            not adjudicator_id
            or adjudicator_id in {item[0] for item in reviewers}
            or adjudicator_kind not in {"human", "ai_agent"}
            or not adjudicator_source
        ):
            raise GoldsetScoringError("reference adjudicator provenance is invalid")
        all_human = all_human and adjudicator_kind == "human"
    if all_human:
        expected = (HUMAN_EVALUATION_BASIS, True, True, True)
    else:
        expected = (AI_EVALUATION_BASIS, False, False, False)
    if (evaluation_basis, is_human, is_gold_accuracy, is_gold) != expected:
        raise GoldsetScoringError(
            "reference evaluation basis and human-gold flags are inconsistent"
        )
    expected_attestation = {
        "version": HUMAN_ATTESTATION_VERSION,
        "attested": all_human,
        "statement": HUMAN_ATTESTATION if all_human else "",
    }
    attestation = reference.get("human_gold_attestation")
    if all_human and attestation != expected_attestation:
        raise GoldsetScoringError(
            "human-gold reference attestation is missing or invalid"
        )
    if not all_human and attestation not in (None, expected_attestation):
        raise GoldsetScoringError(
            "AI or mixed reference carries an invalid human-gold attestation"
        )


def _validate_record_provenance(
    reference: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> None:
    provenance = reference["review_provenance"]
    reviewer_a_id = str(provenance["reviewer_a"]["reviewer_id"]).strip()
    reviewer_b_id = str(provenance["reviewer_b"]["reviewer_id"]).strip()
    adjudicator = provenance.get("adjudicator")
    adjudicator_id = (
        str(adjudicator["reviewer_id"]).strip()
        if isinstance(adjudicator, dict)
        else ""
    )
    disagreement_count = 0
    for record in records:
        item_id = record["item_id"]
        if (
            record["reviewer_a_id"] != reviewer_a_id
            or record["reviewer_b_id"] != reviewer_b_id
        ):
            raise GoldsetScoringError(
                f"{item_id}: record reviewer identities do not match provenance"
            )
        is_adjudicated = record["resolution_type"] == "third_party_adjudication"
        if is_adjudicated:
            disagreement_count += 1
            if not adjudicator_id or record.get("adjudicator_id") != adjudicator_id:
                raise GoldsetScoringError(
                    f"{item_id}: record adjudicator does not match provenance"
                )
        elif record.get("adjudicator_id"):
            raise GoldsetScoringError(
                f"{item_id}: consensus record unexpectedly names an adjudicator"
            )
    if bool(disagreement_count) != bool(adjudicator_id):
        raise GoldsetScoringError(
            "reference adjudicator provenance does not match adjudicated records"
        )


def _validate_candidate_exclusion_provenance(
    reference: Mapping[str, Any],
    integrity: Mapping[str, Any],
    source_hashes: Mapping[str, Any],
) -> None:
    reference_provenance = reference.get("candidate_exclusion_provenance")
    integrity_provenance = integrity.get("candidate_exclusion_provenance")
    if reference_provenance != integrity_provenance:
        raise GoldsetScoringError(
            "candidate exclusion provenance does not match final integrity"
        )
    if not isinstance(reference_provenance, dict) or set(
        reference_provenance
    ) != {
        "seed_integrity_version",
        "applied",
        "version",
        "audit_sha256",
    }:
        raise GoldsetScoringError(
            "candidate exclusion provenance is missing or invalid"
        )
    if (
        reference_provenance.get("seed_integrity_version")
        != SEED_INTEGRITY_VERSION
        or reference_provenance.get("version")
        != CANDIDATE_EXCLUSION_AUDIT_VERSION
        or not isinstance(reference_provenance.get("applied"), bool)
    ):
        raise GoldsetScoringError("candidate exclusion provenance is invalid")
    applied = reference_provenance["applied"]
    candidate_source_declared = "candidate_exclusions" in source_hashes
    if applied:
        _require_sha256(
            reference_provenance.get("audit_sha256"),
            field="candidate exclusion provenance audit_sha256",
        )
    elif reference_provenance.get("audit_sha256") is not None:
        raise GoldsetScoringError(
            "unapplied candidate exclusion provenance declares an audit digest"
        )
    if applied != candidate_source_declared:
        raise GoldsetScoringError(
            "candidate exclusion provenance is not bound to source artifacts"
        )


def validate_reference_bundle(
    reference_json_path: Path,
    reference_csv_path: Path,
    integrity_path: Path,
) -> dict[str, Any]:
    """Load and fail-closed validate the final JSON/CSV/integrity bundle."""

    reference = _read_json_object(reference_json_path, label="final reference")
    integrity = _read_json_object(integrity_path, label="final integrity")
    csv_rows = _read_csv(reference_csv_path, label="final reference CSV")
    if reference.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        raise GoldsetScoringError("unexpected final reference schema version")
    if integrity.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        raise GoldsetScoringError("unexpected final integrity schema version")
    if reference.get("usage_policy") != USAGE_POLICY:
        raise GoldsetScoringError("reference usage policy is missing")
    if reference.get("automatic_predictions_are_gold") is not False:
        raise GoldsetScoringError("automatic predictions are marked as reference gold")
    if integrity.get("usage_policy") != USAGE_POLICY or integrity.get("local_only") is not True:
        raise GoldsetScoringError("final integrity local-only/usage policy is invalid")
    if integrity.get("automatic_predictions_are_gold") is not False:
        raise GoldsetScoringError("final integrity permits automatic gold labels")
    _validate_provenance(reference)

    output_hashes = integrity.get("output_artifact_sha256")
    if not isinstance(output_hashes, dict) or set(output_hashes) != {
        "reference_json",
        "reference_csv",
    }:
        raise GoldsetScoringError("final output artifact hash inventory is invalid")
    for name, path in (
        ("reference_json", reference_json_path),
        ("reference_csv", reference_csv_path),
    ):
        expected = _require_sha256(
            output_hashes.get(name), field=f"integrity.output_artifact_sha256.{name}"
        )
        if sha256_file(path) != expected:
            raise GoldsetScoringError(f"{name} integrity hash mismatch")
    source_hashes = integrity.get("source_artifact_sha256")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise GoldsetScoringError("final source artifact provenance hashes are missing")
    for name, digest in source_hashes.items():
        if not str(name or "").strip():
            raise GoldsetScoringError("final source artifact name is blank")
        _require_sha256(digest, field=f"integrity.source_artifact_sha256.{name}")
    _validate_candidate_exclusion_provenance(
        reference,
        integrity,
        source_hashes,
    )

    raw_records = reference.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise GoldsetScoringError("final reference records are empty")
    official_pairs = _load_official_detail_pairs()
    item_ids: set[str] = set()
    document_hashes: set[str] = set()
    case_ids: set[str] = set()
    records = [
        _validate_reference_record(
            raw,
            official_pairs=official_pairs,
            item_ids=item_ids,
            document_hashes=document_hashes,
            case_ids=case_ids,
        )
        for raw in raw_records
    ]
    raw_records_by_id = {
        str(raw["item_id"]): raw
        for raw in raw_records
        if isinstance(raw, dict) and "item_id" in raw
    }
    _validate_record_provenance(reference, records)
    posting_assignments: dict[str, tuple[str, str]] = {}
    for record in records:
        assignment = (record["split_group_sha256"], record["split"])
        for posting_id in record["posting_ids"]:
            previous = posting_assignments.setdefault(posting_id, assignment)
            if previous != assignment:
                raise GoldsetScoringError(
                    "posting ID leaked across validation/holdout split groups"
                )
    if set(record["split"] for record in records) != set(SPLITS):
        raise GoldsetScoringError(
            "final reference must contain non-empty validation and holdout splits"
        )
    records_sha256 = sha256_bytes(canonical_json_bytes(raw_records))
    if reference.get("records_sha256") != records_sha256:
        raise GoldsetScoringError("final reference records SHA-256 mismatch")
    if integrity.get("records_sha256") != records_sha256:
        raise GoldsetScoringError("final integrity records SHA-256 mismatch")
    _require_sha256(reference.get("source_records_sha256"), field="source_records_sha256")
    summary = reference.get("summary")
    if not isinstance(summary, dict):
        raise GoldsetScoringError("final reference summary is missing")
    try:
        holdout_modulus = int(summary.get("holdout_modulus"))
    except (TypeError, ValueError) as exc:
        raise GoldsetScoringError("final reference holdout modulus is invalid") from exc
    if summary.get("split_key") != SPLIT_KEY:
        raise GoldsetScoringError("final reference split key mismatch")
    try:
        expected_assignments = compute_split_groups(
            records,
            holdout_modulus=holdout_modulus,
        )
    except ValueError as exc:
        raise GoldsetScoringError(str(exc)) from exc
    for record in records:
        expected = expected_assignments[record["document_sha256"]]
        if (
            record["split_group_sha256"] != expected["split_group_sha256"]
            or record["split"] != expected["split"]
        ):
            raise GoldsetScoringError(
                "final reference split group is not reproducible"
            )
    if summary.get("split_group_count") != len(
        {record["split_group_sha256"] for record in records}
    ):
        raise GoldsetScoringError("final reference split group count mismatch")
    if summary.get("posting_id_cross_split_overlap_count") != 0:
        raise GoldsetScoringError("final reference posting split overlap is invalid")
    split_counts = dict(Counter(record["split"] for record in records))
    resolution_counts = dict(
        sorted(Counter(record["resolution_type"] for record in records).items())
    )
    disagreement_count = resolution_counts.get("third_party_adjudication", 0)
    if summary.get("record_count") != len(records):
        raise GoldsetScoringError("final reference record count mismatch")
    if summary.get("case_count") != len(case_ids):
        raise GoldsetScoringError("final reference case count mismatch")
    if summary.get("split_counts") != dict(sorted(split_counts.items())):
        raise GoldsetScoringError("final reference split counts mismatch")
    if summary.get("resolution_counts") != resolution_counts:
        raise GoldsetScoringError("final reference resolution counts mismatch")
    if summary.get("disagreement_count") != disagreement_count:
        raise GoldsetScoringError("final reference disagreement count mismatch")
    if integrity.get("evaluation_basis") != reference.get("evaluation_basis"):
        raise GoldsetScoringError("final integrity evaluation basis mismatch")
    for field in ("is_human_gold", "is_gold_accuracy"):
        if integrity.get(field) is not reference.get(field):
            raise GoldsetScoringError(f"final integrity {field} mismatch")
    attestation = reference.get("human_gold_attestation")
    if attestation is not None:
        if integrity.get("human_gold_attestation") != attestation:
            raise GoldsetScoringError(
                "final integrity human-gold attestation mismatch"
            )
        attestation_sha256 = sha256_bytes(canonical_json_bytes(attestation))
        if integrity.get("human_gold_attestation_sha256") != attestation_sha256:
            raise GoldsetScoringError(
                "final integrity human-gold attestation SHA-256 mismatch"
            )

    csv_by_id: dict[str, dict[str, str]] = {}
    for row in csv_rows:
        item_id = str(row.get("item_id") or "")
        if item_id in csv_by_id:
            raise GoldsetScoringError("duplicate item in final reference CSV")
        csv_by_id[item_id] = row
    if set(csv_by_id) != item_ids:
        raise GoldsetScoringError("final reference JSON/CSV coverage mismatch")
    for record in records:
        row = csv_by_id[record["item_id"]]
        raw_record = raw_records_by_id[record["item_id"]]
        expected_scalars = {
            "split": record["split"],
            "document_sha256": record["document_sha256"],
            "split_group_sha256": record["split_group_sha256"],
            "mapping_state": record["mapping_state"],
            "resolution_type": str(record["resolution_type"]),
            "reviewer_a_id": str(record["reviewer_a_id"]),
            "reviewer_b_id": str(record["reviewer_b_id"]),
            "adjudicator_id": str(record.get("adjudicator_id") or ""),
            "evaluation_basis": str(reference["evaluation_basis"]),
            "is_human_gold": _bool_text(bool(reference["is_human_gold"])),
            "is_gold_accuracy": _bool_text(bool(reference["is_gold_accuracy"])),
            "usage_policy": USAGE_POLICY,
        }
        if any(row.get(field) != value for field, value in expected_scalars.items()):
            raise GoldsetScoringError(
                f"{record['item_id']}: final reference JSON/CSV scalar mismatch"
            )
        expected_arrays = {
            # Compare the two sealed serializations exactly before returning the
            # normalized in-memory record. NFKC can legitimately change a
            # source-stated legacy label (for example, halfwidth middle dots),
            # but that normalization must not make identical JSON/CSV outputs
            # appear inconsistent.
            "case_ids_json": raw_record["case_ids"],
            "posting_ids_json": raw_record["posting_ids"],
            "detail_names_json": raw_record["detail_names"],
            "detail_codes_json": raw_record["detail_codes"],
        }
        for field, expected in expected_arrays.items():
            if _json_array(row.get(field), field=f"CSV.{record['item_id']}.{field}") != expected:
                raise GoldsetScoringError(
                    f"{record['item_id']}: final reference JSON/CSV {field} mismatch"
                )
        try:
            evidence = json.loads(row.get("evidence_json") or "")
        except json.JSONDecodeError as exc:
            raise GoldsetScoringError(
                f"{record['item_id']}: CSV evidence_json is invalid"
            ) from exc
        if canonical_json_bytes(evidence) != canonical_json_bytes(record.get("evidence")):
            raise GoldsetScoringError(
                f"{record['item_id']}: final reference JSON/CSV evidence mismatch"
            )
    return {**reference, "records": records}


def index_source_documents(
    source_dir: Path,
    required_hashes: set[str],
    *,
    source_index_path: Path | None = None,
    allow_directory_scan: bool = True,
) -> dict[str, Path]:
    if not source_dir.is_dir():
        raise GoldsetScoringError(f"private source directory does not exist: {source_dir}")
    resolved_source_dir = source_dir.resolve()
    if source_index_path is not None:
        rows = _read_csv(
            source_index_path,
            fields=["case_id", "local_document_path", "document_sha256"],
            label="private source index",
        )
        indexed_paths: dict[str, set[Path]] = {}
        for row in rows:
            digest = _require_sha256(
                row.get("document_sha256"), field="private source index hash"
            )
            if digest not in required_hashes:
                continue
            raw_path = str(row.get("local_document_path") or "").strip()
            if not raw_path:
                raise GoldsetScoringError(
                    f"private source index path is blank for SHA-256 {digest}"
                )
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = resolved_source_dir / candidate
            candidate = candidate.resolve()
            if not candidate.is_relative_to(resolved_source_dir):
                raise GoldsetScoringError(
                    f"private source index path escapes source directory: {candidate}"
                )
            indexed_paths.setdefault(digest, set()).add(candidate)
        missing_index = sorted(required_hashes - set(indexed_paths))
        if missing_index:
            raise GoldsetScoringError(
                "private source index coverage is incomplete for "
                f"{len(missing_index)} document hash(es)"
            )
        matches: dict[str, Path] = {}
        for digest, candidates in sorted(indexed_paths.items()):
            # Duplicate cases may legitimately point to byte-identical copies.
            # Select one deterministically and read only the requested split.
            path = sorted(candidates, key=lambda value: str(value).casefold())[0]
            if path.is_symlink():
                raise GoldsetScoringError(
                    f"private source index contains a symlink: {path}"
                )
            if not path.is_file():
                raise GoldsetScoringError(
                    f"private source document is missing: {path}"
                )
            try:
                actual_digest = sha256_file(path)
            except OSError as exc:
                raise GoldsetScoringError(
                    f"private source document is unreadable: {path}"
                ) from exc
            if actual_digest != digest:
                raise GoldsetScoringError(
                    f"private source document integrity mismatch: {path}"
                )
            matches[digest] = path
        return matches
    if not allow_directory_scan:
        raise GoldsetScoringError(
            "split-only evaluation requires a private source index so unselected "
            "documents are never read"
        )
    matches: dict[str, Path] = {}
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise GoldsetScoringError(f"private source directory contains a symlink: {path}")
        if not path.is_file():
            continue
        try:
            digest = sha256_file(path)
        except OSError as exc:
            raise GoldsetScoringError(f"private source document is unreadable: {path}") from exc
        if digest not in required_hashes:
            continue
        if digest in matches:
            raise GoldsetScoringError(
                f"duplicate private source document for SHA-256 {digest}"
            )
        matches[digest] = path
    missing = sorted(required_hashes - set(matches))
    if missing:
        raise GoldsetScoringError(
            f"private source coverage is incomplete for {len(missing)} document hash(es)"
        )
    return matches


def _predict_from_parse_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise GoldsetScoringError("parse-review payload has no fields object")
    raw_rows = fields.get("ncs_detail_mapping_states")
    if not isinstance(raw_rows, list):
        raise GoldsetScoringError("parse-review payload has no detail mapping-state list")
    mapping_states: list[str] = []
    exact_names: set[str] = set()
    exact_codes: set[str] = set()
    exact_pairs: set[tuple[str, str]] = set()
    unresolved_names: set[str] = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise GoldsetScoringError(f"parse mapping row {index} is not an object")
        state = str(raw.get("mappingState") or "").strip()
        if not state:
            raise GoldsetScoringError(f"parse mapping row {index} has no mappingState")
        mapping_states.append(state)
        source_name = _normalized_text(raw.get("sourceName"))
        if state == "official_current_exact":
            names = _normalized_string_list(
                raw.get("officialDetailNames"),
                field=f"parse row {index} official names",
                sort_values=False,
            )
            codes = _normalized_string_list(
                raw.get("officialDetailCodes"),
                field=f"parse row {index} official codes",
                sort_values=False,
            )
            if not names or len(names) != len(codes) or any(
                not DETAIL_CODE_RE.fullmatch(code) for code in codes
            ):
                raise GoldsetScoringError(
                    f"parse mapping row {index} has invalid official name/code pairs"
                )
            exact_names.update(names)
            exact_codes.update(codes)
            exact_pairs.update(zip(names, codes))
        elif state in {
            "source_declared_self_developed",
            "not_in_current_official_catalog",
        }:
            if not source_name:
                raise GoldsetScoringError(
                    f"parse mapping row {index} has no unresolved source name"
                )
            unresolved_names.add(source_name)

    state_set = set(mapping_states)
    if not mapping_states:
        absence_state = str(fields.get("ncs_detail_absence_state") or "")
        absence_reason = str(fields.get("ncs_detail_absence_reason") or "")
        if any(
            marker in f"{absence_state};{absence_reason}"
            for marker in (
                "ocr_required",
                "empty_parser_output",
                "extraction_failure",
                "unreadable",
            )
        ):
            predicted_state = "unreadable"
        elif fields.get("ncs_detail_candidates"):
            predicted_state = "ambiguous"
        else:
            predicted_state = "not_stated"
    elif state_set == {"official_current_exact"}:
        predicted_state = "official_current"
    elif state_set == {"source_declared_self_developed"}:
        predicted_state = "self_developed"
    elif state_set == {"not_in_current_official_catalog"}:
        predicted_state = "legacy_or_nonstandard"
    else:
        predicted_state = "ambiguous"
    predicted_names = exact_names | unresolved_names
    return {
        "mapping_state": predicted_state,
        "detail_names": sorted(predicted_names),
        "detail_codes": sorted(exact_codes),
        "detail_pairs": [list(pair) for pair in sorted(exact_pairs)],
        "raw_mapping_states": sorted(state_set),
    }


def _validate_scoring_parser_execution_rows(
    executions: Any,
    *,
    runtime_attestation: Mapping[str, Any],
    allowed_modes: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(executions, list) or not executions:
        raise GoldsetScoringInfrastructureError(
            "parse-review parser execution attestation is missing"
        )
    if allowed_modes != {"local_node_subprocess", "builtin_plain_text"}:
        raise GoldsetScoringInfrastructureError(
            "parse-review scoring parser policy is invalid"
        )
    runtime_bundle_sha256 = runtime_attestation.get("runtime_bundle_sha256")
    parser_contract = runtime_attestation.get("parser_contract")
    if not isinstance(parser_contract, Mapping):
        raise GoldsetScoringInfrastructureError(
            "parse-review runtime parser contract is invalid"
        )
    expected_kordoc_version = parser_contract.get("package_version")
    normalized: list[dict[str, Any]] = []
    for execution in executions:
        if (
            not isinstance(execution, dict)
            or execution.get("schema_version")
            != "ncscope_parser_execution_v1"
            or execution.get("role") != "selected"
            or execution.get("runtime_bundle_sha256")
            != runtime_bundle_sha256
        ):
            raise GoldsetScoringInfrastructureError(
                "parse-review parser execution attestation is invalid"
            )
        parser = str(execution.get("parser") or "").strip()
        mode = str(execution.get("mode") or "").strip()
        if mode not in allowed_modes:
            raise GoldsetScoringInfrastructureError(
                "parse-review parser execution mode is not allowed for scoring"
            )
        build_identity = execution.get("build_identity")
        if mode == "local_node_subprocess":
            valid = (
                parser == "kordoc"
                and execution.get("parser_version") == expected_kordoc_version
                and re.fullmatch(
                    r"\d+\.\d+\.\d+",
                    str(execution.get("node_version") or ""),
                )
                is not None
                and build_identity == {"kind": "local_source_bundle"}
            )
        else:
            valid = (
                parser == "plain_text"
                and build_identity == {"kind": "python_runtime_bundle"}
                and not execution.get("parser_version")
                and not execution.get("node_version")
            )
        if not valid:
            raise GoldsetScoringInfrastructureError(
                "parse-review parser execution identity is invalid"
            )
        normalized.append(dict(execution))
    return normalized


class LocalParseReviewClient:
    """Small loopback-only client for the local parse-review endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 120.0,
        max_retries: int = 8,
        max_retry_after_seconds: float = 60.0,
    ) -> None:
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise GoldsetScoringError(
                "parse-review base URL must be an explicit loopback HTTP(S) endpoint"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise GoldsetScoringError("parse-review base URL must not contain a path/query")
        if max_retries < 0 or max_retries > 20:
            raise GoldsetScoringError("max retries must be between 0 and 20")
        if max_retry_after_seconds < 0 or max_retry_after_seconds > 300:
            raise GoldsetScoringError(
                "max retry-after seconds must be between 0 and 300"
            )
        self._max_retries = max_retries
        self._max_retry_after_seconds = max_retry_after_seconds
        self._expected_runtime_attestation = local_runtime_attestation()
        self._expected_scorer_sources = local_scorer_source_artifact_sha256()
        self._parser_execution_identities: dict[tuple[str, str], str] = {}
        self._allowed_parser_modes = {
            "local_node_subprocess",
            "builtin_plain_text",
        }
        self._local_only_preflight_verified = False
        self.evaluation_configuration = {
            "parser_endpoint": base_url.rstrip("/") + "/api/jd/parse-review",
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "max_retry_after_seconds": max_retry_after_seconds,
            "server_runtime_attestation": None,
            "scorer_source_artifact_sha256": dict(
                self._expected_scorer_sources
            ),
            "parser_execution_identities": [],
            "allowed_parser_modes": sorted(self._allowed_parser_modes),
        }
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "ncscope-private-reference-scorer/1.0",
                "X-NCScope-Parser-Policy": "local-only",
            },
        )

    def close(self) -> None:
        self._client.close()

    def finalize_runtime_attestation(self) -> dict[str, Any]:
        verified = self.evaluation_configuration.get("server_runtime_attestation")
        if verified is None:
            raise GoldsetScoringInfrastructureError(
                "local parse-review runtime attestation was never verified"
            )
        if verified != self._expected_runtime_attestation:
            raise GoldsetScoringInfrastructureError(
                "local parse-review runtime attestation drifted during evaluation"
            )
        if local_runtime_attestation() != self._expected_runtime_attestation:
            raise GoldsetScoringInfrastructureError(
                "evaluation sources changed during the scoring run"
            )
        if local_scorer_source_artifact_sha256() != self._expected_scorer_sources:
            raise GoldsetScoringInfrastructureError(
                "scorer sources changed during the scoring run"
            )
        return dict(verified)

    def __enter__(self) -> "LocalParseReviewClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def parse(self, path: Path, data: bytes) -> Mapping[str, Any]:
        self._ensure_local_only_preflight()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    "/api/jd/parse-review",
                    headers={"X-NCScope-Parser-Policy": "local-only"},
                    files={"jd_file": (path.name, data, content_type)},
                )
            except httpx.HTTPError as exc:
                raise GoldsetScoringInfrastructureError(
                    f"local parse-review transport failed: {exc}"
                ) from exc
            if response.status_code != 429 or attempt >= self._max_retries:
                break
            try:
                retry_after = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                retry_after = 1.0
            time.sleep(max(0.0, min(self._max_retry_after_seconds, retry_after)))
        if response is None:
            raise GoldsetScoringInfrastructureError(
                "local parse-review did not produce a response"
            )
        if response.is_redirect:
            raise GoldsetScoringInfrastructureError(
                "local parse-review redirect was rejected"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise GoldsetScoringInfrastructureError(
                f"local parse-review returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeError) as exc:
            raise GoldsetScoringInfrastructureError(
                "local parse-review returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise GoldsetScoringInfrastructureError(
                "local parse-review returned a non-object payload"
            )
        runtime_attestation = payload.get("evaluation_runtime_attestation")
        if runtime_attestation != self._expected_runtime_attestation:
            raise GoldsetScoringInfrastructureError(
                "local parse-review runtime attestation does not match the "
                "current evaluation sources"
            )
        self.evaluation_configuration["server_runtime_attestation"] = dict(
            runtime_attestation
        )
        self._validate_parser_executions(payload)
        return payload

    def _ensure_local_only_preflight(self) -> None:
        if self._local_only_preflight_verified:
            return
        try:
            response = self._client.get("/api/jd/parse-review/runtime-policy")
        except httpx.HTTPError as exc:
            raise GoldsetScoringInfrastructureError(
                f"local parse-review policy preflight failed: {exc}"
            ) from exc
        if response.is_redirect or response.status_code != 200:
            raise GoldsetScoringInfrastructureError(
                "local parse-review does not enforce the local-only parser policy"
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeError) as exc:
            raise GoldsetScoringInfrastructureError(
                "local parse-review policy preflight returned invalid JSON"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("evaluation_runtime_attestation")
            != self._expected_runtime_attestation
            or payload.get("supported_parser_policies") != ["local-only"]
            or payload.get("policy_header") != "x-ncscope-parser-policy"
        ):
            raise GoldsetScoringInfrastructureError(
                "local parse-review policy preflight runtime attestation is incompatible"
            )
        self._local_only_preflight_verified = True

    def _validate_parser_executions(self, payload: Mapping[str, Any]) -> None:
        executions = _validate_scoring_parser_execution_rows(
            payload.get("parser_executions"),
            runtime_attestation=self._expected_runtime_attestation,
            allowed_modes=self._allowed_parser_modes,
        )
        for execution in executions:
            parser = str(execution["parser"])
            mode = str(execution["mode"])
            identity_text = json.dumps(
                execution,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity_key = (parser, mode)
            previous = self._parser_execution_identities.setdefault(
                identity_key,
                identity_text,
            )
            if previous != identity_text:
                raise GoldsetScoringInfrastructureError(
                    "parse-review parser build identity drifted during evaluation"
                )
        self.evaluation_configuration["parser_execution_identities"] = [
            json.loads(value)
            for _, value in sorted(self._parser_execution_identities.items())
        ]


def _ratio_pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _label_metrics(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    f1_denominator = (2 * tp) + fp + fn
    return {
        "precision_pct": _ratio_pct(tp, precision_denominator),
        "recall_pct": _ratio_pct(tp, recall_denominator),
        "f1_pct": _ratio_pct(2 * tp, f1_denominator),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision_denominator": precision_denominator,
        "recall_denominator": recall_denominator,
        "f1_denominator": f1_denominator,
        "undefined_metrics": [
            name
            for name, denominator in (
                ("precision_pct", precision_denominator),
                ("recall_pct", recall_denominator),
                ("f1_pct", f1_denominator),
            )
            if denominator == 0
        ],
    }


def _aggregate_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "document_count": 0,
            "parse_success_count": 0,
            "parse_success_pct": None,
            "detail_name": _label_metrics(0, 0, 0),
            "detail_code": _label_metrics(0, 0, 0),
            "detail_pair": _label_metrics(0, 0, 0),
            "mapping_state_accuracy_pct": None,
            "mapping_state_exact_count": 0,
            "document_exact_pct": None,
            "document_exact_count": 0,
        }
    name_tp = name_fp = name_fn = 0
    code_tp = code_fp = code_fn = 0
    pair_tp = pair_fp = pair_fn = 0
    state_matches = document_exact = parse_success = 0
    for row in rows:
        reference_names = set(row["reference_detail_names"])
        predicted_names = set(row["predicted_detail_names"])
        reference_codes = set(row["reference_detail_codes"])
        predicted_codes = set(row["predicted_detail_codes"])
        reference_pairs = {
            tuple(pair) for pair in row["reference_detail_pairs"]
        }
        predicted_pairs = {
            tuple(pair) for pair in row["predicted_detail_pairs"]
        }
        name_tp += len(reference_names & predicted_names)
        name_fp += len(predicted_names - reference_names)
        name_fn += len(reference_names - predicted_names)
        code_tp += len(reference_codes & predicted_codes)
        code_fp += len(predicted_codes - reference_codes)
        code_fn += len(reference_codes - predicted_codes)
        pair_tp += len(reference_pairs & predicted_pairs)
        pair_fp += len(predicted_pairs - reference_pairs)
        pair_fn += len(reference_pairs - predicted_pairs)
        state_matches += int(bool(row["state_match"]))
        document_exact += int(bool(row["document_exact"]))
        parse_success += int(row["parse_status"] == "ok")
    count = len(rows)
    return {
        "document_count": count,
        "parse_success_count": parse_success,
        "parse_success_pct": _ratio_pct(parse_success, count),
        "detail_name": _label_metrics(name_tp, name_fp, name_fn),
        "detail_code": _label_metrics(code_tp, code_fp, code_fn),
        "detail_pair": _label_metrics(pair_tp, pair_fp, pair_fn),
        "mapping_state_accuracy_pct": _ratio_pct(state_matches, count),
        "mapping_state_exact_count": state_matches,
        "document_exact_pct": _ratio_pct(document_exact, count),
        "document_exact_count": document_exact,
    }


def _aggregate_official_current_core(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    eligible = [
        row for row in rows if row["reference_mapping_state"] == "official_current"
    ]
    if not eligible:
        return {
            "eligible_document_count": 0,
            "detail_name": _label_metrics(0, 0, 0),
            "detail_code": _label_metrics(0, 0, 0),
            "detail_pair": _label_metrics(0, 0, 0),
            "mapping_state_accuracy_pct": None,
            "mapping_state_exact_count": 0,
            "document_exact_pct": None,
            "document_exact_count": 0,
        }
    metrics = _aggregate_metrics(eligible)
    return {
        "eligible_document_count": len(eligible),
        "detail_name": metrics["detail_name"],
        "detail_code": metrics["detail_code"],
        "detail_pair": metrics["detail_pair"],
        "mapping_state_accuracy_pct": metrics["mapping_state_accuracy_pct"],
        "mapping_state_exact_count": metrics["mapping_state_exact_count"],
        "document_exact_pct": metrics["document_exact_pct"],
        "document_exact_count": metrics["document_exact_count"],
    }


def score_reference(
    reference: Mapping[str, Any],
    source_paths: Mapping[str, Path],
    parse_document: ParseFunction,
    *,
    include_splits: set[str] | None = None,
) -> dict[str, Any]:
    """Reparse private documents and compare predictions to a sealed reference."""

    raw_records = reference.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise GoldsetScoringError("validated reference records are empty")
    selected_splits = set(SPLITS) if include_splits is None else set(include_splits)
    if not selected_splits or not selected_splits.issubset(SPLITS):
        raise GoldsetScoringError("include_splits must contain only known split names")
    selected_records = [
        record for record in raw_records if record.get("split") in selected_splits
    ]
    if not selected_records:
        raise GoldsetScoringError("selected evaluation splits contain no records")
    results: list[dict[str, Any]] = []
    for record in selected_records:
        digest = str(record["document_sha256"])
        path = source_paths.get(digest)
        if path is None:
            raise GoldsetScoringError(f"source path is missing for {digest}")
        data = path.read_bytes()
        if sha256_bytes(data) != digest:
            raise GoldsetScoringError(
                f"private source document changed after indexing: {digest}"
            )
        parse_status = "ok"
        parse_error = ""
        raw_parse_payload_sha256 = ""
        prediction = {
            "mapping_state": "unreadable",
            "detail_names": [],
            "detail_codes": [],
            "detail_pairs": [],
            "raw_mapping_states": [],
        }
        try:
            payload = parse_document(path, data)
            if not isinstance(payload, Mapping):
                raise GoldsetScoringError("parse function returned a non-object payload")
            raw_parse_payload_sha256 = sha256_bytes(canonical_json_bytes(payload))
            prediction = _predict_from_parse_payload(payload)
        except GoldsetScoringInfrastructureError:
            # A partial run caused by transport/backpressure is not a model
            # error and must never be mixed into accuracy denominators.
            raise
        except Exception as exc:  # Per-document failures belong in the error ledger.
            parse_status = "error"
            parse_error = f"{type(exc).__name__}: {exc}"[:500]
        reference_names = set(record["detail_names"])
        reference_codes = set(record["detail_codes"])
        predicted_names = set(prediction["detail_names"])
        predicted_codes = set(prediction["detail_codes"])
        reference_pairs = set(zip(record["detail_names"], record["detail_codes"]))
        predicted_pairs = {
            (str(pair[0]), str(pair[1])) for pair in prediction["detail_pairs"]
        }
        state_match = prediction["mapping_state"] == record["mapping_state"]
        name_exact = predicted_names == reference_names
        code_exact = predicted_codes == reference_codes
        pair_exact = predicted_pairs == reference_pairs
        exact = (
            parse_status == "ok"
            and state_match
            and name_exact
            and code_exact
            and pair_exact
        )
        error_types: list[str] = []
        if parse_status != "ok":
            error_types.append("parse_error")
        if not state_match:
            error_types.append("mapping_state_mismatch")
        if not name_exact:
            error_types.append("detail_name_mismatch")
        if not code_exact:
            error_types.append("detail_code_mismatch")
        if not pair_exact:
            error_types.append("detail_pair_mismatch")
        results.append(
            {
                "item_id": record["item_id"],
                "split": record["split"],
                "document_sha256": digest,
                "parse_status": parse_status,
                "reference_mapping_state": record["mapping_state"],
                "predicted_mapping_state": prediction["mapping_state"],
                "reference_detail_names": sorted(reference_names),
                "predicted_detail_names": sorted(predicted_names),
                "reference_detail_codes": sorted(reference_codes),
                "predicted_detail_codes": sorted(predicted_codes),
                "reference_detail_pairs": [list(pair) for pair in sorted(reference_pairs)],
                "predicted_detail_pairs": [list(pair) for pair in sorted(predicted_pairs)],
                "state_match": state_match,
                "name_exact": name_exact,
                "code_exact": code_exact,
                "pair_exact": pair_exact,
                "document_exact": exact,
                "error_types": error_types,
                "error_message": parse_error,
                "raw_parse_payload_sha256": raw_parse_payload_sha256,
                "raw_predicted_mapping_states": prediction["raw_mapping_states"],
            }
        )
    by_split = {
        split: _aggregate_metrics([row for row in results if row["split"] == split])
        for split in SPLITS
    }
    errors = [row for row in results if row["error_types"]]
    is_human_gold = reference.get("is_human_gold") is True
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference_schema_version": reference.get("schema_version"),
        "reference_records_sha256": reference.get("records_sha256"),
        "evaluation_basis": reference.get("evaluation_basis"),
        "is_human_gold": is_human_gold,
        "is_gold_accuracy": reference.get("is_gold_accuracy") is True,
        "metrics_are_human_gold_accuracy": is_human_gold,
        "metrics_interpretation": (
            "human_gold_accuracy"
            if is_human_gold
            else "ai_adjudicated_reference_comparison_not_human_gold_accuracy"
        ),
        "automatic_predictions_are_reference_labels": False,
        "evaluation_runtime": {
            "parser_endpoint": "injected_parse_function_unattested",
            "runtime_attested": False,
            "scorer_source_artifact_sha256": {},
        },
        "usage_policy": USAGE_POLICY,
        "evaluated_splits": sorted(selected_splits),
        "summary": {
            "record_count": len(results),
            "error_case_count": len(errors),
            "split_counts": dict(sorted(Counter(row["split"] for row in results).items())),
            "overall": _aggregate_metrics(results),
            "by_split": by_split,
            "official_current_core": _aggregate_official_current_core(results),
            "official_current_core_by_split": {
                split: _aggregate_official_current_core(
                    [row for row in results if row["split"] == split]
                )
                for split in SPLITS
            },
        },
        "error_cases": errors,
        "records": results,
    }


def evaluate_bundle(
    reference_json_path: Path,
    reference_csv_path: Path,
    integrity_path: Path,
    source_dir: Path,
    parse_document: ParseFunction,
    *,
    include_splits: set[str] | None = None,
    source_index_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Validate a sealed bundle before invoking an injected parser even once."""

    reference = validate_reference_bundle(
        reference_json_path, reference_csv_path, integrity_path
    )
    selected_splits = set(SPLITS) if include_splits is None else set(include_splits)
    if not selected_splits or not selected_splits.issubset(SPLITS):
        raise GoldsetScoringError("include_splits must contain only known split names")
    required_hashes = {
        record["document_sha256"]
        for record in reference["records"]
        if record["split"] in selected_splits
    }
    source_paths = index_source_documents(
        source_dir,
        required_hashes,
        source_index_path=source_index_path,
        allow_directory_scan=include_splits is None,
    )
    return (
        score_reference(
            reference,
            source_paths,
            parse_document,
            include_splits=selected_splits,
        ),
        source_paths,
    )


def _metric_text(value: Any) -> str:
    return "undefined (empty denominator)" if value is None else f"{value:.2f}%"


def _markdown_report(score: Mapping[str, Any]) -> str:
    summary = score["summary"]
    lines = [
        "# NCS recruitment detail reference score",
        "",
        f"- Evaluation basis: `{score['evaluation_basis']}`",
        f"- Interpretation: `{score['metrics_interpretation']}`",
        f"- Human gold accuracy: `{str(score['metrics_are_human_gold_accuracy']).lower()}`",
        f"- Records: {summary['record_count']}",
        f"- Error cases: {summary['error_case_count']}",
        "",
        "## Official-current detail core",
        "",
        "Only records independently labeled `official_current` enter these name/code/pair precision, recall, and exact-match denominators. A pair is the ordered official detail name with its code; matching the two sets separately is insufficient.",
        "",
        "| Split | Eligible docs | Name P/R/F1 | Code P/R/F1 | Pair P/R/F1 | State exact | Document exact |",
        "|---|---:|---|---|---|---:|---:|",
    ]
    for label, metrics in [
        ("overall", summary["official_current_core"]),
        *[
            (split, summary["official_current_core_by_split"][split])
            for split in SPLITS
        ],
    ]:
        name = metrics["detail_name"]
        code = metrics["detail_code"]
        pair = metrics["detail_pair"]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(metrics["eligible_document_count"]),
                    "/".join(
                        _metric_text(name[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    "/".join(
                        _metric_text(code[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    "/".join(
                        _metric_text(pair[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    _metric_text(metrics["mapping_state_accuracy_pct"]),
                    _metric_text(metrics["document_exact_pct"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## All-state diagnostics",
            "",
        "Automatic predictions were produced only after the sealed reference was loaded and validated. They were not used to write or alter reference labels.",
        "",
        "| Split | Docs | Name P/R/F1 | Code P/R/F1 | Pair P/R/F1 | State exact | Document exact |",
        "|---|---:|---|---|---|---:|---:|",
        ]
    )
    for label, metrics in [
        ("overall", summary["overall"]),
        *[(split, summary["by_split"][split]) for split in SPLITS],
    ]:
        name = metrics["detail_name"]
        code = metrics["detail_code"]
        pair = metrics["detail_pair"]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(metrics["document_count"]),
                    "/".join(
                        _metric_text(name[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    "/".join(
                        _metric_text(code[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    "/".join(
                        _metric_text(pair[key])
                        for key in ("precision_pct", "recall_pct", "f1_pct")
                    ),
                    _metric_text(metrics["mapping_state_accuracy_pct"]),
                    _metric_text(metrics["document_exact_pct"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Undefined values are emitted explicitly when a metric denominator is empty; they are never converted to 0% or 100%.",
            "",
        ]
    )
    if not score["metrics_are_human_gold_accuracy"]:
        lines.extend(
            [
                "> This is comparison against an independently AI-agent-adjudicated reference. It must not be described as human-gold accuracy.",
                "",
            ]
        )
    return "\n".join(lines)


def write_score_report(
    score: Mapping[str, Any],
    output_dir: Path,
    *,
    reference_paths: Mapping[str, Path],
    source_paths: Mapping[str, Path],
    require_runtime_attestation: bool = True,
) -> dict[str, Path]:
    if require_runtime_attestation:
        runtime = score.get("evaluation_runtime")
        if not isinstance(runtime, Mapping) or runtime.get("runtime_attested") is not True:
            raise GoldsetScoringError(
                "score report requires an attested parse-review runtime"
            )
        server_attestation = runtime.get("server_runtime_attestation")
        if server_attestation != local_runtime_attestation():
            raise GoldsetScoringError(
                "score report runtime attestation does not match current sources"
            )
        if runtime.get(
            "scorer_source_artifact_sha256"
        ) != local_scorer_source_artifact_sha256():
            raise GoldsetScoringError(
                "score report scorer provenance does not match current sources"
            )
        allowed_modes = runtime.get("allowed_parser_modes")
        if allowed_modes != ["builtin_plain_text", "local_node_subprocess"]:
            raise GoldsetScoringError(
                "score report local-only parser policy is invalid"
            )
        try:
            execution_identities = _validate_scoring_parser_execution_rows(
                runtime.get("parser_execution_identities"),
                runtime_attestation=server_attestation,
                allowed_modes=set(allowed_modes),
            )
        except GoldsetScoringInfrastructureError as exc:
            raise GoldsetScoringError(
                "score report parser execution provenance is invalid"
            ) from exc
        canonical_identities: set[str] = set()
        identity_by_parser_mode: dict[tuple[str, str], str] = {}
        for row in execution_identities:
            identity_text = json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity_key = (str(row["parser"]), str(row["mode"]))
            previous = identity_by_parser_mode.setdefault(
                identity_key,
                identity_text,
            )
            if previous != identity_text:
                raise GoldsetScoringError(
                    "score report parser execution build identity drifted"
                )
            if identity_text in canonical_identities:
                raise GoldsetScoringError(
                    "score report parser execution provenance is duplicated"
                )
            canonical_identities.add(identity_text)
    output_dir = validate_local_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ncs_recruitment_reference_score.local.json"
    csv_path = output_dir / "ncs_recruitment_reference_errors.local.csv"
    markdown_path = output_dir / "ncs_recruitment_reference_score.local.md"
    integrity_path = output_dir / "score_integrity.local.json"
    json_path.write_text(
        json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ERROR_CSV_FIELDS)
        writer.writeheader()
        for row in score["error_cases"]:
            writer.writerow(
                {
                    "item_id": row["item_id"],
                    "split": row["split"],
                    "document_sha256": row["document_sha256"],
                    "parse_status": row["parse_status"],
                    "reference_mapping_state": row["reference_mapping_state"],
                    "predicted_mapping_state": row["predicted_mapping_state"],
                    "reference_detail_names_json": json.dumps(
                        row["reference_detail_names"], ensure_ascii=False
                    ),
                    "predicted_detail_names_json": json.dumps(
                        row["predicted_detail_names"], ensure_ascii=False
                    ),
                    "reference_detail_codes_json": json.dumps(
                        row["reference_detail_codes"], ensure_ascii=False
                    ),
                    "predicted_detail_codes_json": json.dumps(
                        row["predicted_detail_codes"], ensure_ascii=False
                    ),
                    "reference_detail_pairs_json": json.dumps(
                        row["reference_detail_pairs"], ensure_ascii=False
                    ),
                    "predicted_detail_pairs_json": json.dumps(
                        row["predicted_detail_pairs"], ensure_ascii=False
                    ),
                    "state_match": _bool_text(row["state_match"]),
                    "name_exact": _bool_text(row["name_exact"]),
                    "code_exact": _bool_text(row["code_exact"]),
                    "pair_exact": _bool_text(row["pair_exact"]),
                    "document_exact": _bool_text(row["document_exact"]),
                    "error_types_json": json.dumps(row["error_types"]),
                    "error_message": row["error_message"],
                }
            )
    markdown_path.write_text(_markdown_report(score), encoding="utf-8")
    report_paths = {
        "score_json": json_path,
        "error_csv": csv_path,
        "score_markdown": markdown_path,
    }
    integrity = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_basis": score["evaluation_basis"],
        "metrics_interpretation": score["metrics_interpretation"],
        "metrics_are_human_gold_accuracy": score["metrics_are_human_gold_accuracy"],
        "automatic_predictions_are_reference_labels": False,
        "usage_policy": USAGE_POLICY,
        "local_only": True,
        "reference_artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(reference_paths.items())
        },
        "private_source_document_sha256": sorted(source_paths),
        "evaluation_runtime": score["evaluation_runtime"],
        "parse_payload_ledger_sha256": sha256_bytes(
            canonical_json_bytes(
                [
                    {
                        "item_id": row["item_id"],
                        "raw_parse_payload_sha256": row[
                            "raw_parse_payload_sha256"
                        ],
                    }
                    for row in score["records"]
                ]
            )
        ),
        "output_artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(report_paths.items())
        },
    }
    integrity_path.write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**report_paths, "integrity": integrity_path}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed scoring of private NCS recruitment documents against a sealed "
            "adjudicated reference through a loopback parse-review endpoint."
        )
    )
    parser.add_argument(
        "--reference-json",
        default=str(DEFAULT_FINAL_DIR / "ncs_recruitment_final_reference.local.json"),
    )
    parser.add_argument(
        "--reference-csv",
        default=str(DEFAULT_FINAL_DIR / "ncs_recruitment_final_reference.local.csv"),
    )
    parser.add_argument(
        "--reference-integrity",
        default=str(DEFAULT_FINAL_DIR / "final_integrity.local.json"),
    )
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    parser.add_argument(
        "--source-index",
        help=(
            "private source_index.local.csv; required with --split so files in "
            "unselected splits are never scanned or hashed"
        ),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--max-retry-after-seconds", type=float, default=60.0)
    parser.add_argument(
        "--split",
        action="append",
        choices=SPLITS,
        help=(
            "evaluate only the selected split after validating the complete sealed "
            "reference; repeatable. Omit for the one-time full evaluation"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    reference_paths = {
        "reference_json": Path(args.reference_json).resolve(),
        "reference_csv": Path(args.reference_csv).resolve(),
        "reference_integrity": Path(args.reference_integrity).resolve(),
    }
    with LocalParseReviewClient(
        args.base_url,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        max_retry_after_seconds=args.max_retry_after_seconds,
    ) as client:
        score, source_paths = evaluate_bundle(
            reference_paths["reference_json"],
            reference_paths["reference_csv"],
            reference_paths["reference_integrity"],
            Path(args.source_dir).resolve(),
            client.parse,
            include_splits=set(args.split) if args.split else None,
            source_index_path=(
                Path(args.source_index).resolve() if args.source_index else None
            ),
        )
        score["evaluation_runtime"].update(client.evaluation_configuration)
        score["evaluation_runtime"]["evaluated_splits"] = score["evaluated_splits"]
        score["evaluation_runtime"]["server_runtime_attestation"] = (
            client.finalize_runtime_attestation()
        )
        score["evaluation_runtime"]["runtime_attested"] = True
    output_paths = write_score_report(
        score,
        Path(args.output_dir),
        reference_paths=reference_paths,
        source_paths=source_paths,
    )
    print(json.dumps(score["summary"], ensure_ascii=False, indent=2))
    print(f"metrics_interpretation={score['metrics_interpretation']}")
    for name, path in output_paths.items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
