# NCScope Deployment

`NCS_MCP` is the local read-only NCS DB search server used by NCScope. NCScope
does not open the SQLite serving DB directly; it calls this server through
`NCS_MCP_URL`.

The deployment has two processes:

1. NCS_MCP with the compact read-only SQLite serving DB.
2. NCScope FastAPI, which calls NCS_MCP and the approved OpenAI API gateway.

Large data files and runtime artifacts belong outside the app repository, for
example in Release assets or deployment storage.

## 1. Prepare NCS_MCP

Download the compact serving DB from the public GitHub Release:

- Release URL: `https://github.com/koul777/NCScope/releases/tag/ncscope-db-v0.1.0-20260723`
- Release tag: `ncscope-db-v0.1.0-20260723`
- DB asset: `ncs_interview_serving_release.db`
- Manifest asset: `ncs_interview_serving_release.json`
- DB SHA-256: `F9BB59B8853E8F69DC4698028EC347ED9BD74D26133FBCEB031B05FD90F89B23`

```powershell
$env:NCS_DB_PATH="C:\data\ncs_interview_serving_release.db"
$env:NCS_MCP_READ_ONLY="1"
python -m ncs_mcp.server --transport streamable-http --host 0.0.0.0 --port 8778
```

Required health condition:

- `ncs_search` is available.
- `ncs_unit_detail` is available.
- `ncs_unit_detail` returns official KSA rows.

## 2. Request-scoped OpenAI key contract

The public UI and API are fixed to `openai_api`. Codex/Claude CLI providers,
personal subscription login, a provider selector, and provider fallback are not
available.

Do not configure `OPENAI_API_KEY` in `.env`, the server process, the container,
or a deployment secret manager. It is not a supported fallback. The user enters
an OpenAI API key in the browser, and every generation request sends that key as
the JSON body or multipart form field `openai_api_key`. Query-string keys are
rejected. If a request omits the key, the server returns HTTP 400
`openai_api_key_required`, even if a legacy server environment value exists.

`GET /api/generation-provider/status` never receives or verifies a key. A
healthy contract response has these fields:

```json
{
  "provider": "openai_api",
  "auth_mode": "request_scoped_api_key",
  "status": "key_required",
  "available": true,
  "authenticated": false,
  "credential_configured": false,
  "credential_managed_by": "request",
  "requires_request_api_key": true,
  "local_only": false
}
```

This is request-scoped BYOK through NCScope, not a direct browser-to-OpenAI
connection: the key is temporarily present in the browser, TLS request, and
NCScope server memory before being forwarded to the approved
`OPENAI_BASE_URL`. It is not embedded in the JavaScript bundle and must not be
saved to browser storage, files, DB, logs, traces, error reports, or responses.
It is also not the same as OpenAI Enterprise Key Management. OpenAI's
[API key safety guidance](https://developers.openai.com/api/reference/overview#authentication)
recommends keeping API keys out of client-side browser/app code and loading
them on the server. An institution choosing this BYOK design must explicitly
accept the additional browser and intermediary-server exposure risk described
in `SECURITY.md`.

Primary question generation permits at most three semantic generation POSTs:
the initial request, one slim recovery request, and one whole-set quality
regeneration. Each semantic request has a one-POST transport limit. The
response records the actual count and limit under `strategy.model_quality_retry`.
Auxiliary generation permits at most three distinct variants with one POST
each. Optional vision OCR and NCS AI reranking are separately bounded stages.

## 3. Run locally

Loopback HTTP is for local development only:

```powershell
pip install -r requirements.txt
npm ci
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
$env:MAX_UPLOAD_MB="30"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015`, enter a limited-scope OpenAI project key in the
generation screen, and submit it only through that screen. Do not put a real
key in shell commands, test fixtures, screenshots, tickets, or documentation.

You can inspect the non-authenticating provider contract without a key:

```powershell
Invoke-RestMethod http://127.0.0.1:8015/api/generation-provider/status
```

## 4. Docker and production HTTPS

Build and run the internal app listener:

```powershell
docker build -t ncscope-app .
docker run --rm -p 127.0.0.1:8015:8000 `
  -e NCS_MCP_URL="http://host.docker.internal:8778/mcp" `
  -e OPENAI_BASE_URL="https://api.openai.com/v1" `
  -e MAX_UPLOAD_MB="30" `
  ncscope-app
```

Do not pass `-e OPENAI_API_KEY` and do not bake a key into the image. Keep the
FastAPI listener on a private network. Except for loopback development, expose
NCScope only behind an institution-approved reverse proxy or load balancer that
forces HTTPS, redirects or rejects HTTP, and uses an approved TLS policy.

Because the key is in the request body, disable body capture and sensitive
payload inspection/storage for `/api/questions/*` and
`/api/jd/strategy/upload` in reverse-proxy logs, WAF diagnostics, APM, tracing,
crash reporting, support dumps, and replay tools. Do not log `Authorization`
headers on the upstream OpenAI connection. Limit production administrators who
can inspect process memory or proxy traffic.

Recommended non-secret settings:

```text
NCSCOPE_LOAD_DOTENV=false
NCS_MCP_URL=<required>
INTERVIEW_GENERATION_PROVIDER=openai_api
OPENAI_BASE_URL=https://api.openai.com/v1
MAX_UPLOAD_MB=30
MAX_REQUEST_BODY_MB=62
RATE_LIMIT_ENABLED=true
RATE_LIMIT_WINDOW_SEC=60
RATE_LIMIT_REQUESTS_PER_WINDOW=120
GENERATION_RATE_LIMIT_REQUESTS_PER_WINDOW=30
GENERATION_MAX_CONCURRENCY=8
KORDOC_OCR=true
OPENAI_HTTP_CURL_FALLBACK_ENABLED=false
ENABLE_ADMIN_ENDPOINTS=false
ENABLE_LEGACY_NCS_API=false
AUTO_SYNC_PUBLIC_INST=false
AUTO_SYNC_NCS=false
```

`INTERVIEW_GENERATION_PROVIDER` must remain `openai_api`; other values are
rejected at the public boundary. `OPENAI_BASE_URL` names one approved gateway,
with no alternate provider or endpoint failover. Keep the curl fallback off:
some operating systems can expose its credentials in process execution data.

Only enable admin/legacy endpoints in a private maintenance deployment:

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

Expected behavior:

- Upload parsing returns reviewable Kordoc fields; generation requires `jd_review_json.review_confirmed=true`.
- NCS lookup uses confirmed detail classifications and official KSA rows from NCS_MCP.
- Exact-match failure returns manual NCS suggestions instead of ungrounded questions.
- Every generation request requires a non-empty `openai_api_key` in its body/form; a server environment key is ignored.
- Provider status reports `key_required` and does not authenticate, store, or echo a key.
- OpenAI generation or final quality-gate failure returns sanitized HTTP 502 `openai_api_generation_failed`.
- Public generation accepts only genuine `openai_api` question output. It never substitutes a deterministic/template result or another provider.
- Responses, application/audit logs, and persistent storage contain no API key.
- `strategy.model_quality_retry` reports the bounded generation-request count without request text or credentials.
