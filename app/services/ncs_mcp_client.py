"""Read-only client for the prepared NCS_MCP serving service.

The interview app never opens or copies the multi-gigabyte ontology DB.  It
calls the public NCS_MCP tools over Streamable HTTP after the reviewer confirms
the extracted 세분류.  ``ncs_unit_detail`` is the authoritative KSA path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import httpx

from app.settings import settings
from app.services.request_budget import (
    RequestBudgetExceeded,
    clamp_timeout_to_request_budget,
    remaining_request_budget_sec,
    use_request_budget,
)

MCP_PROTOCOL_VERSION = "2025-03-26"
_TOOLS_TTL = 300.0
_STATUS_TTL = 10.0
_STATUS_PROBE_BUDGET_SEC = 8.0
_OFFICIAL_DETAIL_CATALOG_SHA256 = (
    "a031d1817baa69e6b97bc89b2e75a592afb8067443152dc5ab46ceb2f4c58a8b"
)
_OFFICIAL_UNIT_CATALOG_SHA256 = (
    "8f7bdc665b06ea560d2414c4acb6e1e4088fac37455b8ae8ba864775c13b0357"
)
_tools_cache: tuple[float, str, set[str]] | None = None
_status_cache: tuple[float, str, dict[str, Any]] | None = None
_status_cache_lock = threading.Lock()
_status_probe_lock = threading.Lock()
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
    retained_workers: int = 0
    close_requested: bool = False

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

    def retain_worker(self) -> None:
        with self.state_lock:
            self.retained_workers += 1

    def _drain_clients_locked(self) -> list[httpx.Client]:
        clients = [*self.retired_clients]
        if self.client is not None:
            clients.append(self.client)
        self.client = None
        self.retired_clients = []
        self.initialized = False
        self.session_id = ""
        return clients

    def release_worker(self) -> None:
        clients: list[httpx.Client] = []
        with self.state_lock:
            if self.retained_workers <= 0:
                raise RuntimeError("MCP request-session worker lease underflow")
            self.retained_workers -= 1
            if self.close_requested and self.retained_workers == 0:
                clients = self._drain_clients_locked()
        for client in clients:
            client.close()

    def close(self) -> None:
        clients: list[httpx.Client] = []
        with self.state_lock:
            self.close_requested = True
            if self.retained_workers == 0:
                clients = self._drain_clients_locked()
        for client in clients:
            client.close()


_request_session: ContextVar[_McpRequestSession | None] = ContextVar(
    "ncs_mcp_request_session",
    default=None,
)
_detached_work_lease: ContextVar[
    tuple[Callable[[], None], Callable[[], None]] | None
] = ContextVar("ncs_mcp_detached_work_lease", default=None)


@contextmanager
def use_detached_work_lease(
    retain_worker: Callable[[], None],
    release_worker: Callable[[], None],
):
    """Propagate an outer HTTP concurrency lease into nested MCP workers."""

    token = _detached_work_lease.set((retain_worker, release_worker))
    try:
        yield
    finally:
        _detached_work_lease.reset(token)


def _retain_detached_work() -> Callable[[], None]:
    lease = _detached_work_lease.get()
    if lease is None:
        return lambda: None
    retain_worker, release_worker = lease
    retain_worker()
    return release_worker


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
                            "clientInfo": {"name": "ncscope", "version": "1.4.12"},
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
    result = _rpc("tools/call", {"name": name, "arguments": arguments})
    if not isinstance(result, dict):
        global _last_error
        _last_error = "ncs_mcp_tool_schema_error"
        raise NcsMcpError(f"NCS MCP tool {name} returned an invalid response")
    if result.get("isError") is True:
        _last_error = "ncs_mcp_tool_error"
        # MCP error content can include upstream payload details. Keep the
        # public exception stable and non-reflective while preserving the tool
        # boundary that failed.
        raise NcsMcpError(f"NCS MCP tool {name} failed")
    payload = _payload(result)
    if not isinstance(payload, dict):
        _last_error = "ncs_mcp_tool_schema_error"
        raise NcsMcpError(f"NCS MCP tool {name} returned an invalid response")
    return payload


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
        raw = path.read_bytes()
        normalized_raw = raw.replace(b"\r\n", b"\n")
        if hashlib.sha256(normalized_raw).hexdigest() != _OFFICIAL_DETAIL_CATALOG_SHA256:
            return {}
        payload = json.loads(raw.decode("utf-8"))
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


def _require_official_catalogs_for_unit_search() -> dict[str, tuple[dict[str, str], ...]]:
    """Raise when bundled validation catalogs are unavailable.

    Detail-to-unit search uses the shipped catalogs as a fail-closed gate for
    remote MCP rows.  If the catalogs disappear or normalize to zero rows,
    returning an ordinary no-match result would hide a runtime defect.
    """

    detail_index = _official_details_by_name_key()
    if not detail_index:
        global _last_error
        _last_error = "ncs_detail_catalog_unavailable"
        raise NcsMcpError("Bundled NCS detail catalog is unavailable")
    if not _official_unit_catalog_rows():
        _last_error = "ncs_unit_catalog_unavailable"
        raise NcsMcpError("Bundled NCS unit catalog is unavailable")
    return detail_index


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
def _official_unit_catalog_rows() -> tuple[dict[str, Any], ...]:
    """Load normalized immutable official-unit catalog rows once.

    The catalog deliberately retains every published version.  Callers may
    verify an exact full code first and may only use a base-code match when
    the catalog proves the same canonical unit name and detail ownership.
    It must never be used to infer a "latest" suffix.
    """

    path = Path(__file__).resolve().parents[1] / "data" / "ncs_unit_catalog.json"
    try:
        raw = path.read_bytes()
        normalized_raw = raw.replace(b"\r\n", b"\n")
        if hashlib.sha256(normalized_raw).hexdigest() != _OFFICIAL_UNIT_CATALOG_SHA256:
            return ()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ()
    rows = payload.get("units") if isinstance(payload, dict) else []
    active_detail_codes = _active_official_detail_codes()
    output: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        detail_code = str(row.get("detail_code") or "").strip()
        detail_name = str(row.get("detail_name") or "").strip()
        if not (
            _norm(name)
            and re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code)
            and re.fullmatch(r"\d{8}", detail_code)
            and code.startswith(detail_code)
            and detail_code in active_detail_codes
        ):
            continue
        output.append(
            {
                "ncsClCd": code,
                "officialUnitBaseCode": code.split("_", 1)[0],
                "compeUnitName": name,
                "ncsSubdCdnm": detail_name,
                "canonicalDetailName": detail_name,
                "officialDetailCode": detail_code,
                "source": "ncs-unit-catalog-exact",
                "matchScore": 1.0,
                "isExactUnitNameMatch": True,
            }
        )
    return tuple(output)


@lru_cache(maxsize=1)
def _official_units_by_name_key() -> dict[str, tuple[dict[str, Any], ...]]:
    """Return immutable catalog rows keyed by normalized official name."""

    output: dict[str, list[dict[str, Any]]] = {}
    for row in _official_unit_catalog_rows():
        output.setdefault(_norm(row["compeUnitName"]), []).append(row)
    return {key: tuple(items) for key, items in output.items()}


@lru_cache(maxsize=1)
def _official_units_by_full_code() -> dict[str, tuple[dict[str, Any], ...]]:
    """Return immutable catalog rows keyed by their complete published code."""

    output: dict[str, list[dict[str, Any]]] = {}
    for row in _official_unit_catalog_rows():
        output.setdefault(str(row["ncsClCd"]), []).append(row)
    return {key: tuple(items) for key, items in output.items()}


@lru_cache(maxsize=1)
def _official_units_by_base_code() -> dict[str, tuple[dict[str, Any], ...]]:
    """Return all catalog versions keyed by stable ten-digit unit identity."""

    output: dict[str, list[dict[str, Any]]] = {}
    for row in _official_unit_catalog_rows():
        output.setdefault(str(row["officialUnitBaseCode"]), []).append(row)
    return {key: tuple(items) for key, items in output.items()}


@lru_cache(maxsize=1)
def _official_unit_base_codes_by_detail_code() -> dict[str, frozenset[str]]:
    """Return the complete bundled unit identity set for each active detail.

    The set is used only to detect an incomplete name-search response.  Every
    recovery row still has to pass the same canonical path, code ownership,
    and full/base catalog checks as an ordinary MCP name-search row.
    """

    output: dict[str, set[str]] = {}
    for row in _official_unit_catalog_rows():
        detail_code = str(row["officialDetailCode"])
        output.setdefault(detail_code, set()).add(
            str(row["officialUnitBaseCode"])
        )
    return {
        detail_code: frozenset(base_codes)
        for detail_code, base_codes in output.items()
    }


def _catalog_unit_matches_mcp(
    catalog_row: dict[str, Any],
    *,
    mcp_unit_name: str,
    path_sub_name: str,
    official_detail_code: str,
) -> bool:
    """Require the catalog's canonical unit and detail identities to agree."""

    return (
        _norm(catalog_row.get("compeUnitName")) == _norm(mcp_unit_name)
        and str(catalog_row.get("officialDetailCode") or "")
        == official_detail_code
        and _norm(catalog_row.get("canonicalDetailName")) == _norm(path_sub_name)
    )


