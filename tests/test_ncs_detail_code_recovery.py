from __future__ import annotations

from typing import Any

import pytest

from app.services import ncs_mcp_client as client


DETAIL = {"code": "12345678", "name": "detailalpha"}


def _catalog_unit(
    code: str,
    name: str,
) -> dict[str, Any]:
    return {
        "ncsClCd": code,
        "officialUnitBaseCode": code.split("_", 1)[0],
        "compeUnitName": name,
        "ncsSubdCdnm": DETAIL["name"],
        "canonicalDetailName": DETAIL["name"],
        "officialDetailCode": DETAIL["code"],
        "source": "ncs-unit-catalog-exact",
        "matchScore": 1.0,
        "isExactUnitNameMatch": True,
    }


def _mcp_row(
    catalog_row: dict[str, Any],
    *,
    code: str | None = None,
    name: str | None = None,
    detail_name: str | None = None,
    path_detail_code: str | None = None,
) -> dict[str, Any]:
    path = {
        "small": "smallalpha",
        "sub": detail_name or catalog_row["canonicalDetailName"],
    }
    if path_detail_code is not None:
        path.update(
            {
                "major_code": path_detail_code[0:2],
                "middle_code": path_detail_code[2:4],
                "small_code": path_detail_code[4:6],
                "sub_code": path_detail_code[6:8],
            }
        )
    return {
        "id": code or catalog_row["ncsClCd"],
        "text": name or catalog_row["compeUnitName"],
        "path": path,
    }


def _install_catalog(
    monkeypatch: pytest.MonkeyPatch,
    catalog_rows: list[dict[str, Any]],
) -> None:
    detail_key = client._norm(DETAIL["name"])
    detail_index = {detail_key: (DETAIL,)}
    full_index: dict[str, list[dict[str, Any]]] = {}
    base_index: dict[str, list[dict[str, Any]]] = {}
    for row in catalog_rows:
        full_index.setdefault(row["ncsClCd"], []).append(row)
        base_index.setdefault(row["officialUnitBaseCode"], []).append(row)
    monkeypatch.setattr(client, "_official_details_by_name_key", lambda: detail_index)
    monkeypatch.setattr(
        client,
        "_official_detail_names_by_key",
        lambda: {detail_key: (DETAIL["name"],)},
    )
    monkeypatch.setattr(
        client,
        "_official_unit_catalog_rows",
        lambda: tuple(catalog_rows),
    )
    monkeypatch.setattr(
        client,
        "_official_units_by_full_code",
        lambda: {key: tuple(rows) for key, rows in full_index.items()},
    )
    monkeypatch.setattr(
        client,
        "_official_units_by_base_code",
        lambda: {key: tuple(rows) for key, rows in base_index.items()},
    )
    monkeypatch.setattr(
        client,
        "_official_unit_base_codes_by_detail_code",
        lambda: {
            DETAIL["code"]: frozenset(
                row["officialUnitBaseCode"] for row in catalog_rows
            )
        },
    )


def _install_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "ncs_search"
        calls.append(dict(arguments))
        return responses.get(arguments["query"], {"results": []})

    monkeypatch.setattr(client, "_call_tool", fake_call_tool)
    return calls


def _two_units() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _catalog_unit("1234567801_25v1", "unitalpha"),
        _catalog_unit("1234567802_25v1", "unitbeta"),
    )


def test_broad_name_zero_recovers_by_exact_official_detail_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [
                    {
                        "id": "9999999901_25v1",
                        "text": "unrelated",
                        "path": {"sub": "otherdetail"},
                    }
                ]
            },
            DETAIL["code"]: {
                "results": [
                    _mcp_row(first, path_detail_code=DETAIL["code"]),
                    _mcp_row(second, path_detail_code=DETAIL["code"]),
                ]
            },
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert [row["ncsClCd"] for row in rows] == [
        first["ncsClCd"],
        second["ncsClCd"],
    ]
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]
    assert all(call["limit"] == 200 for call in calls)
    assert all(
        row["unitRetrievalKind"] == "official_detail_code_query_recovery"
        for row in rows
    )
    assert all(row["unitRetrievalQuery"] == DETAIL["code"] for row in rows)
    assert all(row["source"] == "ncs-mcp-detail-code-recovery" for row in rows)
    assert all(row["detailExpectedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailVerifiedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailRetrievalComplete"] is True for row in rows)
    assert all(row["detailRetrievalCapLimited"] is False for row in rows)
    assert all(row["detailPathCodeVerified"] is True for row in rows)


