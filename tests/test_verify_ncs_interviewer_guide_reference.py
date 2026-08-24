from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from app.services.kordoc_parser import KordocParseError
from scripts import verify_ncs_interviewer_guide_reference as verify


def _sample_parse() -> dict:
    markdown = (
        "# 면접 기본\n"
        "실제 문장 한 줄입니다.\n"
        "## 작성 기법\n"
        "세부 설명입니다.\n"
    )
    return {
        "markdown": markdown,
        "outline": [
            {
                "text": "면접 기본",
                "pageNumber": 1,
                "children": [{"text": "작성 기법", "pageNumber": 2}],
            },
            {"text": "평가 기준", "pageNumber": 3},
        ],
        "blocks": [{} for _ in range(196)],
        "warnings": ["warn-1", "warn-2"],
        "qualitySummary": {
            "totalPages": 34,
            "totalTextChars": 74157,
            "needsOcr": False,
            "lowTextPageCount": 0,
            "highPuaPageCount": 0,
        },
        "pageQuality": [{}, {}],
    }


def _workspace_temp_dir() -> Path:
    temp_root = Path(__file__).resolve().parents[1] / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix="verify-ncs-guide-",
            dir=str(temp_root),
        )
    )


def test_main_emits_bounded_json_summary_without_document_sentences(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tmp_path = _workspace_temp_dir()
    pdf_path = tmp_path / "guide.pdf"
    file_bytes = b"%PDF-1.7\nsample guide bytes\n"
    sample_parse = _sample_parse()
    pdf_path.write_bytes(file_bytes)
    monkeypatch.setattr(
        verify,
        "parse_with_kordoc",
        lambda data, filename, ocr: sample_parse,
    )

    exit_code = verify.main([str(pdf_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["pdf"]["filename"] == "guide.pdf"
    assert payload["pdf"]["sha256"] == hashlib.sha256(file_bytes).hexdigest().upper()
    assert payload["kordoc"]["page_count"] == 34
    assert payload["kordoc"]["markdown_chars"] == len(sample_parse["markdown"])
    assert payload["kordoc"]["block_count"] == 196
    assert payload["kordoc"]["outline_count"] == 3
    assert payload["kordoc"]["heading_count"] == 3
    assert payload["kordoc"]["warning_count"] == 2
    assert payload["validation"]["passed"] is True
    assert payload["validation"]["checks"] == {
        "sha256_present": True,
        "page_count_present": True,
        "outline_present": True,
        "heading_present": True,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "실제 문장 한 줄입니다." not in serialized
    assert "면접 기본" not in serialized
    assert "작성 기법" not in serialized


def test_main_reports_expected_metadata_subset_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tmp_path = _workspace_temp_dir()
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nsample guide bytes\n")
    expected_path = tmp_path / "expected.json"
    expected_path.write_text(
        json.dumps({"kordoc": {"page_count": 99}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verify,
        "parse_with_kordoc",
        lambda data, filename, ocr: _sample_parse(),
    )

    exit_code = verify.main(
        [str(pdf_path), "--expected-metadata-json", str(expected_path)]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "expected_metadata_mismatch"
    assert payload["expected_metadata"]["matched"] is False
    assert payload["expected_metadata"]["mismatch_count"] == 1
    assert payload["expected_metadata"]["mismatches"][0]["path"] == "kordoc.page_count"
    assert payload["expected_metadata"]["mismatches"][0]["expected"] == 99
    assert payload["expected_metadata"]["mismatches"][0]["actual"] == 34


def test_main_maps_app_resource_schema_to_runtime_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tmp_path = _workspace_temp_dir()
    pdf_path = tmp_path / "guide.pdf"
    pdf_bytes = b"%PDF-1.7\nsample guide bytes\n"
    sample_parse = _sample_parse()
    pdf_path.write_bytes(pdf_bytes)
    expected_path = tmp_path / "expected.json"
    expected_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": {
                    "sha256": hashlib.sha256(pdf_bytes).hexdigest().upper(),
                    "page_count": 34,
                    "parse_summary": {
                        "markdown_chars": len(sample_parse["markdown"]),
                        "block_count": 196,
                        "outline_count": 3,
                        "warning_count": 2,
                        "needs_ocr": False,
                    },
                },
                "usage": {"mode": "authoring_advice_only"},
                "methods": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verify,
        "parse_with_kordoc",
        lambda data, filename, ocr: sample_parse,
    )

    exit_code = verify.main(
        [str(pdf_path), "--expected-metadata-json", str(expected_path)]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["expected_metadata"]["matched"] is True
    assert payload["expected_metadata"]["mismatch_count"] == 0
    assert payload["expected_metadata"]["mismatches"] == []


def test_main_returns_error_json_for_kordoc_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tmp_path = _workspace_temp_dir()
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nsample guide bytes\n")

    def fail_parse(data: bytes, filename: str, ocr: bool) -> dict:
        raise KordocParseError("node unavailable")

    monkeypatch.setattr(verify, "parse_with_kordoc", fail_parse)

    exit_code = verify.main([str(pdf_path)])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "error",
        "error": {
            "code": "KordocParseError",
            "message": "node unavailable",
        },
    }
