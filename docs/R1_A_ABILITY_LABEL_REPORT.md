# R1-A 능력단위 라벨 분리·정리 오류 수정 보고

- 작업일: 2026-10-23 / 구현 A: Codex
- 브랜치: `perf/r1-codex` / 기준 커밋: `d3cb206`
- 비교 범위: 홀드아웃을 제외한 dev 168건. 전체 206건의 baseline-r0 수치와 혼용하지 않음.
- 가설: 표 셀 경계·글머리표·코드·줄바꿈·동등 구두점의 표기 손실이 정확 일치 연결을 막는다. 원문에 있는 단어를 보존하여 복구하면 의미 매칭 없이 연결을 개선할 수 있다.

## 바꾼 파일과 이유

| 파일 | 변경 |
|---|---|
| `app/services/kordoc_parser.py` | 능력단위로 명시된 표의 셀 경계 보존, 10자리 코드와 NCS 접두어·버전 분리, `¡`·사각 글머리표 제거, 쉼표 목록 안에서 정확한 공식명으로 이어지는 줄바꿈 복구. 가상 표의 괄호 제목은 기존처럼 연결. |
| `app/main.py` | 능력단위의 정확 일치 잠금·유일 세분류 소속 확인에만 가운뎃점 동등 표기 적용. 세분류 정규화 함수, 단어, 유일 세분류/기본 코드 검사 유지. |
| `tests/test_ability_label_boundaries.py` | 원문 최소 조각으로 12개 테스트 함수, 21개 케이스 추가. 기존 테스트 수정 없음. |
| `docs/R1_A_ABILITY_LABEL_REPORT.md` | 유형 집계, dev 비교, 검증 결과와 남은 한계 기록. |

운영 코드 변경은 추가·삭제 합계 63줄이다. 줄바꿈 복구는 기존 공식명 색인을 추출 경계 판별에만 사용한다. 능력단위 확정과 KSA는 기존 MCP 검증을 거치며, 유사어·편집거리·임계값 완화는 추가하지 않았다.

## 기준선 라벨 유형 집계

단위는 문서별 진단 라벨 발생 건수이다. 동일 라벨이 다른 문서에 있으면 각각 센다. 구조적 흔적을 우선 분류하고, 나머지는 `ability_mapping_states`의 현행 공식명 존재 여부로 분류했다. 미확인 항목을 기관 자체명으로 단정하지 않았다.

| 유형 | 모호 | 미일치 | 합계 |
|---|---:|---:|---:|
| 코드 경계 미분리 | 5 | 0 | 5 |
| 인접 표 셀 연결 | 8 | 0 | 8 |
| 글머리표 기호 잔재 | 10 | 0 | 10 |
| 줄바꿈 분절(원문 확인) | 2 | 4 | 6 |
| 공식명 구두점 표기 차이 | 3 | 10 | 13 |
| 세부그룹 접두어 포함 | 78 | 85 | 163 |
| 공식명 존재·범위/코드 미해결 | 32 | 13 | 45 |
| 그 외 현행 공식명 미일치(자체명·구명칭·미확인 포함) | 241 | 225 | 466 |
| **합계** | **379** | **337** | **716** |

세부그룹 접두어와 공식명이 아닌 기관 명칭은 보존했다. 예: `회의운영·지원`, `환자대응관리`, `환자서비스관리`는 원문에도 같은 명칭이므로 미일치 유지. 유형별 전체 행은 로컬 `../.tmp/r1_analysis/label_classification.csv`에 있다.

## dev 벤치마크 전후

| 집계 | 기준선 | 수정 후 | 변화 |
|---|---:|---:|---:|
| `ability_units_exact` | 585 | 600 | +15 |
| `ability_units_ambiguous` | 379 | 361 | -18 |
| `ability_units_unmatched` | 337 | 324 | -13 |
| `strict_pipeline_ready_documents` | 27 | 28 | +1 |

| 목표 KPI (dev) | 기준선 | 수정 후 | 변화(%p) |
|---|---:|---:|---:|
| `ability_exact_of_scoped_pct` | 63.45% | 64.94% | +1.49 |
| `ability_end_to_end_pct` | 44.97% | 46.69% | +1.72 |
| `strict_pipeline_ready_of_detail_docs_pct` | 26.47% | 27.45% | +0.98 |
| `ability_official_code_candidate_pct` | 93.54% | 93.43% | -0.11 |

168건 파일명과 SHA-256 일치를 확인했다. 세분류 관련 행 변화: 0건. KSA 가용률: 100.00%.

## 판정·라벨이 바뀐 문서

정확/모호/미일치는 문서별 라벨 건수이며, 전→후 순서이다. 전체 라벨 추가·제거 목록은 `../.tmp/r1_analysis/dev_comparison.json`에 기록했다.

