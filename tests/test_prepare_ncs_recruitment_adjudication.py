from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "prepare_ncs_recruitment_adjudication.py"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _review(state: str, names: list[str], codes: list[str], marker: str):
    return {
        "answer": {
            "mapping_state": state,
            "detail_names": names,
            "detail_codes": codes,
        },
        "evidence": [{"quote": marker, "page": 1}],
        "confidence": "high",
    }


def _template(mod, item_id: str, digest: str) -> dict[str, str]:
    return {
        field: ""
        for field in mod.finalizer.ADJUDICATION_FIELDS
    } | {
        "item_id": item_id,
        "split": "gold_validation",
        "document_sha256": digest,
        "local_document_path": "private.pdf",
    }


def test_builds_consensus_and_disagreement_without_predictions() -> None:
    mod = load_module("adjudication_build")
    digest_a = "a" * 64
    digest_b = "b" * 64
    records = [
        {"item_id": "one", "document_sha256": digest_a},
        {"item_id": "two", "document_sha256": digest_b},
    ]
    official = _review("official_current", ["사무행정"], ["02020302"], "명시")
    absent = _review("not_stated", [], [], "미기재")
    reviews_a = {"one": official, "two": absent}
    reviews_b = {"one": official, "two": official}
    templates = {
        "one": _template(mod, "one", digest_a),
        "two": _template(mod, "two", digest_b),
    }
    packets = {
        "one": {"packet_path": "one.md", "packet_sha256": "1" * 64},
        "two": {"packet_path": "two.md", "packet_sha256": "2" * 64},
    }

    worklist, disputes = mod.build_worklist(
        records, reviews_a, reviews_b, templates, packets
    )

    assert worklist[0]["agreement_status"] == "agreement"
    assert worklist[0]["adjudication_status"] == "not_required_consensus"
    assert worklist[1]["agreement_status"] == "disagreement"
    assert worklist[1]["adjudication_status"] == "pending_third_party_adjudication"
    assert len(disputes) == 1
    assert disputes[0]["item_id"] == "two"
    assert disputes[0]["automatic_predictions_included"] is False
    assert "split" not in disputes[0]


def test_packet_index_rejects_tampering_and_incomplete_coverage(tmp_path: Path) -> None:
    mod = load_module("adjudication_packets")
    packet = tmp_path / "packet.md"
    packet.write_text("source", encoding="utf-8")
    digest = "a" * 64
    record_by_id = {"one": {"item_id": "one", "document_sha256": digest}}
    payload = {
        "source_only": True,
        "automatic_prediction_fields_included": False,
        "record_count": 1,
        "packets": [
            {
                "item_id": "one",
                "document_sha256": digest,
                "packet_path": str(packet),
                "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
                "source_only": True,
                "automatic_prediction_fields_included": False,
            }
        ],
    }
    index = tmp_path / "index.json"
    import json

    index.write_text(json.dumps(payload), encoding="utf-8")
    assert set(mod._load_packet_index(index, record_by_id=record_by_id)) == {"one"}

    packet.write_text("changed", encoding="utf-8")
    with pytest.raises(mod.AdjudicationPreparationError, match="integrity"):
        mod._load_packet_index(index, record_by_id=record_by_id)
