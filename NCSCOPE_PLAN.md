# NCScope MVP Plan

## Goal

NCScope parses job descriptions with Kordoc, lets a human reviewer confirm the
NCS detail classification, then uses NCS_MCP to fetch official competency units,
performance criteria, and KSA evidence for structured interview generation.

The deployed app must not include the original 12.6 GB NCS source DB or
`NCS_DB.xlsx`. The compact read-only serving DB is published separately as a
Release asset or external artifact and is consumed only through `NCS_MCP_URL`.

## Product Scope

- Product name: NCScope
- Working app folder: `payroll2` for now
- Recommended public repo/folder name: `ncscope`
- Core user flow: upload JD -> parse -> review/confirm detail categories ->
  fetch official NCS/KSA -> generate structured interview questions

## Current Project Baseline

- `app/services/kordoc_parser.py` parses PDF/HWP/HWPX/DOCX through Kordoc and
  normalizes the result into reviewable fields.
- `app/main.py` exposes `/api/jd/parse-review` for human review and
  `/api/jd/strategy/upload` for interview strategy generation.
- `app/services/ncs_mcp_client.py` calls NCS_MCP over Streamable HTTP.
- `app/services/jd_strategy.py` still contains legacy HRDK/XLSX helper code,
  but the NCScope interview endpoints require `NCS_MCP_URL` before KSA lookup.
- `app/static/index.html` contains the existing browser UI and must show the
  extracted detail classifications before final generation.

## NCS Data Strategy

- Canonical full DB stays in `C:\workspace\NCS_MCP\data\processed\ncs.db`.
- Export script creates a compact SQLite serving DB with only the interview
  tables: classifications, competency units, elements, performance criteria,
  KSA rows, training-course readiness table, and aliases.
- The serving DB is read-only at runtime through `NCS_DB_PATH`.
- Git tracks source, scripts, docs, tests, and manifests only.
- Git does not track `.db`, `.xlsx`, temporary exports, logs, or local virtual
  environments.

## Implementation Tracks

1. Kordoc parsing

   Accept PDF/HWP/HWPX/DOCX and return structured fields:
   duties, qualifications, preferences, knowledge, skills, attitudes, basic
   competencies, and `ncs_detail_candidates`.

2. Human-in-the-loop review

   The UI must make the reviewer confirm or edit the extracted detail
   classifications. `jd_review_json.review_confirmed=true` is the gate for
   authoritative NCS lookup.

3. MCP-only official lookup

   When `NCS_MCP_URL` is configured, the app must call NCS_MCP only. It should
   fail visibly if MCP is unavailable, returns no units, or returns no official
   KSA rows. It should not silently fall back to `NCS_DB.xlsx`, HRDK, or local
   sample mappings in production mode.

4. Interview generation

   Generate structured questions using NCS competency units, performance
   criteria, and KSA evidence. Each question should preserve source evidence:
   NCS code, competency unit, KSA type/content, and evaluation intent.

5. Release and deployment

   Publish code without DB payloads. Publish the compact serving DB and its JSON
   report as a GitHub Release asset or external artifact. Configure deployment
   with `NCS_MCP_URL` for the app and `NCS_DB_PATH` for the MCP server.

6. Real-document benchmark

   Use `scripts/benchmark_alio_jd.py` to sample recent JOB-ALIO postings,
   download public job-description attachments, parse them with Kordoc, and
   record detail-classification extraction plus NCS_MCP unit/KSA coverage.

## Verification Checklist

- Kordoc parses at least one sample JD and returns detail classification
  candidates.
- The upload API rejects MCP production mode when human review is not confirmed.
- Confirmed terms such as `경영기획`, `총무`, and `사무행정` return official NCS
  competency units through NCS_MCP.
- `ncs_unit_detail` returns official KSA rows from the compact serving DB.
- `/api/jd/strategy/upload` returns `ncs_source` beginning with `ncs-mcp`.
- Generated interview questions include KSA evidence.
- `python -m py_compile` passes for changed Python modules.
- Git status contains no `.db`, `.xlsx`, `node_modules`, local logs, or virtual
  environment files intended for commit.

## Acceptance Criteria

- A user can upload a job description once and review the extracted detail
  classifications before generation.
- Confirmed detail classifications drive NCS/KSA lookup, not small categories.
- The app works without `NCS_DB.xlsx` in the repository.
- Official KSA is fetched through `NCS_MCP_URL`.
- Recent JOB-ALIO attachments can be benchmarked without committing downloaded
  files.
- The project is clean enough to publish on GitHub with a separate DB artifact
  policy.
