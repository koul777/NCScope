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
from scripts import prepare_ncs_recruitment_adjudication as preparation  # noqa: E402


APPLIED_VERSION = "ncs_recruitment_adjudication_decisions_v2"


class AdjudicationDecisionError(ValueError):
    pass


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdjudicationDecisionError(f"{label} is not readable JSON") from exc
    if not isinstance(payload, dict):
        raise AdjudicationDecisionError(f"{label} must be a JSON object")
    return payload


def apply_decisions(
    worklist_rows: Sequence[Mapping[str, Any]],
    dispute_payload: Mapping[str, Any],
    decision_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if (
        dispute_payload.get("source_only") is not True
        or dispute_payload.get("automatic_predictions_included") is not False
    ):
        raise AdjudicationDecisionError("dispute payload is not source-only")
    raw_disputes = dispute_payload.get("disputes")
    if not isinstance(raw_disputes, list):
        raise AdjudicationDecisionError("dispute payload rows are invalid")
    try:
        declared_count = int(dispute_payload.get("disagreement_count"))
    except (TypeError, ValueError) as exc:
        raise AdjudicationDecisionError("dispute count is invalid") from exc
    if declared_count != len(raw_disputes):
        raise AdjudicationDecisionError("dispute count does not match rows")

    reviewer_a_id = str(dispute_payload.get("reviewer_a_id") or "").strip()
    reviewer_b_id = str(dispute_payload.get("reviewer_b_id") or "").strip()
    if not reviewer_a_id or not reviewer_b_id or reviewer_a_id == reviewer_b_id:
        raise AdjudicationDecisionError("independent reviewer identities are invalid")

    dispute_by_id: dict[str, Mapping[str, Any]] = {}
    for dispute in raw_disputes:
        if not isinstance(dispute, dict):
            raise AdjudicationDecisionError("dispute row is not an object")
        item_id = str(dispute.get("item_id") or "").strip()
        if not item_id or item_id in dispute_by_id:
            raise AdjudicationDecisionError("dispute item ID is blank or duplicated")
        if dispute.get("automatic_predictions_included") is not False:
            raise AdjudicationDecisionError(f"{item_id}: prediction data is not allowed")
        dispute_by_id[item_id] = dispute

    worklist_by_id: dict[str, dict[str, Any]] = {}
    worklist_dispute_ids: set[str] = set()
    for raw in worklist_rows:
        row = dict(raw)
        item_id = str(row.get("item_id") or "").strip()
        if not item_id or item_id in worklist_by_id:
            raise AdjudicationDecisionError("worklist item ID is blank or duplicated")
        worklist_by_id[item_id] = row
        if row.get("agreement_status") == "disagreement":
            if row.get("adjudication_status") != "pending_third_party_adjudication":
                raise AdjudicationDecisionError(
                    f"{item_id}: dispute worklist status is invalid"
                )
            worklist_dispute_ids.add(item_id)
    if worklist_dispute_ids != set(dispute_by_id):
        raise AdjudicationDecisionError("worklist/dispute coverage mismatch")

    decisions_by_id: dict[str, Mapping[str, Any]] = {}
    adjudicator_ids: set[str] = set()
    for row in decision_rows:
        if list(row) != preparation.ADJUDICATOR_DECISION_FIELDS:
            raise AdjudicationDecisionError("decision schema is not the blind schema")
        if set(row).intersection({"split", "local_document_path", "source_url"}):
            raise AdjudicationDecisionError("decision row exposes forbidden metadata")
        item_id = str(row.get("item_id") or "").strip()
        if item_id not in dispute_by_id or item_id in decisions_by_id:
            raise AdjudicationDecisionError(
                "decision coverage has an unknown or duplicate item"
            )
        dispute = dispute_by_id[item_id]
        if str(row.get("document_sha256") or "") != str(
            dispute.get("document_sha256") or ""
        ):
            raise AdjudicationDecisionError(f"{item_id}: decision digest mismatch")
        adjudicator_id = str(row.get("adjudicator_id") or "").strip()
        if not adjudicator_id or adjudicator_id in {reviewer_a_id, reviewer_b_id}:
            raise AdjudicationDecisionError(
                f"{item_id}: adjudicator must be a distinct third reviewer"
            )
        adjudicator_ids.add(adjudicator_id)
        reviewed_at = finalizer._require_utc_timestamp(
            row.get("adjudicated_at_utc"),
            field=f"decision.{item_id}.adjudicated_at_utc",
        )
        answer = finalizer._normalize_answer(
            row.get("final_mapping_state"),
            row.get("final_detail_names_json"),
            row.get("final_detail_codes_json"),
            prefix=f"decision.{item_id}",
        )
        evidence = finalizer._normalize_evidence(
            row.get("final_evidence_json"),
            field=f"decision.{item_id}.final_evidence_json",
        )
        rationale = str(row.get("adjudication_rationale") or "").strip()
        if not rationale:
            raise AdjudicationDecisionError(
                f"{item_id}: adjudication rationale is required"
            )
        decisions_by_id[item_id] = {
            "adjudicator_id": adjudicator_id,
            "adjudicated_at_utc": reviewed_at,
            "answer": answer,
            "evidence": evidence,
            "rationale": rationale,
        }

    if set(decisions_by_id) != set(dispute_by_id):
        raise AdjudicationDecisionError("decision coverage is incomplete")
    if dispute_by_id and len(adjudicator_ids) != 1:
        raise AdjudicationDecisionError(
            "all decisions must use exactly one adjudicator identity"
        )

    completed: list[dict[str, Any]] = []
    for raw in worklist_rows:
        row = dict(raw)
        decision = decisions_by_id.get(str(row["item_id"]))
        if decision is not None:
            answer = decision["answer"]
            row.update(
                {
                    "adjudication_status": "completed_third_party_adjudication",
                    "adjudicator_id": decision["adjudicator_id"],
                    "adjudicated_at_utc": decision["adjudicated_at_utc"],
                    "final_mapping_state": answer["mapping_state"],
                    "final_detail_names_json": json.dumps(
                        answer["detail_names"], ensure_ascii=False
                    ),
                    "final_detail_codes_json": json.dumps(answer["detail_codes"]),
                    "final_evidence_json": json.dumps(
                        decision["evidence"], ensure_ascii=False
                    ),
                    "adjudication_rationale": decision["rationale"],
                }
            )
        completed.append(row)
    return completed


def _validate_decision_evidence_against_packets(
    dispute_payload: Mapping[str, Any],
    decision_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Require every adjudicator quote to exist in the sealed source packet."""

    raw_disputes = dispute_payload.get("disputes")
    if not isinstance(raw_disputes, list):
        raise AdjudicationDecisionError("dispute payload rows are invalid")
    disputes = {
        str(dispute.get("item_id") or "").strip(): dispute
        for dispute in raw_disputes
        if isinstance(dispute, dict)
    }
    for row in decision_rows:
        item_id = str(row.get("item_id") or "").strip()
        dispute = disputes.get(item_id)
        if dispute is None:
            raise AdjudicationDecisionError(
                f"{item_id}: decision has no sealed source packet"
            )
        packet_path = Path(str(dispute.get("source_packet_path") or "")).resolve()
        packet_digest = finalizer._require_sha256(
            dispute.get("source_packet_sha256"),
            field=f"dispute.{item_id}.source_packet_sha256",
        )
        if not packet_path.is_file():
            raise AdjudicationDecisionError(f"{item_id}: source packet is missing")
        if finalizer.sha256_file(packet_path) != packet_digest:
            raise AdjudicationDecisionError(
                f"{item_id}: source packet integrity mismatch"
            )
        try:
            packet_text = packet_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise AdjudicationDecisionError(
                f"{item_id}: source packet is not readable text"
            ) from exc
        evidence = finalizer._normalize_evidence(
            row.get("final_evidence_json"),
            field=f"decision.{item_id}.final_evidence_json",
        )
        for index, item in enumerate(evidence):
            quote = str(item.get("quote") or "").strip()
            if not quote:
                raise AdjudicationDecisionError(
                    f"{item_id}: evidence[{index}] requires an exact source quote"
                )
            normalized_packet = packet_text.replace("\r\n", "\n").replace("\r", "\n")
            normalized_quote = quote.replace("\r\n", "\n").replace("\r", "\n")
            if normalized_quote not in normalized_packet:
                raise AdjudicationDecisionError(
                    f"{item_id}: evidence[{index}] quote is absent from source packet"
                )


def apply_decision_files(
    *,
    worklist_path: Path,
    dispute_path: Path,
    decision_path: Path,
    worklist_integrity_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir = finalizer.validate_local_output_dir(output_dir)
    worklist_integrity = _read_json(
        worklist_integrity_path, label="adjudication worklist integrity"
    )
    if worklist_integrity.get("worklist_version") != preparation.WORKLIST_VERSION:
        raise AdjudicationDecisionError("adjudication worklist version is invalid")
    finalizer._require_utc_timestamp(
        worklist_integrity.get("generated_at_utc"),
        field="adjudication worklist integrity.generated_at_utc",
    )
    if (
        worklist_integrity.get("source_only") is not True
        or worklist_integrity.get("automatic_predictions_included") is not False
    ):
        raise AdjudicationDecisionError(
            "adjudication worklist integrity is not source-only"
        )
    output_hashes = worklist_integrity.get("output_sha256")
    if not isinstance(output_hashes, dict):
        raise AdjudicationDecisionError("worklist integrity outputs are invalid")
    expected_inputs = {
        "adjudication_csv": worklist_path,
        "dispute_json": dispute_path,
        "decision_template": decision_path,
    }
    for name, path in expected_inputs.items():
        expected = finalizer._require_sha256(
            output_hashes.get(name), field=f"worklist_integrity.{name}"
        )
        if finalizer.sha256_file(path) != expected and name != "decision_template":
            raise AdjudicationDecisionError(f"{name} integrity mismatch")
    # The adjudicator necessarily edits the decision template. Bind its fixed
    # item/digest columns below and preserve both pre/post hashes in the output.

    worklist_rows = finalizer._read_csv(
        worklist_path,
        fields=finalizer.ADJUDICATION_FIELDS,
        label="adjudication worklist",
    )
    dispute_payload = _read_json(dispute_path, label="adjudication disputes")
    decision_rows = finalizer._read_csv(
        decision_path,
        fields=preparation.ADJUDICATOR_DECISION_FIELDS,
        label="adjudicator decisions",
    )
    dispute_count = len(dispute_payload.get("disputes") or [])
    if worklist_integrity.get("record_count") != len(worklist_rows):
        raise AdjudicationDecisionError("worklist integrity record count mismatch")
    if worklist_integrity.get("disagreement_count") != dispute_count:
        raise AdjudicationDecisionError("worklist integrity disagreement count mismatch")
    if worklist_integrity.get("agreement_count") != len(worklist_rows) - dispute_count:
        raise AdjudicationDecisionError("worklist integrity agreement count mismatch")
    completed = apply_decisions(worklist_rows, dispute_payload, decision_rows)
    _validate_decision_evidence_against_packets(dispute_payload, decision_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    completed_path = output_dir / "adjudication_completed.local.csv"
    with completed_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=finalizer.ADJUDICATION_FIELDS)
        writer.writeheader()
        writer.writerows(completed)
    integrity_path = output_dir / "adjudication_decisions_integrity.local.json"
    integrity_payload = {
        "applied_version": APPLIED_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_only": True,
        "automatic_predictions_included": False,
        "input_sha256": {
            "worklist": finalizer.sha256_file(worklist_path),
            "disputes": finalizer.sha256_file(dispute_path),
            "decisions_completed": finalizer.sha256_file(decision_path),
            "worklist_integrity": finalizer.sha256_file(worklist_integrity_path),
            "decision_template_original": str(output_hashes["decision_template"]),
        },
        "output_sha256": {
            "adjudication_completed": finalizer.sha256_file(completed_path)
        },
        "record_count": len(completed),
        "adjudicated_count": len(dispute_payload["disputes"]),
    }
    integrity_path.write_text(
        json.dumps(integrity_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "adjudication_completed": completed_path,
        "integrity": integrity_path,
        "record_count": len(completed),
        "adjudicated_count": len(dispute_payload["disputes"]),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a blind adjudicator decision file and merge it into the "
            "internal NCS recruitment adjudication worklist."
        )
    )
    parser.add_argument("--worklist", required=True)
    parser.add_argument("--disputes", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--worklist-integrity", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = apply_decision_files(
            worklist_path=Path(args.worklist).resolve(),
            dispute_path=Path(args.disputes).resolve(),
            decision_path=Path(args.decisions).resolve(),
            worklist_integrity_path=Path(args.worklist_integrity).resolve(),
            output_dir=Path(args.output_dir),
        )
    except (
        AdjudicationDecisionError,
        finalizer.GoldsetFinalizationError,
        OSError,
    ) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
