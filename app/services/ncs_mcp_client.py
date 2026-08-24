"""Read-only client for the prepared NCS_MCP serving service.

The interview app never opens or copies the multi-gigabyte ontology DB.  It
calls the public NCS_MCP tools over Streamable HTTP after the reviewer confirms
the extracted 세분류.  ``ncs_unit_detail`` is the authoritative KSA path.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.settings import settings
from app.services.request_budget import (
    RequestBudgetExceeded,
    clamp_timeout_to_request_budget,
)

MCP_PROTOCOL_VERSION = "2025-03-26"
_TOOLS_TTL = 300.0
_tools_cache: tuple[float, str, set[str]] | None = None
_last_error: str | None = None


@dataclass
class _McpRequestSession:
    """One reusable MCP transport for a single incoming HTTP request."""

    endpoint: str
    client: httpx.Client | None = None
    retired_clients: list[httpx.Client] = field(default_factory=list)
    initialized: bool = False
    session_id: str = ""
    request_id: int = 0
    transport_generation: int = 0
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    initialize_lock: threading.RLock = field(default_factory=threading.RLock)

    def next_request_id(self) -> int:
        with self.state_lock:
            self.request_id += 1
            return self.request_id

    def request_headers(self, generation: int) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        with self.state_lock:
            if generation == self.transport_generation and self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
        return headers

    def remember_session_id(self, response: httpx.Response, generation: int) -> None:
        session_id = str(response.headers.get("mcp-session-id") or "").strip()
        if not session_id:
            return
        with self.state_lock:
            if generation == self.transport_generation:
                self.session_id = session_id

    def get_client(self) -> tuple[httpx.Client, int]:
        with self.state_lock:
            if self.client is None:
                # NCS queries and KSA evidence must go only to the configured
                # MCP endpoint.  Do not inherit workstation/cloud proxy
                # variables or forward a signed session across a redirect.
                self.client = httpx.Client(
                    follow_redirects=False,
                    trust_env=False,
                )
                self.transport_generation += 1
            return self.client, self.transport_generation

    def mark_initialized(self, generation: int) -> None:
        with self.state_lock:
            if generation == self.transport_generation:
                self.initialized = True

    def invalidate_transport(self, generation: int) -> None:
        """Retire a failed transport without closing concurrent in-flight calls."""

        with self.initialize_lock:
            with self.state_lock:
                if generation != self.transport_generation:
                    return
                if self.client is not None:
                    self.retired_clients.append(self.client)
                self.client = None
                self.initialized = False
                self.session_id = ""

    def close(self) -> None:
        with self.state_lock:
            clients = [*self.retired_clients]
            if self.client is not None:
                clients.append(self.client)
            self.client = None
            self.retired_clients = []
            self.initialized = False
            self.session_id = ""
        for client in clients:
            client.close()


_request_session: ContextVar[_McpRequestSession | None] = ContextVar(
    "ncs_mcp_request_session",
    default=None,
)


@contextmanager
def use_ncs_mcp_request_session():
    """Reuse one initialized client and connection pool within a web request.

    Context values are copied into the KSA worker threads, so parallel
    ``ncs_unit_detail`` calls share the same thread-safe ``httpx.Client``.
    """

    endpoint = _endpoint()
    existing = _request_session.get()
    if existing is not None and existing.endpoint == endpoint:
        yield existing
        return

    state = _McpRequestSession(endpoint=endpoint)
    token = _request_session.set(state)
    try:
        yield state
    finally:
        _request_session.reset(token)
        state.close()


class NcsMcpError(RuntimeError):
    """Raised when the configured prepared NCS MCP cannot answer."""


def _decode_rpc(body: str) -> dict[str, Any]:
    candidates = [body.strip()]
    candidates.extend(
        line[5:].strip()
        for line in body.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    )
    for candidate in reversed(candidates):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            error = payload["error"]
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise NcsMcpError(message)
        result = payload.get("result")
        return result if isinstance(result, dict) else {"value": result}
    raise NcsMcpError("NCS MCP returned an unreadable response")


def _endpoint() -> str:
    return settings.ncs_mcp_endpoint()


def _rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint = _endpoint()
    if not endpoint:
        raise NcsMcpError("NCS_MCP_URL is not configured")
    state = _request_session.get()
    if state is None or state.endpoint != endpoint:
        with use_ncs_mcp_request_session():
            return _rpc(method, params)

    transport_generation = 0
    try:
        request_timeout = clamp_timeout_to_request_budget(
            settings.ncs_mcp_timeout_sec(),
            reserve_sec=2.0,
        )
        # KSA calls can run in worker threads. Only the first caller may
        # initialize the shared request-scoped transport.
        with state.initialize_lock:
            client, transport_generation = state.get_client()
            if not state.initialized:
                init = client.post(
                    endpoint,
                    headers=state.request_headers(transport_generation),
                    json={
                        "jsonrpc": "2.0",
                        "id": state.next_request_id(),
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "ncscope", "version": "1.4.8"},
                        },
                    },
                    timeout=request_timeout,
                )
                init.raise_for_status()
                _decode_rpc(init.text)
                state.remember_session_id(init, transport_generation)
                state.mark_initialized(transport_generation)

        request_timeout = clamp_timeout_to_request_budget(
            settings.ncs_mcp_timeout_sec(),
            reserve_sec=2.0,
        )
        response = client.post(
            endpoint,
            headers=state.request_headers(transport_generation),
            json={
                "jsonrpc": "2.0",
                "id": state.next_request_id(),
                "method": method,
                "params": params or {},
            },
            timeout=request_timeout,
        )
        response.raise_for_status()
        state.remember_session_id(response, transport_generation)
        return _decode_rpc(response.text)
    except (httpx.HTTPError, NcsMcpError, RequestBudgetExceeded) as exc:
        if transport_generation:
            state.invalidate_transport(transport_generation)
        global _last_error
        _last_error = "ncs_mcp_request_failed"
        if isinstance(exc, NcsMcpError):
            raise
        raise NcsMcpError(f"NCS MCP request failed: {exc}") from exc


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize FastMCP structured responses and legacy direct responses."""

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    data = result.get("data")
    if isinstance(data, dict):
        return data
    return result


