"""Probe public NCS detail-to-unit coverage without exposing MCP payloads.

The probe reads only the bundled public official catalogs and calls the
production ``search_units_by_detail`` boundary once for each selected active
detail.  Output contains aggregate counts and validated code identities only;
remote response bodies, unit text, and exception messages are never emitted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import ncs_mcp_client as client  # noqa: E402


DETAIL_CODE = re.compile(r"^\d{8}$")
BASE_CODE = re.compile(r"^\d{10}$")
UNIT_CODE = re.compile(r"^\d{10}(?:_[0-9A-Za-z]+)?$")
MAX_FILTERS = 64
MAX_TOP_GAPS = 100

RETRIEVAL_KINDS = (
    "official_detail_code_query_recovery",
    "official_detail_name_query",
)
RESOLUTION_KINDS = (
    "catalog_base_version_compatible",
    "catalog_full_code_exact",
)


class ProbeInputError(RuntimeError):
    """A sanitized, stable input/catalog failure safe for probe output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OfficialDetail:
    code: str
    name: str


@dataclass(frozen=True)
class CatalogScope:
    details: tuple[OfficialDetail, ...]
    expected_bases: dict[str, frozenset[str]]
    active_detail_count: int
    catalog_unit_count: int
    stable_base_count: int


@dataclass
class SearchCallCounter:
    """Count MCP search calls while retaining no query text or response data."""

    total: int = 0
    detail_name_query: int = 0
    detail_code_query: int = 0
    other_query: int = 0

    @contextmanager
    def installed(self) -> Iterator[None]:
        original = client._call_tool

        def counted(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if name == "ncs_search":
                self.total += 1
                query = str(arguments.get("query") or "").strip()
                if DETAIL_CODE.fullmatch(query):
                    self.detail_code_query += 1
                elif query:
                    self.detail_name_query += 1
                else:
                    self.other_query += 1
            return original(name, arguments)

        client._call_tool = counted
        try:
            yield
        finally:
            client._call_tool = original

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrumented": True,
            "ncs_search_total": self.total,
            "detail_name_query": self.detail_name_query,
            "detail_code_query": self.detail_code_query,
            "other_query": self.other_query,
        }


