from __future__ import annotations

import base64
import hashlib
import json
import io
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.services import kordoc_parser


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        if payload.get("parser") == "kordoc" and "parser_execution" not in payload:
            payload = {
                **payload,
                "parser_execution": {
                    "schema_version": "ncscope_parser_execution_v1",
                    "role": "selected",
                    "parser": "kordoc",
                    "mode": "authenticated_serverless_bridge",
                    "parser_version": payload.get("parser_version"),
                    "node_version": "24.0.0",
                    "build_identity": {
                        "kind": "vercel_deployment",
                        "deployment_url": "ncscope-preview.vercel.app",
                    },
                },
            }
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
    # PowerShell can prepend a UTF-8 BOM when a value is piped to the Vercel CLI.
    # It must never leak into the HTTP header value.
    shared_secret = "test-shared-secret-32-bytes-minimum"
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", f"\ufeff{shared_secret}")
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)

    result = kordoc_parser.parse_with_kordoc(b"%PDF-sanitized", filename="직무기술서.pdf")

    request = calls[-1]
    assert request["url"] == "https://ncscope-preview.vercel.app/api/kordoc-parse"
    assert request["content"] == b"%PDF-sanitized"
    assert request["headers"]["content-type"] == "application/octet-stream"
    assert request["headers"]["x-ncscope-kordoc-secret"] == shared_secret
    assert result["parser"] == "kordoc"
    assert result["parser_version"] == "4.9.1"


def test_vercel_bridge_signs_each_request_with_ed25519(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

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
                    "markdown": "document",
                    "blocks": [],
                    "metadata": {},
                }
            )

    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setattr(kordoc_parser.httpx, "Client", FakeClient)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    private_key_bytes = bytes(range(32))
    public_key_bytes = Ed25519PrivateKey.from_private_bytes(
        private_key_bytes
    ).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(
        kordoc_parser,
        "_KORDOC_BRIDGE_ED25519_PUBLIC_KEY_RAW",
        base64.urlsafe_b64encode(public_key_bytes).decode("ascii").rstrip("="),
    )
    encoded_private_key = base64.urlsafe_b64encode(private_key_bytes).decode("ascii").rstrip("=")
    monkeypatch.setenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", encoded_private_key)
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "different-dedicated-key")

    kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")

    headers = calls[-1]["headers"]
    assert "x-ncscope-kordoc-secret" not in headers
    assert headers["x-ncscope-kordoc-body-sha256"] == hashlib.sha256(
        b"document"
    ).hexdigest()
    message = "\n".join(
        (
            headers["x-ncscope-kordoc-timestamp"],
            hashlib.sha256(b"document").hexdigest(),
            headers["x-ncscope-filename-b64"],
            "0",
        )
    ).encode("ascii")
    signature_text = headers["x-ncscope-kordoc-signature"]
    signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
    Ed25519PrivateKey.from_private_bytes(private_key_bytes).public_key().verify(signature, message)


def test_vercel_bridge_refuses_to_run_without_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_SECRET", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable") as caught:
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")

    assert "secret" not in str(caught.value).casefold()
    assert "vercel" not in str(caught.value).casefold()


def test_local_only_parse_never_calls_remote_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_calls: list[bytes] = []

    def local_unavailable(*_args, **_kwargs):
        raise kordoc_parser._LocalKordocUnavailable("missing local runtime")

    def remote_parse(data: bytes, **_kwargs):
        remote_calls.append(data)
        return {"success": True}

    monkeypatch.setattr(kordoc_parser, "_parse_with_local_kordoc", local_unavailable)
    monkeypatch.setattr(kordoc_parser, "_parse_with_remote_kordoc", remote_parse)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")

    with pytest.raises(
        kordoc_parser.KordocParseError,
        match="remote parsing is disabled",
    ):
        kordoc_parser.parse_with_kordoc(
            b"PRIVATE-DOCUMENT-BYTES",
            filename="private.pdf",
            allow_remote=False,
        )

    assert remote_calls == []


def test_vercel_bridge_refuses_non_ascii_header_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "not-ascii-비밀")
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable"):
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")


def test_vercel_bridge_refuses_weak_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "too-short")
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable"):
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")


def test_vercel_bridge_refuses_control_character_shared_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("VERCEL_URL", "ncscope-preview.vercel.app")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "A" * 16 + "\x7f" + "B" * 16)
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KORDOC_BRIDGE_URL", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable"):
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")


