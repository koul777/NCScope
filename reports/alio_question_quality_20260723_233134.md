# ALIO Question Quality - 20260723_233134

- Documents attempted: 28
- Documents evaluated: 19
- Resolved benchmark modes: template=28
- Documents strict source-explicit coverage + template-ready: 11
- Documents passed model-origin quality gate: 0
- Documents passed model-origin quality + strict coverage: 0
- Documents with strict source-explicit detail coverage: 11
- Documents question-ready with unit-name recovery: 1
- Documents question-ready with contextual detail recovery: 4
- Unit-name resolved detail labels: 2
- Explicit detail-source documents: 20
- Contextual detail-source documents: 4
- Unmatched detail labels: 19
- Skipped detail labels due to per-doc limit: 0
- Documents with manual-review suggestions: 8
- Evaluated questions after adjustment: 114
- Template-adjusted ready questions: 114
- Model candidate questions received: 0
- Model-origin questions evaluated: 0
- Model-origin ready questions: 0
- Model questions replaced by template: 0
- Template questions inserted without model candidate: 114
- Template fallback questions: 114
- Template fallback ready questions: 114
- Average template-adjusted document score: 1.0
- Average strict coverage-adjusted score: 0.58

> This report distinguishes template-fallback compliance from model-origin generation quality. If `Model-origin questions evaluated` is 0, method readiness below measures deterministic fallback templates, not LLM output.

| idx | status | model pass | full pass | template pass | mode | source | details | exact | unit-name | unmatched | skipped | adjusted q | ready | model cand | model kept | model repl | inserted | fallback q | tpl score | strict score | unresolved details |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 303039 | template_ready_unit_name_resolved | False | False | False | template | explicit | 15 | 13 | 2 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 |  |
| 303037 | no_exact_units | False | False | False | template | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화〮관광정책 |
| 303036 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303035 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303034 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303033 | template_ready_partial_detail_coverage | False | False | False | template | explicit | 4 | 2 | 0 | 2 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 | 간호수행; 간호행정관리 |
| 303031 | no_exact_units | False | False | False | template | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화・관광정책 |
| 303019 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303018 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303017 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303016 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303014 | template_ready | False | False | True | template | explicit | 4 | 4 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303013 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303012 | template_ready | False | False | True | template | explicit | 2 | 2 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303010 | template_ready_contextual_detail | False | False | False | template | contextual | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 |  |
| 303004 | template_ready_contextual_detail | False | False | False | template | contextual | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 |  |
| 303003 | template_ready | False | False | True | template | explicit | 2 | 2 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303002 | template_ready | False | False | True | template | explicit | 2 | 2 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 303001 | template_ready_contextual_detail | False | False | False | template | contextual | 2 | 2 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 |  |
| 302998 | template_ready | False | False | True | template | explicit | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 302997 | template_ready_partial_detail_coverage | False | False | False | template | explicit | 6 | 2 | 0 | 4 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 | 간호업무 보조; 간호행정 보조; 재원환자 관리; 응급 환자 관리 |
| 302996 | template_ready_partial_detail_coverage | False | False | False | template | explicit | 8 | 4 | 0 | 4 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 | 유지관리; 건축감리; 간호업무 보조; 간호행정 보조 |
| 302994 | template_ready | False | False | True | template | explicit | 2 | 2 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 1.0 |  |
| 302984 | no_exact_units | False | False | False | template | explicit | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 영상의학; 임상병리; 간호업무 보조; 간호행정 보조 |
| 302977 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 302960 | no_exact_units | False | False | False | template | explicit | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 간호수행; 간호행정관리 |
| 302959 | template_ready_contextual_detail | False | False | False | template | contextual | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 0 | 0 | 0 | 6 | 6 | 1.0 | 0 |  |
| 302936 | no_exact_units | False | False | False | template | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 임상병리 |

## Method Quality

| method | questions | ready | ready rate | model | fallback | official sample format fails |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 경험면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |
| 발표면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |
| 상황면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |
| 인바스켓면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |
| 직무지식면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |
| 토론면접 | 19 | 19 | 1.0 | 0 | 19 | 0 |

## Question Source

| source | questions |
| --- | ---: |
| template_fallback | 114 |

## Model Fallback Reasons

| reason | questions |
| --- | ---: |
| no_model_question | 114 |

## Manual Review Suggestions

- `303037`: 문화〮관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage
- `303033`: 간호수행: manual-review-only: no exact local/public NCS hit for nursing-performance label / 간호행정관리: manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `303031`: 문화・관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage
- `302997`: 간호업무 보조: manual-review-only: nearby 요양지원 units include 0601010801_23v3 진료지원보조, 0601010802_23v3 물품전달, 0601010803_23v3 환자이송지원, 0601010808_23v3 사고예방지원; do not count as exact coverage without human selection / 간호행정 보조: manual-review-only: no exact local NCS hit; broad 병원행정 candidates are too weak for automatic coverage / 재원환자 관리: false friend: element-level 재원환자 관리하기 belongs to 0601020110_16v2 진료비관리 under 병원행정; keep unresolved in clinical nursing context / 응급 환자 관리: manual-review-only: source-like 0602020000_17v1 is not available in local MCP; 응급환자 searches return rescue/industrial units, not nursing
- `302996`: 유지관리: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage; do not borrow broad maintenance suggestions automatically / 건축감리: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail coverage / 간호업무 보조: manual-review-only: nearby 요양지원 units include 0601010801_23v3 진료지원보조, 0601010802_23v3 물품전달, 0601010803_23v3 환자이송지원, 0601010808_23v3 사고예방지원; do not count as exact coverage without human selection / 간호행정 보조: manual-review-only: no exact local NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `302984`: 영상의학: manual-review-only: no exact local/public NCS unit hit for human radiology context / 임상병리: false friend: public NCS search returns animal/nonclinical pathology hits, not human clinical laboratory context / 간호업무 보조: manual-review-only: nearby 요양지원 units include 0601010801_23v3 진료지원보조, 0601010802_23v3 물품전달, 0601010803_23v3 환자이송지원, 0601010808_23v3 사고예방지원; do not count as exact coverage without human selection / 간호행정 보조: manual-review-only: no exact local NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `302960`: 간호수행: manual-review-only: no exact local/public NCS hit for nursing-performance label / 간호행정관리: manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `302936`: 임상병리: false friend: public NCS search returns animal/nonclinical pathology hits, not human clinical laboratory context

CSV: `reports\alio_question_quality_20260723_233134.csv`
Question CSV: `reports\alio_question_quality_items_20260723_233134.csv`
