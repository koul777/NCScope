from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify_stored_jd_local_vercel_parity.py"
)
SPEC = importlib.util.spec_from_file_location(
    "verify_stored_jd_local_vercel_parity", SCRIPT_PATH
)
assert SPEC and SPEC.loader
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)


KEY = b"test-only-parity-key-that-is-longer-than-32-bytes"


def _payload(*, markdown: str = "public parser output", detail: str = "Official Detail") -> dict:
    return {
        "filename": "must-not-affect-contract.pdf",
        "parser": "kordoc",
        "parser_version": "4.9.1",
        "review_required": True,
        "review_session_id": "volatile-id",
        "review_session": {"id": "volatile-id", "signature": "secret"},
        "document": {
            "metadata": {"pageCount": 1},
            "outline": [],
            "warnings": [],
            "qualitySummary": {"totalPages": 1},
            "pageQuality": [],
            "markdown": markdown,
        },
        "sections": {"duties": [{"text": "hashed duty"}]},
        "fields": {
            "duties": ["hashed duty"],
            "ncs_detail_candidates": [detail],
        },
    }


class StubClient:
    def __init__(
        self,
        label: str,
        *,
        payload: dict | None = None,
        ncs_code: str = "0101010101_24v1",
    ) -> None:
        self.label = label
        self.payload = payload or _payload()
        self.ncs_code = ncs_code
        self.upload_names: list[str] = []
        self.queries: list[str] = []

    def parse_document(self, data: bytes, upload_filename: str):
        assert data
        self.upload_names.append(upload_filename)
        result = json.loads(json.dumps(self.payload))
        result["filename"] = f"{self.label}-volatile.pdf"
        result["review_session_id"] = f"{self.label}-session"
        result["review_session"] = {"id": f"{self.label}-session"}
        return parity.JsonResult("ok", 200, result)

    def ncs_units(self, public_detail_name: str, limit: int):
        self.queries.append(public_detail_name)
        assert limit == 200
        return parity.JsonResult(
            "ok",
            200,
            {
                "count": 1,
                "source": "ncs-mcp",
                "items": [
                    {
                        "ncsClCd": self.ncs_code,
                        "compeUnitName": "Public Unit",
                        "ncsSubdCdnm": public_detail_name,
                    }
                ],
            },
        )


def test_canonical_hmac_contract_is_stable_and_domain_separated() -> None:
    digester = parity.ContractDigester(KEY)
    left = {"b": "e\u0301\r\n", "a": [1, True]}
    right = {"a": [1, True], "b": "\u00e9\n"}

    assert digester.json("domain-a", left) == digester.json("domain-a", right)
    assert digester.json("domain-a", left) != digester.json("domain-b", right)
    with pytest.raises(parity.ConfigurationError):
        parity.ContractDigester(b"short")


def test_endpoint_contract_ignores_only_transport_session_and_filename() -> None:
    digester = parity.ContractDigester(KEY)
    left = _payload()
    right = _payload()
    right["filename"] = "another.pdf"
    right["review_session_id"] = "another-session"
    right["review_session"] = {"id": "another-session", "signature": "another"}

    left_contract = parity.endpoint_contract(left, digester)
    right_contract = parity.endpoint_contract(right, digester)

    assert left_contract.parser_digest == right_contract.parser_digest
    assert left_contract.structure_digest == right_contract.structure_digest

    right["document"]["markdown"] = "changed parser output"
    changed = parity.endpoint_contract(right, digester)
    assert left_contract.parser_digest != changed.parser_digest
    assert left_contract.structure_digest == changed.structure_digest


def test_only_catalog_owned_public_names_can_be_sent_as_ncs_queries() -> None:
    index = {parity.normalized_label_key("Official Detail"): ("Official Detail",)}

    assert parity.public_ncs_queries(
        [" Official-Detail ", "private institution label"], index
    ) == ("Official Detail",)


