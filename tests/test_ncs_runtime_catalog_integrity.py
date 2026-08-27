from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services import ncs_mcp_client as client


_CACHED_CATALOG_LOADERS = (
    client._official_details_by_name_key,
    client._official_detail_names_by_key,
    client._active_official_detail_codes,
    client._official_unit_catalog_rows,
    client._official_units_by_name_key,
    client._official_units_by_full_code,
    client._official_units_by_base_code,
    client._official_unit_base_codes_by_detail_code,
)


def _clear_catalog_caches() -> None:
    for loader in _CACHED_CATALOG_LOADERS:
        loader.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_catalog_caches():
    _clear_catalog_caches()
    yield
    _clear_catalog_caches()


def _catalog_path(filename: str) -> Path:
    return Path(client.__file__).resolve().parents[1] / "data" / filename


def test_runtime_catalog_digest_constants_match_lf_normalized_bundles() -> None:
    detail_bytes = _catalog_path("ncs_detail_catalog.json").read_bytes()
    unit_bytes = _catalog_path("ncs_unit_catalog.json").read_bytes()

    assert hashlib.sha256(detail_bytes.replace(b"\r\n", b"\n")).hexdigest() == (
        client._OFFICIAL_DETAIL_CATALOG_SHA256
    )
    assert hashlib.sha256(unit_bytes.replace(b"\r\n", b"\n")).hexdigest() == (
        client._OFFICIAL_UNIT_CATALOG_SHA256
    )


def test_detail_catalog_valid_json_digest_drift_fails_before_mcp_call(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    original_read_bytes = Path.read_bytes

    def drifted_detail(path: Path) -> bytes:
        payload = original_read_bytes(path)
        if path.name == "ncs_detail_catalog.json":
            return payload + b" "
        return payload

    monkeypatch.setattr(Path, "read_bytes", drifted_detail)
    call = mocker.patch("app.services.ncs_mcp_client._call_tool")

    with pytest.raises(client.NcsMcpError, match="detail catalog is unavailable"):
        client.search_units_by_detail(["경영기획"], max_units=1)

    call.assert_not_called()


def test_unit_catalog_valid_json_digest_drift_fails_before_mcp_call(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    original_read_bytes = Path.read_bytes

    def drifted_unit(path: Path) -> bytes:
        payload = original_read_bytes(path)
        if path.name == "ncs_unit_catalog.json":
            return payload + b" "
        return payload

    monkeypatch.setattr(Path, "read_bytes", drifted_unit)
    call = mocker.patch("app.services.ncs_mcp_client._call_tool")

    with pytest.raises(client.NcsMcpError, match="unit catalog is unavailable"):
        client.search_units_by_detail(["경영기획"], max_units=1)

    call.assert_not_called()


def test_detail_catalog_digest_accepts_crlf_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes

    def crlf_detail(path: Path) -> bytes:
        payload = original_read_bytes(path)
        if path.name == "ncs_detail_catalog.json":
            return payload.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        return payload

    monkeypatch.setattr(Path, "read_bytes", crlf_detail)

    rows = client._official_details_by_name_key()

    assert sum(len(group) for group in rows.values()) == 1_094


def test_catalog_digest_is_computed_once_per_cached_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes
    reads = 0

    def counted_read(path: Path) -> bytes:
        nonlocal reads
        if path.name == "ncs_detail_catalog.json":
            reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)

    first = client._official_details_by_name_key()
    second = client._official_details_by_name_key()

    assert first is second
    assert reads == 1
