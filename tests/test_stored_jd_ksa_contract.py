from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_stored_jd_ksa_contract.py"
)
SPEC = importlib.util.spec_from_file_location("audit_stored_jd_ksa_contract", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    benchmark = tmp_path / "benchmark.csv"
    with benchmark.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ksa_probe_codes"])
        writer.writeheader()
        writer.writerow({"ksa_probe_codes": "unit-1;unit-2"})
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "units": [
                    {"code": "unit-1", "name": "one", "detail_name": "detail"},
                    {"code": "unit-2", "name": "two", "detail_name": "detail"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return benchmark, catalog


def _row(code: str, kind: str) -> dict:
    return {
        "ncsClCd": code,
        "requestedUnitCode": code,
        "responseUnitCode": code,
        "unitIdentityVerified": True,
        "factorName": f"{kind} factor",
        "ksaTypeName": kind,
        "performanceCriteria": ["criterion"],
        "factorSource": "ncs-mcp",
        "source": "ncs-mcp",
        "ksaStatus": "official",
        "isOfficialKsa": True,
    }


def test_audit_requires_all_three_official_ksa_types_and_criteria(
    tmp_path: Path,
) -> None:
    benchmark, catalog = _inputs(tmp_path)

    def fetch(units: list[dict], limit: int) -> list[dict]:
        assert len(units) == 2
        assert limit == 12
        return [
            _row(str(unit["ncsClCd"]), kind)
            for unit in units
            for kind in ("지식", "기술", "태도")
        ]

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        expected_unit_codes=2,
        fetch_ksa=fetch,
    )

    assert result["passed"] is True
    assert result["probe_unit_codes"] == 2
    assert result["unit_codes_with_complete_ksa_type_set"] == 2
    assert result["complete_ksa_type_set_pct"] == 100.0
    assert result["ksa_row_count"] == 6
    assert result["rows_with_performance_criteria"] == 6
    assert result["rows_with_configured_mcp_contract_markers"] == 6
    assert result["input_digests_checked"] is False
    assert result["input_digests_match"] is None
    assert result["failures"] == []


def test_audit_fails_closed_for_incomplete_types_criteria_and_provenance(
    tmp_path: Path,
) -> None:
    benchmark, catalog = _inputs(tmp_path)

    def fetch(_units: list[dict], _limit: int) -> list[dict]:
        bad = _row("unit-1", "지식")
        bad["performanceCriteria"] = []
        bad["isOfficialKsa"] = False
        return [bad, _row("unit-1", "기술")]

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        expected_unit_codes=2,
        fetch_ksa=fetch,
    )

    assert result["passed"] is False
    assert result["unit_codes_with_complete_ksa_type_set"] == 0
    assert result["missing_row_code_count"] == 1
    assert result["incomplete_type_code_count"] == 2
    assert set(result["failures"]) == {
        "unit_codes_without_ksa_rows",
        "unit_codes_without_complete_ksa_type_set",
        "ksa_rows_without_performance_criteria",
        "ksa_rows_without_configured_mcp_contract_markers",
    }


def test_audit_binds_benchmark_catalog_code_set_and_client_hashes(
    tmp_path: Path,
) -> None:
    benchmark, catalog = _inputs(tmp_path)

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        expected_unit_codes=2,
        expected_benchmark_rows=2,
        expected_benchmark_sha256="0" * 64,
        expected_catalog_sha256="1" * 64,
        expected_code_set_sha256="2" * 64,
        expected_client_sha256="3" * 64,
        expected_audit_script_sha256="4" * 64,
        require_input_digests=True,
        fetch_ksa=lambda _units, _limit: (_ for _ in ()).throw(
            AssertionError("digest mismatch must fail before MCP fetch")
        ),
    )

    assert result["passed"] is False
    assert {
        "benchmark_sha256_mismatch",
        "catalog_sha256_mismatch",
        "code_set_sha256_mismatch",
        "client_sha256_mismatch",
        "audit_script_sha256_mismatch",
    }.issubset(result["failures"])
    assert result["benchmark_row_count"] == 1
    assert result["expected_benchmark_rows"] == 2
    assert result["input_digests_match"] is False


def test_audit_requires_all_expected_digests_when_requested(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        require_input_digests=True,
        fetch_ksa=lambda _units, _limit: [],
    )

    assert "missing_required_input_digests" in result["failures"]


