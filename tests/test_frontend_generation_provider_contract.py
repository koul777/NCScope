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
    assert 'id="generationProviderName"' in html
    assert 'id="openaiApiKey"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html
    assert 'spellcheck="false"' in html
    assert 'id="btnClearOpenAiApiKey"' in html
    assert 'id="apiKeySecurityWarning"' in html
    assert 'role="note"' in html
    assert 'aria-busy="true"' in html
    assert 'id="generationProviderError"' in html
    assert 'role="alert"' in html
    assert 'id="btnRefreshGenerationProvider"' in html
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
    assert "openai_api: 'OpenAI API" in script
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
    assert "errorCode === 'openai_api_quality_rejected'" in script


def test_partial_human_review_result_is_rendered_as_usable_output() -> None:
    _, script = _page()

    assert "const partialHumanReviewRequired = s.question_release_status === 'partial_human_review_required';" in script
    assert "function partialGenerationNotice(strategy, questions)" in script
    assert "strategy.partial_generation && typeof strategy.partial_generation === 'object'" in script
    assert "requested_question_count" in script
    assert "returned_question_count" in script
    assert "omitted_question_count" in script
    assert "omitted_indexes" in script
    assert "요청 ${requested || '-'}개 중 ${returned || 0}개를 반환했고 ${omitted || 0}개는 제외되었습니다." in script
    assert "반환된 문항은 사용할 수 있지만, 누락된 슬롯을 포함해 사람 검토가 필요합니다." in script


def test_human_review_release_state_overrides_completed_orchestration_notice() -> None:
    _, script = _page()

    partial_state = script.index("const partialHumanReviewRequired = s.question_release_status === 'partial_human_review_required';")
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
