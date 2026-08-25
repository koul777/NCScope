from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.prepare_ncs_recruitment_source_packets import (
    SourcePacketError,
    build_source_packets,
)


def _manifest(tmp_path: Path, document: Path) -> Path:
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    payload = {
        "summary": {
            "unique_document_count": 1,
            "automatic_predictions_are_gold": False,
            "is_gold": False,
        },
        "records": [
            {
                "item_id": f"nrg-{digest}",
                "split": "gold_holdout",
                "document_sha256": digest,
                "local_document_path": str(document),
            }
        ],
    }
    path = tmp_path / "manifest.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_build_source_packets_uses_allowlisted_source_text_only(
    tmp_path: Path,
) -> None:
    document = tmp_path / "job.pdf"
    document.write_bytes(b"source bytes")
    manifest = _manifest(tmp_path, document)
    output_dir = tmp_path / "tmp" / "packets"

    result = build_source_packets(
        manifest,
        output_dir,
        parse_fn=lambda *_args, **_kwargs: {
            "markdown": "# Source table\nNCS detail: Office Admin",
            "fields": {
                "ncs_detail_candidates": ["PREDICTION MUST NOT LEAK"],
                "observed_detail_codes": ["00000000"],
            },
        },
        workspace_root=tmp_path,
    )

    index = json.loads(result["index"].read_text(encoding="utf-8"))
    packet = Path(index["packets"][0]["packet_path"]).read_text(encoding="utf-8")
    assert result["packet_count"] == 1
    assert "NCS detail: Office Admin" in packet
    assert "PREDICTION MUST NOT LEAK" not in packet
    assert "00000000" not in packet
    assert index["automatic_prediction_fields_included"] is False
    assert index["packets"][0]["packet_sha256"]
    assert "split" not in index["packets"][0]


def test_build_source_packets_rejects_document_tampering(tmp_path: Path) -> None:
    document = tmp_path / "job.txt"
    document.write_text("original", encoding="utf-8")
    manifest = _manifest(tmp_path, document)
    document.write_text("tampered", encoding="utf-8")

    with pytest.raises(SourcePacketError, match="digest mismatch"):
        build_source_packets(
            manifest,
            tmp_path / "tmp" / "packets",
            parse_fn=lambda *_args, **_kwargs: {"text": "tampered"},
            workspace_root=tmp_path,
        )


def test_build_source_packets_rejects_prediction_tainted_manifest(
    tmp_path: Path,
) -> None:
    document = tmp_path / "job.txt"
    document.write_text("source", encoding="utf-8")
    manifest = _manifest(tmp_path, document)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["summary"]["automatic_predictions_are_gold"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourcePacketError, match="provenance is unsafe"):
        build_source_packets(
            manifest,
            tmp_path / "tmp" / "packets",
            parse_fn=lambda *_args, **_kwargs: {"text": "source"},
            workspace_root=tmp_path,
        )
