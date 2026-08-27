from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import probe_ncs_detail_connections as probe


ROOT = Path(__file__).parents[1]


def _detail(code: str, name: str) -> dict[str, Any]:
    return {"code": code, "name": name, "usage_yn": "Y"}


def _unit(detail: dict[str, Any], ordinal: int) -> dict[str, Any]:
    base = f"{detail['code']}{ordinal:02d}"
    return {
        "code": f"{base}_25v1",
        "base_code": base,
        "name": f"unit-{base}",
        "detail_code": detail["code"],
        "detail_name": detail["name"],
    }


def _write_catalogs(
    tmp_path: Path,
    details: list[dict[str, Any]],
    units: list[dict[str, Any]],
) -> tuple[Path, Path]:
    detail_path = tmp_path / "details.json"
    unit_path = tmp_path / "units.json"
    detail_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "classification_count": len(details),
                "details": details,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    unit_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "unit_count": len(units),
                "units": units,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return detail_path, unit_path


def _verified_row(
    detail: dict[str, Any],
    base: str,
    *,
    retrieval_kind: str = "official_detail_name_query",
    resolution_kind: str = "catalog_full_code_exact",
) -> dict[str, Any]:
    full_code = f"{base}_25v1"
    unit_name = f"unit-{base}"
    return {
        "ncsClCd": full_code,
        "officialUnitBaseCode": base,
        "officialUnitName": unit_name,
        "compeUnitName": unit_name,
        "mcpUnitName": unit_name,
        "ncsSubdCdnm": detail["name"],
        "officialDetailCode": detail["code"],
        "officialDetailName": detail["name"],
        "unitCatalogVerified": True,
        "unitVersionCompatible": resolution_kind
        == "catalog_base_version_compatible",
        "catalogUnitCodes": [full_code],
        "unitRetrievalKind": retrieval_kind,
        "unitRetrievalQuery": (
            detail["code"]
            if retrieval_kind == "official_detail_code_query_recovery"
            else detail["name"]
        ),
        "unitResolutionKind": resolution_kind,
    }


def _synthetic_scope(tmp_path: Path) -> tuple[
    probe.CatalogScope,
    list[dict[str, Any]],
]:
    details = [
        _detail("11111111", "alpha"),
        _detail("22222222", "beta"),
        _detail("33333333", "gamma"),
        _detail("44444444", "delta"),
    ]
    units = [
        _unit(details[0], 1),
        _unit(details[0], 2),
        _unit(details[1], 1),
        _unit(details[1], 2),
        _unit(details[2], 1),
        _unit(details[3], 1),
    ]
    detail_path, unit_path = _write_catalogs(tmp_path, details, units)
    return probe.load_catalog_scope(detail_path, unit_path), details


def test_real_public_catalog_scope_has_stable_expected_counts() -> None:
    scope = probe.load_catalog_scope(
        ROOT / "app/data/ncs_detail_catalog.json",
        ROOT / "app/data/ncs_unit_catalog.json",
    )

    assert scope.active_detail_count == 1094
    assert scope.catalog_unit_count == 13282
    assert scope.stable_base_count == 13281
    assert sum(map(len, scope.expected_bases.values())) == 13281


def test_aggregate_complete_partial_zero_unexpected_and_kinds(
    tmp_path: Path,
) -> None:
    scope, raw_details = _synthetic_scope(tmp_path)
    rows_by_name = {
        "alpha": [
            _verified_row(raw_details[0], "1111111101"),
            _verified_row(raw_details[0], "1111111102"),
        ],
        "beta": [_verified_row(raw_details[1], "2222222201")],
        "gamma": [],
        "delta": [
            _verified_row(raw_details[3], "4444444401"),
            _verified_row(
                raw_details[3],
                "4444444499",
                retrieval_kind="official_detail_code_query_recovery",
                resolution_kind="catalog_base_version_compatible",
            ),
        ],
    }

    report = probe.probe_connections(
        scope,
        scope.details,
        search_fn=lambda names, **_kwargs: rows_by_name[names[0]],
        clock=iter([10.0, 12.3456]).__next__,
    )

    assert report["status"] == "fail"
    assert report["errors"] == ["unexpected_base_identity"]
    assert report["counts"] == {
        "processed_details": 4,
        "unprocessed_details": 0,
        "complete_details": 2,
        "partial_details": 1,
        "zero_details": 1,
        "expected_base_codes": 6,
        "verified_expected_base_codes": 4,
        "missing_base_codes": 2,
        "returned_rows": 5,
        "unexpected_base_codes": 1,
        "identity_violation_rows": 0,
        "identity_violation_findings": 0,
        "search_invocations": 4,
    }
    assert report["coverage_ratio"] == pytest.approx(4 / 6)
    assert report["retrieval_kind_counts"] == {
        "official_detail_code_query_recovery": 1,
        "official_detail_name_query": 4,
        "invalid_or_missing": 0,
    }
    assert report["resolution_kind_counts"] == {
        "catalog_base_version_compatible": 1,
        "catalog_full_code_exact": 4,
        "invalid_or_missing": 0,
    }
    assert [row["detail_code"] for row in report["top_gaps"]] == [
        "22222222",
        "33333333",
    ]
    assert report["unexpected_identities"] == [
        {"detail_code": "44444444", "base_code": "4444444499"}
    ]
    assert report["elapsed_seconds"] == 2.346
    assert "alpha" not in probe.serialize_report(report)


