from __future__ import annotations

import pytest

from app.services import ncs_mcp_client


def _catalog_unit() -> dict[str, object]:
    return dict(ncs_mcp_client._official_unit_catalog_rows()[0])


def _selected_unit(
    catalog: dict[str, object],
    *,
    code: str | None = None,
    unit_name: str | None = None,
    detail_name: str | None = None,
) -> dict[str, str]:
    return {
        "ncsClCd": code or str(catalog["ncsClCd"]),
        "compeUnitName": (
            str(catalog["compeUnitName"]) if unit_name is None else unit_name
        ),
        "ncsSubdCdnm": (
            str(catalog["ncsSubdCdnm"]) if detail_name is None else detail_name
        ),
    }


def _unit_detail_response(
    catalog: dict[str, object],
    *,
    code: str | None = None,
    unit_name: str | None = None,
    detail_code: str | None = None,
    detail_name: str | None = None,
) -> dict[str, object]:
    response_detail_code = detail_code or str(catalog["officialDetailCode"])
    classification = {
        "major_code": response_detail_code[0:2],
        "middle_code": response_detail_code[2:4],
        "small_code": response_detail_code[4:6],
        "sub_code": response_detail_code[6:8],
        "sub": (
            str(catalog["ncsSubdCdnm"])
            if detail_name is None
            else detail_name
        ),
    }
    return {
        "data": {
            "unit": {
                "unit_code": code or str(catalog["ncsClCd"]),
                "unit_name": (
                    str(catalog["compeUnitName"])
                    if unit_name is None
                    else unit_name
                ),
                "classification": classification,
            },
            "elements": [
                {
                    "element_id": "E1",
                    "element_name": "Element",
                    "ksa": [
                        {
                            "text": "Official knowledge",
                            "ksa_type": "knowledge",
                            "ksa_no": "K1",
                        }
                    ],
                }
            ],
        }
    }


def test_official_ksa_preflight_rejects_missing_identity_before_network(mocker):
    catalog = _catalog_unit()
    call = mocker.patch("app.services.ncs_mcp_client._call_tool")

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="official catalogs"):
        ncs_mcp_client.get_ksa_by_units([{"ncsClCd": catalog["ncsClCd"]}])

    call.assert_not_called()


def test_official_ksa_preflight_rejects_same_code_wrong_name_before_network(mocker):
    catalog = _catalog_unit()
    call = mocker.patch("app.services.ncs_mcp_client._call_tool")
    selected = _selected_unit(catalog, unit_name="tampered unit name")

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="official catalogs"):
        ncs_mcp_client.get_ksa_by_units([selected])

    call.assert_not_called()


def test_official_ksa_rejects_response_unit_name_mismatch(mocker):
    catalog = _catalog_unit()
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=_unit_detail_response(catalog, unit_name="tampered unit name"),
    )

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="name identity mismatch"):
        ncs_mcp_client.get_ksa_by_units([_selected_unit(catalog)])


def test_official_ksa_rejects_response_classification_code_mismatch(mocker):
    catalog = _catalog_unit()
    expected_code = str(catalog["officialDetailCode"])
    wrong_suffix = "99" if expected_code[-2:] != "99" else "98"
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=_unit_detail_response(
            catalog,
            detail_code=f"{expected_code[:6]}{wrong_suffix}",
        ),
    )

    with pytest.raises(
        ncs_mcp_client.NcsMcpError,
        match="classification code mismatch",
    ):
        ncs_mcp_client.get_ksa_by_units([_selected_unit(catalog)])


def test_official_ksa_rejects_response_classification_name_mismatch(mocker):
    catalog = _catalog_unit()
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=_unit_detail_response(
            catalog,
            detail_name="tampered detail name",
        ),
    )

    with pytest.raises(
        ncs_mcp_client.NcsMcpError,
        match="classification name mismatch",
    ):
        ncs_mcp_client.get_ksa_by_units([_selected_unit(catalog)])


