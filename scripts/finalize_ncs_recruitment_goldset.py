from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "tmp" / "ncs_recruitment_goldset"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "final"
WORKFLOW_VERSION = "ncs_recruitment_human_goldset_v1"
FINAL_SCHEMA_VERSION = "ncs_recruitment_adjudicated_reference_v1"
USAGE_POLICY = "evaluation_only_no_training_or_rule_tuning"
AI_EVALUATION_BASIS = "independent_ai_agent_adjudicated_reference_not_human_gold"
HUMAN_EVALUATION_BASIS = "independent_human_double_review_adjudicated_gold"
HUMAN_ATTESTATION = "confirmed_independent_human_review"
OFFICIAL_DETAIL_CATALOG = ROOT / "app" / "data" / "ncs_detail_catalog.json"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DETAIL_CODE_RE = re.compile(r"^[0-9]{8}$")
FORBIDDEN_PREDICTION_FRAGMENTS = (
    "observed",
    "predicted",
    "prediction",
    "automatic",
    "source_detail",
)
MAPPING_STATES = {
    "official_current",
    "legacy_or_nonstandard",
    "self_developed",
    "not_stated",
    "ambiguous",
    "unreadable",
}

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

FINAL_CSV_FIELDS = [
    "item_id",
    "split",
    "document_sha256",
    "case_ids_json",
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


class GoldsetFinalizationError(ValueError):
    """Raised when finalization cannot prove every required invariant."""


def _load_current_official_details(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoldsetFinalizationError(
            f"current official detail catalog is not readable: {path}"
        ) from exc
    rows = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise GoldsetFinalizationError("current official detail catalog has no details list")
    details = {
        str(row.get("code") or "").strip(): str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("usage_yn") or "").upper() == "Y"
    }
    if (
        not details
        or any(not DETAIL_CODE_RE.fullmatch(code) for code in details)
        or any(not name for name in details.values())
    ):
        raise GoldsetFinalizationError("current official detail catalog is invalid")
    return details


CURRENT_OFFICIAL_DETAILS = _load_current_official_details(
    OFFICIAL_DETAIL_CATALOG
)
CURRENT_OFFICIAL_DETAIL_CODES = frozenset(CURRENT_OFFICIAL_DETAILS)


def _detail_name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[·ᆞ․‧•∙⋅・ㆍ]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


CURRENT_OFFICIAL_DETAIL_NAME_KEYS = frozenset(
    _detail_name_key(name) for name in CURRENT_OFFICIAL_DETAILS.values()
)


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


def _require_sha256(value: Any, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if not HEX64_RE.fullmatch(digest):
        raise GoldsetFinalizationError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def deterministic_split(document_sha256: str, *, holdout_modulus: int) -> str:
    digest = _require_sha256(document_sha256, field="document_sha256")
    if holdout_modulus < 2:
        raise GoldsetFinalizationError("holdout_modulus must be at least 2")
    return (
        "gold_holdout"
        if int(digest[:16], 16) % holdout_modulus == 0
        else "gold_validation"
    )


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
        raise GoldsetFinalizationError(
            "final reference artifacts may only be written below repository tmp/ or .tmp/"
        )
    return resolved


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoldsetFinalizationError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise GoldsetFinalizationError(f"{label} must be a JSON object")
    return payload


def _read_csv(path: Path, *, fields: Sequence[str], label: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise GoldsetFinalizationError(
                    f"{label} columns must exactly match the prepared template; "
                    "extra columns could expose automatic predictions"
                )
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise GoldsetFinalizationError(f"{label} is not readable CSV: {path}") from exc
    return rows


def _parse_json_array(value: Any, *, field: str) -> list[Any]:
    raw = str(value or "").strip()
    if not raw:
        raise GoldsetFinalizationError(f"{field} is required and must be a JSON array")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoldsetFinalizationError(f"{field} must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise GoldsetFinalizationError(f"{field} must be a JSON array")
    return parsed


def _require_utc_timestamp(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise GoldsetFinalizationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldsetFinalizationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise GoldsetFinalizationError(f"{field} must explicitly use UTC")
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_evidence(value: Any, *, field: str) -> list[dict[str, Any]]:
    evidence = _parse_json_array(value, field=field)
    if not evidence:
        raise GoldsetFinalizationError(f"{field} must contain source evidence")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise GoldsetFinalizationError(f"{item_field} must be an object")
        text = next(
            (
                str(item.get(key) or "").strip()
                for key in ("quote", "text", "note", "reason")
                if str(item.get(key) or "").strip()
            ),
            "",
        )
        locator = next(
            (
                item.get(key)
                for key in ("page", "section", "locator")
                if item.get(key) not in (None, "")
            ),
            None,
        )
        if not text or locator is None:
            raise GoldsetFinalizationError(
                f"{item_field} requires evidence text and page/section/locator"
            )
        output.append(dict(item))
    return output


def _normalize_answer(
    mapping_state_value: Any,
    detail_names_value: Any,
    detail_codes_value: Any,
    *,
    prefix: str,
) -> dict[str, Any]:
    mapping_state = str(mapping_state_value or "").strip()
    if mapping_state not in MAPPING_STATES:
        raise GoldsetFinalizationError(f"{prefix}.mapping_state is invalid or blank")
    names_raw = _parse_json_array(
        detail_names_value, field=f"{prefix}.detail_names_json"
    )
    codes_raw = _parse_json_array(
        detail_codes_value, field=f"{prefix}.detail_codes_json"
    )
    if not all(isinstance(value, str) and value.strip() for value in names_raw):
        raise GoldsetFinalizationError(
            f"{prefix}.detail_names_json must contain only non-blank strings"
        )
    if not all(isinstance(value, str) and value.strip() for value in codes_raw):
        raise GoldsetFinalizationError(
            f"{prefix}.detail_codes_json must contain only non-blank strings"
        )
    names = [value.strip() for value in names_raw]
    codes = [value.strip() for value in codes_raw]

    if mapping_state == "official_current":
        if not names or len(names) != len(codes):
            raise GoldsetFinalizationError(
                f"{prefix}: official_current requires paired official names and codes"
            )
        if any(not DETAIL_CODE_RE.fullmatch(code) for code in codes):
            raise GoldsetFinalizationError(
                f"{prefix}: official detail codes must contain exactly eight digits"
            )
        if any(code not in CURRENT_OFFICIAL_DETAIL_CODES for code in codes):
            raise GoldsetFinalizationError(
                f"{prefix}: detail code is not in the current official catalog"
            )
        pairs = sorted(zip(codes, names))
        mismatched_pairs = [
            (code, name)
            for code, name in pairs
            if CURRENT_OFFICIAL_DETAILS.get(code) != name
        ]
        if mismatched_pairs:
            raise GoldsetFinalizationError(
                f"{prefix}: official detail name/code pair does not match the current catalog"
            )
        if len({code for code, _ in pairs}) != len(pairs):
            raise GoldsetFinalizationError(f"{prefix}: duplicate official detail code")
        if len(set(pairs)) != len(pairs):
            raise GoldsetFinalizationError(f"{prefix}: duplicate official detail pair")
        codes = [code for code, _ in pairs]
        names = [name for _, name in pairs]
    elif mapping_state in {"legacy_or_nonstandard", "self_developed"}:
        if not names or codes:
            raise GoldsetFinalizationError(
                f"{prefix}: {mapping_state} requires source names and no official codes"
            )
        if mapping_state == "legacy_or_nonstandard" and all(
            _detail_name_key(name) in CURRENT_OFFICIAL_DETAIL_NAME_KEYS
            for name in names
        ):
            raise GoldsetFinalizationError(
                f"{prefix}: legacy_or_nonstandard names all resolve to current official details"
            )
        names = sorted(set(names))
    elif names or codes:
        raise GoldsetFinalizationError(
            f"{prefix}: {mapping_state} requires empty name/code JSON arrays"
        )

    return {
        "mapping_state": mapping_state,
        "detail_names": names,
        "detail_codes": codes,
    }


def _answer_key(answer: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(
        {
            "mapping_state": answer["mapping_state"],
            "detail_names": answer["detail_names"],
            "detail_codes": answer["detail_codes"],
        }
    )


def _validate_seed(
    manifest: Mapping[str, Any],
    integrity: Mapping[str, Any],
    *,
    manifest_path: Path,
    do_not_tune_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    summary = manifest.get("summary")
    records = manifest.get("records")
    if not isinstance(summary, dict) or not isinstance(records, list) or not records:
        raise GoldsetFinalizationError("manifest must contain non-empty records and summary")
    if summary.get("workflow_version") != WORKFLOW_VERSION:
        raise GoldsetFinalizationError("unexpected goldset workflow version")
    if summary.get("usage_policy") != USAGE_POLICY:
        raise GoldsetFinalizationError("manifest do-not-tune policy is missing")
    if summary.get("automatic_predictions_are_gold") is not False:
        raise GoldsetFinalizationError("manifest exposes automatic predictions as gold")
    if summary.get("is_gold") is not False:
        raise GoldsetFinalizationError("prepared manifest was already marked gold")
    try:
        holdout_modulus = int(summary.get("holdout_modulus"))
    except (TypeError, ValueError) as exc:
        raise GoldsetFinalizationError("manifest holdout_modulus is invalid") from exc

    item_ids: set[str] = set()
    digests: set[str] = set()
    case_ids: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            raise GoldsetFinalizationError("manifest records must be objects")
        item_id = str(raw.get("item_id") or "").strip()
        digest = _require_sha256(
            raw.get("document_sha256"), field=f"{item_id or '<blank>'}.document_sha256"
        )
        split = str(raw.get("split") or "")
        if not item_id or item_id in item_ids or item_id != f"nrg-{digest}":
            raise GoldsetFinalizationError("manifest item ID is blank, duplicate, or invalid")
        if digest in digests:
            raise GoldsetFinalizationError("duplicate document leaked across manifest rows")
        if split != deterministic_split(digest, holdout_modulus=holdout_modulus):
            raise GoldsetFinalizationError("validation/holdout split invariant failed")
        if raw.get("usage_policy") != USAGE_POLICY or raw.get("is_gold") is not False:
            raise GoldsetFinalizationError("manifest record violates do-not-tune/gold policy")
        raw_case_ids = raw.get("case_ids")
        if not isinstance(raw_case_ids, list) or not raw_case_ids:
            raise GoldsetFinalizationError(f"{item_id}: source case coverage is empty")
        normalized_case_ids = [str(value).strip() for value in raw_case_ids]
        if any(not value for value in normalized_case_ids):
            raise GoldsetFinalizationError(f"{item_id}: source case ID is blank")
        overlap = case_ids.intersection(normalized_case_ids)
        if overlap:
            raise GoldsetFinalizationError("source case leaked across manifest records")
        item_ids.add(item_id)
        digests.add(digest)
        case_ids.update(normalized_case_ids)
        normalized_records.append(dict(raw))

    if int(summary.get("unique_document_count") or -1) != len(normalized_records):
        raise GoldsetFinalizationError("manifest unique document count mismatch")
    if int(summary.get("benchmark_case_count") or -1) != len(case_ids):
        raise GoldsetFinalizationError("manifest benchmark case coverage mismatch")
    records_hash = sha256_bytes(canonical_json_bytes(records))
    if summary.get("records_sha256") != records_hash:
        raise GoldsetFinalizationError("manifest records SHA-256 mismatch")

    if integrity.get("workflow_version") != WORKFLOW_VERSION:
        raise GoldsetFinalizationError("seed integrity workflow version mismatch")
    if integrity.get("local_only") is not True:
        raise GoldsetFinalizationError("seed integrity does not assert local-only handling")
    if integrity.get("automatic_predictions_are_gold") is not False:
        raise GoldsetFinalizationError("seed integrity permits automatic gold labels")
    if integrity.get("records_sha256") != records_hash:
        raise GoldsetFinalizationError("seed integrity records SHA-256 mismatch")
    artifact_hashes = integrity.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        raise GoldsetFinalizationError("seed artifact hashes are missing")
    for artifact_name, artifact_path in (
        ("manifest", manifest_path),
        ("do_not_tune", do_not_tune_path),
    ):
        expected = _require_sha256(
            artifact_hashes.get(artifact_name),
            field=f"integrity.artifact_sha256.{artifact_name}",
        )
        if sha256_file(artifact_path) != expected:
            raise GoldsetFinalizationError(f"{artifact_name} integrity hash mismatch")
    for name in ("reviewer_a", "reviewer_b", "adjudication"):
        _require_sha256(
            artifact_hashes.get(name), field=f"integrity.artifact_sha256.{name}"
        )

    do_not_tune_rows = _read_csv(
        do_not_tune_path,
        fields=["document_sha256", "usage_policy"],
        label="do_not_tune",
    )
    exclusions: dict[str, str] = {}
    for row in do_not_tune_rows:
        digest = _require_sha256(row.get("document_sha256"), field="do_not_tune hash")
        if digest in exclusions:
            raise GoldsetFinalizationError("duplicate do-not-tune digest")
        policy = str(row.get("usage_policy") or "")
        if policy != USAGE_POLICY:
            raise GoldsetFinalizationError("do-not-tune policy mismatch")
        exclusions[digest] = policy
    if set(exclusions) != digests:
        raise GoldsetFinalizationError("do-not-tune coverage does not match manifest")

    return normalized_records, {row["item_id"]: row for row in normalized_records}


def _validate_reviewer_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    slot: str,
    record_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str]:
    output: dict[str, dict[str, Any]] = {}
    reviewer_ids: set[str] = set()
    for row in rows:
        item_id = str(row.get("item_id") or "").strip()
        if item_id not in record_by_id or item_id in output:
            raise GoldsetFinalizationError(
                f"reviewer {slot} coverage has an unknown or duplicate item"
            )
        record = record_by_id[item_id]
        for field in ("document_sha256",):
            if str(row.get(field) or "") != str(record.get(field) or ""):
                raise GoldsetFinalizationError(
                    f"reviewer {slot} {item_id} {field} does not match manifest"
                )
        if row.get("reviewer_slot") != slot:
            raise GoldsetFinalizationError(f"reviewer {slot} slot mismatch")
        reviewer_id = str(row.get("reviewer_id") or "").strip()
        if not reviewer_id:
            raise GoldsetFinalizationError(f"reviewer {slot} ID is required")
        reviewer_ids.add(reviewer_id)
        if row.get("review_status") != "completed_independent_review":
            raise GoldsetFinalizationError(
                f"reviewer {slot} {item_id} is not a completed independent review"
            )
        reviewed_at = _require_utc_timestamp(
            row.get("reviewed_at_utc"), field=f"reviewer {slot}.{item_id}.reviewed_at_utc"
        )
        answer = _normalize_answer(
            row.get("mapping_state"),
            row.get("detail_names_json"),
            row.get("detail_codes_json"),
            prefix=f"reviewer {slot}.{item_id}",
        )
        evidence = _normalize_evidence(
            row.get("evidence_json"), field=f"reviewer {slot}.{item_id}.evidence_json"
        )
        confidence = str(row.get("confidence") or "").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            raise GoldsetFinalizationError(
                f"reviewer {slot}.{item_id}.confidence must be high, medium, or low"
            )
        output[item_id] = {
            "reviewer_id": reviewer_id,
            "reviewed_at_utc": reviewed_at,
            "answer": answer,
            "evidence": evidence,
            "confidence": confidence,
        }
    if set(output) != set(record_by_id):
        raise GoldsetFinalizationError(f"reviewer {slot} case coverage is incomplete")
    if len(reviewer_ids) != 1:
        raise GoldsetFinalizationError(
            f"reviewer {slot} file must contain exactly one reviewer_id"
        )
    return output, next(iter(reviewer_ids))


def _validate_adjudication_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    record_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        item_id = str(row.get("item_id") or "").strip()
        if item_id not in record_by_id or item_id in output:
            raise GoldsetFinalizationError(
                "adjudication coverage has an unknown or duplicate item"
            )
        record = record_by_id[item_id]
        for field in ("split", "document_sha256", "local_document_path"):
            if str(row.get(field) or "") != str(record.get(field) or ""):
                raise GoldsetFinalizationError(
                    f"adjudication {item_id} {field} does not match manifest"
                )
        output[item_id] = row
    if set(output) != set(record_by_id):
        raise GoldsetFinalizationError("adjudication case coverage is incomplete")
    return output


def _validate_reviewer_kind(kind: str, *, field: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized not in {"human", "ai_agent"}:
        raise GoldsetFinalizationError(f"{field} must be human or ai_agent")
    return normalized


def finalize_reference(
    manifest: Mapping[str, Any],
    integrity: Mapping[str, Any],
    reviewer_a_rows: Sequence[Mapping[str, Any]],
    reviewer_b_rows: Sequence[Mapping[str, Any]],
    adjudication_rows: Sequence[Mapping[str, Any]],
    *,
    manifest_path: Path,
    do_not_tune_path: Path,
    reviewer_a_kind: str,
    reviewer_b_kind: str,
    reviewer_a_provenance: str,
    reviewer_b_provenance: str,
    adjudicator_kind: str | None = None,
    adjudicator_provenance: str | None = None,
    human_gold_attestation: str | None = None,
) -> dict[str, Any]:
    """Finalize exact consensus or third-party decisions into a sealed reference."""

    records, record_by_id = _validate_seed(
        manifest,
        integrity,
        manifest_path=manifest_path,
        do_not_tune_path=do_not_tune_path,
    )
    reviews_a, reviewer_a_id = _validate_reviewer_rows(
        reviewer_a_rows, slot="A", record_by_id=record_by_id
    )
    reviews_b, reviewer_b_id = _validate_reviewer_rows(
        reviewer_b_rows, slot="B", record_by_id=record_by_id
    )
    if reviewer_a_id == reviewer_b_id:
        raise GoldsetFinalizationError("reviewer A and B IDs must be different")
    kind_a = _validate_reviewer_kind(reviewer_a_kind, field="reviewer_a_kind")
    kind_b = _validate_reviewer_kind(reviewer_b_kind, field="reviewer_b_kind")
    provenance_a = str(reviewer_a_provenance or "").strip()
    provenance_b = str(reviewer_b_provenance or "").strip()
    if not provenance_a or not provenance_b:
        raise GoldsetFinalizationError("reviewer provenance is required")
    adjudication_by_id = _validate_adjudication_rows(
        adjudication_rows, record_by_id=record_by_id
    )

    final_records: list[dict[str, Any]] = []
    disagreement_count = 0
    adjudicator_ids: set[str] = set()
    for record in records:
        item_id = str(record["item_id"])
        review_a = reviews_a[item_id]
        review_b = reviews_b[item_id]
        adjudication = adjudication_by_id[item_id]
        agreed = _answer_key(review_a["answer"]) == _answer_key(review_b["answer"])

        if agreed:
            if str(adjudication.get("adjudication_status") or "") not in {
                "",
                "pending_two_reviews",
                "not_required_consensus",
            }:
                raise GoldsetFinalizationError(
                    f"{item_id}: consensus must not be overridden by adjudication"
                )
            if any(
                str(adjudication.get(field) or "").strip()
                for field in (
                    "adjudicator_id",
                    "adjudicated_at_utc",
                    "final_mapping_state",
                    "final_detail_names_json",
                    "final_detail_codes_json",
                    "final_evidence_json",
                    "adjudication_rationale",
                )
            ):
                raise GoldsetFinalizationError(
                    f"{item_id}: consensus row contains an unnecessary adjudication decision"
                )
            answer = review_a["answer"]
            final_evidence: dict[str, Any] = {
                "reviewer_a": review_a["evidence"],
                "reviewer_b": review_b["evidence"],
            }
            resolution_type = "exact_two_reviewer_consensus"
            adjudicator_id = ""
        else:
            disagreement_count += 1
            if adjudication.get("agreement_status") != "disagreement":
                raise GoldsetFinalizationError(
                    f"{item_id}: disagreement must be explicitly marked"
                )
            if adjudication.get("adjudication_status") != "completed_third_party_adjudication":
                raise GoldsetFinalizationError(
                    f"{item_id}: disagreement requires completed third-party adjudication"
                )
            adjudicator_id = str(adjudication.get("adjudicator_id") or "").strip()
            if not adjudicator_id or adjudicator_id in {reviewer_a_id, reviewer_b_id}:
                raise GoldsetFinalizationError(
                    f"{item_id}: adjudicator must be a distinct third reviewer"
                )
            adjudicator_ids.add(adjudicator_id)
            _require_utc_timestamp(
                adjudication.get("adjudicated_at_utc"),
                field=f"adjudication.{item_id}.adjudicated_at_utc",
            )
            answer = _normalize_answer(
                adjudication.get("final_mapping_state"),
                adjudication.get("final_detail_names_json"),
                adjudication.get("final_detail_codes_json"),
                prefix=f"adjudication.{item_id}",
            )
            final_evidence = {
                "reviewer_a": review_a["evidence"],
                "reviewer_b": review_b["evidence"],
                "adjudicator": _normalize_evidence(
                    adjudication.get("final_evidence_json"),
                    field=f"adjudication.{item_id}.final_evidence_json",
                ),
            }
            if not str(adjudication.get("adjudication_rationale") or "").strip():
                raise GoldsetFinalizationError(
                    f"{item_id}: adjudication rationale is required"
                )
            resolution_type = "third_party_adjudication"

        final_records.append(
            {
                "item_id": item_id,
                "split": record["split"],
                "document_sha256": record["document_sha256"],
                "case_ids": list(record["case_ids"]),
                **answer,
                "evidence": final_evidence,
                "resolution_type": resolution_type,
                "reviewer_a_id": reviewer_a_id,
                "reviewer_b_id": reviewer_b_id,
                "adjudicator_id": adjudicator_id,
                "usage_policy": USAGE_POLICY,
            }
        )

    kind_adjudicator: str | None = None
    provenance_adjudicator = str(adjudicator_provenance or "").strip()
    if disagreement_count:
        kind_adjudicator = _validate_reviewer_kind(
            str(adjudicator_kind or ""), field="adjudicator_kind"
        )
        if len(adjudicator_ids) != 1:
            raise GoldsetFinalizationError(
                "all adjudicated rows must use exactly one adjudicator_id"
            )
        if not provenance_adjudicator:
            raise GoldsetFinalizationError("adjudicator provenance is required")
    elif adjudicator_kind is not None:
        kind_adjudicator = _validate_reviewer_kind(
            adjudicator_kind, field="adjudicator_kind"
        )

    all_required_reviewers_human = kind_a == kind_b == "human" and (
        not disagreement_count or kind_adjudicator == "human"
    )
    if all_required_reviewers_human:
        if human_gold_attestation != HUMAN_ATTESTATION:
            raise GoldsetFinalizationError(
                "human gold requires explicit independent-human-review attestation"
            )
        is_human_gold = True
        evaluation_basis = HUMAN_EVALUATION_BASIS
    else:
        if human_gold_attestation:
            raise GoldsetFinalizationError(
                "AI or mixed review provenance cannot carry a human-gold attestation"
            )
        is_human_gold = False
        evaluation_basis = AI_EVALUATION_BASIS

    split_counts = Counter(str(record["split"]) for record in final_records)
    resolution_counts = Counter(
        str(record["resolution_type"]) for record in final_records
    )
    return {
        "schema_version": FINAL_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_basis": evaluation_basis,
        "is_human_gold": is_human_gold,
        "is_gold_accuracy": is_human_gold,
        "is_gold": is_human_gold,
        "automatic_predictions_are_gold": False,
        "usage_policy": USAGE_POLICY,
        "review_provenance": {
            "reviewer_a": {
                "reviewer_id": reviewer_a_id,
                "reviewer_kind": kind_a,
                "provenance": provenance_a,
            },
            "reviewer_b": {
                "reviewer_id": reviewer_b_id,
                "reviewer_kind": kind_b,
                "provenance": provenance_b,
            },
            "adjudicator": (
                {
                    "reviewer_id": next(iter(adjudicator_ids)),
                    "reviewer_kind": kind_adjudicator,
                    "provenance": provenance_adjudicator,
                }
                if disagreement_count
                else None
            ),
        },
        "policy": {
            "consensus_rule": "exact normalized answer agreement by two independent reviewers",
            "disagreement_rule": "distinct third reviewer must complete adjudication",
            "split_assignment": "immutable SHA-256/modulus assignment from prepared manifest",
            "tuning": USAGE_POLICY,
        },
        "summary": {
            "record_count": len(final_records),
            "case_count": sum(len(record["case_ids"]) for record in final_records),
            "split_counts": dict(sorted(split_counts.items())),
            "resolution_counts": dict(sorted(resolution_counts.items())),
            "disagreement_count": disagreement_count,
        },
        "source_records_sha256": manifest["summary"]["records_sha256"],
        "records_sha256": sha256_bytes(canonical_json_bytes(final_records)),
        "records": final_records,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FINAL_CSV_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_final_reference(
    reference: Mapping[str, Any],
    output_dir: Path,
    *,
    source_paths: Mapping[str, Path],
) -> dict[str, Path]:
    output_dir = validate_local_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ncs_recruitment_final_reference.local.json"
    csv_path = output_dir / "ncs_recruitment_final_reference.local.csv"
    integrity_path = output_dir / "final_integrity.local.json"

    json_path.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    csv_rows = []
    for record in reference["records"]:
        csv_rows.append(
            {
                "item_id": record["item_id"],
                "split": record["split"],
                "document_sha256": record["document_sha256"],
                "case_ids_json": json.dumps(record["case_ids"], ensure_ascii=False),
                "mapping_state": record["mapping_state"],
                "detail_names_json": json.dumps(
                    record["detail_names"], ensure_ascii=False
                ),
                "detail_codes_json": json.dumps(
                    record["detail_codes"], ensure_ascii=False
                ),
                "evidence_json": json.dumps(record["evidence"], ensure_ascii=False),
                "resolution_type": record["resolution_type"],
                "reviewer_a_id": record["reviewer_a_id"],
                "reviewer_b_id": record["reviewer_b_id"],
                "adjudicator_id": record["adjudicator_id"],
                "evaluation_basis": reference["evaluation_basis"],
                "is_human_gold": str(reference["is_human_gold"]).lower(),
                "is_gold_accuracy": str(reference["is_gold_accuracy"]).lower(),
                "usage_policy": USAGE_POLICY,
            }
        )
    _write_csv(csv_path, csv_rows)

    output_paths = {"reference_json": json_path, "reference_csv": csv_path}
    integrity_payload = {
        "schema_version": FINAL_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_basis": reference["evaluation_basis"],
        "is_human_gold": reference["is_human_gold"],
        "is_gold_accuracy": reference["is_gold_accuracy"],
        "automatic_predictions_are_gold": False,
        "usage_policy": USAGE_POLICY,
        "local_only": True,
        "records_sha256": reference["records_sha256"],
        "source_artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(source_paths.items())
        },
        "output_artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(output_paths.items())
        },
    }
    integrity_path.write_text(
        json.dumps(integrity_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**output_paths, "integrity": integrity_path}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed finalization of two independent NCS detail reviews. "
            "Exact agreement becomes consensus; disagreement requires a distinct adjudicator."
        )
    )
    parser.add_argument("--manifest", default=str(DEFAULT_INPUT_DIR / "goldset_review_manifest.local.json"))
    parser.add_argument("--seed-integrity", default=str(DEFAULT_INPUT_DIR / "integrity.local.json"))
    parser.add_argument("--do-not-tune", default=str(DEFAULT_INPUT_DIR / "do_not_tune.local.csv"))
    parser.add_argument("--reviewer-a", default=str(DEFAULT_INPUT_DIR / "reviewer_a.local.csv"))
    parser.add_argument("--reviewer-b", default=str(DEFAULT_INPUT_DIR / "reviewer_b.local.csv"))
    parser.add_argument("--adjudication", default=str(DEFAULT_INPUT_DIR / "adjudication.local.csv"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--reviewer-a-kind", required=True, choices=("human", "ai_agent"))
    parser.add_argument("--reviewer-b-kind", required=True, choices=("human", "ai_agent"))
    parser.add_argument("--reviewer-a-provenance", required=True)
    parser.add_argument("--reviewer-b-provenance", required=True)
    parser.add_argument("--adjudicator-kind", choices=("human", "ai_agent"))
    parser.add_argument("--adjudicator-provenance")
    parser.add_argument(
        "--human-gold-attestation",
        help=f"Required literal for human-only review: {HUMAN_ATTESTATION}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    paths = {
        "manifest": Path(args.manifest).resolve(),
        "seed_integrity": Path(args.seed_integrity).resolve(),
        "do_not_tune": Path(args.do_not_tune).resolve(),
        "reviewer_a": Path(args.reviewer_a).resolve(),
        "reviewer_b": Path(args.reviewer_b).resolve(),
        "adjudication": Path(args.adjudication).resolve(),
    }
    manifest = _read_json_object(paths["manifest"], label="manifest")
    integrity = _read_json_object(paths["seed_integrity"], label="seed integrity")
    reviewer_a = _read_csv(paths["reviewer_a"], fields=REVIEWER_FIELDS, label="reviewer A")
    reviewer_b = _read_csv(paths["reviewer_b"], fields=REVIEWER_FIELDS, label="reviewer B")
    adjudication = _read_csv(
        paths["adjudication"], fields=ADJUDICATION_FIELDS, label="adjudication"
    )
    reference = finalize_reference(
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
        manifest_path=paths["manifest"],
        do_not_tune_path=paths["do_not_tune"],
        reviewer_a_kind=args.reviewer_a_kind,
        reviewer_b_kind=args.reviewer_b_kind,
        reviewer_a_provenance=args.reviewer_a_provenance,
        reviewer_b_provenance=args.reviewer_b_provenance,
        adjudicator_kind=args.adjudicator_kind,
        adjudicator_provenance=args.adjudicator_provenance,
        human_gold_attestation=args.human_gold_attestation,
    )
    output_paths = write_final_reference(
        reference,
        Path(args.output_dir),
        source_paths=paths,
    )
    print(json.dumps(reference["summary"], ensure_ascii=False, indent=2))
    print(f"evaluation_basis={reference['evaluation_basis']}")
    print(f"is_human_gold={str(reference['is_human_gold']).lower()}")
    for name, path in output_paths.items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
