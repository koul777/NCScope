# NCS detail-unit connection hardening log

- Session window: August 27, 2026 KST, continuing the 3-hour accuracy hardening block
- Scope: raise precision for `세분류 -> 능력단위` linking without using holdout/blind records
- Focus: fail-closed catalog validation, version-skew handling, provenance retention, aggregate audit

## Changes

1. `app/services/ncs_mcp_client.py`
   - `search_units_by_detail()` now fails loudly when the bundled official detail or unit catalogs are unavailable, instead of returning an ordinary empty result.
   - Exact full-code validation remains authoritative.
   - Base-code fallback is now allowed only when every catalog row for that ten-digit base proves one canonical identity.
   - `unitVersionCompatible` now distinguishes exact full-code matches (`False`) from version-compatible base fallback (`True`).

2. `app/main.py`
   - Added a shared provenance reattachment path so MCP/catalog evidence survives rerank, safe fallback, and `selected_ncs` generation input reconstruction.
   - Preserved fields include `officialUnitBaseCode`, `officialUnitName`, `unitResolutionKind`, `unitVersionCompatible`, `catalogUnitCodes`, `mcpUnitName`, and the reviewed-unit lock fields.

3. `scripts/audit_ncs_detail_connections.py`
   - Confirmed aggregate fail-closed audit remains green on the shipped catalogs.
   - Current aggregate counts:
     - active detail: 1094
     - unit total: 13282
     - full codes: 13282
     - base codes: 13281
     - multi-version base: 1

## Validation

- `pytest -q tests/test_mcp_only_policy.py -k "mcp_search or ncs_units_options_reports_catalog_failure"`: passed
- `pytest -q tests/test_mcp_only_policy.py -k "uses_request_scoped_openai_key or recovered_required_unit_provenance_survives_endpoint_rerank or mcp_search or ncs_units_options_reports_catalog_failure"`: passed
- `pytest -q tests/test_ncs_detail_connection_audit.py tests/test_ncs_unit_catalog.py`: passed
- `pytest -q tests/test_mcp_only_policy.py tests/test_ncs_detail_connection_audit.py tests/test_ncs_unit_catalog.py tests/test_release_evidence_contract.py tests/test_stored_jd_final_blind_artifacts.py`: 162 passed
- `pytest -q`: 5208 passed, 72 skipped
- `ruff check app/main.py app/services/ncs_mcp_client.py tests/test_mcp_only_policy.py scripts/audit_ncs_detail_connections.py tests/test_ncs_detail_connection_audit.py tests/test_ncs_unit_catalog.py`: passed
- `python scripts/audit_ncs_detail_connections.py`: pass
- Live local MCP probe on `http://127.0.0.1:8766/mcp` against all 1094 active official detail names:
  - expected unit bases: 13281
  - returned unit bases: 13278
  - complete details: 1091
  - partial details: 3
  - zero-result details: 0
  - false-positive extra bases: 0

## Added regression coverage

- duplicate exact full-code rows are rejected
- base fallback is rejected when the same ten-digit base spans multiple canonical names
- invalid rows do not consume semantic identity or result limit
- missing bundled catalogs raise an explicit error and `/api/ncs/units/options` returns `502` instead of a misleading empty list
- selected `selected_ncs` inputs preserve link provenance in the final response

## Residual risk

- The remaining live MCP gaps are three missing base identities:
  - `02010101 경영기획`: missing `0201010106`
  - `02020302 사무행정`: missing `0202030207`
  - `23040102 환경시설운영`: missing `2304010211`
- Direct `ncs_search` probing with limit `1000` still did not return those base codes, so the residual gap appears to be upstream MCP retrieval/index coverage rather than local fail-closed filtering.
- The shipped aggregate audit proves static catalog integrity, not live remote MCP freshness.
