# ALIO Question Quality - 20260724_052004

- Documents attempted: 8
- Documents evaluated: 4
- Resolved benchmark modes: template=8
- Interview methods requested: 경험면접, 상황면접, 발표면접, 토론면접, 인바스켓면접, 직무지식면접 (8)
- Documents strict source-explicit coverage + template-ready: 2
- Documents passed model-origin quality gate: 0
- Documents passed model-origin quality + strict coverage: 0
- Documents with strict source-explicit detail coverage: 2
- Documents question-ready with unit-name recovery: 0
- Documents question-ready with contextual detail recovery: 0
- Unit-name resolved detail labels: 1
- Explicit detail-source documents: 6
- Contextual detail-source documents: 0
- Unmatched detail labels: 4
- Skipped detail labels due to per-doc limit: 11
- Coverage blocker types: catalog_gap_or_nonstandard_source_label=2, parsed_no_detail=2, skipped_by_max_details_per_doc=11, specialized_healthcare_label_unserved_by_mcp=2, unit_name_only=1
- Documents with manual-review suggestions: 5
- Evaluated questions after adjustment: 12
- Template-adjusted ready questions: 12
- Model candidate questions received: 0
- Model-origin questions evaluated: 0
- Model-origin ready questions: 0
- Fully model-preserved ready questions: 0
- Fully model-preserved questions: 0
- Model-main with repaired follow-ups: 0
- Model-main with template follow-ups: 0
- Model questions replaced by template: 0
- Template questions inserted without model candidate: 12
- Template fallback questions: 12
- Template fallback ready questions: 12
- Average template-adjusted document score: 1.0
- Average strict coverage-adjusted score: 0.5

> This report distinguishes template-fallback compliance from model-origin generation quality. If `Model-origin questions evaluated` is 0, method readiness below measures deterministic fallback templates, not LLM output.

| idx | status | model pass | full pass | template pass | mode | source | details | exact | unit-name | unmatched | skipped | adjusted q | ready | model cand | model-origin | full model | repaired fu | template fu | model repl | inserted | fallback q | tpl score | strict score | unresolved details |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 303039 | template_ready_partial_detail_coverage | False | False | False | template | explicit | 15 | 3 | 1 | 0 | 11 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 3 | 1.0 | 0 | 식음료접객; 한식조리; 양식조리; 중식조리; 일식조리; 복어조리; 제과; 제빵; 카지노 영업 지원; 카지노운영관리; 사무행정 |
| 303037 | no_exact_units | False | False | False | template | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화〮관광정책 |
| 303036 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303035 | parsed_no_detail | False | False | False | template |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303034 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 3 | 1.0 | 1.0 |  |
| 303033 | template_ready_partial_detail_coverage | False | False | False | template | explicit | 4 | 2 | 0 | 2 | 0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 3 | 1.0 | 0 | 간호수행; 간호행정관리 |
| 303031 | no_exact_units | False | False | False | template | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화・관광정책 |
| 303019 | template_ready | False | False | True | template | explicit | 1 | 1 | 0 | 0 | 0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 3 | 1.0 | 1.0 |  |

## Method Quality

| method | questions | ready | ready rate | full model | model-main + repaired follow-ups | model-main + template follow-ups | fallback | official sample format fails |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 경험면접 | 4 | 4 | 1.0 | 0 | 0 | 0 | 4 | 0 |
| 발표면접 | 4 | 4 | 1.0 | 0 | 0 | 0 | 4 | 0 |
| 상황면접 | 4 | 4 | 1.0 | 0 | 0 | 0 | 4 | 0 |

## Quality Issues By Method

No quality issues recorded.

## Question Source

| source | questions |
| --- | ---: |
| template_fallback | 12 |

## Model Fallback Reasons

| reason | questions |
| --- | ---: |
| no_model_question | 12 |

## Manual Review Suggestions

- `303037`: 문화〮관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage
- `303036`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail
- `303035`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail
- `303033`: 간호수행: manual-review-only: no exact local/public NCS hit for nursing-performance label / 간호행정관리: manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `303031`: 문화・관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage

CSV: `reports\alio_question_quality_20260724_052004.csv`
Question CSV: `reports\alio_question_quality_items_20260724_052004.csv`