def test_remote_upload_requires_https_and_explicit_acknowledgement() -> None:
    with pytest.raises(parity.ConfigurationError):
        parity.validate_remote_upload("https://ncscope.vercel.app", False)
    with pytest.raises(parity.ConfigurationError):
        parity.validate_remote_upload("http://example.test", True)

    parity.validate_remote_upload("https://ncscope.vercel.app", True)
    parity.validate_remote_upload("http://127.0.0.1:8001", False)


def test_endpoint_client_retries_rate_limit_using_bounded_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "999"})
        return httpx.Response(200, json={"status": "ok"})

    sleeps: list[float] = []
    client = parity.EndpointClient("test", "https://example.test", 10)
    client._client.close()
    client._client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    monkeypatch.setattr(parity.time, "sleep", sleeps.append)
    try:
        result = client._request("GET", "/health")
    finally:
        client.close()

    assert result.ok
    assert attempts == 2
    assert sleeps == [parity.MAX_RETRY_AFTER_SECONDS]


def test_full_corpus_contract_passes_and_reports_no_source_text(tmp_path: Path) -> None:
    source_name = "private-employer-secret-jd.pdf"
    source_text = b"private document bytes"
    first = tmp_path / source_name
    second = tmp_path / "duplicate-private-name.pdf"
    first.write_bytes(source_text)
    second.write_bytes(source_text)
    local = StubClient("local")
    remote = StubClient("remote")
    detail_index = {
        parity.normalized_label_key("Official Detail"): ("Official Detail",)
    }

    result = parity.verify_corpus(
        [first, second],
        local_client=local,
        remote_client=remote,
        digester=parity.ContractDigester(KEY),
        public_detail_index=detail_index,
        expected_files=2,
        expected_unique_contents=1,
        max_file_bytes=1024,
        ncs_limit=200,
    )

    assert result["summary"]["passed"] is True
    assert result["summary"]["matched_cases"] == 2
    assert len({case["case_id"] for case in result["cases"]}) == 2
    assert all(case["overall_match"] for case in result["cases"])
    assert local.queries == ["Official Detail"]
    assert remote.queries == ["Official Detail"]
    assert all(name.startswith("case-") for name in local.upload_names + remote.upload_names)

    report_dir = tmp_path / "reports"
    json_path, csv_path = parity.write_reports(result, report_dir)
    rendered = json_path.read_text(encoding="utf-8") + csv_path.read_text(
        encoding="utf-8-sig"
    )
    assert source_name not in rendered
    assert "duplicate-private-name.pdf" not in rendered
    assert source_text.decode() not in rendered
    assert "Official Detail" not in rendered
    assert "hashed duty" not in rendered
    assert "secret" not in rendered


def test_parser_and_ncs_mismatches_are_typed_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "jd.pdf"
    path.write_bytes(b"document")
    local = StubClient("local")
    remote = StubClient(
        "remote",
        payload=_payload(markdown="remote parser drift"),
        ncs_code="different-code",
    )
    detail_index = {
        parity.normalized_label_key("Official Detail"): ("Official Detail",)
    }

    result = parity.verify_corpus(
        [path],
        local_client=local,
        remote_client=remote,
        digester=parity.ContractDigester(KEY),
        public_detail_index=detail_index,
        expected_files=2,
        expected_unique_contents=1,
        max_file_bytes=1024,
        ncs_limit=200,
    )

    assert result["summary"]["passed"] is False
    assert result["summary"]["corpus_failures"] == ["unexpected_corpus_size"]
    assert set(result["cases"][0]["mismatch_types"]) == {
        "parser_document_mismatch",
        "ncs_result_mismatch",
    }
    assert result["summary"]["mismatch_counts"] == {
        "ncs_result_mismatch": 1,
        "parser_document_mismatch": 1,
    }


def test_cli_fails_closed_before_any_remote_upload_without_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(parity.DEFAULT_DIGEST_KEY_ENV, KEY.decode())

    exit_code = parity.main(
        [
            "--input-dir",
            str(tmp_path),
            "--remote-base-url",
            "https://ncscope.vercel.app",
        ]
    )

    assert exit_code == parity.EXIT_CONFIGURATION
