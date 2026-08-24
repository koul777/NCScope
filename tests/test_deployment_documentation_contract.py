from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_matches_openai_byok_and_ai_review_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "https://ncscope.vercel.app" in readme
    assert "OpenAI API 키" in readme
    assert "openai_api" in readme
    assert "공식 `https://api.openai.com/v1`" in readme
    assert "최대 5개" in readme
    assert "285초" in readme
    assert "독립 AI 품질검수" in readme
    assert "KSA 연결·측정성, 직무 근거, 면접기법 적합성, 평가 관찰성은 3점 이상" in readme
    assert "한국어 자연스러움·꼬리질문 연계·비기계적 표현은 2점 이상" in readme
    assert "전체 평균으로 추가 탈락시키지 않으며" in readme
    assert "ncs_interviewer_guide_2020.json" in readme
    assert "server_ksa_fallback" in readme
    assert "template_fallback" in readme
    assert "질문을 반환하지 않습니다" in readme
    assert "OpenRouter, 서버 공용키" in readme
    assert "KORDOC_BRIDGE_ED25519_PRIVATE_KEY" in readme
    assert "120초 유효 요청 서명" in readme


def test_deployment_profile_values_are_synchronized_with_vercel_config() -> None:
    deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    environment = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["env"]
    documented_keys = (
        "NCSCOPE_LOAD_DOTENV",
        "NCS_MCP_URL",
        "INTERVIEW_GENERATION_PROVIDER",
        "DATABASE_URL",
        "MAX_UPLOAD_MB",
        "MAX_REQUEST_BODY_MB",
        "KORDOC_OFFLINE",
        "NCS_MCP_TIMEOUT_SEC",
        "NCS_MCP_KSA_CONCURRENCY",
        "KSA_RANK_MAX_UNITS",
        "OPENAI_HTTP_CURL_FALLBACK_ENABLED",
        "OPENAI_NET_CHECK_ENABLED",
        "OPENAI_RERANK_MODEL",
        "OPENAI_STRATEGY_MODEL",
        "OPENAI_STRATEGY_RETRY_MODEL",
        "OPENAI_QUESTION_MODEL",
        "OPENAI_QUALITY_REGENERATION_MODEL",
        "OPENAI_STRATEGY_CANDIDATE_MULTIPLIER",
        "OPENAI_QUESTION_CANDIDATE_MULTIPLIER",
        "OPENAI_QUESTION_VARIANT_ATTEMPTS",
        "INSTITUTION_MODEL_REQUESTS_PER_BATCH",
        "INSTITUTION_QUALITY_RETRY_ENABLED",
        "INSTITUTION_GENERATION_BATCH_SIZE",
        "INSTITUTION_GENERATION_BATCH_CONCURRENCY",
        "GENERATION_REQUEST_BUDGET_SEC",
        "GENERATION_MAX_MAIN_QUESTIONS",
        "OPENAI_QUALITY_REVIEW_MODEL",
        "OPENAI_QUALITY_REVIEW_REASONING_EFFORT",
        "AI_QUALITY_REVIEW_TIMEOUT_SEC",
    )

    for key in documented_keys:
        assert f"{key}={environment[key]}" in deployment

    assert not any(key.startswith("OPENROUTER_") for key in environment)
    assert "KORDOC_BRIDGE_ED25519_PRIVATE_KEY" in deployment
    assert "ED25519_PUBLIC_KEY_RAW" in deployment
    assert "KORDOC_BRIDGE_ED25519_PRIVATE_KEY" not in environment


def test_security_doc_forbids_secret_persistence_and_provider_fallback() -> None:
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in security
    assert "대체 자격증명으로 사용하지 않습니다" in security
    assert "localStorage" in security
    assert "query string" in security
    assert "https://api.openai.com/v1" in security
    assert "제3자 OpenAI 호환 endpoint" in security
    assert "질문 원문, API 키, provider 예외 문자열" in security
    assert "Ed25519 개인키" in security
