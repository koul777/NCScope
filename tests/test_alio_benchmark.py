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
parse_benchmark_document = benchmark_alio_jd.parse_benchmark_document
diagnose_detail_mcp_matches = benchmark_alio_jd.diagnose_detail_mcp_matches
discover_detail_pages = benchmark_alio_jd.discover_detail_pages
detail_member_map = benchmark_alio_jd.detail_member_map
extract_detail_pages_from_list_html = benchmark_alio_jd._extract_detail_pages_from_list_html
summarize_detail_mcp_coverage = benchmark_alio_jd.summarize_detail_mcp_coverage
write_reports = benchmark_alio_jd.write_reports


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
                    "exact_match": False,
                    "exact_units": 0,
                    "exact_top": "",
                    "suggestion_count": 1,
                    "top_suggestion": "건축공사감리",
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
    assert "partial_detail_mcp_match" in csv_path.read_text(encoding="utf-8-sig")
    detail_csv = detail_csv_path.read_text(encoding="utf-8-sig")
    assert "member" in detail_csv
    assert "exact_match" in detail_csv
    assert "unit_name_match" in detail_csv
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
