# NCScope

NCScope turns a Korean job description into a structured NCS interview pack.
It parses PDF/HWP/HWPX/DOCX files with Kordoc, asks a human reviewer to confirm
the NCS detail classification, then calls NCS_MCP for official competency units,
performance criteria, and KSA evidence.

The app does not bundle `NCS_DB.xlsx` or the full NCS source database. NCS data
is served by a separate NCS_MCP process through `NCS_MCP_URL`.

## Run Locally

Start NCS_MCP separately with its compact serving DB:

```powershell
$env:NCS_DB_PATH="C:\data\ncs_interview_serving.db"
python -m ncs_mcp.server --transport streamable-http --host 127.0.0.1 --port 8778
```

Start this app:

```powershell
pip install -r requirements.txt
npm ci
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8015
```

Or use:

```powershell
.\run_local.ps1
```

## Required Environment

- `NCS_MCP_URL`: Streamable HTTP endpoint for NCS_MCP, for example `http://127.0.0.1:8778/mcp`
- `OPENAI_API_KEY`: optional, used for AI interview generation and reranking
- `OPENAI_MODEL`: optional model override
- `DATABASE_URL`: optional app DB URL, default `sqlite:///./payroll2.db`
- `MAX_UPLOAD_MB`: optional upload limit, default `30`
- `ENABLE_ADMIN_ENDPOINTS`: optional, default `false`
- `ADMIN_TOKEN`: required when admin endpoints are enabled
- `ENABLE_LEGACY_NCS_API`: optional, default `false`

Legacy public/open-data keys are still supported for older admin endpoints.
The interview MVP path requires `NCS_MCP_URL` and does not use bundled XLSX/DB
fallbacks.

## Core Flow

1. `POST /api/jd/parse-review`
   - Upload a JD file.
   - Returns Kordoc-parsed fields and `ncs_detail_candidates` for human review.

2. `POST /api/jd/strategy/upload`
   - Send the original JD plus `jd_review_json`.
   - Requires `jd_review_json.review_confirmed=true`.
   - Requires at least one reviewed `fields.ncs_detail_candidates` value.
   - Calls NCS_MCP for official units and KSA.

3. `POST /api/questions/generate-from-text`
   - Generates questions from already selected NCS units.

Useful checks:

```powershell
python -m py_compile app\main.py app\settings.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\kordoc_parser.py scripts\benchmark_alio_jd.py
python -m pytest -q
```

## ALIO Benchmark

To test real public job-description attachments from JOB-ALIO:

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python scripts\benchmark_alio_jd.py --limit 5 --include-ksa
```

The script downloads recent `직무기술서` attachments into `.tmp/`, parses them
with Kordoc, extracts NCS detail classifications, and checks MCP unit/KSA
coverage. Reports are written to `reports/alio_jd_benchmark_*.md` and `.csv`.

## Docker

Build the app image:

```powershell
docker build -t ncscope-app .
```

Run it against a deployed or local NCS_MCP:

```powershell
docker run --rm -p 8015:8000 `
  -e NCS_MCP_URL="http://host.docker.internal:8778/mcp" `
  -e OPENAI_API_KEY="$env:OPENAI_API_KEY" `
  ncscope-app
```

The compact NCS serving DB belongs to NCS_MCP, not this app image. Publish that
DB as a GitHub Release asset or external artifact, then mount it into NCS_MCP
with `NCS_DB_PATH`.

## Data Policy

- Do not commit `.db`, `.xlsx`, local logs, virtual environments, or `node_modules`.
- Keep the full 12.6 GB NCS source DB outside Git.
- Publish the compact serving DB and its JSON manifest separately.
- Runtime KSA evidence should come from `NCS_MCP_URL`.
