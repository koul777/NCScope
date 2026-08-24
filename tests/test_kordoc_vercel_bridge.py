from __future__ import annotations

import json
import io
import zipfile

import pytest

from app.services import kordoc_parser


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> dict:
        return self._payload


def test_vercel_bridge_parses_binary_with_required_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            calls.append({"client": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url: str, *, content: bytes, headers: dict[str, str]):
            calls.append({"url": url, "content": content, "headers": headers})
            return _FakeResponse(
                {
                    "success": True,
                    "parser": "kordoc",
                    "parser_version": "4.9.1",
                    "markdown": "세분류: 프로젝트관리",
                    "blocks": [],
                    "metadata": {},
                }
            )

    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setattr(kordoc_parser.httpx, "Client", FakeClient)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "test-shared-secret")
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)

    result = kordoc_parser.parse_with_kordoc(b"%PDF-sanitized", filename="직무기술서.pdf")

    request = calls[-1]
    assert request["url"] == "https://ncscope-preview.vercel.app/api/kordoc-parse"
    assert request["content"] == b"%PDF-sanitized"
    assert request["headers"]["content-type"] == "application/octet-stream"
    assert request["headers"]["x-ncscope-kordoc-secret"] == "test-shared-secret"
    assert result["parser"] == "kordoc"
    assert result["parser_version"] == "4.9.1"


def test_vercel_bridge_refuses_to_run_without_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_SECRET", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable") as caught:
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")

    assert "secret" not in str(caught.value).casefold()
    assert "vercel" not in str(caught.value).casefold()


def test_external_insecure_bridge_url_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "http://example.com/api/kordoc-parse")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "test-shared-secret")
    monkeypatch.delenv("VERCEL_URL", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable"):
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")


@pytest.mark.parametrize(
    ("parsed", "expected"),
    [
        ({"markdown": "세분류: 프로젝트관리", "parser": "plain_text"}, "plain_text"),
        (
            {
                "markdown": "세분류: 프로젝트관리",
                "parser": "pdf_text_fallback",
                "metadata": {"fallback": "pdf-text"},
            },
            "pdf_text_fallback",
        ),
        (
            {
                "markdown": "세분류: 프로젝트관리",
                "parser": "kordoc",
                "parser_version": "4.9.1",
            },
            "kordoc",
        ),
    ],
)
def test_structured_review_reports_truthful_parser_provenance(parsed: dict, expected: str) -> None:
    result = kordoc_parser.structure_job_description(parsed, filename="jd.pdf")

    assert result["parser"] == expected
    if expected == "kordoc":
        assert result["parser_version"] == "4.9.1"
    else:
        assert "parser_version" not in result


def test_unknown_parser_metadata_cannot_be_reflected_to_the_ui() -> None:
    result = kordoc_parser.structure_job_notice(
        {
            "markdown": "담당업무: 행정 지원",
            "parser": "https://attacker.invalid/?secret=value",
            "parser_version": "secret-value",
        },
        filename="notice.pdf",
    )

    assert result["parser"] == "unknown"
    assert "parser_version" not in result


def test_zip_review_preserves_each_member_parser_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    def fake_parse(data: bytes, filename: str, _label: str) -> dict:
        if filename.endswith(".pdf"):
            return {
                "markdown": "세분류: 프로젝트관리",
                "parser": "kordoc",
                "parser_version": "4.9.1",
                "metadata": {},
            }
        return {
            "markdown": data.decode("utf-8"),
            "parser": "plain_text",
            "metadata": {},
        }

    monkeypatch.setattr(main, "_parse_single_document_upload", fake_parse)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("jd.pdf", b"pdf")
        archive.writestr("notes.txt", "담당업무: 행정 지원".encode("utf-8"))

    parsed = main._parse_upload_document(archive_bytes.getvalue(), "bundle.zip", "jd_file")

    assert parsed["parser"] == "mixed_document_parsers"
    assert "parser_version" not in parsed
    assert parsed["metadata"]["members"] == [
        {
            "filename": "jd.pdf",
            "suffix": ".pdf",
            "parser": "kordoc",
            "parser_version": "4.9.1",
        },
        {"filename": "notes.txt", "suffix": ".txt", "parser": "plain_text"},
    ]
