# 성능 KPI와 가드레일

- 작성: 2026-10-23
- 통합 브랜치: `perf/kpi-guardrails-20261023`
- 측정 도구: `scripts/perf_kpi.py` (테스트: `tests/test_perf_kpi.py`)
- 팀 작업 규칙: 루트 `AGENTS.md`

## 성능의 범위

README "세분류 우선 성능 해석"과 같은 순서로 봄.

1. 세분류 정확도: 직무기술서에 적힌 현행 공식 세분류명과 코드 복구
2. 능력단위·KSA 연결: 확정 세분류에서 공식 능력단위와 KSA까지 이어지는 비율
3. 문항 품질: 결정적 템플릿과 품질 게이트를 통과하는 문항 비율
4. 속도: 문서 파싱, 저장 JD 벤치마크, 템플릿 문항 생성·검수 시간

유료 모델(OpenAI, OpenRouter) 생성 품질은 이 KPI에 포함하지 않음. 개선 라운드에서
유료 API를 호출하지 않기 때문임.

## 측정 방법

```
python scripts/perf_kpi.py snapshot --label <이름>
python scripts/perf_kpi.py compare <기준선>/snapshot.json <후보>/snapshot.json --output <비교.json>
python scripts/perf_kpi.py diff-policy --base <기준 커밋>
```

`snapshot` 단계

| 단계 | 내용 | 필요한 로컬 자원 |
|---|---|---|
| tests | 전체 pytest (CI와 같은 오프라인 환경변수) | 없음 |
| lint | `ruff check --select F821 app scripts tests` | ruff |
| audit | `scripts/audit_ncs_detail_connections.py` | 없음 |
| stored_jd | 저장 JD 206건 벤치마크, 문서별 파싱 시간 | `tmp/alio_jd_200_mcp`, Kordoc(`npm ci`), 로컬 NCS MCP |
| holdout | 동결 36건 채점과 `check_stored_jd_quality_gate.py` | `tmp/stored_jd_final_blind/final_blind_manifest.json` |
| method_matrix | NCS DB 72개 KSA 표본 × 면접유형 7종 = 504문항 템플릿 생성·검수 | `C:/workspace/NCS_MCP/data/processed/ncs.db` |

로컬 NCS MCP는 `http://127.0.0.1:8778/mcp`(읽기 전용)를 씀. worktree에서 실행하면 코퍼스,
매니페스트, NCS DB를 main 체크아웃 기준으로 찾음.

홀드아웃 채점의 문서별 오류는 `<run_dir>/guardian_only/`에만 저장되고, `snapshot.json`에는
집계값만 들어감. 구현자에게는 홀드아웃 문서(SHA-256 36개, 같은 내용의 중복 파일 포함 38개)를
뺀 `stored_jd_dev_rows.csv`(168건)와 같은 168개 파일만 복사한 `tmp/stored_jd_dev_corpus`만
넘김.

## KPI와 기준선

기준선은 통합 브랜치 `f391db4`에서 다른 작업 없이 측정한 `baseline-r0` 값임.

