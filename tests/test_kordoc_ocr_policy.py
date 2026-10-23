"""Kordoc 4.24.1 OCR policy of the shipped ``scripts/kordoc_parse.mjs`` runner.

Kordoc 4.24.1 changed two OCR defaults that 4.9.1 did not have:

* ``ocr`` left unset runs the built-in OCR automatically whenever the PP-OCR
  model happens to be in ``~/.cache/kordoc`` (machine-dependent output).
* ``ocr: true`` also reads every picture without a text layer on normal text
  pages (PDF) and every inserted picture (HWP/HWPX/DOCX). A ten-page text PDF
  with a logo per page went from 1.9 s to 81.6 s and its tables changed.

The runner must keep 4.9.1's behaviour: OCR only when Kordoc reports that the
text layer is missing (``NEEDS_OCR``) or the text-layer parse fails.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import kordoc_parser

# The fake module records the ``ocr`` option of every ``parse`` call in the
# returned markdown, so the test sees exactly which passes the runner made.
_FAKE_KORDOC_MODULE = r"""
export const VERSION = "4.24.1";
const calls = [];
export async function parse(bytes, options = {}) {
  const kind = Buffer.from(bytes).toString("utf8");
  calls.push(Object.prototype.hasOwnProperty.call(options, "ocr") ? options.ocr : "unset");
  const ocrOn = options.ocr === true;
  const done = (extra) => ({
    success: true,
    fileType: "pdf",
    blocks: [],
    ...extra,
    markdown: JSON.stringify({ calls, text: extra.markdown }),
  });
  if (kind === "text") {
    return done({ markdown: ocrOn ? "text+picture-ocr" : "text", qualitySummary: { needsOcr: false, ocrCandidatePages: [] } });
  }
  if (kind === "low-text-page") {
    // Kordoc flags a low-text page without a large picture but does not OCR it
    // and emits no NEEDS_OCR warning.
    return done({ markdown: "low", qualitySummary: { needsOcr: false, ocrCandidatePages: [7] } });
  }
  if (kind === "scan") {
    if (ocrOn) return done({ markdown: "ocr-text", warnings: [{ code: "OCR_APPLIED", message: "1" }] });
    return done({ markdown: "", qualitySummary: { needsOcr: true, ocrCandidatePages: [1] }, warnings: [{ code: "NEEDS_OCR", page: 1, message: "scan" }] });
  }
  if (kind === "fails-without-ocr") {
    if (ocrOn) return done({ markdown: "ocr-only" });
    return { success: false, error: "no text layer", code: "EMPTY" };
  }
  if (kind === "throws-without-ocr") {
    if (ocrOn) return done({ markdown: "ocr-after-throw" });
    throw new Error("text layer parse failed");
  }
  if (kind === "scan-fonts") {
    // pdf.js numbers each loaded document per process: the OCR pass is d1.
    const fontName = `g_d${calls.length - 1}_f1`;
    const blocks = [{ type: "paragraph", text: "t", style: { fontName } }];
    if (ocrOn) return done({ markdown: "ocr-text", blocks });
    return done({ markdown: "", blocks, warnings: [{ code: "NEEDS_OCR", page: 1, message: "scan" }] });
  }
  throw new Error(`unknown kind ${kind}`);
}
"""


@pytest.fixture()
def run_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    node = shutil.which("node") or shutil.which("node.exe")
    assert node, "Node.js is required to run the Kordoc runner"
    script = (
        Path(kordoc_parser.__file__).resolve().parents[2] / "scripts" / "kordoc_parse.mjs"
    )
    fake_kordoc = tmp_path / "fake_kordoc.mjs"
    fake_kordoc.write_text(_FAKE_KORDOC_MODULE, encoding="utf-8")
    hooks = tmp_path / "fake_kordoc_hooks.mjs"
    hooks.write_text(
        "export async function resolve(specifier, context, next) {\n"
        "  if (specifier === 'kordoc') {\n"
        f"    return {{ url: {json.dumps(fake_kordoc.as_uri())}, shortCircuit: true }};\n"
        "  }\n"
        "  return next(specifier, context);\n"
        "}\n",
        encoding="utf-8",
    )
    register = tmp_path / "register_fake_kordoc.mjs"
    register.write_text(
        "import { register } from 'node:module';\n"
        f"register({json.dumps(hooks.as_uri())});\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NODE_OPTIONS", f"--import={register.as_uri()}")

    def run(kind: str, *, ocr: bool) -> dict:
        request = {
            "filename": "job.pdf",
            "dataBase64": base64.b64encode(kind.encode("utf-8")).decode("ascii"),
            "ocr": ocr,
        }
        completed = subprocess.run(
            [node, str(script)],
            input=json.dumps(request).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(script.parents[1]),
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        result = json.loads(completed.stdout)
        return {"result": result, **json.loads(result["markdown"])}

    return run


def test_ocr_off_request_disables_kordoc_auto_ocr_explicitly(run_runner) -> None:
    out = run_runner("text", ocr=False)
    # "unset" would let Kordoc 4.24.1 OCR whenever the model is cached locally.
    assert out["calls"] == [False]
    assert out["text"] == "text"


def test_ocr_request_keeps_text_layer_result_when_no_page_needs_ocr(run_runner) -> None:
    out = run_runner("text", ocr=True)
    assert out["calls"] == [False]
    assert out["text"] == "text"


def test_low_text_page_without_needs_ocr_warning_is_not_reparsed(run_runner) -> None:
    out = run_runner("low-text-page", ocr=True)
    assert out["calls"] == [False]
    assert out["text"] == "low"


def test_ocr_request_reparses_with_ocr_when_kordoc_reports_needs_ocr(run_runner) -> None:
    out = run_runner("scan", ocr=True)
    assert out["calls"] == [False, True]
    assert out["text"] == "ocr-text"
    assert [w["code"] for w in out["result"]["warnings"]] == ["OCR_APPLIED"]


def test_scan_without_ocr_request_is_not_reparsed(run_runner) -> None:
    out = run_runner("scan", ocr=False)
    assert out["calls"] == [False]
    assert [w["code"] for w in out["result"]["warnings"]] == ["NEEDS_OCR"]


def test_failed_or_throwing_text_layer_parse_falls_back_to_ocr(run_runner) -> None:
    failed = run_runner("fails-without-ocr", ocr=True)
    assert failed["calls"] == [False, True]
    assert failed["text"] == "ocr-only"
    assert failed["result"]["success"] is True

    thrown = run_runner("throws-without-ocr", ocr=True)
    assert thrown["calls"] == [False, True]
    assert thrown["text"] == "ocr-after-throw"


def test_one_shot_ocr_pass_font_ids_match_a_fresh_single_parse(run_runner) -> None:
    out = run_runner("scan-fonts", ocr=True)
    assert out["calls"] == [False, True]
    assert [block["style"]["fontName"] for block in out["result"]["blocks"]] == ["g_d0_f1"]