def test_result_retains_resolved_detail_coverage_when_both_queries_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": []},
            DETAIL["code"]: {"results": []},
        },
    )

    result = client.search_units_by_detail_result(
        [DETAIL["name"]],
        max_units=10,
    )

    assert result["items"] == []
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]
    assert result["exactCoverage"] == {
        "details": [
            {
                "sourceDetailName": DETAIL["name"],
                "mappingState": "official_detail_resolved",
                "officialDetailCode": DETAIL["code"],
                "officialDetailName": DETAIL["name"],
                "detailExpectedUnitBaseCount": 2,
                "detailVerifiedUnitBaseCount": 0,
                "detailRetrievalComplete": False,
                "detailRetrievalCapLimited": False,
            }
        ],
        "resolvedOfficialDetailCount": 1,
        "unresolvedDetailCount": 0,
    }


def test_result_marks_unknown_source_detail_unresolved_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    calls = _install_responses(monkeypatch, {})

    result = client.search_units_by_detail_result(
        ["unknown-source-label"],
        max_units=10,
    )

    assert result["items"] == []
    assert calls == []
    assert result["exactCoverage"]["details"][0]["mappingState"] == (
        "official_detail_unresolved"
    )
    assert result["exactCoverage"]["resolvedOfficialDetailCount"] == 0
    assert result["exactCoverage"]["unresolvedDetailCount"] == 1


def test_unknown_detail_keeps_delimiters_inside_brackets_as_one_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    calls = _install_responses(monkeypatch, {})

    result = client.search_units_by_detail_result(
        ["institution role (A,B)"],
        max_units=10,
    )

    assert calls == []
    assert result["exactCoverage"]["unresolvedDetailCount"] == 1
    assert result["exactCoverage"]["details"][0]["sourceDetailName"] == (
        "institution role (A,B)"
    )


def test_client_splitter_keeps_prior_matched_group_when_later_group_is_unmatched() -> None:
    official = "조선비계(족장, 발판, scaffolding)"

    assert client._split_detail_terms([f"{official}, 총무("]) == [
        official,
        "총무(",
    ]
    assert client._split_detail_terms([f"{official}|총무("]) == [
        official,
        "총무(",
    ]


def test_client_splitter_does_not_protect_a_mismatched_group() -> None:
    assert client._split_detail_terms(["총무(인사], 사무행정)"]) == [
        "총무(인사]",
        "사무행정)",
    ]


def test_client_splitter_does_not_protect_close_open_noise_spans() -> None:
    assert client._split_detail_terms(
        ["X][|조선비계(족장, 발판, scaffolding)|X]["]
    ) == [
        "X][",
        "조선비계(족장, 발판, scaffolding)",
    ]
    assert client._split_detail_terms(
        ["X)(/조선비계(족장, 발판, scaffolding)/X)("]
    ) == [
        "X)(",
        "조선비계(족장, 발판, scaffolding)",
    ]


def test_client_detail_splitter_splits_distinct_acronym_official_names() -> None:
    assert client._split_detail_terms(["PR/SCM"]) == ["PR", "SCM"]


def test_client_detail_splitter_preserves_official_slash_surface() -> None:
    assert client._split_detail_terms(["QM/QC관리, 총무"]) == [
        "QM/QC관리",
        "총무",
    ]


def test_client_splitter_preserves_official_slash_span_before_next_term() -> None:
    assert client._split_detail_terms(["QM/QC관리/총무"]) == [
        "QM/QC관리",
        "총무",
    ]


def test_partial_name_result_merges_only_missing_code_recovery_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": [_mcp_row(first)]},
            DETAIL["code"]: {
                "results": [_mcp_row(first), _mcp_row(second)]
            },
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert [row["ncsClCd"] for row in rows] == [
        first["ncsClCd"],
        second["ncsClCd"],
    ]
    assert [row["unitRetrievalKind"] for row in rows] == [
        "official_detail_name_query",
        "official_detail_code_query_recovery",
    ]
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]
    assert all(row["detailExpectedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailVerifiedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailRetrievalComplete"] is True for row in rows)
    assert all(row["detailRetrievalCapLimited"] is False for row in rows)


def test_complete_name_result_does_not_add_a_code_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [_mcp_row(first), _mcp_row(second)]
            }
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert len(rows) == 2
    assert [call["query"] for call in calls] == [DETAIL["name"]]
    assert all(
        row["unitRetrievalKind"] == "official_detail_name_query"
        for row in rows
    )
    assert all(row["detailExpectedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailVerifiedUnitBaseCount"] == 2 for row in rows)
    assert all(row["detailRetrievalComplete"] is True for row in rows)
    assert all(row["detailRetrievalCapLimited"] is False for row in rows)
    assert all(row["detailPathCodeVerified"] is False for row in rows)


