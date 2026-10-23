"""The Vercel Kordoc bridge uses the same OCR policy as the local runner.

A signed bridge request needs the production Ed25519 key, so this checks the
shipped source: Kordoc 4.24.1 must never get an unset ``ocr`` option (automatic
OCR when a model is cached) or ``ocr: true`` on the first pass.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_parses_text_layer_first_and_ocr_only_on_needs_ocr() -> None:
    bridge = (ROOT / "api" / "kordoc-parse.js").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "kordoc_parse.mjs").read_text(encoding="utf-8")

    assert "{ ocr: true } : {}" not in bridge
    assert "parseWithOcrPolicy(parse, bytes, req.headers[\"x-ncscope-ocr\"] === \"1\")" in bridge
    for source in (bridge, runner):
        assert 'warning?.code === "NEEDS_OCR"' in source
        assert "{ ocr: false }" in source
    # Every parse call in the bridge passes an explicit ocr option.
    assert re.findall(r"\bparse\((?:new Uint8Array\(bytes\)|bytes), \{ ocr: (?:true|false) \}\)", bridge) == [
        "parse(bytes, { ocr: false })",
        "parse(new Uint8Array(bytes), { ocr: false })",
        "parse(bytes, { ocr: true })",
    ]
    assert "parse(bytes, options)" not in bridge
