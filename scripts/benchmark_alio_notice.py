from __future__ import annotations

import argparse
import csv
import html
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

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc, structure_job_description


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


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


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
    org = h2_values[0] if h2_values else page.org
    title_text = _clean_html_text(title.group(1)) if title else page.title
    return DetailPage(idx=page.idx, url=page.url, title=title_text or page.title, org=org)


def extract_attachments(text: str) -> list[Attachment]:
    attachments: list[Attachment] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<a[^>]+href=["\'](?P<href>[^"\']*download\.json\?fileNo=\d+[^"\']*)["\'][^>]*>(?P<label>.*?)</a>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = html.unescape(match.group("href"))
        name = _clean_html_text(match.group("label")) or "attachment"
        if href in seen:
            continue
        seen.add(href)
        attachments.append(Attachment(url=urljoin(ALIO_LIST_URL, href), name=name))
    return attachments


def choose_notice_attachment(attachments: list[Attachment]) -> Attachment | None:
    negative = ("직무", "지원서", "입사지원", "자기소개", "블라인드", "이의신청", "편의지원", "동의서")
    positive = ("공고문", "채용공고", "모집공고", "모집요강", "공고")
    for attachment in attachments:
        name = attachment.name
        if any(word in name for word in positive) and not any(word in name for word in negative):
            return attachment
    for attachment in attachments:
        name = attachment.name
        if any(word in name for word in positive):
            return attachment
    return attachments[0] if attachments else None


def safe_filename(name: str, idx: str, seq: int) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    if not cleaned:
        cleaned = f"notice_{idx}_{seq}"
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


def looks_polluted_evaluation(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    key = _norm(text)
    if not key:
        return False
    bad_markers = (
        "서류전형",
        "서류심사",
        "필기전형",
        "필기시험",
        "인적성",
        "직업기초능력평가",
        "논술",
        "전형일정",
        "원서접수",
        "접수기간",
        "접수방법",
        "제출서류",
        "응시원서",
        "입사지원서",
        "임용시기",
        "임용제청",
        "합격자",
        "불합격자",
        "개별통지",
        "채용예정일",
        "일반가점",
        "가점사항",
        "우대사항",
        "취업지원대상자",
        "장애인",
        "채용심의위원회",
        "채용점검위원회",
    )
    if any(_norm(marker) in key for marker in bad_markers):
        interview_markers = ("면접심사", "면접평가", "직무역량면접", "인성면접", "실무면접")
        return not any(_norm(marker) in key for marker in interview_markers)
    return bool(re.search(r"(?:20\d{2}\s*년|20\d{2}\s*[.\-/]\s*\d{1,2}|초순|중순|하순|예정)", text))


def benchmark_one(
    client: httpx.Client,
    page: DetailPage,
    out_dir: Path,
    max_bytes: int,
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
        "duties": 0,
        "qualifications": 0,
        "preferences": 0,
        "evaluation": 0,
        "polluted_evaluation": 0,
        "evaluation_preview": "",
        "polluted_preview": "",
        "status": "unknown",
        "error": "",
    }
    try:
        detail_html = fetch_text(client, page.url)
        page_meta = extract_detail_metadata(detail_html, page)
        row["org"] = page_meta.org
        row["title"] = page_meta.title
        attachments = extract_attachments(detail_html)
        attachment = choose_notice_attachment(attachments)
        if not attachment:
            row["status"] = "no_attachment"
            return row
        row["attachment"] = attachment.name
        out_path = out_dir / safe_filename(attachment.name, page.idx, 1)
        row["bytes"] = download_attachment(client, attachment, out_path, page.url, max_bytes=max_bytes)
        if out_path.suffix.lower() in {".zip", ".7z", ".rar"}:
            row["status"] = "unsupported_archive"
            row["error"] = "archive attachments are not part of notice parser benchmark"
            return row

        start = time.perf_counter()
        parsed = parse_with_kordoc(out_path.read_bytes(), filename=attachment.name, ocr=False)
        row["parse_ms"] = int((time.perf_counter() - start) * 1000)
        markdown = str(parsed.get("markdown") or "")
        row["markdown_len"] = len(markdown)
        structured = structure_job_description(parsed, filename=attachment.name)
        fields = structured.get("fields", {})
        evaluation = [str(x).strip() for x in (fields.get("evaluation") or []) if str(x).strip()]
        polluted = [x for x in evaluation if looks_polluted_evaluation(x)]
        row["duties"] = len(fields.get("duties") or [])
        row["qualifications"] = len(fields.get("qualifications") or [])
        row["preferences"] = len(fields.get("preferences") or [])
        row["evaluation"] = len(evaluation)
        row["polluted_evaluation"] = len(polluted)
        row["evaluation_preview"] = " / ".join(evaluation[:4])[:500]
        row["polluted_preview"] = " / ".join(polluted[:4])[:500]
        if polluted:
            row["status"] = "polluted_evaluation"
        elif evaluation:
            row["status"] = "ok"
        else:
            row["status"] = "parsed_no_evaluation"
        return row
    except (KordocParseError, httpx.HTTPError, RuntimeError, OSError) as exc:
        row["status"] = "error"
        row["error"] = str(exc)[:500]
        return row


def write_reports(rows: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"alio_notice_benchmark_{stamp}.csv"
    md_path = report_dir / f"alio_notice_benchmark_{stamp}.md"
    fields = [
        "idx",
        "status",
        "org",
        "title",
        "attachment",
        "bytes",
        "parse_ms",
        "markdown_len",
        "duties",
        "qualifications",
        "preferences",
        "evaluation",
        "polluted_evaluation",
        "evaluation_preview",
        "polluted_preview",
        "url",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    ok = sum(1 for row in rows if row.get("status") == "ok")
    no_eval = sum(1 for row in rows if row.get("status") == "parsed_no_evaluation")
    polluted = sum(1 for row in rows if row.get("status") == "polluted_evaluation")
    errors = sum(1 for row in rows if row.get("status") == "error")
    lines = [
        f"# ALIO Notice Benchmark - {stamp}",
        "",
        "Source: https://job.alio.go.kr/recruit.do",
        "",
        f"- Samples attempted: {len(rows)}",
        f"- Parsed with clean interview evaluation: {ok}",
        f"- Parsed but no interview evaluation found: {no_eval}",
        f"- Polluted interview evaluation: {polluted}",
        f"- Errors: {errors}",
        "",
        "| idx | status | attachment | eval | polluted | preview |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        attachment = str(row.get("attachment") or "").replace("|", "/")
        preview = str(row.get("evaluation_preview") or row.get("error") or "").replace("|", "/")[:160]
        lines.append(
            f"| {row.get('idx')} | {row.get('status')} | {attachment} | "
            f"{row.get('evaluation')} | {row.get('polluted_evaluation')} | {preview} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Kordoc notice parsing on recent ALIO job notices.")
    parser.add_argument("--limit", type=int, default=8, help="number of recent ALIO postings to inspect")
    parser.add_argument("--max-download-mb", type=int, default=20, help="per-attachment download limit")
    parser.add_argument("--out-dir", default=".tmp/alio_notice_benchmark", help="temporary attachment output directory")
    parser.add_argument("--report-dir", default="reports", help="report output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = max(1, int(args.max_download_mb)) * 1024 * 1024
    with httpx.Client(timeout=30) as client:
        pages = discover_detail_pages(client, max(1, int(args.limit)))
        rows = [benchmark_one(client, page, out_dir=out_dir, max_bytes=max_bytes) for page in pages]
    md_path, csv_path = write_reports(rows, Path(args.report_dir))
    print(f"wrote {md_path}")
    print(f"wrote {csv_path}")
    return 0 if not any(row.get("status") == "polluted_evaluation" for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
