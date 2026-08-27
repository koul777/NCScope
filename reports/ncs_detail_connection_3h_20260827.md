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
   - Official labels that contain transport delimiters remain atomic when the
     whole value has one unique catalog identity. This fixes
     `15080205 조선비계(족장, 발판, scaffolding)` without weakening genuine
     multi-label splitting.
   - Name/format/alias lookup stays at 200 rows. Only when the returned,
     catalog-verified base identities are incomplete does one eight-digit
     official detail-code lookup run; its rows pass the same path, code-prefix,
     canonical unit-name, and catalog checks before merging.
   - MCP tool `isError` and malformed search envelopes raise explicit errors;
     they can no longer degrade into an ordinary zero-result.
   - Exact full codes and unknown suffixes share the same ten-digit base
     identity gate. A conflicting name/detail on any catalog version rejects
     the whole base.
   - Source/format/alias candidates must converge on one official detail before
     the first network call. Populated alias fields such as `id`/`unit_code`,
     `text`/`unit_name`, and detail path aliases must also agree.
   - Small global result limits are applied after per-detail collection by
     input-order round robin, removing the former later-input tie bias.
   - LF-normalized SHA-256 pins make partially replaced or self-consistently
     truncated detail/unit catalog files fail closed before an MCP call.
   - Official-shaped KSA requests are re-resolved before the batch opens a
     network session. The response code, canonical unit name, four two-digit
     classification segments, and canonical detail name must all agree before
     KSA rows receive verified provenance.
   - Every returned official unit row carries the detail's official expected
     and verified base counts. `detailRetrievalComplete` means the expected
     base set was fully verified; `detailRetrievalCapLimited` separately means
     recovery or returned rows were reduced by the caller's output limit, so a
     complete-but-output-truncated group can validly set both flags to `true`.
   - Non-authoritative text suggestions now use the strict search-envelope
     parser and require code, unit-name, and detail-path aliases to agree. A
     path that names a current official detail but contradicts the unit code's
     current detail scope is discarded; stale/free-form labels remain manual
     review suggestions and are not promoted to official links.
   - Search rows that expose four two-digit classification path segments must
     reproduce the official eight-digit detail code exactly. Partial,
     malformed, or contradictory path-code tuples are rejected; compatible
     rows that omit the tuple remain explicitly marked unverified.
   - A result-returning wrapper preserves per-term coverage even when no row
     survives validation or a global cap hides an entire group. The public
     options endpoint keeps official zero-row results fail-closed instead of
     replacing them with non-authoritative suggestions.
   - Six-digit small-class question generation scopes units by code prefix
     only. Code and label searches run independently, candidates are resolved
     against the immutable unit catalog, and multiple published versions are
     deduplicated by their ten-digit semantic base before KSA lookup. Public
     official-KSA evidence requires both catalog and response path
     verification, so legacy fixture identifiers cannot attest public
     questions.
   - Detail transport parsing protects balanced parenthetical delimiters and
     unique catalog-exact slash names, but splits distinct names such as
     `PR/SCM`. Unbalanced grouping cannot consume later structured terms, and
     long numbered rows preserve all 23 current parenthetical detail names.

2. `app/main.py`
   - Added a shared provenance reattachment path so MCP/catalog evidence survives rerank, safe fallback, and `selected_ncs` generation input reconstruction.
   - Preserved fields include `officialUnitBaseCode`, `officialUnitName`, `unitResolutionKind`, `unitVersionCompatible`, `catalogUnitCodes`, `mcpUnitName`, and the reviewed-unit lock fields.
   - Per-row expected/verified counts and the complete/cap-limited retrieval
     flags now survive the same server-controlled provenance path.
   - Manual `selected_ncs` rows are no longer trusted as client assertions.
     The server re-resolves code, canonical unit name, and canonical detail;
     mismatches stop with `422 selected_ncs_official_identity_mismatch` before
     KSA or model work.

3. `scripts/audit_ncs_detail_connections.py`
   - Confirmed aggregate fail-closed audit remains green on the shipped catalogs.
   - Current aggregate counts:
     - active detail: 1094
     - unit total: 13282
     - full codes: 13282
     - base codes: 13281
     - multi-version base: 1

4. `scripts/probe_ncs_detail_connections.py`
   - Added a reproducible, read-only live coverage probe with aggregate-only
     output, exact detail filters, deterministic JSON, sanitized failures, and
     explicit exit codes for gaps versus identity violations.

