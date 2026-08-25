from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "apply_ncs_recruitment_adjudication.py"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _worklist_row(mod, item_id: str, digest: str, *, disagreement: bool) -> dict[str, str]:
    row = {field: "" for field in mod.finalizer.ADJUDICATION_FIELDS}
    row.update(
        {
            "item_id": item_id,
            "split": "gold_validation",
            "document_sha256": digest,
            "local_document_path": "private.pdf",
            "agreement_status": "disagreement" if disagreement else "agreement",
            "adjudication_status": (
                "pending_third_party_adjudication"
                if disagreement
                else "not_required_consensus"
            ),
        }
    )
    return row


def _decision(mod, item_id: str, digest: str) -> dict[str, str]:
    row = {field: "" for field in mod.preparation.ADJUDICATOR_DECISION_FIELDS}
    row.update(
        {
            "item_id": item_id,
            "document_sha256": digest,
            "adjudicator_id": "agent-c",
            "adjudicated_at_utc": "2026-08-25T03:00:00Z",
            "final_mapping_state": "official_current",
            "final_detail_names_json": json.dumps(["경영기획"], ensure_ascii=False),
            "final_detail_codes_json": json.dumps(["02010101"]),
            "final_evidence_json": json.dumps(
                [{"quote": "세분류 01. 경영기획", "section": "NCS 분류체계"}],
                ensure_ascii=False,
            ),
            "adjudication_rationale": "현행 카탈로그의 정확한 명칭·코드 쌍이다.",
        }
    )
    return row


def _disputes(
    item_id: str,
    digest: str,
    *,
    packet_path: Path | None = None,
    packet_sha256: str | None = None,
) -> dict:
    dispute = {
        "item_id": item_id,
        "document_sha256": digest,
        "automatic_predictions_included": False,
    }
    if packet_path is not None:
        dispute["source_packet_path"] = str(packet_path)
    if packet_sha256 is not None:
        dispute["source_packet_sha256"] = packet_sha256
    return {
        "source_only": True,
        "automatic_predictions_included": False,
        "disagreement_count": 1,
        "reviewer_a_id": "agent-a",
        "reviewer_b_id": "agent-b",
        "disputes": [dispute],
    }


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_applies_blind_decision_without_exposing_internal_metadata() -> None:
    mod = load_module("apply_adjudication_happy")
    digest_a = "a" * 64
    digest_b = "b" * 64
    worklist = [
        _worklist_row(mod, "one", digest_a, disagreement=False),
        _worklist_row(mod, "two", digest_b, disagreement=True),
    ]
    decision = _decision(mod, "two", digest_b)

    completed = mod.apply_decisions(
        worklist, _disputes("two", digest_b), [decision]
    )

    assert completed[0]["adjudication_status"] == "not_required_consensus"
    assert completed[0]["adjudicator_id"] == ""
    assert completed[1]["adjudication_status"] == (
        "completed_third_party_adjudication"
    )
    assert completed[1]["adjudicator_id"] == "agent-c"
    assert completed[1]["final_mapping_state"] == "official_current"
    assert json.loads(completed[1]["final_detail_codes_json"]) == ["02010101"]
    assert set(decision).isdisjoint({"split", "local_document_path", "source_url"})


def test_rejects_non_blind_schema_and_non_distinct_adjudicator() -> None:
    mod = load_module("apply_adjudication_rejections")
    digest = "a" * 64
    worklist = [_worklist_row(mod, "one", digest, disagreement=True)]
    payload = _disputes("one", digest)
    decision = _decision(mod, "one", digest)
    decision["split"] = "gold_holdout"

    with pytest.raises(mod.AdjudicationDecisionError, match="blind schema"):
        mod.apply_decisions(worklist, payload, [decision])

    decision.pop("split")
    decision["adjudicator_id"] = "agent-a"
    with pytest.raises(mod.AdjudicationDecisionError, match="distinct third"):
        mod.apply_decisions(worklist, payload, [decision])


def test_rejects_incomplete_or_non_source_only_disputes() -> None:
    mod = load_module("apply_adjudication_coverage")
    digest = "a" * 64
    worklist = [_worklist_row(mod, "one", digest, disagreement=True)]
    payload = _disputes("one", digest)

    with pytest.raises(mod.AdjudicationDecisionError, match="incomplete"):
        mod.apply_decisions(worklist, payload, [])

    payload["automatic_predictions_included"] = True
    with pytest.raises(mod.AdjudicationDecisionError, match="not source-only"):
        mod.apply_decisions(worklist, payload, [_decision(mod, "one", digest)])


def test_all_consensus_worklist_accepts_empty_decision_file() -> None:
    mod = load_module("apply_adjudication_no_disputes")
    digest = "a" * 64
    worklist = [_worklist_row(mod, "one", digest, disagreement=False)]
    payload = {
        "source_only": True,
        "automatic_predictions_included": False,
        "disagreement_count": 0,
        "reviewer_a_id": "agent-a",
        "reviewer_b_id": "agent-b",
        "disputes": [],
    }

    completed = mod.apply_decisions(worklist, payload, [])

    assert completed == worklist