def _norm(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def _load_json(path: Path, expected_key: str) -> tuple[dict[str, Any], list[Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeInputError("catalog_read_error") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get(expected_key), list):
        raise ProbeInputError("catalog_schema_error")
    return payload, payload[expected_key]


def load_catalog_scope(detail_path: Path, unit_path: Path) -> CatalogScope:
    """Load and validate the public catalog identities used as expectations."""

    detail_payload, detail_rows = _load_json(detail_path, "details")
    unit_payload, unit_rows = _load_json(unit_path, "units")
    if detail_payload.get("classification_count") != len(detail_rows):
        raise ProbeInputError("detail_catalog_count_mismatch")
    if unit_payload.get("unit_count") != len(unit_rows):
        raise ProbeInputError("unit_catalog_count_mismatch")

    details_by_code: dict[str, OfficialDetail] = {}
    detail_codes_by_name: dict[str, set[str]] = defaultdict(set)
    for row in detail_rows:
        if not isinstance(row, dict):
            raise ProbeInputError("detail_catalog_schema_error")
        if str(row.get("usage_yn") or "").strip().upper() != "Y":
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not DETAIL_CODE.fullmatch(code) or not _norm(name):
            raise ProbeInputError("detail_catalog_identity_error")
        if code in details_by_code:
            raise ProbeInputError("detail_catalog_duplicate_code")
        details_by_code[code] = OfficialDetail(code=code, name=name)
        detail_codes_by_name[_norm(name)].add(code)
    if not details_by_code:
        raise ProbeInputError("active_detail_catalog_empty")
    if any(len(codes) != 1 for codes in detail_codes_by_name.values()):
        raise ProbeInputError("active_detail_name_collision")

    expected: dict[str, set[str]] = defaultdict(set)
    full_codes: set[str] = set()
    base_identities: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in unit_rows:
        if not isinstance(row, dict):
            raise ProbeInputError("unit_catalog_schema_error")
        code = str(row.get("code") or "").strip()
        base = str(row.get("base_code") or "").strip()
        name = str(row.get("name") or "").strip()
        detail_code = str(row.get("detail_code") or "").strip()
        detail_name = str(row.get("detail_name") or "").strip()
        official_detail = details_by_code.get(detail_code)
        if (
            not UNIT_CODE.fullmatch(code)
            or not BASE_CODE.fullmatch(base)
            or code.split("_", 1)[0] != base
            or base[:8] != detail_code
            or official_detail is None
            or not _norm(name)
            or _norm(detail_name) != _norm(official_detail.name)
        ):
            raise ProbeInputError("unit_catalog_identity_error")
        if code in full_codes:
            raise ProbeInputError("unit_catalog_duplicate_full_code")
        full_codes.add(code)
        expected[detail_code].add(base)
        base_identities[base].add((detail_code, _norm(detail_name), _norm(name)))
    if not full_codes:
        raise ProbeInputError("unit_catalog_empty")
    if any(len(identities) != 1 for identities in base_identities.values()):
        raise ProbeInputError("unit_catalog_base_identity_collision")
    if any(not expected.get(code) for code in details_by_code):
        raise ProbeInputError("active_detail_without_unit")

    details = tuple(details_by_code[code] for code in sorted(details_by_code))
    return CatalogScope(
        details=details,
        expected_bases={
            code: frozenset(expected[code]) for code in sorted(expected)
        },
        active_detail_count=len(details),
        catalog_unit_count=len(unit_rows),
        stable_base_count=len(base_identities),
    )


def select_details(
    scope: CatalogScope,
    *,
    detail_codes: Sequence[str] = (),
    detail_names: Sequence[str] = (),
) -> tuple[OfficialDetail, ...]:
    """Apply bounded exact diagnostic filters; multiple filters form a union."""

    if len(detail_codes) + len(detail_names) > MAX_FILTERS:
        raise ProbeInputError("detail_filter_limit_exceeded")
    code_filters = {str(code or "").strip() for code in detail_codes}
    if any(not DETAIL_CODE.fullmatch(code) for code in code_filters):
        raise ProbeInputError("invalid_detail_code_filter")
    name_filters = {_norm(name) for name in detail_names if str(name or "").strip()}
    if len(name_filters) != len({str(name).strip() for name in detail_names}):
        # Empty or normalization-equivalent duplicates are ambiguous CLI input.
        raise ProbeInputError("invalid_detail_name_filter")

    details_by_code = {detail.code: detail for detail in scope.details}
    details_by_name: dict[str, list[OfficialDetail]] = defaultdict(list)
    for detail in scope.details:
        details_by_name[_norm(detail.name)].append(detail)
    if code_filters - details_by_code.keys():
        raise ProbeInputError("unknown_detail_code_filter")
    if name_filters - details_by_name.keys():
        raise ProbeInputError("unknown_detail_name_filter")
    if any(len(details_by_name[name]) != 1 for name in name_filters):
        raise ProbeInputError("ambiguous_detail_name_filter")

    if not code_filters and not name_filters:
        return scope.details
    selected_codes = set(code_filters)
    for name in name_filters:
        selected_codes.add(details_by_name[name][0].code)
    return tuple(details_by_code[code] for code in sorted(selected_codes))


def _enum_bucket(value: Any, allowed: tuple[str, ...]) -> str:
    text = str(value or "").strip()
    return text if text in allowed else "invalid_or_missing"


def _row_violation_reasons(
    row: Any,
    detail: OfficialDetail,
    *,
    seen_bases: set[str],
) -> tuple[list[str], str | None]:
    if not isinstance(row, dict):
        return ["non_object_row"], None

    reasons: list[str] = []
    full_code = str(row.get("ncsClCd") or "").strip()
    base_code = str(row.get("officialUnitBaseCode") or "").strip()
    if not UNIT_CODE.fullmatch(full_code):
        reasons.append("invalid_unit_code")
    if not BASE_CODE.fullmatch(base_code):
        reasons.append("invalid_base_code")
    if (
        UNIT_CODE.fullmatch(full_code)
        and BASE_CODE.fullmatch(base_code)
        and full_code.split("_", 1)[0] != base_code
    ):
        reasons.append("unit_base_code_mismatch")
    if BASE_CODE.fullmatch(base_code) and base_code[:8] != detail.code:
        reasons.append("base_detail_code_mismatch")
    if row.get("unitCatalogVerified") is not True:
        reasons.append("catalog_verification_missing")
    if row.get("detailPathCodeVerified") is not True:
        reasons.append("detail_path_code_verification_missing")
    if str(row.get("officialDetailCode") or "").strip() != detail.code:
        reasons.append("official_detail_code_mismatch")
    if _norm(row.get("officialDetailName")) != _norm(detail.name):
        reasons.append("official_detail_name_mismatch")
    if _norm(row.get("ncsSubdCdnm")) != _norm(detail.name):
        reasons.append("returned_path_detail_name_mismatch")

    unit_names = (
        _norm(row.get("officialUnitName")),
        _norm(row.get("compeUnitName")),
        _norm(row.get("mcpUnitName")),
    )
    if not all(unit_names) or len(set(unit_names)) != 1:
        reasons.append("unit_name_identity_mismatch")

    retrieval_kind = _enum_bucket(row.get("unitRetrievalKind"), RETRIEVAL_KINDS)
    resolution_kind = _enum_bucket(row.get("unitResolutionKind"), RESOLUTION_KINDS)
    if retrieval_kind == "invalid_or_missing":
        reasons.append("invalid_retrieval_kind")
    if resolution_kind == "invalid_or_missing":
        reasons.append("invalid_resolution_kind")
    retrieval_query = str(row.get("unitRetrievalQuery") or "").strip()
    if retrieval_kind == "official_detail_code_query_recovery":
        if retrieval_query != detail.code:
            reasons.append("recovery_query_identity_mismatch")
    elif retrieval_kind == "official_detail_name_query":
        if _norm(retrieval_query) != _norm(detail.name):
            reasons.append("name_query_identity_mismatch")

    expected_compatible = resolution_kind == "catalog_base_version_compatible"
    if row.get("unitVersionCompatible") is not expected_compatible:
        reasons.append("version_compatibility_mismatch")
    catalog_codes = row.get("catalogUnitCodes")
    if not isinstance(catalog_codes, list) or not catalog_codes:
        reasons.append("catalog_unit_codes_missing")
    elif BASE_CODE.fullmatch(base_code) and any(
        not UNIT_CODE.fullmatch(str(code or "").strip())
        or str(code).strip().split("_", 1)[0] != base_code
        for code in catalog_codes
    ):
        reasons.append("catalog_unit_code_identity_mismatch")

    if BASE_CODE.fullmatch(base_code) and base_code in seen_bases:
        reasons.append("duplicate_returned_base_code")
    if BASE_CODE.fullmatch(base_code):
        seen_bases.add(base_code)
    return sorted(set(reasons)), base_code if BASE_CODE.fullmatch(base_code) else None


def probe_connections(
    scope: CatalogScope,
    details: Sequence[OfficialDetail],
    *,
    max_units: int = 200,
    top_gaps: int = 20,
    search_fn: Callable[..., list[dict[str, Any]]] | None = None,
    call_counter: SearchCallCounter | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run a sequential, fail-closed public coverage probe."""

    if not 1 <= max_units <= 1000:
        raise ProbeInputError("invalid_max_units")
    if not 0 <= top_gaps <= MAX_TOP_GAPS:
        raise ProbeInputError("invalid_top_gaps")
    if not details:
        raise ProbeInputError("empty_probe_scope")
    search = search_fn or client.search_units_by_detail
    retrieval_counts: Counter[str] = Counter()
    resolution_counts: Counter[str] = Counter()
    violation_reasons: Counter[str] = Counter()
    identity_violation_rows = 0
    identity_violation_detail_codes: set[str] = set()
    unexpected_identities: set[tuple[str, str]] = set()
    gap_rows: list[dict[str, Any]] = []
    complete = partial = zero = 0
    expected_total = sum(len(scope.expected_bases[detail.code]) for detail in details)
    verified_total = returned_total = 0
    processed = 0
    runtime_failure_code = ""
    started = clock()

    for detail in details:
        expected = scope.expected_bases[detail.code]
        try:
            rows = search([detail.name], max_units=max_units)
        except Exception:  # The report must not copy upstream exception text.
            runtime_failure_code = detail.code
            break
        if not isinstance(rows, list):
            runtime_failure_code = detail.code
            break

        processed += 1
        returned_total += len(rows)
        valid_bases: set[str] = set()
        seen_bases: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                retrieval_counts[
                    _enum_bucket(row.get("unitRetrievalKind"), RETRIEVAL_KINDS)
                ] += 1
                resolution_counts[
                    _enum_bucket(row.get("unitResolutionKind"), RESOLUTION_KINDS)
                ] += 1
            else:
                retrieval_counts["invalid_or_missing"] += 1
                resolution_counts["invalid_or_missing"] += 1
            reasons, base_code = _row_violation_reasons(
                row,
                detail,
                seen_bases=seen_bases,
            )
            if reasons:
                identity_violation_rows += 1
                identity_violation_detail_codes.add(detail.code)
                violation_reasons.update(reasons)
                continue
            if base_code is None:  # Defensive: invalid bases have reasons above.
                continue
            valid_bases.add(base_code)
            if base_code not in expected:
                unexpected_identities.add((detail.code, base_code))

        verified = valid_bases & expected
        missing = expected - verified
        verified_total += len(verified)
        if not missing:
            complete += 1
        elif verified:
            partial += 1
        else:
            zero += 1
        if missing:
            gap_rows.append(
                {
                    "detail_code": detail.code,
                    "expected_base_codes": len(expected),
                    "verified_base_codes": len(verified),
                    "missing_base_codes": sorted(missing),
                    "missing_count": len(missing),
                }
            )

    elapsed = max(0.0, clock() - started)
    gap_rows.sort(key=lambda row: (-row["missing_count"], row["detail_code"]))
    errors: list[str] = []
    if runtime_failure_code:
        errors.append("search_runtime_error")
        status = "error"
    else:
        if identity_violation_rows:
            errors.append("returned_identity_violation")
        if unexpected_identities:
            errors.append("unexpected_base_identity")
        if errors:
            status = "fail"
        elif partial or zero:
            status = "pass_with_gaps"
        else:
            status = "pass"

    calls = call_counter.as_dict() if call_counter else {
        "instrumented": False,
        "ncs_search_total": None,
        "detail_name_query": None,
        "detail_code_query": None,
        "other_query": None,
    }
    coverage = verified_total / expected_total if expected_total else 0.0
    return {
        "schema_version": 1,
        "probe_kind": "ncs_detail_to_unit_live_coverage",
        "status": status,
        "errors": errors,
        "scope": {
            "active_official_details": scope.active_detail_count,
            "selected_details": len(details),
            "selected_detail_codes": (
                [detail.code for detail in details]
                if len(details) < scope.active_detail_count
                else []
            ),
            "catalog_unit_rows": scope.catalog_unit_count,
            "catalog_stable_base_codes": scope.stable_base_count,
            "max_units_per_detail": max_units,
        },
        "counts": {
            "processed_details": processed,
            "unprocessed_details": len(details) - processed,
            "complete_details": complete,
            "partial_details": partial,
            "zero_details": zero,
            "expected_base_codes": expected_total,
            "verified_expected_base_codes": verified_total,
            "missing_base_codes": expected_total - verified_total,
            "returned_rows": returned_total,
            "unexpected_base_codes": len(unexpected_identities),
            "identity_violation_rows": identity_violation_rows,
            "identity_violation_findings": sum(violation_reasons.values()),
            "search_invocations": processed + bool(runtime_failure_code),
        },
        "coverage_ratio": round(coverage, 9),
        "retrieval_kind_counts": {
            key: retrieval_counts.get(key, 0)
            for key in (*RETRIEVAL_KINDS, "invalid_or_missing")
        },
        "resolution_kind_counts": {
            key: resolution_counts.get(key, 0)
            for key in (*RESOLUTION_KINDS, "invalid_or_missing")
        },
        "calls": calls,
        "identity_violation_reason_counts": dict(sorted(violation_reasons.items())),
        "identity_violation_detail_codes": sorted(identity_violation_detail_codes),
        "unexpected_identities": [
            {"detail_code": detail_code, "base_code": base_code}
            for detail_code, base_code in sorted(unexpected_identities)
        ],
        "runtime_failure_detail_code": runtime_failure_code,
        "top_gaps": gap_rows[:top_gaps],
        "elapsed_seconds": round(elapsed, 3),
    }


def _error_report(error_code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe_kind": "ncs_detail_to_unit_live_coverage",
        "status": "error",
        "errors": [error_code],
    }


def serialize_report(report: dict[str, Any]) -> str:
    """Return stable-key JSON with a single trailing newline."""

    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detail-catalog",
        type=Path,
        default=ROOT / "app/data/ncs_detail_catalog.json",
    )
    parser.add_argument(
        "--unit-catalog",
        type=Path,
        default=ROOT / "app/data/ncs_unit_catalog.json",
    )
    parser.add_argument("--detail-code", action="append", default=[])
    parser.add_argument("--detail-name", action="append", default=[])
    parser.add_argument("--max-units", type=int, default=200)
    parser.add_argument("--top-gaps", type=int, default=20)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scope = load_catalog_scope(args.detail_catalog, args.unit_catalog)
        details = select_details(
            scope,
            detail_codes=args.detail_code,
            detail_names=args.detail_name,
        )
        counter = SearchCallCounter()
        # Reuse one initialized read-only MCP transport across the full public
        # sweep. This changes neither query order nor validation semantics and
        # avoids creating a fresh HTTP client for every detail.
        with client.use_ncs_mcp_request_session(), counter.installed():
            report = probe_connections(
                scope,
                details,
                max_units=args.max_units,
                top_gaps=args.top_gaps,
                call_counter=counter,
            )
    except ProbeInputError as exc:
        report = _error_report(exc.code)

    payload = serialize_report(report)
    if args.output:
        try:
            args.output.write_text(payload, encoding="utf-8")
        except (OSError, UnicodeError):
            sys.stderr.write(serialize_report(_error_report("output_write_error")))
            return 2
    else:
        sys.stdout.write(payload)
    if report["status"] in {"pass", "pass_with_gaps"}:
        return 0
    return 1 if report["status"] == "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
