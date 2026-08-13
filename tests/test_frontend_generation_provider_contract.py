from __future__ import annotations

import re
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _page() -> tuple[str, str]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == 1
    return html, scripts[0]


def test_request_scoped_api_card_has_password_input_and_security_warning() -> None:
    html, script = _page()

    assert 'id="generationProviderCard"' in html
    assert '<span>요청 단위 API</span>' in html
    assert 'id="generationProviderName">OpenAI API · 요청 단위 키<' in html
    assert 'id="openaiApiKey"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html
    assert 'spellcheck="false"' in html
    assert 'id="btnClearOpenAiApiKey"' in html
    assert 'id="apiKeySecurityWarning"' in html
    assert 'role="note"' in html
    assert "HTTPS 연결에서만 입력하세요." in html
    assert "공용 PC·공용 브라우저에서는 API 키를 입력하지 말고" in html
    assert "현재 페이지 탭 메모리에만 유지" in html
    assert 'aria-busy="true"' in html
    assert 'id="generationProviderError"' in html
    assert 'role="alert"' in html
    assert 'id="btnRefreshGenerationProvider"' in html
    assert '<strong>1. OpenAI API 키</strong>' in html
    assert "'/api/generation-provider/status?provider=openai_api'" in script
    assert "btnRefreshGenerationProvider.addEventListener('click', loadGenerationProviderStatus)" in script
    assert "loadGenerationProviderStatus();" in script


def test_provider_is_fixed_to_openai_request_scoped_status_contract() -> None:
    html, script = _page()

    assert 'id="generationProviderSelect"' not in html
    assert '<option value="codex_cli"' not in html
    assert '<option value="claude_code"' not in html
    assert 'id="generationProviderLogin"' not in html
    assert 'id="generationProviderLoginCommand"' not in html
    assert "const INSTITUTION_GENERATION_PROVIDER = 'openai_api'" in script
    assert "function requestScopedApiStatusValid(status=generationProviderStatus)" in script
    assert "status.provider === INSTITUTION_GENERATION_PROVIDER" in script
    assert "status.status === 'key_required'" in script
    assert "status.available === true" in script
    assert "status.authenticated === false" in script
    assert "status.credential_configured === false" in script
    assert "status.auth_mode === 'request_scoped_api_key'" in script
    assert "status.credential_managed_by === 'request'" in script
    assert "status.requires_request_api_key === true" in script
    assert "status.local_only === false" in script
    assert "const requestId = ++generationProviderRequestId" in script
    assert "requestId !== generationProviderRequestId" in script


def test_api_key_is_kept_only_in_page_memory_and_can_be_cleared() -> None:
    _, script = _page()

    assert "function currentOpenAiApiKey()" in script
    assert "String(openaiApiKey.value || '').trim()" in script
    assert "openaiApiKey.addEventListener('input'" in script
    assert "btnClearOpenAiApiKey.addEventListener('click'" in script
    assert "window.addEventListener('pagehide'" in script
    assert script.count("openaiApiKey.value = ''") == 2
    assert "function apiKeyTransportIsSecure()" in script
    assert "window.isSecureContext === true" in script
    assert "openaiApiKey.disabled = !secure" in script
    assert "API 키 보호를 위해 HTTPS 연결에서만" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "Authorization" not in script


def test_generation_requires_valid_status_and_nonempty_request_key() -> None:
    _, script = _page()

    assert "function requestScopedApiReady(status=generationProviderStatus)" in script
    assert "requestScopedApiStatusValid(status)" in script
    assert "Boolean(currentOpenAiApiKey())" in script
    assert "function generationProviderBlockReason()" in script
    assert "if (generationProviderLoading)" in script
    assert "if (generationProviderLoadError || !generationProviderStatus)" in script
    assert "generationProviderStatus.provider !== INSTITUTION_GENERATION_PROVIDER" in script
    assert "if (!requestScopedApiStatusValid())" in script
    assert "if (!currentOpenAiApiKey())" in script
    assert "OpenAI API 키 입력 후 생성" in script
    assert "const providerBlock = generationProviderBlockReason()" in script
    assert "setGenerationProviderBadge('키 입력 필요', 'warn')" in script
    assert "setGenerationProviderBadge('요청 준비됨', 'good')" in script
    assert script.count("syncGenerationButtonState();") >= 5


def test_upload_and_json_generation_send_request_key_with_fixed_provider() -> None:
    _, script = _page()

    assert "const requestOpenAiApiKey = currentOpenAiApiKey()" in script
    assert "fd.append('generation_provider', INSTITUTION_GENERATION_PROVIDER)" in script
    assert "fd.append('openai_api_key', requestOpenAiApiKey)" in script
    assert "generation_provider: INSTITUTION_GENERATION_PROVIDER" in script
    assert "openai_api_key: requestOpenAiApiKey" in script
    assert script.count("generation_provider") == 2
    assert script.count("openai_api_key") == 2
    assert script.count("generationProvider: INSTITUTION_GENERATION_PROVIDER") == 2

    upload_history = script.split("const uploadGenerationContext = JSON.stringify({", 1)[1].split(
        "prepareQuestionHistory", 1
    )[0]
    manual_history = script.split("const manualGenerationContext = JSON.stringify({", 1)[1].split(
        "prepareQuestionHistory", 1
    )[0]
    assert "openai_api_key" not in upload_history
    assert "requestOpenAiApiKey" not in upload_history
    assert "openai_api_key" not in manual_history
    assert "requestOpenAiApiKey" not in manual_history


def test_result_metadata_uses_result_provenance_and_supports_api_model_sources() -> None:
    _, script = _page()

    question_generation = script.index("data.question_generation")
    provider_branch = script.index(
        "Object.hasOwn(GENERATION_PROVIDER_LABELS, resultProvider)",
        question_generation,
    )

    assert question_generation < provider_branch
    assert "resultGeneration.provider || generationProviderStatus" not in script
    assert "resultGeneration.provider_label || generationProviderStatus" not in script
    assert "OpenAI key:" not in script
    assert "openai_api: 'OpenAI API · 요청 단위 키'" in script
    assert "model: 'OpenAI API 모델'" in script
    assert "openai_api: 'OpenAI API 초안 유지'" in script
    assert "model: 'OpenAI API 초안 유지'" in script
    # Historical results remain understandable without making those providers selectable.
    assert "codex_cli: 'Codex 초안 유지'" in script
    assert "claude_code: 'Claude Code 초안 유지'" in script


def test_quality_failures_are_explained_in_panel_language() -> None:
    _, script = _page()

    assert "field_realism: '현장 면접 문장·답변연동성 미충족'" in script
    assert "ksa_measurement_task: '선택한 KSA를 행동으로 측정하지 못함'" in script
    assert "precision_grounding: '제시되지 않은 수치·조항 회상 요구'" in script
    assert "candidate_surface_safe: '후보자 화면에 NCS 내부 명칭 노출'" in script
    assert "candidate_visible_instruction_injection: '외부 문서의 지시문이 질문에 노출됨'" in script
    assert "question_evidence_assignment_failed: '모델 KSA 근거 ID가 서버 배정과 불일치'" in script
    assert "evaluation_elicitation_alignment: '질문하지 않은 평가기준이 포함됨'" in script
    assert ".filter(Boolean).map(issueLabel).join(', ')" in script
    assert "s.model_quality_retry && typeof s.model_quality_retry === 'object'" in script
    assert "모델 생성 요청 ${Number(retryAudit.provider_generation_request_count)}/" in script
