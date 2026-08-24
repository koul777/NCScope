# NCScope Deployment

NCScope는 별도 `NCS_MCP` 서버에서 공식 NCS 능력단위와 KSA를 조회하고, 질문 생성과 독립 품질검수에는 사용자가 요청마다 입력한 OpenAI API 키만 사용합니다.

## 1. NCS_MCP 준비

NCScope는 NCS SQLite DB를 직접 열지 않습니다. 읽기 전용 NCS_MCP를 실행하고 `NCS_MCP_URL`을 설정하세요.

```powershell
$env:NCS_DB_PATH="C:\data\ncs_interview_serving_release.db"
$env:NCS_MCP_READ_ONLY="1"
python -m ncs_mcp.server --transport streamable-http --host 0.0.0.0 --port 8778
```

필수 상태는 `ncs_search`, `ncs_unit_detail`, 공식 KSA 반환이 모두 가능한 것입니다.

## 2. OpenAI BYOK 운영 계약

- 공개 질문 생성은 `openai_api`만 지원합니다.
- 사용자가 입력한 `sk-...` 키는 해당 POST 요청에서만 사용하며 DB, 파일, 브라우저 저장소에 저장하지 않습니다.
- 공개 요청의 `generation_model` 재정의는 허용하지 않습니다. 배포에 승인된 Luna 재정렬, Terra 작성, Sol 검수·재생성 구성을 사용합니다.
- 서버의 `OPENAI_API_KEY`를 읽거나 대신 사용하지 않습니다.
- `sk-or-...` OpenRouter 키, 서버 공용키, Codex/Claude 구독 로그인은 공개 생성에 사용할 수 없습니다.
- OpenAI 키의 전송 대상은 `https://api.openai.com/v1`로 고정합니다. `OPENAI_BASE_URL` 환경변수로 제3자 호환 게이트웨이에 우회할 수 없습니다.
- 키를 query string으로 보내면 거절합니다. JSON body 또는 multipart form의 요청 키 필드만 사용합니다.
- 운영 HTTPS를 강제하고 `/api/questions/*`, `/api/jd/strategy/upload`의 request body와 Authorization 헤더를 proxy, WAF, APM, tracing, 오류 리포트, replay 도구에 기록하지 마세요.

`GET /api/generation-provider/status`는 키를 받거나 검증하지 않습니다. 정상 계약 예시는 다음과 같습니다.

```json
{
  "provider": "openai_api",
  "auth_mode": "request_scoped_api_key",
  "status": "key_required",
  "available": true,
  "authenticated": false,
  "credential_configured": false,
  "credential_managed_by": "request",
  "requires_request_api_key": true,
  "supported_providers": ["openai_api"],
  "generation_limits": {
    "max_main_questions_per_request": 5,
    "max_follow_up_questions_per_main": 5,
    "max_ncs_details_per_request": 1,
    "max_interview_methods_per_request": 1,
    "request_budget_sec": 285
  }
}
```

## 3. 질문 생성과 AI 품질검수

공개 문항의 주질문, 꼬리질문, 평가포인트는 모두 OpenAI가 작성합니다. 서버는 공식 KSA `evidence_id`를 잠그고 메타데이터와 안전 경계만 확인하며 문장을 조립하거나 고치지 않습니다.

호출 순서는 다음 네 단계가 최대입니다.

1. 초안 생성
2. 별도 OpenAI 고추론 품질검수
3. 실패 슬롯의 완전 신규 재생성
4. 최종 OpenAI 품질검수

검수는 한국어 자연스러움, 상황·행동·KSA 연결, KSA 측정 가능성, 직무 근거성, 면접기법 적합성, 꼬리질문 연계성, 평가포인트 관찰 가능성, KSA 명칭의 기계적 삽입 부재를 각각 1~5점으로 평가합니다. KSA 연결·측정성, 직무 근거, 면접기법, 평가 관찰성은 3점 이상을 요구하고, 한국어 자연스러움·꼬리질문 연계·비기계적 표현은 2점 이상을 허용합니다. 전체 평균 점수는 추가 탈락 조건이 아니며 실질 중복만 별도 차단합니다.

검수 실패 뒤 재생성도 실패하면 질문 없이 HTTP 502 `openai_api_quality_rejected`, 네트워크 실패는 503 `openai_api_unreachable`, 전체 요청 시간 초과는 504 `openai_api_timeout`을 반환합니다. `server_ksa_fallback`, `template_fallback`, `human_review_required`, 부분 통과 문항은 공개하지 않습니다.

성공 결과에는 다음 메타데이터가 포함됩니다.

```json
{
  "ai_quality_review": {
    "status": "passed",
    "reviewed_count": 1,
    "attempt_count": 1,
    "scores": [],
    "reason_codes": [],
    "provider": "openai_api",
    "model": "gpt-5.6-sol"
  }
}
```

모델은 역할별로 분리합니다. Luna는 NCS 후보 재정렬, Terra는 질문 초안 작성,
Sol은 독립 품질검수와 실패 슬롯의 완전 재생성을 담당합니다. 한 요청의 OpenAI
API 키만 공유하며, 질문 문장은 Terra 또는 재생성 시 Sol이 직접 작성합니다.

