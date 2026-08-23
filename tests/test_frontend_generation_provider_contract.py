from __future__ import annotations

import re
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _page() -> tuple[str, str]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == 1
    return html, scripts[0]


def test_server_managed_api_card_keeps_an_optional_accessible_override_field() -> None:
    html, script = _page()

    assert 'id="generationProviderCard"' in html
    assert 'id="generationProviderName"' in html
    assert 'id="generationProviderSelect"' not in html
    assert '<label id="generationApiKeyLabel" for="generationApiKey">개인 API 키 (선택)</label>' in html
    assert 'id="generationApiKey"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html
    assert 'spellcheck="false"' in html
    assert '입력하지 않아도 됩니다 · 개인 키로 바꾸려면 입력' in html
    assert 'id="btnToggleApiKey"' in html
    assert 'aria-pressed="false"' in html
    assert 'id="btnPasteApiKey"' in html
    assert 'id="btnClearApiKey"' in html
    assert 'id="openRouterKeyHelp"' in html
    assert 'href="https://openrouter.ai/settings/keys"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert "서버 키를 기본으로 사용합니다." in html
    assert "개인 키로 바꾸려는 경우에만" in html
    assert "<code>sk-or-…</code>" in html
    assert "openRouterKeyHelp generationProviderKeyNote" in html
    assert 'id="apiKeySecurityWarning"' in html
    assert 'role="note"' in html
    assert 'aria-busy="true"' in html
    assert 'id="generationProviderError"' in html
    assert 'role="alert"' in html
    assert 'id="btnRefreshGenerationProvider"' in html
    assert 'id="generationProviderModelNote"' in html
    assert 'OpenRouter · Ox Alpha Max 우선 · 저비용 모델·서버 KSA 자동복구' in html
    assert "btnRefreshGenerationProvider.addEventListener('click', loadGenerationProviderStatus)" in script
    assert "scheduleGenerationProviderDetection(true);" in script
    assert "Vercel Production 서버 키" in html
    assert "Vercel 환경변수의 OpenRouter API 키" in script
    assert "serverEnvApiStatusValid" in script
    assert "server_env_api_key" in script


def test_provider_is_auto_detected_from_one_key_without_a_selector() -> None:
    html, script = _page()

    assert 'id="generationProviderSelect"' not in html
    assert "function detectedGenerationProvider(apiKey=currentGenerationApiKey())" in script
    openrouter_branch = script.index("key.startsWith('sk-or-')")
    openai_branch = script.index("key.startsWith('sk-')")
    assert openrouter_branch < openai_branch
    assert "if (key.startsWith('sk-or-')) return 'openrouter_api'" in script
    assert "if (key.startsWith('sk-')) return 'openai_api'" in script
    assert "modelNote: 'OpenRouter · Ox Alpha Max 우선 · 저비용 모델·서버 KSA 자동복구'" in script
    assert "s.provider_fallback_used === true" in script
    assert "s.generation_mode === 'server_ksa_fallback'" in script
    status_url = "`/api/generation-provider/status?provider=${encodeURIComponent(requestedProvider)}`"
    assert status_url in script
    status_signature = (
        "function requestScopedApiStatusValid("
        "status=generationProviderStatus, provider=detectedGenerationProvider())"
    )
    assert status_signature in script
    assert "status.provider === provider" in script
    assert "status.status === 'key_required'" in script
    assert "status.available === true" in script
    assert "status.authenticated === false" in script
    assert "status.credential_configured === false" in script
    assert "status.auth_mode === 'request_scoped_api_key'" in script
    assert "status.credential_managed_by === 'request'" in script
    assert "status.requires_request_api_key === true" in script
    assert "status.local_only === false" in script
    assert "return requestScoped || serverEnvApiStatusValid(status, effectiveProvider)" in script
    assert "status.auth_mode === 'server_env_api_key'" in script
    assert "status.credential_managed_by === 'server_env'" in script
    assert "status.requires_request_api_key === false" in script
    assert "status.credential_configured === true" in script
    assert "const requestId = ++generationProviderRequestId" in script
    assert "requestId !== generationProviderRequestId" in script