| 파일명 | 정확 | 모호 | 미일치 | 상태 |
|---|---:|---:|---:|---|
| 303812_1_직무기술서(2026년도 하반기 한국농업기술진흥원 정규직 공개경쟁채용 제한경쟁채용 및 사회형평채용).pdf | 72→72 | 1→0 | 1→2 | ability_ambiguous→ability_mismatch |
| 303898_1_[별첨] 직무설명자료.hwp | 0→0 | 8→8 | 0→0 | no_detail |
| 303913_1_NCS 기반 채용 직무 설명 자료.hwp | 2→4 | 0→0 | 4→2 | ability_mismatch |
| 303954_1_붙임) 직무설명자료.hwp | 0→0 | 2→2 | 0→0 | no_detail |
| 303984_1_[붙임3] NCS 기반 채용 직무 설명자료.hwp | 2→4 | 0→0 | 4→2 | ability_mismatch |
| 304000_1_직무기술서(기술직).hwp | 2→2 | 1→0 | 0→0 | ability_ambiguous→official_exact_with_self_developed |
| 304021_1_(붙임2) NCS 기반 채용 직무 설명자료.hwp | 7→8 | 0→0 | 6→5 | ability_mismatch |
| 304024_1_직무기술서.pdf | 2→2 | 1→0 | 1→1 | ability_ambiguous→ability_mismatch |
| 304038_1_붙임 1. 직무기술서(시설관리).hwp | 32→36 | 28→25 | 3→2 | ability_ambiguous |
| 304039_1_직무기술서(운전 및 비서).zip | 18→19 | 0→0 | 1→0 | ability_mismatch→mcp_exact |
| 304043_1_4. NCS 기반 채용 직무 설명자료.hwp | 3→5 | 0→0 | 4→2 | ability_mismatch |
| 304051_1_(직무기술서) 2026년 국립생태원 하반기 체험형 청년인턴 채용 직무기술서.pdf | 34→36 | 23→15 | 6→2 | ability_ambiguous |
| 304061_1_직무기술서(기술직).hwp | 2→2 | 1→0 | 0→0 | ability_ambiguous→official_exact_with_self_developed |
| 304062_1_직무기술서(행정직).hwp | 3→3 | 1→0 | 1→1 | ability_ambiguous→ability_mismatch |
| 304063_1_직무기술서(기술직).hwp | 2→2 | 1→0 | 0→0 | ability_ambiguous→official_exact_with_self_developed |
| 304071_1_직무기술서(행정직).hwp | 3→3 | 1→0 | 1→1 | ability_ambiguous→ability_mismatch |
| 304096_1_직무기술서(일반사무).hwp | 6→7 | 4→4 | 3→2 | detail_mismatch |
| 304107_1_직무기술서(2026년 제1차 경북 상주 실증단지 일반계약직 채용 공고).pdf | 9→9 | 0→0 | 4→4 | ability_mismatch |

- `304000_1_직무기술서(기술직).hwp`: `주거용건물 유지관리 1002020103. 주택 안전관리` 합성 라벨을 제거하고 원문 두 능력단위를 유지했다. 동일 원문의 304061·304063에도 적용됐다.
- `304051_1_(직무기술서) 2026년 국립생태원 하반기 체험형 청년인턴 채용 직무기술서.pdf`: 셀 사이 합성 라벨 7개를 제거하고 `경영평가활동 수행`, `경영평가 모니 터링` 등 줄바꿈을 복구했다. 띄어쓰기만 복구하며 원문 셀 증거는 보존했다.
- `304039_1_직무기술서(운전 및 비서).zip`: 원문 `승객 승･하차지원`과 MCP `승객 승ㆍ하차지원`의 동등 구두점을 정확 일치 비교에 반영했다.

## 나빠진 지표·남은 위험

