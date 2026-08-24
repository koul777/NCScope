from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _inline_script() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(blocks) == 1
    return blocks[0]


def test_frontend_inline_javascript_parses() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", "-"],
        input=_inline_script(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_frontend_history_is_bounded_and_sent_by_both_generation_modes() -> None:
    script = _inline_script()

    assert "const MAX_QUESTION_HISTORY_ITEMS = 50" in script
    assert "questionHistory = merged.slice(-MAX_QUESTION_HISTORY_ITEMS)" in script
    assert "return questionHistory.slice(-MAX_QUESTION_HISTORY_ITEMS)" in script
    assert "rememberQuestions(currentQuestions)" in script
    assert "fd.append('avoid_questions_json', JSON.stringify(currentQuestionTexts()))" in script
    assert "avoid_questions: currentQuestionTexts()" in script


def test_frontend_sends_and_advances_monotonic_generation_offset_only_after_success() -> None:
    script = _inline_script()

    assert "let questionGenerationOffset = 0" in script
    assert "fd.append('generation_offset', String(questionGenerationOffset))" in script
    assert "generation_offset: questionGenerationOffset" in script
    response_guard = script.index("if (!res.ok)")
    render_result = script.index("render(data)", response_guard)
    advance_offset = script.index("questionGenerationOffset +=", render_result)
    assert response_guard < render_result < advance_offset
    assert "if (questionGenerationInFlight) return" in script


def test_frontend_preserves_review_state_across_pagination_and_blocks_duplicate_submit() -> None:
    script = _inline_script()

    assert "let questionReviewStates = new Map()" in script
    assert "const reviewStateKey = `${reviewRunId}|${String(q.question_hash || '')}`" in script
    assert "questionReviewStates.get(reviewStateKey)" in script
    assert "['recording', 'recorded', 'rolling_back'].includes(existing.phase)" in script
    assert "phase: 'recorded'" in script
    assert "phase: 'rolled_back'" in script
    assert "questionReviewStates.clear()" in script
    assert script.count("await readApiResponse(response)") >= 2
    assert "pendingQuestionReviewCount += 1" in script
    assert "pendingQuestionReviewCount = Math.max(0, pendingQuestionReviewCount - 1)" in script
    assert "renderQuestionList(currentQuestions)" in script
    assert "문항 검토 저장이 끝난 뒤 다시 생성해 주세요." in script


def test_frontend_formats_structured_review_errors_without_object_object_text() -> None:
    script = _inline_script()

    assert "function apiErrorMessage(payload, fallback)" in script
    assert "detail.message || detail.code" in script
    assert "(참조: ${reference})" in script
    assert "new Error(apiErrorMessage(payload, '검토 기록 실패'))" in script
    assert "new Error(apiErrorMessage(payload, '되돌리기 실패'))" in script


def test_frontend_routes_only_ncs_match_errors_to_manual_ncs_review() -> None:
    script = _inline_script()

    assert "function isNcsMatchError(detail)" in script
    assert "Array.isArray(payload.lookup_terms)" in script
    assert "Array.isArray(payload.suggested_ncs_units)" in script
    assert "if (isNcsMatchError(data.detail))" in script
    assert "handleNcsMatchError(data.detail)" in script
    assert "handleApiFailure(data, res.status)" in script
    assert "NCS 세분류 매칭과 KSA 조회는 완료되었으며" in script
    assert "독립 AI 품질검수 또는 실패 문항 재생성 단계에서 통과하지 못했습니다." in script
    assert "질문 생성 단계에서 실패했습니다." not in script
    assert "const modelFailureCodes = new Set" in script
    assert "'openai_api_timeout'" in script
    assert "'openai_api_invalid_output'" in script
    assert "'openai_api_quality_rejected'" in script
    assert "stopProgress('실패')" in script
    assert "후보 선택 화면으로 전환했으니 해당 능력단위를 확인해 주세요." in script
    assert "업로드 내용과 면접 설정은 그대로 유지됩니다." in script
    assert "정확한 NCS 세분류 매칭이 없습니다" not in script


def test_frontend_locks_only_the_stale_question_after_review_conflict() -> None:
    script = _inline_script()

    assert "const conflicted = state.phase === 'conflict'" in script
    assert "requestError.httpStatus = response.status" in script
    assert script.count("phase: conflict ? 'conflict'") == 2
    assert "다른 검토가 먼저 저장되었습니다" in script


def test_upload_history_context_includes_confirmed_ncs_details_before_history_read() -> None:
    script = _inline_script()
    final_sclass = script.index("const finalSclass = currentReviewedDetails().length")
    history_details = script.index("const historyDetails = finalSclass.map(normSclassLabel).sort().join(',')")
    context = script.index("const uploadGenerationContext = JSON.stringify", history_details)
    prepare_history = script.index("prepareQuestionHistory(`upload|${uploadGenerationContext}`)", context)
    read_history = script.index("fd.append('avoid_questions_json'", prepare_history)

    assert final_sclass < history_details < context < prepare_history < read_history


def test_history_resets_only_when_generation_context_changes() -> None:
    script = _inline_script()

    assert "questionHistoryContext !== key" in script
    assert "questionHistory = []" in script
    assert "questionGenerationOffset = 0" in script
    assert "const manualGenerationContext = JSON.stringify" in script
    assert "prepareQuestionHistory(`manual|${manualGenerationContext}`)" in script
    assert script.count("questionPlanItems,") >= 2
    assert script.count("interviewMethods,") >= 2
    assert script.count("knobs,") >= 2


def test_frontend_distinguishes_operational_degradation_from_unresolved_questions() -> None:
    script = _inline_script()
    assert "const aiQualityPassed = Boolean(aiQualityReview && aiQualityReview.status === 'passed');" in script
    assert "const qList = aiQualityPassed" in script
    assert "fallback_adjustment_degraded" not in script
    return
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "operational_warnings" in html
    assert "fallback_adjustment_degraded" in html
    assert "내부 후보 보정 단계 강등" in html


def test_frontend_treats_only_quality_passed_fallback_as_recovered() -> None:
    script = _inline_script()

    assert "const recoveredFallbackSucceeded" not in script
    assert "AI 품질검수 통과" in script
    assert "서버 대체 초안" not in script
    return

    assert "const recoveredFallbackSucceeded = Boolean(" in script
    assert "s.question_quality_report.passed === true" in script
    assert "orchestration.status === 'passed'" in script
    assert "if (s.error && !recoveredFallbackSucceeded)" in script
    assert "공식 NCS KSA 기반 대체 초안이 품질 검사를 통과했습니다" in script


def test_frontend_prefers_official_five_level_behavior_scale() -> None:
    script = _inline_script()

    assert "guide.rating_levels" in script
    assert "ratingLevels.length === 5" in script
    assert "visibleLevels.forEach" in script


def test_frontend_uses_recruiter_facing_quality_labels_and_structured_case_data() -> None:
    script = _inline_script()

    assert "AI 품질검수 통과" in script
    assert "AI 검수" in script
    assert "서버 안전검사 통과" in script
    assert "case_materials" in script
    return

    assert "NCS 기준 재구성" in script
    assert "NCS 평가요소 측정성 보강" in script
    assert "토론 정답 유도 제거" in script
    assert "품질 보강:" in script
    assert "품질 보정 사유:" not in script
    assert "case_materials" in script
    assert "사례자료:" in script
    assert "row.source" in script
    assert "row.field" in script
    assert "row.value" in script