def test_api_key_is_kept_only_in_form_memory_and_has_safe_controls() -> None:
    _, script = _page()

    assert "function currentGenerationApiKey()" in script
    assert "String(generationApiKey.value || '').trim()" in script
    assert "generationApiKey.addEventListener('input'" in script
    assert "btnToggleApiKey.addEventListener('click'" in script
    assert "navigator.clipboard.readText" in script
    assert "btnClearApiKey.addEventListener('click'" in script
    assert "window.addEventListener('pagehide'" in script
    assert script.count("generationApiKey.value = ''") == 2
    assert "setApiKeyVisibility(false)" in script
    assert "function apiKeyTransportIsSecure()" in script
    assert "window.isSecureContext === true" in script
    assert "generationApiKey.disabled = !secure" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script
    assert "Authorization" not in script


def test_generation_accepts_server_key_or_valid_request_override_and_focuses_errors() -> None:
    _, script = _page()

    assert "function requestScopedApiReady(status=generationProviderStatus)" in script
    assert "requestScopedApiStatusValid(status)" in script
    assert "Boolean(currentGenerationApiKey())" in script
    assert "if (serverEnvApiStatusValid(status)) return true" in script
    assert "function generationProviderBlockReason()" in script
    assert "if (!apiKey && !serverConfigured)" in script
    assert "const serverConfigured = serverEnvApiStatusValid()" in script
    assert "if (!detectedProvider)" in script
    assert "if (generationProviderLoading)" in script
    assert "if (generationProviderLoadError || !generationProviderStatus)" in script
    assert "generationProviderStatus.provider !== detectedProvider" in script
    assert "if (!requestScopedApiStatusValid())" in script
    assert "const providerBlock = generationProviderBlockReason()" in script
    assert "function focusApiKeyError(message)" in script
    assert "generationApiKey.setAttribute('aria-invalid', 'true')" in script
    assert "generationApiKey.focus({ preventScroll: true })" in script
    assert "if (providerBlock.kind === 'key')" in script
    assert "if (questionGenerationInFlight) return" in script
    assert script.count("syncGenerationButtonState();") >= 5


def test_upload_and_json_send_only_generic_key_and_detected_provider() -> None:
    _, script = _page()

    assert "const requestGenerationProvider = detectedGenerationProvider()" in script
    assert "const requestGenerationApiKey = currentGenerationApiKey()" in script
    assert "fd.append('generation_provider', requestGenerationProvider)" in script
    assert "fd.append('generation_api_key', requestGenerationApiKey)" in script
    assert "generation_provider: requestGenerationProvider" in script
    assert "...generationCredentialPayload(requestGenerationApiKey)" in script
    assert "return apiKey ? { generation_api_key: apiKey } : {}" in script
    assert script.count("generation_provider") == 2
    assert script.count("generation_api_key") == 2
    assert "openai_api_key" not in script
    assert "openrouter_api_key" not in script
    assert script.count("generationProvider: requestGenerationProvider") == 2

    upload_history = script.split("const uploadGenerationContext = JSON.stringify({", 1)[1].split(
        "prepareQuestionHistory", 1
    )[0]
    manual_history = script.split("const manualGenerationContext = JSON.stringify({", 1)[1].split(
        "prepareQuestionHistory", 1
    )[0]
    assert "generation_api_key" not in upload_history
    assert "requestGenerationApiKey" not in upload_history
    assert "generation_api_key" not in manual_history
    assert "requestGenerationApiKey" not in manual_history


def test_openrouter_latency_has_honest_progress_and_accessible_live_state() -> None:
    html, script = _page()

    assert 'id="progressWrap"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="generationProgressBar"' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="0"' in html
    assert "const OPENROUTER_PROGRESS_STEPS = [" in script
    assert "label: 'Ox Alpha 질문 후보 생성'" in script
    assert "label: '중복 제거·품질 선별'" in script
    assert "afterSeconds: 60" in script
    assert "혼잡하면 몇 분 걸릴 수 있습니다" in script
    assert "setGenerationProgress(94)" in script
    assert "btn.setAttribute('aria-busy', 'true')" in script
    assert "generationApiKey.disabled = true" in script


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
    assert "openai_api: GENERATION_PROVIDER_CONFIGS.openai_api.label" in script
    assert "openrouter_api: GENERATION_PROVIDER_CONFIGS.openrouter_api.label" in script
    assert "openrouter_api: 'OpenRouter · Ox Alpha 초안 유지'" in script
    assert "openrouter_api_quality_repaired_fields: 'OpenRouter · Ox Alpha 초안 유지 · 평가요소 보강'" in script
    assert "model: 'OpenAI API" in script
    assert "codex_cli: 'Codex" in script
    assert "claude_code: 'Claude Code" in script


