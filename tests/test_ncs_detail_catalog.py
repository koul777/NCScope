from __future__ import annotations

import json
import re
from pathlib import Path

from app.services import ncs_mcp_client


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "data"
    / "ncs_detail_catalog.json"
)


def test_lightweight_detail_catalog_is_code_unique_and_self_consistent() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows = payload["details"]
    codes = [row["code"] for row in rows]

    assert payload["schema_version"] == 1
    assert payload["classification_count"] == len(rows)
    assert len(rows) >= 1_000
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"\d{8}", code) for code in codes)
    assert all(int(row["unit_count"]) > 0 for row in rows)
    assert all(str(row.get("usage_yn") or "").upper() == "Y" for row in rows)


def test_active_detail_catalog_normalized_name_key_resolves_to_one_code() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows = payload["details"]
    codes_by_name_key: dict[str, set[str]] = {}

    for row in rows:
        if str(row.get("usage_yn") or "").upper() != "Y":
            continue
        code = str(row.get("code") or "")
        assert re.fullmatch(r"\d{8}", code)
        name_key = ncs_mcp_client._norm(row.get("name"))
        assert name_key
        codes_by_name_key.setdefault(name_key, set()).add(code)

    ambiguous = {
        name_key: sorted(codes)
        for name_key, codes in codes_by_name_key.items()
        if len(codes) != 1
    }
    assert ambiguous == {}


def test_explicit_detail_alias_targets_resolve_to_one_current_catalog_code() -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()
    details_by_name_key = ncs_mcp_client._official_details_by_name_key()

    failures: list[tuple[str, str, list[str]]] = []
    for source_key, aliases in ncs_mcp_client._DETAIL_QUERY_ALIASES_BY_KEY.items():
        for alias in aliases:
            rows = details_by_name_key.get(ncs_mcp_client._norm(alias), ())
            codes = sorted({str(row["code"]) for row in rows})
            if len(codes) != 1:
                failures.append((source_key, str(alias), codes))

    assert failures == []


def test_catalog_restores_official_punctuation_before_mcp_lookup() -> None:
    queries = ncs_mcp_client._detail_query_names("보일러설치정비")

    assert queries[0] == "보일러설치정비"
    assert "보일러설치·정비" in queries
    assert (
        ncs_mcp_client._detail_resolution_rule(
            "보일러설치정비",
            "보일러설치·정비",
        )
        == "catalog_display_restored"
    )


def test_inactive_detail_is_not_classified_as_current_official() -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()

    row = ncs_mcp_client.classify_official_detail_names(
        ["석유화학공정운전"]
    )[0]

    assert row["mappingState"] == "not_in_current_official_catalog"
    assert row["officialDetailCodes"] == []
