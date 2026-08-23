from __future__ import annotations

import pytest

from app.services.alio_ingestion import (
    AlioIngestionError,
    ALIO_MAX_HTML_BYTES,
    _validate_alio_url,
    _attachment_kind,
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

    def get(self, _url, **_kwargs):
        return self.responses.pop(0)


def _factory_for(*responses: _FakeResponse):
    def factory(**kwargs):
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
    assert [row["kind"] for row in result["attachments"]] == ["job_description", "notice", "other"]
    assert all(row["selection_required"] for row in result["attachments"])
    assert result["selection"]["required_kinds"] == ["notice", "job_description"]
    assert "2026년 일반행정 채용" not in result["attachments"][0]["url"]


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


def test_api_contract_exposes_metadata_only_and_keeps_selection_human_review(monkeypatch):
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