def _resolve_catalog_unit(
    *,
    mcp_code: str,
    mcp_unit_name: str,
    path_sub_name: str,
    official_detail_code: str,
) -> dict[str, Any] | None:
    """Resolve one MCP unit without accepting stale or renamed identities.

    A present full catalog code is authoritative: a name/detail disagreement
    rejects that row and intentionally cannot fall through to base-code
    matching.  When a full code is absent (for example an MCP catalog version
    released after the bundled static catalog), only one matching stable base
    identity is allowed.  No suffix ordering or "newest version" heuristic is
    involved.
    """

    base_code = mcp_code.split("_", 1)[0]
    base_rows = _official_units_by_base_code().get(base_code, ())
    base_identities = {
        (
            str(row["officialUnitBaseCode"]),
            _norm(row["compeUnitName"]),
            str(row["officialDetailCode"]),
            _norm(row["canonicalDetailName"]),
        )
        for row in base_rows
    }
    # A full suffix is not allowed to bypass a conflict elsewhere on the same
    # stable ten-digit identity. Exact and forward-compatible versions share
    # one semantic-identity gate.
    if len(base_identities) != 1:
        return None

    exact_rows = _official_units_by_full_code().get(mcp_code, ())
    if exact_rows:
        # A duplicated published code is a corrupt identity even when only one
        # duplicate happens to match the MCP text. Never select through that
        # ambiguity; the catalog audit reports the underlying defect.
        if len(exact_rows) != 1:
            return None
        matching_rows = [
            row
            for row in exact_rows
            if _catalog_unit_matches_mcp(
                row,
                mcp_unit_name=mcp_unit_name,
                path_sub_name=path_sub_name,
                official_detail_code=official_detail_code,
            )
        ]
        if len(matching_rows) != 1:
            return None
        resolved = matching_rows[0]
        resolution_kind = "catalog_full_code_exact"
    else:
        matching_rows = [
            row
            for row in base_rows
            if _catalog_unit_matches_mcp(
                row,
                mcp_unit_name=mcp_unit_name,
                path_sub_name=path_sub_name,
                official_detail_code=official_detail_code,
            )
        ]
        if not matching_rows:
            return None
        resolved = matching_rows[0]
        resolution_kind = "catalog_base_version_compatible"

    semantic_rows = [
        row
        for row in _official_units_by_base_code().get(
            str(resolved["officialUnitBaseCode"]), ()
        )
        if _catalog_unit_matches_mcp(
            row,
            mcp_unit_name=mcp_unit_name,
            path_sub_name=path_sub_name,
            official_detail_code=official_detail_code,
        )
    ]
    return {
        "officialUnitBaseCode": str(resolved["officialUnitBaseCode"]),
        "officialUnitName": str(resolved["compeUnitName"]),
        "unitResolutionKind": resolution_kind,
        "unitCatalogVerified": True,
        "unitVersionCompatible": resolution_kind
        == "catalog_base_version_compatible",
        # Preserve source-catalog order. It is audit evidence, not a version
        # recommendation or a basis for inferring the latest suffix.
        "catalogUnitCodes": list(
            dict.fromkeys(str(row["ncsClCd"]) for row in semantic_rows)
        ),
    }


