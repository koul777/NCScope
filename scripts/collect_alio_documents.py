"""Collect JOB-ALIO notices/JDs and build a review-only NCS profile.

The collector is resumable (SQLite URL uniqueness), traverses date windows and
pagination, imports *all* notice and job-description attachments found in each
posting, and keeps parsing/classification evidence.  Raw public files are
optional; structured text and hashes are always retained in the local corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.alio_ingestion import (  # noqa: E402
    ALIO_LIST_PATH,
    ALIO_MAX_ATTACHMENT_BYTES,
    ALIO_MAX_HTML_BYTES,
    ALIO_HOST,
    _extract_attachments,
    _extract_inline_notice,
    _extract_labeled_values,
    _extract_postings,
    download_alio_attachment,
)
from app.services.alio_sclass_profile import build_sclass_profile  # noqa: E402
from app.services import ncs_mcp_client  # noqa: E402
from app.services.ncs_match_gate import (  # noqa: E402
    NCS_MATCH_GATE_VERSION,
    build_ncs_match_bundle,
)
from app.services.hwp_text_fallback import extract_hwp_text, extract_hwpx_text  # noqa: E402
from app.services.jd_strategy import extract_pdf_text, lookup_ncs_codes_by_sclass  # noqa: E402
from app.services.kordoc_parser import (  # noqa: E402
    KordocParseError,
    parse_with_kordoc,
    structure_job_description,
    structure_job_notice,
)


ALIO_LIST_URL = f"https://{ALIO_HOST}{ALIO_LIST_PATH}"
USER_AGENT = "NCScope-ALIO-corpus-builder/1.0 (+bounded; public recruiting documents)"
SUPPORTED_ARCHIVE_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
NCS_MATCHER_VERSION = NCS_MATCH_GATE_VERSION


@dataclass(frozen=True)
class CrawlOptions:
    start_date: date
    end_date: date
    window_days: int
    posting_limit: int
    page_limit_per_window: int
    request_delay_sec: float
    max_attachment_bytes: int
    keep_files: bool
    retry_errors: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _suffix(value: str) -> str:
    return Path(str(value or "").replace("\\", "/")).suffix.casefold()


def _safe_filename(value: str) -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", name)
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
    return name[:180] or "alio_document.bin"


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS postings (
            posting_id TEXT PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            organization TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            list_window_start TEXT NOT NULL DEFAULT '',
            list_window_end TEXT NOT NULL DEFAULT '',
            detail_fields_json TEXT NOT NULL DEFAULT '{}',
            inline_notice_json TEXT NOT NULL DEFAULT '{}',
            discovered_at TEXT NOT NULL,
            inspected_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'discovered',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id),
            file_no TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',
            byte_size INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            parser TEXT NOT NULL DEFAULT '',
            parser_version TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            fields_json TEXT NOT NULL DEFAULT '{}',
            local_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'discovered',
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(posting_id, file_no)
        );
        CREATE INDEX IF NOT EXISTS idx_documents_posting_kind ON documents(posting_id, kind);
        CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
        CREATE TABLE IF NOT EXISTS document_ncs_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            source_detail_index INTEGER NOT NULL,
            source_detail TEXT NOT NULL DEFAULT '',
            ncs_detail_source TEXT NOT NULL DEFAULT '',
            source_signature TEXT NOT NULL,
            source_document_sha256 TEXT NOT NULL DEFAULT '',
            source_fields_sha256 TEXT NOT NULL DEFAULT '',
            matcher_version TEXT NOT NULL,
            status TEXT NOT NULL,
            last_success_status TEXT NOT NULL DEFAULT '',
            last_success_source_signature TEXT NOT NULL DEFAULT '',
            last_success_matcher_version TEXT NOT NULL DEFAULT '',
            match_method TEXT NOT NULL DEFAULT '',
            official_detail_code TEXT NOT NULL DEFAULT '',
            official_detail_name TEXT NOT NULL DEFAULT '',
            expected_unit_count INTEGER NOT NULL DEFAULT 0,
            verified_unit_count INTEGER NOT NULL DEFAULT 0,
            persisted_unit_count INTEGER NOT NULL DEFAULT 0,
            retrieval_complete INTEGER NOT NULL DEFAULT 0,
            retrieval_cap_limited INTEGER NOT NULL DEFAULT 0,
            retryable INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            attempted_at TEXT NOT NULL,
            matched_at TEXT NOT NULL DEFAULT '',
            UNIQUE(document_id, source_detail_index)
        );
        CREATE INDEX IF NOT EXISTS idx_document_ncs_matches_document
            ON document_ncs_matches(document_id, status);
        CREATE INDEX IF NOT EXISTS idx_document_ncs_matches_official_detail
            ON document_ncs_matches(official_detail_code, status);
        CREATE INDEX IF NOT EXISTS idx_document_ncs_matches_retryable
            ON document_ncs_matches(retryable, status);
        CREATE TABLE IF NOT EXISTS document_ncs_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES document_ncs_matches(id) ON DELETE CASCADE,
            official_unit_base_code TEXT NOT NULL,
            official_unit_full_code TEXT NOT NULL,
            official_unit_name TEXT NOT NULL,
            official_detail_code TEXT NOT NULL,
            official_detail_name TEXT NOT NULL,
            ownership_detail_code TEXT NOT NULL,
            ownership_detail_name TEXT NOT NULL,
            catalog_source TEXT NOT NULL,
            catalog_resolution_kind TEXT NOT NULL,
            catalog_unit_codes_json TEXT NOT NULL DEFAULT '[]',
            unit_catalog_verified INTEGER NOT NULL DEFAULT 0,
            unit_version_compatible INTEGER NOT NULL DEFAULT 0,
            detail_path_code_verified INTEGER NOT NULL DEFAULT 0,
            retrieval_kind TEXT NOT NULL DEFAULT '',
            retrieval_query TEXT NOT NULL DEFAULT '',
            mcp_source TEXT NOT NULL DEFAULT '',
            mcp_unit_name TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL,
            UNIQUE(match_id, official_unit_base_code)
        );
        CREATE INDEX IF NOT EXISTS idx_document_ncs_units_match
            ON document_ncs_units(match_id);
        CREATE INDEX IF NOT EXISTS idx_document_ncs_units_full_code
            ON document_ncs_units(official_unit_full_code);
        CREATE INDEX IF NOT EXISTS idx_document_ncs_units_detail_base
            ON document_ncs_units(official_detail_code, official_unit_base_code);
        CREATE TABLE IF NOT EXISTS crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            options_json TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    # Keep existing corpus databases forward-compatible. ``CREATE TABLE IF
    # NOT EXISTS`` does not add newly introduced audit columns.
    match_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(document_ncs_matches)")
    }
    for column_name in (
        "last_success_source_signature",
        "last_success_matcher_version",
    ):
        if column_name not in match_columns:
            connection.execute(
                f"ALTER TABLE document_ncs_matches ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''"
            )
    # Backfill provenance for exact matches written by an older collector.
    connection.execute(
        """
        UPDATE document_ncs_matches SET
            last_success_source_signature=CASE
                WHEN last_success_source_signature='' THEN source_signature
                ELSE last_success_source_signature
            END,
            last_success_matcher_version=CASE
                WHEN last_success_matcher_version='' THEN matcher_version
                ELSE last_success_matcher_version
            END
        WHERE last_success_status='exact_complete'
        """
    )
    connection.commit()


def _ensure_foreign_keys(connection: sqlite3.Connection) -> None:
    """Enable and verify FK enforcement before mutating match evidence."""

    connection.execute("PRAGMA foreign_keys=ON")
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if not enabled or int(enabled[0]) != 1:
        raise RuntimeError(
            "SQLite foreign_keys must be enabled before persisting NCS matches"
        )


def _bounded_get_text(client: httpx.Client, url: str) -> str:
    retries = max(1, int(os.getenv("ALIO_HTTP_RETRIES", "3") or "3"))
    delay = max(0.0, float(os.getenv("ALIO_HTTP_RETRY_DELAY_SEC", "0.5") or "0.5"))
    for attempt in range(retries):
        try:
            response = client.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": ALIO_LIST_URL},
                follow_redirects=True,
            )
            response.raise_for_status()
            if len(response.content) > ALIO_MAX_HTML_BYTES:
                raise RuntimeError("ALIO HTML exceeded the bounded response size")
            response.encoding = response.encoding or "utf-8"
            return response.text
        except (httpx.HTTPError, OSError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {408, 429} or exc.response.status_code >= 500
            if not retryable or attempt >= retries - 1:
                raise
            if delay:
                time.sleep(delay * (2**attempt))
    raise RuntimeError("ALIO request retry loop ended unexpectedly")


def _date_windows(start: date, end: date, window_days: int) -> Iterable[tuple[date, date]]:
    """Yield newest-first, non-overlapping windows for historical ALIO search."""

    cursor_end = end
    span = timedelta(days=max(1, int(window_days)) - 1)
    while cursor_end >= start:
        cursor_start = max(start, cursor_end - span)
        yield cursor_start, cursor_end
        cursor_end = cursor_start - timedelta(days=1)


def discover_postings(client: httpx.Client, options: CrawlOptions) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for window_start, window_end in _date_windows(options.start_date, options.end_date, options.window_days):
        empty_pages = 0
        for page_no in range(1, options.page_limit_per_window + 1):
            query = urlencode(
                {
                    "pageNo": page_no,
                    "pageSet": 100,
                    "sort": "DESC",
                    "order": "REG_DATE",
                    "s_date": window_start.strftime("%Y.%m.%d"),
                    "e_date": window_end.strftime("%Y.%m.%d"),
                }
            )
            page_url = f"{ALIO_LIST_URL}?{query}"
            text = _bounded_get_text(client, page_url)
            rows = _extract_postings(text, ALIO_LIST_URL, limit=500)
            fresh = [row for row in rows if row["posting_id"] not in seen]
            for row in fresh:
                seen.add(row["posting_id"])
                output.append(
                    {
                        **row,
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                    }
                )
                if options.posting_limit and len(output) >= options.posting_limit:
                    return output
            if not rows or not fresh:
                empty_pages += 1
            else:
                empty_pages = 0
            if empty_pages >= 2:
                break
            if options.request_delay_sec:
                time.sleep(options.request_delay_sec)
    return output


def _fallback_parse(data: bytes, filename: str) -> dict[str, Any]:
    suffix = _suffix(filename)
    if suffix == ".txt":
        text = data.decode("utf-8", errors="ignore")
        return {"markdown": text, "parser": "plain_text", "metadata": {"filename": filename}}
    if suffix == ".hwp":
        text = extract_hwp_text(data)
        if text.strip():
            return {"markdown": text, "parser": "hwp_text_fallback", "metadata": {"filename": filename}}
    if suffix == ".hwpx":
        text = extract_hwpx_text(data)
        if text.strip():
            return {"markdown": text, "parser": "hwpx_text_fallback", "metadata": {"filename": filename}}
    if suffix == ".pdf":
        text = extract_pdf_text(data)
        if text.strip():
            return {"markdown": text, "parser": "pdf_text_fallback", "metadata": {"filename": filename}}
    raise KordocParseError("no local fallback parser produced text")


def _parse_single(data: bytes, filename: str) -> dict[str, Any]:
    suffix = _suffix(filename)
    if suffix == ".txt":
        return _fallback_parse(data, filename)
    try:
        return parse_with_kordoc(
            data,
            filename=filename,
            ocr=suffix in {".png", ".jpg", ".jpeg", ".webp"},
        )
    except KordocParseError:
        return _fallback_parse(data, filename)


def parse_document(data: bytes, filename: str, max_bytes: int) -> dict[str, Any]:
    if _suffix(filename) != ".zip":
        return _parse_single(data, filename)

    chunks: list[str] = []
    members: list[dict[str, str]] = []
    total_uncompressed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir() or _suffix(info.filename) not in SUPPORTED_ARCHIVE_SUFFIXES:
                continue
            if info.flag_bits & 0x1:
                continue
            total_uncompressed += int(info.file_size or 0)
            if total_uncompressed > max_bytes:
                raise KordocParseError("ZIP uncompressed contents exceed attachment limit")
            if len(members) >= 30:
                break
            member_name = _safe_filename(info.filename)
            parsed = _parse_single(archive.read(info), member_name)
            markdown = str(parsed.get("markdown") or "").strip()
            if not markdown:
                continue
            members.append({"filename": member_name, "suffix": _suffix(member_name)})
            chunks.append(f"# ZIP member: {member_name}\n\n{markdown}")
    if not chunks:
        raise KordocParseError("ZIP contains no parseable recruiting documents")
    return {
        "markdown": "\n\n---\n\n".join(chunks),
        "metadata": {"filename": filename, "archive": True, "members": members},
        "parser": "mixed_document_parsers",
    }


def _posting_title_and_org(text: str, fallback_title: str) -> tuple[str, str]:
    title_match = re.search(r'<p[^>]+class=["\'][^"\']*titleH2[^"\']*["\'][^>]*>(.*?)</p>', text, re.I | re.S)
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", text, re.I | re.S)
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()
    title = clean(title_match.group(1)) if title_match else fallback_title
    org = next((clean(value) for value in headings if clean(value) not in {"홈페이지 주메뉴"}), "")
    return title, org


def _upsert_discovered_postings(connection: sqlite3.Connection, postings: list[dict[str, str]]) -> None:
    now = _utc_now()
    connection.executemany(
        """
        INSERT INTO postings(posting_id, url, title, list_window_start, list_window_end, discovered_at)
        VALUES(:posting_id, :url, :title, :window_start, :window_end, :discovered_at)
        ON CONFLICT(posting_id) DO UPDATE SET
            url=excluded.url,
            title=CASE WHEN postings.title='' THEN excluded.title ELSE postings.title END,
            list_window_start=excluded.list_window_start,
            list_window_end=excluded.list_window_end
        """,
        [{**row, "discovered_at": now} for row in postings],
    )
    connection.commit()


def _document_should_skip(connection: sqlite3.Connection, url: str, retry_errors: bool) -> bool:
    row = connection.execute("SELECT status FROM documents WHERE url=?", (url,)).fetchone()
    if not row:
        return False
    return row[0] == "parsed" or (row[0] == "error" and not retry_errors)


def _save_document_row(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO documents(
            posting_id,file_no,url,kind,section,filename,content_type,byte_size,sha256,
            parser,parser_version,markdown,fields_json,local_path,status,error,updated_at
        ) VALUES(
            :posting_id,:file_no,:url,:kind,:section,:filename,:content_type,:byte_size,:sha256,
            :parser,:parser_version,:markdown,:fields_json,:local_path,:status,:error,:updated_at
        )
        ON CONFLICT(url) DO UPDATE SET
            filename=excluded.filename,content_type=excluded.content_type,byte_size=excluded.byte_size,
            sha256=excluded.sha256,parser=excluded.parser,parser_version=excluded.parser_version,
            markdown=excluded.markdown,fields_json=excluded.fields_json,local_path=excluded.local_path,
            status=excluded.status,error=excluded.error,updated_at=excluded.updated_at
        """,
        values,
    )
    connection.commit()