def _tool_names(*, force_refresh: bool = False) -> set[str]:
    global _tools_cache
    now = time.monotonic()
    endpoint = _endpoint()
    if (
        not force_refresh
        and _tools_cache
        and _tools_cache[1] == endpoint
        and now - _tools_cache[0] < _TOOLS_TTL
    ):
        return _tools_cache[2]
    result = _rpc("tools/list")
    names = {
        str(item.get("name"))
        for item in result.get("tools", [])
        if isinstance(item, dict) and item.get("name")
    }
    _tools_cache = (now, endpoint, names)
    return names


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _payload(_rpc("tools/call", {"name": name, "arguments": arguments}))


def _path_value(path: Any, *keys: str) -> str:
    if not isinstance(path, dict):
        return ""
    for key in keys:
        value = str(path.get(key) or "").strip()
        if value:
            return value
    return ""


def _norm(value: Any) -> str:
    # NCS recruitment documents use several visually identical middle-dot
    # glyphs (for example ``·``, ``・`` and U+2024 ``․``).  Normalize Unicode
    # compatibility forms before removing transport punctuation so exact-name
    # validation does not fail only because the PDF/HWP text layer changed a
    # glyph.  Semantic rewriting is still intentionally excluded.
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # U+318D HANGUL LETTER ARAEA is used as a middle dot in legacy NCS names
    # even though Unicode classifies it as a letter, so ``\W`` alone does not
    # remove it. Recruitment files commonly substitute U+FF65/U+30FB instead.
    # Treat only this documented visual-separator family as punctuation.
    text = re.sub(r"[·ᆞ․‧•∙⋅・ㆍ]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


@lru_cache(maxsize=1)
def _official_details_by_name_key() -> dict[str, tuple[dict[str, str], ...]]:
    """Load the lightweight official detail code/name index shipped to Vercel.

    The multi-gigabyte MCP database remains remote.  This small catalog holds
    only official eight-digit detail codes and display names, which is enough
    to restore punctuation lost by PDF/HWP text layers before querying MCP.
    """

    path = Path(__file__).resolve().parents[1] / "data" / "ncs_detail_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    rows = payload.get("details") if isinstance(payload, dict) else []
    output: dict[str, list[dict[str, str]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if str(row.get("usage_yn") or "").strip().upper() != "Y":
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        key = _norm(name)
        if not (name and key and re.fullmatch(r"\d{8}", code)):
            continue
        item = {"code": code, "name": name}
        if item not in output.setdefault(key, []):
            output[key].append(item)
    return {key: tuple(items) for key, items in output.items()}


@lru_cache(maxsize=1)
def _official_detail_names_by_key() -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(dict.fromkeys(row["name"] for row in rows))
        for key, rows in _official_details_by_name_key().items()
    }


@lru_cache(maxsize=1)
def _active_official_detail_codes() -> frozenset[str]:
    return frozenset(
        row["code"]
        for rows in _official_details_by_name_key().values()
        for row in rows
    )


def _official_detail_from_ordinal_unit_decoration(
    source_name: str,
    detail_index: dict[str, tuple[dict[str, str], ...]],
) -> list[dict[str, str]]:
    """Resolve only ``official detail (NN. official unit)`` decorations."""

    match = re.fullmatch(
        r"\s*(?P<base>[^()]{1,100}?)\s*\(\s*(?P<ordinal>\d{1,2})\s*[.)]\s*"
        r"(?P<unit>[^(),/|]{1,120}?)\s*\)\s*",
        str(source_name or ""),
    )
    if not match:
        return []
    detail_matches = list(detail_index.get(_norm(match.group("base")), ()))
    if len(detail_matches) != 1:
        return []
    detail_code = detail_matches[0]["code"]
    ordinal = match.group("ordinal").zfill(2)
    matching_units = [
        row
        for row in _official_units_by_name_key().get(_norm(match.group("unit")), ())
        if str(row.get("officialDetailCode") or "") == detail_code
        and str(row.get("ncsClCd") or "").split("_", 1)[0].endswith(ordinal)
    ]
    return detail_matches if matching_units else []


def classify_official_detail_names(
    names: list[str],
    *,
    self_developed_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Classify source detail labels against the complete current catalog.

    An absent name is reported as absent, never promoted by similarity.  A
    source-declared self-developed label remains distinct from a legacy or
    otherwise unmapped label even though both correctly have no current code.
    """

    self_developed_keys = {
        _norm(name) for name in (self_developed_names or []) if _norm(name)
    }
    index = _official_details_by_name_key()
    output: list[dict[str, Any]] = []
    for name in names or []:
        source_name = str(name or "").strip()
        key = _norm(source_name)
        if not source_name or not key:
            continue
        direct_matches = list(index.get(key, ()))
        matches = direct_matches or _official_detail_from_ordinal_unit_decoration(
            source_name,
            index,
        )
        match_method = (
            "catalog_normalized_exact"
            if direct_matches
            else (
                "official_detail_with_exact_ordinal_unit_decoration"
                if matches
                else "none"
            )
        )
        if key in self_developed_keys:
            state = "source_declared_self_developed"
        elif len(matches) == 1:
            state = "official_current_exact"
        elif len(matches) > 1:
            state = "official_current_name_ambiguous"
        else:
            state = "not_in_current_official_catalog"
        output.append(
            {
                "sourceName": source_name,
                "mappingState": state,
                "catalogExact": bool(direct_matches),
                "resolvedCatalogExact": bool(matches),
                "matchMethod": match_method,
                "officialDetailCodes": [row["code"] for row in matches],
                "officialDetailNames": list(
                    dict.fromkeys(row["name"] for row in matches)
                ),
                "automaticSemanticMappingAllowed": False,
                "reviewRequired": True,
            }
        )
    return output


@lru_cache(maxsize=1)
def _official_units_by_name_key() -> dict[str, tuple[dict[str, Any], ...]]:
    """Load the deployable exact-name index for all official NCS units.

    This catalog is only an immutable lookup index.  A row becomes an
    authoritative application result only after its ten-digit code prefix is
    checked against an exact-resolved detail and the code is subsequently
    served by ``ncs_unit_detail`` for KSA.
    """

    path = Path(__file__).resolve().parents[1] / "data" / "ncs_unit_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    rows = payload.get("units") if isinstance(payload, dict) else []
    active_detail_codes = _active_official_detail_codes()
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        detail_code = str(row.get("detail_code") or "").strip()
        detail_name = str(row.get("detail_name") or "").strip()
        key = _norm(name)
        if not (
            key
            and re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code)
            and re.fullmatch(r"\d{8}", detail_code)
            and code.startswith(detail_code)
            and detail_code in active_detail_codes
        ):
            continue
        output.setdefault(key, []).append(
            {
                "ncsClCd": code,
                "compeUnitName": name,
                "ncsSubdCdnm": detail_name,
                "canonicalDetailName": detail_name,
                "officialDetailCode": detail_code,
                "source": "ncs-unit-catalog-exact",
                "matchScore": 1.0,
                "isExactUnitNameMatch": True,
            }
        )
    return {key: tuple(items) for key, items in output.items()}


def exact_official_units_by_name(names: list[str]) -> list[dict[str, Any]]:
    """Return catalog rows for normalized-exact official ability-unit names.

    No fuzzy, synonym, containment, or semantic matching is performed.  The
    caller must still enforce selected-detail code scope before accepting a
    row, because an official unit name may occur in more than one detail.
    """

    output: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    index = _official_units_by_name_key()
    for name in names or []:
        source_name = str(name or "").strip()
        for row in index.get(_norm(source_name), ()):
            code = str(row.get("ncsClCd") or "").strip()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            output.append({**row, "requiredAbilityUnitName": source_name})
    return output


def classify_official_ability_unit_names(
    names: list[str],
    *,
    selected_detail_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Classify exact official unit names and their deterministic scope.

    The state names deliberately distinguish a source-compatible official
    edge from a unique code-derived suggestion and from a scope conflict.
    Unique derivation remains review-required; it is not silently inserted as
    a source-declared detail.
    """

    selected_detail_codes = {
        row["code"]
        for name in (selected_detail_names or [])
        for row in _official_details_by_name_key().get(_norm(name), ())
    }
    index = _official_units_by_name_key()
    output: list[dict[str, Any]] = []
    for name in names or []:
        source_name = str(name or "").strip()
        key = _norm(source_name)
        if not source_name or not key:
            continue
        matches = list(index.get(key, ()))
        detail_codes = {
            str(row.get("officialDetailCode") or "").strip()
            for row in matches
            if str(row.get("officialDetailCode") or "").strip()
        }
        compatible_codes = detail_codes.intersection(selected_detail_codes)
        compatible_rows = [
            row
            for row in matches
            if str(row.get("officialDetailCode") or "").strip()
            in compatible_codes
        ]
        candidate_rows = compatible_rows if len(compatible_codes) == 1 else matches
        base_codes = {
            str(row.get("ncsClCd") or "").split("_", 1)[0]
            for row in candidate_rows
            if re.fullmatch(
                r"\d{10}(?:_[0-9A-Za-z]+)?",
                str(row.get("ncsClCd") or "").strip(),
            )
        }

        if not matches:
            state = "not_in_current_official_catalog"
        elif len(compatible_codes) == 1 and len(base_codes) == 1:
            state = "official_exact_source_scoped"
        elif len(compatible_codes) == 1:
            state = "official_exact_code_ambiguous"
        elif len(compatible_codes) > 1:
            state = "official_exact_detail_ambiguous"
        elif len(detail_codes) == 1 and len(base_codes) == 1:
            state = (
                "official_exact_scope_conflict"
                if selected_detail_codes
                else "official_exact_derived_scope_review_required"
            )
        else:
            state = "official_exact_detail_ambiguous"

        resolved_rows = (
            compatible_rows
            if state == "official_exact_source_scoped"
            else candidate_rows
            if state == "official_exact_derived_scope_review_required"
            else []
        )
        output.append(
            {
                "sourceName": source_name,
                "mappingState": state,
                "catalogExact": bool(matches),
                "selectedDetailCodes": sorted(selected_detail_codes),
                "candidateDetailCodes": sorted(detail_codes),
                "candidateDetailNames": list(
                    dict.fromkeys(
                        str(row.get("ncsSubdCdnm") or "").strip()
                        for row in matches
                        if str(row.get("ncsSubdCdnm") or "").strip()
                    )
                ),
                "candidateUnitCodes": list(
                    dict.fromkeys(
                        str(row.get("ncsClCd") or "").strip()
                        for row in matches
                        if str(row.get("ncsClCd") or "").strip()
                    )
                ),
                "resolvedUnitCodes": list(
                    dict.fromkeys(
                        str(row.get("ncsClCd") or "").strip()
                        for row in resolved_rows
                        if str(row.get("ncsClCd") or "").strip()
                    )
                ),
                "automaticSemanticMappingAllowed": False,
                "reviewRequired": state != "official_exact_source_scoped",
            }
        )
    return output


def derive_detail_candidates_from_exact_ability_scopes(
    ability_units_by_detail: dict[str, list[str]],
    *,
    minimum_distinct_units: int = 2,
) -> list[dict[str, Any]]:
    """Suggest one official detail when exact scoped units all converge.

    This is evidence generation, not automatic alias creation.  Every counted
    ability name must resolve to one official detail and one base unit code;
    at least two distinct units must vote for the same detail, and any vote
    for another detail rejects convergence for that source scope.
    """

    unit_index = _official_units_by_name_key()
    detail_index = _official_details_by_name_key()
    output: list[dict[str, Any]] = []
    threshold = max(2, int(minimum_distinct_units or 2))
    for source_detail, raw_names in (ability_units_by_detail or {}).items():
        source_name = str(source_detail or "").strip()
        # An already current official detail does not need an alias suggestion.
        if source_name and detail_index.get(_norm(source_name)):
            continue
        evidence_by_detail: dict[str, list[dict[str, Any]]] = {}
        seen_names: set[str] = set()
        for raw_name in raw_names if isinstance(raw_names, list) else []:
            ability_name = str(raw_name or "").strip()
            ability_key = _norm(ability_name)
            if not ability_name or not ability_key or ability_key in seen_names:
                continue
            seen_names.add(ability_key)
            matches = list(unit_index.get(ability_key, ()))
            detail_codes = {
                str(row.get("officialDetailCode") or "").strip()
                for row in matches
                if str(row.get("officialDetailCode") or "").strip()
            }
            base_codes = {
                str(row.get("ncsClCd") or "").split("_", 1)[0]
                for row in matches
                if re.fullmatch(
                    r"\d{10}(?:_[0-9A-Za-z]+)?",
                    str(row.get("ncsClCd") or "").strip(),
                )
            }
            if len(detail_codes) != 1 or len(base_codes) != 1:
                continue
            detail_code = next(iter(detail_codes))
            evidence_by_detail.setdefault(detail_code, []).append(
                {
                    "sourceAbilityUnitName": ability_name,
                    "officialUnitCodes": list(
                        dict.fromkeys(
                            str(row.get("ncsClCd") or "").strip()
                            for row in matches
                            if str(row.get("ncsClCd") or "").strip()
                        )
                    ),
                }
            )
        if len(evidence_by_detail) != 1:
            continue
        detail_code, evidence = next(iter(evidence_by_detail.items()))
        if len(evidence) < threshold:
            continue
        detail_rows = [
            row
            for rows in detail_index.values()
            for row in rows
            if row.get("code") == detail_code
        ]
        if len(detail_rows) != 1:
            continue
        output.append(
            {
                "sourceDetailName": source_name,
                "mappingState": "official_detail_candidate_from_exact_unit_convergence",
                "officialDetailCode": detail_code,
                "officialDetailName": detail_rows[0]["name"],
                "distinctExactUnitCount": len(evidence),
                "evidence": evidence,
                "automaticMappingAllowed": False,
                "reviewRequired": True,
            }
        )
    return output


def _split_detail_terms(values: list[str]) -> list[str]:
    """Flatten UI/parser multi-label values into distinct NCS detail terms."""

    terms: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        protected_slash = "\ufff0"
        split_value = re.sub(
            r"(?<=[A-Za-z])/(?=[A-Za-z])",
            protected_slash,
            str(value or ""),
        )
        for part in re.split(r"[\n,;/|]+", split_value):
            part = part.replace(protected_slash, "/")
            term = part.strip()
            key = _norm(term)
            if not term or not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


_DETAIL_QUERY_ALIASES_BY_KEY = {
    # Public NCS classifies this under 건축설계·감리 > 건축공사감리.
    # Some ALIO JDs shorten the 세분류 label to 건축감리.
    _norm("건축감리"): ("건축공사감리",),
    # 일부 직무기술서는 공식 세분류명 뒤에 기관의 직무 설명을 괄호로
    # 덧붙인다. 괄호 전체를 NCS 이름으로 조회하면 공식 ``비서``
    # 세분류가 있어도 정확 매칭이 0건이 되므로, 이 문서 표기만
    # 명시적으로 공식 이름에 연결한다. 일반적인 괄호 제거는
    # 특화분류/동명이인 오매칭을 만들 수 있어 허용하지 않는다.
    _norm("비서 (글로벌경영사무 지원)"): ("비서",),
    # Some ALIO tables append an ability-unit ordinal inside the detail cell.
    # Keep this observed mapping explicit rather than stripping arbitrary
    # parentheses from every institution-specific label.
    _norm("외식운영관리 (02.식자재관리)"): ("외식운영관리",),
}


def _detail_query_names(name: str) -> list[str]:
    base_name = str(name or "").strip()
    names = [base_name]
    known_aliases = tuple(_DETAIL_QUERY_ALIASES_BY_KEY.get(_norm(name), ()))
    # PDF/HWP table cells frequently turn an in-cell line break into a space
    # (e.g. ``시각\n디자인`` -> ``시각 디자인``).  NCS_MCP's search
    # tokenization can return no exact classification for that transport form
    # even though the official compact label exists. Retry only a formatting-
    # equivalent simple label; exact path comparison below still prevents a
    # semantically different classification from being accepted.
    if re.fullmatch(r"[0-9A-Za-z가-힣\s·‧･ㆍ•∙⋅・\-]+", base_name):
        compact_name = re.sub(r"\s+", "", base_name)
        if compact_name and compact_name != base_name:
            names.append(compact_name)
    # Search tokenization also varies for punctuation-only formatting
    # differences (``회계.감사`` vs official ``회계·감사``). The response is
    # accepted only when its official detail path has the same strict
    # normalized key as the original label, so this cannot promote a merely
    # similar classification.
    if not known_aliases:
        punctuation_compact = re.sub(
            r"[\W_]+",
            "",
            unicodedata.normalize("NFKC", base_name),
            flags=re.UNICODE,
        )
        if punctuation_compact and punctuation_compact not in names:
            names.append(punctuation_compact)
        ordinal_stripped = re.sub(
            r"^\d{1,2}(?:\s*[,.)：:\-]\s*|\s+)",
            "",
            base_name,
        ).strip(" \\")
        if ordinal_stripped and ordinal_stripped not in names:
            names.append(ordinal_stripped)
    # The MCP search index tokenizes punctuation. A recruitment document can
    # therefore contain the exact official letters while losing the official
    # middle dot (for example ``보일러설치정비``). Retry the unique canonical
    # display form from the bundled code/name catalog. Matching remains strict
    # because the catalog lookup uses the punctuation-insensitive exact key.
    catalog_names = _official_detail_names_by_key().get(_norm(base_name), ())
    if len(catalog_names) == 1:
        canonical_name = str(catalog_names[0]).strip()
        if canonical_name and canonical_name not in names:
            names.append(canonical_name)
    for alias in known_aliases:
        alias = str(alias or "").strip()
        if alias and all(_norm(alias) != _norm(existing) for existing in names):
            names.append(alias)
    return names


def _detail_search_query_limit(max_units: int) -> int:
    return min(200, max(100, int(max_units or 0) * 5))


def search_units_by_detail(detail_names: list[str], max_units: int = 80) -> list[dict[str, Any]]:
    """Resolve confirmed 세분류 names to NCS ability units."""

    # Call the versioned generation tool directly. Full discovery remains a
    # readiness concern and must not add a cold-path round trip here.
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for detail in _split_detail_terms(detail_names):
        name = str(detail or "").strip()
        if not name:
            continue
        for query_name in _detail_query_names(name):
            result = _call_tool(
                "ncs_search",
                {"query": query_name, "scope": "unit", "limit": _detail_search_query_limit(max_units)},
            )
            rows = result.get("results") or result.get("units") or []
            if isinstance(rows, dict):
                rows = rows.get("items") or []
            matched_before = len(output)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                path = row.get("path") if isinstance(row.get("path"), dict) else {}
                sub_name = _path_value(path, "sub", "sub_name", "ncsSubdCdnm")
                small_name = _path_value(path, "small", "small_name", "ncsSclasCdnm")
                if _norm(sub_name) != _norm(query_name):
                    continue
                code = str(row.get("id") or row.get("unit_code") or "").strip()
                base_code = code.split("_", 1)[0]
                if base_code[:8] not in _active_official_detail_codes():
                    continue
                if not code or code in seen:
                    continue
                seen.add(code)
                is_alias = unicodedata.normalize(
                    "NFKC", query_name
                ).casefold().strip() != unicodedata.normalize(
                    "NFKC", name
                ).casefold().strip()
                output.append(
                    {
                        "ncsClCd": code,
                        "compeUnitName": str(row.get("text") or row.get("unit_name") or "").strip(),
                        "compeUnitLevel": str(row.get("level") or "").strip(),
                        "compeUnitDef": str(row.get("api_definition") or row.get("definition") or "").strip(),
                        "ncsLclasCdnm": _path_value(path, "major", "major_name"),
                        "ncsMclasCdnm": _path_value(path, "middle", "middle_name"),
                        "ncsSclasCdnm": small_name,
                        "ncsSubdCdnm": sub_name,
                        "matchedDetailName": name,
                        "resolvedDetailName": sub_name if is_alias else "",
                        "detailQueryName": query_name if is_alias else "",
                        "source": "ncs-mcp-detail-alias" if is_alias else "ncs-mcp",
                        "matchScore": 1.0,
                    }
                )
                if len(output) >= max_units:
                    return output
            if len(output) > matched_before and _norm(query_name) == _norm(name):
                break
    return output


def suggest_units_by_text(terms: list[str], max_units: int = 20) -> list[dict[str, Any]]:
    """Return non-authoritative NCS unit suggestions for human selection.

    This is intentionally separate from ``search_units_by_detail``.  Exact
    세분류 matches are authoritative enough to drive KSA lookup, while these
    suggestions are only a recovery path when an uploaded JD uses an
    institution-specific or out-of-DB classification label.
    """

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, int(max_units or 20))
    for term in _split_detail_terms(terms):
        query = str(term or "").strip()
        if not query:
            continue
        result = _call_tool("ncs_search", {"query": query, "scope": "unit", "limit": min(50, max(5, limit))})
        rows = result.get("results") or result.get("units") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("id") or row.get("unit_code") or "").strip()
            base_code = code.split("_", 1)[0]
            if base_code[:8] not in _active_official_detail_codes():
                continue
            if not code or code in seen:
                continue
            seen.add(code)
            path = row.get("path") if isinstance(row.get("path"), dict) else {}
            sub_name = _path_value(path, "sub", "sub_name", "ncsSubdCdnm")
            small_name = _path_value(path, "small", "small_name", "ncsSclasCdnm")
            output.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": str(row.get("text") or row.get("unit_name") or "").strip(),
                    "compeUnitLevel": str(row.get("level") or "").strip(),
                    "compeUnitDef": str(row.get("api_definition") or row.get("definition") or "").strip(),
                    "ncsLclasCdnm": _path_value(path, "major", "major_name"),
                    "ncsMclasCdnm": _path_value(path, "middle", "middle_name"),
                    "ncsSclasCdnm": small_name,
                    "ncsSubdCdnm": sub_name,
                    "canonicalDetailName": sub_name,
                    "matchedDetailName": query,
                    "source": "ncs-mcp-suggest",
                    "matchScore": row.get("score", 0.0),
                    "isExactDetailMatch": _norm(sub_name) == _norm(query),
                    "isExactUnitNameMatch": _norm(str(row.get("text") or row.get("unit_name") or "")) == _norm(query),
                }
            )
            if len(output) >= limit:
                return output
    return output


