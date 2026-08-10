# NCScope AX Evidence Gate Map

이 문서는 `AX체크.html`의 13개 비합산 관문을 NCScope 면접문항 생성·검토 흐름에 적용한 운영 기준입니다. 상태는 `미확인`, `설계·시범`, `운영 증거`로 구분하며, 코드가 존재한다는 사실만으로 운영 증거로 계산하지 않습니다.

## 관문 매핑

| AX 관문 | NCScope 구현 | 운영 증거가 되는 기록 |
| --- | --- | --- |
| ready_asset | NCS_MCP 기반 능력단위·KSA 조회, DB 초기화 | `/health`의 configured/reachable/ksaAvailable |
| enabled_output | 과제·후속질문·평가포인트·상중하 행동앵커 생성 | `question_quality_runs`의 정책 버전·문항 해시·게이트 결과 |
| enabled_review | 문항별 승인/수정요청 UI와 API | `question_quality_reviews`의 결정·이슈코드·시각 |
| first_redesign | 생성→검토→반려→다음 생성 개선 흐름 | AS-IS/TO-BE와 역할 변경의 기관 승인 기록은 별도 필요 |
| first_auto | 저위험 회귀·표본 품질 사이클 자동 실행 | `reports/question_quality_loop/state.json`; 실제 업무 자동종료 기록은 별도 필요 |
| first_escalation | KSA·직무맥락·공식형식·블라인드·초점정합 실패 자동 이관 | run의 `escalation_required`, `trigger_codes` |
| first_exception | 승인·반려 경계 | 이의·중지·롤백 실행 로그는 아직 운영 증거가 아님 |
| first_metrics | 승인율·거절률·이관률·이슈별 건수 | `GET /api/ops/quality-metrics` |
| first_feedback | 반려/수정요청 문항을 같은 NCS 코드의 다음 프롬프트에 환류 | 저장 리뷰와 다음 run의 생성 문맥 |
| native_core | 면접문항 생성이 서비스 핵심 기능 | AI 중단 영향분석과 핵심 KPI 연계는 기관 운영 증거 필요 |
| native_loop | 리뷰를 golden/negative/regression 평가셋으로 승격하고 회귀검사 | `question_quality_eval_cases` + `feedback-eval-regression` 통과 로그 |
| native_resilience | 모델 실패 시 공식 NCS KSA 템플릿으로 강등 | 실제 장애훈련, 전환 로그, RTO 기록은 별도 필요 |
| native_scope | 적용 범위·제외 범위·책임·감사 원칙 명시 | 기관 서비스 카탈로그·SLA·감사 승인 문서는 별도 필요 |

현재 단계는 `GET /api/ops/ax-readiness`에서 보수적으로 산출합니다. 일부 상위 관문에 코드나 시험이 있어도 하위 누적 필수 관문에 운영 증거가 없으면 단계는 올라가지 않습니다.

## 품질 운영 API

- `GET /api/ops/quality-metrics`: 생성 run, 리뷰, 승인·반려, 에스컬레이션, 이슈 집계
- `GET /api/ops/ax-readiness`: 13개 관문의 증거/시범/미확인 상태와 보수적 단계
- `GET /api/quality/runs/{run_id}`: `X-Review-Token` 헤더로 run의 검토 상태 조회
- `POST /api/quality/runs/{run_id}/review`: run-scoped 토큰으로 문항 승인·반려·수정요청 기록
- `POST /api/quality/reviews/{review_id}/promote-to-eval`: 관리자 승인으로 운영 표본을 평가셋으로 승격
- `GET /api/quality/eval-cases`: 관리자용 활성 평가셋 조회

## 개인정보·오염 방지

- 생성 시 원문 JD·이력서·지원자 답변은 품질 운영 DB에 복제하지 않습니다.
- 자동 기록은 문항 해시, NCS 코드, 면접유형, 정책 버전, 게이트 결과만 포함합니다.
- 사람이 명시적으로 검토한 문항 텍스트만 리뷰 레코드에 저장합니다.
- API 키 형태, 이력서, 지원자 답변 필드는 피드백 API가 거부합니다.
- 각 생성 run의 검토 토큰은 해시로만 저장하며 다른 run의 문항 해시를 제출할 수 없습니다.

## 승격 규칙

1. 공식 NCS 표본 프로파일, 코드 회귀, 운영 피드백 평가셋, ALIO 실문서 품질 단계가 모두 통과해야 합니다.
2. 평균점수 1.0만으로 승격하지 않습니다. 대표 문항을 사람이 읽고 자연스러움·직무상황·난이도를 확인합니다.
3. 블라인드 채용 위반은 예외 승격할 수 없습니다.
4. 반려 사례를 평가셋으로 승격한 뒤 `feedback-eval-regression`이 실패하면 정책 변경을 승격하지 않습니다.
5. 운영 장애훈련·SLA·기관 승인처럼 이 저장소 밖의 증거가 필요한 관문은 코드만으로 `운영 증거`로 표시하지 않습니다.