| 구분 | KPI | 키 | 기준선 | 방향 | 하한·허용 |
|---|---|---|---:|---|---|
| guard | 테스트 실패 | `tests.failed` | 0 | 낮을수록 좋음 | 상한 0 |
| guard | 테스트 오류 | `tests.errors` | 0 | 낮을수록 좋음 | 상한 0 |
| guard | 테스트 통과 수 | `tests.passed` | 5650 | 높을수록 좋음 | 하락 금지 |
| guard | ruff F821 위반 | `lint.f821_violations` | 0 | 낮을수록 좋음 | 상한 0 |
| guard | 세분류-능력단위 연결 감사 | `audit.detail_connections_passed` | 1 | 높을수록 좋음 | 하한 1 |
| guard | 저장 JD 파싱 성공률 | `stored_jd.parse_success_pct` | 100 | 높을수록 좋음 | 하한 100 |
| guard | 현행 공식 세분류 인식률 | `stored_jd.current_official_detail_recognition_pct` | 100 | 높을수록 좋음 | 하한 90 |
| guard | 세분류 매핑 상태 커버리지 | `stored_jd.detail_mapping_state_coverage_pct` | 100 | 높을수록 좋음 | 하한 100 |
| guard | 현행 공식 세분류 문서 완전일치율 | `stored_jd.documents_all_current_official_details_exact_pct` | 100 | 높을수록 좋음 | 하한 80 |
| guard | 능력단위 매핑 상태 커버리지 | `stored_jd.ability_mapping_state_coverage_pct` | 100 | 높을수록 좋음 | 하락 금지 |
| guard | KSA 조회 가능률 | `stored_jd.ksa_available_pct` | 100 | 높을수록 좋음 | 하락 금지 |
| guard | 저장 JD 품질 게이트 | `holdout.gate_passed` | 1 | 높을수록 좋음 | 하한 1 |
| guard | 홀드아웃 세분류 코드 정밀도 | `holdout.detail_codes_precision_pct` | 96.2 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 세분류 코드 재현율 | `holdout.detail_codes_recall_pct` | 100 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 세분류 코드 문서 완전일치 | `holdout.detail_codes_document_exact_pct` | 90.48 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 세분류명 정밀도 | `holdout.detail_labels_precision_pct` | 98.97 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 세분류명 재현율 | `holdout.detail_labels_recall_pct` | 98.97 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 능력단위 범위 정밀도 | `holdout.ability_pairs_precision_pct` | 97.04 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 능력단위 범위 재현율 | `holdout.ability_pairs_recall_pct` | 97.62 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 능력단위 코드 정밀도 | `holdout.ability_codes_precision_pct` | 91.67 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 능력단위 코드 재현율 | `holdout.ability_codes_recall_pct` | 99.59 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 KSA 재현율 | `holdout.ksa_codes_recall_pct` | 100 | 높을수록 좋음 | 하락 금지 |
| guard | 홀드아웃 문서 상태 정확도 | `holdout.document_state_accuracy_pct` | 97.22 | 높을수록 좋음 | 하락 금지 |
| guard | 면접유형 매트릭스 평균 점수 | `method_matrix.average_quality_score` | 0.997063 | 높을수록 좋음 | 하락 금지 |
| guard | 재검수 멱등성 실패 | `method_matrix.idempotence_failure_count` | 0 | 낮을수록 좋음 | 상한 0 |
| guard | 문항 표면 중복 | `method_matrix.exact_surface_duplicate_count` | 0 | 낮을수록 좋음 | 상한 0 |
| target | 세분류 후보 공식 일치율 | `stored_jd.detail_exact_pct` | 77.63 | 높을수록 좋음 | 하락 금지 |
| target | 공식 검증 대상 세분류 일치율 | `stored_jd.official_validation_detail_exact_pct` | 80.19 | 높을수록 좋음 | 하락 금지 |
| target | 공식 검증 대상 문서 완전일치율 | `stored_jd.documents_all_official_details_exact_pct` | 62.2 | 높을수록 좋음 | 하락 금지 |
| target | 범위 내 능력단위 정확 일치율 | `stored_jd.ability_exact_of_scoped_pct` | 66.17 | 높을수록 좋음 | 하락 금지 |
| target | 능력단위 종단 일치율 | `stored_jd.ability_end_to_end_pct` | 49.15 | 높을수록 좋음 | 하락 금지 |
| target | 세분류부터 KSA까지 한 번에 끝난 문서 비율 | `stored_jd.strict_pipeline_ready_of_detail_docs_pct` | 27.56 | 높을수록 좋음 | 하락 금지 |
| target | 공식 능력단위 범위 후보율 | `stored_jd.ability_official_scope_candidate_pct` | 99.01 | 높을수록 좋음 | 하락 금지 |
| target | 공식 능력단위 코드 후보율 | `stored_jd.ability_official_code_candidate_pct` | 94.62 | 높을수록 좋음 | 하락 금지 |
| target | 면접유형 매트릭스 통과율 | `method_matrix.ready_rate` | 0.926587 | 높을수록 좋음 | 하락 금지 |
| latency | 문서 파싱 p50(ms) | `stored_jd.parse_ms_p50` | 788 | 낮을수록 좋음 | +25% |
| latency | 문서 파싱 p95(ms) | `stored_jd.parse_ms_p95` | 6579 | 낮을수록 좋음 | +25% |
| latency | 저장 JD 벤치마크 전체 시간(s) | `stored_jd.wall_seconds` | 576.944 | 낮을수록 좋음 | +25% |
| latency | 템플릿 문항 생성·검수(ms/문항) | `method_matrix.ms_per_question` | 120.349 | 낮을수록 좋음 | +25% |
| latency | 전체 테스트 시간(s) | `tests.seconds` | 211.919 | 낮을수록 좋음 | +25% |