- 코드 후보율은 **93.54%→93.43%**로 소폭 하락했다. 목표 KPI 전체 통과를 주장하지 않는다. 원문 복구로 현행 공식명 분모가 635→639, 코드 후보 분자가 594→597가 됐다.
- 원인은 304051의 `시설원예 생리장해관리` 복구다. 원문에 `08. 시설원예`가 있지만 세분류가 같은 행의 다른 셀에 붙은 `세분류 01. 조경설계, 02. 조경시공, 03. 조경관리` 구조여서 기존 세분류 추출기가 이를 놓친다. 복구된 능력단위는 `official_exact_scope_conflict` 진단으로 남겼고, 임의 코드 확정이나 세분류 추정은 하지 않았다. 세분류 추출 문제는 이 과제에서 변경하지 않았다.
- 303812의 `제작편집 ¡` 정리 후 모호 1→0, 미일치 1→2로 이동한다. 정확 일치 72건과 미해결 총 2건은 동일하다. 비공식 그룹 접두어 `영상제작:`을 제거해 공식명으로 자동 확정하지 않았다.
- 중간 후보에서 303986의 괄호 제목이 가상 셀 경계로 나뉘어 정확 50→41로 감소했다. 회귀 테스트를 추가하여 수정했고 최종 후보에서는 원래 50건을 보존했다.
- 홀드아웃 문서·guardian_only 내용은 열지 않았다. 홀드아웃 정밀도/재현율 및 전체 206건 KPI는 리드의 독립 측정·판정이 남아 있다. 이 구현자는 병합 여부를 판단하지 않는다.
- 로컬 테스트와 벤치마크를 함께 실행했으므로 독립 지연시간 가드 측정으로 사용하지 않는다.

## 테스트와 변경 범위 검사

수정 전에 각 오류의 실패를 확인했다: 첫 7건, 구두점·글머리표 5건, 가상 표 제목 1건, NCS 접두어·버전 2건. 실패 로그는 `.tmp/r1_analysis/regression_before.txt`와 `regression_*_before.txt`에 있다.

```powershell
$env:NCS_MCP_URL='http://mcp.example/mcp'
$env:OPENAI_NET_CHECK_ENABLED='false'
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_ability_label_boundaries.py tests/test_kordoc_parser.py tests/test_mcp_only_policy.py
python -m ruff check --select F821 app scripts tests
python scripts/perf_kpi.py diff-policy --base d3cb206
```

- 최종 전체 테스트: **5671 passed, 76 skipped in 186.41s (0:03:06)**. 기존 skip 76건 유지, skip/xfail 추가 없음.
- 관련 테스트: **341 passed**. F821 검사 통과.
- 중간 전체 테스트 1회에서 실행 중 소스 편집으로 캐시된 런타임 해시와 파일 해시가 달라졌다. 소스를 고정한 최종 전체 실행으로 재검증했으며 테스트나 해시 검사는 수정하지 않았다.
- 벤치마크 동시 실행 중 기존 병렬성 테스트가 `max_active=1`로 1회 실패했다. 관련 테스트 실행에서는 통과했으며, 최종 전체 테스트는 자체 벤치마크가 모두 종료된 뒤 단독 실행했다. 단언·테스트·동시성 구현은 수정하지 않았다.
- 최종 diff-policy JSON: `../.tmp/r1_analysis/diff_policy.json` (`passed: true`, `violations: []`, `review_required: []`). 동결 파일 수정 없음.

추가 회귀 테스트 이름:

- `test_full_unit_codes_separate_adjacent_ability_labels`
- `test_full_unit_codes_keep_nonofficial_source_name`
- `test_full_unit_code_prefix_and_version_are_not_labels_or_ordinals`
- `test_pdf_inverted_exclamation_bullets_do_not_leak_into_labels`
- `test_pdf_private_use_square_bullets_are_only_removed_at_item_boundaries`
- `test_exact_unit_lock_accepts_equivalent_korean_middle_dot_glyphs`
- `test_middle_dot_normalization_does_not_relax_names_or_code_ambiguity`
- `test_adjacent_table_cells_do_not_create_synthetic_ability_label`
- `test_layout_inferred_table_does_not_split_parenthetical_detail_heading`
- `test_comma_delimited_official_label_preserves_layout_wrap_as_whitespace`
- `test_wrapped_labels_keep_source_cell_evidence`
- `test_source_names_and_unproven_line_boundaries_are_not_rewritten`

## 재현·검증 산출물

```powershell
$env:NCS_MCP_URL='http://127.0.0.1:8778/mcp'
python scripts/benchmark_stored_jd_corpus.py --input-dir C:/workspace/NCScope/tmp/stored_jd_dev_corpus --report-dir .tmp/r1_bench
```

- 최종 후보 CSV: `.tmp/r1_bench/stored_jd_benchmark_20261023_064704.csv`
- 최종 후보 summary: `.tmp/r1_bench/stored_jd_benchmark_20261023_064704.json`
- dev 기준선 재집계: `.tmp/r1_analysis/baseline_dev_summary.json`
- 문서별 비교: `.tmp/r1_analysis/dev_changed_documents.csv`, `.tmp/r1_analysis/dev_comparison.json`
- 유형별 716행: `.tmp/r1_analysis/label_classification.csv`
- 최종 전체 테스트 로그: `.tmp/r1_analysis/full_pytest_frozen.txt`
- `.tmp/`의 원문 추출 조각과 검증 산출물은 로컬에 보존하며 git에는 포함하지 않는다.
