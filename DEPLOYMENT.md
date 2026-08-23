# NCScope Deployment

`NCS_MCP` is the local read-only NCS DB search server used by NCScope. NCScope
does not open the SQLite serving DB directly; it calls this server through
`NCS_MCP_URL`.

The deployment has two processes:

1. NCS_MCP with the compact read-only SQLite serving DB.
2. NCScope FastAPI, which calls NCS_MCP and the selected generation API.

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

## 2. Generation key mode

The Vercel production deployment uses a **server-managed OpenRouter key**.
Store `OPENROUTER_API_KEY` as a sensitive Production environment variable and
set `OPENROUTER_ALLOW_SERVER_KEY=true`. Visitors can then generate without
entering a key in the browser. The public UI and server pin the default path to
OpenRouter `stealth/ox-alpha`; ordinary requests use medium reasoning and
high-risk interview formats use high reasoning. The canonical public URL is
`https://ncscope.vercel.app`. The encrypted Production secret is stored by
Vercel, not read from the developer workstation, so the deployed service keeps
working when that workstation is offline.

The optional browser field remains a per-request override. A `sk-or-` prefix
selects OpenRouter and another `sk-` prefix selects OpenAI. An override is sent
as `generation_api_key` only for that request and is not persisted. Unknown
prefixes and provider/key-prefix mismatches fail with HTTP 400 before any
upstream request. Query-string keys are rejected, and neither server nor
request keys may appear in logs, traces, error reports, or responses.

`GET /api/generation-provider/status` never receives or verifies a key. A
healthy response has these fields:

```json
{
  "provider": "openrouter_api",
  "auth_mode": "server_env_api_key",
  "status": "configured",
  "available": true,
  "authenticated": false,
  "credential_configured": true,
  "credential_managed_by": "server_env",
  "requires_request_api_key": false,
  "server_key_enabled": true,
  "server_key_state": "configured",
  "recovery_model": "openai/gpt-oss-20b",
  "recovery_enabled": true,
  "reasoning_profiles": {
    "standard": "medium",
    "high_risk": "high",
    "quality_retry": "high"
  },
  "timeout_profiles_sec": {
    "standard": 8,
    "high_risk": 15
  },
  "generation_limits": {
    "max_main_questions_per_request": 5,
    "max_follow_up_questions_per_main": 5,
    "max_ncs_details_per_request": 1,
    "max_interview_methods_per_request": 1,
    "request_budget_sec": 285
  },
  "local_only": false
}
```

The server key never reaches the browser and is read only in the NCScope
server process before being forwarded to OpenRouter. An OpenRouter key can
only be sent to the server-pinned
`https://openrouter.ai/api/v1/chat/completions` endpoint and model
`stealth/ox-alpha`; clients cannot override that URL or model. OpenAI keys keep
the administrator-controlled `OPENAI_BASE_URL` and configured OpenAI model.
Neither credential mode embeds a key in the JavaScript bundle. Optional
request keys must not be saved to browser storage, files, DB, logs, traces,
error reports, or responses. See `SECURITY.md` for the shared-key cost and
abuse controls required for a public deployment.

Quality-first primary generation uses the pinned Ox Alpha model. Ordinary
one-question requests use the bounded public `reasoning_effort=medium` setting;
presentation, debate, in-basket, creative-problem-solving, and complex plans use
`high`. The pipeline normally builds a 3x candidate pool before
exact/semantic deduplication and quality selection across
job/unit, scenario, difficulty, KSA, and question-method coverage. OpenAI uses
up to three choices in one POST (`n=3`). Ox Alpha does not advertise `n`, so
OpenRouter uses up to three independent single-choice POSTs with bounded
parallelism instead. A failed OpenRouter variant does not discard the valid
variants; responses record requested and received variant counts. The primary
payload is pinned to `stealth/ox-alpha` and the method-aware reasoning profile, and
capability-safe JSON-object output. A deployment may configure one server-owned
`OPENROUTER_RECOVERY_MODEL`; that model is used only by bounded timeout or
invalid-output recovery and cannot be selected by a browser request.

Configure
`OPENAI_STRATEGY_CANDIDATE_MULTIPLIER` and
`OPENAI_QUESTION_CANDIDATE_MULTIPLIER` between `2` and `3`; setting either to
`1` is the explicit lower-cost escape hatch. Per-path reasoning can be
overridden with `OPENAI_STRATEGY_REASONING_EFFORT` or
`OPENAI_QUESTION_REASONING_EFFORT` for OpenAI. The OpenRouter primary remains
pinned to the Ox Alpha model; the public effort is method-aware for latency control. A
configured recovery model uses its native reasoning parameters and is still
routed only through the pinned OpenRouter origin.
Optional vision OCR and NCS AI reranking are separately bounded stages.

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

