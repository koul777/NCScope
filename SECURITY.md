# NCScope 보안 메모

## OpenAI 사용자 키(BYOK)

- 공개 질문 생성은 사용자가 요청마다 입력한 OpenAI `sk-...` 키만 사용합니다.
- `OPENAI_API_KEY`, OpenRouter 서버 키, Codex/Claude 구독 로그인은 공개 요청의 대체 자격증명으로 사용하지 않습니다.
- OpenRouter `sk-or-...` 키와 `generation_provider=openrouter_api` 요청은 네트워크 호출 전에 거절합니다.
- OpenAI 키의 목적지는 공식 `https://api.openai.com/v1`로 고정합니다. 환경변수로 제3자 OpenAI 호환 endpoint에 키를 전달할 수 없습니다.
- 요청자가 모델을 임의로 변경할 수 없으며, 공개 배포의 역할별 승인 모델 구성을 서버가 고정합니다.
- 키는 JSON body 또는 multipart form으로만 받고 query string 키는 거절합니다.
- 키를 `localStorage`, `sessionStorage`, IndexedDB, Cookie, Cache Storage, 파일, DB, 감사로그, 오류 메시지 또는 응답에 저장하지 않습니다.
- UI는 키를 평문으로 화면에 계속 표시하지 않으며 요청 종료 뒤 서버가 키 상태를 보관하지 않습니다.

## 전송과 운영

- loopback 개발을 제외한 모든 접속에 HTTPS를 강제합니다.
- reverse proxy, WAF, APM, tracing, crash report, support dump, replay 도구에서 `/api/questions/*`와 `/api/jd/strategy/upload`의 request body 수집을 끕니다.
- OpenAI upstream의 `Authorization` 헤더를 기록하지 않습니다.
- CSP, dependency 점검, XSS 방어를 유지합니다. 공유 PC에는 개인 API 키를 입력하지 않는 것을 권장합니다.
- Windows curl subprocess fallback은 비활성화합니다. 일부 환경에서는 프로세스 실행 정보에 자격증명이 노출될 수 있습니다.

## 업로드 문서

- 공고문과 직무기술서에 개인정보, 민감정보, 실제 지원자 정보, 비공개 보안정보가 포함되어 있으면 업로드 전에 제거하거나 가립니다.
- 담당자 이름, 전화번호, 이메일, 서명은 모델 전송 대상에서 제거합니다.
- 외부 AI 반출이 금지된 기관 자료에는 질문 생성 기능을 사용하지 않습니다.
- 파일별 `MAX_UPLOAD_MB`와 요청 전체 `MAX_REQUEST_BODY_MB`를 적용합니다.
- ZIP의 암호화 멤버, 경로 이탈, 비정상 압축 비율을 차단합니다.
- HWP/HWPX 로컬 복구는 섹션 수·압축 해제 총량·XML 크기·추출 글자 수를 제한하고,
  HWPX의 DTD/ENTITY 선언을 거절합니다. 외부 문서 변환 API는 호출하지 않습니다.
- ALIO 외부 파일을 자동 다운로드하거나 모델에 자동 전송하지 않습니다. 사람이 채용 건과 첨부를 확인해 업로드합니다.

## 오픈소스 공급망

- Kordoc은 외부 문서 변환 API가 아니라 NCScope가 자체 실행하는 문서 파서이며 현재 잠금 버전은 `4.9.1`, 자체 라이선스는 MIT입니다. 로컬은 Node subprocess, Vercel은 같은 배포의 인증된 Node 함수에서 실행합니다. Python 함수만 Ed25519 개인키를 환경변수로 보유하고 Node 함수는 공개키로 본문 결합 단기 서명을 검증합니다. 개인키·서명은 응답·로그에 노출하지 않으며, `KORDOC_OFFLINE=1`로 외부 OCR·모델 다운로드를 막습니다.
- NCS 면접관 기본·심화 PDF는 개발 시 Kordoc으로 오프라인 구조화하고, 배포에는 사람이 검토한 짧은 작성 가이드 JSON만 포함합니다. 운영 요청에서 데스크톱 PDF를 읽거나 원문을 외부 API로 보내지 않으며, 가이드는 품질 통과·탈락이나 NCS 근거 판정에 사용하지 않습니다.
- 운영 의존성의 `adm-zip`은 `0.6.0`, `sharp`는 `0.35.3` 이상으로 고정하고 `npm audit --omit=dev` 0건을 배포 기준으로 둡니다.
- 운영 잠금파일에 라이선스 미표기 패키지가 없는지 확인합니다. 전이 의존성에는 LGPL 계열과 복수선택 라이선스가 있으므로 기관 반입 전에 SBOM·라이선스 의무를 별도 심사합니다.
- 로컬 오픈소스 라이브러리 사용과 외부 SaaS/API로의 데이터 전송은 별도 승인 항목입니다. BYOK도 외부 데이터 처리 위탁·국외 이전·보존 정책 검토를 대체하지 않습니다.

## 프롬프트 주입 경계

- 공고문, 직무기술서, OCR, 추가 문맥은 신뢰하지 않는 데이터로 취급합니다.
- 그 안의 이전 지시 무시, 시스템 프롬프트·API 키 공개, 도구 실행, 외부 통신, 출력 형식 변경 명령을 따르지 않습니다.
- 지원자에게 보이는 주질문·꼬리질문·평가포인트에서 주입 문구나 내부 메타데이터가 탐지되면 문항을 반환하지 않습니다.

## NCS KSA 근거

- 서버가 NCS_MCP에서 직접 조회한 공식 KSA 행으로 안정적인 `evidence_id`를 계산합니다.
- 모델이 스스로 만든 `question_task_frame`, `ksa_evidence`, `evidence_id`는 근거 진위를 증명할 수 없습니다.
- `evidence_id`는 추적 사슬만 증명합니다. 질문이 KSA를 의미상 측정하는지는 별도 AI 품질검수가 판정합니다.
- 같은 ID를 사용한 무관한 예산·원장 질문도 의미 연결 점수가 낮으면 탈락합니다.

## 품질 실패와 정보 노출

- 검수 실패 문항, 부분 통과 문항, `server_ksa_fallback`, `template_fallback`, degraded 문항을 공개하지 않습니다.
- 실패 응답에는 질문 원문, API 키, provider 예외 문자열을 포함하지 않습니다.
- HTTP 502·503·504 응답은 제한된 오류 코드와 재시도 가능 여부만 제공합니다.

## OpenAI 데이터 처리

OpenAI API 데이터는 기본적으로 모델 학습에 사용되지 않지만 기본 abuse monitoring 로그에는 최대 30일 보존 조건이 적용될 수 있습니다. 기관의 규정·계약·위탁 처리 요건에 따라 Zero Data Retention, Modified Abuse Monitoring, DPA 자격과 설정을 OpenAI에 확인해야 합니다.

- [OpenAI API 데이터 제어](https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint)
- [OpenAI Enterprise Privacy](https://openai.com/enterprise-privacy/)

## 감사 로그

- 최소한의 action, resource ID, 비식별 운영 메타데이터만 기록합니다.
- API 키, request body, 문서 원문, 생성 질문 원문은 감사로그에 저장하지 않습니다.
- 다중 프로세스·분산 배포에서는 review session과 감사로그 저장소의 접근제어, 암호화, 보존기간을 기관 정책에 맞게 설정합니다.