def test_audit_accepts_uppercase_sha256_expectations(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)
    benchmark_sha = audit._sha256(benchmark)
    catalog_sha = audit._sha256(catalog)
    codes = audit._probe_codes(benchmark)
    client_sha = audit._sha256(
        audit.ROOT / "app" / "services" / "ncs_mcp_client.py"
    )
    script_sha = audit._sha256(SCRIPT_PATH)

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        expected_benchmark_sha256=benchmark_sha.upper(),
        expected_catalog_sha256=catalog_sha.upper(),
        expected_code_set_sha256=audit._code_set_sha256(codes).upper(),
        expected_client_sha256=client_sha.upper(),
        expected_audit_script_sha256=script_sha.upper(),
        require_input_digests=True,
        fetch_ksa=lambda units, _limit: [
            _row(str(unit["ncsClCd"]), kind)
            for unit in units
            for kind in ("knowledge", "skill", "attitude")
        ],
    )

    assert result["passed"] is True
    assert result["input_digests_match"] is True


def test_audit_can_bind_the_entire_active_catalog_without_a_benchmark(
    tmp_path: Path,
) -> None:
    _benchmark, catalog = _inputs(tmp_path)

    def fetch(units: list[dict], _limit: int) -> list[dict]:
        return [
            _row(str(unit["ncsClCd"]), kind)
            for unit in units
            for kind in ("knowledge", "skill", "attitude")
        ]

    result = audit.audit_ksa_contract(
        None,
        catalog,
        all_active_catalog_units=True,
        expected_unit_codes=2,
        fetch_ksa=fetch,
    )

    assert result["passed"] is True
    assert result["input_scope"] == "all_active_catalog_unit_codes"
    assert result["benchmark_sha256"] == ""
    assert result["unit_codes_with_complete_ksa_type_set"] == 2


def test_audit_rejects_unknown_or_missing_ksa_types(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)

    def fetch(units: list[dict], _limit: int) -> list[dict]:
        rows = [
            _row(str(unit["ncsClCd"]), kind)
            for unit in units
            for kind in ("knowledge", "skill", "attitude")
        ]
        rows.append(_row("unit-1", "unknown"))
        rows.append(_row("unit-2", ""))
        return rows

    result = audit.audit_ksa_contract(benchmark, catalog, fetch_ksa=fetch)

    assert result["passed"] is False
    assert result["invalid_ksa_type_row_count"] == 2
    assert "ksa_rows_with_unknown_or_missing_type" in result["failures"]


def test_audit_rejects_unbound_response_unit_identity(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)

    def fetch(units: list[dict], _limit: int) -> list[dict]:
        rows = [
            _row(str(unit["ncsClCd"]), kind)
            for unit in units
            for kind in ("knowledge", "skill", "attitude")
        ]
        rows[0]["responseUnitCode"] = "WRONG"
        return rows

    result = audit.audit_ksa_contract(benchmark, catalog, fetch_ksa=fetch)

    assert result["passed"] is False
    assert result["rows_with_verified_unit_identity"] == 5
    assert result["invalid_unit_identity_row_count"] == 1
    assert "ksa_rows_without_verified_unit_identity" in result["failures"]


def test_audit_writes_fail_closed_result_fields_for_fetch_error(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        fetch_ksa=lambda _units, _limit: (_ for _ in ()).throw(
            RuntimeError("remote response body must not be copied")
        ),
    )

    assert result["passed"] is False
    assert result["fetch_error_type"] == "RuntimeError"
    assert "ncs_mcp_fetch_failed" in result["failures"]
    assert "remote response body" not in json.dumps(result)


def test_audit_counts_only_inspected_rows_for_criteria_and_markers(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)

    result = audit.audit_ksa_contract(
        benchmark,
        catalog,
        fetch_ksa=lambda _units, _limit: [None, _row("unexpected", "knowledge")],
    )

    assert result["ksa_row_count"] == 2
    assert result["inspected_response_row_count"] == 0
    assert result["rows_with_performance_criteria"] == 0
    assert result["rows_with_configured_mcp_contract_markers"] == 0


def test_audit_rejects_duplicate_or_miscounted_catalog_rows(tmp_path: Path) -> None:
    benchmark, catalog = _inputs(tmp_path)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["units"][1]["code"] = "unit-1"
    payload["unit_count"] = 2
    catalog.write_text(json.dumps(payload), encoding="utf-8")

    try:
        audit.audit_ksa_contract(benchmark, catalog, fetch_ksa=lambda *_args: [])
    except ValueError as exc:
        assert "duplicate codes" in str(exc)
    else:
        raise AssertionError("duplicate catalog codes must fail closed")
