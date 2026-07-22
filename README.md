# NCScope

NCScope is a Korean public-sector interview-question builder.

It lets a public institution upload a job notice and job description, review the
NCS detail classification extracted by Kordoc, then generate structured
interview questions grounded in official NCS competency-unit criteria and KSA
evidence served by NCS_MCP.

![NCScope home screen](docs/images/ncscope-home.png)

## What it does

- Parses job descriptions in PDF/HWP/HWPX/DOCX with Kordoc.
- Extracts candidate NCS detail classifications, not just small categories.
- Forces a human-in-the-loop review before official NCS lookup.
- Calls NCS_MCP through `NCS_MCP_URL` for official competency units,
  performance criteria, and KSA rows.
- Generates structured interview questions with main questions, follow-ups, and
  evaluation points.
- Keeps the full NCS source DB and `NCS_DB.xlsx` outside this GitHub repository.

## Target user flow

1. Open NCScope in a browser.
2. Optional: paste an OpenAI API key into the `OpenAI API key` field.
   - The key is not saved by the browser app.
   - It is sent only with the current generation request.
   - If left blank, the server uses its `OPENAI_API_KEY` environment variable.
3. Upload the job-description file.
4. NCScope runs Kordoc and shows extracted duties, qualifications, preferences,
   and NCS detail classifications.
5. The human reviewer checks or edits the detail classification values.
6. Click `추출 결과 검토·확정`.
7. Optional: upload the broader job notice and paste interview evaluation items.
8. Click `MCP KSA 기반 면접 질문 생성`.
9. Review:
   - pipeline diagnostics,
   - matched NCS competency units,
   - official KSA evidence,
   - structured interview questions.

## Architecture

```text
Public institution user
        |
        v
NCScope FastAPI app
        |
        |-- Kordoc Node bridge -> JD parsing
        |-- Human review gate -> confirmed NCS detail classification
        |-- NCS_MCP_URL ------> read-only NCS_MCP serving DB
        |-- OpenAI API -------> structured question generation
        v
Interview question pack
```

NCScope and NCS_MCP are deployed as separate processes.

| Component | Role |
| --- | --- |
| NCScope | Browser UI + FastAPI workflow orchestration |
| Kordoc | PDF/HWP/HWPX/DOCX parsing |
| NCS_MCP | Official NCS competency-unit/KSA lookup |
| Serving DB | Compact read-only SQLite DB, distributed separately |
| OpenAI API | Optional generation/reranking model backend |

## Repository and data policy

This repository intentionally does not include:

- the original 12.6 GB NCS source DB,
- `NCS_DB.xlsx`,
- local SQLite DBs,
- downloaded ALIO attachments,
- local logs,
- virtual environments,
- `node_modules`.

The compact serving DB is published separately as a release artifact or external
artifact and mounted into NCS_MCP.

Prepared artifact:

- Release tag: `ncscope-db-v0.1.0-20260723`
- DB asset: `ncs_interview_serving_release.db`
- Manifest asset: `ncs_interview_serving_release.json`
- DB SHA-256: `F9BB59B8853E8F69DC4698028EC347ED9BD74D26133FBCEB031B05FD90F89B23`

## Local setup

### 1. Install NCScope dependencies

```powershell
git clone https://github.com/koul777/NCScope.git
cd NCScope

pip install -r requirements.txt
npm ci
```

`npm ci` installs Kordoc for the Node parsing bridge.

### 2. Start NCS_MCP

Start NCS_MCP separately with the compact serving DB:

```powershell
$env:NCS_DB_PATH="C:\data\ncs_interview_serving_release.db"
$env:NCS_MCP_READ_ONLY="1"
python -m ncs_mcp.server --transport streamable-http --host 127.0.0.1 --port 8778
```

Required NCS_MCP tools:

- `ncs_search`
- `ncs_unit_detail`

