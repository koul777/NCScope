# NCScope MVP Smoke Report - 2026-07-23

## Scope

Verified the MVP path:

1. Kordoc-backed JD parse/review API.
2. Human-in-the-loop detail classification gate.
3. NCS_MCP-only official unit/KSA lookup when `NCS_MCP_URL` is configured.
4. Compact NCS_MCP serving DB deployment policy.
5. Real ALIO job-description download and parsing benchmark.

## Payroll2 Evidence

- `python -m pytest -q`: 150 passed, 2 FastAPI deprecation warnings.
- `python -m py_compile app\main.py app\settings.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\question_generation.py app\services\kordoc_parser.py app\services\external_api.py scripts\benchmark_alio_jd.py`: passed.
- API smoke with confirmed detail `경영기획`:
  - HTTP status: 200
  - `ncs_source`: `ncs-mcp+rerank`
  - `jd_review_confirmed`: true
  - NCS matches: 4
  - KSA rows: 12
  - KSA factor source: `ncs-mcp`
- Direct MCP smoke:
  - `search_units_by_detail(["경영기획"], max_units=3)`: 3 units.
  - `get_ksa_by_units(units[:1], max_factors_per_unit=3)`: 3 official KSA rows.
- `/health` TestClient smoke:
  - HTTP status: 200
  - `ncs_source`: `remote-mcp`
  - `ncs_mcp.reachable`: true
  - `ncs_mcp.ksaAvailable`: true
  - endpoint URL is not exposed in the public health payload.
- MCP-only policy tests:
  - unconfirmed review -> 400
  - truthy string review confirmation -> 400
  - confirmed review with empty detail candidates -> 422
  - strict detail matching excludes small-category-only matches
  - legacy `/api/ncs/sclass/ksa` default -> 410
  - oversized upload rejected before Kordoc -> 413

## ALIO JD Benchmark Evidence

Command:

```powershell
$env:NCS_MCP_URL='http://127.0.0.1:8778/mcp'
python scripts\benchmark_alio_jd.py --limit 5 --include-ksa
```

Latest report:

- `reports\alio_jd_benchmark_20260723_041523.md`
- `reports\alio_jd_benchmark_20260723_041523.csv`

Results:

- Samples attempted: 5
- Parsed documents: 4
- Documents with detail candidates: 2
- Total detail candidates: 8
- Average parse time: 515 ms
- One ZIP attachment was classified as unsupported because the MVP scope is PDF/HWP/HWPX/DOCX.

Observed detail candidates:

- `정보통신기획평가원`: `프로젝트관리`, `정보기술전략`, `정보기술기획`, `IT프로젝트관리`, `총무`, `환경미화`
- `동남권원자력의학원`: `간호수행`, `간호행정관리`

Known benchmark finding:

- `간호수행` and `간호행정관리` were extracted from the ALIO JD, but current strict NCS_MCP search returned 0 units. Treat this as an NCS_MCP alias/coverage follow-up, not a payroll2 parsing failure.
- Documents without an NCS classification table correctly require human detail entry in the review step.

## NCS_MCP Serving DB Evidence

Compact DB:

`C:\workspace\NCS_MCP\tmp\ncs_interview_serving_test.db`

| Table | Rows |
| --- | ---: |
| `classifications` | 1,109 |
| `competency_units` | 13,435 |
| `performance_criteria` | 196,658 |
| `ksa_items` | 574,279 |
| `ncs_training_courses` | 11,819 |
| `ncs_query_aliases` | 32 |

File size: 117,108,736 bytes.

Release hashes:

- DB SHA-256: `1FA2520DA544F97177A472E3EE8BBD32B3DBFB249986F901EF58CFF138ED79A2`
- Manifest SHA-256: `FD739BFC1240C806EDE810B70FC02B710996AAAE6547ECE7CA34459A29B1DEBD`

Read-only check:

- `NCS_MCP_READ_ONLY=1`
- SQLite `PRAGMA query_only=1`
- Read-only connection successfully queried `competency_units`.

## Deployment Notes

- App image should not contain `.db`, `.xlsx`, logs, virtual environments, or `node_modules`.
- NCS serving DB should be published as a GitHub Release asset or external artifact.
- App runtime requires `NCS_MCP_URL`.
- NCS_MCP runtime requires `NCS_DB_PATH` and should use `NCS_MCP_READ_ONLY=1`.
- Admin endpoints require `ENABLE_ADMIN_ENDPOINTS=true` and `ADMIN_TOKEN`.
- Legacy NCS API endpoints require `ENABLE_LEGACY_NCS_API=true`; default is disabled.
