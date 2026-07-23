from __future__ import annotations

import argparse
import csv
import html
import io
import os
import re
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc, structure_job_description, structure_job_notice
from app.services.ncs_mcp_client import NcsMcpError, get_ksa_by_units, search_units_by_detail, suggest_units_by_text
from detail_gap_classifier import (
    classify_unmatched_detail_gap,
    is_healthcare_specialized_detail,
    normalize_detail_key,
)


ALIO_LIST_URL = "https://job.alio.go.kr/recruit.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
ARCHIVE_MEMBER_LIMIT = 12
SUPPORTED_ARCHIVE_DOC_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class DetailPage:
    idx: str
    url: str
    title: str = ""
    org: str = ""


@dataclass
class Attachment:
    url: str
    name: str


def _headers(referer: str = ALIO_LIST_URL) -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Referer": referer}


def _clean_html_text(value: str) -> str:
    text = html.unescape(re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _html_to_markdownish(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "\n", value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|td|th|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def fetch_text(
    client: httpx.Client,
    url: str,
    referer: str = ALIO_LIST_URL,
    params: dict[str, Any] | None = None,
) -> str:
    response = _get_with_retries(client, url, params=params, headers=_headers(referer))
    response.encoding = response.encoding or "utf-8"
    return response.text


def _get_with_retries(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.Response:
    attempts = max(1, int(os.getenv("ALIO_HTTP_RETRIES", "3") or "3"))
    delay = max(0.0, float(os.getenv("ALIO_HTTP_RETRY_DELAY_SEC", "0.4") or "0.4"))
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params, headers=headers, follow_redirects=follow_redirects)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status_code = int(exc.response.status_code)
            retryable = status_code == 429 or status_code == 408 or status_code >= 500
            if not retryable or attempt >= attempts - 1:
                raise
        except (httpx.RequestError, OSError):
            if attempt >= attempts - 1:
                raise
        if delay:
            time.sleep(delay * (2 ** attempt))
    raise RuntimeError(f"failed to fetch after {attempts} attempts: {url}")


def _extract_detail_pages_from_list_html(text: str, seen: set[str], limit: int) -> list[DetailPage]:
    pages: list[DetailPage] = []
    for match in re.finditer(
        r'<a[^>]+href=["\'](?P<href>[^"\']*recruitview\.do\?idx=(?P<idx>\d+)[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        idx = match.group("idx")
        if idx in seen:
            continue
        seen.add(idx)
        pages.append(
            DetailPage(
                idx=idx,
                url=urljoin(ALIO_LIST_URL, html.unescape(match.group("href"))),
                title=_clean_html_text(match.group("label")),
            )
        )
        if len(pages) >= limit:
            break
    return pages


def discover_detail_pages(client: httpx.Client, limit: int) -> list[DetailPage]:
    limit = max(1, int(limit))
    seen: set[str] = set()
    pages: list[DetailPage] = []
    page_no = 1
    max_empty_pages = max(1, int(os.getenv("ALIO_DISCOVER_EMPTY_PAGE_LIMIT", "3") or "3"))
    max_pages = max(1, int(os.getenv("ALIO_DISCOVER_MAX_PAGES", "200") or "200"))
    empty_pages = 0
    while len(pages) < limit and page_no <= max_pages and empty_pages < max_empty_pages:
        params = {"pageNo": page_no}
        text = fetch_text(client, ALIO_LIST_URL, params=params)
        new_pages = _extract_detail_pages_from_list_html(text, seen, limit - len(pages))
        if new_pages:
            pages.extend(new_pages)
            empty_pages = 0
        else:
            empty_pages += 1
        page_no += 1
        time.sleep(0.2)
    return pages


def extract_detail_metadata(text: str, page: DetailPage) -> DetailPage:
    h2_values = [
        _clean_html_text(match.group(1))
        for match in re.finditer(r"<h2[^>]*>\s*(.*?)\s*</h2>", text, flags=re.IGNORECASE | re.DOTALL)
    ]
    h2_values = [value for value in h2_values if value and value not in {"홈페이지 주메뉴"}]
    title = re.search(r'<p[^>]*class=["\'][^"\']*titleH2[^"\']*["\'][^>]*>(.*?)</p>', text, flags=re.IGNORECASE | re.DOTALL)
    # JOB-ALIO pages usually put organization in h2 and posting title in a nearby text node.
    org = h2_values[0] if h2_values else page.org
    title_text = _clean_html_text(title.group(1)) if title else page.title
    if not title_text:
        body_title = re.search(r"<h2[^>]*>.*?</h2>\s*.*?<p[^>]*>\s*(.*?)\s*</p>", text, flags=re.IGNORECASE | re.DOTALL)
        title_text = _clean_html_text(body_title.group(1)) if body_title else page.title
    return DetailPage(idx=page.idx, url=page.url, title=title_text or page.title, org=org)


def extract_jd_attachments(text: str) -> list[Attachment]:
    attachments: list[Attachment] = []
    seen: set[str] = set()
    row_match = re.search(
        r"<tr[^>]*>\s*<th[^>]*>\s*직무기술서\s*</th>(?P<body>.*?)</tr>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    search_area = row_match.group("body") if row_match else text
    for match in re.finditer(
        r'<a[^>]+href=["\'](?P<href>[^"\']*download\.json\?fileNo=\d+[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        search_area,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = html.unescape(match.group("href"))
        name = _clean_html_text(match.group("label")) or "job_description"
        if href in seen:
            continue
        seen.add(href)
        attachments.append(Attachment(url=urljoin(ALIO_LIST_URL, href), name=name))
    return attachments


def safe_filename(name: str, idx: str, seq: int) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    if not cleaned:
        cleaned = f"job_description_{idx}_{seq}"
    if not re.search(r"\.[A-Za-z0-9]{2,5}$", cleaned):
        cleaned += ".bin"
    return f"{idx}_{seq}_{cleaned}"


def _suffix_of(name: str) -> str:
    return Path(str(name or "").replace("\\", "/")).suffix.lower()


def _safe_member_label(name: str) -> str:
    value = str(name or "").replace("\\", "/").split("/")[-1].strip()
    value = re.sub(r"[\r\n\t]+", " ", value)
    return value[:160] or "archive_member"


def parse_benchmark_document(data: bytes, filename: str, max_bytes: int) -> dict[str, Any]:
    suffix = _suffix_of(filename)
    if suffix == ".txt":
        return {"markdown": data.decode("utf-8", errors="ignore"), "metadata": {"filename": filename}}
    if suffix != ".zip":
        return parse_with_kordoc(data, filename=filename, ocr=False)

    chunks: list[str] = []
    members: list[dict[str, str]] = []
    warnings: list[str] = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_suffix = _suffix_of(info.filename)
                if member_suffix not in SUPPORTED_ARCHIVE_DOC_SUFFIXES:
                    continue
                total_uncompressed += int(info.file_size or 0)
                if total_uncompressed > max_bytes:
                    raise KordocParseError(f"archive contents exceed limit: {total_uncompressed} > {max_bytes}")
                if len(members) >= ARCHIVE_MEMBER_LIMIT:
                    warnings.append(f"archive member limit reached: {ARCHIVE_MEMBER_LIMIT}")
                    break
                member_label = _safe_member_label(info.filename)
                if info.flag_bits & 0x1:
                    warnings.append(f"{member_label}: encrypted ZIP member is not supported")
                    continue
                try:
                    member_bytes = archive.read(info)
                except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                    warnings.append(f"{member_label}: ZIP member could not be read: {exc}")
                    continue
                try:
                    if member_suffix == ".txt":
                        parsed = {
                            "markdown": member_bytes.decode("utf-8", errors="ignore"),
                            "metadata": {"filename": member_label},
                        }
                    else:
                        parsed = parse_with_kordoc(
                            member_bytes,
                            filename=member_label,
                            ocr=member_suffix in SUPPORTED_IMAGE_SUFFIXES,
                        )
                except KordocParseError as exc:
                    warnings.append(f"{member_label}: {exc}")
                    continue
                markdown = str(parsed.get("markdown") or "").strip()
                if not markdown:
                    warnings.append(f"{member_label}: empty parse result")
                    continue
                members.append({"filename": member_label, "suffix": member_suffix})
                chunks.append(f"# ZIP member: {member_label}\n\n{markdown}")
    except zipfile.BadZipFile as exc:
        raise KordocParseError("not a readable ZIP archive") from exc
    if not chunks:
        raise KordocParseError("ZIP contains no parseable PDF/HWP/HWPX/DOCX/TXT/image files")
    return {
        "markdown": "\n\n---\n\n".join(chunks),
        "metadata": {"filename": filename, "archive": True, "members": members},
        "warnings": warnings,
    }


def download_attachment(
    client: httpx.Client,
    attachment: Attachment,
    out_path: Path,
    referer: str,
    max_bytes: int,
) -> int:
    response = _get_with_retries(client, attachment.url, headers=_headers(referer))
    data = response.content
    if len(data) > max_bytes:
        raise RuntimeError(f"attachment exceeds limit: {len(data)} > {max_bytes}")
    out_path.write_bytes(data)
    return len(data)


def _unit_key(unit: dict[str, Any]) -> str:
    return str(unit.get("ncsClCd") or unit.get("ncs_cl_cd") or unit.get("compeUnitCode") or "").strip()


def _detail_key(value: Any) -> str:
    return normalize_detail_key(value)


def _mcp_url_configured() -> bool:
    return bool(os.getenv("NCS_MCP_URL", "").strip())


def _is_healthcare_specialized_detail(value: Any) -> bool:
    return is_healthcare_specialized_detail(value)


def _dedup_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        if not isinstance(unit, dict):
            continue
        key = _unit_key(unit) or str(unit.get("compeUnitName") or unit).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(unit)
    return out


def detail_member_map(parsed: dict[str, Any], fallback_member: str, details: list[str]) -> dict[str, str]:
    """Map extracted detail candidates to the ZIP member or attachment they came from."""

    mapping: dict[str, list[str]] = {}

    def add(detail: Any, member: str) -> None:
        key = _detail_key(detail)
        label = str(member or fallback_member or "").strip()
        if not key or not label:
            return
        mapping.setdefault(key, [])
        if label not in mapping[key]:
            mapping[key].append(label)

    markdown = str(parsed.get("markdown") or "")
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    if metadata.get("archive"):
        pattern = re.compile(
            r"(?ms)^# ZIP member: (?P<member>.+?)\n\n(?P<body>.*?)(?=\n\n---\n\n# ZIP member: |\Z)"
        )
        for match in pattern.finditer(markdown):
            member = match.group("member").strip()
            body = match.group("body")
            structured = structure_job_description({"markdown": body}, filename=member)
            for detail in structured.get("fields", {}).get("ncs_detail_candidates") or []:
                add(detail, member)
    else:
        for detail in details:
            add(detail, fallback_member)

    return {key: "; ".join(values) for key, values in mapping.items()}


def diagnose_detail_mcp_matches(
    details: list[str],
    max_units_per_detail: int = 20,
    max_suggestions_per_detail: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return per-detail MCP diagnostics plus unique exact units.

    Document-level success can hide partial failures in multi-duty ZIP files.
    This diagnostic keeps each extracted 세분류 visible in the benchmark output.
    """

    detail_rows: list[dict[str, Any]] = []
    exact_units: list[dict[str, Any]] = []

    def join_unique(values: list[Any]) -> str:
        return "; ".join(
            dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip())
        )

    for seq, detail in enumerate(details, start=1):
        term = str(detail or "").strip()
        if not term:
            continue
        units = search_units_by_detail([term], max_units=max_units_per_detail)
        exact_units.extend(units)
        suggestions: list[dict[str, Any]] = []
        if not units:
            suggestions = suggest_units_by_text([term], max_units=max_suggestions_per_detail)
        canonical_detail_matches = [
            item
            for item in suggestions
            if item.get("isExactDetailMatch")
            or _detail_key(item.get("canonicalDetailName") or item.get("ncsSubdCdnm")) == _detail_key(term)
        ]
        unit_name_matches = [item for item in suggestions if item.get("isExactUnitNameMatch")]
        if units:
            review_action = "auto_exact_detail"
            review_reason = "MCP exact detail search returned official NCS units."
            match_diagnostic = "exact_detail"
        else:
            gap = classify_unmatched_detail_gap(
                term,
                suggestions=suggestions,
                canonical_detail_matches=canonical_detail_matches,
                unit_name_matches=unit_name_matches,
            )
            review_action = gap["review_action"]
            review_reason = gap["review_reason"]
            match_diagnostic = gap["match_diagnostic"]
        unit_name_parent_details = join_unique(
            [item.get("canonicalDetailName") or item.get("ncsSubdCdnm") for item in unit_name_matches]
        )
        canonical_match_details = join_unique(
            [item.get("canonicalDetailName") or item.get("ncsSubdCdnm") for item in canonical_detail_matches]
        )
        exact_canonical_details = join_unique(
            [item.get("resolvedDetailName") or item.get("ncsSubdCdnm") for item in units]
        )
        resolved_parent_detail = unit_name_parent_details
        if not resolved_parent_detail and canonical_match_details:
            resolved_parent_detail = canonical_match_details
        if not resolved_parent_detail and exact_canonical_details:
            resolved_parent_detail = exact_canonical_details
        detail_rows.append(
            {
                "detail_seq": seq,
                "detail": term,
                "exact_match": bool(units),
                "exact_units": len(units),
                "exact_top": "; ".join(
                    str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                    for item in units[:3]
                    if str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                ),
                "exact_sources": "; ".join(
                    sorted(
                        {
                            str(item.get("source") or "").strip()
                            for item in units
                            if str(item.get("source") or "").strip()
                        }
                    )
                ),
                "exact_canonical_details": exact_canonical_details,
                "suggestion_count": len(suggestions),
                "top_suggestion": "; ".join(
                    str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                    for item in suggestions[:3]
                    if str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                ),
                "suggestion_codes": "; ".join(
                    str(item.get("ncsClCd") or "").strip()
                    for item in suggestions[:3]
                    if str(item.get("ncsClCd") or "").strip()
                ),
                "suggestion_canonical_details": "; ".join(
                    str(item.get("canonicalDetailName") or item.get("ncsSubdCdnm") or "").strip()
                    for item in suggestions[:3]
                    if str(item.get("canonicalDetailName") or item.get("ncsSubdCdnm") or "").strip()
                ),
                "canonical_detail_match": bool(canonical_detail_matches),
                "canonical_detail_match_top": canonical_match_details,
                "unit_name_match": bool(unit_name_matches),
                "unit_name_match_top": "; ".join(
                    str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                    for item in unit_name_matches[:3]
                    if str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                ),
                "unit_name_parent_details": unit_name_parent_details,
                "resolved_parent_detail": resolved_parent_detail,
                "match_diagnostic": match_diagnostic,
                "review_action": review_action,
                "review_reason": review_reason,
            }
        )
    return detail_rows, _dedup_units(exact_units)


def summarize_detail_mcp_coverage(detail_rows: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    exact_match_count = 0
    unit_name_match_count = 0
    uncovered: list[str] = []
    for detail_row in detail_rows:
        detail = str(detail_row.get("detail") or "").strip()
        has_exact = int(detail_row.get("exact_units") or 0) > 0
        has_unit_name = bool(detail_row.get("unit_name_match"))
        if has_exact:
            exact_match_count += 1
        elif has_unit_name:
            unit_name_match_count += 1
        elif detail:
            uncovered.append(detail)
    return exact_match_count, unit_name_match_count, uncovered


def no_detail_category(absence_reason: Any) -> str:
    reason = str(absence_reason or "").strip()
    if not reason:
        return ""
    if reason == "no_ncs_mapping_declared":
        return "declared_no_ncs_mapping"
    if reason in {
        "job_document_without_explicit_ncs_detail",
        "translation_role_without_explicit_ncs_detail",
        "multi_role_healthcare_document_without_explicit_ncs_detail",
    }:
        return "no_explicit_ncs_detail"
    if reason in {
        "ncs_detail_cell_blank_or_dash",
        "ncs_detail_candidate_filtered",
        "ncs_detail_header_without_candidate",
        "ncs_table_without_detail_header",
    }:
        return "ncs_table_without_extractable_detail"
    return "other_no_detail"


def benchmark_one(
    client: httpx.Client,
    page: DetailPage,
    out_dir: Path,
    max_bytes: int,
    include_ksa: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "idx": page.idx,
        "url": page.url,
        "org": page.org,
        "title": page.title,
        "attachment": "",
        "bytes": 0,
        "parse_ms": 0,
        "markdown_len": 0,
        "archive_members": 0,
        "detail_candidates": "",
        "detail_count": 0,
        "no_detail_category": "",
        "ncs_detail_absence_reason": "",
        "ncs_detail_absence_state": "",
        "ncs_detail_absence_evidence": "",
        "ncs_detail_absence_filtered_candidate_reason": "",
        "ncs_detail_absence_saw_ncs_table": False,
        "ncs_detail_absence_saw_detail_header": False,
        "ncs_detail_absence_blank_or_dash_detail_cell": False,
        "ncs_detail_absence_declared_no_mapping": False,
        "notice_duty_chars": 0,
        "notice_eval_chars": 0,
        "notice_has_duty": False,
        "notice_has_eval": False,
        "mcp_units": 0,
        "mcp_ksa": 0,
        "mcp_suggestions": 0,
        "mcp_configured": _mcp_url_configured(),
        "suggestion_top": "",
        "detail_exact_match_count": 0,
        "detail_unit_name_match_count": 0,
        "detail_unmatched_count": 0,
        "detail_partial_match": False,
        "detail_unmatched_candidates": "",
        "detail_diagnostics_skipped_reason": "",
        "mcp_error": "",
        "_detail_rows": [],
        "status": "unknown",
        "error": "",
    }
    try:
        detail_html = fetch_text(client, page.url)
        page_meta = extract_detail_metadata(detail_html, page)
        row["org"] = page_meta.org
        row["title"] = page_meta.title
        notice_review = structure_job_notice({"markdown": _html_to_markdownish(detail_html)}, filename=f"alio-{page.idx}.html")
        notice_fields = notice_review.get("fields", {}) if isinstance(notice_review.get("fields"), dict) else {}
        notice_duty = str(notice_fields.get("duty_text") or "").strip()
        notice_eval = str(notice_fields.get("evaluation_text") or "").strip()
        row["notice_duty_chars"] = len(notice_duty)
        row["notice_eval_chars"] = len(notice_eval)
        row["notice_has_duty"] = bool(notice_duty)
        row["notice_has_eval"] = bool(notice_eval)
        attachments = extract_jd_attachments(detail_html)
        if not attachments:
            row["status"] = "no_jd_attachment"
            return row
        attachment = attachments[0]
        row["attachment"] = attachment.name
        out_path = out_dir / safe_filename(attachment.name, page.idx, 1)
        row["bytes"] = download_attachment(client, attachment, out_path, page.url, max_bytes=max_bytes)

        start = time.perf_counter()
        parsed = parse_benchmark_document(out_path.read_bytes(), filename=attachment.name, max_bytes=max_bytes)
        row["parse_ms"] = int((time.perf_counter() - start) * 1000)
        row["archive_members"] = len((parsed.get("metadata") or {}).get("members") or [])
        markdown = str(parsed.get("markdown") or "")
        row["markdown_len"] = len(markdown)
        structured = structure_job_description(parsed, filename=attachment.name)
        fields = structured.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        details = list(fields.get("ncs_detail_candidates") or [])
        row["ncs_detail_absence_reason"] = str(fields.get("ncs_detail_absence_reason") or "").strip()
        row["no_detail_category"] = no_detail_category(row["ncs_detail_absence_reason"])
        row["ncs_detail_absence_state"] = str(fields.get("ncs_detail_absence_state") or "").strip()
        row["ncs_detail_absence_evidence"] = str(fields.get("ncs_detail_absence_evidence") or "").strip()
        row["ncs_detail_absence_filtered_candidate_reason"] = str(
            fields.get("ncs_detail_absence_filtered_candidate_reason") or ""
        ).strip()
        row["ncs_detail_absence_saw_ncs_table"] = bool(fields.get("ncs_detail_absence_saw_ncs_table"))
        row["ncs_detail_absence_saw_detail_header"] = bool(fields.get("ncs_detail_absence_saw_detail_header"))
        row["ncs_detail_absence_blank_or_dash_detail_cell"] = bool(
            fields.get("ncs_detail_absence_blank_or_dash_detail_cell")
        )
        row["ncs_detail_absence_declared_no_mapping"] = bool(fields.get("ncs_detail_absence_declared_no_mapping"))
        detail_evidence_map = {
            _detail_key(item.get("detail")): item
            for item in (fields.get("ncs_detail_candidate_evidence") or [])
            if isinstance(item, dict) and str(item.get("detail") or "").strip()
        }
        detail_members = detail_member_map(parsed, fallback_member=attachment.name, details=details)
        row["detail_candidates"] = "; ".join(details)
        row["detail_count"] = len(details)
        if details and row["mcp_configured"]:
            try:
                detail_rows, units = diagnose_detail_mcp_matches(details[:20])
            except NcsMcpError as exc:
                row["mcp_error"] = str(exc)[:500]
                detail_rows, units = [], []
            for detail_row in detail_rows:
                detail_row["idx"] = page.idx
                detail_row["attachment"] = attachment.name
                detail_row["member"] = detail_members.get(_detail_key(detail_row.get("detail")), "")
                evidence = detail_evidence_map.get(_detail_key(detail_row.get("detail"))) or {}
                detail_row["extraction_source"] = evidence.get("source", "")
                detail_row["extraction_page"] = evidence.get("page", "")
                detail_row["extraction_line"] = evidence.get("line", "")
                detail_row["extraction_snippet"] = evidence.get("snippet", "")
            row["_detail_rows"] = detail_rows
            exact_match_count, unit_name_match_count, unmatched = summarize_detail_mcp_coverage(detail_rows)
            row["detail_exact_match_count"] = exact_match_count
            row["detail_unit_name_match_count"] = unit_name_match_count
            row["detail_unmatched_count"] = len(unmatched)
            row["detail_partial_match"] = bool(unmatched and (exact_match_count or unit_name_match_count))
            row["detail_unmatched_candidates"] = "; ".join(unmatched)
            row["mcp_units"] = len(units)
            if include_ksa and units:
                ksa = get_ksa_by_units(units[:2], max_factors_per_unit=3)
                row["mcp_ksa"] = len(ksa)
            row["mcp_suggestions"] = sum(
                1 for detail_row in detail_rows if int(detail_row.get("suggestion_count") or 0) > 0
            )
            row["suggestion_top"] = "; ".join(
                f"{detail_row.get('detail')}: {detail_row.get('top_suggestion')}"
                for detail_row in detail_rows
                if str(detail_row.get("top_suggestion") or "").strip()
            )[:500]
        elif details:
            row["detail_diagnostics_skipped_reason"] = "NCS_MCP_URL not configured"
        if not details:
            row["status"] = "parsed_no_detail"
        elif not row["mcp_configured"]:
            row["status"] = "mcp_not_configured"
        elif row.get("mcp_error"):
            row["status"] = "mcp_error"
        elif row.get("detail_partial_match"):
            row["status"] = "partial_detail_mcp_match"
        elif row.get("detail_unit_name_match_count") and int(row.get("detail_unmatched_count") or 0) == 0:
            row["status"] = "ok_unit_name_resolved"
        elif row["mcp_configured"] and not row["mcp_units"] and not row.get("detail_unit_name_match_count"):
            row["status"] = "detail_no_mcp_match"
        else:
            row["status"] = "ok"
        return row
    except (KordocParseError, NcsMcpError, httpx.HTTPError, RuntimeError, OSError) as exc:
        row["status"] = "error"
        row["error"] = str(exc)[:500]
        return row


def write_reports(rows: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"alio_jd_benchmark_{stamp}.csv"
    detail_csv_path = report_dir / f"alio_jd_detail_diagnostics_{stamp}.csv"
    md_path = report_dir / f"alio_jd_benchmark_{stamp}.md"
    fields = [
        "idx",
        "status",
        "org",
        "title",
        "attachment",
        "bytes",
        "parse_ms",
        "markdown_len",
        "archive_members",
        "detail_count",
        "detail_candidates",
        "no_detail_category",
        "ncs_detail_absence_reason",
        "ncs_detail_absence_state",
        "ncs_detail_absence_evidence",
        "ncs_detail_absence_filtered_candidate_reason",
        "ncs_detail_absence_saw_ncs_table",
        "ncs_detail_absence_saw_detail_header",
        "ncs_detail_absence_blank_or_dash_detail_cell",
        "ncs_detail_absence_declared_no_mapping",
        "notice_duty_chars",
        "notice_eval_chars",
        "notice_has_duty",
        "notice_has_eval",
        "mcp_units",
        "mcp_ksa",
        "mcp_suggestions",
        "mcp_configured",
        "suggestion_top",
        "detail_exact_match_count",
        "detail_unit_name_match_count",
        "detail_unmatched_count",
        "detail_partial_match",
        "detail_unmatched_candidates",
        "detail_diagnostics_skipped_reason",
        "mcp_error",
        "url",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output_row = {field: row.get(field, "") for field in fields}
            if not output_row.get("no_detail_category"):
                output_row["no_detail_category"] = no_detail_category(row.get("ncs_detail_absence_reason"))
            writer.writerow(output_row)

    detail_fields = [
        "idx",
        "attachment",
        "member",
        "detail_seq",
        "detail",
        "extraction_source",
        "extraction_page",
        "extraction_line",
        "extraction_snippet",
        "exact_match",
        "exact_units",
        "exact_top",
        "exact_sources",
        "exact_canonical_details",
        "suggestion_count",
        "top_suggestion",
        "suggestion_codes",
        "suggestion_canonical_details",
        "canonical_detail_match",
        "canonical_detail_match_top",
        "unit_name_match",
        "unit_name_match_top",
        "unit_name_parent_details",
        "resolved_parent_detail",
        "match_diagnostic",
        "review_action",
        "review_reason",
    ]
    with detail_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        for row in rows:
            for detail_row in row.get("_detail_rows") or []:
                writer.writerow({field: detail_row.get(field, "") for field in detail_fields})

    ok = sum(1 for row in rows if row.get("status") == "ok")
    detail_no_mcp = sum(1 for row in rows if row.get("status") == "detail_no_mcp_match")
    mcp_error_count = sum(1 for row in rows if row.get("status") == "mcp_error")
    mcp_not_configured_count = sum(1 for row in rows if row.get("status") == "mcp_not_configured")
    with_detail = sum(1 for row in rows if int(row.get("detail_count") or 0) > 0 and row.get("status") != "error")
    parsed = sum(
        1
        for row in rows
        if row.get("status") in {
            "ok",
            "ok_unit_name_resolved",
            "partial_detail_mcp_match",
            "detail_no_mcp_match",
            "parsed_no_detail",
            "mcp_error",
            "mcp_not_configured",
        }
    )
    details = sum(int(row.get("detail_count") or 0) for row in rows)
    notice_duty_count = sum(1 for row in rows if row.get("notice_has_duty"))
    notice_eval_count = sum(1 for row in rows if row.get("notice_has_eval"))
    suggestion_count = sum(1 for row in rows if int(row.get("mcp_suggestions") or 0) > 0)
    partial_match_count = sum(1 for row in rows if row.get("detail_partial_match"))
    unit_name_resolved_count = sum(1 for row in rows if row.get("status") == "ok_unit_name_resolved")
    unit_name_detail_count = sum(int(row.get("detail_unit_name_match_count") or 0) for row in rows)
    unmatched_detail_count = sum(int(row.get("detail_unmatched_count") or 0) for row in rows)
    diagnostics_skipped_detail_count = sum(
        int(row.get("detail_count") or 0)
        for row in rows
        if row.get("status") == "mcp_not_configured"
    )
    match_diagnostics = Counter(
        str(detail_row.get("match_diagnostic") or "unspecified")
        for row in rows
        for detail_row in row.get("_detail_rows") or []
    )
    if diagnostics_skipped_detail_count:
        match_diagnostics["not_evaluated"] += diagnostics_skipped_detail_count
    match_diagnostic_text = "; ".join(
        f"{name}={count}" for name, count in sorted(match_diagnostics.items())
    ) or "none"
    parsed_no_detail_reasons = Counter(
        str(row.get("ncs_detail_absence_reason") or "unspecified")
        for row in rows
        if row.get("status") == "parsed_no_detail"
    )
    parsed_no_detail_reason_text = "; ".join(
        f"{reason}={count}" for reason, count in sorted(parsed_no_detail_reasons.items())
    ) or "none"
    parsed_no_detail_categories = Counter(
        str(row.get("no_detail_category") or no_detail_category(row.get("ncs_detail_absence_reason")) or "unspecified")
        for row in rows
        if row.get("status") == "parsed_no_detail"
    )
    parsed_no_detail_category_text = "; ".join(
        f"{category}={count}" for category, count in sorted(parsed_no_detail_categories.items())
    ) or "none"
    parsed_no_detail_states = Counter(
        str(row.get("ncs_detail_absence_state") or "unspecified")
        for row in rows
        if row.get("status") == "parsed_no_detail"
    )
    parsed_no_detail_state_text = "; ".join(
        f"[{state.replace('; ', ' + ')}]={count}" for state, count in sorted(parsed_no_detail_states.items())
    ) or "none"
    avg_parse = int(sum(int(row.get("parse_ms") or 0) for row in rows if row.get("parse_ms")) / max(1, parsed))
    lines = [
        f"# ALIO JD Benchmark - {stamp}",
        "",
        "Source: https://job.alio.go.kr/recruit.do",
        "",
        f"- Samples attempted: {len(rows)}",
        f"- Parsed documents: {parsed}",
        f"- Documents with detail candidates: {with_detail}",
        f"- Documents with detail candidates but no MCP match: {detail_no_mcp}",
        f"- Documents with detail candidates skipped because MCP URL is not configured: {mcp_not_configured_count}",
        f"- Documents with MCP connection errors: {mcp_error_count}",
        f"- Notice pages with duty text candidates: {notice_duty_count}",
        f"- Notice pages with evaluation text candidates: {notice_eval_count}",
        f"- Detail-no-match documents with manual NCS suggestions: {suggestion_count}",
        f"- Total detail candidates: {details}",
        f"- Documents with unit-name detail recovery: {unit_name_resolved_count}",
        f"- Unit-name recovered detail labels: {unit_name_detail_count}",
        f"- Documents with partial detail MCP matches: {partial_match_count}",
        f"- Unmatched detail candidates: {unmatched_detail_count}",
        f"- Detail candidates with diagnostics skipped because MCP URL is not configured: {diagnostics_skipped_detail_count}",
        f"- Detail match diagnostic counts: {match_diagnostic_text}",
        f"- Parsed-no-detail category counts: {parsed_no_detail_category_text}",
        f"- Parsed-no-detail reason counts: {parsed_no_detail_reason_text}",
        f"- Parsed-no-detail state counts: {parsed_no_detail_state_text}",
        f"- Average parse time: {avg_parse} ms",
        f"- MCP URL configured: {bool(os.getenv('NCS_MCP_URL', '').strip())}",
        "",
        "| idx | status | attachment | parse_ms | archive files | detail candidates | no-detail category | no-detail reason | no-detail state | MCP configured | diagnostics skip reason | exact details | unit-name details | unmatched details | notice duty chars | notice eval chars | MCP units | MCP KSA | suggestions |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        detail = str(row.get("detail_candidates") or "").replace("|", "/")
        absence_category = str(
            row.get("no_detail_category") or no_detail_category(row.get("ncs_detail_absence_reason"))
        ).replace("|", "/")
        absence_reason = str(row.get("ncs_detail_absence_reason") or "").replace("|", "/")
        absence_state = str(row.get("ncs_detail_absence_state") or "").replace("|", "/")
        attachment = str(row.get("attachment") or "").replace("|", "/")
        diagnostics_skip = str(row.get("detail_diagnostics_skipped_reason") or "").replace("|", "/")
        lines.append(
            f"| {row.get('idx')} | {row.get('status')} | {attachment} | "
            f"{row.get('parse_ms') or 0} | {row.get('archive_members') or 0} | {detail} | "
            f"{absence_category} | {absence_reason} | {absence_state} | "
            f"{bool(row.get('mcp_configured'))} | {diagnostics_skip} | "
            f"{row.get('detail_exact_match_count') or 0} | "
            f"{row.get('detail_unit_name_match_count') or 0} | "
            f"{row.get('detail_unmatched_count') or 0} | "
            f"{row.get('notice_duty_chars') or 0} | "
            f"{row.get('notice_eval_chars') or 0} | {row.get('mcp_units') or 0} | "
            f"{row.get('mcp_ksa') or 0} | {row.get('mcp_suggestions') or 0} |"
        )
    lines.extend(["", f"CSV: `{csv_path}`", f"Detail diagnostics CSV: `{detail_csv_path}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path, detail_csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Kordoc/NCS_MCP on recent ALIO job descriptions.")
    parser.add_argument("--limit", type=int, default=5, help="number of recent ALIO postings to inspect")
    parser.add_argument("--max-download-mb", type=int, default=20, help="per-attachment download limit")
    parser.add_argument("--include-ksa", action="store_true", help="also fetch official KSA for top MCP units")
    parser.add_argument("--out-dir", default=".tmp/alio_jd_benchmark", help="temporary attachment output directory")
    parser.add_argument("--report-dir", default="reports", help="report output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    max_bytes = max(1, int(args.max_download_mb)) * 1024 * 1024
    with httpx.Client(timeout=45.0) as client:
        pages = discover_detail_pages(client, max(1, int(args.limit)))
        for page in pages:
            rows.append(benchmark_one(client, page, out_dir, max_bytes=max_bytes, include_ksa=bool(args.include_ksa)))
            time.sleep(0.3)
    md_path, csv_path, detail_csv_path = write_reports(rows, Path(args.report_dir))
    print(f"report={md_path}")
    print(f"csv={csv_path}")
    print(f"detail_csv={detail_csv_path}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