def test_name_result_verifies_four_level_path_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [
                    _mcp_row(first, path_detail_code=DETAIL["code"])
                ]
            }
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert len(rows) == 1
    assert rows[0]["detailPathCodeVerified"] is True


@pytest.mark.parametrize("path_detail_code", ["87654321", "123456"])
def test_name_result_rejects_conflicting_or_partial_path_code(
    monkeypatch: pytest.MonkeyPatch,
    path_detail_code: str,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [
                    _mcp_row(first, path_detail_code=path_detail_code)
                ]
            },
            DETAIL["code"]: {"results": []},
        },
    )

    assert client.search_units_by_detail([DETAIL["name"]], max_units=10) == []


def test_partial_group_at_output_limit_does_not_add_a_recovery_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {DETAIL["name"]: {"results": [_mcp_row(first)]}},
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=1)

    assert [row["ncsClCd"] for row in rows] == [first["ncsClCd"]]
    assert [call["query"] for call in calls] == [DETAIL["name"]]
    assert rows[0]["detailExpectedUnitBaseCount"] == 2
    assert rows[0]["detailVerifiedUnitBaseCount"] == 1
    assert rows[0]["detailRetrievalComplete"] is False
    assert rows[0]["detailRetrievalCapLimited"] is True


def test_complete_group_reports_global_output_cap_without_losing_completeness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [_mcp_row(first), _mcp_row(second)]
            }
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=1)

    assert [row["ncsClCd"] for row in rows] == [first["ncsClCd"]]
    assert [call["query"] for call in calls] == [DETAIL["name"]]
    assert rows[0]["detailExpectedUnitBaseCount"] == 2
    assert rows[0]["detailVerifiedUnitBaseCount"] == 2
    assert rows[0]["detailRetrievalComplete"] is True
    assert rows[0]["detailRetrievalCapLimited"] is True


def test_partial_recovery_reports_upstream_gap_without_call_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": [_mcp_row(first)]},
            DETAIL["code"]: {"results": []},
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert [row["ncsClCd"] for row in rows] == [first["ncsClCd"]]
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]
    assert rows[0]["detailExpectedUnitBaseCount"] == 2
    assert rows[0]["detailVerifiedUnitBaseCount"] == 1
    assert rows[0]["detailRetrievalComplete"] is False
    assert rows[0]["detailRetrievalCapLimited"] is False


def test_code_recovery_rejects_unrelated_wrong_path_and_renamed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": []},
            DETAIL["code"]: {
                "results": [
                    _mcp_row(first, detail_name="wrongdetail"),
                    _mcp_row(second, name="renamedunit"),
                    {
                        "id": "8765432101_25v1",
                        "text": "unrelated",
                        "path": {"sub": DETAIL["name"]},
                    },
                ]
            },
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert rows == []
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]


def test_code_recovery_dedupes_versions_by_first_mcp_rank_and_honors_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_old = _catalog_unit("1234567801_24v1", "unitalpha")
    first_new = _catalog_unit("1234567801_25v1", "unitalpha")
    second = _catalog_unit("1234567802_25v1", "unitbeta")
    _install_catalog(monkeypatch, [first_old, first_new, second])
    calls = _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": []},
            DETAIL["code"]: {
                "results": [
                    _mcp_row(first_new),
                    _mcp_row(first_old),
                    _mcp_row(second),
                ]
            },
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=2)

    assert [row["ncsClCd"] for row in rows] == [
        first_new["ncsClCd"],
        second["ncsClCd"],
    ]
    assert [call["query"] for call in calls] == [DETAIL["name"], DETAIL["code"]]


def test_code_recovery_can_use_catalog_proven_base_version_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog_unit("1234567801_25v1", "unitalpha")
    _install_catalog(monkeypatch, [catalog])
    compatible_code = "1234567801_26v9"
    _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": []},
            DETAIL["code"]: {
                "results": [_mcp_row(catalog, code=compatible_code)]
            },
        },
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert [row["ncsClCd"] for row in rows] == [compatible_code]
    assert rows[0]["unitResolutionKind"] == "catalog_base_version_compatible"
    assert rows[0]["unitVersionCompatible"] is True
    assert rows[0]["unitRetrievalKind"] == "official_detail_code_query_recovery"