`2020년 NCS기반 능력중심 채용모델 면접관 기본·심화` PDF는 Kordoc 4.9.1로
오프라인 구조화·검토한 요약만 `app/resources/ncs_interviewer_guide_2020.json`에
배포합니다. 경험·상황·발표·토론면접과 STAR의 취지를 작성 참고로만 프롬프트에
주입하며, 원본 PDF 경로·원문·파싱 경고는 공개 응답에 포함하지 않습니다. 이
참고자료가 없거나 손상되어도 내장된 짧은 기법 설명으로 생성은 계속되며, 품질
통과 조건이나 NCS 근거 경계는 바뀌지 않습니다.

## 4. 로컬 실행

```powershell
pip install -r requirements.txt
npm ci
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

브라우저에서 `http://127.0.0.1:8015`를 열고 본인의 OpenAI API 키를 입력합니다. `.env`에 OpenAI 키를 넣어도 공개 생성의 대체 키로 사용되지 않습니다.

## 5. Vercel Production

현재 공개 프로필은 다음과 같습니다.

```text
NCSCOPE_LOAD_DOTENV=false
NCS_MCP_URL=https://ncscope-ncs-mcp.vercel.app/api/mcp
INTERVIEW_GENERATION_PROVIDER=openai_api
DATABASE_URL=sqlite:////tmp/ncscope.db
MAX_UPLOAD_MB=4
MAX_REQUEST_BODY_MB=4
NCS_MCP_TIMEOUT_SEC=5
NCS_MCP_KSA_CONCURRENCY=4
KSA_RANK_MAX_UNITS=5
OPENAI_HTTP_CURL_FALLBACK_ENABLED=false
OPENAI_NET_CHECK_ENABLED=false
OPENAI_RERANK_MODEL=gpt-5.6-luna
OPENAI_STRATEGY_MODEL=gpt-5.6-terra
OPENAI_STRATEGY_RETRY_MODEL=gpt-5.6-sol
OPENAI_QUESTION_MODEL=gpt-5.6-terra
OPENAI_QUALITY_REGENERATION_MODEL=gpt-5.6-sol
OPENAI_STRATEGY_CANDIDATE_MULTIPLIER=1
OPENAI_QUESTION_CANDIDATE_MULTIPLIER=1
OPENAI_QUESTION_VARIANT_ATTEMPTS=1
INSTITUTION_MODEL_REQUESTS_PER_BATCH=1
INSTITUTION_QUALITY_RETRY_ENABLED=true
INSTITUTION_GENERATION_BATCH_SIZE=5
INSTITUTION_GENERATION_BATCH_CONCURRENCY=1
GENERATION_REQUEST_BUDGET_SEC=285
GENERATION_MAX_MAIN_QUESTIONS=5
OPENAI_QUALITY_REVIEW_MODEL=gpt-5.6-sol
OPENAI_QUALITY_REVIEW_REASONING_EFFORT=high
AI_QUALITY_REVIEW_TIMEOUT_SEC=70
```

`api/index.py`의 Vercel `maxDuration`은 300초이고 애플리케이션 요청 예산은 285초입니다. 외부 서비스가 느리더라도 서버 템플릿으로 문항을 복원하지 않습니다.

Vercel Python 함수는 Node/Kordoc 실행 파일을 보장하지 않습니다. PDF는 Python
구조·텍스트 복구 경로를 사용하고, HWP 5/HWPX는 `olefile`/표준 XML 기반 로컬
복구 경로를 사용합니다. HWP 원문은 외부 변환 API로 보내지 않습니다. 압축 해제
총량, 섹션 수, 추출 글자 수, ZIP 멤버 수와 파일 크기에 상한을 적용하며, 평면화된
NCS 분류표는 NCS MCP exact 세분류만 자동 후보로 공개합니다.

공공기관 운영 전에는 OpenAI의 데이터 처리 조건과 기관 내부의 개인정보·비공개자료 반출 정책을 별도로 확인해야 합니다. API 데이터는 기본적으로 모델 학습에 사용되지 않지만, 기본 abuse monitoring 로그에는 최대 30일 보존 조건이 적용될 수 있습니다. 필요한 기관은 OpenAI와 Zero Data Retention 또는 Modified Abuse Monitoring 자격 및 DPA를 검토하세요.

## 6. 검증

```powershell
python -m py_compile app/main.py app/services/ai_question_quality_review.py app/services/hwp_text_fallback.py
python -m json.tool vercel.json
pytest -q tests/test_ai_question_quality_review.py tests/test_auxiliary_ai_quality_review.py tests/test_openai_byok_contract.py
python scripts/verify_ncs_interviewer_guide_reference.py "C:\path\to\면접관+기본심화.pdf" --expected-metadata-json app/resources/ncs_interviewer_guide_2020.json
```

실 OpenAI canary는 운영 승인된 테스트 키와 비민감 합성 문서로만 수행하세요. 배포 뒤에는 품질 재생성률, 최종 거부율, p50/p95 지연시간, fallback 반환 0건을 확인합니다.
