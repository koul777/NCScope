from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ncs_mcp_client import get_ksa_by_units  # noqa: E402


DEFAULT_CATALOG = "app/data/ncs_unit_catalog.json"
DEFAULT_OUTPUT_DIR = "tmp/stored_jd_ksa_contract"
EXPECTED_KSA_TYPES = {"knowledge", "skill", "attitude"}


def _ksa_type_key(value: Any) -> str:
    compact = re.sub(r"\s+", "", str(value or "")).casefold()
    if compact in {"k", "knowledge", "지식"} or "지식" in compact:
        return "knowledge"
    if compact in {"s", "skill", "skills", "기술"} or "기술" in compact:
        return "skill"
    if compact in {"a", "attitude", "태도"} or "태도" in compact:
        return "attitude"
    return compact


def _probe_codes(benchmark_csv: Path) -> list[str]:
    with benchmark_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(
        {
            code.strip()
            for row in rows
            for code in str(row.get("ksa_probe_codes") or "").split(";")
            if code.strip()
        }
    )


def _csv_row_count(benchmark_csv: Path) -> int:
    with benchmark_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _row in csv.DictReader(handle))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_set_sha256(codes: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(codes)).encode("utf-8")).hexdigest()


def _catalog_units(catalog_path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unit catalog must contain a units list")
    return {
        str(row.get("code") or "").strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("code") or "").strip()
    }


FetchKsaFn = Callable[[list[dict[str, Any]], int], list[dict[str, Any]]]


