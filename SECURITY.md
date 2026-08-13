# NCScope 보안 메모

## OpenAI API 키: 요청 단위 BYOK

- 공개 UI와 API의 질문 생성 provider는 `openai_api`로 고정됩니다. 개인 Codex·Claude 로그인, CLI provider, provider 선택 또는 자동 전환은 제공하지 않습니다.
- 사용자가 브라우저에 입력한 OpenAI API 키는 매 생성 요청의 JSON body 또는 multipart form에 `openai_api_key`로 전달됩니다. URL query string으로 전달하면 거부합니다.
- 키는 브라우저의 `localStorage`, `sessionStorage`, `IndexedDB`, Cookie 또는 Cache Storage에 저장하지 않고, NCScope의 `.env`, 파일, DB, 감사로그, 애플리케이션 로그, 오류 메시지 또는 응답에도 기록하지 않습니다. 브라우저 입력 필드와 요청 메모리에는 처리 중 일시적으로 존재하며 페이지를 새로고침하면 사라집니다.
- 서버 환경변수 `OPENAI_API_KEY`는 공개 생성 요청의 대체 자격증명으로 사용하지 않습니다. 서버에 값이 있더라도 요청 키가 없으면 HTTP 400 `openai_api_key_required`를 반환합니다.
- `GET /api/generation-provider/status`는 키를 받거나 인증을 시도하지 않습니다. `provider=openai_api`, `auth_mode=request_scoped_api_key`, `status=key_required`를 반환하고 `authenticated=false`, `credential_configured=false`로 요청 단위 계약만 알립니다.
- NCScope 백엔드는 요청 키를 메모리에서 읽어 관리자가 승인한 단일 `OPENAI_BASE_URL`로 전달합니다. 다른 provider, base URL 또는 템플릿 질문으로 자동 대체하지 않습니다. OpenAI 호출이나 품질 검증 실패는 정제된 HTTP 502 `openai_api_generation_failed`로 반환합니다.
- 주 생성 경로의 질문 생성 POST는 최초 생성·1회 slim 복구·1회 전체 품질 재생성을 합쳐 최대 3회이며, 각 의미 요청의 전송 시도는 1회로 고정됩니다. 응답의 `model_quality_retry`에는 실제 생성 요청 수와 상한을 기록합니다. 보조 생성은 서로 다른 프롬프트 variant 최대 3개를 각각 1회만 전송합니다. NCS AI 재정렬과 선택적 vision OCR은 별도 단계이며 각각 제한된 호출만 허용합니다.

## 이 BYOK 방식의 위험

OpenAI의 [API 키 안전 지침](https://developers.openai.com/api/reference/overview#authentication)은 API 키를 브라우저나 앱 같은 클라이언트 코드에 노출하지 말고 서버의 환경변수 또는 키 관리 서비스에서 불러오도록 권고합니다. NCScope는 키를 코드에 하드코딩·번들링하거나 브라우저 저장소에 보관하지는 않지만, 사용자가 입력한 키가 브라우저 런타임과 NCScope 서버를 통과하는 요청 단위 BYOK입니다. 따라서 이 구조는 공식 권고의 서버 소유 비밀키 모델과 같지 않으며, OpenAI의 Enterprise Key Management 의미의 BYOK도 아닙니다.

브라우저 확장 프로그램·XSS·악성 스크립트, TLS 종단 프록시, 요청 본문 로깅, APM/오류 수집기, 서버 메모리 덤프 또는 서버 침해가 키를 노출할 수 있습니다. 공공기관 운영자는 이 잔여 위험을 보안성 검토와 개인정보·외부 AI 이용 심의에서 명시적으로 승인해야 합니다. 승인되지 않으면 배포하지 말고 기관이 관리하는 서버 비밀키 또는 승인된 AI gateway 방식으로 재설계하세요.

- loopback 개발 환경을 제외한 모든 접속에는 HTTPS를 강제하고 HTTP를 HTTPS로 리디렉션합니다. TLS는 신뢰하는 reverse proxy 또는 load balancer에서 종료하며, 프록시에서 요청 body와 `Authorization` 헤더 기록을 끕니다.
- WAF, APM, tracing, crash report, access log와 support dump가 JSON/multipart body를 수집하지 않도록 구성합니다. `/api/questions/*`와 `/api/jd/strategy/upload`의 body 캡처는 금지합니다.
- 사용자에게 공용 master key가 아니라 해당 작업용 OpenAI project key를 발급하고, 최소 권한·낮은 사용 한도·짧은 수명·사용량 알림을 적용합니다. 사용 후 폐기하거나 교체하고 사용량을 확인합니다.
- 화면 공유, 클립보드 기록, 브라우저 자동완성, 개발자 도구, 명령행 기록 및 지원 티켓에 실제 키를 남기지 않습니다.
- Windows `curl` fallback은 기본적으로 끕니다. 이를 켜면 일부 환경에서 자격증명이 프로세스 실행 정보에 노출될 수 있습니다.
- 공개 BYOK 경로는 명시적 전송 상한을 사용하므로 `curl` subprocess fallback을 실행하지 않습니다.
- 노출이 의심되면 즉시 키를 폐기·교체하고 OpenAI 사용량, reverse proxy 및 최소 메타데이터 감사로그를 확인합니다.

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