def test_quality_failures_are_explained_in_panel_language() -> None:
    _, script = _page()

    assert "const QUALITY_ISSUE_LABELS = {" in script
    assert "field_realism: '현장 면접 문장·답변연동성 미충족'" in script
    assert "ksa_measurement_task: '선택한 KSA를 행동으로 측정하지 못함'" in script
    assert "precision_grounding: '제시되지 않은 수치·조항 회상 요구'" in script
    assert "candidate_surface_safe: '후보자 화면에 NCS 내부 명칭 노출'" in script
    assert "candidate_visible_instruction_injection: '외부 문서의 지시문이 질문에 노출됨'" in script
    assert "question_evidence_assignment_failed: '모델 KSA 근거 ID가 서버 배정과 불일치'" in script
    assert "evaluation_elicitation_alignment: '질문하지 않은 평가기준이 포함됨'" in script
    assert "field_realism_instruction_injection_artifact: '외부 문서의 지시문이 질문 표면에 남음'" in script
    assert "field_realism_label_like_metadata_exposure: '내부 라벨·메타데이터가 질문 표면에 노출됨'" in script
    assert "unknown_quality_issue: '기타 필수 품질 검사 실패'" in script
    assert ".filter(Boolean).map(issue => qualityIssueLabel(issue)).join(', ')" in script
    assert "s.model_quality_retry && typeof s.model_quality_retry === 'object'" in script
    assert "모델 생성 요청 ${Number(retryAudit.provider_generation_request_count)}/" in script
    assert "return QUALITY_ISSUE_LABELS[key] || fallback;" in script


def test_quality_rejection_uses_safe_diagnostics_only() -> None:
    _, script = _page()

    assert "function qualityDiagnosticsNotice(detail)" in script
    assert "detail.quality_diagnostics" in script
    assert "requested_question_count" in script
    assert "failed_question_count" in script
    assert "failed_indexes" in script
    assert "issue_counts" in script
    assert "실패 슬롯:" in script
    assert "주요 검사:" in script
    assert "qualityIssueLabel(code, '기타 필수 품질 검사 실패')" in script
    assert "'openai_api_quality_rejected'," in script
    assert "'openrouter_api_quality_rejected'," in script
    assert "].includes(errorCode)" in script


def test_openrouter_failures_are_classified_as_model_stage_errors() -> None:
    _, script = _page()

    for code in (
        "openrouter_api_generation_failed",
        "openrouter_api_timeout",
        "openrouter_api_unreachable",
        "openrouter_api_invalid_output",
        "openrouter_api_quality_rejected",
        "openrouter_api_content_restricted",
        "openrouter_api_request_rejected",
        "openrouter_api_upstream_unavailable",
        "openrouter_api_authentication_failed",
        "openrouter_api_usage_limit_reached",
    ):
        assert f"'{code}'" in script


def test_partial_human_review_result_is_rendered_as_usable_output() -> None:
    _, script = _page()

    release_status = "const partialHumanReviewRequired = s.question_release_status === 'partial_human_review_required';"
    assert release_status in script
    assert "function partialGenerationNotice(strategy, questions)" in script
    assert "strategy.partial_generation && typeof strategy.partial_generation === 'object'" in script
    assert "requested_question_count" in script
    assert "returned_question_count" in script
    assert "omitted_question_count" in script
    assert "omitted_indexes" in script
    message = "요청 ${requested || '-'}개 중 ${returned || 0}개를 반환했고 ${omitted || 0}개는 제외되었습니다."
    assert message in script
    assert "반환된 문항은 사용할 수 있지만, 누락된 슬롯을 포함해 사람 검토가 필요합니다." in script


def test_human_review_release_state_overrides_completed_orchestration_notice() -> None:
    _, script = _page()

    partial_state = script.index(
        "const partialHumanReviewRequired = s.question_release_status === 'partial_human_review_required';"
    )
    release_state = script.index("s.question_release_status === 'human_review_required'")
    retry_state = script.index("retryAudit.outcome === 'accepted_for_human_review'")
    partial_notice = script.index("orchestrationNotice = partialGenerationNotice(s, qList);")
    review_notice = script.index(
        "NCS/KSA 근거와 안전 검사는 통과했지만 일부 표현 품질 항목이 남아 사람 검토가 필요합니다."
    )
    completed_notice = script.index("생성→KSA 실측성 검사→이전 문항 중복 검사→보정→최종 재검사를 완료했습니다.")

    assert partial_state < partial_notice < review_notice < completed_notice
    assert release_state < review_notice
    assert retry_state < review_notice
