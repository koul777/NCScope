from __future__ import annotations

import re

from app.services.ncs_match_gate import (
    NCS_MATCH_GATE_VERSION,
    build_ncs_match_bundle,
)
from app.services.ncs_mcp_client import NcsMcpError


DETAIL_CODE = "02020301"
DETAIL_NAME = "비서"


def _result(*, expected: int = 11, verified: int = 11, complete: bool = True, cap: bool = False):
    units = []
    for number in range(1, verified + 1):
        base = f"{DETAIL_CODE}{number:02d}"
        units.append(
            {
                "ncsClCd": f"{base}_25v1",
                "compeUnitName": f"비서 능력단위 {number}",
                "officialUnitBaseCode": base,
                "officialUnitName": f"비서 능력단위 {number}",
                "officialDetailCode": DETAIL_CODE,
                "officialDetailName": DETAIL_NAME,
                "ncsSubdCdnm": DETAIL_NAME,
                "unitCatalogVerified": True,
                "detailPathCodeVerified": True,
                "unitResolutionKind": "catalog_full_code_exact",
                "catalogUnitCodes": [f"{base}_25v1"],
            }
        )
    return {
        "items": units,
        "exactCoverage": {
            "details": [
                {
                    "sourceDetailName": DETAIL_NAME,
                    "mappingState": "official_detail_resolved",
                    "officialDetailCode": DETAIL_CODE,
                    "officialDetailName": DETAIL_NAME,
                    "detailExpectedUnitBaseCount": expected,
                    "detailVerifiedUnitBaseCount": verified,
                    "detailRetrievalComplete": complete,
                    "detailRetrievalCapLimited": cap,
                }
            ]
        },
    }


def test_secretary_eleven_of_eleven_is_generation_eligible_and_fingerprinted():
    result = _result()
    bundle = build_ncs_match_bundle([DETAIL_NAME], search_fn=lambda *_args, **_kwargs: result)
    again = build_ncs_match_bundle([DETAIL_NAME], search_fn=lambda *_args, **_kwargs: result)

    assert bundle["status"] == "exact_complete"
    assert bundle["eligible_for_generation"] is True
    assert bundle["reason_codes"] == []
    assert bundle["matcher_version"] == NCS_MATCH_GATE_VERSION
    assert len(bundle["units"]) == 11
    assert re.fullmatch(r"[0-9a-f]{64}", bundle["fingerprint"])
    assert bundle["fingerprint"] == again["fingerprint"]


def test_ten_of_eleven_is_incomplete():
    bundle = build_ncs_match_bundle(
        [DETAIL_NAME],
        search_fn=lambda *_args, **_kwargs: _result(
            expected=11, verified=10, complete=False
        ),
    )

    assert bundle["status"] == "incomplete"
    assert bundle["eligible_for_generation"] is False
    assert "detail_verified_count_mismatch" in bundle["reason_codes"]
    assert "unit_count_mismatch" in bundle["reason_codes"]


def test_cap_limited_result_is_incomplete_even_when_counts_match():
    bundle = build_ncs_match_bundle(
        [DETAIL_NAME],
        search_fn=lambda *_args, **_kwargs: _result(cap=True),
    )

    assert bundle["status"] == "incomplete"
    assert "detail_retrieval_cap_limited" in bundle["reason_codes"]


def test_unresolved_mapping_is_blocked():
    result = _result(expected=0, verified=0, complete=False)
    result["items"] = []
    result["exactCoverage"]["details"][0].update(
        mappingState="official_detail_unresolved",
        officialDetailCode="",
        officialDetailName="",
    )

    bundle = build_ncs_match_bundle(
        [DETAIL_NAME], search_fn=lambda *_args, **_kwargs: result
    )

    assert bundle["status"] == "unresolved"
    assert "detail_mapping_unresolved" in bundle["reason_codes"]


def test_contextual_source_is_blocked_without_calling_mcp():
    calls = []

    def lookup(*_args, **_kwargs):
        calls.append(True)
        return _result()

    bundle = build_ncs_match_bundle(
        [DETAIL_NAME], detail_source="contextual", search_fn=lookup
    )

    assert calls == []
    assert bundle["status"] == "unresolved"
    assert bundle["reason_codes"] == ["detail_source_not_explicit"]


def test_gate_uses_same_alio_bullet_split_terms_as_mcp_client():
    captured: list[list[str]] = []

    def lookup(terms, **_kwargs):
        captured.append(list(terms))
        return {
            "items": [],
            "exactCoverage": {
                "details": [
                    {
                        "sourceDetailName": term,
                        "mappingState": "official_detail_unresolved",
                    }
                    for term in terms
                ]
            },
        }

    bundle = build_ncs_match_bundle(["경영기획 ¡ 자금"], search_fn=lookup)

    assert captured == [["경영기획", "자금"]]
    assert bundle["lookup_terms"] == ["경영기획", "자금"]
    assert bundle["status"] == "unresolved"


def test_unit_ownership_mismatch_is_incomplete():
    result = _result()
    result["items"][0]["officialDetailCode"] = "02020302"
    result["items"][0]["officialDetailName"] = "사무행정"
    result["items"][0]["ncsSubdCdnm"] = "사무행정"

    bundle = build_ncs_match_bundle(
        [DETAIL_NAME], search_fn=lambda *_args, **_kwargs: result
    )

    assert bundle["status"] == "incomplete"
    assert bundle["eligible_for_generation"] is False
    assert "unit_detail_prefix_mismatch" in bundle["reason_codes"]
    assert "unit_ownership_unmapped" in bundle["reason_codes"]


def test_only_strict_catalog_identity_is_generation_eligible():
    mutations = (
        ("detailPathCodeVerified", False, "unit_detail_path_unverified"),
        ("unitResolutionKind", "catalog_base_version_compatible", "unit_not_catalog_full_code_exact"),
        ("officialUnitName", "", "unit_name_missing"),
        ("catalogUnitCodes", [], "unit_full_code_not_in_catalog"),
    )
    for field, value, expected_reason in mutations:
        result = _result()
        result["items"][0][field] = value

        bundle = build_ncs_match_bundle(
            [DETAIL_NAME], search_fn=lambda *_args, _result=result, **_kwargs: _result
        )

        assert bundle["status"] == "incomplete"
        assert bundle["eligible_for_generation"] is False
        assert expected_reason in bundle["reason_codes"]


def test_ncs_mcp_error_returns_unavailable_bundle():
    def unavailable(*_args, **_kwargs):
        raise NcsMcpError("temporary outage")

    bundle = build_ncs_match_bundle([DETAIL_NAME], search_fn=unavailable)

    assert bundle["status"] == "unavailable"
    assert bundle["eligible_for_generation"] is False
    assert bundle["reason_codes"] == ["ncs_mcp_unavailable"]

