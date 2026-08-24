from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.services.kordoc_parser import KordocParseError, structure_job_description  # noqa: E402
from benchmark_alio_jd import parse_benchmark_document  # noqa: E402


DEFAULT_INPUT_DIR = "tmp/alio_jd_200_mcp"
DEFAULT_OUTPUT_DIR = "tmp/stored_jd_coordinate_contract"
SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".zip"}
TARGET_SECTIONS = {"ability_units", "ncs_detail"}
COORDINATE_REQUIRED_KEYS = ("row", "column", "row_span", "column_span")
KORDOC_BLOCK_DERIVED_SOURCES = {"kordoc", "kordoc_table"}
DIRECT_TABLE_COORDINATE_SOURCES = {"kordoc_table"}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _pct(numerator: int, denominator: int) -> float:
    return round((100.0 * numerator / denominator), 2) if denominator else 0.0


def _unique_strings(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        key = _norm(text)
        if text and key and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def collect_corpus_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise ValueError("input directory does not exist")
    files = sorted(
        (
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ),
        key=lambda path: path.name,
    )
    if not files:
        raise ValueError("input directory contains no supported documents")
    return files


def _parse_file(path: Path, max_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise RuntimeError(f"file exceeds limit: {len(data)} > {max_bytes}")
    parsed = parse_benchmark_document(
        data,
        filename=path.name,
        max_bytes=max_bytes,
        ocr=True,
    )
    structured = structure_job_description(parsed, filename=path.name)
    return parsed, structured


def _coordinate_reasons(item: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    page = item.get("page")
    table_index = item.get("table_index")
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        reasons.append("missing_page")
    else:
        if page_number < 0:
            reasons.append("invalid_page")
    try:
        table_number = int(table_index)
    except (TypeError, ValueError):
        reasons.append("missing_table_index")
    else:
        if table_number < 0:
            reasons.append("invalid_table_index")
    for label in ("label_cell", "value_cell"):
        cell = item.get(label)
        if not isinstance(cell, dict):
            reasons.append(f"missing_{label}")
            continue
        missing_keys = [key for key in COORDINATE_REQUIRED_KEYS if key not in cell]
        if missing_keys:
            reasons.append(f"invalid_{label}_shape")
            continue
        try:
            row = int(cell.get("row"))
            column = int(cell.get("column"))
            row_span = int(cell.get("row_span"))
            column_span = int(cell.get("column_span"))
        except (TypeError, ValueError):
            reasons.append(f"invalid_{label}_shape")
            continue
        if row < 0 or column < 0:
            reasons.append(f"invalid_{label}_shape")
        if row_span < 1 or column_span < 1:
            reasons.append(f"invalid_{label}_span")
    value_cell = item.get("value_cell")
    row_context_cells = item.get("row_context_cells")
    raw_cell_text = item.get("raw_cell_text")
    evidence_key = _norm(raw_cell_text)
    if not evidence_key:
        reasons.append("missing_raw_cell_text")
    if not isinstance(row_context_cells, list) or not row_context_cells:
        reasons.append("missing_row_context_cells")
    elif isinstance(value_cell, dict) and evidence_key:
        try:
            value_column = int(value_cell.get("column"))
        except (TypeError, ValueError):
            pass
        else:
            matching_context_cells: list[dict[str, Any]] = []
            for context_cell in row_context_cells:
                if not isinstance(context_cell, dict):
                    continue
                try:
                    context_column = int(context_cell.get("column"))
                    context_span = int(context_cell.get("column_span", 1))
                except (TypeError, ValueError):
                    continue
                if context_span >= 1 and context_column <= value_column < (
                    context_column + context_span
                ):
                    matching_context_cells.append(context_cell)
            if not matching_context_cells:
                reasons.append("value_cell_not_in_row_context")
            elif not any(
                evidence_key in _norm(context_cell.get("text"))
                for context_cell in matching_context_cells
            ):
                reasons.append("value_cell_evidence_text_mismatch")
    return sorted(set(reasons))


def _structured_fields(structured: dict[str, Any]) -> dict[str, Any]:
    fields = structured.get("fields")
    return fields if isinstance(fields, dict) else {}


def _case_result_from_structured(
    *,
    seq: int,
    suffix: str,
    parsed: dict[str, Any],
    structured: dict[str, Any],
) -> dict[str, Any]:
    del parsed
    fields = _structured_fields(structured)
    final_abilities = _unique_strings(fields.get("ability_units") or [])
    final_ability_keys = {_norm(value) for value in final_abilities if _norm(value)}
    positioned_items = (
        fields.get("positioned_items") if isinstance(fields.get("positioned_items"), list) else []
    )
    target_items = [
        item
        for item in positioned_items
        if isinstance(item, dict) and str(item.get("section") or "") in TARGET_SECTIONS
    ]
    positioned_ability_items = [
        item for item in target_items if str(item.get("section") or "") == "ability_units"
    ]
    positioned_detail_items = [
        item for item in target_items if str(item.get("section") or "") == "ncs_detail"
    ]
    positioned_ability_names = _unique_strings(
        [item.get("text") for item in positioned_ability_items]
    )
    positioned_ability_keys = {
        _norm(value) for value in positioned_ability_names if _norm(value)
    }
    coordinate_failures = sum(
        1 for item in target_items if _coordinate_reasons(item)
    )
    reason_counts: Counter[str] = Counter()
    reason_context_counts: Counter[str] = Counter()
    for item in target_items:
        item_reasons = _coordinate_reasons(item)
        reason_counts.update(item_reasons)
        context = "|".join(
            (
                str(item.get("section") or "unknown"),
                str(item.get("source") or "unknown"),
                str(item.get("layout") or "unknown"),
            )
        )
        reason_context_counts.update(
            f"{reason}|{context}" for reason in item_reasons
        )
    final_with_position = sum(
        1 for key in final_ability_keys if key and key in positioned_ability_keys
    )
    final_without_position = sum(
        1 for key in final_ability_keys if key and key not in positioned_ability_keys
    )
    positioned_source_counts = Counter(
        str(item.get("source") or "") for item in positioned_ability_items
    )
    positioned_layout_counts = Counter(
        str(item.get("layout") or "") for item in positioned_ability_items
    )
    positioned_target_source_counts = Counter(
        str(item.get("source") or "") for item in target_items
    )
    kordoc_block_derived_coordinate_item_count = sum(
        1
        for item in target_items
        if str(item.get("source") or "") in KORDOC_BLOCK_DERIVED_SOURCES
    )
    direct_table_coordinate_item_count = sum(
        1
        for item in target_items
        if str(item.get("source") or "") in DIRECT_TABLE_COORDINATE_SOURCES
    )
    reasons = sorted(reason_counts) if reason_counts else []
    status = "coordinate_contract_failure" if reasons else "ok"
    return {
        "seq": seq,
        "suffix": suffix,
        "status": status,
        "reason_counts": dict(sorted(reason_counts.items())),
        "reason_context_counts": dict(sorted(reason_context_counts.items())),
        "reasons": reasons,
        "final_ability_unit_count": len(final_abilities),
        "positioned_ability_unit_count": len(positioned_ability_names),
        "positioned_ability_item_count": len(positioned_ability_items),
        "positioned_detail_item_count": len(positioned_detail_items),
        "positioned_target_item_count": len(target_items),
        "coordinate_contract_valid_item_count": len(target_items) - coordinate_failures,
        "coordinate_contract_failure_item_count": coordinate_failures,
        "final_ability_with_table_position_count": final_with_position,
        "final_ability_without_table_position_count": final_without_position,
        "positioned_ability_source_counts": dict(sorted(positioned_source_counts.items())),
        "positioned_ability_layout_counts": dict(sorted(positioned_layout_counts.items())),
        "positioned_target_source_counts": dict(
            sorted(positioned_target_source_counts.items())
        ),
        "kordoc_block_derived_coordinate_item_count": (
            kordoc_block_derived_coordinate_item_count
        ),
        "direct_table_coordinate_item_count": direct_table_coordinate_item_count,
        "recovered_logical_coordinate_item_count": (
            len(target_items) - kordoc_block_derived_coordinate_item_count
        ),
    }


def _error_case(
    *,
    seq: int,
    suffix: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "suffix": suffix,
        "status": status,
        "reason_counts": {reason: 1},
        "reason_context_counts": {},
        "reasons": [reason],
        "final_ability_unit_count": 0,
        "positioned_ability_unit_count": 0,
        "positioned_ability_item_count": 0,
        "positioned_detail_item_count": 0,
        "positioned_target_item_count": 0,
        "coordinate_contract_valid_item_count": 0,
        "coordinate_contract_failure_item_count": 0,
        "final_ability_with_table_position_count": 0,
        "final_ability_without_table_position_count": 0,
        "positioned_ability_source_counts": {},
        "positioned_ability_layout_counts": {},
        "positioned_target_source_counts": {},
        "kordoc_block_derived_coordinate_item_count": 0,
        "direct_table_coordinate_item_count": 0,
        "recovered_logical_coordinate_item_count": 0,
    }


ParseFileFn = Callable[[Path, int], tuple[dict[str, Any], dict[str, Any]]]


def audit_corpus(
    files: list[Path],
    *,
    expected_files: int,
    expected_unique_contents: int,
    max_file_bytes: int,
    parse_file: ParseFileFn = _parse_file,
) -> dict[str, Any]:
    content_hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    ]
    unique_contents = len(set(content_hashes))
    corpus_failures: list[str] = []
    if len(files) != expected_files:
        corpus_failures.append("unexpected_corpus_size")
    if unique_contents != expected_unique_contents:
        corpus_failures.append("unexpected_unique_content_count")

    cases: list[dict[str, Any]] = []
    failure_cases: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    reason_context_counts: Counter[str] = Counter()
    positioned_source_counts: Counter[str] = Counter()
    positioned_layout_counts: Counter[str] = Counter()
    positioned_target_source_counts: Counter[str] = Counter()

    for seq, path in enumerate(files, start=1):
        suffix = path.suffix.lower()
        try:
            parsed, structured = parse_file(path, max_file_bytes)
            case = _case_result_from_structured(
                seq=seq,
                suffix=suffix,
                parsed=parsed,
                structured=structured,
            )
        except KordocParseError:
            reason = "kordoc_parse_error"
            case = _error_case(
                seq=seq,
                suffix=suffix,
                status="parse_error",
                reason=reason,
            )
        except RuntimeError as exc:
            reason = (
                "document_size_limit"
                if str(exc).startswith("file exceeds limit:")
                else "runtime_parse_error"
            )
            case = _error_case(
                seq=seq,
                suffix=suffix,
                status="parse_error",
                reason=reason,
            )
        except OSError:
            reason = "document_read_error"
            case = _error_case(
                seq=seq,
                suffix=suffix,
                status="parse_error",
                reason=reason,
            )
        except ValueError:
            reason = "document_structure_error"
            case = _error_case(
                seq=seq,
                suffix=suffix,
                status="parse_error",
                reason=reason,
            )
        cases.append(case)
        status_counts[case["status"]] += 1
        reason_counts.update(case["reason_counts"])
        reason_context_counts.update(case["reason_context_counts"])
        positioned_source_counts.update(case["positioned_ability_source_counts"])
        positioned_layout_counts.update(case["positioned_ability_layout_counts"])
        positioned_target_source_counts.update(case["positioned_target_source_counts"])
        if case["status"] != "ok":
            failure_cases.append(
                {
                    "seq": case["seq"],
                    "suffix": case["suffix"],
                    "status": case["status"],
                    "reasons": case["reasons"],
                }
            )

    final_ability_unit_count = sum(
        int(case.get("final_ability_unit_count") or 0) for case in cases
    )
    positioned_ability_unit_count = sum(
        int(case.get("positioned_ability_unit_count") or 0) for case in cases
    )
    positioned_target_item_count = sum(
        int(case.get("positioned_target_item_count") or 0) for case in cases
    )
    coordinate_contract_valid_item_count = sum(
        int(case.get("coordinate_contract_valid_item_count") or 0) for case in cases
    )
    kordoc_block_derived_coordinate_item_count = sum(
        int(case.get("kordoc_block_derived_coordinate_item_count") or 0)
        for case in cases
    )
    direct_table_coordinate_item_count = sum(
        int(case.get("direct_table_coordinate_item_count") or 0)
        for case in cases
    )
    recovered_logical_coordinate_item_count = sum(
        int(case.get("recovered_logical_coordinate_item_count") or 0)
        for case in cases
    )
    final_ability_with_table_position_count = sum(
        int(case.get("final_ability_with_table_position_count") or 0) for case in cases
    )
    final_ability_without_table_position_count = sum(
        int(case.get("final_ability_without_table_position_count") or 0) for case in cases
    )
    documents_with_final_ability_units = sum(
        int(case.get("final_ability_unit_count") or 0) > 0 for case in cases
    )
    documents_with_positioned_ability_units = sum(
        int(case.get("positioned_ability_unit_count") or 0) > 0 for case in cases
    )
    documents_with_final_ability_but_no_positioned_ability = sum(
        int(case.get("final_ability_unit_count") or 0) > 0
        and int(case.get("positioned_ability_unit_count") or 0) == 0
        for case in cases
    )
    documents_with_positioned_target_items = sum(
        int(case.get("positioned_target_item_count") or 0) > 0 for case in cases
    )

    audit_failures: list[str] = []
    if positioned_target_item_count == 0:
        audit_failures.append("insufficient_positioned_evidence")

    summary = {
        "files": len(files),
        "expected_files": expected_files,
        "unique_contents": unique_contents,
        "expected_unique_contents": expected_unique_contents,
        "duplicate_files": len(files) - unique_contents,
        "corpus_failures": corpus_failures,
        "status_counts": dict(sorted(status_counts.items())),
        "failure_case_count": len(failure_cases),
        "documents_with_positioned_target_items": documents_with_positioned_target_items,
        "coordinate_contract_item_count": positioned_target_item_count,
        "coordinate_contract_valid_item_count": coordinate_contract_valid_item_count,
        "coordinate_contract_pct": _pct(
            coordinate_contract_valid_item_count,
            positioned_target_item_count,
        ),
        "coordinate_contract_definition": (
            "logical_coordinate_shape_and_raw_value_cell_text_alignment_"
            "not_native_page_fidelity"
        ),
        "kordoc_block_derived_coordinate_item_count": (
            kordoc_block_derived_coordinate_item_count
        ),
        "kordoc_block_derived_coordinate_pct": _pct(
            kordoc_block_derived_coordinate_item_count,
            positioned_target_item_count,
        ),
        "kordoc_block_derived_coordinate_sources": sorted(
            KORDOC_BLOCK_DERIVED_SOURCES
        ),
        "direct_table_coordinate_item_count": direct_table_coordinate_item_count,
        "direct_table_coordinate_pct": _pct(
            direct_table_coordinate_item_count,
            positioned_target_item_count,
        ),
        "direct_table_coordinate_sources": sorted(
            DIRECT_TABLE_COORDINATE_SOURCES
        ),
        "recovered_logical_coordinate_item_count": (
            recovered_logical_coordinate_item_count
        ),
        "documents_with_final_ability_units": documents_with_final_ability_units,
        "documents_with_positioned_ability_units": documents_with_positioned_ability_units,
        "documents_with_final_ability_but_no_positioned_ability": (
            documents_with_final_ability_but_no_positioned_ability
        ),
        "final_ability_unit_count": final_ability_unit_count,
        "positioned_ability_unit_count": positioned_ability_unit_count,
        "final_ability_with_table_position_count": final_ability_with_table_position_count,
        "final_ability_without_table_position_count": (
            final_ability_without_table_position_count
        ),
        "final_ability_has_table_position_pct": _pct(
            final_ability_with_table_position_count,
            final_ability_unit_count,
        ),
        "final_ability_table_position_association": (
            "document_unique_normalized_exact_name_non_recall_diagnostic"
        ),
        "final_unique_ability_name_with_position_pct": _pct(
            final_ability_with_table_position_count,
            final_ability_unit_count,
        ),
        "positioned_target_source_counts": dict(
            sorted(positioned_target_source_counts.items())
        ),
        "positioned_ability_source_counts": dict(
            sorted(positioned_source_counts.items())
        ),
        "positioned_ability_layout_counts": dict(
            sorted(positioned_layout_counts.items())
        ),
        "reason_counts": dict(sorted(reason_counts.items())),
        "reason_context_counts": dict(sorted(reason_context_counts.items())),
        "audit_failures": audit_failures,
    }
    passed = (
        not corpus_failures
        and not audit_failures
        and status_counts.get("parse_error", 0) == 0
        and coordinate_contract_valid_item_count == positioned_target_item_count
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_contract": {
            "expected_files": expected_files,
            "expected_unique_contents": expected_unique_contents,
            "max_file_bytes": max_file_bytes,
        },
        "summary": {**summary, "passed": passed},
        "failures": failure_cases,
        "cases": [
            {
                "seq": case["seq"],
                "suffix": case["suffix"],
                "status": case["status"],
                "reasons": case["reasons"],
            }
            for case in cases
        ],
    }


def write_reports(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"stored_jd_coordinate_contract_{stamp}.json"
    md_path = output_dir / f"stored_jd_coordinate_contract_{stamp}.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = result.get("summary") or {}
    failures = result.get("failures") or []
    lines = [
        "# Stored JD coordinate contract audit",
        "",
        f"- Files: {summary.get('files', 0)} / expected {summary.get('expected_files', 0)}",
        (
            f"- Unique contents: {summary.get('unique_contents', 0)} / expected "
            f"{summary.get('expected_unique_contents', 0)}"
        ),
        f"- Passed: {bool(summary.get('passed'))}",
        "",
        "## Coordinate contract",
        "",
        (
            "- Definition: "
            f"{summary.get('coordinate_contract_definition', '')}; "
            "recovered HTML/Markdown coordinates are not native "
            "page-provenance claims."
        ),
        (
            f"- Positioned target items: "
            f"{summary.get('coordinate_contract_item_count', 0)}"
        ),
        (
            f"- Valid positioned target items: "
            f"{summary.get('coordinate_contract_valid_item_count', 0)}"
        ),
        (
            f"- coordinate_contract_pct: "
            f"{summary.get('coordinate_contract_pct', 0.0)}"
        ),
        (
            f"- Kordoc block/table logical-coordinate items: "
            f"{summary.get('kordoc_block_derived_coordinate_item_count', 0)} "
            f"({summary.get('kordoc_block_derived_coordinate_pct', 0.0)}%)"
        ),
        (
            f"- Direct parsed-table coordinate items: "
            f"{summary.get('direct_table_coordinate_item_count', 0)} "
            f"({summary.get('direct_table_coordinate_pct', 0.0)}%)"
        ),
        (
            f"- Recovered logical coordinate items: "
            f"{summary.get('recovered_logical_coordinate_item_count', 0)}"
        ),
        "",
        "## Ability provenance split",
        "",
        (
            f"- Final ability units: {summary.get('final_ability_unit_count', 0)}"
        ),
        (
            f"- Final abilities with table position: "
            f"{summary.get('final_ability_with_table_position_count', 0)}"
        ),
        (
            f"- Final abilities without table position: "
            f"{summary.get('final_ability_without_table_position_count', 0)}"
        ),
        (
            f"- final_unique_ability_name_with_position_pct: "
            f"{summary.get('final_unique_ability_name_with_position_pct', 0.0)}"
        ),
        (
            "- Association rule: document-unique normalized exact names only; this "
            "is a provenance diagnostic, not occurrence-level coordinate recall."
        ),
        "",
        "## Failures",
        "",
        "| seq | suffix | status | reasons |",
        "| ---: | --- | --- | --- |",
    ]
    for failure in failures:
        lines.append(
            f"| {failure.get('seq', '')} | {failure.get('suffix', '')} | "
            f"{failure.get('status', '')} | "
            f"{', '.join(str(value) for value in failure.get('reasons') or [])} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit stored JD coordinate-contract completeness and separate "
            "final ability units from table-positioned ability evidence."
        )
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-files", type=int, default=206)
    parser.add_argument("--expected-unique-contents", type=int, default=198)
    parser.add_argument("--max-file-mb", type=int, default=25)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = collect_corpus_files(Path(args.input_dir))
    result = audit_corpus(
        files,
        expected_files=max(1, int(args.expected_files)),
        expected_unique_contents=max(1, int(args.expected_unique_contents)),
        max_file_bytes=max(1, int(args.max_file_mb)) * 1024 * 1024,
    )
    json_path, md_path = write_reports(result, Path(args.output_dir))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json={json_path}")
    print(f"md={md_path}")
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