판정 규칙

| 구분 | 규칙 |
|---|---|
| guard | 기준선보다 나빠지면 불합격. 하한이 있는 KPI는 하한 미달이면 기준선과 관계없이 불합격 |
| target | 개선 목표. 기준선보다 나빠지면 불합격 |
| latency | 기준선 대비 25%까지는 허용(같은 PC에서 다른 작업과 겹칠 때 생기는 흔들림), 넘으면 불합격 |
| 공통 | 기준선에 있던 KPI가 후보에서 측정되지 않으면 불합격 |
| 재측정 | 파싱 성공률만 하한 미달이고 실패 문서가 단독 실행에서 파싱되면 같은 후보를 한 번 다시 측정함. 2026-10-23 사전 측정에서 테스트와 동시에 돌릴 때 Kordoc 일시 실패 1건이 있었음 |

하한은 기존 릴리스 기준과 같음: 현행 공식 세분류 인식률 90, 세분류 매핑 상태 100,
현행 공식 세분류 문서 완전일치 80, 테스트 실패·오류 0, ruff F821 0, 품질 게이트 통과.

## 가드레일

### KPI 판정

- `compare`가 통과해야 병합함.
- 리드만 `--allow KEY=사유`로 하나의 하락을 예외 승인할 수 있음. 사유는 비교 결과 JSON에
  기록됨. 하한 미달은 예외 승인 대상이 아님.
- 병합할 때마다 새 snapshot을 다음 라운드 기준선으로 씀. 한 번 오른 값은 다시 내려가지
  않음.

### 변경 범위 (`diff-policy`)

| 검사 | 결과 |
|---|---|
| 동결 파일 수정 (`PROTECTED_PATTERNS`) | 불합격 |
| 기존 테스트 함수 삭제·이름 변경 | 불합격 |
| `pytest.mark.skip`·`skipif`·`xfail`, `pytest.skip()` 추가 | 불합격 |
| 운영 코드 변경 800줄 초과 | 불합격 |
| 기존 단언 삭제 | 리드 검토 (같거나 더 엄격한 단언으로 바뀌었는지 확인) |
| 운영 코드(`app/`)에 벤치마크 전용 태그·평가 코퍼스 경로 추가 (`BENCHMARK_ONLY_MARKERS`: `matrix_template`, `stored_jd_final_blind` 등) | 불합격 |
| 품질 게이트 모듈, `_..._ok` 검사 함수, `_attach_question_quality_report`, `_run_runtime_question_quality_orchestration` 변경 | 리드 검토 (같은 입력에서 검사가 느슨해지지 않았는지 확인) |

벤치마크 전용 태그 규칙은 2026-10-23 1라운드에서 추가함. 한 구현자가 품질 검사 보고 함수 안에서
`question_source == "matrix_template"`(면접유형 매트릭스 벤치마크만 붙이는 태그)인 문항의 표면
문구를 바꿔 매트릭스 통과율을 올리려 했고, 리드 검토에서 거부함.

동결 파일: 평가 기준표·홀드아웃·블라인드 픽스처, `reports/`, 측정 스크립트
(`perf_kpi.py`, `benchmark_stored_jd_corpus.py`, `benchmark_question_method_matrix.py`,
`score_stored_jd_holdout.py`, `check_stored_jd_quality_gate.py`,
`audit_ncs_detail_connections.py`), NCS 카탈로그 JSON, 팀 규칙(`AGENTS.md`, `.cursor/`,
`.claude/`, 이 문서), CI·배포 설정, 의존성 파일.

### 홀드아웃 격리

- 구현자는 홀드아웃 문서와 채점 결과를 보지 않음. 문서별 분석과 벤치마크는 dev 168건
  (`tmp/stored_jd_dev_corpus`)만 사용.
- 홀드아웃 지표는 과적합을 잡는 guard로만 씀.

### 제품 불변 조건

