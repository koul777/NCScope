from __future__ import annotations

import io
import importlib.util
from pathlib import Path
import sys
import zipfile

import pytest

from app.services.kordoc_parser import KordocParseError


_BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_alio_jd.py"
_SPEC = importlib.util.spec_from_file_location("ncscope_benchmark_alio_jd", _BENCHMARK_PATH)
assert _SPEC and _SPEC.loader
benchmark_alio_jd = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = benchmark_alio_jd
_SPEC.loader.exec_module(benchmark_alio_jd)
benchmark_one = benchmark_alio_jd.benchmark_one
parse_benchmark_document = benchmark_alio_jd.parse_benchmark_document
diagnose_detail_mcp_matches = benchmark_alio_jd.diagnose_detail_mcp_matches
discover_detail_pages = benchmark_alio_jd.discover_detail_pages
detail_member_map = benchmark_alio_jd.detail_member_map
extract_detail_pages_from_list_html = benchmark_alio_jd._extract_detail_pages_from_list_html
summarize_detail_mcp_coverage = benchmark_alio_jd.summarize_detail_mcp_coverage
no_detail_category = benchmark_alio_jd.no_detail_category
write_reports = benchmark_alio_jd.write_reports


def test_get_with_retries_recovers_from_transient_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIO_HTTP_RETRIES", "2")
    monkeypatch.setenv("ALIO_HTTP_RETRY_DELAY_SEC", "0")

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            request = benchmark_alio_jd.httpx.Request("GET", url)
            if self.calls == 1:
                raise benchmark_alio_jd.httpx.ConnectError("temporary dns failure", request=request)
            return benchmark_alio_jd.httpx.Response(200, text="ok", request=request)

    client = FlakyClient()

    response = benchmark_alio_jd._get_with_retries(client, "https://example.test")

    assert response.text == "ok"
    assert client.calls == 2


def test_get_with_retries_does_not_retry_non_retryable_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIO_HTTP_RETRIES", "3")
    monkeypatch.setenv("ALIO_HTTP_RETRY_DELAY_SEC", "0")

    class NotFoundClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            request = benchmark_alio_jd.httpx.Request("GET", url)
            return benchmark_alio_jd.httpx.Response(404, text="missing", request=request)

    client = NotFoundClient()

    with pytest.raises(benchmark_alio_jd.httpx.HTTPStatusError):
        benchmark_alio_jd._get_with_retries(client, "https://example.test/missing")

    assert client.calls == 1


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _mark_zip_encrypted(data: bytes) -> bytes:
    blob = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while True:
            idx = blob.find(signature, start)
            if idx < 0:
                break
            flags = int.from_bytes(blob[idx + offset : idx + offset + 2], "little") | 0x1
            blob[idx + offset : idx + offset + 2] = flags.to_bytes(2, "little")
            start = idx + 4
    return bytes(blob)


def test_extract_detail_pages_from_list_html_deduplicates_seen_ids() -> None:
    seen: set[str] = {"100"}
    html = """
    <a href="/recruitview.do?idx=100">old</a>
    <a href="/recruitview.do?idx=101">first</a>
    <a href="/recruitview.do?idx=102">second</a>
    """

    pages = extract_detail_pages_from_list_html(html, seen, limit=2)

    assert [page.idx for page in pages] == ["101", "102"]
    assert seen == {"100", "101", "102"}


def test_discover_detail_pages_reads_multiple_pages() -> None:
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text
            self.encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self):
            self.calls: list[int] = []
            self.pages = {
                1: '<a href="/recruitview.do?idx=201">one</a><a href="/recruitview.do?idx=202">two</a>',
                2: '<a href="/recruitview.do?idx=203">three</a><a href="/recruitview.do?idx=204">four</a>',
            }

        def get(self, url, params=None, headers=None, follow_redirects=True):
            page_no = int((params or {}).get("pageNo") or 1)
            self.calls.append(page_no)
            return FakeResponse(self.pages.get(page_no, ""))

    client = FakeClient()

    pages = discover_detail_pages(client, limit=3)

    assert [page.idx for page in pages] == ["201", "202", "203"]
    assert client.calls == [1, 2]