def audit_ksa_contract(
    benchmark_csv: Path | None,
    catalog_path: Path,
    *,
    all_active_catalog_units: bool = False,
    expected_unit_codes: int = 0,
    expected_benchmark_rows: int = 0,
    expected_benchmark_sha256: str = "",
    expected_catalog_sha256: str = "",
    expected_code_set_sha256: str = "",
    expected_client_sha256: str = "",
    expected_audit_script_sha256: str = "",
    require_input_digests: bool = False,
    max_factors_per_unit: int = 12,
    fetch_ksa: FetchKsaFn = get_ksa_by_units,
) -> dict[str, Any]:
    catalog = _catalog_units(catalog_path)
    if all_active_catalog_units:
        if benchmark_csv is not None:
            raise ValueError(
                "benchmark_csv cannot be supplied with all_active_catalog_units"
            )
        codes = sorted(catalog)
        benchmark_rows = 0
        benchmark_sha256 = ""
        input_scope = "all_active_catalog_unit_codes"
    else:
        if benchmark_csv is None:
            raise ValueError("benchmark_csv is required unless all catalog units are used")
        codes = _probe_codes(benchmark_csv)
        benchmark_rows = _csv_row_count(benchmark_csv)
        benchmark_sha256 = _sha256(benchmark_csv)
        input_scope = "benchmark_selected_probe_code_set"
    catalog_sha256 = _sha256(catalog_path)
    code_set_sha256 = _code_set_sha256(codes)
    client_path = ROOT / "app" / "services" / "ncs_mcp_client.py"
    client_sha256 = _sha256(client_path)
    audit_script_sha256 = _sha256(Path(__file__).resolve())
    missing_catalog_codes = [code for code in codes if code not in catalog]
    units = [
        {
            "ncsClCd": code,
            "compeUnitName": catalog[code].get("name", ""),
            "ncsSubdCdnm": catalog[code].get("detail_name", ""),
        }
        for code in codes
        if code in catalog
    ]
    failures: list[str] = []
    supplied_digests = (
        expected_catalog_sha256,
        expected_code_set_sha256,
        expected_client_sha256,
        expected_audit_script_sha256,
    )
    if not all_active_catalog_units:
        supplied_digests = (expected_benchmark_sha256, *supplied_digests)
    if require_input_digests and not all(supplied_digests):
        failures.append("missing_required_input_digests")
    for label, actual, expected in (
        ("catalog", catalog_sha256, expected_catalog_sha256),
        ("code_set", code_set_sha256, expected_code_set_sha256),
        ("client", client_sha256, expected_client_sha256),
        ("audit_script", audit_script_sha256, expected_audit_script_sha256),
    ):
        if expected and actual != expected:
            failures.append(f"{label}_sha256_mismatch")
    if (
        not all_active_catalog_units
        and expected_benchmark_sha256
        and benchmark_sha256 != expected_benchmark_sha256
    ):
        failures.append("benchmark_sha256_mismatch")
    if not codes:
        failures.append("no_probe_codes")
    if expected_unit_codes > 0 and len(codes) != expected_unit_codes:
        failures.append("unexpected_probe_code_count")
    if expected_benchmark_rows > 0 and benchmark_rows != expected_benchmark_rows:
        failures.append("unexpected_benchmark_row_count")
    if missing_catalog_codes:
        failures.append("probe_codes_missing_from_active_catalog")
    rows = (
        fetch_ksa(units, max(3, int(max_factors_per_unit or 12)))
        if units and not failures
        else []
    )

    expected_codes = set(codes)
    types_by_code: dict[str, set[str]] = defaultdict(set)
    type_row_counts: Counter[str] = Counter()
    unexpected_codes: set[str] = set()
    invalid_mcp_contract_marker_rows = 0
    invalid_ksa_type_rows = 0
    missing_factor_rows = 0
    missing_criteria_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_mcp_contract_marker_rows += 1
            continue
        code = str(row.get("ncsClCd") or "").strip()
        if code not in expected_codes:
            if code:
                unexpected_codes.add(code)
            invalid_mcp_contract_marker_rows += 1
            continue
        ksa_type = _ksa_type_key(row.get("ksaTypeName"))
        if ksa_type in EXPECTED_KSA_TYPES:
            types_by_code[code].add(ksa_type)
            type_row_counts[ksa_type] += 1
        else:
            invalid_ksa_type_rows += 1
        if not str(row.get("factorName") or "").strip():
            missing_factor_rows += 1
        criteria = row.get("performanceCriteria")
        if not isinstance(criteria, list) or not any(
            str(value or "").strip() for value in criteria
        ):
            missing_criteria_rows += 1
        if not (
            row.get("isOfficialKsa") is True
            and str(row.get("ksaStatus") or "") == "official"
            and str(row.get("source") or "") == "ncs-mcp"
            and str(row.get("factorSource") or "") == "ncs-mcp"
        ):
            invalid_mcp_contract_marker_rows += 1

    missing_row_codes = sorted(expected_codes - set(types_by_code))
    complete_codes = sorted(
        code
        for code in expected_codes
        if EXPECTED_KSA_TYPES.issubset(types_by_code.get(code, set()))
    )
    incomplete_type_codes = sorted(expected_codes - set(complete_codes))
    type_set_distribution = Counter(
        len(types_by_code.get(code, set())) for code in expected_codes
    )
    if missing_row_codes:
        failures.append("unit_codes_without_ksa_rows")
    if incomplete_type_codes:
        failures.append("unit_codes_without_complete_ksa_type_set")
    if unexpected_codes:
        failures.append("unexpected_unit_codes_in_ksa_response")
    if missing_factor_rows:
        failures.append("ksa_rows_without_factor_text")
    if missing_criteria_rows:
        failures.append("ksa_rows_without_performance_criteria")
    if invalid_mcp_contract_marker_rows:
        failures.append("ksa_rows_without_configured_mcp_contract_markers")
    if invalid_ksa_type_rows:
        failures.append("ksa_rows_with_unknown_or_missing_type")

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "definition": (
            "configured_ncs_mcp_client_rows_with_knowledge_skill_attitude_"
            f"and_performance_criteria_for_each_{input_scope}"
        ),
        "input_scope": input_scope,
        "provenance_scope": (
            "configured_ncs_unit_detail_client_contract_not_independent_"
            "upstream_database_attestation"
        ),
        "passed": not failures,
        "benchmark_row_count": benchmark_rows,
        "benchmark_sha256": benchmark_sha256,
        "catalog_sha256": catalog_sha256,
        "probe_code_set_sha256": code_set_sha256,
        "ncs_mcp_client_sha256": client_sha256,
        "audit_script_sha256": audit_script_sha256,
        "input_digests_required": require_input_digests,
        "input_digest_expectations": {
            "benchmark_sha256": expected_benchmark_sha256,
            "catalog_sha256": expected_catalog_sha256,
            "probe_code_set_sha256": expected_code_set_sha256,
            "ncs_mcp_client_sha256": expected_client_sha256,
            "audit_script_sha256": expected_audit_script_sha256,
        },
        "input_digests_match": not any(
            failure == "missing_required_input_digests"
            or failure.endswith("_sha256_mismatch")
            for failure in failures
        ),
        "ncs_mcp_tool": "ncs_unit_detail",
        "ncs_mcp_include": ["elements", "criteria", "ksa"],
        "ncs_mcp_text_version": "raw",
        "upstream_database_identity": "not_exposed_by_current_client_contract",
        "max_factors_per_unit": max(3, int(max_factors_per_unit or 12)),
        "probe_unit_codes": len(codes),
        "expected_unit_codes": expected_unit_codes,
        "expected_benchmark_rows": expected_benchmark_rows,
        "catalog_resolved_unit_codes": len(units),
        "unit_codes_with_ksa_rows": len(types_by_code),
        "unit_codes_with_complete_ksa_type_set": len(complete_codes),
        "complete_ksa_type_set_pct": (
            round(100.0 * len(complete_codes) / len(codes), 2) if codes else 0.0
        ),
        "ksa_row_count": len(rows),
        "ksa_type_row_counts": dict(sorted(type_row_counts.items())),
        "ksa_type_count_distribution": {
            str(key): value for key, value in sorted(type_set_distribution.items())
        },
        "rows_with_performance_criteria": len(rows) - missing_criteria_rows,
        "rows_with_configured_mcp_contract_markers": (
            len(rows) - invalid_mcp_contract_marker_rows
        ),
        "missing_catalog_code_count": len(missing_catalog_codes),
        "missing_row_code_count": len(missing_row_codes),
        "incomplete_type_code_count": len(incomplete_type_codes),
        "unexpected_response_code_count": len(unexpected_codes),
        "missing_factor_row_count": missing_factor_rows,
        "missing_criteria_row_count": missing_criteria_rows,
        "invalid_mcp_contract_marker_row_count": invalid_mcp_contract_marker_rows,
        "invalid_ksa_type_row_count": invalid_ksa_type_rows,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit configured NCS MCP client K/S/A and criteria coverage for "
            "benchmark-selected or active-catalog unit codes."
        )
    )
    parser.add_argument("benchmark_csv", nargs="?")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--all-active-catalog-units", action="store_true")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-unit-codes", type=int, default=0)
    parser.add_argument("--expected-benchmark-rows", type=int, default=0)
    parser.add_argument("--expected-benchmark-sha256", default="")
    parser.add_argument("--expected-catalog-sha256", default="")
    parser.add_argument("--expected-code-set-sha256", default="")
    parser.add_argument("--expected-client-sha256", default="")
    parser.add_argument("--expected-audit-script-sha256", default="")
    parser.add_argument("--require-input-digests", action="store_true")
    parser.add_argument("--max-factors-per-unit", type=int, default=12)
    args = parser.parse_args()
    if bool(args.benchmark_csv) == bool(args.all_active_catalog_units):
        parser.error(
            "provide exactly one of benchmark_csv or --all-active-catalog-units"
        )

    result = audit_ksa_contract(
        Path(args.benchmark_csv) if args.benchmark_csv else None,
        Path(args.catalog),
        all_active_catalog_units=bool(args.all_active_catalog_units),
        expected_unit_codes=max(0, args.expected_unit_codes),
        expected_benchmark_rows=max(0, args.expected_benchmark_rows),
        expected_benchmark_sha256=args.expected_benchmark_sha256,
        expected_catalog_sha256=args.expected_catalog_sha256,
        expected_code_set_sha256=args.expected_code_set_sha256,
        expected_client_sha256=args.expected_client_sha256,
        expected_audit_script_sha256=args.expected_audit_script_sha256,
        require_input_digests=bool(args.require_input_digests),
        max_factors_per_unit=max(3, args.max_factors_per_unit),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"stored_jd_ksa_contract_{stamp}.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"report={output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