## Validation

- `pytest -q tests/test_mcp_only_policy.py`: 149 passed
- Focused detail recovery, KSA identity, live-probe, and runtime catalog
  integrity suites: passed
- `pytest -q`: 5323 passed, 72 skipped
- `ruff check` on every changed Python file: passed
- Independent diff review: no confirmed P0/P1 findings
- `python scripts/audit_ncs_detail_connections.py`: pass
- `python scripts/probe_ncs_detail_connections.py --output reports/ncs_detail_connection_live_probe_20260827.json` on the local read-only public MCP:
  - expected unit bases: 13281
  - verified expected unit bases: 13278 (coverage 0.999774113)
  - complete details: 1091
  - partial details: 3
  - zero-result details: 0
  - unexpected bases: 0; identity violations: 0
  - name queries: 1094; eight-digit recovery queries: 4
  - name/format/alias retrieval: 13235 bases
  - eight-digit code recovery: 43 bases
  - resolution: 13278 full-code exact, 0 natural base-version fallback
  - measured elapsed time for the recorded fresh-server run with one shared
    MCP transport session: 23.848 seconds
- Live KSA identity probe: one normal public unit passed canonical response-path
  verification; the three known upstream wrong-path units all failed with a
  classification-code mismatch.
- Live six-digit scope probe for `020203`: 12/12 selected units retained the
  requested prefix, passed immutable catalog resolution, and had 12 distinct
  semantic base identities. A sampled unit returned three KSA rows with the
  catalog, response-path, and official-KSA flags all verified.
- A measured 500-row query recovered the broad `이용` result but increased a
  warm request from 21.5 ms to 39.8 ms and payload from 332,043 to 840,571
  bytes. Applying it by name length/unit count would affect 1013/1094 details,
  so the bounded 200-row + gap-only code query is used instead.

## Added regression coverage

- duplicate exact full-code rows are rejected
- base fallback is rejected when the same ten-digit base spans multiple canonical names
- invalid rows do not consume semantic identity or result limit
- missing bundled catalogs raise an explicit error and `/api/ncs/units/options` returns `502` instead of a misleading empty list
- selected `selected_ncs` inputs preserve link provenance in the final response
- selected `selected_ncs` code/name/detail tampering is rejected before KSA
- incomplete name results merge only missing verified identities; complete
  results add no recovery call
- code recovery rejects unrelated prefixes, wrong paths, renamed units, and
  the three observed upstream path mismatches
- MCP tool errors and schema drift cannot masquerade as a no-match
- exact suffixes cannot bypass a conflicting stable base identity
- alias catalog drift and conflicting duplicate identity fields fail closed
- result limits below the number of details retain input-order precedence
- complete, partial, output-cap-limited, and upstream-gap retrieval states
  retain the official expected/verified base counts on every returned row;
  these fields survive server-controlled provenance reconstruction
- non-authoritative suggestion schema drift raises explicitly; malformed unit
  codes, conflicting code/name/path aliases, and current-detail code/path
  contradictions are rejected without treating suggestions as official links
- official zero-row coverage survives without a sentinel option or suggestion
  fallback; unresolved labels retain the existing manual suggestion path
- four-level search path codes accept exact tuples and reject partial or
  conflicting tuples in both exact and suggestion paths
- contradictory six-digit small-class names cannot introduce another code
  prefix; numeric-search saturation cannot hide the independent name query;
  semantic unit versions are deduplicated; and unverified KSA registry rows
  fail the public quality gate
- bracketed delimiters, distinct acronym slash labels, malformed grouping,
  punctuation-equivalent official labels, and all 1,094 official detail-name
  round trips retain the intended atomic/split boundary
- valid-JSON catalog byte drift fails before MCP access, while CRLF checkouts
  preserve the pinned LF-normalized digest
- KSA input tampering, response name drift, response code/path drift, and
  conflicting response aliases are rejected; canonical responses retain full
  verification provenance

## Residual risk

- The remaining live MCP gaps are three missing base identities:
  - `02010101 경영기획`: missing `0201010106`
  - `02020302 사무행정`: missing `0202030207`
  - `23040102 환경시설운영`: missing `2304010211`
- Direct unit-name/base/full-code probes return those three rows with a path
  owned by a different detail. Keeping them rejected is therefore an upstream
  data-integrity quarantine, not a local retrieval miss.
- The shipped aggregate audit proves static catalog integrity, not live remote MCP freshness.
