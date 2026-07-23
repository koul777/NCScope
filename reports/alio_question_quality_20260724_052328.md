# ALIO Question Quality - 20260724_052328

- Documents attempted: 4
- Documents evaluated: 1
- Resolved benchmark modes: model=4
- Interview methods requested: 경험면접, 상황면접, 발표면접, 토론면접, 인바스켓면접, 직무지식면접 (4)
- Documents strict source-explicit coverage + template-ready: 0
- Documents passed model-origin quality gate: 1
- Documents passed model-origin quality + strict coverage: 0
- Documents with strict source-explicit detail coverage: 0
- Documents question-ready with unit-name recovery: 1
- Documents question-ready with contextual detail recovery: 0
- Unit-name resolved detail labels: 2
- Explicit detail-source documents: 2
- Contextual detail-source documents: 0
- Unmatched detail labels: 1
- Skipped detail labels due to per-doc limit: 0
- Coverage blocker types: catalog_gap_or_nonstandard_source_label=1, parsed_no_detail=2, unit_name_only=2
- Documents with manual-review suggestions: 3
- Evaluated questions after adjustment: 6
- Template-adjusted ready questions: 6
- Model candidate questions received: 6
- Model-origin questions evaluated: 6
- Model-origin ready questions: 6
- Fully model-preserved ready questions: 6
- Fully model-preserved questions: 6
- Model-main with repaired follow-ups: 0
- Model-main with template follow-ups: 0
- Model questions replaced by template: 0
- Template questions inserted without model candidate: 0
- Template fallback questions: 0
- Template fallback ready questions: 0
- Average template-adjusted document score: 1.0
- Average strict coverage-adjusted score: 0.0

> This report distinguishes template-fallback compliance from model-origin generation quality. If `Model-origin questions evaluated` is 0, method readiness below measures deterministic fallback templates, not LLM output.

| idx | status | model pass | full pass | template pass | mode | source | details | exact | unit-name | unmatched | skipped | adjusted q | ready | model cand | model-origin | full model | repaired fu | template fu | model repl | inserted | fallback q | tpl score | strict score | unresolved details |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 303039 | template_ready_unit_name_resolved | True | False | False | model | explicit | 15 | 13 | 2 | 0 | 0 | 6 | 6 | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1.0 | 0 |  |
| 303037 | no_exact_units | False | False | False | model | explicit | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 문화〮관광정책 |
| 303036 | parsed_no_detail | False | False | False | model |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |
| 303035 | parsed_no_detail | False | False | False | model |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |  |

## Method Quality

| method | questions | ready | ready rate | full model | model-main + repaired follow-ups | model-main + template follow-ups | fallback | official sample format fails |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 경험면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |
| 발표면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |
| 상황면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |
| 인바스켓면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |
| 직무지식면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |
| 토론면접 | 1 | 1 | 1.0 | 1 | 0 | 0 | 0 | 0 |

## Quality Issues By Method

No quality issues recorded.

## Question Source

| source | questions |
| --- | ---: |
| model | 6 |

## Manual Review Suggestions

- `303037`: 문화〮관광정책: manual-review-only: explicit JD label, but current local NCS_MCP has no exact detail or unit-name coverage
- `303036`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail
- `303035`: parsed-no-detail: multi_role_healthcare_document_without_explicit_ncs_detail

CSV: `reports\alio_question_quality_20260724_052328.csv`
Question CSV: `reports\alio_question_quality_items_20260724_052328.csv`
