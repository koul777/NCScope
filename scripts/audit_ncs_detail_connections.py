"""Fail-closed, public-safe audit for the NCS detail/unit catalog join.

Only aggregate values and code identities are emitted.  Catalog names are used
for comparisons but are never included in the report.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

CODE = re.compile(r"^\d{8}$")
UNIT_CODE = re.compile(r"^\d{10}(?:_[0-9A-Za-z]+)?$")


def _norm(value: Any) -> str:
    """Mirror the runtime's exact NCS identity normalization contract."""

    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[·ᆞ․‧•∙⋅・ㆍ]", "", value)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def _load(path: Path, key: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, [], ["catalog_read_error"]
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        return {}, [], [f"schema_missing_{key}"]
    rows = data[key]
    if not all(isinstance(row, dict) for row in rows):
        errors.append(f"schema_non_object_{key}")
    return data, [row for row in rows if isinstance(row, dict)], errors


def audit_catalogs(detail_path: Path, unit_path: Path) -> dict[str, Any]:
    detail_meta, details, errors = _load(detail_path, "details")
    unit_meta, units, unit_errors = _load(unit_path, "units")
    errors.extend(unit_errors)
    active = [r for r in details if str(r.get("usage_yn", "")).strip().upper() == "Y"]
    detail_by_code: dict[str, dict[str, Any]] = {}
    for row in details:
        code = row.get("code")
        if not isinstance(code, str) or not CODE.fullmatch(code):
            errors.append("schema_invalid_detail_code")
        elif code in detail_by_code:
            errors.append("duplicate_detail_code")
        else:
            detail_by_code[code] = row
    active_by_code = {r.get("code"): r for r in active}
    for row in units:
        code, base, detail_code = row.get("code"), row.get("base_code"), row.get("detail_code")
        if not isinstance(code, str) or not UNIT_CODE.fullmatch(code):
            errors.append("schema_invalid_unit_code")
        if not isinstance(base, str) or not re.fullmatch(r"\d{10}", base):
            errors.append("schema_invalid_base_code")
        if not isinstance(detail_code, str) or not CODE.fullmatch(detail_code):
            errors.append("schema_invalid_unit_detail_code")
        if isinstance(code, str) and isinstance(base, str) and code.split("_", 1)[0] != base:
            errors.append("unit_base_code_mismatch")
        if isinstance(base, str) and isinstance(detail_code, str) and base[:8] != detail_code:
            errors.append("base_detail_code_mismatch")
        if not str(row.get("name", "")).strip():
            errors.append("empty_unit_name")
        if not str(row.get("detail_name", "")).strip():
            errors.append("empty_unit_detail_name")
    for row in details:
        if not str(row.get("name", "")).strip():
            errors.append("empty_detail_name")
    for expected, rows, meta, field in (
        ("classification_count", details, detail_meta, "details"),
        ("unit_count", units, unit_meta, "units"),
    ):
        if not isinstance(meta.get(expected), int) or meta[expected] != len(rows):
            errors.append(f"count_mismatch_{field}")

    full_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    base_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    name_groups: dict[str, set[str]] = defaultdict(set)
    for row in units:
        full_groups[str(row.get("code"))].append(row)
        base_groups[str(row.get("base_code"))].append(row)
    for row in active:
        name_groups[_norm(row.get("name"))].add(str(row.get("code")))
    code_name_mismatches = [r for r in units if r.get("detail_code") in active_by_code and _norm(r.get("detail_name")) != _norm(active_by_code[r["detail_code"]].get("name"))]
    missing_detail_codes = sorted({str(r.get("detail_code")) for r in units if r.get("detail_code") not in active_by_code and isinstance(r.get("detail_code"), str)})
    referenced = {r.get("detail_code") for r in units}
    orphan_detail_codes = sorted(str(r["code"]) for r in active if r.get("code") not in referenced)
    duplicate_conflicts = sorted(k for k, rows in full_groups.items() if len(rows) > 1)
    detail_name_collisions = sum(bool(k) and len(v) > 1 for k, v in name_groups.items())
    base_identity_collisions = sum(len({(r.get("detail_code"), _norm(r.get("detail_name")), _norm(r.get("name"))) for r in rows}) > 1 for rows in base_groups.values())
    if missing_detail_codes:
        errors.append("orphan_unit")
    if orphan_detail_codes:
        errors.append("orphan_active_detail")
    if code_name_mismatches:
        errors.append("unit_detail_name_mismatch")
    if detail_name_collisions:
        errors.append("active_detail_name_collision")
    if duplicate_conflicts:
        errors.append("duplicate_full_code")
    if base_identity_collisions:
        errors.append("base_canonical_name_collision")
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "fail" if errors else "pass",
        "errors": sorted(set(errors)),
        "counts": {
            "active_detail": len(active), "detail_total": len(details), "unit_total": len(units),
            "full_code": len(full_groups), "base_code": len(base_groups),
        },
        "connections": {
            "detail_unit_code_mismatch": len(missing_detail_codes),
            "detail_unit_name_mismatch": len(code_name_mismatches),
            "orphan_detail": len(orphan_detail_codes),
            "orphan_unit": len(missing_detail_codes),
        },
        "collisions": {
            "normalized_detail_name": detail_name_collisions,
            "duplicate_full_code_conflict": len(duplicate_conflicts),
            "base_canonical_name": base_identity_collisions,
            "multi_version_base": sum(len(v) > 1 for v in base_groups.values()),
        },
        "identities": {
            "orphan_detail_codes": orphan_detail_codes,
            "orphan_unit_detail_codes": missing_detail_codes,
            "duplicate_full_code_conflict_codes": duplicate_conflicts,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", type=Path, default=Path("app/data/ncs_detail_catalog.json"))
    parser.add_argument("--unit", type=Path, default=Path("app/data/ncs_unit_catalog.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = audit_catalogs(args.detail, args.unit)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
