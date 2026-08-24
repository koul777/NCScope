from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc  # noqa: E402


_HEADING_LINE_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+\S|\d+(?:\.\d+)*[.)]?\s+\S)")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _count_outline_entries(outline: Any) -> tuple[int, int]:
    total = 0
    titled = 0

    def walk(value: Any) -> None:
        nonlocal total, titled
        if isinstance(value, dict):
            title = str(value.get("title") or value.get("text") or "").strip()
            if title:
                total += 1
                titled += 1
            children = value.get("children")
            if isinstance(children, list):
                for child in children:
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(outline)
    return total, titled


def _count_markdown_headings(markdown: str) -> int:
    return sum(
        1
        for line in str(markdown or "").splitlines()
        if _HEADING_LINE_RE.match(line.strip())
    )


def _extract_page_count(parsed: dict[str, Any]) -> int:
    quality_summary = parsed.get("qualitySummary")
    if isinstance(quality_summary, dict):
        total_pages = quality_summary.get("totalPages")
        if isinstance(total_pages, int) and total_pages > 0:
            return total_pages
    page_quality = parsed.get("pageQuality")
    if isinstance(page_quality, list) and page_quality:
        return len(page_quality)
    blocks = parsed.get("blocks")
    max_page = 0
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            page = block.get("page")
            if isinstance(page, int) and page > max_page:
                max_page = page
    return max_page


def _quality_summary_snapshot(parsed: dict[str, Any]) -> dict[str, Any]:
    quality_summary = parsed.get("qualitySummary")
    if not isinstance(quality_summary, dict):
        return {}
    snapshot: dict[str, Any] = {}
    for key in (
        "totalPages",
        "totalTextChars",
        "avgHangulRatio",
        "avgControlCharRatio",
        "avgReplacementCharRatio",
        "avgPuaRatio",
        "lowTextPageCount",
        "highPuaPageCount",
        "needsOcr",
        "ocrCandidatePages",
    ):
        if key in quality_summary:
            snapshot[key] = quality_summary[key]
    return snapshot


def build_reference_summary(pdf_path: Path, parsed: dict[str, Any], file_bytes: bytes) -> dict[str, Any]:
    outline_count, titled_outline_count = _count_outline_entries(parsed.get("outline"))
    markdown = str(parsed.get("markdown") or "")
    markdown_heading_count = _count_markdown_headings(markdown)
    heading_count = max(markdown_heading_count, titled_outline_count)
    page_count = _extract_page_count(parsed)
    blocks = parsed.get("blocks")
    block_count = len(blocks) if isinstance(blocks, list) else 0
    checks = {
        "sha256_present": bool(file_bytes),
        "page_count_present": page_count > 0,
        "outline_present": outline_count > 0,
        "heading_present": heading_count > 0,
    }
    return {
        "status": "ok" if all(checks.values()) else "validation_failed",
        "pdf": {
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "size_bytes": len(file_bytes),
            "sha256": _sha256_bytes(file_bytes),
        },
        "kordoc": {
            "page_count": page_count,
            "markdown_chars": len(markdown),
            "block_count": block_count,
            "outline_count": outline_count,
            "heading_count": heading_count,
            "warning_count": len(parsed.get("warnings") or []) if isinstance(parsed.get("warnings"), list) else 0,
            "quality_summary": _quality_summary_snapshot(parsed),
        },
        "validation": {
            "passed": all(checks.values()),
            "checks": checks,
        },
    }


def _compare_expected_subset(
    actual: Any,
    expected: Any,
    *,
    path: str = "",
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            mismatches.append(
                {
                    "path": path or "$",
                    "expected": "object",
                    "actual": type(actual).__name__,
                }
            )
            return mismatches
        for key, expected_value in expected.items():
            child_path = f"{path}.{key}" if path else key
            if key not in actual:
                mismatches.append(
                    {
                        "path": child_path,
                        "expected": expected_value,
                        "actual": "__missing__",
                    }
                )
                continue
            mismatches.extend(
                _compare_expected_subset(actual[key], expected_value, path=child_path)
            )
        return mismatches
    if isinstance(expected, list):
        if actual != expected:
            mismatches.append(
                {
                    "path": path or "$",
                    "expected": expected,
                    "actual": actual,
                }
            )
        return mismatches
    if actual != expected:
        mismatches.append(
            {
                "path": path or "$",
                "expected": expected,
                "actual": actual,
            }
        )
    return mismatches


def _normalize_expected_metadata(expected: Any) -> Any:
    if not isinstance(expected, dict):
        return expected
    if not {"schema_version", "source", "usage", "methods"} <= set(expected):
        return expected
    source = expected.get("source")
    if not isinstance(source, dict):
        return expected
    parse_summary = source.get("parse_summary")
    if not isinstance(parse_summary, dict):
        parse_summary = {}
    return {
        "pdf": {
            "sha256": str(source.get("sha256") or "").strip().upper(),
        },
        "kordoc": {
            "page_count": source.get("page_count"),
            "markdown_chars": parse_summary.get("markdown_chars"),
            "block_count": parse_summary.get("block_count"),
            "outline_count": parse_summary.get("outline_count"),
            "warning_count": parse_summary.get("warning_count"),
            "quality_summary": {
                "needsOcr": parse_summary.get("needs_ocr"),
            },
        },
    }


def compare_expected_metadata(
    summary: dict[str, Any],
    expected_metadata_path: Path,
) -> tuple[bool, dict[str, Any]]:
    expected = _normalize_expected_metadata(
        json.loads(expected_metadata_path.read_text(encoding="utf-8"))
    )
    mismatches = _compare_expected_subset(summary, expected)
    report = {
        "path": str(expected_metadata_path),
        "matched": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    return not mismatches, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a local NCS interviewer guide PDF by hashing it and checking "
            "Kordoc parse metadata without printing document sentences."
        )
    )
    parser.add_argument("pdf_path", help="Local PDF path to verify.")
    parser.add_argument(
        "--expected-metadata-json",
        dest="expected_metadata_json",
        default="",
        help="Optional JSON file containing expected metadata fields to compare as a subset.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    pdf_path = Path(args.pdf_path).expanduser()
    if not pdf_path.is_file():
        payload = {
            "status": "error",
            "error": {
                "code": "pdf_not_found",
                "message": "Local PDF file was not found.",
                "path": str(pdf_path),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    try:
        file_bytes = pdf_path.read_bytes()
        parsed = parse_with_kordoc(file_bytes, filename=pdf_path.name, ocr=False)
        summary = build_reference_summary(pdf_path, parsed, file_bytes)
        exit_code = 0 if summary["validation"]["passed"] else 1
        expected_path = str(args.expected_metadata_json or "").strip()
        if expected_path:
            matched, expected_report = compare_expected_metadata(
                summary,
                Path(expected_path).expanduser(),
            )
            summary["expected_metadata"] = expected_report
            if not matched:
                summary["status"] = "expected_metadata_mismatch"
                exit_code = 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return exit_code
    except (KordocParseError, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "status": "error",
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