@pytest.mark.parametrize(
    ("conflict_kind", "error_pattern"),
    [
        ("code", "unit detail identity mismatch"),
        ("unit_name", "name identity mismatch"),
        ("detail_name", "classification name mismatch"),
    ],
)
def test_official_ksa_rejects_conflicting_response_alias_fields(
    mocker,
    conflict_kind,
    error_pattern,
):
    catalog = _catalog_unit()
    response = _unit_detail_response(catalog)
    detail_unit = response["data"]["unit"]
    if conflict_kind == "code":
        detail_unit["ncsClCd"] = "9999999999_99v9"
    elif conflict_kind == "unit_name":
        detail_unit["compeUnitName"] = "conflicting unit name"
    else:
        detail_unit["classification"]["ncsSubdCdnm"] = "conflicting detail name"
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=response,
    )

    with pytest.raises(ncs_mcp_client.NcsMcpError, match=error_pattern):
        ncs_mcp_client.get_ksa_by_units([_selected_unit(catalog)])


def test_official_ksa_accepts_consistent_response_alias_fields(mocker):
    catalog = _catalog_unit()
    response = _unit_detail_response(catalog)
    detail_unit = response["data"]["unit"]
    detail_unit["ncsClCd"] = detail_unit["unit_code"]
    detail_unit["compeUnitName"] = detail_unit["unit_name"]
    detail_unit["classification"]["ncsSubdCdnm"] = detail_unit["classification"]["sub"]
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=response,
    )

    rows = ncs_mcp_client.get_ksa_by_units(
        [_selected_unit(catalog)],
        max_factors_per_unit=1,
    )

    assert rows[0]["unitIdentityVerified"] is True
    assert rows[0]["unitResponsePathVerified"] is True


def test_official_ksa_emits_only_canonical_catalog_identity_and_provenance(mocker):
    catalog = _catalog_unit()
    call = mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=_unit_detail_response(catalog),
    )
    selected = _selected_unit(
        catalog,
        unit_name=f"  {catalog['compeUnitName']}  ",
        detail_name=f"\n{catalog['ncsSubdCdnm']}\t",
    )

    rows = ncs_mcp_client.get_ksa_by_units([selected], max_factors_per_unit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["ncsClCd"] == catalog["ncsClCd"]
    assert row["compeUnitName"] == catalog["compeUnitName"]
    assert row["ncsSubdCdnm"] == catalog["ncsSubdCdnm"]
    assert row["officialUnitName"] == catalog["compeUnitName"]
    assert row["officialDetailCode"] == catalog["officialDetailCode"]
    assert row["officialDetailName"] == catalog["ncsSubdCdnm"]
    assert row["responseDetailCode"] == catalog["officialDetailCode"]
    assert row["unitCatalogVerified"] is True
    assert row["unitResponsePathVerified"] is True
    assert row["unitIdentityVerified"] is True
    assert row["unitIdentityVerificationKind"] == (
        "immutable_catalog_and_mcp_response_path_exact"
    )
    assert row["unitVersionCompatible"] is False
    assert row["unitResolutionKind"] == "catalog_full_code_exact"
    assert row["catalogUnitCodes"] == [catalog["ncsClCd"]]
    assert call.call_args.args[1]["unit_code"] == catalog["ncsClCd"]


def test_official_ksa_allows_verified_new_suffix_for_one_stable_base_identity(mocker):
    catalog = _catalog_unit()
    base_code = str(catalog["officialUnitBaseCode"])
    synthetic_code = f"{base_code}_99v9"
    assert synthetic_code not in ncs_mcp_client._official_units_by_full_code()
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value=_unit_detail_response(catalog, code=synthetic_code),
    )

    rows = ncs_mcp_client.get_ksa_by_units(
        [_selected_unit(catalog, code=synthetic_code)],
        max_factors_per_unit=1,
    )

    assert rows[0]["ncsClCd"] == synthetic_code
    assert rows[0]["unitVersionCompatible"] is True
    assert rows[0]["unitResolutionKind"] == "catalog_base_version_compatible"
    assert rows[0]["unitCatalogVerified"] is True
    assert rows[0]["unitResponsePathVerified"] is True
