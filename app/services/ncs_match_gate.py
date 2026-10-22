"""Fail-closed NCS detail-to-unit matching gate for interview generation.

The public NCS MCP client already performs catalog and response-path
verification.  This module adds the final, shared generation boundary: every
submitted explicit detail must resolve to one official eight-digit detail and
must bring back its complete, uncapped set of catalog-verified ability units.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.services.ncs_mcp_client import (
    NcsMcpError,
    _split_detail_terms,
    search_units_by_detail_result,
)


NCS_MATCH_GATE_VERSION = "ncs-exact-complete-v1"

SearchUnits = Callable[..., dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _name_key(value: Any) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        unicodedata.normalize("NFKC", _text(value)).casefold(),
        flags=re.UNICODE,
    )


def _lookup_terms(values: Iterable[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    # Keep the gate's source terms aligned with the MCP client's coverage
    # terms.  Otherwise a PDF-bullet-packed input is split by the client but
    # compared here as one label and is rejected as a term mismatch.
    for value in _split_detail_terms([_text(value) for value in values or ()]):
        term = _text(value)
        key = _name_key(term)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(term)
    return output


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _plain(value: Any) -> Any:
    """Return a deterministic JSON-compatible copy of an MCP value."""

    if isinstance(value, Mapping):
        return {
            str(key): _plain(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _finish(
    *,
    status: str,
    reasons: Iterable[str],
    lookup_terms: list[str],
    details: list[dict[str, Any]],
    units: list[dict[str, Any]],
    matcher_version: str,
) -> dict[str, Any]:
    reason_codes = sorted({_text(reason) for reason in reasons if _text(reason)})
    bundle: dict[str, Any] = {
        "status": status,
        "eligible_for_generation": status == "exact_complete",
        "reason_codes": reason_codes,
        "lookup_terms": list(lookup_terms),
        "details": _plain(details),
        "units": _plain(units),
        "matcher_version": _text(matcher_version) or NCS_MATCH_GATE_VERSION,
    }
    canonical = json.dumps(
        bundle,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    bundle["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    return bundle


def build_ncs_match_bundle(
    terms: Iterable[Any] | None,
    *,
    detail_source: str = "explicit",
    max_units: int = 200,
    search_fn: SearchUnits | None = None,
    matcher_version: str = NCS_MATCH_GATE_VERSION,
) -> dict[str, Any]:
    """Resolve official NCS details and decide whether generation may start.

    ``search_fn`` has the same contract as
    :func:`search_units_by_detail_result` and is injectable for an offline
    orchestrator or unit tests.  Only ``detail_source='explicit'`` is eligible;
    contextual/inferred parser output is deliberately never promoted to an
    official match.
    """

    lookup_terms = _lookup_terms(terms)
    version = _text(matcher_version) or NCS_MATCH_GATE_VERSION
    if _text(detail_source).casefold() != "explicit":
        return _finish(
            status="unresolved",
            reasons=["detail_source_not_explicit"],
            lookup_terms=lookup_terms,
            details=[],
            units=[],
            matcher_version=version,
        )
    if not lookup_terms:
        return _finish(
            status="unresolved",
            reasons=["lookup_terms_empty"],
            lookup_terms=[],
            details=[],
            units=[],
            matcher_version=version,
        )

    lookup = search_fn or search_units_by_detail_result
    try:
        result = lookup(lookup_terms, max_units=max(1, int(max_units)))
    except NcsMcpError:
        return _finish(
            status="unavailable",
            reasons=["ncs_mcp_unavailable"],
            lookup_terms=lookup_terms,
            details=[],
            units=[],
            matcher_version=version,
        )

    if not isinstance(result, Mapping):
        return _finish(
            status="unresolved",
            reasons=["invalid_match_response"],
            lookup_terms=lookup_terms,
            details=[],
            units=[],
            matcher_version=version,
        )

    coverage = result.get("exactCoverage")
    raw_details = coverage.get("details") if isinstance(coverage, Mapping) else None
    details = [dict(row) for row in raw_details or () if isinstance(row, Mapping)]
    raw_units = result.get("items")
    units = [dict(row) for row in raw_units or () if isinstance(row, Mapping)]
    reasons: set[str] = set()

    if not isinstance(raw_details, list):
        reasons.add("detail_coverage_missing")
    if not isinstance(raw_units, list):
        reasons.add("invalid_match_response")

    details_by_term: dict[str, list[dict[str, Any]]] = {}
    for detail in details:
        details_by_term.setdefault(_name_key(detail.get("sourceDetailName")), []).append(detail)

    selected_details: list[dict[str, Any]] = []
    for term in lookup_terms:
        matches = details_by_term.get(_name_key(term), [])
        if len(matches) != 1:
            reasons.add(
                "detail_coverage_missing" if not matches else "detail_coverage_ambiguous"
            )
            continue
        detail = matches[0]
        selected_details.append(detail)
        if _text(detail.get("mappingState")) != "official_detail_resolved":
            reasons.add("detail_mapping_unresolved")

    if len(details) != len(selected_details):
        reasons.add("detail_coverage_term_mismatch")

    resolved_codes: set[str] = set()
    detail_identity: dict[str, str] = {}
    for detail in selected_details:
        detail_code = _text(detail.get("officialDetailCode"))
        detail_name = _text(detail.get("officialDetailName"))
        expected = _count(detail.get("detailExpectedUnitBaseCount"))
        verified = _count(detail.get("detailVerifiedUnitBaseCount"))
        if re.fullmatch(r"\d{8}", detail_code) is None:
            reasons.add("detail_code_invalid")
            continue
        if not detail_name:
            reasons.add("detail_name_missing")
        previous_name = detail_identity.get(detail_code)
        if previous_name is not None and _name_key(previous_name) != _name_key(detail_name):
            reasons.add("detail_identity_conflict")
        detail_identity[detail_code] = detail_name
        resolved_codes.add(detail_code)
        if expected is None or expected <= 0:
            reasons.add("detail_expected_count_invalid")
        if verified is None or expected is None or verified != expected:
            reasons.add("detail_verified_count_mismatch")
        if detail.get("detailRetrievalComplete") is not True:
            reasons.add("detail_retrieval_incomplete")
        if detail.get("detailRetrievalCapLimited") is not False:
            reasons.add("detail_retrieval_cap_limited")

    bases_by_detail: dict[str, set[str]] = {code: set() for code in resolved_codes}
    seen_bases: set[str] = set()
    for unit in units:
        base_code = _text(unit.get("officialUnitBaseCode"))
        full_code = _text(unit.get("ncsClCd") or unit.get("unit_code"))
        unit_name = _text(unit.get("officialUnitName"))
        unit_detail_code = _text(unit.get("officialDetailCode"))
        unit_detail_name = _text(unit.get("officialDetailName"))
        owner_name = _text(unit.get("ncsSubdCdnm"))
        resolution_kind = _text(unit.get("unitResolutionKind"))
        raw_catalog_codes = unit.get("catalogUnitCodes")
        catalog_codes = {
            _text(value)
            for value in raw_catalog_codes
            if _text(value)
        } if isinstance(raw_catalog_codes, list) else set()

        if unit.get("unitCatalogVerified") is not True:
            reasons.add("unit_catalog_unverified")
        if unit.get("detailPathCodeVerified") is not True:
            reasons.add("unit_detail_path_unverified")
        if resolution_kind != "catalog_full_code_exact":
            reasons.add("unit_not_catalog_full_code_exact")
        if not unit_name:
            reasons.add("unit_name_missing")
        if re.fullmatch(r"\d{10}", base_code) is None:
            reasons.add("unit_base_code_invalid")
        if re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", full_code) is None:
            reasons.add("unit_full_code_invalid")
        if full_code not in catalog_codes:
            reasons.add("unit_full_code_not_in_catalog")
        if base_code and full_code and full_code.split("_", 1)[0] != base_code:
            reasons.add("unit_code_mismatch")
        if base_code and unit_detail_code and base_code[:8] != unit_detail_code:
            reasons.add("unit_detail_prefix_mismatch")
        if unit_detail_code not in resolved_codes:
            reasons.add("unit_ownership_unmapped")
            continue
        expected_name = detail_identity.get(unit_detail_code, "")
        if _name_key(unit_detail_name) != _name_key(expected_name):
            reasons.add("unit_detail_name_mismatch")
        if _name_key(owner_name) != _name_key(expected_name):
            reasons.add("unit_owner_name_mismatch")
        if base_code in seen_bases:
            reasons.add("unit_base_duplicate")
        elif re.fullmatch(r"\d{10}", base_code):
            seen_bases.add(base_code)
            bases_by_detail[unit_detail_code].add(base_code)

    for detail in selected_details:
        detail_code = _text(detail.get("officialDetailCode"))
        expected = _count(detail.get("detailExpectedUnitBaseCount"))
        verified = _count(detail.get("detailVerifiedUnitBaseCount"))
        unique_count = len(bases_by_detail.get(detail_code, set()))
        if expected is None or verified is None or not (
            expected > 0 and expected == verified == unique_count
        ):
            reasons.add("unit_count_mismatch")

    unresolved_codes = {
        "detail_coverage_ambiguous",
        "detail_coverage_missing",
        "detail_coverage_term_mismatch",
        "detail_mapping_unresolved",
        "invalid_match_response",
    }
    if reasons & unresolved_codes:
        status = "unresolved"
    elif reasons:
        status = "incomplete"
    else:
        status = "exact_complete"
    return _finish(
        status=status,
        reasons=reasons,
        lookup_terms=lookup_terms,
        details=details,
        units=units,
        matcher_version=version,
    )