def test_apply_decision_files_verifies_immutable_worklist_inputs(
    tmp_path: Path,
) -> None:
    mod = load_module("apply_adjudication_files")
    mod.finalizer.ROOT = tmp_path
    input_dir = tmp_path / "private"
    output_dir = tmp_path / "tmp" / "completed"
    input_dir.mkdir()
    digest = "a" * 64
    worklist_path = input_dir / "worklist.csv"
    dispute_path = input_dir / "disputes.json"
    decision_path = input_dir / "decisions.csv"
    integrity_path = input_dir / "worklist_integrity.json"
    packet_path = input_dir / "source.md"
    packet_path.write_text("세분류 01. 경영기획", encoding="utf-8")
    packet_sha256 = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    _write_csv(
        worklist_path,
        mod.finalizer.ADJUDICATION_FIELDS,
        [_worklist_row(mod, "one", digest, disagreement=True)],
    )
    dispute_path.write_text(
        json.dumps(
            _disputes(
                "one",
                digest,
                packet_path=packet_path,
                packet_sha256=packet_sha256,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_csv(
        decision_path,
        mod.preparation.ADJUDICATOR_DECISION_FIELDS,
        [_decision(mod, "one", digest)],
    )
    integrity_path.write_text(
        json.dumps(
            {
                "worklist_version": mod.preparation.WORKLIST_VERSION,
                "generated_at_utc": "2026-08-25T02:30:00+00:00",
                "source_only": True,
                "automatic_predictions_included": False,
                "record_count": 1,
                "agreement_count": 0,
                "disagreement_count": 1,
                "output_sha256": {
                    "adjudication_csv": hashlib.sha256(
                        worklist_path.read_bytes()
                    ).hexdigest(),
                    "dispute_json": hashlib.sha256(
                        dispute_path.read_bytes()
                    ).hexdigest(),
                    "decision_template": "f" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    result = mod.apply_decision_files(
        worklist_path=worklist_path,
        dispute_path=dispute_path,
        decision_path=decision_path,
        worklist_integrity_path=integrity_path,
        output_dir=output_dir,
    )

    assert result["record_count"] == 1
    assert result["adjudicated_count"] == 1
    assert result["adjudication_completed"].is_file()
    assert result["integrity"].is_file()

    worklist_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(mod.AdjudicationDecisionError, match="integrity mismatch"):
        mod.apply_decision_files(
            worklist_path=worklist_path,
            dispute_path=dispute_path,
            decision_path=decision_path,
            worklist_integrity_path=integrity_path,
            output_dir=output_dir,
        )


def test_apply_decision_files_rejects_unsealed_evidence_quote(tmp_path: Path) -> None:
    mod = load_module("apply_adjudication_quote_integrity")
    mod.finalizer.ROOT = tmp_path
    input_dir = tmp_path / "private"
    output_dir = tmp_path / "tmp" / "completed"
    input_dir.mkdir()
    digest = "a" * 64
    packet_path = input_dir / "source.md"
    packet_path.write_text("세분류 01. 경영기획", encoding="utf-8")
    packet_sha256 = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    worklist_path = input_dir / "worklist.csv"
    dispute_path = input_dir / "disputes.json"
    decision_path = input_dir / "decisions.csv"
    integrity_path = input_dir / "worklist_integrity.json"
    _write_csv(
        worklist_path,
        mod.finalizer.ADJUDICATION_FIELDS,
        [_worklist_row(mod, "one", digest, disagreement=True)],
    )
    dispute_path.write_text(
        json.dumps(
            _disputes(
                "one",
                digest,
                packet_path=packet_path,
                packet_sha256=packet_sha256,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    decision = _decision(mod, "one", digest)
    decision["final_evidence_json"] = json.dumps(
        [{"quote": "원문에 없는 인용", "section": "NCS 분류체계"}],
        ensure_ascii=False,
    )
    _write_csv(
        decision_path,
        mod.preparation.ADJUDICATOR_DECISION_FIELDS,
        [decision],
    )
    integrity_path.write_text(
        json.dumps(
            {
                "worklist_version": mod.preparation.WORKLIST_VERSION,
                "generated_at_utc": "2026-08-25T02:30:00+00:00",
                "source_only": True,
                "automatic_predictions_included": False,
                "record_count": 1,
                "agreement_count": 0,
                "disagreement_count": 1,
                "output_sha256": {
                    "adjudication_csv": hashlib.sha256(
                        worklist_path.read_bytes()
                    ).hexdigest(),
                    "dispute_json": hashlib.sha256(
                        dispute_path.read_bytes()
                    ).hexdigest(),
                    "decision_template": "f" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(mod.AdjudicationDecisionError, match="absent from source"):
        mod.apply_decision_files(
            worklist_path=worklist_path,
            dispute_path=dispute_path,
            decision_path=decision_path,
            worklist_integrity_path=integrity_path,
            output_dir=output_dir,
        )