def resolve_official_unit_selection(
    code: str,
    unit_name: str,
    detail_name: str,
) -> dict[str, Any] | None:
    """Fail closed unless one selected unit is owned by one active detail.

    Client-submitted ``selected_ncs`` rows are untrusted even when they were
    originally rendered from an MCP response.  Re-resolve their code, unit
    name, and detail name against the bundled immutable catalogs before they
    can drive KSA lookup or generation.  A code for a newer remote catalog
    version may reuse a proven ten-digit base identity, but no suffix ordering
    or latest-version inference is allowed.
    """

    input_code = str(code or "").strip()
    input_unit_name = str(unit_name or "").strip()
    input_detail_name = str(detail_name or "").strip()
    if not (
        re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", input_code)
        and _norm(input_unit_name)
        and _norm(input_detail_name)
    ):
        return None

    detail_index = _require_official_catalogs_for_unit_search()
    detail_code = input_code[:8]
    detail_rows = [
        row
        for rows in detail_index.values()
        for row in rows
        if str(row.get("code") or "") == detail_code
    ]
    # More than one active row for a code is catalog corruption, even if the
    # duplicate display names happen to normalize alike.  Do not silently
    # choose one or let a client-supplied name break the tie.
    if len(detail_rows) != 1:
        return None
    official_detail = detail_rows[0]
    if _norm(official_detail.get("name")) != _norm(input_detail_name):
        return None

    unit_resolution = _resolve_catalog_unit(
        mcp_code=input_code,
        mcp_unit_name=input_unit_name,
        path_sub_name=str(official_detail["name"]),
        official_detail_code=detail_code,
    )
    if unit_resolution is None:
        return None

    canonical_unit_name = str(unit_resolution["officialUnitName"])
    canonical_detail_name = str(official_detail["name"])
    return {
        # Preserve the exact submitted full code.  For a proven base-version
        # fallback it is intentionally distinct from ``catalogUnitCodes``.
        "ncsClCd": input_code,
        "compeUnitName": canonical_unit_name,
        "ncsSubdCdnm": canonical_detail_name,
        "canonicalDetailName": canonical_detail_name,
        "officialDetailCode": detail_code,
        "officialDetailName": canonical_detail_name,
        "detailResolutionKind": "selected_catalog_verified",
        "detailResolutionRule": "selected_catalog_verified",
        **unit_resolution,
        "source": "selected-ncs-catalog-verified",
    }


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


def _split_unbracketed_detail_surface(
    value: str,
    delimiters: frozenset[str],
) -> list[str]:
    """Split delimiters while preserving every locally matched group span."""

    text = str(value or "")
    pairs = {
        "(": ")",
        "[": "]",
        "{": "}",
        "（": "）",
        "［": "］",
        "｛": "｝",
    }
    closers = {closer: opener for opener, closer in pairs.items()}
    stack: list[tuple[str, int, bool]] = []
    protection_delta = [0] * (len(text) + 1)
    for index, char in enumerate(text):
        if char in pairs:
            stack.append((char, index, True))
            continue
        expected_opener = closers.get(char)
        if not expected_opener or not stack:
            continue
        if stack[-1][0] == expected_opener:
            _opener, opener_index, locally_valid = stack.pop()
            if locally_valid:
                protection_delta[opener_index + 1] += 1
                protection_delta[index] -= 1
            continue
        # A wrong closer inside an open group invalidates only those still-open
        # spans. Already completed groups elsewhere keep their protection.
        stack = [(opener, opener_index, False) for opener, opener_index, _ in stack]

    parts: list[str] = []
    start = 0
    protection_depth = 0
    for index, char in enumerate(text):
        protection_depth += protection_delta[index]
        if protection_depth == 0 and char in delimiters:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _split_slash_detail_surface(value: str) -> list[str]:
    """Preserve the longest catalog-exact slash spans, then split the rest."""

    slash_parts = _split_unbracketed_detail_surface(
        value,
        frozenset("/"),
    )
    if len(slash_parts) <= 1:
        return slash_parts
    output: list[str] = []
    index = 0
    detail_index = _official_details_by_name_key()
    while index < len(slash_parts):
        matched_end = index + 1
        for end in range(len(slash_parts), index + 1, -1):
            candidate = "/".join(slash_parts[index:end]).strip()
            if len(detail_index.get(_norm(candidate), ())) == 1:
                matched_end = end
                break
        output.append("/".join(slash_parts[index:matched_end]))
        index = matched_end
    return output


def _split_detail_terms(values: list[str]) -> list[str]:
    """Flatten UI/parser multi-label values into distinct NCS detail terms."""

    terms: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw_value = str(value or "").strip()
        whole_matches = _official_details_by_name_key().get(_norm(raw_value), ())
        # Some official detail labels contain the same punctuation used by the
        # UI for multi-value transport (for example commas inside parentheses).
        # Preserve the whole value only when it resolves to one official name
        # that itself contains a transport delimiter; otherwise a genuine
        # ``detail A, detail B`` input must still be split.
        if (
            raw_value
            and len(whole_matches) == 1
            and re.search(r"[,;/|]", whole_matches[0]["name"])
        ):
            key = _norm(raw_value)
            if key not in seen:
                seen.add(key)
                terms.append(raw_value)
            continue
        parts: list[str] = []
        for coarse_part in _split_unbracketed_detail_surface(
            raw_value,
            frozenset("\n,，、;|"),
        ):
            coarse_matches = _official_details_by_name_key().get(
                _norm(coarse_part),
                (),
            )
            if len(coarse_matches) == 1:
                parts.append(coarse_part)
            else:
                parts.extend(_split_slash_detail_surface(coarse_part))
        for part in parts:
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
    # The MCP ranker can place the first exact-detail row well below the
    # caller's final output cap.  Keep discovery deep and bound the retained
    # rows separately so a small UI limit does not turn into a false miss.
    return 200


def _detail_resolution_kind(name: str, query_name: str) -> str:
    """Classify an accepted exact-detail query without widening matching."""

    source_key = _norm(name)
    query_key = _norm(query_name)
    known_aliases = _DETAIL_QUERY_ALIASES_BY_KEY.get(source_key, ())
    if any(query_key == _norm(alias) for alias in known_aliases):
        return "safe_alias"
    source_surface = unicodedata.normalize("NFKC", name).casefold().strip()
    query_surface = unicodedata.normalize("NFKC", query_name).casefold().strip()
    return "direct" if query_surface == source_surface else "format_variant"


def _detail_resolution_rule(name: str, query_name: str) -> str:
    """Return the exact, auditable rule that produced a detail query."""

    source_key = _norm(name)
    query_key = _norm(query_name)
    known_aliases = _DETAIL_QUERY_ALIASES_BY_KEY.get(source_key, ())
    if any(query_key == _norm(alias) for alias in known_aliases):
        return "safe_alias"

    source_surface = unicodedata.normalize("NFKC", name).casefold().strip()
    query_surface = unicodedata.normalize("NFKC", query_name).casefold().strip()
    if query_surface == source_surface:
        return "direct"

    whitespace_compact = re.sub(r"\s+", "", source_surface)
    if query_surface == whitespace_compact:
        return "whitespace_compact"

    ordinal_stripped = re.sub(
        r"^\d{1,2}(?:\s*[,.)：:\-]\s*|\s+)",
        "",
        source_surface,
    ).strip(" \\")
    if query_surface == ordinal_stripped:
        return "ordinal_prefix_stripped"

    catalog_names = _official_detail_names_by_key().get(source_key, ())
    if any(
        query_surface
        == unicodedata.normalize("NFKC", canonical_name).casefold().strip()
        for canonical_name in catalog_names
    ):
        return "catalog_display_restored"

    punctuation_compact = re.sub(
        r"[\W_]+",
        "",
        source_surface,
        flags=re.UNICODE,
    )
    if query_surface == punctuation_compact:
        return "punctuation_variant"
    return "format_variant"