def test_duplicate_and_unverified_rows_fail_identity_contract(tmp_path: Path) -> None:
    detail = _detail("11111111", "alpha")
    catalog_unit = _unit(detail, 1)
    detail_path, unit_path = _write_catalogs(tmp_path, [detail], [catalog_unit])
    scope = probe.load_catalog_scope(detail_path, unit_path)
    first = _verified_row(detail, "1111111101")
    duplicate = dict(first)
    unverified = _verified_row(detail, "1111111101")
    unverified["unitCatalogVerified"] = False

    report = probe.probe_connections(
        scope,
        scope.details,
        search_fn=lambda *_args, **_kwargs: [first, duplicate, unverified],
    )

    assert report["status"] == "fail"
    assert report["errors"] == ["returned_identity_violation"]
    assert report["counts"]["identity_violation_rows"] == 2
    assert report["identity_violation_reason_counts"] == {
        "catalog_verification_missing": 1,
        "duplicate_returned_base_code": 2,
    }
    assert report["identity_violation_detail_codes"] == ["11111111"]


def test_cli_partial_gaps_exit_zero_and_count_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = [
        _detail("11111111", "alpha"),
        _detail("22222222", "beta"),
    ]
    units = [_unit(details[0], 1), _unit(details[0], 2), _unit(details[1], 1)]
    detail_path, unit_path = _write_catalogs(tmp_path, details, units)
    output_path = tmp_path / "probe.json"
    monkeypatch.setattr(
        probe.client,
        "_call_tool",
        lambda _name, _arguments: {"results": []},
    )

    def fake_search(names: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        detail = next(row for row in details if row["name"] == names[0])
        probe.client._call_tool(
            "ncs_search",
            {"query": detail["name"], "scope": "unit", "limit": 200},
        )
        probe.client._call_tool(
            "ncs_search",
            {"query": detail["code"], "scope": "unit", "limit": 200},
        )
        if detail["code"] == "11111111":
            return [_verified_row(detail, "1111111101")]
        return []

    monkeypatch.setattr(probe.client, "search_units_by_detail", fake_search)

    exit_code = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
            "--output",
            str(output_path),
        ]
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["status"] == "pass_with_gaps"
    assert report["counts"]["partial_details"] == 1
    assert report["counts"]["zero_details"] == 1
    assert report["calls"] == {
        "instrumented": True,
        "ncs_search_total": 4,
        "detail_name_query": 2,
        "detail_code_query": 2,
        "other_query": 0,
    }
    assert output_path.read_text(encoding="utf-8").endswith("\n")
    assert list(report) == sorted(report)


def test_cli_exact_filter_is_bounded_to_one_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scope, raw_details = _synthetic_scope(tmp_path)
    detail_path, unit_path = _write_catalogs(
        tmp_path,
        raw_details,
        [
            _unit(detail, ordinal)
            for detail, count in zip(raw_details, (2, 2, 1, 1))
            for ordinal in range(1, count + 1)
        ],
    )
    selected = raw_details[2]
    monkeypatch.setattr(
        probe.client,
        "search_units_by_detail",
        lambda *_args, **_kwargs: [_verified_row(selected, "3333333301")],
    )

    exit_code = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
            "--detail-code",
            selected["code"],
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert scope.active_detail_count == 4
    assert exit_code == 0
    assert report["scope"]["selected_details"] == 1
    assert report["scope"]["selected_detail_codes"] == [selected["code"]]
    assert report["counts"]["search_invocations"] == 1


def test_cli_unexpected_identity_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detail = _detail("11111111", "alpha")
    detail_path, unit_path = _write_catalogs(tmp_path, [detail], [_unit(detail, 1)])
    monkeypatch.setattr(
        probe.client,
        "search_units_by_detail",
        lambda *_args, **_kwargs: [
            _verified_row(detail, "1111111101"),
            _verified_row(detail, "1111111199"),
        ],
    )

    exit_code = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["status"] == "fail"
    assert report["errors"] == ["unexpected_base_identity"]


def test_cli_runtime_error_is_sanitized_and_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detail = _detail("11111111", "alpha")
    detail_path, unit_path = _write_catalogs(tmp_path, [detail], [_unit(detail, 1)])

    def fail_search(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("SECRET remote payload")

    monkeypatch.setattr(probe.client, "search_units_by_detail", fail_search)

    exit_code = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
        ]
    )
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == 2
    assert report["status"] == "error"
    assert report["errors"] == ["search_runtime_error"]
    assert report["runtime_failure_detail_code"] == detail["code"]
    assert "SECRET" not in output


def test_unknown_filter_and_catalog_identity_drift_exit_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detail = _detail("11111111", "alpha")
    detail_path, unit_path = _write_catalogs(tmp_path, [detail], [_unit(detail, 1)])
    monkeypatch.setattr(
        probe.client,
        "search_units_by_detail",
        lambda *_args, **_kwargs: [],
    )

    filter_exit = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
            "--detail-code",
            "99999999",
        ]
    )
    filter_report = json.loads(capsys.readouterr().out)

    assert filter_exit == 2
    assert filter_report["errors"] == ["unknown_detail_code_filter"]

    malformed_unit = _unit(detail, 1)
    malformed_unit["base_code"] = "9999999901"
    detail_path, unit_path = _write_catalogs(
        tmp_path,
        [detail],
        [malformed_unit],
    )
    catalog_exit = probe.main(
        [
            "--detail-catalog",
            str(detail_path),
            "--unit-catalog",
            str(unit_path),
        ]
    )
    catalog_report = json.loads(capsys.readouterr().out)

    assert catalog_exit == 2
    assert catalog_report["errors"] == ["unit_catalog_identity_error"]


def test_serialization_is_stable_and_contains_no_timestamp() -> None:
    report = {
        "z": 1,
        "a": {"한글": True},
    }

    first = probe.serialize_report(report)
    second = probe.serialize_report(report)

    assert first == second
    assert first.startswith('{\n  "a"')
    assert first.endswith("\n")
    assert "generated_at" not in first
