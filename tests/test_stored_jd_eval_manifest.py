from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_stored_jd_eval_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("build_stored_jd_eval_manifest", SCRIPT_PATH)
assert SPEC and SPEC.loader
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


def test_split_is_stable_and_rejects_invalid_hash() -> None:
    digest = "a" * 64
    assert manifest.deterministic_split(digest) == manifest.deterministic_split(digest)
    with pytest.raises(ValueError):
        manifest.deterministic_split("not-a-hash")


def test_manifest_collapses_duplicates_without_claiming_gold() -> None:
    duplicate_hash = "0" * 64
    other_hash = "1" * 64
    rows = [
        {"filename": "b.pdf", "sha256": duplicate_hash, "status": "mcp_exact"},
        {"filename": "a.pdf", "sha256": duplicate_hash, "status": "mcp_exact"},
        {"filename": "c.hwp", "sha256": other_hash, "status": "no_detail"},
    ]

    result = manifest.build_manifest_rows(rows)

    assert len(result) == 2
    assert result[0]["representative_filename"] == "a.pdf"
    assert result[0]["file_count"] == 2
    assert result[0]["annotation_status"] == "pending_human_review"
    assert result[0]["expected_details"] == ""
    assert result[0]["split"] == manifest.deterministic_split(duplicate_hash)