def _detail_code_recovery_rows(
    result: dict[str, Any],
    *,
    source_detail_name: str,
    official_detail: dict[str, str],
    detail_index: dict[str, tuple[dict[str, str], ...]],
) -> list[dict[str, Any]]:
    """Validate rows returned by an exact eight-digit detail-code query."""

    rows = _search_result_rows(result)
    official_name = official_detail["name"]
    official_code = official_detail["code"]
    is_alias = unicodedata.normalize(
        "NFKC", official_name
    ).casefold().strip() != unicodedata.normalize(
        "NFKC", source_detail_name
    ).casefold().strip()
    resolution_kind = _detail_resolution_kind(
        source_detail_name,
        official_name,
    )
    resolution_rule = _detail_resolution_rule(
        source_detail_name,
        official_name,
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get("path") if isinstance(row.get("path"), dict) else {}
        sub_name = _consistent_identity_value(
            path,
            "sub",
            "sub_name",
            "ncsSubdCdnm",
            normalize=True,
        )
        if sub_name is None:
            continue
        if _norm(sub_name) != _norm(official_name):
            continue
        code = _consistent_identity_value(
            row,
            "id",
            "unit_code",
            normalize=False,
        )
        if code is None:
            continue
        if not re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code):
            continue
        base_code = code.split("_", 1)[0]
        if base_code[:8] != official_code:
            continue
        official_details = [
            candidate
            for candidate in detail_index.get(_norm(sub_name), ())
            if candidate["code"] == base_code[:8]
        ]
        if len(official_details) != 1:
            continue
        resolved_detail = official_details[0]
        if (
            resolved_detail["code"] != official_code
            or _norm(resolved_detail["name"]) != _norm(official_name)
        ):
            continue
        path_code_verification = _search_path_detail_code_verification(
            path,
            official_code,
        )
        if path_code_verification is False:
            continue
        mcp_unit_name = _consistent_identity_value(
            row,
            "text",
            "unit_name",
            normalize=True,
        )
        if mcp_unit_name is None:
            continue
        catalog_resolution = _resolve_catalog_unit(
            mcp_code=code,
            mcp_unit_name=mcp_unit_name,
            path_sub_name=sub_name,
            official_detail_code=official_code,
        )
        if catalog_resolution is None:
            continue
        output.append(
            {
                "ncsClCd": code,
                "compeUnitName": mcp_unit_name,
                "compeUnitLevel": str(row.get("level") or "").strip(),
                "compeUnitDef": str(
                    row.get("api_definition") or row.get("definition") or ""
                ).strip(),
                "ncsLclasCdnm": _path_value(path, "major", "major_name"),
                "ncsMclasCdnm": _path_value(path, "middle", "middle_name"),
                "ncsSclasCdnm": _path_value(
                    path,
                    "small",
                    "small_name",
                    "ncsSclasCdnm",
                ),
                "ncsSubdCdnm": sub_name,
                "matchedDetailName": source_detail_name,
                "resolvedDetailName": sub_name if is_alias else "",
                "detailQueryName": official_name if is_alias else "",
                "officialDetailCode": official_code,
                "officialDetailName": official_name,
                "detailResolutionKind": resolution_kind,
                "detailResolutionRule": resolution_rule,
                "unitRetrievalKind": "official_detail_code_query_recovery",
                "unitRetrievalQuery": official_code,
                "detailPathCodeVerified": path_code_verification is True,
                "mcpUnitName": mcp_unit_name,
                **catalog_resolution,
                "source": "ncs-mcp-detail-code-recovery",
                "matchScore": 1.0,
            }
        )
    return output