def test_exact_full_code_rejects_conflicting_semantic_identity_across_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact = _catalog_unit("1234567801_24v1", "unitalpha")
    conflicting_version = _catalog_unit("1234567801_25v1", "renamedalpha")
    _install_catalog(monkeypatch, [exact, conflicting_version])

    resolved = client._resolve_catalog_unit(
        mcp_code=exact["ncsClCd"],
        mcp_unit_name=exact["compeUnitName"],
        path_sub_name=exact["canonicalDetailName"],
        official_detail_code=exact["officialDetailCode"],
    )

    assert resolved is None


def test_same_semantic_identity_across_versions_supports_exact_and_new_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _catalog_unit("1234567801_24v1", "unitalpha")
    current = _catalog_unit("1234567801_25v1", "unitalpha")
    _install_catalog(monkeypatch, [old, current])

    exact = client._resolve_catalog_unit(
        mcp_code=old["ncsClCd"],
        mcp_unit_name=old["compeUnitName"],
        path_sub_name=old["canonicalDetailName"],
        official_detail_code=old["officialDetailCode"],
    )
    compatible = client._resolve_catalog_unit(
        mcp_code="1234567801_26v1",
        mcp_unit_name=old["compeUnitName"],
        path_sub_name=old["canonicalDetailName"],
        official_detail_code=old["officialDetailCode"],
    )

    assert exact is not None
    assert exact["unitVersionCompatible"] is False
    assert exact["catalogUnitCodes"] == [old["ncsClCd"], current["ncsClCd"]]
    assert compatible is not None
    assert compatible["unitVersionCompatible"] is True
    assert compatible["catalogUnitCodes"] == [old["ncsClCd"], current["ncsClCd"]]


def test_name_query_tool_error_is_not_treated_as_an_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    monkeypatch.setattr(
        client,
        "_rpc",
        lambda *_args, **_kwargs: {
            "isError": True,
            "content": [{"type": "text", "text": "sensitive upstream detail"}],
        },
    )

    with pytest.raises(client.NcsMcpError, match="tool ncs_search failed") as exc_info:
        client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert "sensitive upstream detail" not in str(exc_info.value)
    assert client._last_error == "ncs_mcp_tool_error"


