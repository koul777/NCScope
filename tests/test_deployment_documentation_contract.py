from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_current_readme_matches_public_generation_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    current = _section(readme, "## 현재 기본 생성 경로", "## 업데이트 내역")

    assert "https://ncscope.vercel.app" in current
    assert "Vercel Production" in current
    assert "주질문 1개" in current
    assert "2개 이상 계획" in current
    assert "각각 드롭다운에서 하나만 선택" in current
    assert "회피 이력에 누적" in current
    assert "285초 공통 예산" in current
    assert "최대 8초" in current
    assert "최대 15초" in current
    assert "`OPENROUTER_RECOVERY_MODEL`" in current
    assert "공식 NCS KSA `evidence_id`" in current
    assert "human_review_required" in current
    assert "서명된 검토 세션" in current


def test_deployment_profile_values_are_synchronized_with_vercel_config() -> None:
    deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    profile = _section(
        deployment,
        "Current public Vercel profile, excluding the secret value:",
        "`INTERVIEW_GENERATION_PROVIDER` accepts",
    )
    environment = json.loads(
        (ROOT / "vercel.json").read_text(encoding="utf-8")
    )["env"]
    documented_keys = (
        "NCSCOPE_LOAD_DOTENV",
        "NCS_MCP_URL",
        "INTERVIEW_GENERATION_PROVIDER",
        "OPENROUTER_ALLOW_SERVER_KEY",
        "DATABASE_URL",
        "OPENROUTER_PRIMARY_REASONING_EFFORT",
        "OPENROUTER_TIMEOUT_SEC",
        "OPENROUTER_HIGH_RISK_REASONING_EFFORT",
        "OPENROUTER_QUALITY_RETRY_REASONING_EFFORT",
        "OPENROUTER_HIGH_RISK_TIMEOUT_SEC",
        "OPENROUTER_MAX_REASONING_RESERVE",
        "OPENROUTER_RECOVERY_MODEL",
        "OPENROUTER_RECOVERY_JSON_MODE",
        "OPENROUTER_FALLBACK_REASONING_EFFORT",
        "OPENROUTER_FALLBACK_TIMEOUT_SEC",
        "OPENROUTER_INVALID_OUTPUT_RETRY_REASONING_EFFORT",
        "OPENROUTER_INVALID_OUTPUT_RETRY_TIMEOUT_SEC",
        "OPENAI_STRATEGY_CANDIDATE_MULTIPLIER",
        "OPENAI_QUESTION_CANDIDATE_MULTIPLIER",
        "OPENAI_QUESTION_VARIANT_ATTEMPTS",
        "OPENROUTER_CANDIDATE_CONCURRENCY",
        "INSTITUTION_MODEL_REQUESTS_PER_BATCH",
        "INSTITUTION_QUALITY_RETRY_ENABLED",
        "INSTITUTION_GENERATION_BATCH_SIZE",
        "INSTITUTION_GENERATION_BATCH_CONCURRENCY",
        "GENERATION_REQUEST_BUDGET_SEC",
        "GENERATION_MAX_MAIN_QUESTIONS",
        "NCS_MCP_TIMEOUT_SEC",
        "NCS_MCP_KSA_CONCURRENCY",
        "KSA_RANK_MAX_UNITS",
        "MAX_UPLOAD_MB",
        "MAX_REQUEST_BODY_MB",
        "OPENAI_HTTP_CURL_FALLBACK_ENABLED",
    )

    for key in documented_keys:
        assert f"{key}={environment[key]}" in profile

    assert '"recovery_model": "openai/gpt-oss-20b"' in deployment
    assert '"recovery_enabled": true' in deployment
    assert '"max_main_questions_per_request": 5' in deployment
    assert '"max_ncs_details_per_request": 1' in deployment
    assert '"max_interview_methods_per_request": 1' in deployment
    assert '"request_budget_sec": 285' in deployment


def test_vercel_notes_describe_capacity_budget_recovery_and_review_reuse() -> None:
    deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    notes = _section(deployment, "## 5. Vercel serverless notes", "## 6. Verification")
    compact_notes = " ".join(notes.split())

    assert "https://ncscope.vercel.app" in deployment
    assert "285-second application request budget" in compact_notes
    assert "`maxDuration` is 300 seconds" in compact_notes
    assert "ordinary Ox Alpha requests at most 8 seconds" in compact_notes
    assert "high-risk high-reasoning requests at most 15 seconds" in compact_notes
    assert "at most 15 seconds" in compact_notes
    assert "`openai/gpt-oss-20b`" in compact_notes
    assert "up to five main questions" in compact_notes
    assert "one interview-method dropdown" in compact_notes
    assert "one confirmed NCS-detail dropdown" in compact_notes
    assert "repeatedly generate another non-duplicate question" in compact_notes
    assert "more than five main questions return HTTP 422" in compact_notes
    assert "signed, hash-bound review" in compact_notes
    assert "provider-free server fallback" in compact_notes
    assert "exact official NCS KSA `evidence_id`" in compact_notes
    assert "reuses verified markdown" in compact_notes
    assert "at most one main question per request" not in compact_notes
    assert "shared 108-second" not in compact_notes
    assert "proxied request timeout is 120 seconds" not in compact_notes
    assert "Quality regeneration is disabled" not in compact_notes
