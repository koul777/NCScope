from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hwp_text_fallback import (  # noqa: E402
    HwpTextExtractionError,
    extract_hwp_text,
    extract_hwpx_text,
    extract_linear_ncs_classification_terms,
)
from app.services.ncs_mcp_client import NcsMcpError, search_units_by_detail  # noqa: E402


HANGUL_SUFFIXES = {".hwp", ".hwpx"}
MAX_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s:：·ㆍ･.,()\[\]{}\-_/]+", "", text)


def _hierarchy_names() -> list[str]:
    path = ROOT / "ncs_sclass_codes_with_code_no.csv"
    names: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            for field in ("NCS_LCLAS_CDNM", "NCS_MCLAS_CDNM"):
                value = str(row.get(field) or "").strip()
                key = _key(value)
                if value and key and key not in seen:
                    seen.add(key)
                    names.append(value)
    return names


def _extract_one(data: bytes, suffix: str) -> str:
    return extract_hwpx_text(data) if suffix == ".hwpx" else extract_hwp_text(data)


def collect_fallback_terms(source_dir: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    hierarchy = _hierarchy_names()
    rows: list[dict[str, Any]] = []
    terms_by_idx: dict[str, list[str]] = defaultdict(list)
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        idx = path.name.split("_", 1)[0]
        members: list[tuple[str, str, bytes]] = []
        if path.suffix.casefold() in HANGUL_SUFFIXES:
            members.append((path.name, path.suffix.casefold(), path.read_bytes()))
        elif path.suffix.casefold() == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        suffix = Path(info.filename).suffix.casefold()
                        if info.is_dir() or suffix not in HANGUL_SUFFIXES:
                            continue
                        if int(info.file_size or 0) > MAX_ARCHIVE_MEMBER_BYTES:
                            rows.append(
                                {
                                    "idx": idx,
                                    "attachment": path.name,
                                    "member": info.filename,
                                    "status": "member_too_large",
                                    "candidate_terms": "",
                                    "recovered_details": "",
                                    "error": "archive member exceeds 4 MiB",
                                }
                            )
                            continue
                        members.append((info.filename, suffix, archive.read(info)))
            except (OSError, zipfile.BadZipFile) as exc:
                rows.append(
                    {
                        "idx": idx,
                        "attachment": path.name,
                        "member": "",
                        "status": "archive_error",
                        "candidate_terms": "",
                        "recovered_details": "",
                        "error": str(exc)[:300],
                    }
                )
                continue
        for member_name, suffix, data in members:
            try:
                text = _extract_one(data, suffix)
                terms = extract_linear_ncs_classification_terms(
                    text,
                    excluded_hierarchy_names=hierarchy,
                    limit=40,
                )
                for term in terms:
                    if _key(term) not in {_key(value) for value in terms_by_idx[idx]}:
                        terms_by_idx[idx].append(term)
                rows.append(
                    {
                        "idx": idx,
                        "attachment": path.name,
                        "member": member_name,
                        "status": "text_extracted",
                        "candidate_terms": "; ".join(terms),
                        "recovered_details": "",
                        "error": "",
                    }
                )
            except (HwpTextExtractionError, OSError) as exc:
                rows.append(
                    {
                        "idx": idx,
                        "attachment": path.name,
                        "member": member_name,
                        "status": "extract_error",
                        "candidate_terms": "",
                        "recovered_details": "",
                        "error": str(exc)[:300],
                    }
                )
    return rows, dict(terms_by_idx)


def resolve_terms(terms: list[str], workers: int) -> tuple[dict[str, str], dict[str, str]]:
    unique = {_key(term): term for term in terms if _key(term)}
    resolved: dict[str, str] = {}
    errors: dict[str, str] = {}

    def resolve(term: str) -> tuple[str, str]:
        units = search_units_by_detail([term], max_units=1)
        canonical = ""
        if units:
            canonical = str(units[0].get("ncsSubdCdnm") or units[0].get("resolvedDetailName") or "").strip()
        return term, canonical

    with ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as executor:
        futures = {executor.submit(resolve, term): key for key, term in unique.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                _term, canonical = future.result()
                if canonical:
                    resolved[key] = canonical
            except (NcsMcpError, OSError, RuntimeError) as exc:
                errors[key] = str(exc)[:300]
    return resolved, errors


def reference_exact_by_idx(path: Path) -> dict[str, list[str]]:
    output: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            member = str(row.get("member") or row.get("attachment") or "")
            if Path(member).suffix.casefold() not in HANGUL_SUFFIXES:
                continue
            if str(row.get("exact_match") or "").casefold() != "true":
                continue
            idx = str(row.get("idx") or "").strip()
            canonical = str(row.get("resolved_parent_detail") or row.get("detail") or "").strip()
            if idx and canonical and _key(canonical) not in {_key(value) for value in output[idx]}:
                output[idx].append(canonical)
    return dict(output)


def write_reports(
    *,
    rows: list[dict[str, Any]],
    terms_by_idx: dict[str, list[str]],
    resolved: dict[str, str],
    errors: dict[str, str],
    reference: dict[str, list[str]],
    report_dir: Path,
) -> tuple[Path, Path]:
    recovered_by_idx: dict[str, list[str]] = {}
    for idx, terms in terms_by_idx.items():
        recovered: list[str] = []
        for term in terms:
            canonical = resolved.get(_key(term), "")
            if canonical and _key(canonical) not in {_key(value) for value in recovered}:
                recovered.append(canonical)
        recovered_by_idx[idx] = recovered
    for row in rows:
        row["recovered_details"] = "; ".join(recovered_by_idx.get(str(row["idx"]), []))

    reference_pairs = {(idx, _key(value)) for idx, values in reference.items() for value in values}
    recovered_pairs = {(idx, _key(value)) for idx, values in recovered_by_idx.items() for value in values}
    overlap = reference_pairs & recovered_pairs
    missing = reference_pairs - recovered_pairs
    additional = recovered_pairs - reference_pairs
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f"hwp_serverless_fallback_{stamp}.csv"
    md_path = report_dir / f"hwp_serverless_fallback_{stamp}.md"
    fields = ["idx", "attachment", "member", "status", "candidate_terms", "recovered_details", "error"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    extracted = sum(row["status"] == "text_extracted" for row in rows)
    failed = sum(row["status"] != "text_extracted" for row in rows)
    recall = (len(overlap) / len(reference_pairs)) if reference_pairs else 1.0
    lines = [
        "# HWP/HWPX Serverless Fallback Benchmark",
        "",
        f"- HWP/HWPX members attempted: {len(rows)}",
        f"- Text extraction succeeded: {extracted}",
        f"- Text extraction failed/skipped: {failed}",
        f"- Flattened classification terms: {sum(len(values) for values in terms_by_idx.values())}",
        f"- Unique term MCP errors: {len(errors)}",
        f"- Reference Kordoc+MCP exact pairs: {len(reference_pairs)}",
        f"- Serverless fallback exact pairs: {len(recovered_pairs)}",
        f"- Exact overlap: {len(overlap)}",
        f"- Reference recall: {recall:.1%}",
        f"- Additional official exact pairs: {len(additional)}",
        f"- Missed reference pairs: {len(missing)}",
        "",
        "Additional pairs are official MCP exact matches but may represent details that the reference Kordoc extraction missed; they are not automatically classified as false positives.",
        "",
        f"CSV: `{csv_path.as_posix()}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--reference-detail-csv", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=Path("tmp/alio_match_reports"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not os.getenv("NCS_MCP_URL", "").strip():
        raise SystemExit("NCS_MCP_URL is required")

    rows, terms_by_idx = collect_fallback_terms(args.source_dir)
    all_terms = [term for terms in terms_by_idx.values() for term in terms]
    resolved, errors = resolve_terms(all_terms, args.workers)
    reference = reference_exact_by_idx(args.reference_detail_csv)
    md_path, csv_path = write_reports(
        rows=rows,
        terms_by_idx=terms_by_idx,
        resolved=resolved,
        errors=errors,
        reference=reference,
        report_dir=args.report_dir,
    )
    print(f"report={md_path}")
    print(f"csv={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