def test_discover_detail_pages_handles_sparse_pages() -> None:
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text
            self.encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self):
            self.calls: list[int] = []

        def get(self, url, params=None, headers=None, follow_redirects=True):
            page_no = int((params or {}).get("pageNo") or 1)
            self.calls.append(page_no)
            if page_no <= 5:
                return FakeResponse(f'<a href="/recruitview.do?idx={300 + page_no}">page {page_no}</a>')
            return FakeResponse("")

    client = FakeClient()

    pages = discover_detail_pages(client, limit=5)

    assert [page.idx for page in pages] == ["301", "302", "303", "304", "305"]
    assert client.calls == [1, 2, 3, 4, 5]


def test_benchmark_one_marks_detail_docs_when_mcp_url_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NCS_MCP_URL", raising=False)
    page = benchmark_alio_jd.DetailPage(idx="1", url="https://example.test/recruitview.do?idx=1")

    monkeypatch.setattr(benchmark_alio_jd, "fetch_text", lambda *args, **kwargs: "<html></html>")
    monkeypatch.setattr(benchmark_alio_jd, "extract_detail_metadata", lambda text, page: page)
    monkeypatch.setattr(benchmark_alio_jd, "structure_job_notice", lambda parsed, filename: {"fields": {}})
    monkeypatch.setattr(
        benchmark_alio_jd,
        "extract_jd_attachments",
        lambda text: [benchmark_alio_jd.Attachment(url="https://example.test/jd.txt", name="jd.txt")],
    )

    def fake_download(client, attachment, out_path, referer, max_bytes):
        out_path.write_bytes(b"detail document")
        return 15

    monkeypatch.setattr(benchmark_alio_jd, "download_attachment", fake_download)
    monkeypatch.setattr(
        benchmark_alio_jd,
        "parse_benchmark_document",
        lambda data, filename, max_bytes: {"markdown": "detail-a", "metadata": {}},
    )
    monkeypatch.setattr(
        benchmark_alio_jd,
        "structure_job_description",
        lambda parsed, filename: {"fields": {"ncs_detail_candidates": ["detail-a"]}},
    )
    monkeypatch.setattr(
        benchmark_alio_jd,
        "diagnose_detail_mcp_matches",
        lambda details: (_ for _ in ()).throw(AssertionError("MCP diagnostics should be skipped")),
    )

    row = benchmark_one(object(), page, tmp_path, max_bytes=1024, include_ksa=True)

    assert row["status"] == "mcp_not_configured"
    assert row["mcp_configured"] is False
    assert row["detail_count"] == 1
    assert row["detail_diagnostics_skipped_reason"] == "NCS_MCP_URL not configured"

    md_path, csv_path, _ = write_reports([row], tmp_path)
    md_text = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    assert "Parsed documents: 1" in md_text
    assert "Documents with detail candidates but no MCP match: 0" in md_text
    assert "Documents with detail candidates skipped because MCP URL is not configured: 1" in md_text
    assert "Detail candidates with diagnostics skipped because MCP URL is not configured: 1" in md_text
    assert "Detail match diagnostic counts: not_evaluated=1" in md_text
    assert "MCP configured" in md_text
    assert "diagnostics skip reason" in md_text
    assert "NCS_MCP_URL not configured" in md_text
    assert "mcp_not_configured" in csv_text
    assert "detail_diagnostics_skipped_reason" in csv_text


def test_benchmark_zip_txt_member_is_parsed_without_kordoc() -> None:
    data = _zip_bytes({"직무기술서.txt": "세분류: 경영기획\n담당업무: 경영계획 수립"})

    parsed = parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)

    assert "ZIP member: 직무기술서.txt" in parsed["markdown"]
    assert "세분류: 경영기획" in parsed["markdown"]
    assert parsed["metadata"]["archive"] is True
    assert parsed["metadata"]["members"] == [{"filename": "직무기술서.txt", "suffix": ".txt"}]


