# ALIO Question Quality - 20260724_045154

- Documents attempted: 8
- Documents evaluated: 4
- Resolved benchmark modes: model=8
- Interview methods requested: 경험면접, 상황면접, 발표면접, 토론면접, 인바스켓면접, 직무지식면접 (8)
- Documents strict source-explicit coverage + template-ready: 2
- Documents passed model-origin quality gate: 4
- Documents passed model-origin quality + strict coverage: 2
- Documents with strict source-explicit detail coverage: 2
- Documents question-ready with unit-name recovery: 1
- Documents question-ready with contextual detail recovery: 0
- Unit-name resolved detail labels: 2
- Explicit detail-source documents: 6
- Contextual detail-source documents: 0
- Unmatched detail labels: 4
- Skipped detail labels due to per-doc limit: 0
- Documents with manual-review suggestions: 5
- Evaluated questions after adjustment: 24
- Template-adjusted ready questions: 24
- Model candidate questions received: 24
- Model-origin questions evaluated: 24
- Model-origin ready questions: 24
- Fully model-preserved ready questions: 24
- Fully model-preserved questions: 24
- Model-main with repaired follow-ups: 0
- Model-main with template follow-ups: 0
- Model questions replaced by template: 0
- Template questions inserted without model candidate: 0
- Template fallback questions: 0
- Template fallback ready questions: 0
- Average template-adjusted document score: 1.0
- Average strict coverage-adjusted score: 0.5

> This report distinguishes template-fallback compliance from model-origin generation quality. If `Model-origin questions evaluated` is 0, method readiness below measures deterministic fallback templates, not LLM output.

| idx | status | model pass | full pass | template pass | mode | source | details | exact | unit-name | unmatched | skipped | adjusted q | ready | model cand | model-origin | full model | repaired fu | template fu | model repl | inserted | fallback q | tpl score | strict score | unresolved details |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 303039 | template_ready_unit_name_resolved | True | False | False | model | explicit | 15 | 13 | 2 | 0 | 0 | 6 | 6 | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1.0 | 0 |  |
| 303037 | no_exact_units | False | False | False | model | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화〮관광정책 |
| 303036 | parsed_no_detail | False | False | False | model |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303035 | parsed_no_detail | False | False | False | model |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303034 | ok_model | True | True | True | model | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1.0 | 1.0 |  |
| 303033 | template_ready_partial_detail_coverage | True | False | False | model | explicit | 4 | 2 | 0 | 2 | 0 | 6 | 6 | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1.0 | 0 | 간호수행; 간호행정관리 |
| 303031 | no_exact_units | False | False | False | model | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화・관광정책 |
| 303019 | ok_model | True | True | True | model | explicit | 1 | 1 | 0 | 0 | 0 | 6 | 6 | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1.0 | 1.0 |  |

## Method Quality

| method | questions | ready | ready rate | full model | model-main + repaired follow-ups | model-main + template follow-ups | fallback | official sample format fails |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 경험면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |
| 발표면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |
| 상황면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |
| 인바스켓면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |
| 직무지식면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |
| 토론면접 | 4 | 4 | 1.0 | 4 | 0 | 0 | 0 | 0 |

## Quality Issues By Method

No quality issues recorded.

## Question Source

| source | questions |
| --- | ---: |
| model | 24 |

## Manual Review Suggestions

- `303037`: 문화〮관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage
- `303036`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail
- `303035`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail
- `303033`: 간호수행: manual-review-only: no exact local/public NCS hit for nursing-performance label / 간호행정관리: manual-review-only: no exact local/public NCS hit; broad 병원행정 candidates are too weak for automatic coverage
- `303031`: 문화・관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage

CSV: `reports\alio_question_quality_20260724_045154.csv`
Question CSV: `reports\alio_question_quality_items_20260724_045154.csv`