def _search_units_by_detail_result(
    detail_names: list[str],
    max_units: int = 80,
) -> dict[str, Any]:
    """Resolve confirmed details while retaining zero-row coverage state."""

    # Call the versioned generation tool directly. Full discovery remains a
    # readiness concern and must not add a cold-path round trip here.
    limit = max(1, int(max_units or 80))
    detail_index = _require_official_catalogs_for_unit_search()
    details = _split_detail_terms(detail_names)
    if len(details) > 64:
        raise NcsMcpError("NCS detail term limit exceeded")
    groups: list[list[dict[str, Any]]] = []
    group_retrieval_states: list[dict[str, Any]] = []
    expected_bases_by_detail = _official_unit_base_codes_by_detail_code()
    # A stable ten-digit ability-unit code is the semantic identity.  Several
    # published suffixes can describe that same identity; retain the first MCP
    # ranked row only after all validation succeeds.
    seen_semantic_identities: set[str] = set()
    for detail in details:
        name = str(detail or "").strip()
        if not name:
            continue
        group: list[dict[str, Any]] = []
        groups.append(group)
        retrieval_state: dict[str, Any] = {
            "sourceDetailName": name,
            "mappingState": "official_detail_unresolved",
            "officialDetailCode": "",
            "officialDetailName": "",
            "detailExpectedUnitBaseCount": 0,
            "detailVerifiedUnitBaseCount": 0,
            "detailRetrievalComplete": False,
            "detailRetrievalCapLimited": False,
        }
        group_retrieval_states.append(retrieval_state)
        query_names = _detail_query_names(name)
        official_candidates = {
            (row["code"], _norm(row["name"])): row
            for query_name in query_names
            for row in detail_index.get(_norm(query_name), ())
        }
        # A source label and all of its narrowly allowed formatting/alias
        # variants must converge on exactly one current official identity.
        # If a former alias becomes a separate official detail, fail closed
        # before querying instead of merging two classifications.
        if len(official_candidates) != 1:
            continue
        expected_detail = next(iter(official_candidates.values()))
        retrieval_state.update(
            {
                "mappingState": "official_detail_resolved",
                "officialDetailCode": expected_detail["code"],
                "officialDetailName": expected_detail["name"],
            }
        )
        verified_detail_identities: set[str] = set()
        for query_name in query_names:
            result = _call_tool(
                "ncs_search",
                {
                    "query": query_name,
                    "scope": "unit",
                    "limit": _detail_search_query_limit(limit),
                },
            )
            rows = _search_result_rows(result)
            matched_before = len(group)
            for row in rows:
                path = row.get("path") if isinstance(row.get("path"), dict) else {}
                sub_name = _consistent_identity_value(
                    path,
                    "sub",
                    "sub_name",
                    "ncsSubdCdnm",
                    normalize=True,
                )
                if sub_name is None:
                    continue
                small_name = _path_value(path, "small", "small_name", "ncsSclasCdnm")
                if _norm(sub_name) != _norm(query_name):
                    continue
                code = _consistent_identity_value(
                    row,
                    "id",
                    "unit_code",
                    normalize=False,
                )
                if code is None:
                    continue
                # A detail prefix does not make an arbitrary MCP identifier a
                # valid ability-unit code. Reject truncated and malformed IDs
                # here so they cannot surface as exact options and fail only
                # later during the unit-detail/KSA lookup.
                if not re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code):
                    continue
                base_code = code.split("_", 1)[0]
                official_details = [
                    detail
                    for detail in detail_index.get(_norm(sub_name), ())
                    if detail["code"] == base_code[:8]
                ]
                # A current code alone is insufficient: the MCP result's
                # declared path name must own that exact eight-digit detail
                # prefix in the shipped official catalog. This rejects stale
                # or internally inconsistent index rows before they can be
                # treated as an exact reviewed-detail match.
                if len(official_details) != 1:
                    continue
                official_detail = official_details[0]
                if (
                    official_detail["code"] != expected_detail["code"]
                    or _norm(official_detail["name"])
                    != _norm(expected_detail["name"])
                ):
                    continue
                path_code_verification = _search_path_detail_code_verification(
                    path,
                    official_detail["code"],
                )
                if path_code_verification is False:
                    continue
                mcp_unit_name = _consistent_identity_value(
                    row,
                    "text",
                    "unit_name",
                    normalize=True,
                )
                if mcp_unit_name is None:
                    continue
                catalog_resolution = _resolve_catalog_unit(
                    mcp_code=code,
                    mcp_unit_name=mcp_unit_name,
                    path_sub_name=sub_name,
                    official_detail_code=official_detail["code"],
                )
                # Catalog validation intentionally happens before seen/cap
                # accounting so malformed, stale, or renamed MCP rows cannot
                # suppress a later valid row for the same semantic identity.
                if catalog_resolution is None:
                    continue
                semantic_identity = catalog_resolution["officialUnitBaseCode"]
                verified_detail_identities.add(semantic_identity)
                if semantic_identity in seen_semantic_identities:
                    continue
                seen_semantic_identities.add(semantic_identity)
                is_alias = unicodedata.normalize(
                    "NFKC", query_name
                ).casefold().strip() != unicodedata.normalize(
                    "NFKC", name
                ).casefold().strip()
                resolution_kind = _detail_resolution_kind(name, query_name)
                resolution_rule = _detail_resolution_rule(name, query_name)
                group.append(
                    {
                        "ncsClCd": code,
                        "compeUnitName": mcp_unit_name,
                        "compeUnitLevel": str(row.get("level") or "").strip(),
                        "compeUnitDef": str(row.get("api_definition") or row.get("definition") or "").strip(),
                        "ncsLclasCdnm": _path_value(path, "major", "major_name"),
                        "ncsMclasCdnm": _path_value(path, "middle", "middle_name"),
                        "ncsSclasCdnm": small_name,
                        "ncsSubdCdnm": sub_name,
                        "matchedDetailName": name,
                        "resolvedDetailName": sub_name if is_alias else "",
                        "detailQueryName": query_name if is_alias else "",
                        "officialDetailCode": official_detail["code"],
                        "officialDetailName": official_detail["name"],
                        "detailResolutionKind": resolution_kind,
                        "detailResolutionRule": resolution_rule,
                        "unitRetrievalKind": "official_detail_name_query",
                        "unitRetrievalQuery": query_name,
                        "detailPathCodeVerified": path_code_verification is True,
                        "mcpUnitName": mcp_unit_name,
                        **catalog_resolution,
                        "source": "ncs-mcp-detail-alias" if is_alias else "ncs-mcp",
                        "matchScore": 1.0,
                    }
                )
            if len(group) > matched_before and _norm(query_name) == _norm(name):
                break

        expected_base_codes = expected_bases_by_detail.get(
            expected_detail["code"],
            frozenset(),
        )
        missing_base_codes = expected_base_codes - verified_detail_identities
        recovery_skipped_for_output_limit = bool(
            missing_base_codes and len(group) >= limit
        )
        # A numeric detail-code query is a bounded recovery path for broad
        # labels whose name search exhausts the MCP ranking window. It is never
        # called for a complete response or when this detail already fills the
        # caller's output budget.
        if missing_base_codes and len(group) < limit:
            recovery_result = _call_tool(
                "ncs_search",
                {
                    "query": expected_detail["code"],
                    "scope": "unit",
                    "limit": _detail_search_query_limit(limit),
                },
            )
            for recovered_row in _detail_code_recovery_rows(
                recovery_result,
                source_detail_name=name,
                official_detail=expected_detail,
                detail_index=detail_index,
            ):
                semantic_identity = recovered_row["officialUnitBaseCode"]
                if semantic_identity not in missing_base_codes:
                    continue
                verified_detail_identities.add(semantic_identity)
                if semantic_identity in seen_semantic_identities:
                    continue
                seen_semantic_identities.add(semantic_identity)
                group.append(recovered_row)

        verified_expected_base_codes = (
            expected_base_codes & verified_detail_identities
        )
        retrieval_state.update(
            {
                "detailExpectedUnitBaseCount": len(expected_base_codes),
                "detailVerifiedUnitBaseCount": len(verified_expected_base_codes),
                "detailRetrievalComplete": (
                    verified_detail_identities == set(expected_base_codes)
                ),
                "detailRetrievalCapLimited": recovery_skipped_for_output_limit,
            }
        )
        row_coverage = {
            field: retrieval_state[field]
            for field in (
                "detailExpectedUnitBaseCount",
                "detailVerifiedUnitBaseCount",
                "detailRetrievalComplete",
                "detailRetrievalCapLimited",
            )
        }
        for row in group:
            row.update(row_coverage)

    # Preserve at least one exact unit from each confirmed detail before a
    # large first classification can consume the global response limit.
    output: list[dict[str, Any]] = []
    offsets = [0 for _group in groups]
    while len(output) < limit and any(
        offset < len(group) for offset, group in zip(offsets, groups)
    ):
        for index, group in enumerate(groups):
            if len(output) >= limit:
                break
            if offsets[index] >= len(group):
                continue
            output.append(group[offsets[index]])
            offsets[index] += 1
    for index, group in enumerate(groups):
        if offsets[index] >= len(group):
            continue
        group_retrieval_states[index]["detailRetrievalCapLimited"] = True
        for row in group:
            row["detailRetrievalCapLimited"] = True
    coverage_details = [dict(state) for state in group_retrieval_states]
    return {
        "items": output,
        "exactCoverage": {
            "details": coverage_details,
            "resolvedOfficialDetailCount": sum(
                state["mappingState"] == "official_detail_resolved"
                for state in coverage_details
            ),
            "unresolvedDetailCount": sum(
                state["mappingState"] != "official_detail_resolved"
                for state in coverage_details
            ),
        },
    }


