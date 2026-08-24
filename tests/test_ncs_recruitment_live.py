from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import ssl
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_ncs_recruitment_live.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_ncs_recruitment_live_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_postings_and_jd_attachments_filters_notice_rows():
    mod = load_module()
    list_html = """
    <a onclick="fn_view('1001')" title="SECRET POSTING 1"></a>
    <a onclick="fn_view('1002')" title="SECRET POSTING 2"></a>
    <a onclick="fn_view('1002')" title="SECRET POSTING 2 duplicate"></a>
    """
    postings = mod.parse_postings_from_list_html(list_html)
    assert [posting.recrt_no for posting in postings] == ["1001", "1002"]

    detail_html = """
    <a href="#" onclick="gfn_file_downloadFile('A','B','1')">직무기술서.pdf</a>
    <a href="#" onclick="gfn_file_downloadFile('A','B','1')">직무기술서.pdf</a>
    <a href="#" onclick="gfn_file_downloadFile('A','B','2')">공고문_직무기술서.pdf</a>
    <a href="#" onclick="gfn_file_downloadFile('A','B','3')">안내문.pdf</a>
    """
    attachments = mod.parse_jd_attachments(detail_html, postings[0])
    assert [(item.sys_dstin_cd, item.file_mstky, item.filedetl_seq) for item in attachments] == [("A", "B", "1")]


def test_parse_posting_source_union_combines_four_stage_codes():
    mod = load_module()
    payload = {
        "jdsptList": [
            {
                "ncsLclasCd": "01",
                "ncsMclasCd": "02",
                "ncsSclasCd": "03",
                "ncsSubdCd": "04",
                "ncsSubdCdnm": "경영기획",
            },
            {
                "ncsLclasCd": "01",
                "ncsMclasCd": "02",
                "ncsSclasCd": "03",
                "ncsSubdCd": "04",
                "ncsSubdCdNm": "경영기획",
            },
        ]
    }
    names, codes = mod.parse_posting_source_union(payload)
    assert names == {"경영기획"}
    assert codes == {"01020304"}

    malformed = {"jdsptList": [{"ncsLclasCd": "01", "ncsSubdCdnm": "ignored"}]}
    _, malformed_codes = mod.parse_posting_source_union(malformed)
    assert malformed_codes == set()


def test_validate_parse_endpoint_requires_https_and_explicit_flag_for_non_loopback():
    mod = load_module()
    assert mod.validate_parse_endpoint("http://127.0.0.1:8000", False) == "http://127.0.0.1:8000"

    try:
        mod.validate_parse_endpoint("http://parser.example.com", True)
    except mod.ConfigurationError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("expected ConfigurationError for non-HTTPS remote endpoint")

    try:
        mod.validate_parse_endpoint("https://parser.example.com", False)
    except mod.ConfigurationError as exc:
        assert "allow-remote-parse-upload" in str(exc)
    else:
        raise AssertionError("expected ConfigurationError without explicit remote-upload flag")

    assert mod.validate_parse_endpoint("https://parser.example.com", True) == "https://parser.example.com"


def test_source_client_rejects_cross_origin_redirect():
    mod = load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/redirect"})

    client = mod.SourceClient(transport=httpx.MockTransport(handler), sleep_func=lambda _seconds: None)
    try:
        try:
            client.fetch_detail_html(mod.Posting(recrt_no="1001", title="ignored"))
        except (mod.SourceFetchError, mod.ConfigurationError) as exc:
            assert str(exc) in {"source URL host is not allowlisted", "cross_origin_redirect_rejected"}
        else:
            raise AssertionError("expected redirect rejection")
    finally:
        client.close()