def test_benchmark_zip_image_member_uses_kordoc_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_parse_with_kordoc(data: bytes, filename: str, ocr: bool) -> dict:
        calls.append((filename, ocr))
        return {"markdown": "세분류: 경영기획"}

    monkeypatch.setattr(benchmark_alio_jd, "parse_with_kordoc", fake_parse_with_kordoc)
    data = _zip_bytes({"직무기술서.jpg": "fake image bytes"})

    parsed = parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)

    assert "ZIP member: 직무기술서.jpg" in parsed["markdown"]
    assert calls == [("직무기술서.jpg", True)]


def test_detail_member_map_tracks_zip_member_sources() -> None:
    data = _zip_bytes(
        {
            "총무_직무기술서.txt": "세분류: 총무",
            "사무행정_직무기술서.txt": "세분류: 사무행정",
        }
    )
    parsed = parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)

    mapping = detail_member_map(parsed, fallback_member="alio.zip", details=["총무", "사무행정"])

    assert mapping[benchmark_alio_jd._detail_key("총무")] == "총무_직무기술서.txt"
    assert mapping[benchmark_alio_jd._detail_key("사무행정")] == "사무행정_직무기술서.txt"


def test_benchmark_zip_encrypted_member_returns_parse_error() -> None:
    data = _mark_zip_encrypted(_zip_bytes({"직무기술서.txt": "세분류: 경영기획"}))

    with pytest.raises(KordocParseError, match="ZIP contains no parseable"):
        parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)


def test_detail_diagnostics_records_exact_and_suggestion(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(details: list[str], max_units: int) -> list[dict]:
        if details == ["사무행정"]:
            return [{"ncsClCd": "0202030201_25v3", "compeUnitName": "문서작성"}]
        return []

    def fake_suggest(details: list[str], max_units: int) -> list[dict]:
        assert details == ["건축감리"]
        return [{"ncsClCd": "1403020101_25v3", "compeUnitName": "건축공사감리"}]

    monkeypatch.setattr(benchmark_alio_jd, "search_units_by_detail", fake_search)
    monkeypatch.setattr(benchmark_alio_jd, "suggest_units_by_text", fake_suggest)

    detail_rows, units = diagnose_detail_mcp_matches(["사무행정", "건축감리"])

    assert len(units) == 1
    assert detail_rows[0]["exact_match"] is True
    assert detail_rows[0]["exact_units"] == 1
    assert detail_rows[1]["exact_match"] is False
    assert detail_rows[1]["exact_units"] == 0
    assert detail_rows[1]["suggestion_count"] == 1
    assert "건축공사감리" in detail_rows[1]["top_suggestion"]
    assert detail_rows[0]["match_diagnostic"] == "exact_detail"
    assert detail_rows[1]["match_diagnostic"] == "semantic_suggestion_unverified"


def test_detail_diagnostics_records_unit_name_suggestion_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_alio_jd, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        benchmark_alio_jd,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "1203040206_23v2",
                "compeUnitName": "casino customer support",
                "canonicalDetailName": "casino operations management",
                "isExactUnitNameMatch": True,
            }
        ],
    )

    detail_rows, units = diagnose_detail_mcp_matches(["casino customer support"])

    assert units == []
    assert detail_rows[0]["exact_match"] is False
    assert detail_rows[0]["suggestion_codes"] == "1203040206_23v2"
    assert detail_rows[0]["suggestion_canonical_details"] == "casino operations management"
    assert detail_rows[0]["unit_name_match"] is True
    assert detail_rows[0]["unit_name_match_top"] == "casino customer support"
    assert detail_rows[0]["unit_name_parent_details"] == "casino operations management"
    assert detail_rows[0]["resolved_parent_detail"] == "casino operations management"
    assert detail_rows[0]["match_diagnostic"] == "unit_name_only"
    assert detail_rows[0]["review_action"] == "manual_review_unit_name"


