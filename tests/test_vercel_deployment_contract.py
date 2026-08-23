from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vercel_fastapi_entrypoint_and_duration_are_production_safe() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    function = config["functions"]["api/index.py"]

    assert function["maxDuration"] == 300
    assert "app/**" in function["includeFiles"]
    assert config["routes"] == [{"src": "/(.*)", "dest": "api/index.py"}]
    assert (ROOT / "api" / "index.py").read_text(encoding="utf-8").strip().endswith(
        '__all__ = ["app"]'
    )


def test_vercel_runtime_uses_ephemeral_sqlite_and_small_upload_boundary() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    environment = config["env"]

    assert environment["INTERVIEW_GENERATION_PROVIDER"] == "openrouter_api"
    assert environment["NCS_MCP_URL"] == "https://ncscope-ncs-mcp.vercel.app/api/mcp"
    assert environment["NCS_MCP_TIMEOUT_SEC"] == "5"
    assert environment["NCS_MCP_KSA_CONCURRENCY"] == "4"
    assert environment["KSA_RANK_MAX_UNITS"] == "5"
    assert environment["DATABASE_URL"] == "sqlite:////tmp/ncscope.db"
    assert environment["MAX_UPLOAD_MB"] == "4"
    assert environment["MAX_REQUEST_BODY_MB"] == "4"
    assert environment["OPENROUTER_ALLOW_SERVER_KEY"] == "true"
    assert environment["OPENROUTER_TIMEOUT_SEC"] == "8"
    assert environment["OPENROUTER_FALLBACK_TIMEOUT_SEC"] == "15"
    assert environment["OPENROUTER_RECOVERY_MODEL"] == "openai/gpt-oss-20b"
    assert environment["OPENAI_STRATEGY_CANDIDATE_MULTIPLIER"] == "1"
    assert environment["OPENAI_QUESTION_CANDIDATE_MULTIPLIER"] == "1"
    assert environment["OPENAI_QUESTION_VARIANT_ATTEMPTS"] == "1"
    assert environment["OPENROUTER_CANDIDATE_CONCURRENCY"] == "1"
    assert environment["INSTITUTION_MODEL_REQUESTS_PER_BATCH"] == "2"
    assert environment["INSTITUTION_QUALITY_RETRY_ENABLED"] == "true"
    assert environment["INSTITUTION_GENERATION_BATCH_SIZE"] == "5"
    assert environment["INSTITUTION_GENERATION_BATCH_CONCURRENCY"] == "1"
    assert environment["GENERATION_REQUEST_BUDGET_SEC"] == "285"
    assert environment["GENERATION_MAX_MAIN_QUESTIONS"] == "5"
    assert environment["OPENROUTER_PRIMARY_REASONING_EFFORT"] == "medium"
    assert environment["OPENROUTER_INVALID_OUTPUT_RETRY_REASONING_EFFORT"] == "medium"
    assert environment["OPENROUTER_INVALID_OUTPUT_RETRY_TIMEOUT_SEC"] == "15"
    assert not any("api_key" in key.casefold() for key in environment)


def test_vercel_upload_excludes_local_state_and_test_artifacts() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert {".env", "*.db", "tests", "reports", "tmp"}.issubset(ignored)