def test_aggregate_metrics_keeps_accuracy_and_attachment_diagnostic_separate():
    mod = load_module()
    cases = [
        {
            "case_id": "case-1",
            "posting_id": "posting-1",
            "source_detail_name_ids": ["s1", "s2"],
            "source_detail_code_ids": ["c1", "c2"],
            "observed_detail_name_ids": ["s1"],
            "observed_detail_code_ids": ["c1"],
            "positioned_coordinate_contract_total": 1,
            "positioned_coordinate_contract_valid_count": 1,
            "positioned_table_label_count": 1,
            "positioned_table_label_to_final_exact_match_count": 1,
            "final_exact_ability_count": 2,
            "final_exact_table_provenance_count": 1,
            "posting_query_ids": ["q1", "q2"],
            "posting_ksa_probe_unit_count": 2,
            "posting_ksa_available_unit_count": 1,
        },
        {
            "case_id": "case-2",
            "posting_id": "posting-1",
            "source_detail_name_ids": ["s1", "s2"],
            "source_detail_code_ids": ["c1", "c2"],
            "observed_detail_name_ids": ["s2"],
            "observed_detail_code_ids": ["c2"],
            "positioned_coordinate_contract_total": 0,
            "positioned_coordinate_contract_valid_count": 0,
            "positioned_table_label_count": 0,
            "positioned_table_label_to_final_exact_match_count": 0,
            "final_exact_ability_count": 0,
            "final_exact_table_provenance_count": 0,
            "posting_query_ids": ["q1", "q2"],
            "posting_ksa_probe_unit_count": 2,
            "posting_ksa_available_unit_count": 1,
        },
    ]

    summary, postings, _ = mod.aggregate_metrics(cases)
    assert summary["posting_accuracy_evidence"]["exact_pct"] == 100.0
    assert summary["attachment_union_non_accuracy_diagnostic"]["exact_pct"] == 0.0
    assert summary["coordinate_metrics"]["positioned_coordinate_shape_completeness_pct"] == 100.0
    assert summary["coordinate_metrics"]["positioned_table_label_to_final_exact_match_pct"] == 100.0
    assert summary["coordinate_metrics"]["final_unique_exact_ability_table_provenance_pct"] == 50.0
    assert summary["ksa_metrics"]["availability_pct"] == 50.0
    assert summary["posting_mismatch_diagnostics"] == {
        "counts": {"exact": 1},
        "posting_count": 1,
        "definition": "set_relation_diagnostic_not_causal_accuracy",
    }
    assert summary["document_mapping_state_diagnostics"] == {
        "status_counts": {"not_recorded": 2},
        "status_reason_counts": {"not_recorded": 2},
        "declared_no_mapping_document_count": 0,
        "document_count": 2,
        "definition": "document_mapping_state_diagnostic_not_accuracy",
    }
    assert postings[0]["exact_match"] is True
    assert postings[0]["mismatch_diagnostic"] == "exact"


def test_posting_mismatch_diagnostic_reports_set_relations_without_causal_claims():
    mod = load_module()
    classify = mod.posting_mismatch_diagnostic

    assert classify(
        source_names={"a"}, source_codes={"1"},
        observed_names={"a"}, observed_codes={"1"}, accuracy_eligible=True,
    ) == "exact"
    assert classify(
        source_names={"a"}, source_codes={"1"},
        observed_names=set(), observed_codes=set(), accuracy_eligible=True,
    ) == "no_observed_detail_review_required"
    assert classify(
        source_names={"a", "b"}, source_codes={"1", "2"},
        observed_names={"a"}, observed_codes={"1"}, accuracy_eligible=True,
    ) == "source_union_superset_possible"
    assert classify(
        source_names={"a"}, source_codes={"1"},
        observed_names={"a", "b"}, observed_codes={"1", "2"}, accuracy_eligible=True,
    ) == "document_extra_not_in_source_union"
    assert classify(
        source_names={"a"}, source_codes={"1"},
        observed_names={"b"}, observed_codes={"2"}, accuracy_eligible=True,
    ) == "cross_mismatch_review_required"
    assert classify(
        source_names={"a"}, source_codes={"1"},
        observed_names={"a"}, observed_codes={"2"}, accuracy_eligible=True,
    ) == "cross_mismatch_review_required"
    assert classify(
        source_names=set(), source_codes=set(),
        observed_names={"a"}, observed_codes={"1"}, accuracy_eligible=False,
    ) == "excluded_missing_source_ground_truth"


