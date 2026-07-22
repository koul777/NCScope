# NCScope MVP Smoke Report - 2026-07-23

## Scope

Verified the MVP path:

1. Kordoc-backed JD parse/review API.
2. Human-in-the-loop detail classification gate.
3. NCS_MCP-only official unit/KSA lookup when `NCS_MCP_URL` is configured.
4. Compact NCS_MCP serving DB deployment policy.
5. Real ALIO job-description download and parsing benchmark.

## NCScope Evidence

- `python -m pytest -q`: 168 passed, 2 FastAPI deprecation warnings.
- `python -m py_compile app\main.py app\settings.py app\repository.py app\models.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\question_generation.py app\services\kordoc_parser.py app\services\external_api.py scripts\benchmark_alio_jd.py`: passed.
- API smoke with confirmed detail `경영기획`:
  - HTTP status: 200
  - `ncs_source`: `ncs-mcp+ai-rerank`
  - `openai_key_source`: `env`
  - `jd_review_confirmed`: true
  - NCS matches: 4
  - KSA rows: 12
  - KSA factor source: `ncs-mcp`
  - Generated interview questions: 10 in the latest live model smoke
  - Question-level `ksa_evidence`: 2-3 official KSA rows attached per generated question
  - `question_evidence_policy`: `ncs_mcp_ksa_attached_by_code_and_ref`
  - `jd_review_session_id`: present
  - Audit event for the confirmed review/generation path: recorded without API key or document body.
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
  - encrypted ZIP member -> 422 instead of server error
  - ZIP image member -> parsed through Kordoc/OCR path instead of being skipped
  - confirmed generation requires a server-created `review_session_id`
  - KSA lookup requires `NCS_MCP_URL`; no XLSX/HRDK/definition fallback is used for official KSA

## ALIO JD Benchmark Evidence

Command:

```powershell
$env:NCS_MCP_URL='http://127.0.0.1:8778/mcp'
python scripts\benchmark_alio_jd.py --limit 10 --include-ksa
```

Latest report:

- `reports\alio_jd_benchmark_20260723_053926.md`
- `reports\alio_jd_benchmark_20260723_053926.csv`

Results:

- Samples attempted: 10
- Parsed documents: 9
- Documents with detail candidates: 7
- Documents with detail candidates but no MCP match: 3
- Notice pages with duty text candidates: 10
- Notice pages with evaluation text candidates: 9
- Detail-no-match documents with manual NCS suggestions: 2
- Total detail candidates: 27
- Average parse time: 638 ms
- ZIP attachments are parsed in memory when they contain supported PDF/HWP/HWPX/DOCX/TXT files.

Observed detail candidates:

- `정보통신기획평가원`: `프로젝트관리`, `정보기술전략`, `정보기술기획`, `IT프로젝트관리`, `총무`, `환경미화`
- `동남권원자력의학원`: `간호수행`, `간호행정관리`
- `한국수력원자력`: `원자력발전설비운영`, `원자력발전기계설비정비`
- `동남권원자력의학원`: `임상병리`

Known benchmark finding:

- `간호수행`, `간호행정관리`, and `임상병리` were extracted from ALIO JDs, but current strict NCS_MCP search returned 0 units. Treat this as an NCS_MCP alias/coverage follow-up, not a NCScope parsing failure.
- Documents without an NCS classification table correctly require human detail entry in the review step.
- Table-label noise such as `능력단위` and `주요사업` is now filtered from detail-class candidates.
- When exact detail-class matching fails, NCScope now returns manual-selection suggestions instead of generating ungrounded interview questions.
- Gap evidence: `reports\ncs_mcp_detail_gap_20260723.md`

## NCS_MCP Serving DB Evidence

Compact DB:

`C:\workspace\NCS_MCP\tmp\ncs_interview_serving_release.db`

| Table | Rows |
| --- | ---: |
| `classifications` | 1,109 |
| `competency_units` | 13,435 |
| `competency_elements` | 47,620 |
| `performance_criteria` | 196,658 |
| `ksa_items` | 574,279 |
| `ncs_training_courses` | 11,819 |
| `ncs_query_aliases` | 32 |

File size: 117,108,736 bytes.

Release hashes:

- DB SHA-256: `F9BB59B8853E8F69DC4698028EC347ED9BD74D26133FBCEB031B05FD90F89B23`
- Manifest SHA-256: `1BC90A36BBE8CDBEC2A5162EAA1AECC48AC1ED3FA0334864B078960176A20368`

GitHub public release:

- Tag: `ncscope-db-v0.1.0-20260723`
- Assets:
  - `ncs_interview_serving_release.db` — 117,108,736 bytes
  - `ncs_interview_serving_release.json` — 476 bytes

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
