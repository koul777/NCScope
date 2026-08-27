"""Fail-closed regression tests for client-submitted NCS unit selections."""

from typing import Any

import pytest

from app.services import ncs_mcp_client as ncs_mcp


def _catalog_case() -> dict[str, Any]:
    details_by_code: dict[str, list[dict[str, str]]] = {}
    for rows in ncs_mcp._official_details_by_name_key().values():
        for row in rows:
            details_by_code.setdefault(row["code"], []).append(row)

    for row in ncs_mcp._official_unit_catalog_rows():
        details = details_by_code.get(str(row["officialDetailCode"]), [])
        if (
            len(details) == 1
            and ncs_mcp._norm(details[0]["name"])
            == ncs_mcp._norm(row["canonicalDetailName"])
        ):
            return row
    raise AssertionError("expected at least one unambiguous active catalog unit")


def _semantic_catalog_codes(row: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(candidate["ncsClCd"])
            for candidate in ncs_mcp._official_units_by_base_code()[
                str(row["officialUnitBaseCode"])
            ]
            if ncs_mcp._norm(candidate["compeUnitName"])
            == ncs_mcp._norm(row["compeUnitName"])
            and candidate["officialDetailCode"] == row["officialDetailCode"]
            and ncs_mcp._norm(candidate["canonicalDetailName"])
            == ncs_mcp._norm(row["canonicalDetailName"])
        )
    )


def _absent_version_code(row: dict[str, Any]) -> str:
    base_code = str(row["officialUnitBaseCode"])
    known_codes = ncs_mcp._official_units_by_full_code()
    for suffix in ("SELECTEDTEST", "SELECTEDTEST2", "SELECTEDTEST3"):
        candidate = f"{base_code}_{suffix}"
        if candidate not in known_codes:
            return candidate
    raise AssertionError("expected an unused alphanumeric catalog suffix")


def test_resolve_official_unit_selection_returns_canonical_exact_provenance():
    row = _catalog_case()

    result = ncs_mcp.resolve_official_unit_selection(
        f" {row['ncsClCd']} ",
        f" {row['compeUnitName']} ",
        f" {row['canonicalDetailName']} ",
    )

    assert result == {
        "ncsClCd": row["ncsClCd"],
        "compeUnitName": row["compeUnitName"],
        "ncsSubdCdnm": row["canonicalDetailName"],
        "canonicalDetailName": row["canonicalDetailName"],
        "officialDetailCode": row["officialDetailCode"],
        "officialDetailName": row["canonicalDetailName"],
        "detailResolutionKind": "selected_catalog_verified",
        "detailResolutionRule": "selected_catalog_verified",
        "officialUnitBaseCode": row["officialUnitBaseCode"],
        "officialUnitName": row["compeUnitName"],
        "unitResolutionKind": "catalog_full_code_exact",
        "unitCatalogVerified": True,
        "unitVersionCompatible": False,
        "catalogUnitCodes": _semantic_catalog_codes(row),
        "source": "selected-ncs-catalog-verified",
    }


def test_resolve_official_unit_selection_allows_only_proven_base_version_identity():
    row = _catalog_case()
    input_code = _absent_version_code(row)

    result = ncs_mcp.resolve_official_unit_selection(
        input_code,
        row["compeUnitName"],
        row["canonicalDetailName"],
    )

    assert result is not None
    assert result["ncsClCd"] == input_code
    assert result["officialUnitBaseCode"] == row["officialUnitBaseCode"]
    assert result["officialUnitName"] == row["compeUnitName"]
    assert result["unitResolutionKind"] == "catalog_base_version_compatible"
    assert result["unitCatalogVerified"] is True
    assert result["unitVersionCompatible"] is True
    assert result["catalogUnitCodes"] == _semantic_catalog_codes(row)


@pytest.mark.parametrize(
    ("code_transform", "unit_transform", "detail_transform"),
    [
        (lambda _value: "12345678", lambda value: value, lambda value: value),
        (lambda value: value, lambda _value: "", lambda value: value),
        (lambda value: value, lambda value: f"{value} mismatch", lambda value: value),
        (lambda value: value, lambda value: value, lambda _value: ""),
        (lambda value: value, lambda value: value, lambda value: f"{value} mismatch"),
        (lambda _value: "9999999901_FUTURE", lambda value: value, lambda value: value),
    ],
)
def test_resolve_official_unit_selection_rejects_unverified_input(
    code_transform,
    unit_transform,
    detail_transform,
):
    row = _catalog_case()

    assert (
        ncs_mcp.resolve_official_unit_selection(
            code_transform(row["ncsClCd"]),
            unit_transform(row["compeUnitName"]),
            detail_transform(row["canonicalDetailName"]),
        )
        is None
    )


def test_resolve_official_unit_selection_rejects_duplicate_exact_code(monkeypatch):
    row = _catalog_case()
    monkeypatch.setattr(
        ncs_mcp,
        "_official_units_by_full_code",
        lambda: {row["ncsClCd"]: (row, dict(row))},
    )

    assert (
        ncs_mcp.resolve_official_unit_selection(
            row["ncsClCd"],
            row["compeUnitName"],
            row["canonicalDetailName"],
        )
        is None
    )


def test_resolve_official_unit_selection_rejects_ambiguous_base_identity(monkeypatch):
    row = _catalog_case()
    input_code = _absent_version_code(row)
    conflicting_row = {**row, "compeUnitName": f"{row['compeUnitName']} conflict"}
    monkeypatch.setattr(ncs_mcp, "_official_units_by_full_code", lambda: {})
    monkeypatch.setattr(
        ncs_mcp,
        "_official_units_by_base_code",
        lambda: {row["officialUnitBaseCode"]: (row, conflicting_row)},
    )

    assert (
        ncs_mcp.resolve_official_unit_selection(
            input_code,
            row["compeUnitName"],
            row["canonicalDetailName"],
        )
        is None
    )


def test_resolve_official_unit_selection_rejects_duplicate_active_detail(monkeypatch):
    row = _catalog_case()
    detail = {
        "code": row["officialDetailCode"],
        "name": row["canonicalDetailName"],
    }
    monkeypatch.setattr(
        ncs_mcp,
        "_official_details_by_name_key",
        lambda: {"one": (detail,), "two": (dict(detail),)},
    )

    assert (
        ncs_mcp.resolve_official_unit_selection(
            row["ncsClCd"],
            row["compeUnitName"],
            row["canonicalDetailName"],
        )
        is None
    )