def test_run_benchmark_and_write_reports_keep_private_values_out_of_reports(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    private_source_dir = tmp_path / "tmp" / "gold_source"

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(mod, "use_ncs_mcp_request_session", fake_session)

    posting = mod.Posting(recrt_no="1001", title="SECRET POSTING")
    attachment = mod.AttachmentRef(
        posting=posting,
        sys_dstin_cd="SYS",
        file_mstky="MST",
        filedetl_seq="1",
        label="SECRET LABEL 직무기술서.pdf",
        ordinal=1,
    )

    class FakeSourceClient:
        def list_postings(self, *, max_postings: int, max_pages: int):
            assert max_postings == 1
            assert max_pages == 1
            return [posting]

        def fetch_detail_html(self, posting_arg):
            assert posting_arg == posting
            return "<html></html>"

        def fetch_posting_source_union(self, posting_arg):
            assert posting_arg == posting
            return {
                "jdsptList": [
                    {
                        "ncsLclasCd": "01",
                        "ncsMclasCd": "02",
                        "ncsSclasCd": "03",
                        "ncsSubdCd": "04",
                        "ncsSubdCdnm": "경영기획",
                    }
                ]
            }

        def download_attachment(self, attachment_arg, *, max_bytes: int, case_id: str):
            assert attachment_arg == attachment
            assert max_bytes == 4096
            assert case_id
            return mod.AttachmentDownload(
                upload_filename=mod.safe_upload_filename(case_id, ".pdf"),
                content_type="application/pdf",
                data=b"%PDF-1.7\nsecret-bytes",
            )

    class FakeParseClient:
        def parse_review(self, upload):
            assert upload.upload_filename.startswith("case-")
            return mod.JsonResult(
                "ok",
                200,
                {
                    "fields": {
                        "ncs_detail_mapping_states": [
                            {
                                "mappingState": "official_current_exact",
                                "officialDetailNames": ["경영기획"],
                                "officialDetailCodes": ["01020304"],
                            }
                        ],
                        "ability_unit_mapping_states": [
                            {
                                "mappingState": "official_exact_current",
                                "sourceName": "문제해결능력",
                                "resolvedUnitCodes": ["0102030401"],
                            },
                            {
                                "mappingState": "official_exact_current",
                                "sourceName": "수리능력",
                                "resolvedUnitCodes": ["0102030402"],
                            },
                        ],
                        "positioned_items": [
                            {
                                "section": "ability_units",
                                "text": "문제해결능력",
                                "page": 1,
                                "table_index": 0,
                                "label_cell": {
                                    "row": 1,
                                    "column": 0,
                                    "row_span": 1,
                                    "column_span": 1,
                                },
                                "value_cell": {
                                    "row": 1,
                                    "column": 1,
                                    "row_span": 1,
                                    "column_span": 2,
                                },
                            },
                        ],
                        "ncs_detail_absence_declared_no_mapping": False,
                        "ncs_detail_source": "pdf_table_detail_exact",
                    }
                },
            )

    def fake_search_units(detail_names, max_units=80):
        assert detail_names == ["경영기획"]
        assert max_units == 5
        return [{"ncsClCd": "0102030401"}, {"ncsClCd": "0102030402"}]

    def fake_get_ksa(units, max_factors_per_unit=12):
        assert max_factors_per_unit == 1
        return [{"ncsClCd": "0102030401"}]

    monkeypatch.setattr(mod, "parse_jd_attachments", lambda detail_html, posting_arg: [attachment])
    digester = mod.PrivacyDigester(b"x" * 32)
    payload = mod.run_benchmark(
        source_client=FakeSourceClient(),
        parse_client=FakeParseClient(),
        digester=digester,
        max_postings=1,
        max_pages=1,
        max_attachment_bytes=4096,
        delay_seconds=0,
        ncs_unit_limit=5,
        max_ksa_factors_per_unit=1,
        search_units_fn=fake_search_units,
        get_ksa_fn=fake_get_ksa,
        sleep_func=lambda _seconds: None,
        private_gold_source_dir=private_source_dir,
    )

    assert payload["summary"]["posting_count"] == 1
    assert payload["summary"]["document_count"] == 1
    assert payload["summary"]["sampling"] == {
        "strategy": "ordered_current_board_window",
        "skipped_posting_count": 0,
        "selected_posting_limit": 1,
        "raw_posting_identifiers_written": False,
    }
    assert payload["summary"]["posting_accuracy_evidence"]["exact_pct"] == 100.0
    assert payload["summary"]["document_mapping_state_diagnostics"]["status_reason_counts"] == {
        "official_current_exact": 1,
    }
    assert payload["summary"]["attachment_union_non_accuracy_diagnostic"]["exact_pct"] == 100.0
    assert payload["summary"]["coordinate_metrics"]["positioned_coordinate_shape_completeness_pct"] == 100.0
    assert payload["summary"]["coordinate_metrics"]["positioned_table_label_to_final_exact_match_pct"] == 100.0
    assert payload["summary"]["coordinate_metrics"]["final_unique_exact_ability_table_provenance_pct"] == 50.0
    assert payload["summary"]["ksa_metrics"]["probe_unit_count"] == 2
    assert payload["summary"]["ksa_metrics"]["available_unit_count"] == 1
    assert payload["summary"]["passed"] is False
    assert "ksa_availability_below_threshold" in payload["summary"]["failures"]
    assert payload["summary"]["quality_thresholds"]["ksa_availability_pct"] == 100.0
    assert payload["summary"]["privacy"]["private_gold_source_capture_enabled"] is True
    assert payload["cases"][0]["posting_query_ids"]
    assert len(payload["cases"][0]["case_id"]) == 64
    assert len(payload["cases"][0]["posting_id"]) == 64

    json_path, csv_path, md_path = mod.write_reports(payload, tmp_path)
    combined = json_path.read_text(encoding="utf-8") + csv_path.read_text(encoding="utf-8-sig") + md_path.read_text(encoding="utf-8")
    assert "SECRET POSTING" not in combined
    assert "SECRET LABEL" not in combined
    assert "경영기획" not in combined

    source_index = private_source_dir / "source_index.local.csv"
    source_rows = list(csv.DictReader(source_index.open(encoding="utf-8-sig")))
    assert len(source_rows) == 1
    assert source_rows[0]["case_id"] == payload["cases"][0]["case_id"]
    assert source_rows[0]["document_sha256"] == hashlib.sha256(
        b"%PDF-1.7\nsecret-bytes"
    ).hexdigest()
    captured_document = Path(source_rows[0]["local_document_path"])
    assert captured_document.read_bytes() == b"%PDF-1.7\nsecret-bytes"
    assert "SECRET POSTING" not in source_index.read_text(encoding="utf-8-sig")
    assert "SECRET LABEL" not in source_index.read_text(encoding="utf-8-sig")


def test_private_gold_source_capture_refuses_paths_outside_local_tmp(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    with pytest.raises(mod.ConfigurationError, match="below tmp/ or .tmp/"):
        mod.validate_private_gold_source_dir(tmp_path / "tracked-output")


def test_coordinate_contract_validates_shape_instead_of_label_matching():
    mod = load_module()
    fields = {
        "positioned_items": [
            {
                "section": "ability_units",
                "text": "unit-a",
                "page": 0,
                "table_index": 0,
                "label_cell": {
                    "row": 0,
                    "column": 0,
                    "row_span": 1,
                    "column_span": 1,
                },
                "value_cell": {
                    "row": 0,
                    "column": 1,
                    "row_span": 1,
                    "column_span": 2,
                },
            },
            {
                "section": "ability_units",
                "text": "unit-b",
                "page": 1,
                "table_index": 0,
                "label_cell": {
                    "row": 1,
                    "column": 0,
                    "row_span": 1,
                    "column_span": 1,
                },
                "value_cell": {
                    "row": 1,
                    "column": 1,
                    "row_span": 0,
                    "column_span": 1,
                },
            },
        ]
    }

    assert mod.positioned_ability_coordinate_counts(fields) == (2, 1)


def test_no_attachment_posting_is_retained_and_benchmark_fails_closed(monkeypatch):
    mod = load_module()

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(mod, "use_ncs_mcp_request_session", fake_session)
    monkeypatch.setattr(mod, "parse_jd_attachments", lambda _html, _posting: [])
    posting = mod.Posting(recrt_no="1001", title="SECRET")

    class FakeSourceClient:
        def list_postings(self, *, max_postings, max_pages):
            return [posting]

        def fetch_detail_html(self, posting_arg):
            return "<html></html>"

        def fetch_posting_source_union(self, posting_arg):
            return {
                "jdsptList": [
                    {
                        "ncsLclasCd": "01",
                        "ncsMclasCd": "02",
                        "ncsSclasCd": "03",
                        "ncsSubdCd": "04",
                        "ncsSubdCdnm": "secret-detail",
                    }
                ]
            }

    class FakeParseClient:
        def parse_review(self, _upload):
            raise AssertionError("no upload expected")

    payload = mod.run_benchmark(
        source_client=FakeSourceClient(),
        parse_client=FakeParseClient(),
        digester=mod.PrivacyDigester(b"z" * 32),
        max_postings=1,
        max_pages=1,
        max_attachment_bytes=4096,
        delay_seconds=0,
        search_units_fn=lambda *_args, **_kwargs: [],
        get_ksa_fn=lambda *_args, **_kwargs: [],
        sleep_func=lambda _seconds: None,
    )

    assert payload["summary"]["passed"] is False
    assert "posting_without_jd_attachment" in payload["summary"]["failures"]
    assert payload["summary"]["posting_accuracy_evidence"]["all_posting_count"] == 1
    assert payload["postings"][0]["document_count"] == 0


def test_error_reasons_are_stable_codes_and_zip_bombs_are_rejected():
    mod = load_module()
    assert mod.safe_exception_status_reason(
        mod.SourceFetchError("SECRET filename and URL")
    ) == "source_fetch_error"
    try:
        mod.safe_suffix_from_headers(
            b"PK\x03\x04payload",
            "application/zip",
            "secret.zip",
        )
    except mod.SourceFetchError as exc:
        assert str(exc) == "zip_attachment_not_allowed"
    else:
        raise AssertionError("expected ZIP attachment rejection")

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * (2 * 1024 * 1024))
    try:
        mod.validate_package_archive(archive_bytes.getvalue())
    except mod.SourceFetchError as exc:
        assert str(exc) == "unsafe_package_compression_ratio"
    else:
        raise AssertionError("expected package compression-ratio rejection")


def test_source_tls_context_keeps_certificate_checks_and_tls12_minimum(monkeypatch):
    mod = load_module()
    context = mod.build_source_ssl_context()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True

    class BrokenContext:
        minimum_version = None

        def set_ciphers(self, _value):
            raise ssl.SSLError("SECRET TLS DETAIL")

    monkeypatch.setattr(mod.ssl, "create_default_context", lambda: BrokenContext())
    try:
        mod.build_source_ssl_context()
    except mod.ConfigurationError as exc:
        assert str(exc) == "unable to configure NCS TLS compatibility"
        assert "SECRET" not in str(exc)
    else:
        raise AssertionError("expected TLS configuration failure")


def test_main_returns_nonzero_when_summary_fails_closed(tmp_path, monkeypatch):
    mod = load_module()

    class FakeContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(mod, "load_digest_key_from_env", lambda _name: b"k" * 32)
    monkeypatch.setattr(mod, "SourceClient", lambda **_kwargs: FakeContext())
    monkeypatch.setattr(mod, "ParseReviewClient", lambda **_kwargs: FakeContext())
    monkeypatch.setattr(
        mod,
        "run_benchmark",
        lambda **_kwargs: {"summary": {"passed": False}, "postings": [], "cases": []},
    )
    monkeypatch.setattr(
        mod,
        "write_reports",
        lambda _payload, _output: (
            tmp_path / "result.json",
            tmp_path / "result.csv",
            tmp_path / "result.md",
        ),
    )

    assert mod.main(["--output-dir", str(tmp_path)]) == 1


def test_main_redacts_top_level_source_failure(capsys, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "load_digest_key_from_env", lambda _name: b"k" * 32)

    def fail_source_client(**_kwargs):
        raise mod.SourceFetchError("SECRET posting URL and identifier")

    monkeypatch.setattr(mod, "SourceClient", fail_source_client)

    assert mod.main([]) == 1
    output = capsys.readouterr().out
    assert "upstream_audit_failure" in output
    assert "SECRET" not in output


def test_run_benchmark_selects_ordered_window_after_skip(monkeypatch):
    mod = load_module()

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(mod, "use_ncs_mcp_request_session", fake_session)
    postings = [
        mod.Posting(recrt_no=str(index), title="SECRET")
        for index in range(1, 4)
    ]

    class FakeSourceClient:
        def list_postings(self, *, max_postings, max_pages):
            assert max_postings == 3
            assert max_pages == 1
            return postings

        def fetch_detail_html(self, posting):
            assert posting.recrt_no == "3"
            return "<html></html>"

        def fetch_posting_source_union(self, posting):
            return {
                "jdsptList": [
                    {
                        "ncsLclasCd": "01",
                        "ncsMclasCd": "02",
                        "ncsSclasCd": "03",
                        "ncsSubdCd": "04",
                        "ncsSubdCdnm": "secret-detail",
                    }
                ]
            }

    monkeypatch.setattr(mod, "parse_jd_attachments", lambda _html, _posting: [])
    payload = mod.run_benchmark(
        source_client=FakeSourceClient(),
        parse_client=object(),
        digester=mod.PrivacyDigester(b"w" * 32),
        max_postings=1,
        skip_postings=2,
        max_pages=1,
        delay_seconds=0,
        search_units_fn=lambda *_args, **_kwargs: [],
        get_ksa_fn=lambda *_args, **_kwargs: [],
    )

    assert payload["summary"]["posting_count"] == 1
    assert payload["summary"]["sampling"]["skipped_posting_count"] == 2
    assert len(payload["postings"]) == 1
