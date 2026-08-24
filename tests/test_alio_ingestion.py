from __future__ import annotations

import pytest

from app.services.alio_ingestion import (
    AlioAttachmentDownload,
    AlioIngestionError,
    ALIO_MAX_HTML_BYTES,
    _validate_alio_url,
    _attachment_kind,
    download_alio_attachment,
    inspect_alio_url,
)


class _FakeResponse:
    def __init__(self, body: str | bytes, *, status_code: int = 200, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8", **(headers or {})}
        self.encoding = "utf-8"
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_bytes(self):
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse], **_kwargs):
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    class _Stream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    def stream(self, method, _url, **_kwargs):
        assert method == "GET"
        return self._Stream(self.responses.pop(0))

    def get(self, *_args, **_kwargs):
        raise AssertionError("bounded ALIO fetches must use streaming requests")


def _factory_for(*responses: _FakeResponse):
    def factory(**kwargs):
        assert kwargs.get("trust_env") is False
        return _FakeClient(list(responses), **kwargs)

    return factory


def test_validate_alio_url_allowlists_public_list_detail_and_attachment_paths():
    assert _validate_alio_url("https://job.alio.go.kr/recruit.do?foo=bar") == "https://job.alio.go.kr/recruit.do?foo=bar"
    assert _validate_alio_url("https://job.alio.go.kr/recruitview.do?idx=123&tracking=1") == (
        "https://job.alio.go.kr/recruitview.do?idx=123"
    )
    assert _validate_alio_url("https://job.alio.go.kr/download.json?fileNo=88", allow_list=False) == (
        "https://job.alio.go.kr/download.json?fileNo=88"
    )
    assert _validate_alio_url("https://www.alio.go.kr/download/download.json?fileNo=88", allow_list=False) == (
        "https://www.alio.go.kr/download/download.json?fileNo=88"
    )
    for value in (
        "http://job.alio.go.kr/recruit.do",
        "https://example.com/recruit.do",
        "https://job.alio.go.kr:8443/recruit.do",
        "https://job.alio.go.kr/recruitview.do?idx=abc",
        "https://job.alio.go.kr/recruitview.do?idx=123#fragment",
    ):
        with pytest.raises(AlioIngestionError):
            _validate_alio_url(value)


def test_attachment_kind_classifies_real_korean_alio_filenames():
    assert _attachment_kind("채용공고문.pdf") == "notice"
    assert _attachment_kind("NCS 기반 직무기술서.pdf") == "job_description"
    assert _attachment_kind("붙임2.hwp", "공고문") == "notice"
    assert _attachment_kind("첨부자료.zip", "직무기술서") == "job_description"


def test_inspect_list_returns_postings_and_human_review_contract():
    body = """
    <a href="/recruitview.do?idx=101">첫 번째 공고</a>
    <a href="/recruitview.do?idx=101">중복 공고</a>
    <a href="/recruitview.do?idx=102">두 번째 <b>공고</b></a>
    """
    result = inspect_alio_url(
        "https://job.alio.go.kr/recruit.do",
        client_factory=_factory_for(_FakeResponse(body)),
    )
    assert result["status"] == "human_review_required"
    assert result["selection"] == {"required": True, "kind": "posting", "max_selected": 1}
    assert [row["posting_id"] for row in result["postings"]] == ["101", "102"]
    assert result["postings"][1]["title"] == "두 번째 공고"
    assert result["attachments"] == []


def test_inspect_detail_returns_classified_attachment_metadata_without_downloading_files():
    body = """
    <h2>한국학중앙연구원</h2>
    <p class="titleH2">2026년 일반행정 채용</p>
    <table><tr><th>직무기술서</th><td>
      <a href="/download.json?fileNo=11">NCS 기반 직무기술서.pdf</a>
      <a href="/download.json?fileNo=12">채용공고문.pdf</a>
      <a href="/download.json?fileNo=13">기타 안내.txt</a>
    </td></tr></table>
    """
    result = inspect_alio_url(
        "https://job.alio.go.kr/recruitview.do?idx=987&foo=tracking",
        client_factory=_factory_for(_FakeResponse(body)),
    )
    assert result["status"] == "human_review_required"
    assert result["posting"] == {
        "posting_id": "987",
        "organization": "한국학중앙연구원",
        "title": "2026년 일반행정 채용",
    }
    assert [row["kind"] for row in result["attachments"]] == ["job_description", "notice", "job_description"]
    assert all(row["selection_required"] for row in result["attachments"])
    assert result["selection"]["required_kinds"] == ["notice", "job_description"]
    assert "2026년 일반행정 채용" not in result["attachments"][0]["url"]


