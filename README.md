# NCScope

NCScope는 공공기관 채용 직무기술서와 공고문을 활용해 NCS 기반 구조화 면접 질문 생성을 지원하는 도구입니다.

직무기술서를 기준 문서로 삼고, 공고문은 보완 자료로 사용합니다. 세분류 확정과 NCS 조회는 직무기술서에서 사람이 검토·확정한 값만 사용합니다.

![NCScope 실행 화면](docs/images/ncscope-home.png)

## 핵심 기능

- PDF/HWP/HWPX/DOCX/TXT/이미지 직무기술서 파싱
- Kordoc 기반 수행업무, 지원자격, 우대사항, 세분류 후보 추출
- Human-in-the-loop 방식의 세분류 검토·확정
- 공고문에서 직무기술서에 없는 담당업무, 지원자격, 우대사항, 면접 평가항목 보완
- 확정 세분류 기준 로컬 NCS DB 조회 서버에서 공식 능력단위·수행준거·KSA 조회
- KSA 근거 기반 구조화 면접 질문, 꼬리질문, 평가 포인트 생성
- 요청 단위 OpenAI API key 입력 지원

## 사용 흐름

1. 브라우저에서 NCScope를 엽니다.
2. 필요한 경우 `OpenAI API key` 칸에 키를 입력합니다.
   - 입력한 키는 저장하지 않습니다.
   - 현재 생성 요청에만 사용합니다.
   - 비워두면 서버의 `OPENAI_API_KEY` 환경변수를 사용합니다.
3. 직무기술서 파일을 업로드합니다.
4. Kordoc 자동 추출 결과를 확인합니다.
5. 수행업무, 지원자격, 우대사항, 세분류 후보를 수정합니다.
6. 세분류를 확정합니다.
7. 공고문 파일이 있으면 업로드합니다.
8. 공고문에서 추출된 보완 텍스트를 확인·수정합니다.
9. `로컬 NCS DB KSA 기반 면접 질문 생성` 버튼을 누릅니다.
10. NCS 매칭 결과, KSA 항목, 구조화 면접 질문을 확인합니다.

## 직무기술서와 공고문 적용 기준

NCScope의 기준 문서는 직무기술서입니다.

- 세분류: 직무기술서 검토·확정값만 사용
- 담당업무: 직무기술서 추출값 우선, 공고문 값은 보완
- 지원자격: 직무기술서 추출값 우선, 공고문 값은 보완
- 우대사항: 직무기술서 추출값 우선, 공고문 값은 보완
- 면접 평가항목: 공고문에서 면접전형·면접평가 구간을 우선 추출

공고문은 기관마다 형식이 크게 다릅니다. 따라서 공고문 추출값은 확정값이 아니라 검토 후보입니다. 사용자가 수정한 최종 보완 텍스트만 질문 생성 맥락에 반영됩니다.

## 실행 방법

### 1. 의존성 설치

```powershell
git clone https://github.com/koul777/NCScope.git
cd NCScope

pip install -r requirements.txt
npm ci
```

`npm ci`는 문서 파싱용 Kordoc 브리지 실행에 필요합니다.

### 2. 환경변수 설정

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
$env:MAX_UPLOAD_MB="30"
$env:OPENAI_API_KEY="sk-..."   # 선택
```

현재 코드의 환경변수명은 `NCS_MCP_URL`입니다. 실제 역할은 로컬 NCS DB 조회 서버 주소입니다.

### 3. 앱 실행

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8015
```

또는 Windows 실행 스크립트를 사용할 수 있습니다.

```powershell
.\run_local.ps1
```

접속 주소:

```text
http://127.0.0.1:8015
```

## 주요 API

### 직무기술서 검토 파싱

```http
POST /api/jd/parse-review
```

직무기술서를 Kordoc으로 파싱하고, 사람이 검토할 필드를 반환합니다.

반환 핵심 필드:

- `fields.duties`
- `fields.qualifications`
- `fields.preferences`
- `fields.ncs_detail_candidates`

### 공고문 보완 파싱

```http
POST /api/notice/parse-review
```

공고문에서 보완 후보를 추출합니다.

반환 핵심 필드:

- `fields.duties`
- `fields.qualifications`
- `fields.preferences`
- `fields.evaluation`

### KSA 기반 질문 생성

```http
POST /api/jd/strategy/upload
```

필수 조건:

- `jd_file`
- `jd_review_json.review_confirmed = true`
- `jd_review_json.fields.ncs_detail_candidates` 1개 이상

선택 입력:

- `notice_file`
- `duty_text`
- `qualification_text`
- `preference_text`
- `evaluation_text`
- `openai_api_key`

## 검증

```powershell
python -m py_compile app\main.py app\services\kordoc_parser.py app\services\question_generation.py
python -m pytest -q
```

ALIO 직무기술서 벤치마크:

```powershell
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python scripts\benchmark_alio_jd.py --limit 5 --include-ksa
```

## Kordoc 사용

NCScope의 문서 파싱은 Kordoc을 사용합니다.

- Kordoc: https://github.com/koul777/kordoc
- 관련 프로젝트: https://github.com/koul777
