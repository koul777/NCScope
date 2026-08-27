from __future__ import annotations

import re
import subprocess
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _page() -> tuple[str, str]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == 1
    return html, scripts[0]


def test_inline_javascript_has_valid_syntax() -> None:
    _, script = _page()
    completed = subprocess.run(
        ["node", "--check", "--input-type=commonjs"],
        input=script.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_review_only_convergence_suggestion_behavior_executes_in_node() -> None:
    _, script = _page()
    start = script.index("function reviewedConvergenceSuggestions(fields)")
    end = script.index("function positionedAbilityItemsForReviewedDetail(fields, detail)")
    functions = script[start:end]
    harness = """
const makeElement = (tagName) => ({
  tagName,
  children: [],
  dataset: {},
  value: '',
  textContent: '',
  label: '',
  appendChild(child) { this.children.push(child); },
});
const document = { createElement: makeElement };
const reviewNcsDetail = makeElement('select');
const dedupSclassLabels = (values) => [...new Set(
  values.map(value => String(value || '').trim()).filter(Boolean)
)];
""" + functions + """
const fields = {
  ncs_detail_candidates: [],
  ability_units: ['오염되면 안 되는 전체 능력단위'],
  ncs_detail_convergence_suggestions: [
    {
      officialDetailName: '사무행정',
      distinctExactUnitCount: 2,
      reviewRequired: true,
      automaticMappingAllowed: false,
      mappingState: 'official_detail_candidate_from_exact_unit_convergence',
      evidence: [
        { sourceAbilityUnitName: '문서 작성' },
        { sourceAbilityUnitName: '문서 관리' },
      ],
    },
    {
      officialDetailName: '총무',
      distinctExactUnitCount: 3,
      reviewRequired: true,
      automaticMappingAllowed: true,
      mappingState: 'official_detail_candidate_from_exact_unit_convergence',
      evidence: [{ sourceAbilityUnitName: '비품관리' }],
    },
  ],
};
const suggestions = reviewedConvergenceSuggestions(fields);
if (suggestions.length !== 1 || suggestions[0].name !== '사무행정') {
  throw new Error('unsafe convergence rows were not filtered');
}
const direct = setReviewedNcsDetailOptions([], '', suggestions);
if (direct.length !== 0 || reviewNcsDetail.value !== '' || reviewNcsDetail.selectedIndex !== 0) {
  throw new Error('review-only suggestion was automatically selected');
}
const groups = reviewNcsDetail.children.filter(child => child.tagName === 'optgroup');
if (groups.length !== 1 || groups[0].children[0].dataset.reviewSuggestion !== 'true') {
  throw new Error('review-only suggestion group was not rendered');
}
const units = abilityUnitsForReviewedDetail(fields, '사무행정');
if (JSON.stringify(units) !== JSON.stringify(['문서 작성', '문서 관리'])) {
  throw new Error('selected suggestion did not retain only its exact evidence units');
}
console.log('convergence suggestion behavior ok');
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_review_only_profile_suggestion_is_separate_and_not_preselected() -> None:
    _, script = _page()
    start = script.index("function reviewedConvergenceSuggestions(fields)")
    end = script.index("function positionedAbilityItemsForReviewedDetail(fields, detail)")
    functions = script[start:end]
    harness = """
const makeElement = (tagName) => ({
  tagName,
  children: [],
  dataset: {},
  value: '',
  textContent: '',
  label: '',
  appendChild(child) { this.children.push(child); },
});
const document = { createElement: makeElement };
const reviewNcsDetail = makeElement('select');
const dedupSclassLabels = (values) => [...new Set(
  values.map(value => String(value || '').trim()).filter(Boolean)
)];
""" + functions + """
const fields = {
  ncs_detail_candidates: [],
  ncs_detail_suggestions: [
    {
      sclass_name: '경영기획',
      ncs_code_no: '02010101',
      confidence: 0.77,
      review_required: true,
      source: 'alio_corpus_profile',
      training_documents: 4,
      matched_tokens: ['경영계획', '사업환경'],
    },
  ],
};
const suggestions = reviewedProfileSuggestions(fields);
if (suggestions.length !== 1 || suggestions[0].name !== '경영기획') {
  throw new Error('profile review suggestion was not normalized');
}
const direct = setReviewedNcsDetailOptions([], '', suggestions);
if (direct.length !== 0 || reviewNcsDetail.value !== '' || reviewNcsDetail.selectedIndex !== 0) {
  throw new Error('profile review suggestion was automatically selected');
}
const groups = reviewNcsDetail.children.filter(child => child.tagName === 'optgroup');
if (groups.length !== 1 || !groups[0].label.includes('ALIO')
    || groups[0].children[0].dataset.reviewSuggestion !== 'true') {
  throw new Error('profile review suggestion group was not rendered');
}
if (reviewNcsDetail.value !== '') {
  throw new Error('unselected profile suggestion leaked into reviewed details');
}
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_profile_suggestion_does_not_reuse_extracted_ability_scope_in_node() -> None:
    _, script = _page()
    start = script.index("function reviewedConvergenceSuggestions(fields)")
    end = script.index("function positionedAbilityItemsForReviewedDetail(fields, detail)")
    functions = script[start:end]
    harness = """
const makeElement = (tagName) => ({
  tagName,
  children: [],
  dataset: {},
  value: '',
  textContent: '',
  label: '',
  appendChild(child) { this.children.push(child); },
});
const document = { createElement: makeElement };
const reviewNcsDetail = makeElement('select');
const dedupSclassLabels = (values) => [...new Set(
  values.map(value => String(value || '').trim()).filter(Boolean)
)];
""" + functions + """
const fields = {
  ncs_detail_source: 'alio_corpus_review_suggestion',
  ncs_detail_candidates: ['경영기획'],
  ncs_detail_suggestions: [
    {
      sclass_name: '경영기획',
      review_required: true,
      source: 'alio_corpus_profile',
    },
  ],
  ability_units: ['사업환경 분석'],
};
const units = abilityUnitsForReviewedDetail(fields, '경영기획');
if (units.length !== 0) {
  throw new Error('profile suggestion reused extracted ability scope');
}
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_detail_first_official_ability_selection_executes_in_node() -> None:
    _, script = _page()
    start = script.index("function selectedReviewedOfficialAbilityUnits()")
    end = script.index("function positionedAbilityItemsForReviewedDetail(fields, detail)")
    functions = script[start:end]
    harness = """
const makeSelect = () => ({
  children: [],
  disabled: false,
  replaceChildren() { this.children = []; },
  appendChild(child) { this.children.push(child); },
  get selectedOptions() { return this.children.filter(child => child.selected); },
});
const document = { createElement: () => ({ value: '', textContent: '', selected: false }) };
const reviewOfficialAbilityUnits = makeSelect();
const reviewOfficialAbilityUnitsStatus = { textContent: '' };
const reviewOfficialAbilityUnitsCoverage = { textContent: '', hidden: true };
let reviewAbilityRequestId = 0;
const normalizeNcsReviewKey = value => String(value || '').toLowerCase().replace(/[\\s\\-_/|(),.·・]+/g, '');
const abilityUnitsForReviewedDetail = () => ['Document Writing', 'Unknown Extracted'];
const readApiResponse = async response => response.payload;
const apiErrorMessage = (_payload, fallback) => fallback;
let fetchPayload = {
  source: 'ncs-mcp',
  items: [
    { compeUnitName: 'Document Writing', ncsClCd: '0201010101_24v1', compeUnitLevel: '3', ncsSubdCdnm: 'Office Admin' },
    { compeUnitName: 'Ambiguous Name', ncsClCd: '0201010102_24v1', compeUnitLevel: '3', ncsSubdCdnm: 'Office Admin' },
    { compeUnitName: 'Ambiguous Name', ncsClCd: '0201010199_24v1', compeUnitLevel: '3', ncsSubdCdnm: 'Office Admin' },
    { compeUnitName: 'Cross Detail', ncsClCd: '9999999999_24v1', compeUnitLevel: '2', ncsSubdCdnm: 'Other' },
  ],
};
const fetch = async () => ({ ok: true, payload: fetchPayload });
""" + functions + """
(async () => {
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (reviewOfficialAbilityUnits.children.length !== 1) {
    throw new Error('cross-detail or ambiguous official units were not filtered');
  }
  if (JSON.stringify(selectedReviewedOfficialAbilityUnits()) !== JSON.stringify(['Document Writing'])) {
    throw new Error('only exact extracted official names should be preselected');
  }
  if (!reviewOfficialAbilityUnitsStatus.textContent.includes('1')) {
    throw new Error('selection status did not expose exact and unmatched counts');
  }
  if (!reviewOfficialAbilityUnitsStatus.textContent.includes('코드가 하나로 확정되지 않는 공식명 1개 제외')) {
    throw new Error('ambiguous official unit name exclusion was not disclosed');
  }
  fetchPayload = {
    source: 'ncs-mcp',
    items: [{
      compeUnitName: 'Document Writing',
      ncsClCd: '0201010101_24v1',
      ncsSubdCdnm: 'Office Admin',
      officialDetailCode: '02010101',
      detailExpectedUnitBaseCount: 2,
      detailVerifiedUnitBaseCount: 1,
      detailRetrievalComplete: false,
      detailRetrievalCapLimited: false,
    }],
  };
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (reviewOfficialAbilityUnitsCoverage.hidden
      || !reviewOfficialAbilityUnitsCoverage.textContent.includes('2개 중 1개')) {
    throw new Error('partial official retrieval coverage was not disclosed');
  }
  fetchPayload = {
    source: 'ncs-mcp',
    items: [{
      compeUnitName: 'Document Writing',
      ncsClCd: '0201010101_24v1',
      ncsSubdCdnm: 'Office Admin',
      officialDetailCode: '02010101',
      detailExpectedUnitBaseCount: 2,
      detailVerifiedUnitBaseCount: 2,
      detailRetrievalComplete: true,
      detailRetrievalCapLimited: false,
    }],
  };
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (!reviewOfficialAbilityUnitsCoverage.hidden || reviewOfficialAbilityUnitsCoverage.textContent) {
    throw new Error('complete uncapped retrieval must not create a warning');
  }
  fetchPayload = {
    source: 'ncs-mcp',
    items: [{
      compeUnitName: 'Document Writing',
      ncsClCd: '0201010101_24v1',
      ncsSubdCdnm: 'Office Admin',
      officialDetailCode: '02010101',
      detailExpectedUnitBaseCount: 2,
      detailVerifiedUnitBaseCount: 2,
      detailRetrievalComplete: true,
      detailRetrievalCapLimited: true,
    }],
  };
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (!reviewOfficialAbilityUnitsCoverage.textContent.includes('조회 한도')) {
    throw new Error('complete but response-capped retrieval was not disclosed');
  }
  fetchPayload = {
    source: 'ncs-mcp',
    items: [
      {
        compeUnitName: 'Document Writing',
        ncsClCd: '0201010101_24v1',
        ncsSubdCdnm: 'Office Admin',
        officialDetailCode: '02010101',
        detailExpectedUnitBaseCount: 2,
        detailVerifiedUnitBaseCount: 1,
        detailRetrievalComplete: false,
        detailRetrievalCapLimited: false,
      },
      {
        compeUnitName: 'Document Writing',
        ncsClCd: '0201010101_24v1',
        ncsSubdCdnm: 'Office Admin',
        officialDetailCode: '02010101',
        detailExpectedUnitBaseCount: 2,
        detailVerifiedUnitBaseCount: 2,
        detailRetrievalComplete: true,
        detailRetrievalCapLimited: false,
      },
    ],
  };
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (!reviewOfficialAbilityUnitsCoverage.hidden || reviewOfficialAbilityUnitsCoverage.textContent) {
    throw new Error('conflicting same-detail retrieval metadata must be ignored');
  }
  fetchPayload = {
    source: 'ncs-mcp-suggest',
    items: [{
      compeUnitName: 'Suggested',
      detailExpectedUnitBaseCount: 2,
      detailVerifiedUnitBaseCount: 1,
      detailRetrievalComplete: false,
      detailRetrievalCapLimited: true,
    }],
  };
  await loadReviewedOfficialAbilityUnits({}, 'Office Admin');
  if (reviewOfficialAbilityUnits.children.length !== 0 || !reviewOfficialAbilityUnits.disabled) {
    throw new Error('suggested units must not enter the exact ability selector');
  }
  if (!reviewOfficialAbilityUnitsCoverage.hidden || reviewOfficialAbilityUnitsCoverage.textContent) {
    throw new Error('suggestion metadata must not create an exact coverage warning');
  }
  console.log('detail-first official ability selection ok');
})().catch(error => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_coordinate_rendering_behavior_executes_in_node() -> None:
    _, script = _page()
    start = script.index("function normalizeNcsReviewKey(value)")
    end = script.index("function clearJdReviewExtractedFields()")
    functions = script[start:end]
    harness = """
const makeElement = (tagName) => ({
  tagName,
  children: [],
  className: '',
  style: {},
  textContent: '',
  open: true,
  appendChild(child) { this.children.push(child); },
  append(...children) { this.children.push(...children); },
  replaceChildren() { this.children = []; },
});
const document = {
  createElement: makeElement,
  createTextNode: text => ({ tagName: '#text', textContent: String(text) }),
};
const reviewAbilityEvidence = makeElement('details');
const reviewAbilityEvidenceSummary = makeElement('summary');
const reviewAbilityEvidenceList = makeElement('div');
""" + functions + """
const cell = (row, column, row_span = 1, column_span = 1) => ({
  row, column, row_span, column_span,
});
const fields = {
  table_coordinate_contract: { index_base: 0 },
  positioned_items: [
    {
      section: 'ability_units', text: '문서 작성', source: 'kordoc', page: 2, table_index: 0,
      label_cell: cell(0, 0), value_cell: cell(0, 1),
      scope: { ncs_details: ['사무행정'] },
    },
    {
      section: 'ability_units', text: '오류 좌표', source: 'kordoc', page: 1, table_index: 0,
      label_cell: cell(1, 0), value_cell: cell(1, 1, 0, 1),
      scope: { ncs_details: ['사무행정'] },
    },
    {
      section: 'ability_units', text: '미연결 복구', source: 'html_table_recovery', page: 0, table_index: 1,
      label_cell: cell(2, 0), value_cell: cell(2, 1),
      scope: { ncs_details: [] },
    },
    {
      section: 'ability_units', text: '코드 기준 복구', source: 'kordoc_code_anchored_training_recovery', page: 3, table_index: 2,
      label_cell: cell(3, 0), value_cell: cell(3, 3),
      scope: { ncs_details: ['사무행정'] },
    },
  ],
};
renderAbilityCoordinateEvidence(fields, '사무행정');
if (reviewAbilityEvidenceSummary.textContent !== '선택 세분류 표 위치 3건 · 세분류 미연결 1건 · 좌표 보기') {
  throw new Error('scoped and unscoped coordinate counts were mixed');
}
const renderedRows = reviewAbilityEvidenceList.children.filter(child => child.tagName === 'div');
const rowText = row => row.children.map(child => child.textContent || '').join('');
const nativeRow = renderedRows.find(row => rowText(row).includes('문서 작성'));
const recoveredRow = renderedRows.find(row => rowText(row).includes('미연결 복구'));
const codeRecoveredRow = renderedRows.find(row => rowText(row).includes('코드 기준 복구'));
const invalidRow = renderedRows.find(row => rowText(row).includes('오류 좌표'));
if (!rowText(nativeRow).includes('2페이지 · 표 1 · 라벨 R1C1 · span 1×1 · 값 R1C2')) {
  throw new Error('native page or 0-based cell display is wrong');
}
if (!rowText(recoveredRow).includes('세분류 미연결 · 원본 페이지 미확정(복구 좌표)')) {
  throw new Error('recovered coordinates were shown as native page evidence');
}
if (!rowText(codeRecoveredRow).includes('원본 페이지 미확정(복구 좌표)')) {
  throw new Error('code-anchored recovery was shown as native page evidence');
}
if (!invalidRow.className.includes('invalid') || !rowText(invalidRow).includes('값 좌표 형식 오류')) {
  throw new Error('invalid coordinate shape was silently normalized');
}
console.log('coordinate rendering behavior ok');
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_mapping_state_notice_behavior_executes_in_node() -> None:
    _, script = _page()
    start = script.index("function mappingSourceNames(rows, states)")
    end = script.index("function abilityUnitsForReviewedDetail(fields, detail)")
    functions = script[start:end]
    harness = """
const makeElement = (tagName) => ({
  tagName,
  children: [],
  textContent: '',
  hidden: true,
  appendChild(child) { this.children.push(child); },
  replaceChildren() { this.children = []; },
});
const document = { createElement: makeElement };
const reviewNcsMappingNotice = makeElement('div');
const reviewedProfileSuggestions = () => [];
""" + functions + """
const fields = {
  ncs_detail_candidates: [],
  ncs_detail_absence_declared_no_mapping: true,
  ncs_detail_absence_reason: 'no_ncs_mapping_declared',
  ncs_detail_mapping_states: [
    { sourceName: '기관 자체 직무', mappingState: 'source_declared_self_developed' },
    { sourceName: '구버전 세분류', mappingState: 'not_in_current_official_catalog' },
    { sourceName: '중복 공식명', mappingState: 'official_current_name_ambiguous' },
  ],
  ability_unit_mapping_states: [
    { sourceName: '기관 능력단위', mappingState: 'not_in_current_official_catalog' },
    { sourceName: '공통 관리', mappingState: 'official_exact_scope_conflict' },
    { sourceName: '중복 코드 능력', mappingState: 'official_exact_code_ambiguous' },
    { sourceName: '파생 범위 능력', mappingState: 'official_exact_derived_scope_review_required' },
  ],
};
const notices = renderNcsMappingNotice(fields);
const rendered = notices.join('\\n');
for (const expected of ['NCS 매핑 없음 선언', '현재 공식 NCS 세분류 미매핑', '세분류 공식명 모호', '현재 공식 NCS 능력단위 미매핑', '능력단위 범위 모호·충돌', '능력단위 코드 모호', '능력단위 범위 검토']) {
  if (!rendered.includes(expected)) throw new Error(`missing mapping state: ${expected}`);
}
if (reviewNcsMappingNotice.hidden || reviewNcsMappingNotice.children.length !== notices.length) {
  throw new Error('mapping notices were not rendered accessibly');
}
const exactOnly = renderNcsMappingNotice({
  ncs_detail_candidates: ['사무행정'],
  ncs_detail_mapping_states: [
    { sourceName: '사무행정', mappingState: 'official_current_exact' },
  ],
});
if (exactOnly.length !== 0 || !reviewNcsMappingNotice.hidden || reviewNcsMappingNotice.children.length !== 0) {
  throw new Error('exact-only state produced a false warning');
}
const absenceCases = [
  ['ncs_detail_header_without_candidate', '세분류 값 미확정'],
  ['recruitment_notice_not_job_description', '문서 유형 확인'],
  ['translation_role_without_explicit_ncs_detail', '번역 직무·세분류 미기재'],
  ['multi_role_healthcare_document_without_explicit_ncs_detail', '의료 다직종·세분류 미기재'],
  ['job_document_without_explicit_ncs_detail', '직무기술서·세분류 미기재'],
];
for (const [reason, expected] of absenceCases) {
  const result = renderNcsMappingNotice({ ncs_detail_candidates: [], ncs_detail_absence_reason: reason });
  if (!result.some(value => value.startsWith(expected))) throw new Error(`missing absence reason: ${reason}`);
}
const tableEmpty = renderNcsMappingNotice({
  ncs_detail_candidates: [],
  ncs_detail_source: 'pdf_table_detail_empty',
});
if (!tableEmpty.some(value => value.startsWith('표 기반 세분류 미확정'))) {
  throw new Error('pdf_table_detail_empty was silent');
}
const failed = renderNcsMappingNotice({}, true);
if (failed.length !== 1 || !failed[0].startsWith('추출 실패:')) {
  throw new Error('parse failure was not distinguished');
}
console.log('mapping state notice behavior ok');
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_upload_mode_explains_both_required_documents_before_api_setup() -> None:
    html, _ = _page()

    requirements = html.index('id="uploadRequirementsCard"')
    provider = html.index('id="generationProviderCard"')
    assert requirements < provider
    assert "같은 채용 건의 필수 자료 2종을 탑재하세요" in html
    assert "1. 공공기관 채용공고문" in html
    assert "2. 해당 공고의 NCS 기반 직무기술서" in html
    assert "둘 중 하나라도 없으면 파일 업로드 방식의 질문 생성을 진행할 수 없습니다." in html
    assert "PDF/HWP/HWPX/DOCX/TXT와 지원 문서를 담은 ZIP을 사용할 수 있습니다." in html
    assert 'accept=".pdf,.hwp,.hwpx,.docx,.txt,.zip"' in html
    assert 'accept=".pdf,.hwp,.hwpx,.docx,.zip,.png,.jpg,.jpeg,.webp,.txt"' in html


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
    assert "const MAX_REVIEW_ABILITY_UNIT_CHARS = 6000;" in script
    assert "const MAX_NOTICE_TEXT_CHARS = 12000;" in script
    assert "const MAX_STRENGTHS_CHARS = 2000;" in script
    assert "const UPLOAD_TEXT_FIELD_LIMITS = Object.freeze({" in script
    assert "function textBoundaryIssue(mode = inputMode.value || 'upload')" in script
    assert "['확정 세분류', reviewNcsDetail, MAX_REVIEW_NCS_DETAIL_CHARS]" in script
    assert "['확정 요구능력단위', reviewAbilityUnits, MAX_REVIEW_ABILITY_UNIT_CHARS]" in script
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
    assert "function reviewedConvergenceSuggestions(fields)" in script
    assert "row.mappingState !== 'official_detail_candidate_from_exact_unit_convergence'" in script
    assert "!row.reviewRequired || row.automaticMappingAllowed" in script
    assert "function setReviewedNcsDetailOptions(candidates, selectedValue = '', suggestions = [])" in script
    assert "'직접 세분류 없음 · 검토 제안 선택 필요'" in script
    assert "능력단위 정확 일치 기반 검토 제안 · 자동 확정 아님" in script
    assert "const convergenceSuggestions = reviewedConvergenceSuggestions(fields);" in script
    assert "const reviewedCandidates = setReviewedNcsDetailOptions(" in script
    assert "jdReviewPayload.fields.ncs_detail_candidates = currentReviewedDetails();" in script
    assert "return detail ? [detail] : [];" in script
    assert "reviewNcsDetail.addEventListener('change'" in script
    assert '<textarea id="reviewAbilityUnits"' in html
    assert "function abilityUnitsForReviewedDetail(fields, detail)" in script
    assert "const singleDetailFallback = !profileSuggestionSelected" in script
    assert "&& detailCandidates.length === 1" in script
    assert "const convergenceSuggestion = reviewedConvergenceSuggestions(fields).find(" in script
    assert "row?.sourceAbilityUnitName" in script
    assert 'id="reviewNcsMappingNotice"' in html
    assert "function renderNcsMappingNotice(fields, parseFailure = false)" in script
    assert "source_declared_self_developed" in script
    assert "not_in_current_official_catalog" in script
    assert "official_current_name_ambiguous" in script
    assert "official_exact_scope_conflict" in script
    assert "ncs_detail_cell_blank_or_dash" in script
    assert "renderNcsMappingNotice({}, true);" in script
    assert 'id="reviewOfficialAbilityUnits"' in html
    assert 'id="reviewOfficialAbilityUnitsStatus"' in html
    assert "function selectedReviewedOfficialAbilityUnits()" in script
    assert "function populateReviewedOfficialAbilityUnits(items, extractedNames = [])" in script
    assert "async function loadReviewedOfficialAbilityUnits(fields, detail)" in script
    assert "function exactDetailRetrievalNotice(data, items = [])" in script
    assert "renderExactDetailRetrievalNotice(ncsRetrievalNotice, data, data.items || []);" in script
    assert "String(data.source || '') === 'ncs-mcp'" in script
    assert "normalizeNcsReviewKey(item?.ncsSubdCdnm) === normalizeNcsReviewKey(reviewedDetail)" in script
    assert "jdReviewPayload.fields.extracted_ability_units = reviewAbilityUnits.value.split(/\\n+/)" in script
    assert "jdReviewPayload.fields.ability_units = selectedReviewedOfficialAbilityUnits();" in script
    assert "reviewOfficialAbilityUnits?.addEventListener('change'" in script

    assert 'id="reviewAbilityEvidence"' in html
    assert 'id="reviewAbilityEvidenceSummary"' in html
    assert 'id="reviewAbilityEvidenceList"' in html
    assert "function positionedAbilityItemsForReviewedDetail(fields, detail)" in script
    assert "function unscopedPositionedAbilityItems(fields, detail)" in script
    assert "function renderAbilityCoordinateEvidence(fields, detail)" in script
    assert "item.section !== 'ability_units'" in script
    assert "return !detailKey || scopeDetails.includes(detailKey);" in script
    assert "scopeDetails.length === 0" in script
    assert "function nonnegativeCoordinate(value)" in script
    assert "function positiveCoordinateSpan(value)" in script
    assert "function formattedCellCoordinate(cell, label, indexBase)" in script
    assert "? `${page}페이지`" in script
    assert "원본 페이지 미확정(복구 좌표)" in script
    assert "좌표 형식 오류" in script
    assert "세분류 미연결" in script
    assert "function clearJdReviewExtractedFields()" in script
    assert script.count("clearJdReviewExtractedFields();") >= 4
    assert "renderAbilityCoordinateEvidence(fields, reviewNcsDetail.value);" in script
    assert 'id="jdReviewStatus" class="sub" role="status" aria-live="polite"' in html

    assert 'id="interviewMethodSelect" class="dropdown"' in html
    assert 'name="interviewMethod"' not in html
    assert "const interviewMethodSelect = document.getElementById('interviewMethodSelect');" in script
    assert "const SUPPORTED_INTERVIEW_METHODS = Object.freeze([" in script
    assert '<option value="발표면접">발표면접</option>' not in html
    supported_methods = script.split(
        "const SUPPORTED_INTERVIEW_METHODS = Object.freeze([", 1
    )[1].split("]);", 1)[0]
    authoring_guides = script.split(
        "const INTERVIEW_METHOD_AUTHORING_GUIDES = Object.freeze({", 1
    )[1].split("});", 1)[0]
    assert "'발표면접'" not in supported_methods
    assert "발표면접:" not in authoring_guides
    assert "const INTERVIEW_METHOD_AUTHORING_GUIDES = Object.freeze({" in script
    assert "function renderInterviewMethodGuide()" in script
    assert "공통 면접 기본원칙과 선택한 면접기법의 지침이 AI 작성에만 반영" in script
    assert "통과·탈락 규칙으로 쓰이지 않습니다" in script
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


def test_required_upload_ux_uses_openai_byok_contract() -> None:
    html, script = _page()
    assert "OpenAI API 키 (필수)" in html
    assert "OpenAI · NCS MCP 확정·로컬 정렬 → Terra 작성 → Sol 검수·재생성" in html
    assert "OpenRouter · Ox Alpha" not in html
    assert "serverEnvApiStatusValid" not in script
    assert "openrouter_api" not in script
    assert "if (key.startsWith('sk-')) return 'openai_api'" in script


def test_reviewed_notice_is_reused_without_a_second_generation_parse() -> None:
    _, script = _page()

    assert "let noticeReviewPayload = null" in script
    assert "noticeReviewPayload = data" in script
    assert "review_session_id: data.review_session_id || ''" in script
    assert "review_confirmed: true" in script
    assert "fd.append('notice_review_json', JSON.stringify(noticeReviewPayload))" in script


def test_document_review_surfaces_truthful_parser_provenance() -> None:
    _, script = _page()

    assert "function documentParserLabel(payload)" in script
    assert "if (parser === 'kordoc')" in script
    assert "pdf_text_fallback: 'PDF 대체 파서'" in script
    assert "`${parserLabel} 분석 완료 · 검토 필요" in script
    assert "parser: data.parser || 'unknown'" in script
    assert "parser_version: data.parser_version || ''" in script