def test_inspect_detail_prefers_attachment_table_heading_and_extracts_inline_notice():
    body = """
    <h2>한국공공기관</h2><p class="titleH2">행정직 채용</p>
    <div class="detailTxt"><table><tr><th>표준직무(NCS)</th><td>경영·회계·사무</td></tr></table></div>
    <div id="tab-1"><h4>응시자격</h4><p>행정업무 수행 가능자</p></div><div id="tab-2"></div>
    <table>
      <tr><th>공고문</th><td><a href="https://www.alio.go.kr/download/download.json?fileNo=301">붙임1.hwp</a></td></tr>
      <tr><th>직무기술서</th><td><a href="https://www.alio.go.kr/download/download.json?fileNo=302">붙임2.pdf</a></td></tr>
    </table>
    """
    result = inspect_alio_url(
        "https://job.alio.go.kr/recruitview.do?idx=1001",
        client_factory=_factory_for(_FakeResponse(body)),
    )
    assert [row["kind"] for row in result["attachments"]] == ["notice", "job_description"]
    assert [row["file_no"] for row in result["attachments"]] == ["301", "302"]
    assert result["detail_fields"]["표준직무(NCS)"] == "경영·회계·사무"
    assert result["inline_notice"]["응시자격"] == "행정업무 수행 가능자"


def test_inspect_detail_keeps_bounded_attachments_after_first_twenty():
    rows = "".join(
        (
            "<tr><th>attachments</th><td>"
            f'<a href="/download.json?fileNo={index}">'
            f"{'NCS job description.pdf' if index == 25 else f'attachment-{index}.pdf'}"
            "</a></td></tr>"
        )
        for index in range(1, 26)
    )
    result = inspect_alio_url(
        "https://job.alio.go.kr/recruitview.do?idx=987",
        client_factory=_factory_for(_FakeResponse(f"<table>{rows}</table>")),
    )

    assert len(result["attachments"]) == 25
    assert result["attachments"][-1]["file_no"] == "25"
    assert result["attachments"][-1]["kind"] == "job_description"


def test_download_attachment_is_bounded_and_uses_content_disposition_filename():
    response = _FakeResponse(
        b"%PDF-test",
        headers={
            "content-type": "application/pdf",
            "content-disposition": "attachment; filename*=UTF-8''NCS%20%EC%A7%81%EB%AC%B4%EA%B8%B0%EC%88%A0%EC%84%9C.pdf",
            "content-length": "9",
        },
    )
    result = download_alio_attachment(
        "https://www.alio.go.kr/download/download.json?fileNo=88",
        expected_name="fallback.pdf",
        max_bytes=100,
        client_factory=_factory_for(response),
    )
    assert isinstance(result, AlioAttachmentDownload)
    assert result.filename == "NCS 직무기술서.pdf"
    assert result.content_type == "application/pdf"
    assert result.data == b"%PDF-test"


def test_download_attachment_rejects_stream_over_limit():
    response = _FakeResponse(
        b"0123456789",
        headers={"content-type": "application/pdf"},
    )
    with pytest.raises(AlioIngestionError) as error:
        download_alio_attachment(
            "https://www.alio.go.kr/download/download.json?fileNo=89",
            expected_name="too-large.pdf",
            max_bytes=5,
            client_factory=_factory_for(response),
        )
    assert error.value.code == "attachment_too_large"


def test_download_attachment_rejects_html_disguised_by_expected_pdf_name():
    response = _FakeResponse(
        b"<html><body>upstream error</body></html>",
        headers={"content-type": "text/html"},
    )
    with pytest.raises(AlioIngestionError) as error:
        download_alio_attachment(
            "https://www.alio.go.kr/download/download.json?fileNo=90",
            expected_name="fake.pdf",
            max_bytes=1000,
            client_factory=_factory_for(response),
        )
    assert error.value.code == "attachment_type_not_supported"


def test_inspect_rejects_oversized_html_before_returning_metadata():
    oversized = _FakeResponse(b"x" * (ALIO_MAX_HTML_BYTES + 1))
    with pytest.raises(AlioIngestionError) as error:
        inspect_alio_url(
            "https://job.alio.go.kr/recruit.do",
            client_factory=_factory_for(oversized),
        )
    assert error.value.code == "page_too_large"


def test_inspect_does_not_follow_redirects_to_another_host():
    redirect = _FakeResponse(
        "",
        status_code=302,
        headers={"location": "https://example.com/recruit.do"},
    )
    with pytest.raises(AlioIngestionError) as error:
        inspect_alio_url(
            "https://job.alio.go.kr/recruit.do",
            client_factory=_factory_for(redirect),
        )
    assert error.value.code == "url_not_allowed"


def test_api_contract_discovers_metadata_and_keeps_selection_human_review(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(
        main,
        "inspect_alio_url",
        lambda url: {
            "status": "human_review_required",
            "source_url": url,
            "selection": {"required": True, "kind": "attachments", "max_selected": 2},
            "postings": [],
            "attachments": [],
        },
    )
    client = TestClient(main.app)
    missing = client.post("/api/alio/attachments", json={})
    assert missing.status_code == 422
    response = client.post(
        "/api/alio/attachments",
        json={"url": "https://job.alio.go.kr/recruitview.do?idx=1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "human_review_required"


def test_api_can_proxy_one_bounded_alio_file_into_existing_review_flow(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(
        main,
        "download_alio_attachment",
        lambda url, expected_name, max_bytes: AlioAttachmentDownload(
            url=url,
            filename="NCS 기반 직무기술서.pdf",
            content_type="application/pdf",
            data=b"%PDF-safe-test",
        ),
    )
    monkeypatch.setattr(main, "_record_audit_event", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    response = client.post(
        "/api/alio/attachment",
        json={
            "url": "https://www.alio.go.kr/download/download.json?fileNo=88",
            "name": "직무기술서.pdf",
        },
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-safe-test"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