def search_units_by_detail_result(
    detail_names: list[str],
    max_units: int = 80,
) -> dict[str, Any]:
    """Return verified units plus per-detail exact retrieval coverage."""

    return _search_units_by_detail_result(detail_names, max_units=max_units)


def search_units_by_detail(
    detail_names: list[str],
    max_units: int = 80,
) -> list[dict[str, Any]]:
    """Resolve confirmed 세분류 names to NCS ability units."""

    return list(
        _search_units_by_detail_result(detail_names, max_units=max_units)["items"]
    )


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
    detail_index = _official_details_by_name_key()
    active_detail_codes = _active_official_detail_codes()
    for term in _split_detail_terms(terms):
        query = str(term or "").strip()
        if not query:
            continue
        result = _call_tool(
            "ncs_search",
            {
                "query": query,
                "scope": "unit",
                "limit": min(50, max(5, limit)),
            },
        )
        rows = _search_result_rows(result)
        for row in rows:
            code = _consistent_identity_value(
                row,
                "id",
                "unit_code",
                normalize=False,
            )
            if code is None or not re.fullmatch(
                r"\d{10}(?:_[0-9A-Za-z]+)?",
                code,
            ):
                continue
            base_code = code.split("_", 1)[0]
            detail_code = base_code[:8]
            if detail_code not in active_detail_codes:
                continue
            mcp_unit_name = _consistent_identity_value(
                row,
                "text",
                "unit_name",
                normalize=True,
            )
            if not mcp_unit_name:
                continue
            path = row.get("path") if isinstance(row.get("path"), dict) else {}
            sub_name = _consistent_identity_value(
                path,
                "sub",
                "sub_name",
                "ncsSubdCdnm",
                normalize=True,
            )
            if not sub_name:
                continue
            path_code_verification = _search_path_detail_code_verification(
                path,
                detail_code,
            )
            if path_code_verification is False:
                continue
            current_name_candidates = detail_index.get(_norm(sub_name), ())
            # A non-current/free-form path label remains eligible for manual
            # review, but a path that names a different *current* official
            # detail is an internal identity contradiction and is discarded.
            detail_path_verified = False
            if current_name_candidates:
                if (
                    len(current_name_candidates) != 1
                    or current_name_candidates[0]["code"] != detail_code
                ):
                    continue
                detail_path_verified = True
            if code in seen:
                continue
            seen.add(code)
            small_name = _path_value(path, "small", "small_name", "ncsSclasCdnm")
            output.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": mcp_unit_name,
                    "compeUnitLevel": str(row.get("level") or "").strip(),
                    "compeUnitDef": str(row.get("api_definition") or row.get("definition") or "").strip(),
                    "ncsLclasCdnm": _path_value(path, "major", "major_name"),
                    "ncsMclasCdnm": _path_value(path, "middle", "middle_name"),
                    "ncsSclasCdnm": small_name,
                    "ncsSubdCdnm": sub_name,
                    "canonicalDetailName": sub_name,
                    "matchedDetailName": query,
                    "officialDetailCode": detail_code,
                    "detailPathVerified": detail_path_verified,
                    "detailPathCodeVerified": path_code_verification is True,
                    "unitIdentityFieldsConsistent": True,
                    "source": "ncs-mcp-suggest",
                    "matchScore": row.get("score", 0.0),
                    "isExactDetailMatch": _norm(sub_name) == _norm(query),
                    "isExactUnitNameMatch": _norm(mcp_unit_name) == _norm(query),
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


def _search_path_detail_code_verification(
    path: Any,
    expected_detail_code: str,
) -> bool | None:
    """Verify a search row's four-level classification code when supplied.

    Older compatible MCP fixtures omitted path codes entirely, so absence is
    reported as ``None``. A partially populated, malformed, or contradictory
    path is an explicit failure and must never be treated like absence.
    """

    if not isinstance(path, dict):
        return None
    segments = [
        str(path.get(field) or "").strip()
        for field in ("major_code", "middle_code", "small_code", "sub_code")
    ]
    if not any(segments):
        return None
    if not all(re.fullmatch(r"\d{2}", segment) for segment in segments):
        return False
    return "".join(segments) == expected_detail_code


def _consistent_identity_value(
    payload: Any,
    *keys: str,
    normalize: bool,
) -> str | None:
    """Return one identity value, rejecting conflicting alias fields.

    MCP versions can expose the same semantic value under multiple field
    names. A first-non-empty fallback would hide a conflicting secondary field,
    so every populated alias must agree before the row can be trusted.
    ``None`` means conflict; an empty string means no alias was populated.
    """

    if not isinstance(payload, dict):
        return ""
    values = [str(payload.get(key) or "").strip() for key in keys]
    values = [value for value in values if value]
    if not values:
        return ""
    identity_keys = {_norm(value) if normalize else value for value in values}
    if len(identity_keys) != 1:
        return None
    return values[0]


