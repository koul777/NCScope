from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc, structure_job_description, structure_job_notice
from app.services.ncs_mcp_client import NcsMcpError, get_ksa_by_units, search_units_by_detail, suggest_units_by_text


ALIO_LIST_URL = "https://job.alio.go.kr/recruit.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


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


def fetch_text(client: httpx.Client, url: str, referer: str = ALIO_LIST_URL) -> str:
    response = client.get(url, headers=_headers(referer), follow_redirects=True)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def discover_detail_pages(client: httpx.Client, limit: int) -> list[DetailPage]:
    text = fetch_text(client, ALIO_LIST_URL)
    seen: set[str] = set()
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


def download_attachment(
    client: httpx.Client,
    attachment: Attachment,
    out_path: Path,
    referer: str,
    max_bytes: int,
) -> int:
    response = client.get(attachment.url, headers=_headers(referer), follow_redirects=True)
    response.raise_for_status()
    data = response.content
    if len(data) > max_bytes:
        raise RuntimeError(f"attachment exceeds limit: {len(data)} > {max_bytes}")
    out_path.write_bytes(data)
    return len(data)


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
        "detail_candidates": "",
        "detail_count": 0,
        "notice_duty_chars": 0,
        "notice_eval_chars": 0,
        "notice_has_duty": False,
        "notice_has_eval": False,
        "mcp_units": 0,
        "mcp_ksa": 0,
        "mcp_suggestions": 0,
        "suggestion_top": "",
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
        if out_path.suffix.lower() in {".zip", ".7z", ".rar"}:
            row["status"] = "unsupported_archive"
            row["error"] = "archive attachments are not part of the PDF/HWP/HWPX/DOCX MVP parser scope"
            return row

        start = time.perf_counter()
        parsed = parse_with_kordoc(out_path.read_bytes(), filename=attachment.name, ocr=False)
        row["parse_ms"] = int((time.perf_counter() - start) * 1000)
        markdown = str(parsed.get("markdown") or "")
        row["markdown_len"] = len(markdown)
        structured = structure_job_description(parsed, filename=attachment.name)
        details = list(structured.get("fields", {}).get("ncs_detail_candidates") or [])
        row["detail_candidates"] = "; ".join(details)
        row["detail_count"] = len(details)
        if details and os.getenv("NCS_MCP_URL", "").strip():
            units = search_units_by_detail(details[:10], max_units=30)
            row["mcp_units"] = len(units)
            if include_ksa and units:
                ksa = get_ksa_by_units(units[:2], max_factors_per_unit=3)
                row["mcp_ksa"] = len(ksa)
            if not units:
                suggestions = suggest_units_by_text(details[:10], max_units=8)
                row["mcp_suggestions"] = len(suggestions)
                row["suggestion_top"] = "; ".join(
                    str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                    for item in suggestions[:3]
                    if str(item.get("compeUnitName") or item.get("ncsClCd") or "").strip()
                )
        if not details:
            row["status"] = "parsed_no_detail"
        elif os.getenv("NCS_MCP_URL", "").strip() and not row["mcp_units"]:
            row["status"] = "detail_no_mcp_match"
        else:
            row["status"] = "ok"
        return row
    except (KordocParseError, NcsMcpError, httpx.HTTPError, RuntimeError, OSError) as exc:
        row["status"] = "error"
        row["error"] = str(exc)[:500]
        return row


def write_reports(rows: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"alio_jd_benchmark_{stamp}.csv"
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
        "detail_count",
        "detail_candidates",
        "notice_duty_chars",
        "notice_eval_chars",
        "notice_has_duty",
        "notice_has_eval",
        "mcp_units",
        "mcp_ksa",
        "mcp_suggestions",
        "suggestion_top",
        "url",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    ok = sum(1 for row in rows if row.get("status") == "ok")
    detail_no_mcp = sum(1 for row in rows if row.get("status") == "detail_no_mcp_match")
    with_detail = ok + detail_no_mcp
    parsed = sum(1 for row in rows if row.get("status") in {"ok", "detail_no_mcp_match", "parsed_no_detail"})
    details = sum(int(row.get("detail_count") or 0) for row in rows)
    notice_duty_count = sum(1 for row in rows if row.get("notice_has_duty"))
    notice_eval_count = sum(1 for row in rows if row.get("notice_has_eval"))
    suggestion_count = sum(1 for row in rows if int(row.get("mcp_suggestions") or 0) > 0)
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
        f"- Notice pages with duty text candidates: {notice_duty_count}",
        f"- Notice pages with evaluation text candidates: {notice_eval_count}",
        f"- Detail-no-match documents with manual NCS suggestions: {suggestion_count}",
        f"- Total detail candidates: {details}",
        f"- Average parse time: {avg_parse} ms",
        f"- MCP URL configured: {bool(os.getenv('NCS_MCP_URL', '').strip())}",
        "",
        "| idx | status | attachment | parse_ms | detail candidates | notice duty chars | notice eval chars | MCP units | MCP KSA | suggestions |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        detail = str(row.get("detail_candidates") or "").replace("|", "/")
        attachment = str(row.get("attachment") or "").replace("|", "/")
        lines.append(
            f"| {row.get('idx')} | {row.get('status')} | {attachment} | "
            f"{row.get('parse_ms') or 0} | {detail} | {row.get('notice_duty_chars') or 0} | "
            f"{row.get('notice_eval_chars') or 0} | {row.get('mcp_units') or 0} | "
            f"{row.get('mcp_ksa') or 0} | {row.get('mcp_suggestions') or 0} |"
        )
    lines.extend(["", f"CSV: `{csv_path}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path


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
    md_path, csv_path = write_reports(rows, Path(args.report_dir))
    print(f"report={md_path}")
    print(f"csv={csv_path}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
