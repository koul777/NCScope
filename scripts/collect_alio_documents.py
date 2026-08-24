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
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

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
        CREATE TABLE IF NOT EXISTS crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            options_json TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    connection.commit()


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
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> int:
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
    parser.add_argument("--out-dir", default=".local/alio_corpus")
    args = parser.parse_args()

    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "alio_corpus.sqlite3"
    profile_path = out_dir / "sclass_profiles.json"
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
        profile = write_profile(connection, profile_path)
        summary = _summary(connection)
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