Open `http://127.0.0.1:8015`. To use a local server key, keep it in the ignored
`.env` file with `OPENROUTER_ALLOW_SERVER_KEY=true`; do not put the value in
source, shell commands, test fixtures, screenshots, tickets, or documentation.
The optional browser field can still supply a one-request OpenRouter (`sk-or-`)
or OpenAI (`sk-`) override.

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

Do not bake a key into the image. If a server-managed key is used, inject it
through the platform's encrypted runtime secret mechanism. Keep the FastAPI
listener on a private network. Except for
loopback development, expose NCScope only behind an institution-approved
reverse proxy or load balancer that forces HTTPS, redirects or rejects HTTP,
and uses an approved TLS policy.

Because the key is in the request body, disable body capture and sensitive
payload inspection/storage for `/api/questions/*` and
`/api/jd/strategy/upload` in reverse-proxy logs, WAF diagnostics, APM, tracing,
crash reporting, support dumps, and replay tools. Do not log `Authorization`
headers on either upstream connection. Limit production administrators who can
inspect process memory or proxy traffic.

Current public Vercel profile, excluding the secret value:

```text
NCSCOPE_LOAD_DOTENV=false
NCS_MCP_URL=https://ncscope-ncs-mcp.vercel.app/api/mcp
INTERVIEW_GENERATION_PROVIDER=openrouter_api
OPENROUTER_ALLOW_SERVER_KEY=true
DATABASE_URL=sqlite:////tmp/ncscope.db
OPENROUTER_PRIMARY_REASONING_EFFORT=medium
OPENROUTER_TIMEOUT_SEC=8
OPENROUTER_HIGH_RISK_REASONING_EFFORT=high
OPENROUTER_QUALITY_RETRY_REASONING_EFFORT=high
OPENROUTER_HIGH_RISK_TIMEOUT_SEC=15
OPENROUTER_MAX_REASONING_RESERVE=6000
OPENROUTER_RECOVERY_MODEL=openai/gpt-oss-20b
OPENROUTER_RECOVERY_JSON_MODE=true
OPENROUTER_FALLBACK_REASONING_EFFORT=medium
OPENROUTER_FALLBACK_TIMEOUT_SEC=15
OPENROUTER_INVALID_OUTPUT_RETRY_REASONING_EFFORT=medium
OPENROUTER_INVALID_OUTPUT_RETRY_TIMEOUT_SEC=15
OPENAI_STRATEGY_CANDIDATE_MULTIPLIER=1
OPENAI_QUESTION_CANDIDATE_MULTIPLIER=1
OPENAI_QUESTION_VARIANT_ATTEMPTS=1
OPENROUTER_CANDIDATE_CONCURRENCY=1
INSTITUTION_MODEL_REQUESTS_PER_BATCH=2
INSTITUTION_QUALITY_RETRY_ENABLED=true
INSTITUTION_GENERATION_BATCH_SIZE=5
INSTITUTION_GENERATION_BATCH_CONCURRENCY=1
GENERATION_REQUEST_BUDGET_SEC=285
GENERATION_MAX_MAIN_QUESTIONS=5
NCS_MCP_TIMEOUT_SEC=5
NCS_MCP_KSA_CONCURRENCY=4
KSA_RANK_MAX_UNITS=5
MAX_UPLOAD_MB=4
MAX_REQUEST_BODY_MB=4
OPENAI_HTTP_CURL_FALLBACK_ENABLED=false
```

`INTERVIEW_GENERATION_PROVIDER` accepts `openrouter_api` or `openai_api` as the
no-key/status default; the request key prefix is revalidated before generation.
Store `OPENROUTER_API_KEY` separately as an encrypted runtime secret. The
server key is read only when `OPENROUTER_ALLOW_SERVER_KEY=true` and must begin
with `sk-or-`.
`OPENAI_BASE_URL` names one approved OpenAI gateway, with no endpoint failover.
The OpenRouter URL and model are fixed in server code. Keep the curl fallback
off: some operating systems can expose its credentials in process execution
data.

Only enable admin/legacy endpoints in a private maintenance deployment:

```text
ENABLE_ADMIN_ENDPOINTS=true
ADMIN_TOKEN=<strong token>
ENABLE_LEGACY_NCS_API=true
```

## 5. Vercel serverless notes

