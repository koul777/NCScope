# NCScope Deployment

This project deploys as two processes:

1. NCS_MCP serving process with the compact SQLite serving DB.
2. NCScope FastAPI app that calls NCS_MCP through `NCS_MCP_URL`.

The app repository must not contain `NCS_DB.xlsx`, raw SQLite DBs, downloaded
ALIO attachments, local logs, virtual environments, or `node_modules`.

## 1. Prepare NCS_MCP

Download the compact serving DB from the public GitHub Release:

- Release URL: `https://github.com/koul777/NCScope/releases/tag/ncscope-db-v0.1.0-20260723`
- Release tag: `ncscope-db-v0.1.0-20260723`
- DB asset: `ncs_interview_serving_release.db`
- Manifest asset: `ncs_interview_serving_release.json`
- DB SHA-256: `F9BB59B8853E8F69DC4698028EC347ED9BD74D26133FBCEB031B05FD90F89B23`

Set NCS_MCP environment:

```powershell
$env:NCS_DB_PATH="C:\data\ncs_interview_serving_release.db"
$env:NCS_MCP_READ_ONLY="1"
python -m ncs_mcp.server --transport streamable-http --host 0.0.0.0 --port 8778
```

Required health condition:

- `ncs_search` tool is available.
- `ncs_unit_detail` tool is available.
- `ncs_unit_detail` returns official KSA rows.

## 2. Run NCScope locally

```powershell
pip install -r requirements.txt
npm ci
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
$env:MAX_UPLOAD_MB="30"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

Open:

```text
http://127.0.0.1:8015
```

## 3. Docker deployment

Build:

```powershell
docker build -t ncscope-app .
```

Run:

```powershell
docker run --rm -p 8015:8000 `
  -e NCS_MCP_URL="http://host.docker.internal:8778/mcp" `
  -e MAX_UPLOAD_MB="30" `
  -e OPENAI_API_KEY="$env:OPENAI_API_KEY" `
  ncscope-app
```

Note: Docker CLI was not available in the local development environment, so the
Dockerfile was syntax-reviewed and dependency-pinned but not build-executed
locally.

## 4. Production environment flags

Recommended defaults:

```text
NCSCOPE_LOAD_DOTENV=false
NCS_MCP_URL=<required>
MAX_UPLOAD_MB=30
KORDOC_OCR=true
ENABLE_ADMIN_ENDPOINTS=false
ENABLE_LEGACY_NCS_API=false
AUTO_SYNC_PUBLIC_INST=false
AUTO_SYNC_NCS=false
```

Only enable admin/legacy endpoints for private maintenance deployments:

```text
ENABLE_ADMIN_ENDPOINTS=true
ADMIN_TOKEN=<strong token>
ENABLE_LEGACY_NCS_API=true
```

## 5. Verification

```powershell
python -m pytest -q
python -m py_compile app\main.py app\settings.py app\repository.py app\models.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\question_generation.py app\services\kordoc_parser.py app\services\external_api.py scripts\benchmark_alio_jd.py
```

Real-document benchmark:

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python scripts\benchmark_alio_jd.py --limit 10 --include-ksa
```

Expected MVP behavior:

- Uploading a JD returns reviewable Kordoc fields.
- Generation requires `jd_review_json.review_confirmed=true`.
- MCP lookup uses confirmed NCS detail classifications only.
- If exact detail-class matching fails, the app returns manual NCS unit suggestions instead of generating ungrounded questions.
- KSA rows have `factorSource=ncs-mcp` and `ksaStatus=official`.
- Legacy NCS API endpoints return 410 unless explicitly enabled.
- In production, pass secrets through the platform environment. Do not rely on
  automatic `.env` loading unless `NCSCOPE_LOAD_DOTENV=true` is intentionally
  set for a trusted single-process local environment.
