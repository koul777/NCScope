from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import mimetypes
import os
import re
import ssl
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ncs_mcp_client import (  # noqa: E402
    NcsMcpError,
    get_ksa_by_units,
    search_units_by_detail,
    use_ncs_mcp_request_session,
)


SOURCE_BASE_URL = "https://www.ncs.go.kr"
SOURCE_ALLOWED_HOSTS = frozenset({"www.ncs.go.kr", "ncs.go.kr"})
LIST_PATH = "/blind/bl04/RecrtNotifList.do"
DETAIL_PATH = "/blind/bl04/RecrtNotifDetail.do"
DETAIL_SOURCE_PATH = "/blind/bl04/selectJdsptList.do"
DOWNLOAD_PATH = "/common/file/downloadFile.do"
DEFAULT_PARSE_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_OUTPUT_DIR = Path("tmp") / "ncs_recruitment_live"
DEFAULT_PRIVATE_GOLD_SOURCE_DIR = Path("tmp") / "ncs_recruitment_goldset" / "source_documents"
DEFAULT_DIGEST_KEY_ENV = "NCS_RECRUITMENT_LIVE_DIGEST_KEY"
DEFAULT_MAX_POSTINGS = 20
DEFAULT_PAGE_LIMIT = 8
DEFAULT_MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
DEFAULT_DELAY_SECONDS = 0.15
DEFAULT_PARSE_TIMEOUT_SECONDS = 180.0
DEFAULT_SOURCE_TIMEOUT_SECONDS = 60.0
DEFAULT_NCS_UNIT_LIMIT = 200
DEFAULT_MAX_KSA_FACTORS_PER_UNIT = 1
DEFAULT_MIN_POSTING_PRECISION_PCT = 90.0
DEFAULT_MIN_POSTING_RECALL_PCT = 80.0
DEFAULT_MIN_POSTING_EXACT_PCT = 50.0
DEFAULT_MIN_COORDINATE_SHAPE_PCT = 100.0
DEFAULT_MIN_KSA_AVAILABILITY_PCT = 100.0
MAX_REDIRECTS = 3
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60.0
SUPPORTED_SUFFIXES = frozenset({".pdf", ".hwp", ".hwpx", ".docx", ".txt"})
ZIP_CONTAINER_SUFFIXES = frozenset({".docx", ".hwpx"})
MAX_PACKAGE_MEMBERS = 512
MAX_PACKAGE_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_MEMBER_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_COMPRESSION_RATIO = 200.0
JD_TOKEN = "\uc9c1\ubb34\uae30\uc220\uc11c"
NOTICE_TOKEN = "\uacf5\uace0\ubb38"
LIST_ITEM_RE = re.compile(
    r'onclick="fn_view\(\'(?P<recrt_no>\d+)\'\)"[^>]*title="(?P<title>[^"]*)"',
    re.IGNORECASE,
)
ATTACHMENT_RE = re.compile(
    r"""gfn_file_downloadFile\('(?P<sys_dstin_cd>[^']+)','(?P<file_mstky>[^']+)','(?P<filedetl_seq>[^']+)'\).*?>(?P<label>.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)


class ConfigurationError(RuntimeError):
    pass


class SourceFetchError(RuntimeError):
    pass


def validate_private_gold_source_dir(path: Path) -> Path:
    """Keep raw evaluation documents in a local Git-ignored tree only."""

    resolved = path.resolve()
    allowed_roots = ((ROOT / "tmp").resolve(), (ROOT / ".tmp").resolve())
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        raise ConfigurationError(
            "private gold source capture must be written below tmp/ or .tmp/"
        )
    return resolved


def write_private_gold_source_index(rows: list[dict[str, str]], output_dir: Path) -> Path:
    output_dir = validate_private_gold_source_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "source_index.local.csv"
    fieldnames = ["case_id", "local_document_path", "document_sha256"]
    with index_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["case_id"]):
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return index_path


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Co")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalized_label_key(value: Any) -> str:
    return re.sub(
        r"[^0-9a-z\u3131-\u318e\uac00-\ud7a3]+",
        "",
        normalize_text(value).casefold(),
    )


def canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite numbers are not valid canonical values")
        return value
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            normalize_text(str(key)): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: normalize_text(str(pair[0])))
        }
    return normalize_text(str(value))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class PrivacyDigester:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ConfigurationError("digest key must be at least 32 bytes")
        self._key = bytes(key)

    def bytes(self, domain: str, payload: bytes) -> str:
        return hmac.new(
            self._key,
            normalize_text(domain).encode("utf-8") + b"\x00" + bytes(payload),
            hashlib.sha256,
        ).hexdigest()

    def text(self, domain: str, value: Any) -> str:
        return self.bytes(domain, normalize_text(value).encode("utf-8"))


@dataclass(frozen=True)
class JsonResult:
    status: str
    status_code: int
    payload: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and isinstance(self.payload, dict)


@dataclass(frozen=True)
class Posting:
    recrt_no: str
    title: str


@dataclass(frozen=True)
class AttachmentRef:
    posting: Posting
    sys_dstin_cd: str
    file_mstky: str
    filedetl_seq: str
    label: str
    ordinal: int


@dataclass(frozen=True)
class AttachmentDownload:
    upload_filename: str
    content_type: str
    data: bytes


def pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def load_digest_key_from_env(name: str) -> bytes:
    raw = str(os.environ.get(name, "") or "").encode("utf-8")
    if not raw:
        raise ConfigurationError(f"set {name} to a private digest key with at least 32 bytes")
    if len(raw) < 32:
        raise ConfigurationError(f"{name} must be at least 32 bytes")
    return raw


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def normalize_base_url(value: str) -> str:
    split = urlsplit(str(value or "").strip())
    if split.scheme not in {"http", "https"} or not split.hostname:
        raise ConfigurationError("base URL must use http or https")
    if split.username or split.password or split.query or split.fragment:
        raise ConfigurationError("base URL must not contain credentials, query, or fragment")
    return urlunsplit((split.scheme, split.netloc, split.path.rstrip("/"), "", ""))


def validate_parse_endpoint(base_url: str, allow_remote_parse_upload: bool) -> str:
    normalized = normalize_base_url(base_url)
    split = urlsplit(normalized)
    if is_loopback_host(split.hostname or ""):
        return normalized
    if split.scheme != "https":
        raise ConfigurationError("non-loopback parse endpoint requires HTTPS")
    if not allow_remote_parse_upload:
        raise ConfigurationError(
            "non-loopback parse endpoint uploads document bytes; pass "
            "--allow-remote-parse-upload only after explicit approval"
        )
    return normalized


def validate_source_url(value: str) -> str:
    split = urlsplit(str(value or "").strip())
    if split.scheme != "https":
        raise ConfigurationError("source URLs must use HTTPS")
    if (split.hostname or "").casefold() not in SOURCE_ALLOWED_HOSTS:
        raise ConfigurationError("source URL host is not allowlisted")
    if split.username or split.password or split.fragment:
        raise ConfigurationError("source URL must not contain credentials or fragments")
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))


def same_origin(left: str, right: str) -> bool:
    left_split = urlsplit(left)
    right_split = urlsplit(right)
    left_port = left_split.port or (443 if left_split.scheme == "https" else 80)
    right_port = right_split.port or (443 if right_split.scheme == "https" else 80)
    return (
        left_split.scheme.casefold(),
        (left_split.hostname or "").casefold(),
        left_port,
    ) == (
        right_split.scheme.casefold(),
        (right_split.hostname or "").casefold(),
        right_port,
    )


def build_source_ssl_context() -> ssl.SSLContext:
    """Keep certificate verification while supporting the fixed NCS TLS endpoint."""

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError as exc:
        raise ConfigurationError("unable to configure NCS TLS compatibility") from exc
    return context


def decode_html(data: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def clean_html_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def filename_from_disposition(value: str) -> str:
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", str(value or ""), flags=re.IGNORECASE)
    if encoded:
        from urllib.parse import unquote

        return unquote(encoded.group(1))
    plain = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;]+))', str(value or ""), flags=re.IGNORECASE)
    if plain:
        from urllib.parse import unquote

        return unquote((plain.group(1) or plain.group(2) or "").strip().strip('"'))
    return ""


def safe_upload_filename(case_id: str, suffix: str) -> str:
    normalized_suffix = str(suffix or "").lower()
    if normalized_suffix not in SUPPORTED_SUFFIXES:
        raise ConfigurationError(f"unsupported document suffix: {normalized_suffix or '<empty>'}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(case_id or "")):
        raise ConfigurationError("case_id must be a 64-character lowercase hex digest")
    return f"case-{case_id[:24]}{normalized_suffix}"


def safe_suffix_from_headers(data: bytes, content_type: str, filename: str) -> str:
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    prefix = bytes(data[:32])
    magic_suffix = ""
    if prefix.startswith(b"%PDF-"):
        magic_suffix = ".pdf"
    elif prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        magic_suffix = ".zip"
    elif prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        magic_suffix = ".hwp"
    type_suffix = {
        "application/pdf": ".pdf",
        "application/x-hwp": ".hwp",
        "application/haansofthwp": ".hwp",
        "application/vnd.hancom.hwp": ".hwp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "text/plain": ".txt",
    }.get(normalized_type, "")
    name_suffix = Path(str(filename or "").strip()).suffix.lower()
    if magic_suffix == ".zip" and name_suffix in ZIP_CONTAINER_SUFFIXES:
        return name_suffix
    if magic_suffix in {".pdf", ".hwp"}:
        if name_suffix in SUPPORTED_SUFFIXES and name_suffix != magic_suffix:
            raise SourceFetchError("attachment_type_mismatch")
        return magic_suffix
    if magic_suffix == ".zip":
        raise SourceFetchError("zip_attachment_not_allowed")
    if name_suffix == ".txt" or type_suffix == ".txt":
        if b"\x00" in data[:4096]:
            raise SourceFetchError("invalid_text_attachment")
        return ".txt"
    raise SourceFetchError("unsupported_attachment_type")


def validate_package_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SourceFetchError("invalid_package_archive") from exc
    if not members or len(members) > MAX_PACKAGE_MEMBERS:
        raise SourceFetchError("unsafe_package_member_count")
    total_uncompressed = 0
    for member in members:
        normalized_name = str(member.filename or "").replace("\\", "/")
        parts = [part for part in normalized_name.split("/") if part not in {"", "."}]
        if not normalized_name or normalized_name.startswith("/") or ".." in parts:
            raise SourceFetchError("unsafe_package_member_path")
        if member.flag_bits & 0x1:
            raise SourceFetchError("encrypted_package_member")
        if member.is_dir():
            continue
        if member.file_size < 0 or member.file_size > MAX_PACKAGE_MEMBER_BYTES:
            raise SourceFetchError("unsafe_package_member_size")
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise SourceFetchError("unsafe_package_uncompressed_size")
        if (
            member.file_size > 1024 * 1024
            and member.file_size / max(1, member.compress_size)
            > MAX_PACKAGE_COMPRESSION_RATIO
        ):
            raise SourceFetchError("unsafe_package_compression_ratio")


def looks_like_html(data: bytes, content_type: str) -> bool:
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    prefix = bytes(data[:512]).lstrip().lower()
    if normalized_type in {"text/html", "application/xhtml+xml"}:
        return True
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def exact_detail_sets(fields: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    codes: set[str] = set()
    for row in fields.get("ncs_detail_mapping_states") or []:
        if not isinstance(row, dict):
            continue
        if normalize_text(row.get("mappingState")) != "official_current_exact":
            continue
        for value in row.get("officialDetailNames") or []:
            text = normalize_text(value)
            if text:
                names.add(text)
        for value in row.get("officialDetailCodes") or []:
            text = normalize_text(value)
            if text:
                codes.add(text)
    return names, codes


def exact_ability_rows(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in fields.get("ability_unit_mapping_states") or []:
        if not isinstance(row, dict):
            continue
        if not normalize_text(row.get("mappingState")).startswith("official_exact_"):
            continue
        if not normalize_text(row.get("sourceName")):
            continue
        rows.append(row)
    return rows


def positioned_table_ability_labels(fields: Mapping[str, Any]) -> set[str]:
    labels: set[str] = set()
    for item in fields.get("positioned_items") or []:
        if not isinstance(item, dict):
            continue
        if normalize_text(item.get("section")) != "ability_units":
            continue
        text = normalize_text(item.get("text"))
        if text:
            labels.add(text)
    return labels


def _nonnegative_integer(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _valid_coordinate_cell(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    row = _nonnegative_integer(value.get("row"))
    column = _nonnegative_integer(value.get("column"))
    try:
        row_span = int(value.get("row_span"))
        column_span = int(value.get("column_span"))
    except (TypeError, ValueError):
        return False
    return row is not None and column is not None and row_span >= 1 and column_span >= 1


def positioned_ability_coordinate_counts(fields: Mapping[str, Any]) -> tuple[int, int]:
    total = 0
    valid = 0
    for item in fields.get("positioned_items") or []:
        if not isinstance(item, dict):
            continue
        if normalize_text(item.get("section")) != "ability_units":
            continue
        if not normalize_text(item.get("text")):
            continue
        total += 1
        if (
            _nonnegative_integer(item.get("page")) is not None
            and _nonnegative_integer(item.get("table_index")) is not None
            and _valid_coordinate_cell(item.get("label_cell"))
            and _valid_coordinate_cell(item.get("value_cell"))
        ):
            valid += 1
    return total, valid


def final_exact_ability_labels(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {text for row in rows if (text := normalize_text(row.get("sourceName")))}


def final_exact_unit_codes(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for row in rows:
        for value in row.get("resolvedUnitCodes") or []:
            code = normalize_text(value)
            if code:
                codes.add(code)
    return codes


def detail_status_reason(fields: Mapping[str, Any], observed_exact_names: set[str]) -> str:
    if observed_exact_names:
        return "official_current_exact"
    if fields.get("ncs_detail_absence_declared_no_mapping") is True:
        return "declared_no_mapping"
    if normalize_text(fields.get("ncs_detail_source")) == "pdf_table_detail_empty":
        return "pdf_table_detail_empty"
    if fields.get("ncs_detail_candidates"):
        return "detail_candidates_without_exact_current_match"
    return normalize_text(fields.get("ncs_detail_absence_state")) or "no_detail_candidate"


class SourceClient:
    def __init__(
        self,
        *,
        base_url: str = SOURCE_BASE_URL,
        timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.base_url = validate_source_url(base_url)
        self._sleep = sleep_func
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "ncscope-live-benchmark/1.0"},
            transport=transport,
            verify=ssl_context or build_source_ssl_context(),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SourceClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _request(
        self,
        method: str,
        url: str,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        current = validate_source_url(url)
        rate_limit_attempts = 0
        redirect_attempts = 0
        while True:
            request = self._client.build_request(method, current, **kwargs)
            response = self._client.send(request, stream=stream)
            if response.status_code == 429:
                response.close()
                if rate_limit_attempts >= MAX_RATE_LIMIT_RETRIES:
                    raise SourceFetchError("http_429")
                rate_limit_attempts += 1
                try:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    retry_after = 1.0
                self._sleep(max(0.0, min(MAX_RETRY_AFTER_SECONDS, retry_after)))
                continue
            if response.is_redirect:
                location = normalize_text(response.headers.get("location"))
                response.close()
                if redirect_attempts >= MAX_REDIRECTS:
                    raise SourceFetchError("redirect_limit_exceeded")
                if not location:
                    raise SourceFetchError("redirect_without_location")
                redirected = validate_source_url(urljoin(current, location))
                if not same_origin(current, redirected):
                    raise SourceFetchError("cross_origin_redirect_rejected")
                current = redirected
                redirect_attempts += 1
                continue
            try:
                response.raise_for_status()
                if not stream:
                    response.read()
            except Exception:
                response.close()
                raise
            return response

    def list_postings(self, *, max_postings: int, max_pages: int) -> list[Posting]:
        output: list[Posting] = []
        seen: set[str] = set()
        for page_index in range(max(1, int(max_pages))):
            query = "" if page_index == 0 else f"?pageIndex={page_index}"
            response = self._request("GET", f"{self.base_url}{LIST_PATH}{query}")
            page_items = parse_postings_from_list_html(decode_html(response.content))
            added = 0
            for posting in page_items:
                if posting.recrt_no in seen:
                    continue
                seen.add(posting.recrt_no)
                output.append(posting)
                added += 1
                if len(output) >= max_postings:
                    return output
            if added == 0:
                break
        return output

    def fetch_detail_html(self, posting: Posting) -> str:
        response = self._request("GET", f"{self.base_url}{DETAIL_PATH}?recrtNo={posting.recrt_no}")
        return decode_html(response.content)

    def fetch_posting_source_union(self, posting: Posting) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"{self.base_url}{DETAIL_SOURCE_PATH}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=urlencode({"recrtNo": posting.recrt_no}),
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceFetchError("invalid_source_json") from exc
        if not isinstance(payload, dict):
            raise SourceFetchError("invalid_source_json")
        return payload

    def download_attachment(self, attachment: AttachmentRef, *, max_bytes: int, case_id: str) -> AttachmentDownload:
        response = self._request(
            "POST",
            f"{self.base_url}{DOWNLOAD_PATH}",
            stream=True,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=urlencode(
                {
                    "sysDstinCd": attachment.sys_dstin_cd,
                    "fileMstky": attachment.file_mstky,
                    "filedetlSeq": attachment.filedetl_seq,
                }
            ),
        )
        try:
            try:
                content_length = int(response.headers.get("content-length", "0") or 0)
            except (TypeError, ValueError):
                content_length = 0
            if content_length > max_bytes:
                raise SourceFetchError("attachment_too_large")
            size = 0
            chunks: list[bytes] = []
            for chunk in response.iter_bytes():
                payload = bytes(chunk or b"")
                size += len(payload)
                if size > max_bytes:
                    raise SourceFetchError("attachment_too_large")
                chunks.append(payload)
            data = b"".join(chunks)
        finally:
            response.close()
        if not data:
            raise SourceFetchError("attachment_empty")
        content_type = normalize_text(response.headers.get("content-type")).split(";", 1)[0].lower()
        if looks_like_html(data, content_type):
            raise SourceFetchError("attachment_html_response")
        display_name = filename_from_disposition(response.headers.get("content-disposition", "")) or attachment.label
        suffix = safe_suffix_from_headers(data, content_type, display_name)
        if suffix in ZIP_CONTAINER_SUFFIXES:
            validate_package_archive(data)
        return AttachmentDownload(
            upload_filename=safe_upload_filename(case_id, suffix),
            content_type=content_type or (mimetypes.guess_type(f"x{suffix}")[0] or "application/octet-stream"),
            data=data,
        )


class ParseReviewClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PARSE_BASE_URL,
        timeout_seconds: float = DEFAULT_PARSE_TIMEOUT_SECONDS,
        allow_remote_parse_upload: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = validate_parse_endpoint(base_url, allow_remote_parse_upload)
        self._sleep = sleep_func
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "ncscope-live-benchmark/1.0"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ParseReviewClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def parse_review(self, upload: AttachmentDownload) -> JsonResult:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.post(
                    "/api/jd/parse-review",
                    files={"jd_file": (upload.upload_filename, upload.data, upload.content_type)},
                )
            except httpx.TimeoutException:
                return JsonResult("timeout", 0)
            except httpx.TransportError:
                return JsonResult("transport_error", 0)
            if response.is_redirect:
                return JsonResult("redirect_rejected", response.status_code)
            if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    retry_after = 1.0
                self._sleep(max(0.0, min(MAX_RETRY_AFTER_SECONDS, retry_after)))
                continue
            if response.status_code < 200 or response.status_code >= 300:
                return JsonResult(f"http_{response.status_code}", response.status_code)
            try:
                payload = response.json()
            except (ValueError, UnicodeError):
                return JsonResult("invalid_json", response.status_code)
            if not isinstance(payload, dict):
                return JsonResult("invalid_envelope", response.status_code)
            return JsonResult("ok", response.status_code, payload)
        return JsonResult("http_429", 429)


def parse_postings_from_list_html(text: str) -> list[Posting]:
    seen: set[str] = set()
    output: list[Posting] = []
    for match in LIST_ITEM_RE.finditer(text):
        recrt_no = normalize_text(match.group("recrt_no"))
        if not recrt_no or recrt_no in seen:
            continue
        seen.add(recrt_no)
        output.append(Posting(recrt_no=recrt_no, title=clean_html_text(match.group("title"))))
    return output


def parse_jd_attachments(detail_html: str, posting: Posting) -> list[AttachmentRef]:
    output: list[AttachmentRef] = []
    seen: set[tuple[str, str, str]] = set()
    for match in ATTACHMENT_RE.finditer(detail_html):
        label = clean_html_text(match.group("label"))
        normalized = label.replace(" ", "")
        if JD_TOKEN not in normalized or NOTICE_TOKEN in normalized:
            continue
        key = (
            normalize_text(match.group("sys_dstin_cd")),
            normalize_text(match.group("file_mstky")),
            normalize_text(match.group("filedetl_seq")),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(
            AttachmentRef(
                posting=posting,
                sys_dstin_cd=key[0],
                file_mstky=key[1],
                filedetl_seq=key[2],
                label=label,
                ordinal=len(output) + 1,
            )
        )
    return output


def parse_posting_source_union(payload: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    codes: set[str] = set()
    for row in payload.get("jdsptList") or []:
        if not isinstance(row, dict):
            continue
        name = normalize_text(row.get("ncsSubdCdnm") or row.get("ncsSubdCdNm"))
        code_parts = [
            normalize_text(row.get(key))
            for key in ("ncsLclasCd", "ncsMclasCd", "ncsSclasCd", "ncsSubdCd")
        ]
        if name:
            names.add(name)
        if all(re.fullmatch(r"\d{2}", part) for part in code_parts):
            codes.add("".join(code_parts))
    return names, codes


def posting_mismatch_diagnostic(
    *,
    source_names: set[str],
    source_codes: set[str],
    observed_names: set[str],
    observed_codes: set[str],
    accuracy_eligible: bool,
) -> str:
    """Describe set relations without claiming a causal parser diagnosis."""
    if not accuracy_eligible:
        return "excluded_missing_source_ground_truth"
    if source_names == observed_names and source_codes == observed_codes:
        return "exact"
    if not observed_names and not observed_codes:
        return "no_observed_detail_review_required"
    has_missing = bool(
        (source_names - observed_names)
        or (source_codes - observed_codes)
    )
    has_extra = bool(
        (observed_names - source_names)
        or (observed_codes - source_codes)
    )
    if has_missing and not has_extra:
        return "source_union_superset_possible"
    if has_extra and not has_missing:
        return "document_extra_not_in_source_union"
    if has_missing and has_extra:
        return "cross_mismatch_review_required"
    return "code_set_mismatch_review_required"


def aggregate_metrics(
    cases: list[dict[str, Any]],
    posting_evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    posting_groups: dict[str, dict[str, Any]] = {}
    posting_tp = posting_fp = posting_fn = posting_exact = 0
    attachment_tp = attachment_fp = attachment_fn = attachment_exact = 0
    coordinate_contract_num = coordinate_contract_den = 0
    table_label_link_num = table_label_link_den = 0
    table_provenance_num = table_provenance_den = 0
    posting_query_total = posting_query_available = 0
    posting_mismatch_counts: dict[str, int] = {}
    document_status_counts: dict[str, int] = {}
    document_status_reason_counts: dict[str, int] = {}
    declared_no_mapping_document_count = 0

    for case in cases:
        document_status = normalize_text(case.get("status")) or "not_recorded"
        document_status_reason = normalize_text(case.get("status_reason")) or "not_recorded"
        document_status_counts[document_status] = document_status_counts.get(document_status, 0) + 1
        document_status_reason_counts[document_status_reason] = (
            document_status_reason_counts.get(document_status_reason, 0) + 1
        )
        declared_no_mapping_document_count += int(bool(case.get("declared_no_mapping")))
        posting_id = normalize_text(case.get("posting_id"))
        source_names = set(case.get("source_detail_name_ids") or [])
        source_codes = set(case.get("source_detail_code_ids") or [])
        observed_names = set(case.get("observed_detail_name_ids") or [])
        observed_codes = set(case.get("observed_detail_code_ids") or [])

        attachment_tp += len(source_names & observed_names)
        attachment_fp += len(observed_names - source_names)
        attachment_fn += len(source_names - observed_names)
        attachment_exact += int(source_names == observed_names and source_codes == observed_codes)

        coordinate_contract_num += int(
            case.get("positioned_coordinate_contract_valid_count") or 0
        )
        coordinate_contract_den += int(
            case.get("positioned_coordinate_contract_total") or 0
        )
        table_label_link_num += int(
            case.get("positioned_table_label_to_final_exact_match_count") or 0
        )
        table_label_link_den += int(case.get("positioned_table_label_count") or 0)
        table_provenance_num += int(case.get("final_exact_table_provenance_count") or 0)
        table_provenance_den += int(case.get("final_exact_ability_count") or 0)

        if posting_evidence is not None:
            continue
        group = posting_groups.setdefault(
            posting_id,
            {
                "posting_id": posting_id,
                "document_count": 0,
                "source_detail_name_ids": set(),
                "source_detail_code_ids": set(),
                "observed_detail_name_ids": set(),
                "observed_detail_code_ids": set(),
                "query_ids": set(),
                "ksa_probe_unit_count": 0,
                "ksa_available_unit_count": 0,
                "ksa_status": "not_recorded",
                "accuracy_eligible": bool(source_names and source_codes),
            },
        )
        group["document_count"] += 1
        group["source_detail_name_ids"].update(source_names)
        group["source_detail_code_ids"].update(source_codes)
        group["observed_detail_name_ids"].update(observed_names)
        group["observed_detail_code_ids"].update(observed_codes)
        group["query_ids"].update(case.get("posting_query_ids") or [])
        group["ksa_probe_unit_count"] = max(
            int(group["ksa_probe_unit_count"]),
            int(case.get("posting_ksa_probe_unit_count") or 0),
        )
        group["ksa_available_unit_count"] = max(
            int(group["ksa_available_unit_count"]),
            int(case.get("posting_ksa_available_unit_count") or 0),
        )

    if posting_evidence is not None:
        for row in posting_evidence:
            posting_id = normalize_text(row.get("posting_id"))
            if not posting_id:
                continue
            source_names = set(row.get("source_detail_name_ids") or [])
            source_codes = set(row.get("source_detail_code_ids") or [])
            posting_groups[posting_id] = {
                "posting_id": posting_id,
                "document_count": int(row.get("document_count") or 0),
                "source_detail_name_ids": source_names,
                "source_detail_code_ids": source_codes,
                "observed_detail_name_ids": set(
                    row.get("observed_detail_name_ids") or []
                ),
                "observed_detail_code_ids": set(
                    row.get("observed_detail_code_ids") or []
                ),
                "query_ids": set(row.get("query_ids") or []),
                "ksa_probe_unit_count": int(row.get("ksa_probe_unit_count") or 0),
                "ksa_available_unit_count": int(
                    row.get("ksa_available_unit_count") or 0
                ),
                "ksa_status": normalize_text(row.get("ksa_status")) or "not_recorded",
                "accuracy_eligible": bool(
                    row.get("accuracy_eligible", bool(source_names and source_codes))
                ),
            }

    posting_rows: list[dict[str, Any]] = []
    accuracy_eligible_count = 0
    for group in posting_groups.values():
        source_names = set(group["source_detail_name_ids"])
        source_codes = set(group["source_detail_code_ids"])
        observed_names = set(group["observed_detail_name_ids"])
        observed_codes = set(group["observed_detail_code_ids"])

        accuracy_eligible = bool(group.get("accuracy_eligible"))
        if accuracy_eligible:
            accuracy_eligible_count += 1
            posting_tp += len(source_names & observed_names)
            posting_fp += len(observed_names - source_names)
            posting_fn += len(source_names - observed_names)
            posting_exact += int(
                source_names == observed_names and source_codes == observed_codes
            )

        posting_query_total += int(group["ksa_probe_unit_count"])
        posting_query_available += int(group["ksa_available_unit_count"])

        mismatch_diagnostic = posting_mismatch_diagnostic(
            source_names=source_names,
            source_codes=source_codes,
            observed_names=observed_names,
            observed_codes=observed_codes,
            accuracy_eligible=accuracy_eligible,
        )
        posting_mismatch_counts[mismatch_diagnostic] = (
            posting_mismatch_counts.get(mismatch_diagnostic, 0) + 1
        )

        posting_rows.append(
            {
                "posting_id": group["posting_id"],
                "document_count": int(group["document_count"]),
                "source_detail_count": len(source_names),
                "observed_detail_count": len(observed_names),
                "missing_detail_count": len(source_names - observed_names),
                "extra_detail_count": len(observed_names - source_names),
                "query_id_count": len(group["query_ids"]),
                "ksa_probe_unit_count": int(group["ksa_probe_unit_count"]),
                "ksa_available_unit_count": int(group["ksa_available_unit_count"]),
                "ksa_status": normalize_text(group.get("ksa_status")) or "not_recorded",
                "exact_match": source_names == observed_names and source_codes == observed_codes,
                "accuracy_eligible": accuracy_eligible,
                "mismatch_diagnostic": mismatch_diagnostic,
            }
        )

    summary = {
        "posting_accuracy_evidence": {
            "precision_pct": pct(posting_tp, posting_tp + posting_fp),
            "recall_pct": pct(posting_tp, posting_tp + posting_fn),
            "exact_pct": pct(posting_exact, accuracy_eligible_count),
            "posting_count": accuracy_eligible_count,
            "all_posting_count": len(posting_groups),
            "excluded_posting_count": len(posting_groups) - accuracy_eligible_count,
            "true_positive": posting_tp,
            "false_positive": posting_fp,
            "false_negative": posting_fn,
            "exact_count": posting_exact,
            "label": "posting_level_union_vs_posting_level_union_accuracy_evidence",
        },
        "posting_mismatch_diagnostics": {
            "counts": dict(sorted(posting_mismatch_counts.items())),
            "posting_count": len(posting_groups),
            "definition": "set_relation_diagnostic_not_causal_accuracy",
        },
        "document_mapping_state_diagnostics": {
            "status_counts": dict(sorted(document_status_counts.items())),
            "status_reason_counts": dict(sorted(document_status_reason_counts.items())),
            "declared_no_mapping_document_count": declared_no_mapping_document_count,
            "document_count": len(cases),
            "definition": "document_mapping_state_diagnostic_not_accuracy",
        },
        "attachment_union_non_accuracy_diagnostic": {
            "precision_pct": pct(attachment_tp, attachment_tp + attachment_fp),
            "recall_pct": pct(attachment_tp, attachment_tp + attachment_fn),
            "exact_pct": pct(attachment_exact, len(cases)),
            "document_count": len(cases),
            "true_positive": attachment_tp,
            "false_positive": attachment_fp,
            "false_negative": attachment_fn,
            "exact_count": attachment_exact,
            "label": "attachment_level_observed_union_vs_posting_level_source_union_non_accuracy_diagnostic",
        },
        "coordinate_metrics": {
            "positioned_coordinate_shape_completeness_pct": pct(
                coordinate_contract_num,
                coordinate_contract_den,
            ),
            "positioned_coordinate_contract_valid_count": coordinate_contract_num,
            "positioned_coordinate_contract_total": coordinate_contract_den,
            "positioned_coordinate_definition": (
                "logical_coordinate_shape_completeness_not_native_page_fidelity"
            ),
            "positioned_table_label_to_final_exact_match_pct": pct(
                table_label_link_num,
                table_label_link_den,
            ),
            "positioned_table_label_to_final_exact_match_count": table_label_link_num,
            "positioned_table_label_count": table_label_link_den,
            "final_unique_exact_ability_table_provenance_pct": pct(
                table_provenance_num,
                table_provenance_den,
            ),
            "final_exact_ability_table_provenance_count": table_provenance_num,
            "final_exact_ability_count": table_provenance_den,
            "final_ability_provenance_definition": (
                "document_unique_normalized_exact_name_non_recall_diagnostic"
            ),
        },
        "ksa_metrics": {
            "probe_unit_count": posting_query_total,
            "available_unit_count": posting_query_available,
            "availability_pct": pct(posting_query_available, posting_query_total),
        },
    }
    posting_rows.sort(key=lambda row: row["posting_id"])
    case_rows = sorted(cases, key=lambda row: (row.get("posting_id", ""), row.get("case_id", "")))
    return summary, posting_rows, case_rows


def _private_id(digester: PrivacyDigester, domain: str, *parts: bytes) -> str:
    return digester.bytes(domain, b"\x00".join(parts))


SAFE_SOURCE_ERROR_CODES = frozenset(
    {
        "attachment_empty",
        "attachment_html_response",
        "attachment_too_large",
        "attachment_type_mismatch",
        "cross_origin_redirect_rejected",
        "encrypted_package_member",
        "http_429",
        "invalid_package_archive",
        "invalid_source_json",
        "invalid_text_attachment",
        "redirect_limit_exceeded",
        "redirect_without_location",
        "unsafe_package_compression_ratio",
        "unsafe_package_member_count",
        "unsafe_package_member_path",
        "unsafe_package_member_size",
        "unsafe_package_uncompressed_size",
        "unsupported_attachment_type",
        "zip_attachment_not_allowed",
    }
)


def safe_exception_status_reason(exc: Exception) -> str:
    if isinstance(exc, SourceFetchError):
        reason = normalize_text(str(exc))
        return reason if reason in SAFE_SOURCE_ERROR_CODES else "source_fetch_error"
    if isinstance(exc, ConfigurationError):
        return "configuration_error"
    if isinstance(exc, NcsMcpError):
        return "ncs_mcp_error"
    if isinstance(exc, httpx.TimeoutException):
        return "source_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "source_http_status_error"
    if isinstance(exc, httpx.TransportError):
        return "source_transport_error"
    return "processing_error"


def _query_posting_ksa(
    *,
    detail_names: Iterable[str],
    digester: PrivacyDigester,
    ncs_unit_limit: int,
    max_ksa_factors_per_unit: int,
    search_units_fn: Callable[[list[str], int], list[dict[str, Any]]],
    get_ksa_fn: Callable[[list[dict[str, Any]], int], list[dict[str, Any]]],
) -> tuple[list[str], int, int]:
    normalized_names = sorted({normalize_text(name) for name in detail_names if normalize_text(name)})
    if not normalized_names:
        return [], 0, 0
    query_ids = sorted(digester.text("query-id", name) for name in normalized_names)
    query_rows: list[dict[str, Any]] = []
    for detail_name in normalized_names:
        rows = search_units_fn([detail_name], max_units=ncs_unit_limit)
        if rows:
            query_rows.extend(row for row in rows if isinstance(row, dict))
    probe_codes = {
        normalize_text(row.get("ncsClCd") or row.get("id"))
        for row in query_rows
        if normalize_text(row.get("ncsClCd") or row.get("id"))
    }
    if not probe_codes:
        return query_ids, 0, 0
    ksa_rows = get_ksa_fn(query_rows, max_factors_per_unit=max_ksa_factors_per_unit)
    available_codes = {
        normalize_text(row.get("ncsClCd") or row.get("unitCode"))
        for row in ksa_rows
        if isinstance(row, dict) and normalize_text(row.get("ncsClCd") or row.get("unitCode"))
    }
    return query_ids, len(probe_codes), len(available_codes & probe_codes)


def run_benchmark(
    *,
    source_client: SourceClient,
    parse_client: ParseReviewClient,
    digester: PrivacyDigester,
    max_postings: int = DEFAULT_MAX_POSTINGS,
    skip_postings: int = 0,
    max_pages: int = DEFAULT_PAGE_LIMIT,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    expected_postings: int | None = None,
    expected_documents: int | None = None,
    ncs_unit_limit: int = DEFAULT_NCS_UNIT_LIMIT,
    max_ksa_factors_per_unit: int = DEFAULT_MAX_KSA_FACTORS_PER_UNIT,
    min_posting_precision_pct: float = DEFAULT_MIN_POSTING_PRECISION_PCT,
    min_posting_recall_pct: float = DEFAULT_MIN_POSTING_RECALL_PCT,
    min_posting_exact_pct: float = DEFAULT_MIN_POSTING_EXACT_PCT,
    min_coordinate_shape_pct: float = DEFAULT_MIN_COORDINATE_SHAPE_PCT,
    min_ksa_availability_pct: float = DEFAULT_MIN_KSA_AVAILABILITY_PCT,
    search_units_fn: Callable[[list[str], int], list[dict[str, Any]]] = search_units_by_detail,
    get_ksa_fn: Callable[[list[dict[str, Any]], int], list[dict[str, Any]]] = get_ksa_by_units,
    sleep_func: Callable[[float], None] = time.sleep,
    private_gold_source_dir: Path | None = None,
) -> dict[str, Any]:
    if max_postings < 1 or max_pages < 1:
        raise ConfigurationError("max_postings and max_pages must be positive")
    if skip_postings < 0:
        raise ConfigurationError("skip_postings must be non-negative")
    if max_attachment_bytes < 1:
        raise ConfigurationError("max_attachment_bytes must be positive")
    if ncs_unit_limit < 1 or max_ksa_factors_per_unit < 1:
        raise ConfigurationError("NCS lookup limits must be positive")
    if delay_seconds < 0:
        raise ConfigurationError("delay_seconds must be non-negative")
    thresholds = {
        "posting_precision_pct": float(min_posting_precision_pct),
        "posting_recall_pct": float(min_posting_recall_pct),
        "posting_exact_pct": float(min_posting_exact_pct),
        "coordinate_shape_pct": float(min_coordinate_shape_pct),
        "ksa_availability_pct": float(min_ksa_availability_pct),
    }
    if any(value < 0.0 or value > 100.0 for value in thresholds.values()):
        raise ConfigurationError("quality thresholds must be between 0 and 100")

    source_postings = source_client.list_postings(
        max_postings=max_postings + skip_postings,
        max_pages=max_pages,
    )
    postings = source_postings[skip_postings : skip_postings + max_postings]
    cases: list[dict[str, Any]] = []
    posting_evidence: list[dict[str, Any]] = []
    private_source_rows: list[dict[str, str]] = []
    capture_dir = (
        validate_private_gold_source_dir(private_gold_source_dir)
        if private_gold_source_dir is not None
        else None
    )
    if capture_dir is not None:
        capture_dir.mkdir(parents=True, exist_ok=True)

    with use_ncs_mcp_request_session():
        for posting_index, posting in enumerate(postings, start=1):
            detail_html = source_client.fetch_detail_html(posting)
            source_payload = source_client.fetch_posting_source_union(posting)
            source_names, source_codes = parse_posting_source_union(source_payload)
            posting_id = digester.text("posting-id", posting.recrt_no)
            source_name_ids = sorted(digester.text("detail-name", name) for name in sorted(source_names))
            source_code_ids = sorted(digester.text("detail-code", code) for code in sorted(source_codes))
            attachments = parse_jd_attachments(detail_html, posting)

            posting_observed_names_raw: set[str] = set()
            posting_observed_codes_raw: set[str] = set()
            attachment_cases: list[dict[str, Any]] = []
            for attachment in attachments:
                if delay_seconds:
                    sleep_func(delay_seconds)

                attachment_key = "|".join(
                    [posting.recrt_no, attachment.sys_dstin_cd, attachment.file_mstky, attachment.filedetl_seq]
                ).encode("utf-8")
                attachment_id = _private_id(digester, "attachment-id", attachment_key)
                case: dict[str, Any] = {
                    "posting_id": posting_id,
                    "attachment_id": attachment_id,
                    "attachment_ordinal": attachment.ordinal,
                    "source_detail_name_ids": source_name_ids,
                    "source_detail_code_ids": source_code_ids,
                    "source_detail_count": len(source_names),
                    "observed_detail_name_ids": [],
                    "observed_detail_code_ids": [],
                    "observed_detail_count": 0,
                    "positioned_coordinate_contract_total": 0,
                    "positioned_coordinate_contract_valid_count": 0,
                    "positioned_table_label_count": 0,
                    "positioned_table_label_to_final_exact_match_count": 0,
                    "final_exact_ability_count": 0,
                    "final_exact_table_provenance_count": 0,
                    "final_exact_unit_code_count": 0,
                    "posting_query_ids": [],
                    "posting_ksa_probe_unit_count": 0,
                    "posting_ksa_available_unit_count": 0,
                    "declared_no_mapping": False,
                    "detail_source": "",
                    "status": "ok",
                    "status_reason": "",
                    "bytes": 0,
                    "parse_ms": 0.0,
                }
                try:
                    provisional_case_id = _private_id(
                        digester,
                        "case-id",
                        attachment_key,
                        str(attachment.ordinal).encode("utf-8"),
                    )
                    upload = source_client.download_attachment(
                        attachment,
                        max_bytes=max_attachment_bytes,
                        case_id=provisional_case_id,
                    )
                    content_digest = hashlib.sha256(upload.data).digest()
                    case_id = _private_id(digester, "case-id", attachment_key, content_digest)
                    upload = AttachmentDownload(
                        upload_filename=safe_upload_filename(case_id, Path(upload.upload_filename).suffix.lower()),
                        content_type=upload.content_type,
                        data=upload.data,
                    )
                    if capture_dir is not None:
                        source_path = capture_dir / upload.upload_filename
                        source_path.write_bytes(upload.data)
                        private_source_rows.append(
                            {
                                "case_id": case_id,
                                "local_document_path": str(source_path.resolve()),
                                "document_sha256": content_digest.hex(),
                            }
                        )
                    started = time.perf_counter()
                    parse_result = parse_client.parse_review(upload)
                    case["case_id"] = case_id
                    case["bytes"] = len(upload.data)
                    case["parse_ms"] = round((time.perf_counter() - started) * 1000, 2)

                    if not parse_result.ok:
                        case["status"] = "parse_error"
                        case["status_reason"] = parse_result.status
                        attachment_cases.append(case)
                        continue

                    fields = parse_result.payload.get("fields") if isinstance(parse_result.payload.get("fields"), dict) else {}
                    observed_names, observed_codes = exact_detail_sets(fields)
                    posting_observed_names_raw.update(observed_names)
                    posting_observed_codes_raw.update(observed_codes)

                    ability_rows = exact_ability_rows(fields)
                    final_label_keys = {
                        normalized_label_key(label)
                        for label in final_exact_ability_labels(ability_rows)
                        if normalized_label_key(label)
                    }
                    table_label_keys = {
                        normalized_label_key(label)
                        for label in positioned_table_ability_labels(fields)
                        if normalized_label_key(label)
                    }
                    coordinate_total, coordinate_valid = (
                        positioned_ability_coordinate_counts(fields)
                    )
                    case.update(
                        {
                            "observed_detail_name_ids": sorted(
                                digester.text("detail-name", name) for name in sorted(observed_names)
                            ),
                            "observed_detail_code_ids": sorted(
                                digester.text("detail-code", code) for code in sorted(observed_codes)
                            ),
                            "observed_detail_count": len(observed_names),
                            "positioned_coordinate_contract_total": coordinate_total,
                            "positioned_coordinate_contract_valid_count": coordinate_valid,
                            "positioned_table_label_count": len(table_label_keys),
                            "positioned_table_label_to_final_exact_match_count": len(
                                table_label_keys & final_label_keys
                            ),
                            "final_exact_ability_count": len(final_label_keys),
                            "final_exact_table_provenance_count": len(final_label_keys & table_label_keys),
                            "final_exact_unit_code_count": len(final_exact_unit_codes(ability_rows)),
                            "declared_no_mapping": bool(fields.get("ncs_detail_absence_declared_no_mapping")),
                            "detail_source": normalize_text(fields.get("ncs_detail_source")),
                            "status_reason": detail_status_reason(fields, observed_names),
                        }
                    )
                except (ConfigurationError, SourceFetchError, NcsMcpError, httpx.HTTPError) as exc:
                    case["case_id"] = _private_id(digester, "case-id", attachment_key, b"error")
                    case["status"] = "error"
                    case["status_reason"] = safe_exception_status_reason(exc)
                attachment_cases.append(case)

            posting_query_ids: list[str] = []
            posting_ksa_probe_unit_count = 0
            posting_ksa_available_unit_count = 0
            posting_ksa_status = "not_applicable"
            if posting_observed_names_raw:
                try:
                    (
                        posting_query_ids,
                        posting_ksa_probe_unit_count,
                        posting_ksa_available_unit_count,
                    ) = _query_posting_ksa(
                        detail_names=posting_observed_names_raw,
                        digester=digester,
                        ncs_unit_limit=ncs_unit_limit,
                        max_ksa_factors_per_unit=max_ksa_factors_per_unit,
                        search_units_fn=search_units_fn,
                        get_ksa_fn=get_ksa_fn,
                    )
                    posting_ksa_status = "ok"
                except NcsMcpError:
                    posting_ksa_status = "error"

            for case in attachment_cases:
                case["posting_query_ids"] = list(posting_query_ids)
                case["posting_ksa_probe_unit_count"] = posting_ksa_probe_unit_count
                case["posting_ksa_available_unit_count"] = posting_ksa_available_unit_count
                cases.append(case)

            posting_evidence.append(
                {
                    "posting_id": posting_id,
                    "document_count": len(attachment_cases),
                    "source_detail_name_ids": source_name_ids,
                    "source_detail_code_ids": source_code_ids,
                    "observed_detail_name_ids": sorted(
                        digester.text("detail-name", name)
                        for name in posting_observed_names_raw
                    ),
                    "observed_detail_code_ids": sorted(
                        digester.text("detail-code", code)
                        for code in posting_observed_codes_raw
                    ),
                    "query_ids": list(posting_query_ids),
                    "ksa_probe_unit_count": posting_ksa_probe_unit_count,
                    "ksa_available_unit_count": posting_ksa_available_unit_count,
                    "ksa_status": posting_ksa_status,
                    "accuracy_eligible": bool(source_names and source_codes),
                }
            )

            if posting_index < len(postings) and delay_seconds:
                sleep_func(delay_seconds)

    metric_summary, posting_rows, case_rows = aggregate_metrics(
        cases,
        posting_evidence=posting_evidence,
    )
    if capture_dir is not None:
        write_private_gold_source_index(private_source_rows, capture_dir)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posting_count": len(postings),
        "document_count": len(case_rows),
        "sampling": {
            "strategy": "ordered_current_board_window",
            "skipped_posting_count": skip_postings,
            "selected_posting_limit": max_postings,
            "raw_posting_identifiers_written": False,
        },
        "quality_thresholds": thresholds,
        **metric_summary,
        "failures": [],
        "passed": True,
        "privacy": {
            "digest": "HMAC-SHA-256",
            "raw_filenames_written": False,
            "raw_document_text_written": False,
            "raw_labels_written": False,
            "normal_reports_exclude_private_gold_source_capture": True,
            "private_gold_source_capture_enabled": capture_dir is not None,
        },
    }
    if expected_postings is not None and len(postings) != int(expected_postings):
        summary["failures"].append("unexpected_posting_count")
    if expected_documents is not None and len(case_rows) != int(expected_documents):
        summary["failures"].append("unexpected_document_count")
    if not postings:
        summary["failures"].append("no_postings")
    if not case_rows:
        summary["failures"].append("no_documents")
    if any(int(row.get("document_count") or 0) == 0 for row in posting_rows):
        summary["failures"].append("posting_without_jd_attachment")
    if any(not bool(row.get("accuracy_eligible")) for row in posting_rows):
        summary["failures"].append("posting_without_source_ground_truth")
    if any(normalize_text(case.get("status")) != "ok" for case in case_rows):
        summary["failures"].append("case_processing_failure")
    if any(normalize_text(row.get("ksa_status")) == "error" for row in posting_evidence):
        summary["failures"].append("ncs_mcp_query_failure")
    if (
        int(summary["coordinate_metrics"].get("positioned_coordinate_contract_total") or 0)
        == 0
    ):
        summary["failures"].append("insufficient_coordinate_evidence")
    if int(summary["ksa_metrics"].get("probe_unit_count") or 0) == 0:
        summary["failures"].append("insufficient_ksa_probe_evidence")
    posting_accuracy = summary["posting_accuracy_evidence"]
    coordinate_metrics = summary["coordinate_metrics"]
    ksa_metrics = summary["ksa_metrics"]
    if float(posting_accuracy.get("precision_pct") or 0.0) < thresholds["posting_precision_pct"]:
        summary["failures"].append("posting_precision_below_threshold")
    if float(posting_accuracy.get("recall_pct") or 0.0) < thresholds["posting_recall_pct"]:
        summary["failures"].append("posting_recall_below_threshold")
    if float(posting_accuracy.get("exact_pct") or 0.0) < thresholds["posting_exact_pct"]:
        summary["failures"].append("posting_exact_below_threshold")
    if (
        float(coordinate_metrics.get("positioned_coordinate_shape_completeness_pct") or 0.0)
        < thresholds["coordinate_shape_pct"]
    ):
        summary["failures"].append("coordinate_shape_below_threshold")
    if float(ksa_metrics.get("availability_pct") or 0.0) < thresholds["ksa_availability_pct"]:
        summary["failures"].append("ksa_availability_below_threshold")
    summary["failures"] = sorted(set(summary["failures"]))
    summary["passed"] = not summary["failures"]
    return {"summary": summary, "postings": posting_rows, "cases": case_rows}


def write_reports(payload: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"ncs_recruitment_live_{stamp}.json"
    csv_path = output_dir / f"ncs_recruitment_live_{stamp}.csv"
    md_path = output_dir / f"ncs_recruitment_live_{stamp}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_fields = [
        "case_id",
        "posting_id",
        "attachment_id",
        "attachment_ordinal",
        "status",
        "status_reason",
        "source_detail_count",
        "observed_detail_count",
        "positioned_coordinate_contract_total",
        "positioned_coordinate_contract_valid_count",
        "positioned_table_label_count",
        "positioned_table_label_to_final_exact_match_count",
        "final_exact_ability_count",
        "final_exact_table_provenance_count",
        "final_exact_unit_code_count",
        "posting_ksa_probe_unit_count",
        "posting_ksa_available_unit_count",
        "declared_no_mapping",
        "detail_source",
        "bytes",
        "parse_ms",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in payload.get("cases") or []:
            writer.writerow({field: row.get(field, "") for field in csv_fields})

    summary = payload.get("summary") or {}
    posting_accuracy = summary.get("posting_accuracy_evidence") or {}
    posting_mismatch = summary.get("posting_mismatch_diagnostics") or {}
    document_mapping = summary.get("document_mapping_state_diagnostics") or {}
    attachment_diag = summary.get("attachment_union_non_accuracy_diagnostic") or {}
    coordinate = summary.get("coordinate_metrics") or {}
    ksa = summary.get("ksa_metrics") or {}
    md_path.write_text(
        "\n".join(
            [
                "# Live NCS Recruitment Benchmark",
                "",
                f"- Generated at: `{summary.get('generated_at', '')}`",
                f"- Passed: `{summary.get('passed', False)}`",
                f"- Postings: `{summary.get('posting_count', 0)}`",
                f"- Documents: `{summary.get('document_count', 0)}`",
                "",
                "## Posting Accuracy Evidence",
                "",
                f"- Precision: `{posting_accuracy.get('precision_pct', 0)}%`",
                f"- Recall: `{posting_accuracy.get('recall_pct', 0)}%`",
                f"- Exact: `{posting_accuracy.get('exact_pct', 0)}%`",
                "",
                "## Posting Mismatch Diagnostics",
                "",
                "- These set-relation buckets do not assign parser causality.",
                "- Counts: `"
                + ", ".join(
                    f"{key}={value}"
                    for key, value in sorted((posting_mismatch.get("counts") or {}).items())
                )
                + "`",
                "",
                "## Document Mapping-State Diagnostics",
                "",
                "- Status reasons: `"
                + ", ".join(
                    f"{key}={value}"
                    for key, value in sorted((document_mapping.get("status_reason_counts") or {}).items())
                )
                + "`",
                f"- Declared no-mapping documents: `{document_mapping.get('declared_no_mapping_document_count', 0)}`",
                "- These document states are diagnostics, not posting-level accuracy labels.",
                "",
                "## Attachment Diagnostic",
                "",
                f"- Precision: `{attachment_diag.get('precision_pct', 0)}%`",
                f"- Recall: `{attachment_diag.get('recall_pct', 0)}%`",
                f"- Exact: `{attachment_diag.get('exact_pct', 0)}%`",
                f"- Scope: `{attachment_diag.get('label', '')}`",
                "",
                "## Coordinate Metrics",
                "",
                f"- Positioned logical-coordinate shape completeness: `{coordinate.get('positioned_coordinate_shape_completeness_pct', 0)}%`",
                f"- Positioned table label to final exact-name match: `{coordinate.get('positioned_table_label_to_final_exact_match_pct', 0)}%`",
                f"- Final unique exact ability table provenance: `{coordinate.get('final_unique_exact_ability_table_provenance_pct', 0)}%`",
                "",
                "## KSA Metrics",
                "",
                f"- Probe unit count: `{ksa.get('probe_unit_count', 0)}`",
                f"- Available unit count: `{ksa.get('available_unit_count', 0)}`",
                f"- Availability: `{ksa.get('availability_pct', 0)}%`",
                "",
                "## Privacy",
                "",
                "- Outputs contain only HMAC identifiers, stable status codes, counts, and aggregate metrics.",
                "- Raw posting titles, attachment labels, filenames, and document text are intentionally omitted.",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, csv_path, md_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit current NCS recruitment postings through the public source, local "
            "parse-review, and NCS MCP with privacy-preserving HMAC reporting."
        )
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--private-gold-source-dir",
        nargs="?",
        const=str(DEFAULT_PRIVATE_GOLD_SOURCE_DIR),
        help=(
            "retain downloaded documents and a case_id source index below tmp/ or .tmp/ "
            "for independent human gold review"
        ),
    )
    parser.add_argument("--parse-base-url", default=DEFAULT_PARSE_BASE_URL)
    parser.add_argument("--allow-remote-parse-upload", action="store_true")
    parser.add_argument("--digest-key-env", default=DEFAULT_DIGEST_KEY_ENV)
    parser.add_argument("--max-postings", type=int, default=DEFAULT_MAX_POSTINGS)
    parser.add_argument(
        "--skip-postings",
        type=int,
        default=0,
        help="skip this many current-board postings before selecting the audit window",
    )
    parser.add_argument("--page-limit", type=int, default=DEFAULT_PAGE_LIMIT)
    parser.add_argument("--max-attachment-bytes", type=int, default=DEFAULT_MAX_ATTACHMENT_BYTES)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--parse-timeout-seconds", type=float, default=DEFAULT_PARSE_TIMEOUT_SECONDS)
    parser.add_argument("--source-timeout-seconds", type=float, default=DEFAULT_SOURCE_TIMEOUT_SECONDS)
    parser.add_argument("--expected-postings", type=int)
    parser.add_argument("--expected-documents", type=int)
    parser.add_argument("--ncs-unit-limit", type=int, default=DEFAULT_NCS_UNIT_LIMIT)
    parser.add_argument("--max-ksa-factors-per-unit", type=int, default=DEFAULT_MAX_KSA_FACTORS_PER_UNIT)
    parser.add_argument(
        "--min-posting-precision-pct",
        type=float,
        default=DEFAULT_MIN_POSTING_PRECISION_PCT,
    )
    parser.add_argument(
        "--min-posting-recall-pct",
        type=float,
        default=DEFAULT_MIN_POSTING_RECALL_PCT,
    )
    parser.add_argument(
        "--min-posting-exact-pct",
        type=float,
        default=DEFAULT_MIN_POSTING_EXACT_PCT,
    )
    parser.add_argument(
        "--min-coordinate-shape-pct",
        type=float,
        default=DEFAULT_MIN_COORDINATE_SHAPE_PCT,
    )
    parser.add_argument(
        "--min-ksa-availability-pct",
        type=float,
        default=DEFAULT_MIN_KSA_AVAILABILITY_PCT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        digester = PrivacyDigester(load_digest_key_from_env(args.digest_key_env))
        with SourceClient(timeout_seconds=float(args.source_timeout_seconds)) as source_client, ParseReviewClient(
            base_url=str(args.parse_base_url),
            timeout_seconds=float(args.parse_timeout_seconds),
            allow_remote_parse_upload=bool(args.allow_remote_parse_upload),
        ) as parse_client:
            payload = run_benchmark(
                source_client=source_client,
                parse_client=parse_client,
                digester=digester,
                max_postings=int(args.max_postings),
                skip_postings=int(args.skip_postings),
                max_pages=int(args.page_limit),
                max_attachment_bytes=int(args.max_attachment_bytes),
                delay_seconds=float(args.delay_seconds),
                expected_postings=args.expected_postings,
                expected_documents=args.expected_documents,
                ncs_unit_limit=int(args.ncs_unit_limit),
                max_ksa_factors_per_unit=int(args.max_ksa_factors_per_unit),
                min_posting_precision_pct=float(args.min_posting_precision_pct),
                min_posting_recall_pct=float(args.min_posting_recall_pct),
                min_posting_exact_pct=float(args.min_posting_exact_pct),
                min_coordinate_shape_pct=float(args.min_coordinate_shape_pct),
                min_ksa_availability_pct=float(args.min_ksa_availability_pct),
                private_gold_source_dir=(
                    Path(args.private_gold_source_dir)
                    if args.private_gold_source_dir
                    else None
                ),
            )
        json_path, csv_path, md_path = write_reports(payload, Path(args.output_dir))
    except ConfigurationError:
        print(json.dumps({"passed": False, "failures": ["configuration_error"]}))
        return 2
    except (SourceFetchError, NcsMcpError, httpx.HTTPError):
        print(json.dumps({"passed": False, "failures": ["upstream_audit_failure"]}))
        return 1
    except Exception:
        print(json.dumps({"passed": False, "failures": ["unexpected_audit_failure"]}))
        return 1
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(str(json_path))
    print(str(csv_path))
    print(str(md_path))
    if args.private_gold_source_dir:
        private_dir = validate_private_gold_source_dir(Path(args.private_gold_source_dir))
        print(str(private_dir / "source_index.local.csv"))
    return 0 if payload["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