def _search_result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse one search result without turning schema drift into no-match."""

    if "results" in result:
        rows: Any = result["results"]
    elif "units" in result:
        rows = result["units"]
    else:
        global _last_error
        _last_error = "ncs_mcp_search_schema_error"
        raise NcsMcpError("NCS MCP search returned an invalid response")
    if isinstance(rows, dict):
        if "items" not in rows:
            _last_error = "ncs_mcp_search_schema_error"
            raise NcsMcpError("NCS MCP search returned an invalid response")
        rows = rows["items"]
    if not isinstance(rows, list):
        _last_error = "ncs_mcp_search_schema_error"
        raise NcsMcpError("NCS MCP search returned an invalid response")
    if any(not isinstance(row, dict) for row in rows):
        _last_error = "ncs_mcp_search_schema_error"
        raise NcsMcpError("NCS MCP search returned an invalid response")
    return rows


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


def _preflight_ksa_unit_identity(unit: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize official-shaped KSA inputs before any MCP request.

    Ten-digit NCS unit codes embed their owning eight-digit detail code.  A
    caller-supplied name or detail label must therefore resolve to that exact
    immutable catalog identity before it is allowed to select remote KSA
    evidence.  Synthetic/legacy fixture identifiers retain the historical
    response-code-only contract.
    """

    code = str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
    if not re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code):
        return dict(unit)

    for identity_field in ("ncsClCd", "unit_code"):
        alias_code = str(unit.get(identity_field) or "").strip()
        if alias_code and alias_code != code:
            raise NcsMcpError("NCS MCP KSA request unit code identity mismatch")

    unit_name = _first_ksa_value(
        unit,
        "compeUnitName",
        "unit_name",
        "officialUnitName",
    )
    detail_name = _first_ksa_value(
        unit,
        "ncsSubdCdnm",
        "canonicalDetailName",
        "officialDetailName",
    )
    resolved = resolve_official_unit_selection(code, unit_name, detail_name)
    if resolved is None:
        raise NcsMcpError(
            "NCS MCP KSA request unit identity is not verified by official catalogs"
        )

    canonical_unit_name = str(resolved["compeUnitName"])
    canonical_detail_name = str(resolved["ncsSubdCdnm"])
    for identity_field in ("compeUnitName", "unit_name", "officialUnitName"):
        value = str(unit.get(identity_field) or "").strip()
        if value and _norm(value) != _norm(canonical_unit_name):
            raise NcsMcpError("NCS MCP KSA request unit name identity mismatch")
    for identity_field in (
        "ncsSubdCdnm",
        "canonicalDetailName",
        "officialDetailName",
    ):
        value = str(unit.get(identity_field) or "").strip()
        if value and _norm(value) != _norm(canonical_detail_name):
            raise NcsMcpError("NCS MCP KSA request detail name identity mismatch")

    official_detail_code = str(unit.get("officialDetailCode") or "").strip()
    if official_detail_code and official_detail_code != code[:8]:
        raise NcsMcpError("NCS MCP KSA request detail code identity mismatch")
    official_base_code = str(unit.get("officialUnitBaseCode") or "").strip()
    if official_base_code and official_base_code != code.split("_", 1)[0]:
        raise NcsMcpError("NCS MCP KSA request unit base identity mismatch")

    canonical = dict(unit)
    canonical.update(resolved)
    return canonical


def _classification_detail_code(classification: dict[str, Any]) -> str:
    """Return the exact eight-digit code from the MCP four-level path."""

    segments = [
        str(classification.get(field) or "").strip()
        for field in ("major_code", "middle_code", "small_code", "sub_code")
    ]
    if not all(re.fullmatch(r"\d{2}", segment) for segment in segments):
        return ""
    return "".join(segments)