def _detail_payload(result: dict[str, Any]) -> dict[str, Any]:
    data = _payload(result)
    if isinstance(data.get("data"), dict):
        return data["data"]
    return data


def _first_ksa_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _criteria_texts(value: Any) -> list[str]:
    """Normalize the optional NCS performance-criteria payload.

    NCS_MCP versions in the wild expose criteria as either strings or small
    objects (``text``, ``description``, ``criterion``).  Keep the client
    tolerant so the question layer can preserve a trace even when the server
    changes only the envelope shape.
    """

    if isinstance(value, dict):
        value = value.get("items") or value.get("criteria") or value.get("performance_criteria") or [value]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            text = _first_ksa_value(
                item,
                "text",
                "description",
                "criterion",
                "criteria",
                "performance_criteria",
                "performanceCriteria",
            )
        else:
            text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _canonical_ksa_type(row: dict[str, Any]) -> str:
    raw = _first_ksa_value(row, "ksa_type", "ksaType", "ksa_type_name", "ksaTypeName", "factorType")
    compact = re.sub(r"\s+", "", raw).lower()
    if compact in {"k", "knowledge", "지식"} or "지식" in compact:
        return "지식"
    if compact in {"s", "skill", "skills", "기술"} or any(token in compact for token in ("기술", "스킬")):
        return "기술"
    if compact in {"a", "attitude", "태도"} or "태도" in compact:
        return "태도"
    return raw


