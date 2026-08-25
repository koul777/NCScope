from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc  # noqa: E402
from scripts.prepare_ncs_recruitment_goldset import (  # noqa: E402
    GoldsetPreparationError,
    canonical_json_bytes,
    require_sha256,
    sha256_file,
    validate_local_output_dir,
)


PACKET_VERSION = "ncs_recruitment_source_only_review_packet_v1"
ITEM_ID_RE = re.compile(r"^nrg-([0-9a-f]{64})$")


class SourcePacketError(ValueError):
    pass


def _source_text(parsed: Mapping[str, Any], *, raw_bytes: bytes, suffix: str) -> str:
    for key in ("markdown", "text"):
        value = str(parsed.get(key) or "").strip()
        if value:
            return value
    if suffix.lower() == ".txt":
        return raw_bytes.decode("utf-8-sig", errors="replace").strip()
    return ""


def _load_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SourcePacketError("review manifest must be a JSON object")
    summary = payload.get("summary")
    records = payload.get("records")
    if not isinstance(summary, dict) or not isinstance(records, list) or not records:
        raise SourcePacketError("review manifest summary/records are missing")
    if summary.get("automatic_predictions_are_gold") is not False:
        raise SourcePacketError("review manifest prediction provenance is unsafe")
    if summary.get("is_gold") is not False:
        raise SourcePacketError("review manifest must not already claim gold status")
    if int(summary.get("unique_document_count") or 0) != len(records):
        raise SourcePacketError("review manifest record count mismatch")
    return summary, [dict(record) for record in records if isinstance(record, dict)]


def build_source_packets(
    manifest_path: Path,
    output_dir: Path,
    *,
    parse_fn: Callable[..., Mapping[str, Any]] = parse_with_kordoc,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    output_dir = validate_local_output_dir(
        output_dir,
        root=ROOT if workspace_root is None else workspace_root,
    )
    summary, records = _load_manifest(manifest_path)
    if len(records) != int(summary["unique_document_count"]):
        raise SourcePacketError("review manifest contains non-object records")

    packets_dir = output_dir / "source_packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    packet_rows: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    for record in sorted(records, key=lambda item: str(item.get("item_id") or "")):
        item_id = str(record.get("item_id") or "").strip()
        item_match = ITEM_ID_RE.fullmatch(item_id)
        if not item_match or item_id in seen_item_ids:
            raise SourcePacketError(f"invalid or duplicate item_id: {item_id or '<blank>'}")
        seen_item_ids.add(item_id)
        expected_digest = require_sha256(
            record.get("document_sha256"), field=f"{item_id}.document_sha256"
        )
        if item_match.group(1) != expected_digest:
            raise SourcePacketError(f"{item_id}: item_id/document digest mismatch")
        document_path = Path(str(record.get("local_document_path") or "")).resolve()
        if not document_path.is_file():
            raise SourcePacketError(f"{item_id}: source document is missing")
        if sha256_file(document_path) != expected_digest:
            raise SourcePacketError(f"{item_id}: source document digest mismatch")

        raw_bytes = document_path.read_bytes()
        try:
            parsed = parse_fn(raw_bytes, filename=document_path.name, ocr=False)
        except (KordocParseError, OSError, ValueError) as exc:
            raise SourcePacketError(f"{item_id}: source extraction failed") from exc
        if not isinstance(parsed, Mapping):
            raise SourcePacketError(f"{item_id}: source parser returned an invalid envelope")
        source_text = _source_text(
            parsed,
            raw_bytes=raw_bytes,
            suffix=document_path.suffix,
        )
        if not source_text:
            raise SourcePacketError(f"{item_id}: source packet text is empty")

        packet_path = packets_dir / f"{item_id}.source.md"
        header = (
            f"<!-- packet_version: {PACKET_VERSION} -->\n"
            f"<!-- item_id: {item_id} -->\n"
            f"<!-- document_sha256: {expected_digest} -->\n"
            "<!-- source_only: true; automatic NCS predictions intentionally omitted -->\n\n"
        )
        packet_path.write_text(header + source_text.rstrip() + "\n", encoding="utf-8")
        packet_rows.append(
            {
                "item_id": item_id,
                "split": str(record.get("split") or ""),
                "document_sha256": expected_digest,
                "packet_path": str(packet_path.resolve()),
                "packet_sha256": sha256_file(packet_path),
                "source_only": True,
                "automatic_prediction_fields_included": False,
            }
        )

    index = {
        "packet_version": PACKET_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": sha256_file(manifest_path),
        "record_count": len(packet_rows),
        "source_only": True,
        "automatic_prediction_fields_included": False,
        "packets_sha256": hashlib.sha256(canonical_json_bytes(packet_rows)).hexdigest(),
        "packets": packet_rows,
    }
    index_path = output_dir / "source_packet_index.local.json"
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = {
        "packet_version": PACKET_VERSION,
        "index_sha256": sha256_file(index_path),
        "packet_count": len(packet_rows),
        "packet_files": {
            Path(row["packet_path"]).name: row["packet_sha256"] for row in packet_rows
        },
    }
    integrity_path = output_dir / "source_packet_integrity.local.json"
    integrity_path.write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "index": index_path,
        "integrity": integrity_path,
        "packet_count": len(packet_rows),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create source-only review packets from a private NCS recruitment "
            "goldset manifest without exposing automatic NCS predictions."
        )
    )
    parser.add_argument("manifest_json")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "tmp" / "ncs_recruitment_goldset"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = build_source_packets(
            Path(args.manifest_json).resolve(),
            Path(args.output_dir),
        )
    except (GoldsetPreparationError, SourcePacketError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "passed": True,
                "packet_count": result["packet_count"],
                "index": str(result["index"]),
                "integrity": str(result["integrity"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