`api/index.py` exposes the FastAPI app as one stateless function. Its Vercel
function `maxDuration` is 300 seconds, so the public profile uses a 285-second
application request budget. Every NCS and OpenRouter timeout is clamped to the
remaining shared budget, leaving 15 seconds for deterministic fallback and
response serialization.

The public profile gives ordinary Ox Alpha requests at most 8 seconds and
high-risk high-reasoning requests at most 15 seconds. The 6,000-token reasoning
reserve remains bounded by the shared request budget. If a request times out,
the same OpenRouter origin may call the administrator-configured
`OPENROUTER_RECOVERY_MODEL` (currently `openai/gpt-oss-20b`) for at most 15
seconds. If Ox Alpha returns unusable JSON, the single quality correction uses
the high reasoning profile within that same bounded retry budget. When a
recovery model is configured, its native reasoning parameters are used;
`OPENROUTER_FALLBACK_REASONING_EFFORT` is only the fallback policy for a
deployment without a recovery model.

If both external attempts fail, or their output fails a mandatory quality
gate, the two public institution-generation routes use a provider-free server
fallback. It may return a draft only when every locked plan slot maps to an
exact official NCS KSA `evidence_id`; the result is marked
`provider_fallback_used=true` and `human_review_required`. Provider exception
text and credentials are never reflected. Missing official evidence remains
fail-closed.

One candidate and one provider batch are used because public generation accepts
up to five main questions for one confirmed NCS detail and one interview method.
The five slots are sent as one batch so the provider is not called once per
question. Plans containing more than five main questions return HTTP 422
`question_plan_capacity_exceeded` before Kordoc, NCS, or model work. Additional
questions use the existing history/offset regeneration flow. A bounded quality
regeneration is enabled, but it does not receive a fresh wall-clock allowance:
it must fit inside the same 285-second request budget. Independent NCS KSA
lookups use bounded concurrency and inspect at most five units.

The browser exposes one interview-method dropdown and one confirmed NCS-detail
dropdown per request. Direct API attempts containing multiple methods or
multiple enabled detail plan items return HTTP 422 before NCS or model work.
Successful question text is carried into the existing avoid/history payload,
so a reviewer can repeatedly generate another non-duplicate question for the
same single selection.

The JD and notice parse-review endpoints both return signed, hash-bound review
sessions. The browser submits those sessions with the same filenames and bytes,
so generation reuses verified markdown instead of parsing either document a
second time.
This is an operational profile; local or longer-running deployments can restore
the normal 3x pool and 24,000-token reserve.

Vercel rejects request bodies above 4.5 MB. The deployment config therefore
sets `MAX_UPLOAD_MB=4` and `MAX_REQUEST_BODY_MB=4`; larger JD files must be
reduced before upload. `DATABASE_URL=sqlite:////tmp/ncscope.db` is ephemeral and
must not be treated as durable review-session or audit storage. Use an external
durable database if persistence across function instances is required.

Keep the NCS MCP endpoint aligned with `NCS_MCP_URL` in the deployment profile.
The target must pass MCP readiness with its NCS database loaded; if readiness
reports `database_missing` or otherwise fails, NCS-grounded generation is
blocked with an explicit service error. Add `OPENROUTER_API_KEY` as a sensitive
Vercel Production variable and keep `OPENROUTER_ALLOW_SERVER_KEY=true`. A new
deployment is required after changing either value. Never place the actual key
in `vercel.json` or any committed file.

## 6. Verification

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

- Upload parsing returns reviewable Kordoc fields; generation requires confirmed,
  hash-bound JD and notice review sessions in the browser workflow.
- Plans above five main questions are rejected before any external work; the UI
  displays the current total and disables submission at the same boundary.
- NCS lookup uses confirmed detail classifications and official KSA rows from NCS_MCP.
- Exact-match failure returns manual NCS suggestions instead of ungrounded questions.
- In server-managed mode, generation works without a browser key; BYOK requests
  may provide one non-empty `generation_api_key` in their body/form.
- `sk-or-` routes only to fixed OpenRouter Ox Alpha, another `sk-` routes only to the configured OpenAI endpoint, and a provider/prefix mismatch is rejected before networking.
- Provider status reports `configured` with `auth_mode=server_env_api_key` when
  the Vercel secret is present, and never authenticates, stores, or echoes it.
- Provider generation or final quality-gate failure returns a sanitized provider-specific error; an empty usable question list is never returned as HTTP 200.
- Public generation accepts only genuine `openai_api` or `openrouter_api` question output. It never substitutes a deterministic/template result or another provider.
- Responses, application/audit logs, and persistent storage contain no API key.
- Generation metadata reports the selected provider/model plus bounded requested/received candidate counts without request text or credentials.