def test_recovery_query_tool_error_is_not_treated_as_an_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _two_units()
    _install_catalog(monkeypatch, [first, second])
    calls: list[str] = []

    def fake_rpc(_method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = str((params or {}).get("arguments", {}).get("query") or "")
        calls.append(query)
        if query == DETAIL["name"]:
            return {"structuredContent": {"results": [_mcp_row(first)]}}
        return {
            "isError": True,
            "content": [{"type": "text", "text": "recovery backend failed"}],
        }

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    with pytest.raises(client.NcsMcpError, match="tool ncs_search failed"):
        client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert calls == [DETAIL["name"], DETAIL["code"]]


@pytest.mark.parametrize(
    "malformed_result",
    [
        {},
        {"results": "not-a-list"},
        {"results": {}},
        {"results": {"items": "not-a-list"}},
        {"results": ["not-an-object"]},
    ],
)
def test_search_schema_drift_is_not_treated_as_no_match(
    monkeypatch: pytest.MonkeyPatch,
    malformed_result: dict[str, Any],
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    monkeypatch.setattr(client, "_call_tool", lambda *_args, **_kwargs: malformed_result)

    with pytest.raises(client.NcsMcpError, match="search returned an invalid response"):
        client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert client._last_error == "ncs_mcp_search_schema_error"


def test_unknown_source_label_cannot_adopt_an_arbitrary_mcp_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda _name, arguments: calls.append(dict(arguments))
        or {"results": [_mcp_row(first)]},
    )

    rows = client.search_units_by_detail(["unknown-source-label"], max_units=10)

    assert rows == []
    assert calls == []


def test_alias_catalog_drift_with_two_official_targets_fails_closed_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _catalog_unit("1234567801_25v1", "unitalpha")
    second = {
        **_catalog_unit("8765432101_25v1", "unitbeta"),
        "ncsSubdCdnm": "detailbeta",
        "canonicalDetailName": "detailbeta",
        "officialDetailCode": "87654321",
    }
    detail_beta = {"code": "87654321", "name": "detailbeta"}
    detail_index = {
        client._norm(DETAIL["name"]): (DETAIL,),
        client._norm(detail_beta["name"]): (detail_beta,),
    }
    monkeypatch.setattr(client, "_official_details_by_name_key", lambda: detail_index)
    monkeypatch.setattr(
        client,
        "_official_detail_names_by_key",
        lambda: {key: tuple(row["name"] for row in rows) for key, rows in detail_index.items()},
    )
    monkeypatch.setattr(client, "_official_unit_catalog_rows", lambda: (first, second))
    monkeypatch.setattr(
        client,
        "_official_units_by_full_code",
        lambda: {first["ncsClCd"]: (first,), second["ncsClCd"]: (second,)},
    )
    monkeypatch.setattr(
        client,
        "_official_units_by_base_code",
        lambda: {
            first["officialUnitBaseCode"]: (first,),
            second["officialUnitBaseCode"]: (second,),
        },
    )
    monkeypatch.setattr(
        client,
        "_official_unit_base_codes_by_detail_code",
        lambda: {
            DETAIL["code"]: frozenset({first["officialUnitBaseCode"]}),
            detail_beta["code"]: frozenset({second["officialUnitBaseCode"]}),
        },
    )
    monkeypatch.setattr(
        client,
        "_DETAIL_QUERY_ALIASES_BY_KEY",
        {client._norm(DETAIL["name"]): (detail_beta["name"],)},
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        client,
        "_call_tool",
        lambda _name, arguments: calls.append(dict(arguments)) or {"results": []},
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert rows == []
    assert calls == []


def _conflicting_alias_row(
    catalog_row: dict[str, Any],
    conflict_kind: str,
) -> dict[str, Any]:
    row = _mcp_row(catalog_row)
    if conflict_kind == "code":
        row["unit_code"] = "1234567899_25v1"
    elif conflict_kind == "unit_name":
        row["unit_name"] = "conflicting-unit-name"
    elif conflict_kind == "detail_name":
        row["path"]["ncsSubdCdnm"] = "conflicting-detail-name"
    else:  # pragma: no cover - protects the test helper itself.
        raise AssertionError(conflict_kind)
    return row


@pytest.mark.parametrize("conflict_kind", ["code", "unit_name", "detail_name"])
def test_name_query_rejects_conflicting_identity_alias_fields(
    monkeypatch: pytest.MonkeyPatch,
    conflict_kind: str,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {
                "results": [_conflicting_alias_row(first, conflict_kind)]
            },
            DETAIL["code"]: {"results": []},
        },
    )

    assert client.search_units_by_detail([DETAIL["name"]], max_units=10) == []


@pytest.mark.parametrize("conflict_kind", ["code", "unit_name", "detail_name"])
def test_code_recovery_rejects_conflicting_identity_alias_fields(
    monkeypatch: pytest.MonkeyPatch,
    conflict_kind: str,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    _install_responses(
        monkeypatch,
        {
            DETAIL["name"]: {"results": []},
            DETAIL["code"]: {
                "results": [_conflicting_alias_row(first, conflict_kind)]
            },
        },
    )

    assert client.search_units_by_detail([DETAIL["name"]], max_units=10) == []


def test_consistent_duplicate_identity_alias_fields_remain_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = _two_units()
    _install_catalog(monkeypatch, [first])
    row = _mcp_row(first)
    row["unit_code"] = row["id"]
    row["unit_name"] = row["text"]
    row["path"]["ncsSubdCdnm"] = row["path"]["sub"]
    _install_responses(
        monkeypatch,
        {DETAIL["name"]: {"results": [row]}},
    )

    rows = client.search_units_by_detail([DETAIL["name"]], max_units=10)

    assert [item["ncsClCd"] for item in rows] == [first["ncsClCd"]]


@pytest.mark.parametrize(
    "unit_code",
    [
        "0201010106_13v1",
        "0202030207_22v4",
        "2304010211_22v1",
    ],
)
def test_public_catalog_unit_with_wrong_mcp_path_remains_rejected(
    monkeypatch: pytest.MonkeyPatch,
    unit_code: str,
) -> None:
    catalog_row = next(
        row
        for row in client._official_unit_catalog_rows()
        if row["ncsClCd"] == unit_code
    )
    official_detail = next(
        row
        for rows in client._official_details_by_name_key().values()
        for row in rows
        if row["code"] == catalog_row["officialDetailCode"]
    )
    wrong_detail_name = next(
        row["name"]
        for rows in client._official_details_by_name_key().values()
        for row in rows
        if row["code"] != official_detail["code"]
    )
    calls: list[dict[str, Any]] = []

    def fake_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "ncs_search"
        calls.append(dict(arguments))
        if arguments["query"] != official_detail["code"]:
            return {"results": []}
        return {
            "results": [
                _mcp_row(catalog_row, detail_name=wrong_detail_name)
            ]
        }

    monkeypatch.setattr(client, "_call_tool", fake_call_tool)

    rows = client.search_units_by_detail(
        [official_detail["name"]],
        max_units=200,
    )

    assert rows == []
    assert sum(
        call["query"] == official_detail["code"] for call in calls
    ) == 1
