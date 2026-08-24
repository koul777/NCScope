from __future__ import annotations

from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _page() -> tuple[str, str]:
    html = PAGE.read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    return html, script


def test_openai_byok_card_is_required_and_accessible() -> None:
    html, script = _page()

    assert 'id="generationProviderCard"' in html
    assert 'id="generationApiKey"' in html
    assert '<label id="generationApiKeyLabel" for="generationApiKey">OpenAI API 키 (필수)</label>' in html
    assert 'autocomplete="off"' in html
    assert 'spellcheck="false"' in html
    assert 'id="btnToggleApiKey"' in html
    assert 'id="btnPasteApiKey"' in html
    assert 'id="btnClearApiKey"' in html
    assert 'id="generationProviderSelect"' not in html
    assert "OpenAI · Luna 분류 → Terra 작성 → Sol 검수·재생성" in html
    assert "Vercel Production 서버 키" not in script


def test_frontend_releases_only_openai_ai_reviewed_questions() -> None:
    _, script = _page()

    assert "function isReleasedAiQuestion(item)" in script
    assert "function releasedPayloadForDisplay(value)" in script
    assert "releasedPayloadForDisplay(data)" in script
    assert "String(row.question_source || '').trim() === 'openai_api'" in script
    assert "candidateQList.filter(isReleasedAiQuestion)" in script
    assert "data.generated_questions_all.filter(isReleasedAiQuestion)" in script
    assert "data.generated_question_text_rows.filter(isReleasedAiQuestion)" in script


def test_only_openai_key_prefix_is_accepted_by_the_ui() -> None:
    _, script = _page()

    assert "if (key.startsWith('sk-or-')) return '';" in script
    assert "if (key.startsWith('sk-')) return 'openai_api';" in script
    assert "const requestedProvider = detectedGenerationProvider() || 'openai_api';" in script
    assert "const probeProvider = detectedProvider || 'openai_api';" in script
    assert "serverEnvApiStatusValid" not in script


def test_api_key_remains_in_form_memory_only() -> None:
    _, script = _page()

    assert "function currentGenerationApiKey()" in script
    assert "String(generationApiKey.value || '').trim()" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "indexedDB" not in script
    assert "document.cookie" not in script
    assert "window.isSecureContext === true" in script
    assert "setApiKeyVisibility(false)" in script


def test_generation_requires_key_and_sends_request_scoped_credential() -> None:
    _, script = _page()

    assert "OpenAI API 키(sk-…)를 입력해 주세요." in script
    assert "function generationCredentialPayload(apiKey)" in script
    assert "generation_api_key: apiKey" in script
    assert "generation_provider: requestGenerationProvider" in script
    assert "fd.append('generation_api_key', requestGenerationApiKey)" in script
    assert "fd.append('generation_provider', requestGenerationProvider)" in script
    assert "openai_api_key" not in script
    assert "openrouter_api_key" not in script


def test_progress_describes_generation_and_independent_review_without_fallback() -> None:
    html, script = _page()

    assert 'id="generationProgressBar"' in html
    assert 'role="progressbar"' in html
    assert "질문 생성과 독립 AI 품질검수를 진행 중입니다." in script
    assert "공식 KSA 서버 문항으로 자동" not in script
    assert "OPENROUTER_PROGRESS_STEPS" not in script
    assert "로컬 NCS DB" not in html
    assert "공식 NCS MCP 조회" in html


def test_only_ai_review_passed_questions_are_rendered_and_exported() -> None:
    _, script = _page()

    assert "const aiQualityReview = s.ai_quality_review" in script
    assert "const aiQualityPassed = Boolean(aiQualityReview && aiQualityReview.status === 'passed');" in script
    assert "const qList = aiQualityPassed" in script
    assert "AI 품질검수 통과" in script
    assert "독립 AI의 현재 승인 기준을 통과했습니다" in script
    assert "모든 점수가 4점 이상" not in script
    assert "const authoritativeAiPass = Boolean(aiReview && aiReview.status === 'passed');" in script
    assert "if (summary && !authoritativeAiPass)" in script
    assert "if (authoritativeAiPass) add('최종 사람 검토 권장');" in script
    assert "const qualityReportPassed" not in script
    assert "서버 대체 초안" not in script
    assert "partial_human_review_required" not in script
    assert "question_release_status === 'human_review_required'" not in script


def test_quality_errors_use_safe_retry_guidance() -> None:
    _, script = _page()

    assert "openai_api_quality_rejected" in script
    assert "qualityDiagnosticsNotice" in script
    assert "openai_api_unreachable" in script
    assert "openai_api_timeout" in script
    assert "API 키" in script