def get_ksa_by_units(units: list[dict[str, Any]], max_factors_per_unit: int = 12) -> list[dict[str, Any]]:
    """Fetch official KSA rows from NCS_MCP's ncs_unit_detail tool."""

    per_unit_limit = max(1, int(max_factors_per_unit or 12))
    submitted_units = [
        dict(unit)
        for unit in (units or [])
        if isinstance(unit, dict)
        and str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
    ]
    # Resolve the complete batch before opening an MCP request session.  An
    # invalid later row must not race a valid earlier row into a network call.
    selected_units = [_preflight_ksa_unit_identity(unit) for unit in submitted_units]
    parallel_stop = threading.Event()
    retry_budget_lock = threading.Lock()
    parallel_retry_budget = 1

    def fetch_unit(unit: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal parallel_retry_budget
        code = str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
        result: dict[str, Any] | None = None
        for attempt in range(2):
            if concurrency > 1 and parallel_stop.is_set():
                raise NcsMcpError("NCS MCP KSA batch cancelled after peer failure")
            try:
                result = _call_tool(
                    "ncs_unit_detail",
                    {
                        "unit_code": code,
                        "include": ["elements", "criteria", "ksa"],
                        "text_version": "raw",
                    },
                )
                break
            except NcsMcpError:
                if attempt == 1:
                    if concurrency > 1:
                        parallel_stop.set()
                    raise
                if concurrency > 1:
                    with retry_budget_lock:
                        if parallel_retry_budget <= 0:
                            parallel_stop.set()
                            raise
                        parallel_retry_budget -= 1
        if result is None:  # pragma: no cover - defensive; both attempts either return or raise.
            raise NcsMcpError("NCS MCP unit detail request returned no result")
        detail = _detail_payload(result)
        detail_unit = detail.get("unit") if isinstance(detail.get("unit"), dict) else {}
        response_code = _consistent_identity_value(
            detail_unit,
            "unit_code",
            "ncsClCd",
            "id",
            normalize=False,
        )
        if response_code is None or response_code != code:
            raise NcsMcpError("NCS MCP unit detail identity mismatch")
        classification = (
            detail_unit.get("classification")
            if isinstance(detail_unit.get("classification"), dict)
            else {}
        )
        catalog_verified = bool(unit.get("unitCatalogVerified")) and bool(
            re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", code)
        )
        identity_provenance: dict[str, Any] = {}
        if catalog_verified:
            canonical_unit_name = str(unit.get("officialUnitName") or "").strip()
            canonical_detail_name = str(unit.get("officialDetailName") or "").strip()
            expected_detail_code = str(unit.get("officialDetailCode") or "").strip()
            response_unit_name = _consistent_identity_value(
                detail_unit,
                "unit_name",
                "compeUnitName",
                normalize=True,
            )
            response_detail_code = _classification_detail_code(classification)
            response_detail_name = _consistent_identity_value(
                classification,
                "sub",
                "sub_name",
                "ncsSubdCdnm",
                normalize=True,
            )
            if (
                response_unit_name is None
                or _norm(response_unit_name) != _norm(canonical_unit_name)
            ):
                raise NcsMcpError("NCS MCP unit detail name identity mismatch")
            if response_detail_code != expected_detail_code:
                raise NcsMcpError("NCS MCP unit detail classification code mismatch")
            if (
                response_detail_name is None
                or _norm(response_detail_name) != _norm(canonical_detail_name)
            ):
                raise NcsMcpError("NCS MCP unit detail classification name mismatch")
            identity_provenance = {
                "unitCatalogVerified": True,
                "unitResponsePathVerified": True,
                "unitIdentityVerificationKind": (
                    "immutable_catalog_and_mcp_response_path_exact"
                ),
                "officialUnitName": canonical_unit_name,
                "officialDetailCode": expected_detail_code,
                "officialDetailName": canonical_detail_name,
                "responseDetailCode": response_detail_code,
                "unitResolutionKind": unit.get("unitResolutionKind"),
                "unitVersionCompatible": bool(unit.get("unitVersionCompatible")),
                "catalogUnitCodes": list(unit.get("catalogUnitCodes") or []),
            }
        interleaved = _interleave_element_ksa(
            [element for element in (detail.get("elements") or []) if isinstance(element, dict)]
        )
        rows: list[dict[str, Any]] = []
        for row in _balanced_ksa(interleaved, per_unit_limit):
            rows.append(
                {
                    "ncsClCd": code,
                    "requestedUnitCode": code,
                    "responseUnitCode": response_code,
                    "unitIdentityVerified": catalog_verified,
                    **identity_provenance,
                    "compeUnitName": (
                        unit.get("compeUnitName")
                        if catalog_verified
                        else unit.get("compeUnitName") or detail_unit.get("unit_name", "")
                    ),
                    "ncsSubdCdnm": (
                        unit.get("ncsSubdCdnm")
                        if catalog_verified
                        else unit.get("ncsSubdCdnm") or classification.get("sub", "")
                    ),
                    "elementId": row.get("_ncscope_element_id"),
                    "elementName": row.get("_ncscope_element_name", ""),
                    "performanceCriteria": row.get("_ncscope_element_criteria") or [],
                    "factorName": _first_ksa_value(row, "text", "ksa_text", "factorName", "factor_name"),
                    "ksaTypeName": _canonical_ksa_type(row),
                    "ksaNo": _first_ksa_value(row, "ksa_no", "ksaNo", "number"),
                    "factorSource": "ncs-mcp",
                    "source": "ncs-mcp",
                    "ksaStatus": (
                        "official" if catalog_verified else "unverified"
                    ),
                    "isOfficialKsa": catalog_verified,
                    "provenanceScope": (
                        "configured-ncs-mcp-client-contract-not-independent-"
                        "upstream-database-attestation"
                    ),
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
    with use_ncs_mcp_request_session() as request_session:
        if concurrency == 1:
            per_unit_rows = [fetch_unit(unit) for unit in selected_units]
        else:
            per_unit_rows = []
            batch_size = concurrency
            executor = ThreadPoolExecutor(
                max_workers=concurrency,
                thread_name_prefix="ncs-mcp-ksa",
            )
            shutdown_without_wait = False
            try:
                # Bound the submitted backlog while retaining the confirmed
                # NCS-unit order and sharing one initialized MCP transport.
                for offset in range(0, len(selected_units), batch_size):
                    futures = []
                    for unit in selected_units[offset : offset + batch_size]:
                        request_session.retain_worker()
                        release_detached_work = _retain_detached_work()
                        try:
                            future = executor.submit(
                                copy_context().run,
                                fetch_unit,
                                unit,
                            )
                        except BaseException:
                            request_session.release_worker()
                            release_detached_work()
                            raise

                        def release_worker_leases(
                            _future,
                            session=request_session,
                            release_outer=release_detached_work,
                        ) -> None:
                            try:
                                session.release_worker()
                            finally:
                                release_outer()

                        future.add_done_callback(release_worker_leases)
                        futures.append(future)
                    completed, pending = wait(futures, return_when=FIRST_EXCEPTION)
                    failures = [
                        future.exception()
                        for future in completed
                        if not future.cancelled() and future.exception() is not None
                    ]
                    failure = next(
                        (
                            error
                            for error in failures
                            if "batch cancelled after peer failure" not in str(error)
                        ),
                        failures[0] if failures else None,
                    )
                    if failure is not None:
                        parallel_stop.set()
                        for future in pending:
                            future.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        shutdown_without_wait = True
                        raise failure
                    per_unit_rows.extend(future.result() for future in futures)
            finally:
                if not shutdown_without_wait:
                    executor.shutdown(wait=True, cancel_futures=True)

    output: list[dict[str, Any]] = []
    for rows in per_unit_rows:
        output.extend(rows)
    return output


def ncs_mcp_status(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return a short-lived readiness probe without amplifying health traffic."""

    global _status_cache
    endpoint = _endpoint()
    now = time.monotonic()
    with _status_cache_lock:
        stale_status = (
            dict(_status_cache[2])
            if _status_cache and _status_cache[1] == endpoint
            else None
        )
        if (
            not force_refresh
            and _status_cache
            and _status_cache[1] == endpoint
            and now - _status_cache[0] < _STATUS_TTL
        ):
            cached = dict(_status_cache[2])
            cached["tools"] = list(cached.get("tools") or [])
            return cached

    acquired_probe = _status_probe_lock.acquire(blocking=False)
    if not acquired_probe:
        if stale_status is not None:
            stale_status["tools"] = list(stale_status.get("tools") or [])
            stale_status["stale"] = True
            stale_status["probeInProgress"] = True
            stale_status["cacheAgeSeconds"] = max(
                0.0,
                round(now - float(_status_cache[0]), 3),
            ) if _status_cache else None
            return stale_status
        return {
            "configured": bool(endpoint),
            "reachable": False,
            "tools": [],
            "ksaAvailable": False,
            "lastError": "ncs_mcp_probe_in_progress",
            "stale": False,
            "probeInProgress": True,
        }

    try:
        # A different caller may have populated the cache between our first
        # snapshot and acquiring the single-flight probe lease.
        if not force_refresh:
            refreshed_now = time.monotonic()
            with _status_cache_lock:
                if (
                    _status_cache
                    and _status_cache[1] == endpoint
                    and refreshed_now - _status_cache[0] < _STATUS_TTL
                ):
                    cached = dict(_status_cache[2])
                    cached["tools"] = list(cached.get("tools") or [])
                    return cached

        try:
            # The status cache is deliberately much shorter than discovery's
            # five-minute cache, so readiness detects outages promptly while
            # repeated platform probes share one downstream request.
            if remaining_request_budget_sec() is None:
                with use_request_budget(_STATUS_PROBE_BUDGET_SEC):
                    names = sorted(_tool_names(force_refresh=True))
            else:
                names = sorted(_tool_names(force_refresh=True))
            status = {
                "configured": bool(endpoint),
                "reachable": True,
                "tools": names,
                "ksaAvailable": "ncs_unit_detail" in names,
                "lastError": None,
            }
        except NcsMcpError:
            status = {
                "configured": bool(endpoint),
                "reachable": False,
                "tools": [],
                "ksaAvailable": False,
                "lastError": "ncs_mcp_unreachable",
            }
        with _status_cache_lock:
            _status_cache = (time.monotonic(), endpoint, status)
        return {**status, "tools": list(status["tools"])}
    finally:
        _status_probe_lock.release()