def test_detail_diagnostics_records_canonical_detail_exact_suggestion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_alio_jd, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        benchmark_alio_jd,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "1204020101_23v2",
                "compeUnitName": "문화관광정책 개발",
                "canonicalDetailName": "문화·관광정책",
                "ncsSubdCdnm": "문화·관광정책",
                "isExactDetailMatch": True,
                "isExactUnitNameMatch": False,
            }
        ],
    )

    detail_rows, units = diagnose_detail_mcp_matches(["문화・관광정책"])

    assert units == []
    assert detail_rows[0]["canonical_detail_match"] is True
    assert detail_rows[0]["canonical_detail_match_top"] == "문화·관광정책"
    assert detail_rows[0]["resolved_parent_detail"] == "문화·관광정책"
    assert detail_rows[0]["match_diagnostic"] == "catalog_gap_verified_source_label"
    assert detail_rows[0]["unit_name_match"] is False
    assert detail_rows[0]["review_action"] == "manual_review_canonical_detail"


@pytest.mark.parametrize(
    "label",
    [
        "간호업무 보조",
        "간호행정 보조",
        "재원환자 관리",
        "응급 환자 관리",
        "간호수행",
        "간호행정관리",
        "영상의학",
        "임상병리",
    ],
)
def test_detail_diagnostics_separates_healthcare_specialized_source_labels(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    monkeypatch.setattr(benchmark_alio_jd, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(benchmark_alio_jd, "suggest_units_by_text", lambda details, max_units: [])

    detail_rows, units = diagnose_detail_mcp_matches([label])

    assert units == []
    assert detail_rows[0]["exact_match"] is False
    assert detail_rows[0]["match_diagnostic"] == "specialized_healthcare_label_unserved_by_mcp"
    assert detail_rows[0]["review_action"] == "manual_review_healthcare_specialized_label"
    assert "do not auto-alias" in detail_rows[0]["review_reason"]


def test_detail_diagnostics_keeps_healthcare_specialized_labels_ahead_of_loose_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark_alio_jd, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        benchmark_alio_jd,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "2402010507_23v2",
                "compeUnitName": "동물임상병리진단 지원",
                "canonicalDetailName": "동물보건",
                "isExactUnitNameMatch": True,
                "isExactDetailMatch": True,
            }
        ],
    )

    detail_rows, units = diagnose_detail_mcp_matches(["임상병리"])

    assert units == []
    assert detail_rows[0]["suggestion_count"] == 1
    assert detail_rows[0]["canonical_detail_match"] is True
    assert detail_rows[0]["unit_name_match"] is True
    assert detail_rows[0]["match_diagnostic"] == "specialized_healthcare_label_unserved_by_mcp"
    assert detail_rows[0]["review_action"] == "manual_review_healthcare_specialized_label"


def test_detail_diagnostics_upgrades_healthcare_label_when_exact_units_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        benchmark_alio_jd,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "0602020001_26v1",
                "compeUnitName": "간호수행",
                "ncsSubdCdnm": details[0],
                "resolvedDetailName": details[0],
                "source": "ncs-mcp-exact",
            }
        ],
    )
    monkeypatch.setattr(benchmark_alio_jd, "suggest_units_by_text", lambda details, max_units: [])

    detail_rows, units = diagnose_detail_mcp_matches(["간호수행"])

    assert len(units) == 1
    assert detail_rows[0]["exact_match"] is True
    assert detail_rows[0]["match_diagnostic"] == "exact_detail"
    assert detail_rows[0]["review_action"] == "auto_exact_detail"


def test_detail_diagnostics_records_exact_alias_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        benchmark_alio_jd,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "1403010301_23v3",
                "compeUnitName": "공사착공관리",
                "ncsSubdCdnm": "건축공사감리",
                "resolvedDetailName": "건축공사감리",
                "matchedDetailName": details[0],
                "source": "ncs-mcp-detail-alias",
            }
        ],
    )
    monkeypatch.setattr(benchmark_alio_jd, "suggest_units_by_text", lambda details, max_units: [])

    detail_rows, units = diagnose_detail_mcp_matches(["건축감리"])

    assert len(units) == 1
    assert detail_rows[0]["exact_match"] is True
    assert detail_rows[0]["exact_sources"] == "ncs-mcp-detail-alias"
    assert detail_rows[0]["exact_canonical_details"] == "건축공사감리"
    assert detail_rows[0]["resolved_parent_detail"] == "건축공사감리"
    assert detail_rows[0]["match_diagnostic"] == "exact_detail"
    assert detail_rows[0]["review_action"] == "auto_exact_detail"


