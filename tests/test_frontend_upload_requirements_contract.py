from __future__ import annotations

import re
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _page() -> tuple[str, str]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == 1
    return html, scripts[0]


def test_upload_mode_explains_both_required_documents_before_api_setup() -> None:
    html, _ = _page()

    requirements = html.index('id="uploadRequirementsCard"')
    provider = html.index('id="generationProviderCard"')
    assert requirements < provider
    assert "같은 채용 건의 필수 자료 2종을 탑재하세요" in html
    assert "1. 공공기관 채용공고문" in html
    assert "2. 해당 공고의 NCS 기반 직무기술서" in html
    assert "둘 중 하나라도 없으면 파일 업로드 방식의 질문 생성을 진행할 수 없습니다." in html
    assert "HWP/HWPX는 PDF로 변환해 탑재하세요." in html


def test_required_document_inputs_are_single_accessible_controls() -> None:
    html, _ = _page()

    assert html.count('id="noticeFile"') == 1
    assert html.count('id="jdFile"') == 1
    assert '<label for="noticeFile">' in html
    assert '<label for="jdFile">' in html
    assert re.search(r'id="noticeFile"[^>]+\brequired\b', html)
    assert re.search(r'id="jdFile"[^>]+\brequired\b', html)
    assert 'id="noticeUploadState" class="document-state" role="status"' in html
    assert 'id="jdUploadState" class="document-state" role="status"' in html
    assert 'aria-describedby="noticeFileHelp uploadRequirementsNote"' in html
    assert 'aria-describedby="jdFileHelp uploadRequirementsNote"' in html


def test_document_selection_state_and_mode_visibility_are_synchronized() -> None:
    _, script = _page()

    assert "function refreshRequiredDocumentStatus()" in script
    assert "status.classList.toggle('ready', Boolean(file))" in script
    assert "status.textContent = file ? '선택 완료' : '선택 필요'" in script
    assert script.count("refreshRequiredDocumentStatus();") >= 4
    assert "uploadRequirementsCard.classList.toggle('hidden', !isUpload)" in script


def test_upload_generation_is_blocked_until_both_documents_are_reviewed() -> None:
    html, script = _page()

    assert '<button id="btnRun" type="button" disabled>필수 자료 2종 선택 후 면접 질문 생성</button>' in html
    assert "const hasJdFile = !!(jdFile.files && jdFile.files[0])" in script
    assert "const hasNoticeFile = !!(noticeFile.files && noticeFile.files[0])" in script
    assert "if (!hasJdFile || !hasNoticeFile)" in script
    assert "공공기관 채용공고문과 해당 NCS 기반 직무기술서를 모두 탑재해 주세요." in script
    assert "if (!noticeReviewConfirmed)" in script
    assert "공고문 검토·적용 후 면접 질문 생성" in script
    assert "const nFile = noticeFile.files && noticeFile.files[0]" in script
    assert "if (!nFile)" in script
    assert "공고문과 NCS 기반 직무기술서는 모두 필수입니다." in script


def test_upload_mode_blocks_oversized_single_and_combined_files_before_network_requests() -> None:
    _, script = _page()

    assert "const MAX_SINGLE_UPLOAD_FILE_BYTES = 4 * 1024 * 1024;" in script
    assert "const MAX_COMBINED_GENERATION_UPLOAD_BYTES = 3 * 1024 * 1024;" in script
    assert "function uploadPayloadBoundaryState(nextField = '', nextFile = null)" in script
    assert "function clientInputBoundaryIssue(mode = inputMode.value || 'upload')" in script
    assert "생성 요청은 직무기술서·공고문 PDF와 검토 JSON을 함께 전송하므로" in script
    assert "const uploadBoundary = uploadPayloadBoundaryState('jd', file);" in script
    assert "const uploadBoundary = uploadPayloadBoundaryState('notice', file);" in script
    assert "showClientBoundaryError(uploadBoundary);" in script
    assert "button: '파일 4MiB 이하로 조정'" in script
    assert "button: '파일 2종 합산 용량 줄이기'" in script


