from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import finalize_ncs_recruitment_goldset as finalizer  # noqa: E402


WORKLIST_VERSION = "ncs_recruitment_adjudication_worklist_v1"


class AdjudicationPreparationError(ValueError):
    pass


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdjudicationPreparationError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise AdjudicationPreparationError(f"{label} must be a JSON object")
    return value


def _load_packet_index(
    path: Path, *, record_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    payload = _read_json(path, label="source packet index")
    if (
        payload.get("source_only") is not True
        or payload.get("automatic_prediction_fields_included") is not False
    ):
        raise AdjudicationPreparationError("source packet index is not source-only")
    rows = payload.get("packets")
    if not isinstance(rows, list) or payload.get("record_count") != len(rows):
        raise AdjudicationPreparationError("source packet index count is invalid")
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise AdjudicationPreparationError("source packet row is not an object")
        item_id = str(raw.get("item_id") or "").strip()
        if item_id not in record_by_id or item_id in output:
            raise AdjudicationPreparationError(
                "source packet coverage has an unknown or duplicate item"
            )
        record = record_by_id[item_id]
        digest = str(raw.get("document_sha256") or "").strip()
        if digest != str(record.get("document_sha256") or ""):
            raise AdjudicationPreparationError(f"{item_id}: packet digest mismatch")
        if (
            raw.get("source_only") is not True
            or raw.get("automatic_prediction_fields_included") is not False
        ):
            raise AdjudicationPreparationError(f"{item_id}: packet is not source-only")
        packet_path = Path(str(raw.get("packet_path") or "")).resolve()
        if not packet_path.is_file():
            raise AdjudicationPreparationError(f"{item_id}: packet file is missing")
        packet_digest = finalizer._require_sha256(
            raw.get("packet_sha256"), field=f"{item_id}.packet_sha256"
        )
        if finalizer.sha256_file(packet_path) != packet_digest:
            raise AdjudicationPreparationError(f"{item_id}: packet integrity mismatch")
        output[item_id] = {
            "item_id": item_id,
            "document_sha256": digest,
            "packet_path": str(packet_path),
            "packet_sha256": packet_digest,
        }
    if set(output) != set(record_by_id):
        raise AdjudicationPreparationError("source packet coverage is incomplete")
    return output


def build_worklist(
    records: Sequence[Mapping[str, Any]],
    reviews_a: Mapping[str, Mapping[str, Any]],
    reviews_b: Mapping[str, Mapping[str, Any]],
    adjudication_rows: Mapping[str, Mapping[str, Any]],
    packets: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    worklist: list[dict[str, Any]] = []
    disputes: list[dict[str, Any]] = []
    for record in records:
        item_id = str(record["item_id"])
        review_a = reviews_a[item_id]
        review_b = reviews_b[item_id]
        answer_a = review_a["answer"]
        answer_b = review_b["answer"]
        agreed = finalizer._answer_key(answer_a) == finalizer._answer_key(answer_b)
        row = dict(adjudication_rows[item_id])
        row.update(
            {
                "reviewer_a_status": "completed_independent_review",
                "reviewer_a_mapping_state": answer_a["mapping_state"],
                "reviewer_a_detail_names_json": json.dumps(
                    answer_a["detail_names"], ensure_ascii=False
                ),
                "reviewer_a_detail_codes_json": json.dumps(answer_a["detail_codes"]),
                "reviewer_b_status": "completed_independent_review",
                "reviewer_b_mapping_state": answer_b["mapping_state"],
                "reviewer_b_detail_names_json": json.dumps(
                    answer_b["detail_names"], ensure_ascii=False
                ),
                "reviewer_b_detail_codes_json": json.dumps(answer_b["detail_codes"]),
                "agreement_status": "agreement" if agreed else "disagreement",
                "adjudication_status": (
                    "not_required_consensus"
                    if agreed
                    else "pending_third_party_adjudication"
                ),
            }
        )
        for field in (
            "adjudicator_id",
            "adjudicated_at_utc",
            "final_mapping_state",
            "final_detail_names_json",
            "final_detail_codes_json",
            "final_evidence_json",
            "adjudication_rationale",
        ):
            row[field] = ""
        worklist.append(row)
        if agreed:
            continue
        packet = packets[item_id]
        disputes.append(
            {
                "item_id": item_id,
                "document_sha256": record["document_sha256"],
                "source_packet_path": packet["packet_path"],
                "source_packet_sha256": packet["packet_sha256"],
                "reviewer_a": {
                    "answer": answer_a,
                    "evidence": review_a["evidence"],
                    "confidence": review_a["confidence"],
                },
                "reviewer_b": {
                    "answer": answer_b,
                    "evidence": review_b["evidence"],
                    "confidence": review_b["confidence"],
                },
                "automatic_predictions_included": False,
            }
        )
    return worklist, disputes


def prepare_adjudication(
    *,
    manifest_path: Path,
    seed_integrity_path: Path,
    do_not_tune_path: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    adjudication_template_path: Path,
    packet_index_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir = finalizer.validate_local_output_dir(output_dir)
    manifest = _read_json(manifest_path, label="review manifest")
    integrity = _read_json(seed_integrity_path, label="seed integrity")
    records, record_by_id = finalizer._validate_seed(
        manifest,
        integrity,
        manifest_path=manifest_path,
        do_not_tune_path=do_not_tune_path,
    )
    reviewer_a_rows = finalizer._read_csv(
        reviewer_a_path, fields=finalizer.REVIEWER_FIELDS, label="reviewer A"
    )
    reviewer_b_rows = finalizer._read_csv(
        reviewer_b_path, fields=finalizer.REVIEWER_FIELDS, label="reviewer B"
    )
    reviews_a, reviewer_a_id = finalizer._validate_reviewer_rows(
        reviewer_a_rows, slot="A", record_by_id=record_by_id
    )
    reviews_b, reviewer_b_id = finalizer._validate_reviewer_rows(
        reviewer_b_rows, slot="B", record_by_id=record_by_id
    )
    if reviewer_a_id == reviewer_b_id:
        raise AdjudicationPreparationError("reviewer A and B identities must differ")
    template_rows = finalizer._read_csv(
        adjudication_template_path,
        fields=finalizer.ADJUDICATION_FIELDS,
        label="adjudication template",
    )
    adjudication_rows = finalizer._validate_adjudication_rows(
        template_rows, record_by_id=record_by_id
    )
    packets = _load_packet_index(packet_index_path, record_by_id=record_by_id)
    worklist, disputes = build_worklist(
        records, reviews_a, reviews_b, adjudication_rows, packets
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "adjudication_ready.local.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=finalizer.ADJUDICATION_FIELDS)
        writer.writeheader()
        writer.writerows(worklist)
    dispute_path = output_dir / "adjudication_disputes.local.json"
    dispute_payload = {
        "worklist_version": WORKLIST_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_only": True,
        "automatic_predictions_included": False,
        "record_count": len(records),
        "agreement_count": len(records) - len(disputes),
        "disagreement_count": len(disputes),
        "reviewer_a_id": reviewer_a_id,
        "reviewer_b_id": reviewer_b_id,
        "disputes": disputes,
    }
    dispute_path.write_text(
        json.dumps(dispute_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity_path = output_dir / "adjudication_worklist_integrity.local.json"
    integrity_payload = {
        "worklist_version": WORKLIST_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_only": True,
        "automatic_predictions_included": False,
        "input_sha256": {
            "manifest": finalizer.sha256_file(manifest_path),
            "seed_integrity": finalizer.sha256_file(seed_integrity_path),
            "do_not_tune": finalizer.sha256_file(do_not_tune_path),
            "reviewer_a": finalizer.sha256_file(reviewer_a_path),
            "reviewer_b": finalizer.sha256_file(reviewer_b_path),
            "adjudication_template": finalizer.sha256_file(adjudication_template_path),
            "packet_index": finalizer.sha256_file(packet_index_path),
        },
        "output_sha256": {
            "adjudication_csv": finalizer.sha256_file(csv_path),
            "dispute_json": finalizer.sha256_file(dispute_path),
        },
    }
    integrity_path.write_text(
        json.dumps(integrity_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "adjudication_csv": csv_path,
        "dispute_json": dispute_path,
        "integrity": integrity_path,
        "record_count": len(records),
        "agreement_count": len(records) - len(disputes),
        "disagreement_count": len(disputes),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two completed independent NCS reviews and create a source-only "
            "third-party adjudication worklist for disagreements."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--seed-integrity", required=True)
    parser.add_argument("--do-not-tune", required=True)
    parser.add_argument("--reviewer-a", required=True)
    parser.add_argument("--reviewer-b", required=True)
    parser.add_argument("--adjudication-template", required=True)
    parser.add_argument("--packet-index", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = prepare_adjudication(
        manifest_path=Path(args.manifest).resolve(),
        seed_integrity_path=Path(args.seed_integrity).resolve(),
        do_not_tune_path=Path(args.do_not_tune).resolve(),
        reviewer_a_path=Path(args.reviewer_a).resolve(),
        reviewer_b_path=Path(args.reviewer_b).resolve(),
        adjudication_template_path=Path(args.adjudication_template).resolve(),
        packet_index_path=Path(args.packet_index).resolve(),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