def _balanced_ksa(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {"지식": [], "기술": [], "태도": []}
    other: list[dict[str, Any]] = []
    for row in rows:
        kind = _canonical_ksa_type(row)
        if kind in buckets:
            buckets[kind].append(row)
        else:
            other.append(row)
    selected: list[dict[str, Any]] = []
    while len(selected) < max(1, limit) and any(buckets.values()):
        for kind in ("지식", "기술", "태도"):
            if buckets[kind] and len(selected) < limit:
                selected.append(buckets[kind].pop(0))
    selected.extend(other[: max(0, limit - len(selected))])
    return selected[:limit]


def _interleave_element_ksa(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-robin KSA rows so an early large element cannot hide later ones."""

    queues: list[list[dict[str, Any]]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_rows: list[dict[str, Any]] = []
        for row in element.get("ksa") or []:
            if not isinstance(row, dict):
                continue
            annotated = dict(row)
            annotated["_ncscope_element_id"] = element.get("element_id")
            annotated["_ncscope_element_name"] = element.get("element_name", "")
            annotated["_ncscope_element_criteria"] = _criteria_texts(
                element.get("criteria")
                or element.get("performance_criteria")
                or element.get("performanceCriteria")
            )
            element_rows.append(annotated)
        if element_rows:
            queues.append(element_rows)

    interleaved: list[dict[str, Any]] = []
    offsets = [0 for _queue in queues]
    while any(offset < len(queue) for offset, queue in zip(offsets, queues)):
        for index, queue in enumerate(queues):
            if offsets[index] >= len(queue):
                continue
            interleaved.append(queue[offsets[index]])
            offsets[index] += 1
    return interleaved


def get_ksa_by_units(units: list[dict[str, Any]], max_factors_per_unit: int = 12) -> list[dict[str, Any]]:
    """Fetch official KSA rows from NCS_MCP's ncs_unit_detail tool."""

    per_unit_limit = max(1, int(max_factors_per_unit or 12))
    selected_units = [
        dict(unit)
        for unit in (units or [])
        if isinstance(unit, dict)
        and str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
    ]

    def fetch_unit(unit: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
        result = _call_tool(
            "ncs_unit_detail",
            {"unit_code": code, "include": ["elements", "criteria", "ksa"], "text_version": "raw"},
        )
        detail = _detail_payload(result)
        detail_unit = detail.get("unit") if isinstance(detail.get("unit"), dict) else {}
        classification = (
            detail_unit.get("classification")
            if isinstance(detail_unit.get("classification"), dict)
            else {}
        )
        interleaved = _interleave_element_ksa(
            [element for element in (detail.get("elements") or []) if isinstance(element, dict)]
        )
        rows: list[dict[str, Any]] = []
        for row in _balanced_ksa(interleaved, per_unit_limit):
            rows.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": unit.get("compeUnitName") or detail_unit.get("unit_name", ""),
                    "ncsSubdCdnm": unit.get("ncsSubdCdnm") or classification.get("sub", ""),
                    "elementId": row.get("_ncscope_element_id"),
                    "elementName": row.get("_ncscope_element_name", ""),
                    "performanceCriteria": row.get("_ncscope_element_criteria") or [],
                    "factorName": _first_ksa_value(row, "text", "ksa_text", "factorName", "factor_name"),
                    "ksaTypeName": _canonical_ksa_type(row),
                    "ksaNo": _first_ksa_value(row, "ksa_no", "ksaNo", "number"),
                    "factorSource": "ncs-mcp",
                    "source": "ncs-mcp",
                    "ksaStatus": "official",
                    "isOfficialKsa": True,
                }
            )
        return rows

    try:
        configured_concurrency = int(
            str(os.getenv("NCS_MCP_KSA_CONCURRENCY", "1")).strip()
        )
    except (TypeError, ValueError):
        configured_concurrency = 1
    concurrency = max(1, min(8, configured_concurrency, len(selected_units) or 1))
    if concurrency == 1:
        per_unit_rows = [fetch_unit(unit) for unit in selected_units]
    else:
        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="ncs-mcp-ksa",
        ) as executor:
            # ``map`` retains the confirmed NCS-unit order while overlapping
            # independent network calls. This keeps evidence assignment stable.
            futures = [
                executor.submit(copy_context().run, fetch_unit, unit)
                for unit in selected_units
            ]
            per_unit_rows = [future.result() for future in futures]

    output: list[dict[str, Any]] = []
    for rows in per_unit_rows:
        output.extend(rows)
    return output


def ncs_mcp_status() -> dict[str, Any]:
    try:
        # A readiness endpoint must probe the configured service. Reusing the
        # five-minute discovery cache can report a stopped MCP as reachable.
        names = sorted(_tool_names(force_refresh=True))
        return {
            "configured": bool(_endpoint()),
            "reachable": True,
            "tools": names,
            "ksaAvailable": "ncs_unit_detail" in names,
            "lastError": None,
        }
    except NcsMcpError:
        return {
            "configured": bool(_endpoint()),
            "reachable": False,
            "tools": [],
            "ksaAvailable": False,
            "lastError": "ncs_mcp_unreachable",
        }
