from __future__ import annotations

import argparse
import csv
import io
import os
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc  # noqa: E402


NCS_HOST = "https://www.ncs.go.kr"
INTERVIEW_LIST_URL = f"{NCS_HOST}/blind/rh13/bbs_lib_list.do?libDstinCd=49&menuId=MN02020303"
VIEW_URL = f"{NCS_HOST}/blind/rh13/bbs_lib_view.do"
DOWNLOAD_URL = f"{NCS_HOST}/common/file/downloadFile.do"
SUPPORTED_DOC_SUFFIXES = {".hwp", ".hwpx", ".docx", ".pdf", ".txt"}
METHOD_NAMES = ("경험면접", "상황면접", "발표면접", "토론면접", "인바스켓면접", "직무지식면접")


@dataclass
class OfficialSampleEntry:
    seq: str
    title: str
    file_mstky: str
    filedetl_seq: str
    filename: str


def _curl_bytes(url: str, post_data: dict[str, str] | None = None, timeout_sec: int = 60) -> bytes:
    curl = "curl.exe" if os.name == "nt" else "curl"
    cmd = [curl, "-L", "-s", "--max-time", str(timeout_sec)]
    if post_data:
        cmd.extend(["-X", "POST", "-d", urlencode(post_data)])
    cmd.append(url)
    completed = subprocess.run(cmd, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or f"curl failed with exit code {completed.returncode}")
    return completed.stdout