def test_detail_coverage_summary_separates_unit_name_recovery() -> None:
    exact_count, unit_name_count, uncovered = summarize_detail_mcp_coverage(
        [
            {"detail": "사무행정", "exact_units": 2, "unit_name_match": False},
            {"detail": "카지노 고객 지원", "exact_units": 0, "unit_name_match": True},
            {"detail": "임상병리", "exact_units": 0, "unit_name_match": False},
        ]
    )

    assert exact_count == 1
    assert unit_name_count == 1
    assert uncovered == ["임상병리"]


def test_no_detail_category_groups_parser_absence_reasons() -> None:
    assert no_detail_category("no_ncs_mapping_declared") == "declared_no_ncs_mapping"
    assert (
        no_detail_category("job_document_without_explicit_ncs_detail")
        == "no_explicit_ncs_detail"
    )
    assert (
        no_detail_category("ncs_detail_header_without_candidate")
        == "ncs_table_without_extractable_detail"
    )


def test_write_reports_emits_detail_diagnostics_csv(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "1",
            "status": "partial_detail_mcp_match",
            "attachment": "직무기술서.pdf",
            "detail_count": 2,
            "detail_candidates": "사무행정; 건축감리",
            "detail_exact_match_count": 1,
            "detail_unmatched_count": 1,
            "detail_partial_match": True,
            "mcp_units": 3,
            "mcp_ksa": 2,
            "mcp_suggestions": 1,
            "_detail_rows": [
                {
                    "idx": "1",
                    "attachment": "직무기술서.pdf",
                    "member": "직무기술서.pdf",
                    "detail_seq": 2,
                    "detail": "건축감리",
                    "extraction_source": "markdown",
                    "extraction_page": 0,
                    "extraction_line": 12,
                    "extraction_snippet": "세분류: 건축감리",
                    "exact_match": False,
                    "exact_units": 0,
                    "exact_top": "",
                    "suggestion_count": 1,
                    "top_suggestion": "건축공사감리",
                    "resolved_parent_detail": "",
                    "match_diagnostic": "semantic_suggestion_unverified",
                    "review_action": "manual_review_semantic_suggestion",
                    "review_reason": "No exact detail match; semantic suggestions require human confirmation.",
                }
            ],
        }
    ]

    md_path, csv_path, detail_csv_path = write_reports(rows, tmp_path)

    assert md_path.exists()
    assert csv_path.exists()
    assert detail_csv_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert "Documents with partial detail MCP matches: 1" in md_text
    assert "Unit-name recovered detail labels: 0" in md_text
    assert "Detail match diagnostic counts: semantic_suggestion_unverified=1" in md_text
    assert "partial_detail_mcp_match" in csv_path.read_text(encoding="utf-8-sig")
    detail_csv = detail_csv_path.read_text(encoding="utf-8-sig")
    assert "member" in detail_csv
    assert "extraction_source" in detail_csv
    assert "extraction_snippet" in detail_csv
    assert "세분류: 건축감리" in detail_csv
    assert "exact_match" in detail_csv
    assert "exact_sources" in detail_csv
    assert "exact_canonical_details" in detail_csv
    assert "canonical_detail_match" in detail_csv
    assert "canonical_detail_match_top" in detail_csv
    assert "unit_name_match" in detail_csv
    assert "unit_name_parent_details" in detail_csv
    assert "resolved_parent_detail" in detail_csv
    assert "match_diagnostic" in detail_csv
    assert "review_action" in detail_csv
    assert "review_reason" in detail_csv
    assert "semantic_suggestion_unverified" in detail_csv
    assert "manual_review_semantic_suggestion" in detail_csv
    assert "False" in detail_csv


