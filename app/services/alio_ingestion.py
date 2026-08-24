"""Bounded discovery and download of public JOB-ALIO recruiting documents.

Only the public JOB-ALIO list/detail pages and ALIO's numeric ``fileNo``
download endpoints are accepted.  Attachment downloads remain byte-bounded
and are returned to the normal NCScope parse/review flow; this module never
sends a document to an AI model or decides an NCS classification by itself.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx


ALIO_HOST = "job.alio.go.kr"
ALIO_DOWNLOAD_HOST = "www.alio.go.kr"
ALIO_ALLOWED_HOSTS = {ALIO_HOST, ALIO_DOWNLOAD_HOST}
ALIO_LIST_PATH = "/recruit.do"
ALIO_DETAIL_PATH = "/recruitview.do"
ALIO_DOWNLOAD_PATHS = {
    "/download.json",
    "/download.do",
    "/fileDownload.do",
    "/recruitDownload.do",
    "/download/download.json",
    "/download/download.do",
}
ALIO_MAX_HTML_BYTES = 1 * 1024 * 1024
ALIO_MAX_POSTINGS = 30
ALIO_MAX_ATTACHMENTS = 20
ALIO_MAX_INSPECTION_ATTACHMENTS = 100
ALIO_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ALIO_MAX_REDIRECTS = 3
ALIO_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0)
ALIO_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
ALIO_SUPPORTED_SUFFIXES = {
    ".pdf",
    ".hwp",
    ".hwpx",
    ".docx",
    ".txt",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
ALIO_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
ALIO_DOCUMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-hwp",
    "application/haansofthwp",
    "application/vnd.hancom.hwp",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/webp",
}


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


@dataclass(frozen=True)
class AlioAttachmentDownload:
    """One validated, bounded ALIO attachment download."""

    url: str
    filename: str
    content_type: str
    data: bytes


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
    host = str(parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or host not in ALIO_ALLOWED_HOSTS:
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
    if host == ALIO_HOST and path == ALIO_LIST_PATH and allow_list:
        return value
    if path in ALIO_DOWNLOAD_PATHS:
        file_no = parse_qs(parsed.query).get("fileNo", [""])[0]
        if re.fullmatch(r"\d{1,20}", file_no):
            return f"https://{host}{path}?fileNo={file_no}"
        raise AlioIngestionError("attachment_id_required", "ALIO 첨부파일 URL에 유효한 fileNo가 필요합니다.")
    if host != ALIO_HOST or path != ALIO_DETAIL_PATH:
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
        with client_factory(
            timeout=ALIO_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _ in range(ALIO_MAX_REDIRECTS + 1):
                with client.stream("GET", current, headers=headers) as response:
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


def _attachment_kind(name: str, section: str = "") -> str:
    """Classify an attachment, preferring ALIO's table-row heading.

    Real pages frequently use opaque names such as ``붙임2.hwp``.  The
    surrounding ``<th>공고문</th>``/``<th>직무기술서</th>`` heading is therefore
    stronger evidence than the filename and must be considered first.
    """

    key = re.sub(r"\s+", "", str(name or "")).lower()
    if any(term in key for term in ("직무기술서", "직무설명", "ncs", "직무설명자료")):
        return "job_description"
    if any(term in key for term in ("공고문", "채용공고", "모집공고", "채용계획")):
        return "notice"
    section_key = re.sub(r"\s+", "", str(section or "")).lower()
    if any(term in section_key for term in ("직무기술서", "직무설명", "ncs")):
        return "job_description"
    if any(term in section_key for term in ("공고문", "채용공고", "모집공고")):
        return "notice"
    return "other"


def _extract_attachments(text: str, source_url: str, *, limit: int = ALIO_MAX_ATTACHMENTS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    anchor_pattern = re.compile(
        r'<a[^>]+href=["\'](?P<href>[^"\']*(?:download\.(?:json|do)|fileDownload\.do|recruitDownload\.do)[?][^"\']*fileNo=\d+[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def append_from_area(area: str, section: str = "") -> None:
        for match in anchor_pattern.finditer(area):
            if len(rows) >= limit:
                return
            href = html.unescape(match.group("href"))
            absolute = _validate_alio_url(urljoin(source_url, href), allow_list=False)
            if absolute in seen:
                continue
            seen.add(absolute)
            file_no = parse_qs(urlsplit(absolute).query).get("fileNo", [""])[0]
            name = _clean_html_text(match.group("label")) or f"ALIO 첨부파일 {len(rows) + 1}"
            kind = _attachment_kind(name, section)
            rows.append(
                {
                    "attachment_id": file_no or str(len(rows) + 1),
                    "file_no": file_no,
                    "name": name[:160],
                    "url": absolute,
                    "kind": kind,
                    "section": _clean_html_text(section)[:80],
                    # Importing is automatic, but document interpretation and
                    # NCS confirmation still require the existing human gate.
                    "selection_required": True,
                    "auto_import_eligible": kind in {"notice", "job_description"},
                }
            )

    # Preserve the semantic row heading.  It is the only reliable signal when
    # an attachment is simply called "붙임1" or "첨부파일".
    for row_match in re.finditer(r"<tr\b[^>]*>(?P<body>.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL):
        body = row_match.group("body")
        if not anchor_pattern.search(body):
            continue
        heading = re.search(r"<th\b[^>]*>(?P<label>.*?)</th>", body, flags=re.IGNORECASE | re.DOTALL)
        append_from_area(body, heading.group("label") if heading else "")
        if len(rows) >= limit:
            return rows

    # Some historical templates render attachment anchors outside a table.
    append_from_area(text)
    return rows


def _extract_labeled_values(text: str) -> dict[str, str]:
    """Extract useful, non-file detail-page fields for notice context."""

    fields: dict[str, str] = {}
    for match in re.finditer(
        r"<tr\b[^>]*>(?P<body>.*?)</tr>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body = match.group("body")
        cells = re.findall(r"<t(?P<tag>[hd])\b[^>]*>(?P<value>.*?)</t[hd]>", body, flags=re.IGNORECASE | re.DOTALL)
        label = ""
        for tag, value in cells:
            cleaned = _clean_html_text(value)
            if not cleaned:
                continue
            if tag.lower() == "h":
                label = cleaned
            elif label and label not in fields:
                fields[label[:80]] = cleaned[:4000]
                label = ""
    return fields


def _extract_inline_notice(text: str) -> dict[str, str]:
    """Extract the public detail-page notice sections as supplemental text."""

    output: dict[str, str] = {}
    tab = re.search(
        r'<div[^>]+id=["\']tab-1["\'][^>]*>(?P<body>.*?)</div>\s*<div[^>]+id=["\']tab-2["\']',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    area = tab.group("body") if tab else text
    section_pattern = re.compile(
        r"<h4\b[^>]*>(?P<label>.*?)</h4>\s*(?P<body>.*?)(?=<h4\b|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in section_pattern.finditer(area):
        label = _clean_html_text(match.group("label"))
        value = _clean_html_text(match.group("body"))
        if label and value:
            output[label[:80]] = value[:12000]
    return output


def _filename_from_content_disposition(value: str) -> str:
    header = str(value or "")
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, flags=re.IGNORECASE)
    if encoded:
        return unquote(encoded.group(1)).strip().strip('"')
    plain = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;]+))', header, flags=re.IGNORECASE)
    if plain:
        return unquote((plain.group(1) or plain.group(2) or "").strip()).strip('"')
    return ""


def _safe_attachment_filename(value: str, fallback: str = "alio_attachment.bin") -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", name)
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
    return (name[:180] or fallback)[:180]


def _detected_attachment_suffix(data: bytes, content_type: str) -> str:
    prefix = bytes(data[:32])
    if prefix.startswith(b"%PDF-"):
        return ".pdf"
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return ".zip"
    if prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".hwp"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if prefix.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/x-hwp": ".hwp",
        "application/haansofthwp": ".hwp",
        "application/vnd.hancom.hwp": ".hwp",
        "text/plain": ".txt",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(content_type, "")


def _validated_attachment_filename(
    *,
    disposition_name: str,
    expected_name: str,
    content_type: str,
    data: bytes,
) -> str:
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    html_prefix = bytes(data[:512]).lstrip().lower()
    if normalized_type in ALIO_HTML_CONTENT_TYPES or html_prefix.startswith(
        (b"<!doctype html", b"<html", b"<head", b"<body")
    ):
        raise AlioIngestionError(
            "attachment_type_not_supported",
            "ALIO가 문서 대신 HTML 응답을 반환했습니다.",
        )

    upstream_name = _safe_attachment_filename(disposition_name, fallback="")
    upstream_suffix = (
        "." + upstream_name.rsplit(".", 1)[-1].lower()
        if "." in upstream_name
        else ""
    )
    if upstream_suffix and upstream_suffix not in ALIO_SUPPORTED_SUFFIXES:
        raise AlioIngestionError(
            "attachment_type_not_supported",
            "지원하지 않는 ALIO 첨부파일 형식입니다.",
        )

    detected_suffix = _detected_attachment_suffix(data, normalized_type)
    if not upstream_suffix and (
        normalized_type not in ALIO_DOCUMENT_CONTENT_TYPES or not detected_suffix
    ):
        raise AlioIngestionError(
            "attachment_type_not_supported",
            "ALIO 응답에서 지원 문서 형식을 확인할 수 없습니다.",
        )

    if upstream_suffix:
        filename = upstream_name
    else:
        requested = _safe_attachment_filename(expected_name, fallback="alio_attachment")
        requested_stem = requested.rsplit(".", 1)[0] if "." in requested else requested
        # ZIP is the transport format for DOCX/HWPX. The caller may retain one
        # of those safe container labels, but it cannot make HTML/octet-stream
        # content pass the upstream type checks above.
        requested_suffix = (
            "." + requested.rsplit(".", 1)[-1].lower()
            if "." in requested
            else ""
        )
        if detected_suffix == ".zip" and requested_suffix in {".zip", ".docx", ".hwpx"}:
            detected_suffix = requested_suffix
        filename = _safe_attachment_filename(
            f"{requested_stem or 'alio_attachment'}{detected_suffix}"
        )
    return filename


def download_alio_attachment(
    raw_url: str,
    *,
    expected_name: str = "",
    max_bytes: int = ALIO_MAX_ATTACHMENT_BYTES,
    client_factory: Callable[..., Any] = httpx.Client,
) -> AlioAttachmentDownload:
    """Download one allowlisted ALIO attachment with redirect/size guards."""

    current = _validate_alio_url(raw_url, allow_list=False)
    max_bytes = max(1, min(int(max_bytes), ALIO_MAX_ATTACHMENT_BYTES))
    headers = {
        "Accept": "application/octet-stream,application/pdf,application/zip,*/*",
        "User-Agent": "NCScope-public-document-import/1.0",
        "Referer": f"https://{ALIO_HOST}{ALIO_LIST_PATH}",
    }
    try:
        with client_factory(
            timeout=ALIO_DOWNLOAD_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _ in range(ALIO_MAX_REDIRECTS + 1):
                with client.stream("GET", current, headers=headers) as response:
                    if 300 <= int(response.status_code) < 400:
                        location = str(response.headers.get("location", "")).strip()
                        if not location:
                            raise AlioIngestionError("redirect_invalid", "ALIO 첨부파일 redirect 위치가 비어 있습니다.")
                        current = _validate_alio_url(urljoin(current, location), allow_list=False)
                        continue
                    response.raise_for_status()
                    content_length = str(response.headers.get("content-length", "")).strip()
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise AlioIngestionError("attachment_too_large", "ALIO 첨부파일이 허용된 크기를 초과했습니다.")
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        chunk_bytes = bytes(chunk or b"")
                        size += len(chunk_bytes)
                        if size > max_bytes:
                            raise AlioIngestionError("attachment_too_large", "ALIO 첨부파일이 허용된 크기를 초과했습니다.")
                        chunks.append(chunk_bytes)
                    data = b"".join(chunks)
                    if not data:
                        raise AlioIngestionError("attachment_empty", "ALIO 첨부파일이 비어 있습니다.")
                    content_type = str(
                        response.headers.get("content-type", "application/octet-stream")
                    ).split(";", 1)[0]
                    disposition_name = _filename_from_content_disposition(
                        str(response.headers.get("content-disposition", ""))
                    )
                    filename = _validated_attachment_filename(
                        disposition_name=disposition_name,
                        expected_name=expected_name,
                        content_type=content_type,
                        data=data,
                    )
                    return AlioAttachmentDownload(
                        url=current,
                        filename=filename,
                        content_type=content_type,
                        data=data,
                    )
    except AlioIngestionError:
        raise
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise AlioIngestionError("upstream_timeout", "ALIO 첨부파일 응답 시간이 초과되었습니다.", retryable=True) from exc
    except httpx.HTTPStatusError as exc:
        status = int(exc.response.status_code)
        raise AlioIngestionError(
            "upstream_http_error",
            f"ALIO 첨부파일을 가져오지 못했습니다(HTTP {status}).",
            retryable=status == 429 or status >= 500,
        ) from exc
    except httpx.RequestError as exc:
        raise AlioIngestionError("upstream_unavailable", "ALIO 첨부파일에 연결할 수 없습니다.", retryable=True) from exc
    raise AlioIngestionError("redirect_limit", "ALIO 첨부파일 redirect 횟수가 허용 범위를 초과했습니다.")


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
    attachments = _extract_attachments(
        page.text,
        page.url,
        limit=ALIO_MAX_INSPECTION_ATTACHMENTS,
    )
    detail_fields = _extract_labeled_values(page.text)
    inline_notice = _extract_inline_notice(page.text)
    return {
        "status": "human_review_required",
        "source_url": page.url,
        "source_kind": "posting_detail",
        "posting": {
            "posting_id": idx,
            "organization": headings[0] if headings else "",
            "title": _clean_html_text(title_match.group(1)) if title_match else "",
        },
        "detail_fields": detail_fields,
        "inline_notice": inline_notice,
        "selection": {
            "required": True,
            "kind": "attachments",
            "max_selected": max(2, len([item for item in attachments if item["kind"] in {"notice", "job_description"}])),
            "required_kinds": ["notice", "job_description"],
        },
        "postings": [],
        "attachments": attachments,
        "message": "같은 공고의 공고문과 직무기술서를 자동 가져오거나 직접 선택한 뒤 일반 업로드 검토를 진행하세요.",
    }