def _decode_html(data: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def discover_official_samples(limit: int, list_url: str = INTERVIEW_LIST_URL) -> list[OfficialSampleEntry]:
    html = _decode_html(_curl_bytes(list_url))
    row_pattern = re.compile(r"<tr>\s*<td>(?P<num>\d+)</td>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
    entries: list[OfficialSampleEntry] = []
    for row in row_pattern.finditer(html):
        body = row.group("body")
        view = re.search(
            r"fn_view\('(?P<seq>\d+)'\).*?title=\"(?P<title>.*?)\"",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        file_call = re.search(
            r"gfn_file_downloadFile\('(?P<sys>[^']+)','(?P<mst>[^']+)','(?P<detl>[^']+)'",
            body,
            flags=re.IGNORECASE,
        )
        if not view or not file_call:
            continue
        title = _clean_html_text(view.group("title"))
        filename = title
        entries.append(
            OfficialSampleEntry(
                seq=view.group("seq"),
                title=title,
                file_mstky=file_call.group("mst"),
                filedetl_seq=file_call.group("detl"),
                filename=filename,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def fetch_sample_view_text(entry: OfficialSampleEntry) -> str:
    html = _decode_html(
        _curl_bytes(
            VIEW_URL,
            {
                "libDstinCd": "49",
                "menuId": "MN02020303",
                "libSeq": entry.seq,
            },
        )
    )
    match = re.search(r"<span id=\"iframeBbsNtcSource\"[^>]*>(?P<body>.*?)</span>", html, flags=re.IGNORECASE | re.DOTALL)
    return _clean_html_text(match.group("body")) if match else ""


def download_sample_archive(entry: OfficialSampleEntry, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", entry.title).strip(" .")[:120] or entry.seq
    out_path = out_dir / f"{entry.seq}_{safe_title}.zip"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    data = _curl_bytes(
        DOWNLOAD_URL,
        {
            "sysDstinCd": "01",
            "fileMstky": entry.file_mstky,
            "filedetlSeq": entry.filedetl_seq,
            "downlDstinCd": "09",
        },
        timeout_sec=120,
    )
    out_path.write_bytes(data)
    return out_path


def _method_from_name(filename: str) -> str:
    name = str(filename or "")
    for short, method in (
        ("경험", "경험면접"),
        ("상황", "상황면접"),
        ("발표", "발표면접"),
        ("토론", "토론면접"),
        ("인바스켓", "인바스켓면접"),
        ("직무지식", "직무지식면접"),
    ):
        if short in name:
            return method
    return ""


def _artifact_type(filename: str) -> str:
    if "평가" in filename:
        return "evaluation_form"
    if "과제" in filename or "문항" in filename:
        return "task"
    return "other"


def _extract_terms(text: str, limit: int = 8) -> list[str]:
    cleaned_text = _clean_html_text(text)
    candidates: list[str] = []
    for pattern in (r"평가\s*요소\s*[:：]?\s*([^\n\r]{2,80})", r"평가\s*항목\s*[:：]?\s*([^\n\r]{2,80})", r"역량\s*[:：]?\s*([^\n\r]{2,80})"):
        for match in re.finditer(pattern, cleaned_text):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" -:：|")
            if value and value not in candidates:
                candidates.append(value[:80])
            if len(candidates) >= limit:
                return candidates
    for token in ("평가요소", "평가항목", "면접과제", "평가양식", "응시자", "면접위원", "채점"):
        if token in cleaned_text and token not in candidates:
            candidates.append(token)
    return candidates[:limit]


def profile_sample_archive(archive_path: Path, max_members: int = 16) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist()[:max_members]:
                if info.is_dir():
                    continue
                suffix = Path(info.filename).suffix.lower()
                if suffix not in SUPPORTED_DOC_SUFFIXES:
                    continue
                try:
                    data = archive.read(info)
                except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                    warnings.append(f"{info.filename}: read failed: {exc}")
                    continue
                try:
                    if suffix == ".txt":
                        markdown = data.decode("utf-8", errors="ignore")
                    else:
                        parsed = parse_with_kordoc(data, filename=Path(info.filename).name, ocr=False)
                        markdown = str(parsed.get("markdown") or "")
                except KordocParseError as exc:
                    warnings.append(f"{info.filename}: parse failed: {exc}")
                    markdown = ""
                rows.append(
                    {
                        "member": Path(info.filename).name,
                        "suffix": suffix,
                        "method": _method_from_name(info.filename),
                        "artifact_type": _artifact_type(info.filename),
                        "chars": len(markdown),
                        "has_task_prompt": any(token in markdown for token in ("면접과제", "과제", "응시자", "발표", "토론")),
                        "has_evaluation_form": any(token in markdown for token in ("평가양식", "평가요소", "평가항목", "채점", "평정")),
                        "terms": "; ".join(_extract_terms(markdown)),
                    }
                )
    except zipfile.BadZipFile as exc:
        warnings.append(f"{archive_path.name}: not a readable ZIP: {exc}")
    return rows, warnings


def summarize_sample(title: str, member_rows: list[dict[str, Any]]) -> dict[str, Any]:
    methods = sorted({str(row.get("method") or "") for row in member_rows if str(row.get("method") or "")})
    task_methods = sorted({str(row.get("method") or "") for row in member_rows if row.get("artifact_type") == "task" and str(row.get("method") or "")})
    eval_methods = sorted({str(row.get("method") or "") for row in member_rows if row.get("artifact_type") == "evaluation_form" and str(row.get("method") or "")})
    return {
        "title": title,
        "member_count": len(member_rows),
        "methods": "; ".join(methods),
        "task_methods": "; ".join(task_methods),
        "evaluation_methods": "; ".join(eval_methods),
        "method_count": len(methods),
        "has_task_and_eval_pairs": bool(task_methods and set(task_methods).issubset(set(eval_methods))),
    }


def write_reports(samples: list[dict[str, Any]], members: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = report_dir / f"ncs_official_interview_samples_{stamp}.md"
    sample_csv = report_dir / f"ncs_official_interview_samples_{stamp}.csv"
    member_csv = report_dir / f"ncs_official_interview_sample_members_{stamp}.csv"

    sample_fields = ["seq", "title", "view_text", "member_count", "methods", "task_methods", "evaluation_methods", "method_count", "has_task_and_eval_pairs", "archive", "warnings"]
    with sample_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=sample_fields)
        writer.writeheader()
        for row in samples:
            writer.writerow({field: row.get(field, "") for field in sample_fields})

    member_fields = ["seq", "title", "member", "suffix", "method", "artifact_type", "chars", "has_task_prompt", "has_evaluation_form", "terms"]
    with member_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=member_fields)
        writer.writeheader()
        for row in members:
            writer.writerow({field: row.get(field, "") for field in member_fields})

    passed_pairs = sum(1 for row in samples if row.get("has_task_and_eval_pairs"))
    lines = [
        f"# NCS Official Interview Samples - {stamp}",
        "",
        f"- Samples profiled: {len(samples)}",
        f"- Samples with task/evaluation pairs: {passed_pairs}",
        "",
        "| seq | title | methods | task methods | evaluation methods | members | paired |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in samples:
        lines.append(
            f"| {row.get('seq')} | {str(row.get('title') or '').replace('|', '/')} | {row.get('methods')} | "
            f"{row.get('task_methods')} | {row.get('evaluation_methods')} | {row.get('member_count')} | {row.get('has_task_and_eval_pairs')} |"
        )
    lines.extend(["", f"CSV: `{sample_csv}`", f"Member CSV: `{member_csv}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, sample_csv, member_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile official NCS fair-hiring interview task/evaluation samples.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--out-dir", default=".tmp/ncs_official_interview_samples")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--max-members", type=int, default=16)
    args = parser.parse_args()

    entries = discover_official_samples(max(1, int(args.limit)))
    samples: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for entry in entries:
        archive_path = download_sample_archive(entry, Path(args.out_dir))
        view_text = fetch_sample_view_text(entry)
        member_rows, warnings = profile_sample_archive(archive_path, max_members=max(1, int(args.max_members)))
        summary = summarize_sample(entry.title, member_rows)
        sample_row = {
            "seq": entry.seq,
            "title": entry.title,
            "view_text": view_text[:500],
            "archive": str(archive_path),
            "warnings": "; ".join(warnings),
            **summary,
        }
        samples.append(sample_row)
        for row in member_rows:
            members.append({"seq": entry.seq, "title": entry.title, **row})
        time.sleep(0.2)
    md_path, sample_csv, member_csv = write_reports(samples, members, Path(args.report_dir))
    print(f"report={md_path}")
    print(f"csv={sample_csv}")
    print(f"member_csv={member_csv}")
    print(f"rows={len(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