### 3. Start NCScope

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
$env:MAX_UPLOAD_MB="30"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8015
```

Open:

```text
http://127.0.0.1:8015
```

You can also use:

```powershell
.\run_local.ps1
```

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `NCS_MCP_URL` | Yes | empty | Streamable HTTP endpoint for NCS_MCP |
| `OPENAI_API_KEY` | No | empty | Server-side fallback OpenAI key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | General model override |
| `OPENAI_STRATEGY_MODEL` | No | `gpt-4o-mini` | Interview strategy generation model |
| `DATABASE_URL` | No | `sqlite:///./ncscope.db` | Small app DB, not the NCS source DB |
| `MAX_UPLOAD_MB` | No | `30` | Upload limit |
| `KORDOC_OCR` | No | `true` | Enable Kordoc OCR path when available |
| `ENABLE_ADMIN_ENDPOINTS` | No | `false` | Enables admin endpoints only when explicitly needed |
| `ADMIN_TOKEN` | Conditional | empty | Required if admin endpoints are enabled |
| `ENABLE_LEGACY_NCS_API` | No | `false` | Re-enables legacy local/NCS API endpoints |

The interview MVP path requires `NCS_MCP_URL` and does not use bundled
`NCS_DB.xlsx` or local full-DB fallbacks.

## API overview

### Parse for review

```http
POST /api/jd/parse-review
```

Form:

- `jd_file`: PDF/HWP/HWPX/DOCX job description.

Returns:

- parsed markdown,
- duties,
- qualifications,
- preferences,
- `fields.ncs_detail_candidates`.

### Generate from uploaded JD

```http
POST /api/jd/strategy/upload
```

Form:

- `jd_file`: original job-description file.
- `notice_file`: optional job notice.
- `jd_review_json`: reviewed result from `/api/jd/parse-review`.
- `openai_api_key`: optional request-scoped OpenAI key.
- `duty_text`: optional duties override.
- `evaluation_text`: optional interview-evaluation criteria.

Required review gate:

```json
{
  "review_confirmed": true,
  "fields": {
    "ncs_detail_candidates": ["경영기획"]
  }
}
```

### Generate from manually selected NCS units

```http
POST /api/questions/generate-from-text
```

JSON:

```json
{
  "notice_text": "담당업무 ...",
  "evaluation_text": "평가항목 ...",
  "selected_ncs": [
    {
      "ncsClCd": "0201010103_22v2",
      "compeUnitName": "경영계획 수립"
    }
  ],
  "openai_api_key": "optional-request-key"
}
```

## Verification

Run the core checks:

```powershell
python -m py_compile app\main.py app\settings.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\question_generation.py app\services\kordoc_parser.py app\services\external_api.py scripts\benchmark_alio_jd.py
python -m pytest -q
```

Run a real-document ALIO benchmark:

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python scripts\benchmark_alio_jd.py --limit 5 --include-ksa
```

Latest benchmark report in this branch:

- `reports/alio_jd_benchmark_20260723_042758.md`
- `reports/alio_jd_benchmark_20260723_042758.csv`

Observed result:

- 5 recent JOB-ALIO postings inspected.
- 4 documents parsed.
- 2 documents had NCS detail candidates.
- 1 document had extracted detail candidates but no current MCP match.
- 1 ZIP attachment was marked out-of-scope for the MVP parser.

## Docker deployment

Build:

```powershell
docker build -t ncscope-app .
```

Run against local NCS_MCP:

```powershell
docker run --rm -p 8015:8000 `
  -e NCS_MCP_URL="http://host.docker.internal:8778/mcp" `
  -e MAX_UPLOAD_MB="30" `
  -e OPENAI_API_KEY="$env:OPENAI_API_KEY" `
  ncscope-app
```

The Docker image should contain the app only. The compact NCS serving DB belongs
to the separate NCS_MCP process.

See `DEPLOYMENT.md` for the full two-process deployment checklist.

## Known MVP boundaries

- ZIP attachments are not parsed directly; unzip and upload the contained
  PDF/HWP/HWPX/DOCX file.
- Some institutions use local labels that look like NCS detail classifications
  but do not exist in the current serving DB. Those require alias/coverage work
  in NCS_MCP.
- Docker CLI was not available in the local development environment during the
  first validation pass, so Dockerfile validation was syntax/dependency review
  plus `.dockerignore` hygiene checks.
- `npm audit` currently reports transitive vulnerabilities under the Kordoc
  dependency chain. Do not run `npm audit fix --force` blindly because it may
  change Kordoc major behavior; validate parser compatibility before upgrading.

## License and usage note

NCScope is an MVP implementation for public-sector structured interview
preparation. Before production use, confirm the institution's data-handling
rules, API-key handling policy, model logging policy, and official NCS source
licensing requirements.
