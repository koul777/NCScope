from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ALLOWED_DOCUMENT_STATES = {
    "explicit_detail",
    "declared_no_mapping",
    "no_explicit_ncs_detail",
    "parse_error",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_list(record: dict[str, Any], key: str) -> list[Any]:
    value = record.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{record.get('sha256')}: {key} must be a list")
    return value


def _validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("blind record must be an object")
    digest = str(record.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("blind record has invalid sha256")
    state = str(record.get("document_state") or "").strip()
    if state not in ALLOWED_DOCUMENT_STATES:
        raise ValueError(f"{digest}: invalid document_state {state!r}")

    detail_labels = _required_list(record, "expected_detail_labels")
    detail_codes = _required_list(record, "expected_detail_codes")
    non_current = _required_list(record, "non_current_or_custom_detail_labels")
    unit_codes = _required_list(record, "expected_ability_unit_codes")
    units_by_detail = record.get("expected_ability_units_by_detail")
    if not isinstance(units_by_detail, dict):
        raise ValueError(
            f"{digest}: expected_ability_units_by_detail must be an object"
        )
    detail_label_set = {str(label or "").strip() for label in detail_labels}
    if "" in detail_label_set or len(detail_label_set) != len(detail_labels):
        raise ValueError(f"{digest}: detail labels must be non-empty and unique")
    for item in detail_codes:
        if not isinstance(item, dict) or not re.fullmatch(
            r"\d{8}", str(item.get("code") or "")
        ):
            raise ValueError(f"{digest}: invalid expected detail code")
        if str(item.get("source_label") or "").strip() not in detail_label_set:
            raise ValueError(f"{digest}: detail code source label is not declared")
    for item in non_current:
        if (
            not isinstance(item, dict)
            or str(item.get("label") or "").strip() not in detail_label_set
        ):
            raise ValueError(f"{digest}: non-current detail label is not declared")
    for detail, units in units_by_detail.items():
        if not str(detail or "").strip() or not isinstance(units, list):
            raise ValueError(f"{digest}: invalid ability-unit scope entry")
        if str(detail).strip() not in detail_label_set:
            raise ValueError(f"{digest}: ability-unit scope is not a declared detail")
        cleaned_units = [str(unit or "").strip() for unit in units]
        if "" in cleaned_units or len(set(cleaned_units)) != len(cleaned_units):
            raise ValueError(f"{digest}: scoped ability units must be non-empty and unique")
    for item in unit_codes:
        if not isinstance(item, dict) or not re.fullmatch(
            r"\d{10}(?:_[0-9A-Za-z]+)?", str(item.get("code") or "")
        ):
            raise ValueError(f"{digest}: invalid expected ability-unit code")
        source_detail = str(item.get("source_detail_label") or "").strip()
        source_unit = str(item.get("source_unit_label") or "").strip()
        if source_detail not in detail_label_set:
            raise ValueError(f"{digest}: ability code detail is not declared")
        if source_unit not in units_by_detail.get(source_detail, []):
            raise ValueError(f"{digest}: ability code unit is not in scoped units")
    if state == "explicit_detail" and not detail_labels:
        raise ValueError(f"{digest}: explicit_detail requires a source label")
    if state != "explicit_detail" and (detail_labels or units_by_detail):
        raise ValueError(
            f"{digest}: non-explicit document cannot contain expected detail data"
        )

    return {
        "sha256": digest,
        "document_state": state,
        "expected_detail_labels": detail_labels,
        "expected_detail_codes": detail_codes,
        "non_current_or_custom_detail_labels": non_current,
        "expected_ability_units_by_detail": units_by_detail,
        "expected_ability_unit_codes": unit_codes,
        "confidence": record.get("confidence"),
        "unresolved_reason": record.get("unresolved_reason"),
    }


def build_reference(
    batches: list[tuple[str, dict[str, Any]]],
    *,
    expected_record_count: int | None = None,
    adjudications: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    batch_metadata: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch_name, payload in batches:
        if not isinstance(payload, dict):
            raise ValueError(f"{batch_name}: batch must be an object")
        reviewer_type = str(payload.get("reviewer_type") or "")
        if reviewer_type != "agent_source_evidence_review":
            raise ValueError(f"{batch_name}: unexpected reviewer_type")
        if payload.get("blind_completed_before_observed_comparison") is not True:
            raise ValueError(f"{batch_name}: blind completion is not confirmed")
        blind_records = payload.get("blind_records")
        if not isinstance(blind_records, list) or not blind_records:
            raise ValueError(f"{batch_name}: blind_records must be non-empty")
        blind_hash = canonical_sha256(blind_records)
        observed = payload.get("observed_comparison")
        declared_hash = (
            str(observed.get("blind_records_sha256") or "").strip().lower()
            if isinstance(observed, dict)
            else ""
        )
        if declared_hash != blind_hash:
            raise ValueError(f"{batch_name}: blind record hash mismatch")
        for raw_record in blind_records:
            record = _validate_record(raw_record)
            digest = record["sha256"]
            if digest in seen:
                raise ValueError(f"duplicate blind reference sha256: {digest}")
            seen.add(digest)
            records.append(record)
        batch_metadata.append(
            {
                "batch": batch_name,
                "record_count": len(blind_records),
                "blind_records_sha256": blind_hash,
            }
        )

    records.sort(key=lambda item: item["sha256"])
    batch_metadata.sort(key=lambda item: item["batch"])
    if expected_record_count is not None and len(records) != expected_record_count:
        raise ValueError(
            f"expected {expected_record_count} records, received {len(records)}"
        )

    adjudication_metadata: list[dict[str, Any]] = []
    records_by_sha = {record["sha256"]: record for record in records}
    for adjudication_name, payload in adjudications or []:
        if not isinstance(payload, dict):
            raise ValueError(f"{adjudication_name}: adjudication must be an object")
        if payload.get("reviewer_type") != "agent_source_evidence_cross_adjudication":
            raise ValueError(f"{adjudication_name}: unexpected reviewer_type")
        if payload.get("source_exact_corrections_only") is not True:
            raise ValueError(
                f"{adjudication_name}: source-exact correction scope is not confirmed"
            )
        corrections = payload.get("corrections")
        if not isinstance(corrections, list) or not corrections:
            raise ValueError(f"{adjudication_name}: corrections must be non-empty")
        for correction in corrections:
            if not isinstance(correction, dict):
                raise ValueError(f"{adjudication_name}: correction must be an object")
            if correction.get("operation") != "replace_ability_unit_label":
                raise ValueError(f"{adjudication_name}: unsupported correction operation")
            digest = str(correction.get("sha256") or "").strip().lower()
            record = records_by_sha.get(digest)
            if record is None:
                raise ValueError(f"{adjudication_name}: unknown record {digest}")
            detail = str(correction.get("detail_label") or "").strip()
            old = str(correction.get("from") or "").strip()
            new = str(correction.get("to") or "").strip()
            source_evidence = str(correction.get("source_evidence") or "")
            if not detail or not old or not new or old == new or new not in source_evidence:
                raise ValueError(f"{adjudication_name}: invalid source-exact correction")
            scoped_units = record["expected_ability_units_by_detail"].get(detail)
            if not isinstance(scoped_units, list) or scoped_units.count(old) != 1:
                raise ValueError(
                    f"{adjudication_name}: correction source label is not unique"
                )
            if new in scoped_units:
                raise ValueError(f"{adjudication_name}: correction target already exists")
            scoped_units[scoped_units.index(old)] = new
        adjudication_metadata.append(
            {
                "adjudication": adjudication_name,
                "correction_count": len(corrections),
                "corrections_sha256": canonical_sha256(corrections),
            }
        )

    records_hash = canonical_sha256(records)
    return {
        "schema_version": 1,
        "reference_id": f"stored-jd-agent-reviewed-{records_hash[:16]}",
        "reference_tier": (
            "agent_source_evidence_review_v2_cross_adjudicated"
            if adjudication_metadata
            else "agent_source_evidence_review_v1"
        ),
        "reviewer_type": "agent_source_evidence_review",
        "is_human_reviewed": False,
        "is_gold": False,
        "blind_completed_before_observed_comparison": True,
        "record_count": len(records),
        "records_sha256": records_hash,
        "source_batches": batch_metadata,
        "source_adjudications": adjudication_metadata,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a leakage-resistant, non-human stored-JD holdout reference "
            "from blind source-evidence review batches."
        )
    )
    parser.add_argument("batch_json", nargs="+")
    parser.add_argument(
        "--adjudication-json",
        action="append",
        default=[],
        help="Source-exact cross-adjudication file; may be supplied more than once.",
    )
    parser.add_argument(
        "--output",
        default="tests/fixtures/stored_jd_holdout_reference.json",
    )
    parser.add_argument("--expected-record-count", type=int, default=33)
    args = parser.parse_args()

    batches = []
    for value in args.batch_json:
        path = Path(value)
        batches.append(
            (
                path.stem,
                json.loads(path.read_text(encoding="utf-8")),
            )
        )
    reference = build_reference(
        batches,
        expected_record_count=args.expected_record_count,
        adjudications=[
            (
                Path(value).stem,
                json.loads(Path(value).read_text(encoding="utf-8")),
            )
            for value in args.adjudication_json
        ],
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in reference.items() if key != "records"}, ensure_ascii=False, indent=2))
    print(f"reference={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