- 세분류·능력단위·KSA는 NCS MCP 공식 결과만 사용함. 느슨한 매핑이나 로컬 샘플로 강등하지
  않음.
- 공식명과 정확히 일치하지 않는 라벨은 진단으로 표시하고, 비슷한 이름으로 자동 확정하지
  않음. target KPI를 올리려고 이 규칙을 바꾸면 불합격.
- 블라인드 채용 안전 규칙, 공식 KSA 근거(`evidence_id`) 보존.

### 실행 환경

- 유료 API 호출, `git push`, 배포, main 체크아웃 변경 금지.
- 구현자는 자기 worktree(`.local/team/<이름>`) 안에서만 파일을 바꾸고 자기 브랜치에 커밋함.
- Windows에서 새 콘솔 창을 띄우는 실행 금지. `perf_kpi.py`는 자식 프로세스를
  `CREATE_NO_WINDOW`로 실행함.

## 팀 구성

| 역할 | 실행 방법 | 맡는 일 |
|---|---|---|
| 리드·판정 | Claude Code (이 세션) | 과제 선정, worktree·브리프 준비, 측정, 판정, 병합 |
| 구현 A | `codex exec -C .local/team/codex -c notify=[] -o <결과.md> "<브리프>"` | 배정 과제 구현 |
| 구현 B | Cursor Agent CLI `agent -p --force --trust --workspace .local/team/cursor "<브리프>"` | 배정 과제 구현 |
| 교차 검토 | 상대 구현자 CLI를 읽기 전용(`codex exec -s read-only`, `agent --mode ask`)으로 실행 | 상대 변경의 회귀·과적합 확인 |
| 검토 에이전트 | Claude `ncs-question-reviewer`, `ncs-corpus-auditor` | 문항 문장, 문서별 판정 변화 확인 |

Cursor Agent는 `.cmd` 대신 `%LOCALAPPDATA%/cursor-agent/versions/<버전>/node.exe index.js`로
실행함. Git Bash가 아니라 PowerShell에서 실행해야 함(Orca가 설치한 Cursor 훅이 PowerShell
명령이라 Git Bash에서는 구문 오류로 도구 호출이 모두 막힘). 기본 모델이 월 사용 한도에 걸리면
지출 한도를 설정하지 않고 `--model auto`로 바꿈.

Codex `workspace-write` 샌드박스는 이 PC에서 anaconda 패키지 읽기가 막혀 pytest가 돌지 않음.
구현은 사용자 설정 그대로 실행하고, 읽기 전용 검토는 `-s read-only`로 실행함.

지연시간 KPI는 구현자가 동시에 측정을 돌리면 25% 넘게 흔들림. 라운드 중에는 기준·후보를 같은
시점에 번갈아 재는 교차 측정으로 확인하고 `--allow`에 그 수치를 남김. 최종 판정은 구현자를 모두
멈춘 뒤 단독 측정으로 함.

## 라운드 진행

1. 리드가 기준선 snapshot과 dev 행에서 과제를 고름. 구현자 둘의 수정 파일이 겹치지 않게 나눔.
2. 구현자마다 통합 브랜치 최신 커밋에서 worktree와 브랜치를 만들고, 기준 커밋과 브리프를 넘김.
3. 구현자는 회귀 테스트를 먼저 추가하고 고친 뒤, 관련 테스트와 `diff-policy` 결과를 보고함.
4. 리드 판정 순서: `diff-policy`, 후보 worktree `snapshot`, `compare`, 교차 검토, 검토 에이전트.
5. 모두 통과하면 통합 브랜치에 병합하고 병합 결과로 다시 snapshot을 찍어 새 기준선으로 씀.
6. 불합격이면 실패 KPI와 사유를 붙여 한 번 되돌려 보냄. 두 번째도 불합격이면 그 과제는 버림.

## 한계

- 저장 JD 206건과 홀드아웃 36건 기준표는 독립 AI 검토 결과이며 사람 골드가 아님.
- 지연시간 KPI는 한 PC에서 측정하므로 다른 작업과 겹치면 흔들림. 그래서 25%를 허용함.
- 문항 품질 KPI는 결정적 템플릿과 게이트 기준임. 유료 모델이 만든 문항의 품질은 별도 실모델
  점검이 필요함.
