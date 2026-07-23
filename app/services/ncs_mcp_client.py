"""Read-only client for the prepared NCS_MCP serving service.

The interview app never opens or copies the multi-gigabyte ontology DB.  It
calls the public NCS_MCP tools over Streamable HTTP after the reviewer confirms
the extracted 세분류.  ``ncs_unit_detail`` is the authoritative KSA path.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.settings import settings

MCP_PROTOCOL_VERSION = "2025-03-26"
_TOOLS_TTL = 300.0
_tools_cache: tuple[float, set[str]] | None = None
_last_error: str | None = None


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
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    try:
        with httpx.Client(timeout=settings.ncs_mcp_timeout_sec(), follow_redirects=True) as client:
            init = client.post(
                endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ncscope", "version": "0.1"},
                    },
                },
            )
            init.raise_for_status()
            _decode_rpc(init.text)
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": method,
                    "params": params or {},
                },
            )
            response.raise_for_status()
            return _decode_rpc(response.text)
    except (httpx.HTTPError, NcsMcpError) as exc:
        global _last_error
        _last_error = str(exc)
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


def _tool_names() -> set[str]:
    global _tools_cache
    now = time.monotonic()
    if _tools_cache and now - _tools_cache[0] < _TOOLS_TTL:
        return _tools_cache[1]
    result = _rpc("tools/list")
    names = {
        str(item.get("name"))
        for item in result.get("tools", [])
        if isinstance(item, dict) and item.get("name")
    }
    _tools_cache = (now, names)
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
    return re.sub(r"[\s·‧･ㆍ•∙⋅・\-\_/|(),.]+", "", str(value or "")).lower()


_DETAIL_QUERY_ALIASES_BY_KEY = {
    # Public NCS classifies this under 건축설계·감리 > 건축공사감리.
    # Some ALIO JDs shorten the 세분류 label to 건축감리.
    _norm("건축감리"): ("건축공사감리",),
}


def _detail_query_names(name: str) -> list[str]:
    names = [str(name or "").strip()]
    for alias in _DETAIL_QUERY_ALIASES_BY_KEY.get(_norm(name), ()):
        alias = str(alias or "").strip()
        if alias and all(_norm(alias) != _norm(existing) for existing in names):
            names.append(alias)
    return names


def _detail_search_query_limit(max_units: int) -> int:
    return min(200, max(100, int(max_units or 0) * 5))


def search_units_by_detail(detail_names: list[str], max_units: int = 80) -> list[dict[str, Any]]:
    """Resolve confirmed 세분류 names to NCS ability units."""

    if "ncs_search" not in _tool_names():
        raise NcsMcpError("configured NCS MCP does not expose ncs_search")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for detail in detail_names:
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
                if not code or code in seen:
                    continue
                seen.add(code)
                is_alias = _norm(query_name) != _norm(name)
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

    if "ncs_search" not in _tool_names():
        raise NcsMcpError("configured NCS MCP does not expose ncs_search")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, int(max_units or 20))
    for term in terms:
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


def _balanced_ksa(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {"지식": [], "기술": [], "태도": []}
    other: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row.get("ksa_type") or row.get("ksaType") or row.get("ksa_type_name") or "").strip()
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


def get_ksa_by_units(units: list[dict[str, Any]], max_factors_per_unit: int = 12) -> list[dict[str, Any]]:
    """Fetch official KSA rows from NCS_MCP's ncs_unit_detail tool."""

    if "ncs_unit_detail" not in _tool_names():
        raise NcsMcpError("configured NCS MCP does not expose ncs_unit_detail")
    output: list[dict[str, Any]] = []
    per_unit_limit = max(1, int(max_factors_per_unit or 12))
    for unit in units:
        code = str(unit.get("ncsClCd") or unit.get("unit_code") or "").strip()
        if not code:
            continue
        result = _call_tool(
            "ncs_unit_detail",
            {"unit_code": code, "include": ["elements", "criteria", "ksa"], "text_version": "raw"},
        )
        detail = _detail_payload(result)
        selected_for_unit = 0
        for element in detail.get("elements") or []:
            if selected_for_unit >= per_unit_limit:
                break
            if not isinstance(element, dict):
                continue
            raw_ksa = [row for row in (element.get("ksa") or []) if isinstance(row, dict)]
            remaining = max(0, per_unit_limit - selected_for_unit)
            for row in _balanced_ksa(raw_ksa, remaining):
                output.append(
                    {
                        "ncsClCd": code,
                        "compeUnitName": unit.get("compeUnitName") or detail.get("unit", {}).get("unit_name", ""),
                        "ncsSubdCdnm": unit.get("ncsSubdCdnm") or detail.get("unit", {}).get("classification", {}).get("sub", ""),
                        "elementId": element.get("element_id"),
                        "elementName": element.get("element_name", ""),
                        "factorName": row.get("text", ""),
                        "ksaTypeName": row.get("ksa_type", ""),
                        "ksaNo": row.get("ksa_no", ""),
                        "factorSource": "ncs-mcp",
                        "source": "ncs-mcp",
                        "ksaStatus": "official",
                        "isOfficialKsa": True,
                    }
                )
                selected_for_unit += 1
                if selected_for_unit >= per_unit_limit:
                    break
    return output


def ncs_mcp_status() -> dict[str, Any]:
    try:
        names = sorted(_tool_names())
        return {
            "configured": bool(_endpoint()),
            "reachable": True,
            "tools": names,
            "ksaAvailable": "ncs_unit_detail" in names,
            "lastError": _last_error,
        }
    except NcsMcpError as exc:
        return {
            "configured": bool(_endpoint()),
            "reachable": False,
            "tools": [],
            "ksaAvailable": False,
            "lastError": str(exc),
        }
