from __future__ import annotations

from typing import Any

import pytest

from app.services import ncs_mcp_client as client


DETAIL_ALPHA = {"code": "12345678", "name": "detailalpha"}
DETAIL_BETA = {"code": "87654321", "name": "detailbeta"}


def _install_active_details(
    monkeypatch: pytest.MonkeyPatch,
    details: list[dict[str, str]] | None = None,
) -> None:
    active_details = details or [DETAIL_ALPHA, DETAIL_BETA]
    detail_index = {
        client._norm(detail["name"]): (detail,)
        for detail in active_details
    }
    monkeypatch.setattr(client, "_official_details_by_name_key", lambda: detail_index)
    monkeypatch.setattr(
        client,
        "_official_detail_names_by_key",
        lambda: {
            key: tuple(row["name"] for row in rows)
            for key, rows in detail_index.items()
        },
    )
    monkeypatch.setattr(
        client,
        "_active_official_detail_codes",
        lambda: frozenset(detail["code"] for detail in active_details),
    )


def _mcp_row(
    *,
    code: str = "1234567801_25v1",
    text: str = "unitalpha",
    detail_name: str = "detailalpha",
) -> dict[str, Any]:
    return {
        "id": code,
        "text": text,
        "path": {
            "small": "smallalpha",
            "sub": detail_name,
        },
    }


def _conflicting_alias_row(conflict_kind: str) -> dict[str, Any]:
    row = _mcp_row()
    if conflict_kind == "code":
        row["unit_code"] = "1234567899_25v1"
    elif conflict_kind == "unit_name":
        row["unit_name"] = "conflicting-unit-name"
    elif conflict_kind == "detail_name":
        row["path"]["ncsSubdCdnm"] = "conflicting-detail-name"
    else:  # pragma: no cover - protects the helper itself.
        raise AssertionError(conflict_kind)
    return row


@pytest.mark.parametrize(
    "malformed_result",
    [
        {},
        {"results": "not-a-list"},
        {"results": {}},
        {"results": {"items": "not-a-list"}},
    ],
)
def test_suggestion_schema_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    malformed_result: dict[str, Any],
) -> None:
    _install_active_details(monkeypatch, [DETAIL_ALPHA])
    monkeypatch.setattr(client, "_call_tool", lambda *_args, **_kwargs: malformed_result)

    with pytest.raises(client.NcsMcpError, match="search returned an invalid response"):
        client.suggest_units_by_text(["freeform"], max_units=5)

    assert client._last_error == "ncs_mcp_search_schema_error"


@pytest.mark.parametrize("conflict_kind", ["code", "unit_name", "detail_name"])
def test_suggestion_rejects_conflicting_identity_alias_fields(
    monkeypatch: pytest.MonkeyPatch,
    conflict_kind: str,
) -> None:
    _install_active_details(monkeypatch, [DETAIL_ALPHA])
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda *_args, **_kwargs: {"results": [_conflicting_alias_row(conflict_kind)]},
    )

    assert client.suggest_units_by_text(["freeform"], max_units=5) == []


def test_suggestion_rejects_malformed_unit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_active_details(monkeypatch, [DETAIL_ALPHA])
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda *_args, **_kwargs: {"results": [_mcp_row(code="1234")]} ,
    )

    assert client.suggest_units_by_text(["freeform"], max_units=5) == []


def test_suggestion_rejects_official_detail_code_name_scope_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_active_details(monkeypatch, [DETAIL_ALPHA, DETAIL_BETA])
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda *_args, **_kwargs: {
            "results": [
                _mcp_row(
                    code="1234567801_25v1",
                    detail_name=DETAIL_BETA["name"],
                )
            ]
        },
    )

    assert client.suggest_units_by_text(["freeform"], max_units=5) == []


def test_suggestion_keeps_stale_but_active_candidate_when_code_scope_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_active_details(monkeypatch, [DETAIL_ALPHA, DETAIL_BETA])
    stale_name = "legacy-detailalpha"
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda *_args, **_kwargs: {
            "results": [
                _mcp_row(
                    code="1234567801_25v1",
                    detail_name=stale_name,
                )
            ]
        },
    )

    rows = client.suggest_units_by_text(["freeform"], max_units=5)

    assert [row["ncsClCd"] for row in rows] == ["1234567801_25v1"]
    assert rows[0]["ncsSubdCdnm"] == stale_name
    assert rows[0]["canonicalDetailName"] == stale_name
    assert rows[0]["source"] == "ncs-mcp-suggest"