def _document_detail_inputs(fields: dict[str, Any]) -> tuple[str, list[str]]:
    """Return source-labelled details without deriving semantic candidates."""

    detail_source = str(fields.get("ncs_detail_source") or "").strip()
    raw_candidates = fields.get("ncs_detail_candidates")
    if isinstance(raw_candidates, list):
        candidates = [str(value or "").strip() for value in raw_candidates]
    elif isinstance(raw_candidates, str):
        candidates = [raw_candidates.strip()]
    else:
        candidates = []
    candidates = [value for value in candidates if value]
    return detail_source, candidates


def _match_source_signature(
    *,
    document_id: int,
    source_detail_index: int,
    source_detail: str,
    ncs_detail_source: str,
    source_document_sha256: str,
    source_fields_sha256: str,
    matcher_version: str,
) -> str:
    payload = _json(
        {
            "document_id": document_id,
            "source_detail_index": source_detail_index,
            "source_detail": source_detail,
            "ncs_detail_source": ncs_detail_source,
            "source_document_sha256": source_document_sha256,
            "source_fields_sha256": source_fields_sha256,
            "matcher_version": matcher_version,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replace_ncs_match(
    connection: sqlite3.Connection,
    values: dict[str, Any],
    units: list[dict[str, Any]],
) -> int:
    """Atomically upsert one match and replace its stale verified-unit rows."""

    connection.execute(
        """
        INSERT INTO document_ncs_matches(
            document_id,source_detail_index,source_detail,ncs_detail_source,
            source_signature,source_document_sha256,source_fields_sha256,
            matcher_version,status,last_success_status,
            last_success_source_signature,last_success_matcher_version,
            match_method,
            official_detail_code,official_detail_name,expected_unit_count,
            verified_unit_count,persisted_unit_count,retrieval_complete,
            retrieval_cap_limited,retryable,error,attempted_at,matched_at
        ) VALUES(
            :document_id,:source_detail_index,:source_detail,:ncs_detail_source,
            :source_signature,:source_document_sha256,:source_fields_sha256,
            :matcher_version,:status,:last_success_status,
            :last_success_source_signature,:last_success_matcher_version,
            :match_method,
            :official_detail_code,:official_detail_name,:expected_unit_count,
            :verified_unit_count,:persisted_unit_count,:retrieval_complete,
            :retrieval_cap_limited,:retryable,:error,:attempted_at,:matched_at
        )
        ON CONFLICT(document_id,source_detail_index) DO UPDATE SET
            source_detail=excluded.source_detail,
            ncs_detail_source=excluded.ncs_detail_source,
            source_signature=excluded.source_signature,
            source_document_sha256=excluded.source_document_sha256,
            source_fields_sha256=excluded.source_fields_sha256,
            matcher_version=excluded.matcher_version,
            status=excluded.status,
            last_success_status=excluded.last_success_status,
            last_success_source_signature=excluded.last_success_source_signature,
            last_success_matcher_version=excluded.last_success_matcher_version,
            match_method=excluded.match_method,
            official_detail_code=excluded.official_detail_code,
            official_detail_name=excluded.official_detail_name,
            expected_unit_count=excluded.expected_unit_count,
            verified_unit_count=excluded.verified_unit_count,
            persisted_unit_count=excluded.persisted_unit_count,
            retrieval_complete=excluded.retrieval_complete,
            retrieval_cap_limited=excluded.retrieval_cap_limited,
            retryable=excluded.retryable,
            error=excluded.error,
            attempted_at=excluded.attempted_at,
            matched_at=excluded.matched_at
        """,
        values,
    )
    match_id = int(
        connection.execute(
            "SELECT id FROM document_ncs_matches WHERE document_id=? AND source_detail_index=?",
            (values["document_id"], values["source_detail_index"]),
        ).fetchone()[0]
    )
    connection.execute("DELETE FROM document_ncs_units WHERE match_id=?", (match_id,))
    if units:
        connection.executemany(
            """
            INSERT INTO document_ncs_units(
                match_id,official_unit_base_code,official_unit_full_code,
                official_unit_name,official_detail_code,official_detail_name,
                ownership_detail_code,ownership_detail_name,catalog_source,
                catalog_resolution_kind,catalog_unit_codes_json,
                unit_catalog_verified,unit_version_compatible,
                detail_path_code_verified,retrieval_kind,retrieval_query,
                mcp_source,mcp_unit_name,verified_at
            ) VALUES(
                :match_id,:official_unit_base_code,:official_unit_full_code,
                :official_unit_name,:official_detail_code,:official_detail_name,
                :ownership_detail_code,:ownership_detail_name,:catalog_source,
                :catalog_resolution_kind,:catalog_unit_codes_json,
                :unit_catalog_verified,:unit_version_compatible,
                :detail_path_code_verified,:retrieval_kind,:retrieval_query,
                :mcp_source,:mcp_unit_name,:verified_at
            )
            """,
            [{**unit, "match_id": match_id} for unit in units],
        )
    return match_id


def _record_ncs_mcp_unavailable(
    connection: sqlite3.Connection,
    values: dict[str, Any],
    error: Exception,
) -> bool:
    """Record a retryable failure and retain same-input exact evidence.

    ``status`` always describes the latest attempt. Retained official fields,
    units, and ``matched_at`` are historical evidence only and therefore never
    make an unavailable row generation-eligible.
    """

    existing = connection.execute(
        """
        SELECT id,source_detail,ncs_detail_source,source_document_sha256,
               source_fields_sha256,last_success_status
        FROM document_ncs_matches
        WHERE document_id=? AND source_detail_index=?
        """,
        (values["document_id"], values["source_detail_index"]),
    ).fetchone()
    error_text = str(error)[:800]
    # Re-structuring retained Markdown legitimately changes fields_json and
    # its hash.  The official match input is still the same when the immutable
    # document hash, explicit source kind, and detail label are unchanged.
    same_source = bool(
        existing
        and str(existing[1]) == str(values["source_detail"])
        and str(existing[2]) == str(values["ncs_detail_source"])
        and str(existing[3]) == str(values["source_document_sha256"])
    )
    if same_source and str(existing[5]) == "exact_complete":
        connection.execute(
            """
            UPDATE document_ncs_matches SET
                source_detail=:source_detail,
                ncs_detail_source=:ncs_detail_source,
                source_signature=:source_signature,
                source_document_sha256=:source_document_sha256,
                source_fields_sha256=:source_fields_sha256,
                matcher_version=:matcher_version,
                status='mcp_unavailable',
                match_method='ncs_mcp_unavailable_preserving_last_exact_complete',
                retryable=1,error=:error,
                attempted_at=:attempted_at
            WHERE id=:id
            """,
            {**values, "id": int(existing[0]), "error": error_text},
        )
        return True

    failed = {
        **values,
        "status": "mcp_unavailable",
        "last_success_status": "",
        "last_success_source_signature": "",
        "last_success_matcher_version": "",
        "match_method": "ncs_mcp_unavailable",
        "official_detail_code": "",
        "official_detail_name": "",
        "expected_unit_count": 0,
        "verified_unit_count": 0,
        "persisted_unit_count": 0,
        "retrieval_complete": 0,
        "retrieval_cap_limited": 0,
        "retryable": 1,
        "error": error_text,
        "matched_at": "",
    }
    _replace_ncs_match(connection, failed, [])
    return False


def _record_failed_ncs_match_attempt(
    connection: sqlite3.Connection,
    values: dict[str, Any],
    units: list[dict[str, Any]],
) -> bool:
    """Persist a failed attempt without erasing same-source exact evidence.

    The latest attempt remains explicit through ``status``, ``retryable``,
    ``error``, ``attempted_at``, ``matcher_version``, and ``source_signature``.
    The last exact result keeps its own signature/version plus official unit
    rows so reviewers can audit what was previously proven.
    """

    existing = connection.execute(
        """
        SELECT id,source_detail,ncs_detail_source,source_document_sha256,
               source_fields_sha256,last_success_status
        FROM document_ncs_matches
        WHERE document_id=? AND source_detail_index=?
        """,
        (values["document_id"], values["source_detail_index"]),
    ).fetchone()
    # Preserve the last exact proof across parser-only fields_json changes;
    # never preserve it across a changed document hash or detail identity.
    same_source = bool(
        existing
        and str(existing[1]) == str(values["source_detail"])
        and str(existing[2]) == str(values["ncs_detail_source"])
        and str(existing[3]) == str(values["source_document_sha256"])
    )
    if same_source and str(existing[5]) == "exact_complete":
        connection.execute(
            """
            UPDATE document_ncs_matches SET
                source_detail=:source_detail,
                ncs_detail_source=:ncs_detail_source,
                source_signature=:source_signature,
                source_document_sha256=:source_document_sha256,
                source_fields_sha256=:source_fields_sha256,
                matcher_version=:matcher_version,
                status=:status,
                match_method=:match_method,
                retryable=:retryable,
                error=:error,
                attempted_at=:attempted_at
            WHERE id=:id
            """,
            {**values, "id": int(existing[0])},
        )
        return True
    _replace_ncs_match(connection, values, units)
    return False


def _verified_ncs_unit_rows(
    items: Any,
    *,
    official_detail_code: str,
    official_detail_name: str,
    verified_at: str,
) -> list[dict[str, Any]]:
    """Keep only unit identities already proven by MCP plus bundled catalog."""

    output: list[dict[str, Any]] = []
    seen_base_codes: set[str] = set()
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict) or row.get("unitCatalogVerified") is not True:
            continue
        full_code = str(row.get("ncsClCd") or "").strip()
        base_code = str(row.get("officialUnitBaseCode") or "").strip()
        unit_name = str(row.get("officialUnitName") or "").strip()
        row_detail_code = str(row.get("officialDetailCode") or "").strip()
        row_detail_name = str(row.get("officialDetailName") or "").strip()
        ownership_name = str(row.get("ncsSubdCdnm") or row_detail_name).strip()
        normalized_row_detail_name = re.sub(
            r"[\W_]+",
            "",
            unicodedata.normalize("NFKC", row_detail_name).casefold(),
            flags=re.UNICODE,
        )
        normalized_official_detail_name = re.sub(
            r"[\W_]+",
            "",
            unicodedata.normalize("NFKC", official_detail_name).casefold(),
            flags=re.UNICODE,
        )
        normalized_owner_name = re.sub(
            r"[\W_]+",
            "",
            unicodedata.normalize("NFKC", ownership_name).casefold(),
            flags=re.UNICODE,
        )
        if not (
            re.fullmatch(r"\d{8}", official_detail_code)
            and re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", full_code)
            and re.fullmatch(r"\d{10}", base_code)
            and full_code.split("_", 1)[0] == base_code
            and base_code.startswith(official_detail_code)
            and row_detail_code == official_detail_code
            and normalized_row_detail_name == normalized_official_detail_name
            and normalized_owner_name == normalized_official_detail_name
            and unit_name
            and base_code not in seen_base_codes
        ):
            continue
        catalog_codes = [
            str(value)
            for value in (row.get("catalogUnitCodes") or [])
            if re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", str(value or "").strip())
            and str(value).strip().split("_", 1)[0] == base_code
        ]
        seen_base_codes.add(base_code)
        output.append(
            {
                "official_unit_base_code": base_code,
                "official_unit_full_code": full_code,
                "official_unit_name": unit_name,
                "official_detail_code": official_detail_code,
                "official_detail_name": official_detail_name,
                "ownership_detail_code": base_code[:8],
                "ownership_detail_name": ownership_name,
                "catalog_source": "bundled_ncs_unit_catalog",
                "catalog_resolution_kind": str(row.get("unitResolutionKind") or ""),
                "catalog_unit_codes_json": _json(catalog_codes),
                "unit_catalog_verified": 1,
                "unit_version_compatible": int(bool(row.get("unitVersionCompatible"))),
                "detail_path_code_verified": int(bool(row.get("detailPathCodeVerified"))),
                "retrieval_kind": str(row.get("unitRetrievalKind") or ""),
                "retrieval_query": str(row.get("unitRetrievalQuery") or ""),
                "mcp_source": str(row.get("source") or ""),
                "mcp_unit_name": str(row.get("mcpUnitName") or row.get("compeUnitName") or ""),
                "verified_at": verified_at,
            }
        )
    return output


def reparse_existing_job_descriptions(
    connection: sqlite3.Connection,
    *,
    dry_run: bool = False,
    document_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Rebuild stored JD fields from retained Markdown without downloading.

    The immutable source evidence (document SHA-256 and Markdown) is never
    rewritten.  All changed ``fields_json`` rows are committed together after
    every selected document has structured successfully; a parser exception
    therefore leaves the database untouched.  Matching is intentionally a
    separate, targeted step so callers can preview this function through a
    read-only SQLite connection.
    """

    _ensure_foreign_keys(connection)
    targeted = document_ids is not None
    selected_document_ids = sorted(
        {int(value) for value in (document_ids or []) if int(value) > 0}
    )
    if targeted and not selected_document_ids:
        return {
            "documents": 0,
            "changed": 0,
            "unchanged": 0,
            "obsolete_matches": 0,
            "preservable_exact_matches": 0,
            "dry_run": int(bool(dry_run)),
            "changed_document_ids": [],
        }

    document_filter = ""
    query_params: tuple[int, ...] = ()
    if selected_document_ids:
        document_filter = (
            " AND id IN ("
            + ",".join("?" for _value in selected_document_ids)
            + ")"
        )
        query_params = tuple(selected_document_ids)
    rows = connection.execute(
        f"""
        SELECT id,filename,parser,parser_version,markdown,fields_json
        FROM documents
        WHERE kind='job_description' AND status='parsed'
        {document_filter}
        ORDER BY id
        """,
        query_params,
    ).fetchall()

    planned_updates: list[tuple[str, str, int, str]] = []
    changed_document_ids: list[int] = []
    obsolete_matches = 0
    preservable_exact_matches = 0
    for (
        document_id_raw,
        filename_raw,
        parser_raw,
        parser_version_raw,
        markdown_raw,
        fields_json_raw,
    ) in rows:
        document_id = int(document_id_raw)
        parsed = {
            "markdown": str(markdown_raw or ""),
            "parser": str(parser_raw or ""),
            "parser_version": str(parser_version_raw or ""),
        }
        structured = structure_job_description(
            parsed,
            filename=str(filename_raw or ""),
        )
        fields = structured.get("fields")
        if not isinstance(fields, dict):
            raise ValueError(
                f"reparse produced invalid fields for document_id={document_id}"
            )
        new_fields_json = _json(fields)
        try:
            old_fields = json.loads(str(fields_json_raw or "{}"))
        except (TypeError, ValueError):
            old_fields = None
        if old_fields == fields:
            continue

        changed_document_ids.append(document_id)
        planned_updates.append(
            (
                new_fields_json,
                _utc_now(),
                document_id,
                str(fields_json_raw or "{}"),
            )
        )
        detail_source, candidates = _document_detail_inputs(fields)
        detail_inputs = candidates or [""]
        for source_detail_index, source_detail, old_detail_source, last_success in connection.execute(
            """
            SELECT source_detail_index,source_detail,ncs_detail_source,last_success_status
            FROM document_ncs_matches
            WHERE document_id=?
            ORDER BY source_detail_index
            """,
            (document_id,),
        ):
            index = int(source_detail_index)
            same_source = bool(
                index < len(detail_inputs)
                and str(source_detail or "") == detail_inputs[index]
                and str(old_detail_source or "") == detail_source
            )
            obsolete_matches += int(not same_source)
            preservable_exact_matches += int(
                same_source and str(last_success or "") == "exact_complete"
            )

    if planned_updates and not dry_run:
        with connection:
            cursor = connection.executemany(
                """
                UPDATE documents
                SET fields_json=?,updated_at=?,error=''
                WHERE id=? AND kind='job_description' AND status='parsed'
                  AND fields_json=?
                """,
                planned_updates,
            )
            if cursor.rowcount != len(planned_updates):
                raise RuntimeError(
                    "stored job descriptions changed during reparse; retry"
                )

    return {
        "documents": len(rows),
        "changed": len(planned_updates),
        "unchanged": len(rows) - len(planned_updates),
        "obsolete_matches": obsolete_matches,
        "preservable_exact_matches": preservable_exact_matches,
        "dry_run": int(bool(dry_run)),
        "changed_document_ids": changed_document_ids,
    }


def persist_document_ncs_matches(
    connection: sqlite3.Connection,
    *,
    max_units: int = 80,
    matcher_version: str = NCS_MATCHER_VERSION,
    document_ids: Iterable[int] | None = None,
) -> dict[str, int]:
    """Persist strict official-detail matches for parsed ALIO job descriptions.

    Only parser-declared ``explicit`` details are sent to the official matcher.
    Contextual, inferred, or empty inputs stay in a human-review state.  This
    function never calls the loose text-suggestion API.
    """

    _ensure_foreign_keys(connection)

    summary = {
        "documents": 0,
        "exact_complete": 0,
        "incomplete": 0,
        "unresolved": 0,
        "review_required": 0,
        "mcp_unavailable": 0,
        "persisted_units": 0,
        "last_success_preserved": 0,
    }
    targeted = document_ids is not None
    selected_document_ids = sorted(
        {
            int(value)
            for value in (document_ids or [])
            if int(value) > 0
        }
    )
    if targeted and not selected_document_ids:
        return summary
    document_filter = ""
    query_params: tuple[int, ...] = ()
    if selected_document_ids:
        document_filter = (
            " AND id IN ("
            + ",".join("?" for _value in selected_document_ids)
            + ")"
        )
        query_params = tuple(selected_document_ids)
    rows = connection.execute(
        f"""
        SELECT id,sha256,fields_json FROM documents
        WHERE kind='job_description' AND status='parsed'
        {document_filter}
        ORDER BY id
        """,
        query_params,
    ).fetchall()
    for document_id_raw, document_sha256_raw, fields_json_raw in rows:
        document_id = int(document_id_raw)
        document_sha256 = str(document_sha256_raw or "")
        fields_json = str(fields_json_raw or "{}")
        try:
            fields = json.loads(fields_json)
        except (TypeError, ValueError):
            fields = {}
        if not isinstance(fields, dict):
            fields = {}
        detail_source, candidates = _document_detail_inputs(fields)
        source_fields_sha256 = hashlib.sha256(fields_json.encode("utf-8")).hexdigest()
        # One placeholder row makes a missing extraction visible to reviewers.
        detail_inputs = candidates or [""]
        summary["documents"] += 1
        for source_detail_index, source_detail in enumerate(detail_inputs):
            now = _utc_now()
            base = {
                "document_id": document_id,
                "source_detail_index": source_detail_index,
                "source_detail": source_detail,
                "ncs_detail_source": detail_source,
                "source_document_sha256": document_sha256,
                "source_fields_sha256": source_fields_sha256,
                "matcher_version": str(matcher_version),
                "attempted_at": now,
            }
            base["source_signature"] = _match_source_signature(**{
                key: base[key]
                for key in (
                    "document_id",
                    "source_detail_index",
                    "source_detail",
                    "ncs_detail_source",
                    "source_document_sha256",
                    "source_fields_sha256",
                    "matcher_version",
                )
            })
            if detail_source != "explicit" or not source_detail:
                review = {
                    **base,
                    "status": "review_required",
                    "last_success_status": "",
                    "last_success_source_signature": "",
                    "last_success_matcher_version": "",
                    "match_method": "not_eligible_for_automatic_exact_match",
                    "official_detail_code": "",
                    "official_detail_name": "",
                    "expected_unit_count": 0,
                    "verified_unit_count": 0,
                    "persisted_unit_count": 0,
                    "retrieval_complete": 0,
                    "retrieval_cap_limited": 0,
                    "retryable": 0,
                    "error": "",
                    "matched_at": "",
                }
                with connection:
                    _replace_ncs_match(connection, review, [])
                summary["review_required"] += 1
                continue

            bundle = build_ncs_match_bundle(
                [source_detail],
                detail_source=detail_source,
                max_units=max(1, int(max_units)),
                search_fn=ncs_mcp_client.search_units_by_detail_result,
                matcher_version=matcher_version,
            )
            bundle_status = str(bundle.get("status") or "unresolved")
            if bundle_status == "unavailable":
                with connection:
                    preserved = _record_ncs_mcp_unavailable(
                        connection,
                        base,
                        ncs_mcp_client.NcsMcpError("NCS MCP unavailable"),
                    )
                summary["last_success_preserved"] += int(preserved)
                summary["mcp_unavailable"] += 1
                continue

            states = bundle.get("details")
            state = (
                states[0]
                if isinstance(states, list)
                and len(states) == 1
                and isinstance(states[0], dict)
                else {}
            )
            official_detail_code = str(state.get("officialDetailCode") or "").strip()
            official_detail_name = str(state.get("officialDetailName") or "").strip()
            has_official_identity = bool(
                state.get("mappingState") == "official_detail_resolved"
                and re.fullmatch(r"\d{8}", official_detail_code)
                and official_detail_name
            )
            status = (
                bundle_status
                if bundle_status in {"exact_complete", "incomplete", "unresolved"}
                else "unresolved"
            )
            expected_count = max(0, int(state.get("detailExpectedUnitBaseCount") or 0))
            verified_count = max(0, int(state.get("detailVerifiedUnitBaseCount") or 0))
            units = _verified_ncs_unit_rows(
                bundle.get("units") if has_official_identity else [],
                official_detail_code=(official_detail_code if has_official_identity else ""),
                official_detail_name=(official_detail_name if has_official_identity else ""),
                verified_at=now,
            )
            reason_codes = [
                str(value)
                for value in (bundle.get("reason_codes") or [])
                if str(value).strip()
            ]
            if status == "exact_complete" and (
                len(units) != expected_count
                or len(units) != len(bundle.get("units") or [])
            ):
                status = "incomplete"
                reason_codes.append("persisted_unit_rows_dropped")
            match = {
                **base,
                "status": status,
                "last_success_status": (
                    "exact_complete" if status == "exact_complete" else ""
                ),
                "last_success_source_signature": (
                    base["source_signature"] if status == "exact_complete" else ""
                ),
                "last_success_matcher_version": (
                    str(matcher_version) if status == "exact_complete" else ""
                ),
                "match_method": (
                    "official_catalog_exact_complete_via_ncs_mcp"
                    if status == "exact_complete"
                    else "official_catalog_incomplete_via_ncs_mcp"
                    if status == "incomplete" and has_official_identity
                    else "no_official_exact_match"
                ),
                "official_detail_code": official_detail_code if has_official_identity else "",
                "official_detail_name": official_detail_name if has_official_identity else "",
                "expected_unit_count": expected_count if has_official_identity else 0,
                "verified_unit_count": verified_count if has_official_identity else 0,
                "persisted_unit_count": len(units),
                "retrieval_complete": int(bool(state.get("detailRetrievalComplete"))) if has_official_identity else 0,
                "retrieval_cap_limited": int(bool(state.get("detailRetrievalCapLimited"))) if has_official_identity else 0,
                "retryable": int(status == "incomplete"),
                "error": ",".join(sorted(set(reason_codes)))[:800],
                "matched_at": now if status == "exact_complete" else "",
            }
            with connection:
                if status == "exact_complete":
                    _replace_ncs_match(connection, match, units)
                    preserved = False
                else:
                    preserved = _record_failed_ncs_match_attempt(
                        connection,
                        match,
                        units,
                    )
            summary["last_success_preserved"] += int(preserved)
            summary[status] += 1
            if not preserved:
                summary["persisted_units"] += len(units)

        # Candidate removal is authoritative parser input and safe to reconcile
        # independently of MCP availability for the candidates that remain.
        placeholders = ",".join("?" for _value in detail_inputs)
        with connection:
            connection.execute(
                f"""
                DELETE FROM document_ncs_matches
                WHERE document_id=? AND source_detail_index NOT IN ({placeholders})
                """,
                (document_id, *range(len(detail_inputs))),
            )
    return summary


def _load_ncs_mcp_url_from_dotenv(path: Path | None = None) -> bool:
    """Load only a credential-free NCS_MCP_URL from the project .env."""

    if os.getenv("NCS_MCP_URL", "").strip():
        return False
    env_path = path or (ROOT / ".env")
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        if key.strip() != "NCS_MCP_URL":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return False
        os.environ["NCS_MCP_URL"] = value
        return True
    return False


def inspect_and_collect_posting(
    client: httpx.Client,
    connection: sqlite3.Connection,
    posting: dict[str, str],
    files_dir: Path,
    options: CrawlOptions,
) -> tuple[int, int]:
    posting_id = posting["posting_id"]
    try:
        detail_html = _bounded_get_text(client, posting["url"])
        title, organization = _posting_title_and_org(detail_html, posting.get("title", ""))
        attachments = _extract_attachments(detail_html, posting["url"], limit=100)
        targets = [row for row in attachments if row.get("kind") in {"notice", "job_description"}]
        connection.execute(
            """
            UPDATE postings SET organization=?,title=?,detail_fields_json=?,inline_notice_json=?,
                inspected_at=?,status='inspected',error='' WHERE posting_id=?
            """,
            (
                organization,
                title,
                _json(_extract_labeled_values(detail_html)),
                _json(_extract_inline_notice(detail_html)),
                _utc_now(),
                posting_id,
            ),
        )
        connection.commit()
    except Exception as exc:
        connection.execute(
            "UPDATE postings SET inspected_at=?,status='error',error=? WHERE posting_id=?",
            (_utc_now(), str(exc)[:800], posting_id),
        )
        connection.commit()
        return 0, 1

    parsed_count = 0
    error_count = 0
    for attachment in targets:
        if _document_should_skip(connection, attachment["url"], options.retry_errors):
            continue
        base = {
            "posting_id": posting_id,
            "file_no": attachment.get("file_no", ""),
            "url": attachment["url"],
            "kind": attachment["kind"],
            "section": attachment.get("section", ""),
            "filename": attachment.get("name", ""),
            "content_type": "",
            "byte_size": 0,
            "sha256": "",
            "parser": "",
            "parser_version": "",
            "markdown": "",
            "fields_json": "{}",
            "local_path": "",
            "status": "error",
            "error": "",
            "updated_at": _utc_now(),
        }
        try:
            downloaded = download_alio_attachment(
                attachment["url"],
                expected_name=attachment.get("name", ""),
                max_bytes=options.max_attachment_bytes,
            )
            sha256 = hashlib.sha256(downloaded.data).hexdigest()
            filename = _safe_filename(downloaded.filename or attachment.get("name", ""))
            parsed = parse_document(downloaded.data, filename, options.max_attachment_bytes)
            if attachment["kind"] == "job_description":
                structured = structure_job_description(parsed, filename=filename)
            else:
                structured = structure_job_notice(parsed, filename=filename)
            local_path = ""
            if options.keep_files:
                target_dir = files_dir / posting_id
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{attachment.get('file_no') or sha256[:12]}_{filename}"
                if not target.exists():
                    target.write_bytes(downloaded.data)
                local_path = str(target.resolve())
            base.update(
                {
                    "filename": filename,
                    "content_type": downloaded.content_type,
                    "byte_size": len(downloaded.data),
                    "sha256": sha256,
                    "parser": str(structured.get("parser") or parsed.get("parser") or "unknown"),
                    "parser_version": str(structured.get("parser_version") or parsed.get("parser_version") or ""),
                    "markdown": str((structured.get("document") or {}).get("markdown") or parsed.get("markdown") or "")[:500000],
                    "fields_json": _json(structured.get("fields") or {}),
                    "local_path": local_path,
                    "status": "parsed",
                    "error": "",
                }
            )
            parsed_count += 1
        except Exception as exc:
            base["error"] = str(exc)[:800]
            error_count += 1
        _save_document_row(connection, base)
        if options.request_delay_sec:
            time.sleep(options.request_delay_sec)
    return parsed_count, error_count


def _profile_examples(connection: sqlite3.Connection) -> list[dict[str, str]]:
    notice_text_by_posting: dict[str, str] = {}
    for posting_id, fields_json, markdown in connection.execute(
        "SELECT posting_id,fields_json,markdown FROM documents WHERE kind='notice' AND status='parsed'"
    ):
        try:
            fields = json.loads(fields_json or "{}")
        except ValueError:
            fields = {}
        parts = [
            str(fields.get("duty_text") or ""),
            str(fields.get("qualification_text") or ""),
            str(fields.get("preference_text") or ""),
            str(markdown or "")[:30000],
        ]
        notice_text_by_posting[posting_id] = "\n".join(filter(None, [notice_text_by_posting.get(posting_id, ""), *parts]))

    examples: list[dict[str, str]] = []
    for posting_id, fields_json, markdown in connection.execute(
        "SELECT posting_id,fields_json,markdown FROM documents WHERE kind='job_description' AND status='parsed'"
    ):
        try:
            fields = json.loads(fields_json or "{}")
        except ValueError:
            continue
        candidates = [str(value).strip() for value in (fields.get("ncs_detail_candidates") or []) if str(value).strip()]
        if len(candidates) != 1 or str(fields.get("ncs_detail_source") or "") != "explicit":
            continue
        official = lookup_ncs_codes_by_sclass(candidates)
        if len(official) != 1 or float(official[0].get("confidence") or 0.0) < 0.999:
            continue
        row = official[0]
        parts: list[str] = []
        for key in ("duties", "knowledge", "skills", "attitudes", "qualifications", "preferences"):
            value = fields.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if str(item).strip())
            elif value:
                parts.append(str(value))
        parts.append(str(markdown or "")[:50000])
        parts.append(notice_text_by_posting.get(posting_id, "")[:30000])
        examples.append(
            {
                "ncs_code_no": str(row.get("ncs_code_no") or ""),
                "sclass_name": str(row.get("sclass_name") or ""),
                "text": "\n".join(parts),
            }
        )
    return examples


def write_profile(connection: sqlite3.Connection, profile_path: Path) -> dict[str, Any]:
    profile = build_sclass_profile(_profile_examples(connection))
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _summary(connection: sqlite3.Connection) -> dict[str, Any]:
    def count(query: str) -> int:
        return int(connection.execute(query).fetchone()[0])

    statuses = {
        str(status): int(total)
        for status, total in connection.execute("SELECT status,COUNT(*) FROM documents GROUP BY status")
    }
    kinds = {
        str(kind): int(total)
        for kind, total in connection.execute("SELECT kind,COUNT(*) FROM documents WHERE status='parsed' GROUP BY kind")
    }
    ncs_match_statuses = {
        str(status): int(total)
        for status, total in connection.execute(
            "SELECT status,COUNT(*) FROM document_ncs_matches GROUP BY status"
        )
    }
    explicit = 0
    for (fields_json,) in connection.execute("SELECT fields_json FROM documents WHERE kind='job_description' AND status='parsed'"):
        try:
            fields = json.loads(fields_json or "{}")
        except ValueError:
            continue
        if fields.get("ncs_detail_candidates"):
            explicit += 1
    return {
        "postings": count("SELECT COUNT(*) FROM postings"),
        "documents": count("SELECT COUNT(*) FROM documents"),
        "document_statuses": statuses,
        "parsed_kinds": kinds,
        "job_descriptions_with_detail_candidates": explicit,
        "ncs_match_statuses": ncs_match_statuses,
        "verified_document_ncs_units": count(
            "SELECT COUNT(*) FROM document_ncs_units WHERE unit_catalog_verified=1"
        ),
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description="Collect public JOB-ALIO notices and job descriptions.")
    parser.add_argument("--start-date", type=_parse_date, default=today - timedelta(days=365))
    parser.add_argument("--end-date", type=_parse_date, default=today)
    parser.add_argument("--window-days", type=int, default=31, help="historical search window size")
    parser.add_argument("--limit", type=int, default=1000, help="posting limit; 0 means no limit")
    parser.add_argument("--page-limit-per-window", type=int, default=500)
    parser.add_argument("--delay", type=float, default=0.35, help="delay between public requests")
    parser.add_argument("--max-download-mb", type=int, default=20)
    parser.add_argument("--keep-files", action="store_true", help="also retain raw public attachments")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument(
        "--reparse-existing-job-descriptions",
        action="store_true",
        help=(
            "skip ALIO discovery/download; rebuild fields from stored parsed "
            "job-description Markdown and rematch only changed documents"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview --reparse-existing-job-descriptions through a read-only database connection",
    )
    ncs_matching = parser.add_mutually_exclusive_group()
    ncs_matching.add_argument(
        "--match-ncs",
        dest="match_ncs",
        action="store_true",
        help="persist exact official NCS detail/unit matches (default)",
    )
    ncs_matching.add_argument(
        "--skip-ncs-match",
        dest="match_ncs",
        action="store_false",
        help="collect documents without running the official NCS MCP matcher",
    )
    parser.set_defaults(match_ncs=True)
    parser.add_argument("--ncs-max-units", type=int, default=80)
    parser.add_argument("--out-dir", default=".local/alio_corpus")
    args = parser.parse_args(argv)

    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date")
    if args.dry_run and not args.reparse_existing_job_descriptions:
        parser.error("--dry-run requires --reparse-existing-job-descriptions")
    if (
        args.reparse_existing_job_descriptions
        and not args.dry_run
        and not args.match_ncs
    ):
        parser.error(
            "--reparse-existing-job-descriptions requires NCS rematching; "
            "omit --skip-ncs-match"
        )
    out_dir = Path(args.out_dir)
    db_path = out_dir / "alio_corpus.sqlite3"
    profile_path = out_dir / "sclass_profiles.json"
    if args.reparse_existing_job_descriptions and args.dry_run:
        if not db_path.is_file():
            parser.error(f"existing corpus database not found: {db_path}")
        read_only_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(read_only_uri, uri=True) as connection:
            reparse_summary = reparse_existing_job_descriptions(
                connection,
                dry_run=True,
            )
        print(
            json.dumps(
                {
                    "mode": "reparse_existing_job_descriptions",
                    "dry_run": True,
                    "reparse": reparse_summary,
                    "ncs_matching": "not_run_in_dry_run",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    if args.reparse_existing_job_descriptions:
        started_at = _utc_now()
        with sqlite3.connect(db_path) as connection:
            _schema(connection)
            reparse_summary = reparse_existing_job_descriptions(connection)
            _load_ncs_mcp_url_from_dotenv()
            ncs_match_summary = persist_document_ncs_matches(
                connection,
                max_units=max(1, min(500, int(args.ncs_max_units))),
                document_ids=reparse_summary["changed_document_ids"],
            )
            profile = write_profile(connection, profile_path)
            summary = _summary(connection)
            summary["reparse_existing_job_descriptions"] = reparse_summary
            summary["ncs_matching"] = ncs_match_summary
            summary["profile_training_documents"] = int(
                profile.get("training_documents") or 0
            )
            summary["profile_classes"] = int(profile.get("class_count") or 0)
            connection.execute(
                """
                INSERT INTO crawl_runs(
                    started_at,finished_at,options_json,summary_json
                ) VALUES(?,?,?,?)
                """,
                (
                    started_at,
                    _utc_now(),
                    _json(
                        {
                            "mode": "reparse_existing_job_descriptions",
                            "match_ncs": True,
                            "ncs_max_units": max(
                                1,
                                min(500, int(args.ncs_max_units)),
                            ),
                        }
                    ),
                    _json(summary),
                ),
            )
            connection.commit()

        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"database={db_path.resolve()}")
        print(f"profile={profile_path.resolve()}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    options = CrawlOptions(
        start_date=args.start_date,
        end_date=args.end_date,
        window_days=max(1, min(93, int(args.window_days))),
        posting_limit=max(0, int(args.limit)),
        page_limit_per_window=max(1, int(args.page_limit_per_window)),
        request_delay_sec=max(0.0, float(args.delay)),
        max_attachment_bytes=max(1, min(ALIO_MAX_ATTACHMENT_BYTES, int(args.max_download_mb) * 1024 * 1024)),
        keep_files=bool(args.keep_files),
        retry_errors=bool(args.retry_errors),
    )

    with sqlite3.connect(db_path) as connection:
        _schema(connection)
        run_id = connection.execute(
            "INSERT INTO crawl_runs(started_at,options_json) VALUES(?,?)",
            (_utc_now(), _json({key: str(value) for key, value in vars(options).items()})),
        ).lastrowid
        connection.commit()
        timeout = httpx.Timeout(connect=8.0, read=35.0, write=8.0, pool=8.0)
        with httpx.Client(timeout=timeout) as client:
            postings = discover_postings(client, options)
            _upsert_discovered_postings(connection, postings)
            print(f"discovered_postings={len(postings)}", flush=True)
            for index, posting in enumerate(postings, start=1):
                parsed_count, error_count = inspect_and_collect_posting(
                    client,
                    connection,
                    posting,
                    out_dir / "files",
                    options,
                )
                print(
                    f"[{index}/{len(postings)}] posting={posting['posting_id']} parsed={parsed_count} errors={error_count}",
                    flush=True,
                )
                if options.request_delay_sec:
                    time.sleep(options.request_delay_sec)
        ncs_match_summary: dict[str, int] = {}
        if args.match_ncs:
            _load_ncs_mcp_url_from_dotenv()
            ncs_match_summary = persist_document_ncs_matches(
                connection,
                max_units=max(1, min(500, int(args.ncs_max_units))),
            )
        profile = write_profile(connection, profile_path)
        summary = _summary(connection)
        if args.match_ncs:
            summary["ncs_matching"] = ncs_match_summary
        summary["profile_training_documents"] = int(profile.get("training_documents") or 0)
        summary["profile_classes"] = int(profile.get("class_count") or 0)
        connection.execute(
            "UPDATE crawl_runs SET finished_at=?,summary_json=? WHERE id=?",
            (_utc_now(), _json(summary), run_id),
        )
        connection.commit()

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"database={db_path.resolve()}")
    print(f"profile={profile_path.resolve()}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