def test_external_insecure_bridge_url_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv("KORDOC_BRIDGE_URL", "http://example.com/api/kordoc-parse")
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "test-shared-secret")
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VERCEL_URL", raising=False)

    with pytest.raises(kordoc_parser.KordocParseError, match="runtime is unavailable"):
        kordoc_parser.parse_with_kordoc(b"document", filename="jd.pdf")


def test_external_non_vercel_bridge_host_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kordoc_parser.shutil, "which", lambda _name: None)
    monkeypatch.setenv(
        "KORDOC_BRIDGE_URL",
        "https://example.com/api/kordoc-parse",
    )
    monkeypatch.setenv("KORDOC_BRIDGE_SECRET", "A" * 32)
    monkeypatch.delenv("KORDOC_BRIDGE_ED25519_PRIVATE_KEY", raising=False)
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

    def fake_parse(
        data: bytes,
        filename: str,
        _label: str,
        **_kwargs,
    ) -> dict:
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
    assert [row["parser"] for row in parsed["metadata"]["members"]] == [
        "kordoc",
        "plain_text",
    ]
    assert parsed["metadata"]["members"][1]["parser_execution"]["mode"] == (
        "builtin_plain_text"
    )
    assert parsed["parser_executions"][0]["mode"] == "builtin_plain_text"


def test_parser_execution_is_bound_to_static_runtime_bundle() -> None:
    from app import main

    structured = {
        "parser_executions": [
            {
                "schema_version": "ncscope_parser_execution_v1",
                "role": "selected",
                "parser": "kordoc",
                "mode": "local_node_subprocess",
                "parser_version": "4.9.1",
                "node_version": "24.0.0",
                "build_identity": {"kind": "local_source_bundle"},
            }
        ]
    }

    main._bind_parser_executions_to_runtime(structured)

    assert structured["parser_executions"][0]["runtime_bundle_sha256"] == (
        main._evaluation_runtime_attestation()["runtime_bundle_sha256"]
    )

    structured["parser_executions"][0]["parser_version"] = "0.0.0"
    with pytest.raises(main.HTTPException) as caught:
        main._bind_parser_executions_to_runtime(structured)
    assert getattr(caught.value, "status_code", None) == 503

    wrong_kind = {
        "parser_executions": [
            {
                "schema_version": "ncscope_parser_execution_v1",
                "role": "selected",
                "parser": "kordoc",
                "mode": "local_node_subprocess",
                "parser_version": "4.9.1",
                "node_version": "24.0.0",
                "build_identity": {"kind": "not_the_execution_source"},
            }
        ]
    }
    with pytest.raises(main.HTTPException) as caught:
        main._bind_parser_executions_to_runtime(wrong_kind)
    assert getattr(caught.value, "status_code", None) == 503


def test_remote_execution_must_match_current_vercel_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    monkeypatch.setenv("VERCEL_URL", "current-preview.vercel.app")
    structured = {
        "parser_executions": [
            {
                "schema_version": "ncscope_parser_execution_v1",
                "role": "selected",
                "parser": "kordoc",
                "mode": "authenticated_serverless_bridge",
                "parser_version": "4.9.1",
                "node_version": "24.0.0",
                "build_identity": {
                    "kind": "vercel_deployment",
                    "deployment_url": "stale-preview.vercel.app",
                },
            }
        ]
    }

    with pytest.raises(main.HTTPException) as caught:
        main._bind_parser_executions_to_runtime(structured)
    assert getattr(caught.value, "status_code", None) == 503


def test_kordoc_result_rejects_missing_or_stale_execution_identity() -> None:
    base = {
        "success": True,
        "parser": "kordoc",
        "parser_version": "4.9.1",
        "parser_execution": {
            "schema_version": "ncscope_parser_execution_v1",
            "role": "selected",
            "parser": "kordoc",
            "mode": "local_node_subprocess",
            "parser_version": "4.9.1",
            "node_version": "24.0.0",
            "build_identity": {"kind": "local_source_bundle"},
        },
    }
    accepted = kordoc_parser._stamp_kordoc_result(
        base,
        expected_mode="local_node_subprocess",
    )
    assert accepted["parser_execution"]["node_version"] == "24.0.0"

    stale = json.loads(json.dumps(base))
    stale["parser_version"] = "4.8.0"
    with pytest.raises(kordoc_parser.KordocParseError, match="execution provenance"):
        kordoc_parser._stamp_kordoc_result(
            stale,
            expected_mode="local_node_subprocess",
        )

    missing = dict(base)
    missing.pop("parser_execution")
    with pytest.raises(kordoc_parser.KordocParseError, match="execution provenance"):
        kordoc_parser._stamp_kordoc_result(
            missing,
            expected_mode="local_node_subprocess",
        )