def test_write_reports_counts_unit_name_recovery_as_parsed(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "1",
            "status": "ok_unit_name_resolved",
            "attachment": "jd.zip",
            "detail_count": 2,
            "detail_candidates": "카지노 고객 지원; 카지노 영업 지원",
            "detail_exact_match_count": 0,
            "detail_unit_name_match_count": 2,
            "detail_unmatched_count": 0,
            "detail_partial_match": False,
            "mcp_units": 0,
            "mcp_ksa": 0,
            "mcp_suggestions": 2,
            "_detail_rows": [],
        }
    ]

    md_path, csv_path, _ = write_reports(rows, tmp_path)
    md_text = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    assert "Parsed documents: 1" in md_text
    assert "Documents with unit-name detail recovery: 1" in md_text
    assert "Unit-name recovered detail labels: 2" in md_text
    assert "ok_unit_name_resolved" in csv_text


def test_write_reports_surfaces_no_detail_absence_reasons(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "303000",
            "status": "parsed_no_detail",
            "attachment": "직무기술서.pdf",
            "detail_count": 0,
            "detail_candidates": "",
            "ncs_detail_absence_reason": "no_ncs_mapping_declared",
            "ncs_detail_absence_state": "saw_ncs_table; saw_detail_header; declared_no_mapping",
            "ncs_detail_absence_evidence": "NCS 세분류명 현재 NCS에 Mapping 가능한 직무가 없어 별도 분석",
            "ncs_detail_absence_filtered_candidate_reason": "",
            "ncs_detail_absence_saw_ncs_table": True,
            "ncs_detail_absence_saw_detail_header": True,
            "ncs_detail_absence_blank_or_dash_detail_cell": False,
            "ncs_detail_absence_declared_no_mapping": True,
            "detail_exact_match_count": 0,
            "detail_unit_name_match_count": 0,
            "detail_unmatched_count": 0,
            "detail_partial_match": False,
            "mcp_units": 0,
            "mcp_ksa": 0,
            "mcp_suggestions": 0,
            "_detail_rows": [],
        },
        {
            "idx": "303006",
            "status": "parsed_no_detail",
            "attachment": "직무기술서(사무보조).hwp",
            "detail_count": 0,
            "detail_candidates": "",
            "ncs_detail_absence_reason": "job_document_without_explicit_ncs_detail",
            "ncs_detail_absence_state": "",
            "ncs_detail_absence_evidence": "",
            "ncs_detail_absence_filtered_candidate_reason": "",
            "ncs_detail_absence_saw_ncs_table": False,
            "ncs_detail_absence_saw_detail_header": False,
            "ncs_detail_absence_blank_or_dash_detail_cell": False,
            "ncs_detail_absence_declared_no_mapping": False,
            "detail_exact_match_count": 0,
            "detail_unit_name_match_count": 0,
            "detail_unmatched_count": 0,
            "detail_partial_match": False,
            "mcp_units": 0,
            "mcp_ksa": 0,
            "mcp_suggestions": 0,
            "_detail_rows": [],
        },
    ]

    md_path, csv_path, _ = write_reports(rows, tmp_path)
    md_text = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    assert "Parsed-no-detail reason counts:" in md_text
    assert "Parsed-no-detail category counts:" in md_text
    assert "declared_no_ncs_mapping=1" in md_text
    assert "no_explicit_ncs_detail=1" in md_text
    assert "job_document_without_explicit_ncs_detail=1" in md_text
    assert "no_ncs_mapping_declared=1" in md_text
    assert "Parsed-no-detail state counts:" in md_text
    assert "[saw_ncs_table + saw_detail_header + declared_no_mapping]=1" in md_text
    assert "no-detail reason" in md_text
    assert "no-detail state" in md_text
    assert "no-detail category" in md_text
    assert "no_detail_category" in csv_text
    assert "declared_no_ncs_mapping" in csv_text
    assert "no_explicit_ncs_detail" in csv_text
    assert "ncs_detail_absence_reason" in csv_text
    assert "ncs_detail_absence_state" in csv_text
    assert "ncs_detail_absence_evidence" in csv_text
    assert "ncs_detail_absence_filtered_candidate_reason" in csv_text
    assert "ncs_detail_absence_saw_ncs_table" in csv_text
    assert "ncs_detail_absence_saw_detail_header" in csv_text
    assert "ncs_detail_absence_blank_or_dash_detail_cell" in csv_text
    assert "ncs_detail_absence_declared_no_mapping" in csv_text
    assert "no_ncs_mapping_declared" in csv_text
