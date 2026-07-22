# NCScope Completion Audit - 2026-07-23

This audit checks the current NCScope implementation against the active MVP
goal:

> Kordoc으로 직무기술서(PDF/HWP/HWPX/DOCX)를 파싱하고 Human-in-the-loop 검토를 거쳐 세분류를 확정한다. 확정된 세분류를 기준으로 NCS_MCP의 경량 read-only DB에서 공식 능력단위·수행준거·KSA를 조회하고, 이를 활용해 구조화된 NCS 면접 질문을 생성하는 MVP를 완성한다. 원본 12.6GB DB와 NCS_DB.xlsx는 GitHub에 포함하지 않고, 약 117MB serving DB를 Release Artifact 또는 외부 스토리지로 분리하여 배포한다. payroll2는 NCS_MCP_URL만 사용하도록 정리하고, 전체 흐름을 통합 테스트한 뒤 GitHub 공개 및 배포 가능한 상태로 만든다.

## Repository evidence

- Public repository: `https://github.com/koul777/NCScope`
- Default branch: `main`
- Current pushed commit before this audit file: `461fbeb` (`NCScope enforces MCP-only KSA lookup`)
- Latest CI run checked: `29957279549`, status `success`
- DB release: `https://github.com/koul777/NCScope/releases/tag/ncscope-db-v0.1.0-20260723`
- Release assets:
  - `ncs_interview_serving_release.db` — 117,108,736 bytes
  - `ncs_interview_serving_release.json` — 476 bytes

## Requirement-by-requirement audit

| Requirement | Evidence | Status |
| --- | --- | --- |
| Parse job descriptions with Kordoc for PDF/HWP/HWPX/DOCX | `app/services/kordoc_parser.py`, `scripts/kordoc_parse.mjs`, README install path with `npm ci`; upload parser accepts PDF/HWP/HWPX/DOCX plus TXT/image/ZIP. | Achieved |
| Extract NCS detail classification, not only small category | `structure_job_description()` extracts `fields.ncs_detail_candidates`; tests cover HTML table, pipe table, punctuation cleanup, exact detail-vs-small-category MCP behavior. | Achieved |
| Human-in-the-loop review before generation | UI review panel requires confirmation; `/api/jd/strategy/upload` rejects unconfirmed or truthy-string confirmation; server-created `review_session_id` is required. | Achieved |
| Prevent client-only fake confirmation | `_create_review_session()` and `_validate_review_session()` bind review to uploaded file SHA-256; test `test_mcp_only_requires_server_review_session`. | Achieved |
| Use confirmed detail classifications to query NCS_MCP | Upload generation calls `search_units_by_detail()` only after reviewed detail terms exist; empty reviewed details return 422. | Achieved |
| Use compact read-only NCS_MCP serving DB for official unit/KSA lookup | NCScope uses `NCS_MCP_URL`; release DB is separate artifact; NCS_MCP docs require `NCS_MCP_READ_ONLY=1`; live smoke returned `ncs_source=ncs-mcp+ai-rerank`. | Achieved |
| Official KSA only; no silent XLSX/HRDK/definition fallback | `fetch_ncs_ksa_by_units()` now raises without `NCS_MCP_URL` and returns only `get_ksa_by_units()` rows; regression test `test_ksa_lookup_requires_ncs_mcp_url`. | Achieved |
| Structured NCS interview questions | Live smoke generated 10 questions from JD/notice/reviewed detail; response includes `strategy.interview_questions`. | Achieved |
| Question-level KSA evidence | `_attach_ksa_evidence_to_strategy()` adds `ksa_evidence`; live smoke confirmed minimum 2 evidence rows per generated question. | Achieved |
| No raw 12.6GB DB or `NCS_DB.xlsx` in GitHub app repo | `git ls-files` check found no tracked `.db`, `.db-wal`, `.db-shm`, `.xlsx`, `node_modules`, `.env`, or serving DB asset. `.gitignore` excludes them. | Achieved |
| Publish compact ~117MB DB outside normal Git tree | GitHub Release `ncscope-db-v0.1.0-20260723` is public and contains 117,108,736-byte DB asset plus JSON manifest. | Achieved |
| GitHub 공개 | `gh repo view` verified `visibility=PUBLIC`, `nameWithOwner=koul777/NCScope`. | Achieved |
| Deployable state | `Dockerfile`, `.dockerignore`, `DEPLOYMENT.md`, `.env.example`, and CI are present. Docker CLI was unavailable locally, so Docker build is documented but not locally build-executed. | Achieved for deployable packaging |
| Whole-flow integration tested | `python -m pytest -q`: 168 passed; `py_compile`: passed; GitHub Actions CI: success; live MCP/OpenAI smoke succeeded. | Achieved |
| Real ALIO JD benchmark | `reports/alio_jd_benchmark_20260723_053926.md/csv`: 10 attempts, 9 parsed documents, 7 with detail candidates, ZIPs parsed in memory. | Achieved |
| Original UI without official NCS asset copying | UI screenshot and source use original layout/colors; no official logo/image assets; `NOTICE.md` states official-service separation. | Achieved |
| API key handling | UI accepts request-scoped key; server does not echo it; `.env` auto-load disabled by default; `SECURITY.md` documents rotation/handling. | Achieved |

## Current verification commands

```powershell
python -m py_compile app\main.py app\settings.py app\repository.py app\models.py app\services\jd_strategy.py app\services\ncs_mcp_client.py app\services\question_generation.py app\services\kordoc_parser.py app\services\external_api.py scripts\benchmark_alio_jd.py
python -m pytest -q
gh run list --repo koul777/NCScope --workflow CI --limit 3
```

Observed result:

- `py_compile`: passed
- `pytest`: 168 passed, 2 FastAPI deprecation warnings
- GitHub Actions CI: success

## Remaining operational notes

- A live public service URL has not been created in this workspace. The goal
  condition verified here is GitHub public + deployment-ready packaging.
- Actual production deployment still requires choosing a host and providing:
  `NCS_MCP_URL`, `OPENAI_API_KEY`, and a running NCS_MCP process mounted to the
  release DB.
- The local `.env` is ignored and not committed. Any API key that existed in the
  shared workspace should still be rotated before public operation.
