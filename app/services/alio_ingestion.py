"""Bounded, human-reviewed inspection of public JOB-ALIO postings.

This module intentionally stops at metadata discovery.  It never downloads an
attachment or sends public-page content to a model.  The browser receives the
allowlisted posting/attachment URLs and a human must select the matching
notice and job-description files before using the normal upload/review flow.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx


ALIO_HOST = "job.alio.go.kr"
ALIO_LIST_PATH = "/recruit.do"
ALIO_DETAIL_PATH = "/recruitview.do"
ALIO_DOWNLOAD_PATHS = {"/download.json", "/download.do", "/fileDownload.do", "/recruitDownload.do"}
ALIO_MAX_HTML_BYTES = 1 * 1024 * 1024
ALIO_MAX_POSTINGS = 30
ALIO_MAX_ATTACHMENTS = 20
ALIO_MAX_REDIRECTS = 3
ALIO_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0)


class AlioIngestionError(RuntimeError):
    """A safe, user-facing error from bounded ALIO metadata inspection."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class _AlioPage:
    url: str
    text: str


def _clean_html_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", str(value or ""), flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _validate_alio_url(raw_url: str, *, allow_list: bool = True) -> str:
    value = str(raw_url or "").strip()
    if len(value) > 500:
        raise AlioIngestionError("url_too_long", "ALIO URL이 너무 깁니다.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or parsed.hostname != ALIO_HOST:
        raise AlioIngestionError(
            "url_not_allowed",
            "https://job.alio.go.kr의 공고 목록 또는 상세 URL만 사용할 수 있습니다.",
        )
    try:
        if parsed.port not in (None, 443):
            raise AlioIngestionError("url_not_allowed", "ALIO URL에는 기본 HTTPS 포트만 사용할 수 있습니다.")
    except ValueError as exc:
        raise AlioIngestionError("url_not_allowed", "ALIO URL의 포트 형식이 올바르지 않습니다.") from exc
    if parsed.username or parsed.password or parsed.fragment:
        raise AlioIngestionError("url_not_allowed", "인증정보·fragment가 포함된 URL은 사용할 수 없습니다.")
    path = parsed.path or "/"
    if path == ALIO_LIST_PATH and allow_list:
        return value
    if path in ALIO_DOWNLOAD_PATHS:
        file_no = parse_qs(parsed.query).get("fileNo", [""])[0]
        if re.fullmatch(r"\d{1,20}", file_no):
            return f"https://{ALIO_HOST}{path}?fileNo={file_no}"
        raise AlioIngestionError("attachment_id_required", "ALIO 첨부파일 URL에 유효한 fileNo가 필요합니다.")
    if path != ALIO_DETAIL_PATH:
        raise AlioIngestionError(
            "url_not_allowed",
            "ALIO 공고 목록(/recruit.do) 또는 공고 상세(/recruitview.do?idx=...) URL을 입력하세요.",
        )
    idx = parse_qs(parsed.query).get("idx", [""])[0]
    if not re.fullmatch(r"\d{1,20}", idx):
        raise AlioIngestionError("detail_id_required", "공고 상세 URL에 유효한 idx가 필요합니다.")
    # Keep only the public identifier; discard tracking parameters before fetch.
    return f"https://{ALIO_HOST}{ALIO_DETAIL_PATH}?idx={idx}"


def _bounded_response_text(response: Any, *, max_bytes: int = ALIO_MAX_HTML_BYTES) -> str:
    content_length = str(getattr(response, "headers", {}).get("content-length", "")).strip()
    try:
        if content_length and int(content_length) > max_bytes:
            raise AlioIngestionError("page_too_large", "ALIO 페이지가 허용된 크기를 초과했습니다.")
    except ValueError:
        pass

    chunks: list[bytes] = []
    size = 0
    iterator = response.iter_bytes()
    for chunk in iterator:
        data = bytes(chunk or b"")
        size += len(data)
        if size > max_bytes:
            raise AlioIngestionError("page_too_large", "ALIO 페이지가 허용된 크기를 초과했습니다.")
        chunks.append(data)
    raw = b"".join(chunks)
    encoding = getattr(response, "encoding", None) or "utf-8"
    return raw.decode(str(encoding), errors="replace")


def _fetch_bounded(
    url: str,
    *,
    client_factory: Callable[..., Any] = httpx.Client,
) -> _AlioPage:
    current = _validate_alio_url(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "NCScope-public-metadata-review/1.0",
        "Referer": f"https://{ALIO_HOST}{ALIO_LIST_PATH}",
    }
    try:
        with client_factory(timeout=ALIO_TIMEOUT, follow_redirects=False) as client:
            for _ in range(ALIO_MAX_REDIRECTS + 1):
                response = client.get(current, headers=headers)
                if 300 <= int(response.status_code) < 400:
                    location = str(response.headers.get("location", "")).strip()
                    if not location:
                        raise AlioIngestionError("redirect_invalid", "ALIO 페이지 redirect 위치가 비어 있습니다.")
                    current = _validate_alio_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content_type = str(response.headers.get("content-type", "")).lower()
                if content_type and "html" not in content_type and "text/plain" not in content_type:
                    raise AlioIngestionError("unexpected_content", "ALIO 공고 페이지가 HTML이 아닙니다.")
                return _AlioPage(current, _bounded_response_text(response))
    except AlioIngestionError:
        raise
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise AlioIngestionError("upstream_timeout", "ALIO 페이지 응답 시간이 초과되었습니다.", retryable=True) from exc
    except httpx.HTTPStatusError as exc:
        status = int(exc.response.status_code)
        raise AlioIngestionError(
            "upstream_http_error",
            f"ALIO 페이지를 가져오지 못했습니다(HTTP {status}).",
            retryable=status == 429 or status >= 500,
        ) from exc
    except httpx.RequestError as exc:
        raise AlioIngestionError("upstream_unavailable", "ALIO 페이지에 연결할 수 없습니다.", retryable=True) from exc
    raise AlioIngestionError("redirect_limit", "ALIO 페이지 redirect 횟수가 허용 범위를 초과했습니다.")


def _extract_postings(text: str, source_url: str, *, limit: int = ALIO_MAX_POSTINGS) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+href=["\'](?P<href>[^"\']*recruitview\.do\?[^"\']*idx=(?P<idx>\d+)[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        idx = match.group("idx")
        if idx in seen:
            continue
        seen.add(idx)
        rows.append(
            {
                "posting_id": idx,
                "title": _clean_html_text(match.group("label")) or f"ALIO 공고 {idx}",
                "url": _validate_alio_url(urljoin(source_url, html.unescape(match.group("href"))), allow_list=False),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _attachment_kind(name: str) -> str:
    key = re.sub(r"\s+", "", str(name or "")).lower()
    if any(term in key for term in ("직무기술서", "직무설명", "ncs", "직무설명자료")):
        return "job_description"
    if any(term in key for term in ("공고문", "채용공고", "모집공고", "채용계획")):
        return "notice"
    return "other"


def _extract_attachments(text: str, source_url: str, *, limit: int = ALIO_MAX_ATTACHMENTS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+href=["\'](?P<href>[^"\']*(?:download\.(?:json|do)\?fileNo=\d+|fileDownload\.do\?fileNo=\d+)[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for sequence, match in enumerate(pattern.finditer(text), start=1):
        href = html.unescape(match.group("href"))
        absolute = _validate_alio_url(urljoin(source_url, href), allow_list=False)
        if absolute in seen:
            continue
        seen.add(absolute)
        name = _clean_html_text(match.group("label")) or f"ALIO 첨부파일 {sequence}"
        rows.append(
            {
                "attachment_id": str(sequence),
                "name": name[:160],
                "url": absolute,
                "kind": _attachment_kind(name),
                "selection_required": True,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def inspect_alio_url(
    raw_url: str,
    *,
    client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, Any]:
    """Inspect an ALIO list/detail URL without downloading attachments."""
    requested = _validate_alio_url(raw_url)
    page = _fetch_bounded(requested, client_factory=client_factory)
    parsed = urlsplit(page.url)
    if parsed.path == ALIO_LIST_PATH:
        postings = _extract_postings(page.text, page.url)
        return {
            "status": "human_review_required",
            "source_url": page.url,
            "source_kind": "posting_list",
            "selection": {"required": True, "kind": "posting", "max_selected": 1},
            "postings": postings,
            "attachments": [],
            "message": "공고를 하나 선택한 뒤 상세 URL을 다시 조회하세요.",
        }

    idx = parse_qs(parsed.query).get("idx", [""])[0]
    title_match = re.search(
        r'<p[^>]+class=["\'][^"\']*titleH2[^"\']*["\'][^>]*>(.*?)</p>',
        page.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    headings = [
        _clean_html_text(item)
        for item in re.findall(r"<h2[^>]*>(.*?)</h2>", page.text, flags=re.IGNORECASE | re.DOTALL)
    ]
    attachments = _extract_attachments(page.text, page.url)
    return {
        "status": "human_review_required",
        "source_url": page.url,
        "source_kind": "posting_detail",
        "posting": {
            "posting_id": idx,
            "organization": headings[0] if headings else "",
            "title": _clean_html_text(title_match.group(1)) if title_match else "",
        },
        "selection": {
            "required": True,
            "kind": "attachments",
            "max_selected": 2,
            "required_kinds": ["notice", "job_description"],
        },
        "postings": [],
        "attachments": attachments,
        "message": "같은 공고의 공고문과 직무기술서를 사람이 확인·선택한 뒤 일반 업로드 검토를 진행하세요.",
    }