def test_upload_review_and_generation_block_oversized_text_inputs_early() -> None:
    _, script = _page()

    assert "const MAX_REVIEW_NCS_DETAIL_CHARS = 2000;" in script
    assert "const MAX_NOTICE_TEXT_CHARS = 12000;" in script
    assert "const MAX_STRENGTHS_CHARS = 2000;" in script
    assert "const UPLOAD_TEXT_FIELD_LIMITS = Object.freeze({" in script
    assert "function textBoundaryIssue(mode = inputMode.value || 'upload')" in script
    assert "['확정 세분류', reviewNcsDetail, MAX_REVIEW_NCS_DETAIL_CHARS]" in script
    assert "['담당업무 텍스트', dutyText, UPLOAD_TEXT_FIELD_LIMITS.dutyText]" in script
    assert "['직접입력 공고문 텍스트', noticeText, MAX_NOTICE_TEXT_CHARS]" in script
    assert "['강점 텍스트', strengthsInput, MAX_STRENGTHS_CHARS]" in script
    assert "const boundaryIssue = textBoundaryIssue();" in script
    assert "const inputBoundary = clientInputBoundaryIssue();" in script
    assert "showClientBoundaryError(boundaryIssue);" in script
    assert "showClientBoundaryError(inputBoundary);" in script


def test_upload_review_uses_single_select_for_ncs_detail_and_single_method_select() -> None:
    html, script = _page()

    assert '<select id="reviewNcsDetail" class="dropdown"' in html
    assert '<textarea id="reviewNcsDetail"' not in html
    assert 'id="reviewNcsDetailHelp"' in html
    assert "function setReviewedNcsDetailOptions(candidates, selectedValue = '')" in script
    assert "const reviewedCandidates = setReviewedNcsDetailOptions(fields.ncs_detail_candidates || []);" in script
    assert "jdReviewPayload.fields.ncs_detail_candidates = currentReviewedDetails();" in script
    assert "return detail ? [detail] : [];" in script
    assert "reviewNcsDetail.addEventListener('change'" in script

    assert 'id="interviewMethodSelect" class="dropdown"' in html
    assert 'name="interviewMethod"' not in html
    assert "const interviewMethodSelect = document.getElementById('interviewMethodSelect');" in script
    assert "const SUPPORTED_INTERVIEW_METHODS = Object.freeze([" in script
    assert "const selected = String(interviewMethodSelect?.value || '').trim();" in script
    assert "return [SUPPORTED_INTERVIEW_METHODS.includes(selected) ? selected : SUPPORTED_INTERVIEW_METHODS[0]];" in script

    assert '<select id="ncsSelect" class="dropdown">' in html
    assert '<select id="ncsSelect" multiple>' not in html
    assert "NCS 세분류·능력단위 1개 선택" in html


def test_successful_generation_exposes_repeat_one_question_action() -> None:
    html, script = _page()

    assert "같은 조건으로 다른 질문 1개 생성" in script
    assert "현재 문항을 회피 이력에 포함해 겹치지 않는 다음 문항을 생성합니다." in script
    assert "현재 문항을 회피 이력에 포함해 같은 세분류·면접 형태의 다음 문항을 생성합니다." in script
    assert "fd.append('avoid_questions_json', JSON.stringify(currentQuestionTexts()))" in script
    assert "avoid_questions: currentQuestionTexts()" in script


def test_required_upload_ux_preserves_default_ox_alpha_key_contract() -> None:
    html, script = _page()

    assert "OpenRouter · Ox Alpha" in html
    assert "OpenRouter · Ox Alpha · 고위험 high 추론·서버 KSA 자동복구" in html
    assert "Vercel Production 서버 키가 연결되면 브라우저에 키를 입력하지 않아도 됩니다." in html
    assert "serverEnvApiStatusValid" in script
    assert "if (key.startsWith('sk-or-')) return 'openrouter_api'" in script
    assert "if (key.startsWith('sk-')) return 'openai_api'" in script
    assert script.index("key.startsWith('sk-or-')") < script.index("key.startsWith('sk-')")


def test_reviewed_notice_is_reused_without_a_second_generation_parse() -> None:
    _, script = _page()

    assert "let noticeReviewPayload = null" in script
    assert "noticeReviewPayload = data" in script
    assert "review_session_id: data.review_session_id || ''" in script
    assert "review_confirmed: true" in script
    assert "fd.append('notice_review_json', JSON.stringify(noticeReviewPayload))" in script
