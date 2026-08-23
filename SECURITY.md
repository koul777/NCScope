# NCScope 보안 메모

## OpenRouter 서버 키와 선택적 개인 키

- Vercel Production은 `OPENROUTER_ALLOW_SERVER_KEY=true`일 때 암호화된 `OPENROUTER_API_KEY`를 기본 자격증명으로 사용합니다. 서버 키는 JavaScript·HTML·API 응답으로 보내지 않으므로 방문자는 키를 입력하지 않아도 됩니다.
- 서버 키는 `sk-or-` 형식만 허용하고 OpenRouter의 고정 URL과 `stealth/ox-alpha` 모델에만 전송합니다. 다른 provider나 base URL로 자동 우회하지 않습니다.
- `GET /api/generation-provider/status`는 키 값이나 외부 인증을 조회하지 않습니다. 비밀을 노출하지 않고 `auth_mode=server_env_api_key`, `status=configured`, `credential_configured=true`, `authenticated=false` 같은 구성 상태만 반환합니다.
- 화면의 개인 키 입력은 선택 사항입니다. 입력한 키가 있으면 현재 생성 요청에서 서버 키보다 우선하며 `generation_api_key` body/form 필드로만 전달됩니다. URL query string의 키는 거부합니다.
- 개인 키는 `localStorage`, `sessionStorage`, `IndexedDB`, Cookie, Cache Storage, 파일, DB, 감사로그, 애플리케이션 로그, 오류 메시지 또는 응답에 저장하지 않습니다. 브라우저 입력 필드와 요청 메모리에만 일시적으로 존재합니다.
- `OPENAI_API_KEY`는 공개 생성 요청의 서버 fallback으로 사용하지 않습니다. 개인 `sk-` 키를 명시한 요청만 관리자가 승인한 단일 `OPENAI_BASE_URL`로 전송합니다.
- 주 생성 경로는 호출·재시도 상한을 적용하고, 응답 메타데이터에는 비밀정보 없이 실제 생성 요청 수를 기록합니다.

## 공개 서버 키 운영 위험

공개 사용자는 키 자체를 볼 수 없지만 NCScope의 생성 API를 통해 운영자 계정의 사용량을 소비할 수 있습니다. 따라서 서버 키를 공개 배포에 연결하면 남용, 비용·쿼터 소진, 자동화 호출 및 서비스 거부 위험을 운영자가 부담합니다.

- OpenRouter에서 NCScope 전용 키를 발급하고 가능한 최소 한도와 사용량 알림을 설정합니다. 다른 서비스와 같은 키를 공유하지 않습니다.
- 애플리케이션의 요청률 제한과 생성 동시성 제한을 활성화하고, Vercel·WAF에서도 비정상 트래픽을 제한합니다.
- 사용량과 오류율을 정기적으로 확인하고, 이상 징후나 노출 의심 시 즉시 키를 폐기·교체한 뒤 새 배포를 수행합니다.
- 실제 키는 Vercel secret 또는 커밋되지 않는 로컬 `.env`에만 저장합니다. 소스, `vercel.json`, 이미지, 문서, 명령행, 셸 기록, 스크린샷, 지원 티켓에 넣지 않습니다.
- loopback 개발 환경을 제외한 모든 접속에는 HTTPS를 강제합니다. 프록시·WAF·APM·tracing·crash report·support dump에서 요청 body와 `Authorization` 헤더 기록을 끕니다.
- 개인 키 입력 기능에는 브라우저 확장 프로그램, XSS, TLS 종단 프록시, 요청 본문 로깅 및 서버 메모리 노출 위험이 남습니다. 공용 PC에서는 개인 키를 입력하지 않습니다.
- Windows `curl` subprocess fallback은 기본적으로 끕니다. 일부 환경에서는 프로세스 실행 정보에 자격증명이 노출될 수 있습니다.

## 업로드 문서

- 직무기술서와 공고문에 개인정보 또는 민감정보가 포함될 수 있으므로, 운영 전 기관 내부 데이터 처리 기준을 확인해야 합니다.
- Kordoc 파싱 결과는 검토 후보로만 사용합니다. 세분류와 공고문 핵심 텍스트는 담당자가 검토·확정해야 하며, 자동 추출값을 최종 판단으로 사용하지 않습니다.
- ZIP 업로드는 암호화 멤버를 파싱하지 않고 오류/경고로 처리합니다.
- 모든 공개 업로드 엔드포인트는 파일별 `MAX_UPLOAD_MB`와 요청 전체 `MAX_REQUEST_BODY_MB` 제한을 적용해야 합니다.
- 앱 저장소에는 업로드 원본, ALIO 다운로드 원본, 로컬 DB, serving DB를 포함하지 않습니다.

### 업로드 텍스트의 프롬프트 주입 경계

- 공고문, 직무기술서, 사용자 프로필, 추가 컨텍스트와 OCR·파싱 결과는 모두 신뢰하지 않는 참고 데이터로 취급합니다. 그 안의 역할 변경, 이전 지시 무시, 시스템 프롬프트·비밀 공개, 도구 실행, 외부 전송 또는 출력 형식 변경 지시는 따르지 않습니다.
- 생성 프롬프트는 외부 텍스트에서 직무 사실·문서·제약만 추출하도록 명시합니다. 외부 텍스트를 시스템 명령이나 개발자 지침으로 승격하지 않습니다.
- 출력 품질 경계는 주질문·꼬리질문·평가포인트에서 후보자에게 노출된 프롬프트 주입 흔적을 다시 검사합니다. 명시적 지시 무시, 시스템 프롬프트/API 키 공개, 도구 실행·외부 전송, JSON-only 강제 문구가 발견되면 전체 생성을 실패시킵니다.
- 정상적인 보안 직무 질문에서 공격 문구를 인용하거나 API 키 관리 위험을 설명하게 하는 것은 문맥을 함께 검사해 허용합니다. 자동 판정만으로 최종 안전을 보증하지 않으므로 담당자는 원문과 생성 질문을 함께 검토해야 합니다.

### NCS KSA 근거 무결성

- 질문 내부의 `question_task_frame`, `ksa_evidence` 또는 모델이 주장한 stable-shaped ID는 자기 증명 자료로 신뢰하지 않습니다.
- 보조 공개 생성 경로는 NCS_MCP에서 서버가 직접 조회한 원시 KSA 식별 필드로 `evidence_id`를 재계산한 `official_ksa_evidence` registry와 질문의 NCS 코드·KSA 참조·task frame을 모두 대조합니다.
- 모델이 제출한 원시 evidence ID는 `provider_question_evidence_id`로 보존합니다. 누락 또는 불일치는 자연어 표면 정리 과정에서 정상 ID로 세탁하지 않고 최종 품질 경계에서 거부합니다.
- batch/diverse 경로는 각 생성 회차를 먼저 검증하고, 회차별 registry 합집합으로 최종 중복 제거 세트를 다시 검증합니다.

## 감사 로그

- MVP는 `audit_logs`에 액션명, 리소스 ID, IP 해시 같은 최소 메타데이터만 기록합니다.
- API 키, 요청 body, 문서 본문, 생성 질문 전문은 감사로그에 저장하지 않습니다.
- 다중 워커/분산 배포에서는 review session과 감사로그 저장소를 외부 영속 저장소로 옮기는 구성이 필요합니다.
