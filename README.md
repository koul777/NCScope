<p align="center"><strong>▶ NCScope 32초 브랜드 홍보 영상</strong></p>
<p align="center">
  <a href="./docs/media/ncscope-promo.mp4">
    <img src="./docs/media/ncscope-promo.gif" alt="NCScope 32초 브랜드 홍보 영상" width="960">
  </a>
</p>
<p align="center">문제 제기 → 핵심 가치 → 실제 제품 하이라이트 → 실행 안내 · 클릭하면 원본 MP4가 열립니다.</p>

<br>

<p align="center"><strong>▶ NCScope 2분 3초 상세 설명 영상</strong></p>
<p align="center">
  <a href="./docs/media/ncscope-explainer.mp4">
    <img src="./docs/media/ncscope-explainer.gif" alt="NCScope 2분 3초 상세 설명 영상" width="960">
  </a>
</p>
<p align="center">문서 파싱부터 NCS 근거 추적 결과까지 실제 조작 전체 · 클릭하면 원본 MP4가 열립니다.</p>

<p align="center"><a href="https://ncscope.vercel.app"><strong>NCScope 실행하기</strong></a></p>

# NCScope v1.4.12

## 최신 운영 릴리스

2026년 8월 26일 기준 `main`을 [운영 서비스](https://ncscope.vercel.app)에 배포했습니다.
이번 릴리스의 세부 근거는
[`reports/ncscope_1_4_12_release_evidence.json`](reports/ncscope_1_4_12_release_evidence.json)에
기계 판독 가능한 형태로 보관합니다.

| 항목 | 현재 상태 |
|---|---|
| 생성 모델 | `gpt-5.6-terra` |
| NCS 후보 재정렬 | `gpt-5.6-luna` |
| 독립 검수·재생성 | `gpt-5.6-sol` |
| 전체 회귀 테스트 | 5,167 passed, 72 skipped |
| 의존성 보안 감사 | 운영·개발 Python requirements 및 npm 알려진 취약점 0건 |
| 현행 공식 세분류 57문서 | 명칭 F1 99.69%, 코드·`(명칭, 코드)` 쌍 F1 100%, 문서 exact 98.25% |
| GitHub Actions | [릴리스 검증 성공](https://github.com/koul777/NCScope/actions/runs/32882542451) |
| GitHub Release | [`v1.4.12`](https://github.com/koul777/NCScope/releases/tag/v1.4.12) |
| Vercel | Production `dpl_pgLgEQUZPx219AWCScsj6xK157YJ`, runtime bundle `888689cca5ffd29a05da39c7d6f4f86af6e609584ff675fcbb5db6ed26259efa` |

정확도 수치는 서로 답을 보지 않은 두 AI 검토와 별도 AI 중재로 만든
`independent_ai_agent_adjudicated_reference_not_human_gold` 기준입니다. 사람 골드셋
정확도로 해석하지 않습니다. 운영 헬스·runtime policy, PDF 텍스트 폴백과 개인정보가
없는 합성 PDF의 Kordoc 4.9.1 `authenticated_serverless_bridge` smoke를 통과했습니다.
다만 로컬 Kordoc에서 처리되던 특정 복잡 PDF 한 건이 운영에서 `422`를 반환한 사례가
있어 문서별 Kordoc 호환성은 후속 추적 대상입니다. 이 경우 오류를 숨겨 추정 결과를
만들지 않고 사용자에게 파싱 실패를 명시합니다.

### 업데이트 내역 구독하기

GitHub의 **Follow**와 저장소 **Star**만으로는 모든 변경 알림이 보장되지 않습니다.
이 저장소 페이지에서 **Watch → Custom → Releases**를 선택하면 새 GitHub Release 알림을,
**All Activity**를 선택하면 커밋·이슈·Pull Request를 포함한 전체 활동 알림을 받을 수
있습니다. 배포별 상세 내용은 다음 위치에서 확인할 수 있습니다.

- 사용자용 변경 요약: 이 README의 `최신 운영 릴리스`,
  [`v1.4.12` 릴리스 노트](docs/releases/v1.4.12.md),
  [버전별 GitHub Releases](https://github.com/koul777/NCScope/releases)
- 코드 변경 내역: [`main` 커밋 기록](https://github.com/koul777/NCScope/commits/main/)
- 자동 검증 결과: [GitHub Actions](https://github.com/koul777/NCScope/actions)
- 운영 서비스: [ncscope.vercel.app](https://ncscope.vercel.app)

관리자는 공개할 버전의 커밋을 푸시한 뒤 태그와 GitHub Release를 함께 발행합니다.
릴리스 본문에는 변경점, 검증 결과, 알려진 제한, 운영 URL을 적어야 구독자가 한 번에
확인할 수 있습니다.

## v1.4.12 안정성·평가 무결성 보강

- ZIP/HWP/PDF 파싱은 손상된 압축 스트림과 지원하지 않는 ZIP 방식을 예측 가능한 `422` 오류로 종료합니다.
- HTML/Markdown 표, 줄 수, Markdown 총 1,000,000자, 세분류 후보 수에 구조 예산을 적용해 작은 악성 입력이나 압축 해제 후 팽창한 문서가 작업 슬롯을 오래 점유하지 못하게 했습니다. 최종 configured-MCP 재검사는 저장 문서 206/206 파싱, KSA 555/555를 성공했고 직전 정상 실행 대비 비타이밍 필드 변경은 0건입니다.
- 요청 시간 초과 뒤에도 실제 스레드와 중첩 MCP 작업이 끝날 때까지 동시성 슬롯을 유지하며, MCP 상태 확인은 single-flight 캐시를 사용합니다.
- KSA 병렬 조회는 첫 실제 오류를 보존하고 새 backlog를 중단합니다. 남은 작업이 실행 중이면 외부 요청 슬롯과 MCP 세션은 작업 종료 시점까지 유지됩니다.
- 공정채용 평가 분할은 문서 SHA-256뿐 아니라 동일 공고의 모든 첨부를 connected component로 묶습니다. workflow/reference v2는 `posting_ids`, 재계산 가능한 `split_group_sha256`, split 정책을 필수로 검증합니다.
- 평가기는 공식 세분류명과 8자리 코드를 독립 집합이 아닌 `(세분류명, 코드)` 쌍으로도 채점합니다. 또한 원시 parse 응답 digest와 실제 loopback 서버의 Python 소스·Node local/remote bridge·package/lock·배포 설정 hash를 runtime attestation v2로 매 응답에서 검증합니다. 각 문서에는 실제 local/bridge/fallback 실행 mode와 Kordoc·Node 버전도 별도 봉인합니다.
- 비공개 정확도 채점기는 원문을 보내기 전에 loopback 서버의 `local-only` 정책과 동일 runtime bundle을 byte-free preflight로 확인합니다. 운영 bridge는 현재 Vercel 배포 URL과 실행 identity가 일치할 때만 같은 runtime으로 봉인됩니다.
- 현재 source packet은 운영 파서와 같은 Kordoc 계열 추출을 사용하므로 `human_gold_eligible=false`입니다. 독립 원문 렌더/OCR 교차검증이 추가되기 전에는 AI adjudicated reference로만 해석해야 합니다.
- 개발·CI 도구는 `pytest 9.0.3`, `pytest-asyncio 1.4.0`으로 올렸고, 격리 환경의 `pip-audit`에서 운영·개발 requirements 모두 알려진 취약점 0건을 확인했습니다.

### 2026-08-27 세분류 추출·매칭 후속 보강

- 좌표 표의 명시적 `세분류`/`NCS 세분류` 헤더 값은 같은 표의 NCS 분류체계 문맥, 활성 공식 카탈로그의 정규화 exact, 빈값·미적용·능력단위 차단 조건을 모두 통과할 때만 추출 후보로 승격합니다. Kordoc·HTML·Markdown 표와 rowspan/colspan 좌표를 같은 계약으로 처리합니다.
- ALIO 코퍼스 프로필 결과는 문서에서 추출한 `ncs_detail_candidates`와 분리해 검토 제안으로만 표시합니다. 기본 선택하지 않으며, 사용자가 직접 고르기 전에는 추출 능력단위를 그 세분류 범위로 재사용하지 않습니다.
- MCP 능력단위 결과는 공식 세분류명과 8자리 코드 소유권을 함께 확인하고, 10자리 능력단위 코드 형식까지 검증합니다. 직접 일치·공백 정리·구두점 변형·순번 제거·공식 표시명 복원·명시적 안전 별칭을 별도 provenance로 기록합니다.
- 공식 세분류명 normalized key, 안전 별칭 target, 능력단위의 세분류 코드·명칭 정합성을 정적 불변식으로 검사합니다. 관련 통합 회귀는 399개가 통과했습니다.
- 공개 ALIO 운영 문서 2건의 네트워크 probe는 파싱 2/2, 현행 공식 세분류 16/16 exact였습니다. 이 값은 연결·회귀 확인용 공개 튜닝 진단이며 사람 골드나 새 블라인드 정확도가 아닙니다. 세부 기록은 [`reports/ncs_detail_matching_6h_20260827.md`](reports/ncs_detail_matching_6h_20260827.md)에 있습니다.
- 2026-08-27 추가 보강으로 `세분류 -> 능력단위` 연결은 `full code exact`를 우선 검증하고, exact code가 없을 때만 `base code + canonical unit name + verified detail ownership`이 하나의 안정 identity로 증명될 경우에만 fallback합니다. 정적 detail/unit 카탈로그가 비거나 읽기 실패하면 조용히 빈 결과를 돌려주지 않고 즉시 오류로 종료하며, `unitResolutionKind`, `unitVersionCompatible`, `catalogUnitCodes` provenance는 재정렬 이후에도 유지됩니다.
- 쉼표가 포함된 공식 세분류명은 전체 이름이 카탈로그에서 하나로 확인될 때 원자적으로 보존합니다. 200행 이름 조회로 공식 능력단위가 덜 회수된 경우에만 8자리 세분류 코드 조회를 한 번 추가하고, 동일한 path·코드 소유권·canonical 이름 검증을 통과한 누락 identity만 합칩니다.
- MCP `isError`·검색 schema drift·동일 base의 버전 간 identity 충돌·서로 다른 alias 필드값·복수 공식 세분류로 갈라지는 alias drift는 모두 자동연결 전에 차단합니다. 배포된 detail/unit 카탈로그는 LF 정규화 SHA-256까지 확인하므로 일부만 남은 손상 카탈로그도 정상적인 무결과로 위장할 수 없습니다.
- 수동 `selected_ncs`도 클라이언트 값을 신뢰하지 않고 서버에서 공식 코드·능력단위명·세분류명을 다시 확인합니다. KSA 조회는 응답 코드·canonical 능력단위명·4단계 분류코드로 만든 8자리 세분류 코드·세분류명을 모두 재검증한 뒤에만 공식 근거로 표시합니다.
- 각 공식 능력단위 행에 `detailExpectedUnitBaseCount`·`detailVerifiedUnitBaseCount`를 남겨 해당 세분류의 공식 예상 base와 실제 검증 base를 비교할 수 있게 했습니다. `detailRetrievalComplete`는 공식 예상 집합을 모두 검증했음을, `detailRetrievalCapLimited`는 호출자의 출력 한도로 회수·반환이 줄었음을 뜻합니다. 따라서 전체 집합을 검증한 후 반환만 잘린 경우에는 두 값이 모두 `true`일 수 있습니다.
- 사람이 고르는 비권위 `ncs-mcp-suggest` 경로도 검색 응답 schema를 엄격히 해석하고, 코드·능력단위명·세분류 path의 중복 alias 필드가 충돌하면 제안에서 제외합니다. path가 현행 공식 세분류명을 쓰면 능력단위 코드의 세분류 범위와 다른 경우도 차단하며, 비현행·자유 라벨은 공식 연결로 승격하지 않고 수동 검토 후보로만 남깁니다.
- 재현 가능한 `python scripts/probe_ncs_detail_connections.py --output <path>` 공개 전수 probe는 하나의 공유 MCP transport session으로 23.848초 동안 1,094개 세분류의 예상 13,281개 base 중 13,278개를 연결했고 예상 밖 연결과 identity 위반은 각각 0개, complete 1,091개, partial 3개, zero 0개였습니다. 이 값은 공개 카탈로그 연결 진단이지 사람 골드·신규 블라인드 정확도가 아닙니다. 남은 3개는 MCP path 오결합으로 격리했으며, 상세 근거는 [`reports/ncs_detail_connection_3h_20260827.md`](reports/ncs_detail_connection_3h_20260827.md)에 있습니다.

NCScope는 공공기관 채용공고문과 NCS 직무기술서를 바탕으로 공식 NCS KSA 근거가 추적되는 구조화 면접 질문 초안을 만드는 프로그램입니다.

공개 서비스: **https://ncscope.vercel.app**

## 영상으로 보는 NCScope

두 영상의 목적과 편집 문법을 분리했습니다. 홍보 영상은 NCScope가 해결하는 문제와 핵심 가치를 실제 제품 하이라이트로 짧게 전달하는 브랜드 트레일러입니다. 상세 설명 영상은 공고문과 NCS 직무기술서를 [Kordoc](https://github.com/chrisryugj/kordoc) 4.9.1로 구조화하고, 사람이 `프로젝트관리` 세분류를 확정한 뒤, 면접기법별 작성 지침과 NCS KSA 기반 결과를 확인하는 실제 조작 흐름입니다.

- [32초 브랜드 홍보 영상](./docs/media/ncscope-promo.mp4): 문제 제기 → 핵심 차별점 → 실제 제품 하이라이트 → 서비스 실행 안내
- [2분 3초 상세 설명 영상](./docs/media/ncscope-explainer.mp4): 네 단계의 실제 조작과 근거 추적 화면 전체
- 결과 장면은 비식별화한 안전한 예시 응답을 재생했으며 화면에도 `예시 결과 재생 · API 키 미사용`을 명시했습니다. 영상 제작 과정에서 API 키를 입력하거나 노출하지 않았습니다.

## 현재 질문 생성 계약

- 사용자가 화면에 본인의 OpenAI API 키를 직접 입력합니다.
- 공개 생성 provider는 `openai_api` 하나뿐입니다. OpenRouter, 서버 공용키, Codex/Claude 구독 로그인으로 우회하지 않습니다.
- OpenAI 키는 해당 요청에만 사용하고 브라우저 저장소, 파일, DB에 저장하지 않습니다. 서버의 `OPENAI_API_KEY`도 대체 키로 읽지 않습니다.
- 공개 요청은 모델을 임의로 덮어쓸 수 없습니다. 승인된 역할별 구성인 Luna(NCS 후보 재정렬) → Terra(질문 작성) → Sol(독립 검수·재생성)을 사용합니다.
- OpenAI 키의 전송 대상은 공식 `https://api.openai.com/v1`로 고정합니다.
- 주질문은 최대 5개, NCS 세분류 1개, 면접형태 1개, 전체 요청 예산은 285초입니다.
- 구성된 NCS MCP가 반환한 KSA 원문과 K/S/A 유형, NCS 능력단위·요소·정의, 선택 면접형태, 실제 담당업무 맥락, 잠긴 `evidence_id`만 질문 생성 근거로 사용합니다. 이 표시는 연결된 MCP 계약에 대한 것이며 upstream DB 자체의 기관·버전 provenance를 별도로 증명하지는 않습니다.
- 역할별 모델은 Luna(NCS 후보 재정렬), Terra(질문 초안), Sol(독립 검수·실패 슬롯 재생성)로 분리합니다. 한 요청의 사용자 OpenAI 키만 사용하며 실제 사용 모델은 응답의 `ai_model_orchestration`에 기록합니다.
- 주질문, 꼬리질문, 평가포인트는 모두 AI가 작성합니다. 서버는 STAR 고정문구, 공통 상황, 조사 결합 문장, `server_ksa_fallback`, `template_fallback`을 만들지 않습니다.
- `evidence_id`는 근거 추적용이며 KSA 의미 측정성 통과 증명이 아닙니다.

### 면접 기본원칙 + 면접 종류별 작성 지침

`2020년 NCS기반 능력중심 채용모델 면접관 기본·심화` PDF는
[Kordoc](https://github.com/chrisryugj/kordoc) 4.9.1로
로컬 구조화한 뒤 사람이 검토해 `공통 면접 기본원칙 + 선택한 면접 종류별 작성 지침` 형태의 짧은 참고자료로 저장했습니다.
원자료의 경험·상황·토론면접 취지와 NCScope가 지원하는 인바스켓·직무지식·창의적
문제해결력면접의 지침, 개방형 질문, 답변 연동 꼬리질문, 관찰 가능한 평가 근거를
질문 작성 프롬프트에만 제공합니다. 원자료가 다루는 발표면접은 참조 기록에만 남기고
공개 선택지에서는 제외했습니다. 경험면접의 STAR도
주질문과 꼬리질문 전체에서 행동증거를 탐색하는 가이드일 뿐 고정 문구나 통과
조건이 아닙니다. 면접관 가이드 원본 PDF는 Vercel 요청마다 다시 파싱하지 않으며,
이 참고자료는 세분류 매칭·KSA 근거 확정·품질 점수·질문 공개 여부에 관여하지 않습니다.

배포본에는 원문 대신 검토된 요약인
`app/resources/ncs_interviewer_guide_2020.json`만 포함합니다. 원자료를 갱신할 때는
`scripts/verify_ncs_interviewer_guide_reference.py`로 해시와 Kordoc 구조 메타데이터를
다시 확인합니다.

## 독립 AI 품질검수

초안 생성 뒤 같은 OpenAI 연결에서 별도 고추론 품질검수를 실행합니다.

1. 한국어 문법과 자연스러움
2. 상황·직무행동·KSA 의미 연결
3. KSA 측정 가능성
4. 공고·직무기술서 근거성
5. 선택 면접기법 적합성
6. 꼬리질문의 주질문 연계성
7. 평가포인트의 관찰 가능성
8. KSA 명칭의 기계적 삽입 부재

검수는 문체 취향이 아니라 심각한 의미 이탈을 찾는 가이드 우선 정책입니다. KSA 연결·측정성, 직무 근거, 면접기법 적합성, 평가 관찰성은 3점 이상, 한국어 자연스러움·꼬리질문 연계·비기계적 표현은 2점 이상이면 통과할 수 있습니다. 전체 평균으로 추가 탈락시키지 않으며, 실질 중복은 별도 차단합니다. 실패 슬롯은 같은 공식 KSA와 직무 맥락에서 완전히 새로 한 번 생성한 뒤 다시 검수합니다.

호출 순서는 최대 `초안 생성 → AI 검수 → 실패 슬롯 재생성 → 최종 AI 검수`입니다. 최종 검수 실패나 검수 JSON 오류는 502, 네트워크 실패는 503, 전체 시간 초과는 504로 끝나며 질문을 반환하지 않습니다.

성공 응답의 공개 문항은 `question_source=openai_api`이고 `ai_quality_review.status=passed`입니다. 화면은 이 조건을 만족한 문항만 목록·복사·다운로드 대상으로 사용하며 `AI 품질검수 통과` 배지를 표시합니다. 최종 사람 검토 안내도 이 초안에만 표시합니다.

## 공공기관 데이터 처리 주의

API 데이터는 OpenAI 모델 학습에 기본적으로 사용되지 않지만, 기본 abuse monitoring 로그에는 최대 30일 보존 조건이 적용될 수 있습니다. 기관 정책과 계약 요건에 따라 OpenAI의 Zero Data Retention, Modified Abuse Monitoring, DPA 적용 가능 여부를 확인하세요.

- 개인정보, 민감정보, 비공개 보안정보는 업로드 전에 제거하거나 가리십시오.
- 담당자 이름, 전화번호, 이메일, 서명, 실제 지원자 사례를 외부 AI에 보내지 마십시오.
- 기관 정책상 외부 AI 전송이 금지된 자료에는 생성 기능을 사용하지 마십시오.
- 운영 HTTPS를 강제하고 proxy, WAF, APM, tracing, crash report에서 생성 요청 body와 Authorization 헤더 수집을 끄십시오.

공식 참고:

- [OpenAI API 데이터 제어](https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint)
- [OpenAI Enterprise Privacy](https://openai.com/enterprise-privacy/)

자세한 운영 설정은 [DEPLOYMENT.md](DEPLOYMENT.md), 보안 경계는 [SECURITY.md](SECURITY.md)를 확인하세요.

## 로컬 실행

요구 사항:

- Python 3.11+
- Node.js 20+
- KSA 조회 계약을 제공하는 별도 NCS_MCP

```powershell
pip install -r requirements.txt
npm ci
$env:NCS_MCP_URL="http://127.0.0.1:8778/mcp"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

브라우저에서 `http://127.0.0.1:8015`를 연 뒤 OpenAI API 키를 입력합니다. 키를 `.env`에 넣어도 공개 생성의 대체 키로 사용되지 않습니다.

## 주요 흐름

1. 공고문과 NCS 직무기술서 업로드
2. 문서 파서가 표 좌표와 함께 추출한 세분류 후보를 검토하고 세분류 1개 확정
3. 추출 요구능력단위는 참고 근거로 확인하고, 필요하면 확정 세분류의 공식 능력단위를 선택
4. 능력단위를 선택했으면 그 정확 일치 범위만, 선택하지 않았으면 확정 세분류 전체 범위에서 KSA 조회 및 `evidence_id` 잠금
5. 면접형태 1개를 반영해 OpenAI 질문 생성
6. 독립 OpenAI 품질검수
7. 필요 시 실패 슬롯 재생성 및 최종 검수
8. 통과 문항만 표시·복사·다운로드

### 세분류 우선 성능 해석

NCScope의 1차 제품 성능은 직무기술서에 명시된 **현행 공식 NCS 세분류명과
세분류 코드**를 정확히 복구하는 능력으로 판단합니다. 파싱 성공률, 세분류
precision/recall, 문서 단위 세분류 완전일치율, `현행 공식/구버전·비표준/자체개발/
미명시/모호` 상태 판정을 핵심 지표로 사용합니다.

능력단위·KSA는 확정 세분류에서 면접 질문 근거를 좁히는 심화 단계입니다. 따라서
`Strict pipeline-ready`는 세분류 추출 성능이 아니라 **세분류부터 능력단위 exact와
KSA 조회까지 한 번에 끝난 비율**입니다. 이 값을 세분류 정확도나 세분류 자동처리율로
해석하지 않습니다. 현재 공개 생성 경로는 안전을 위해 검토한 능력단위가 공식명과
정확히 일치할 때만 해당 능력단위로 KSA 범위를 좁힙니다. 화면은 세분류를 먼저
확정하고, 문서 추출값과 공식명이 정확히 일치하는 능력단위만 선택 후보로 연결합니다.
일치하지 않는 추출값은 세분류 확정을 막지 않고 진단으로 남기며, 사용자는 확정
세분류의 공식 능력단위를 직접 선택하거나 선택 없이 세분류 전체 KSA를 사용할 수 있습니다.

사람 골드 정확도는 기존 튜닝 표본과 분리한 NCS 공정채용 사이트 신규 직무기술서를
두 명 이상이 독립 검수하고 불일치를 조정한 뒤에만 발표합니다. 아래 에이전트 검토
동결셋과 실전 공고 집계는 회귀·운영 진단 근거이며 사람 골드 정확도가 아닙니다.

업로드는 PDF, HWP, HWPX, DOCX, TXT, 이미지와 이 문서들을 담은 ZIP을 지원합니다.
로컬은 Node/Kordoc을 직접 실행하고, Vercel은 같은 NCScope 배포의 인증된 비공개
Node 함수에서 Kordoc 4.9.1을 자체 실행합니다. 문서를 외부 변환 API로 보내지 않으며
`KORDOC_OFFLINE=1`로 외부 OCR·모델 다운로드를 막습니다. 운영 환경에는 저장소에
기록하지 않은 `KORDOC_BRIDGE_ED25519_PRIVATE_KEY`를 설정하며, Node 함수는 저장소에
고정한 공개키로 120초 유효 요청 서명을 검증합니다. 공유 비밀은 로컬·전환용
fallback으로만 지원합니다. Python 함수는 현재 요청의 고유 `VERCEL_URL`이 Vercel
Deployment Protection에 막힐 수 있어 봉인된 production alias를 전송 경로로 사용합니다.
다만 응답의 deployment URL·선택적 deployment ID·Git SHA가
현재 Python 함수의 `VERCEL_URL` 실행 환경과 일치해야만 runtime attestation을 결속합니다.
Kordoc 실행이 불가능해
대체 파서를 사용한 경우 응답과 화면에 실제 파서명을 표시하며, 표 좌표가 사라진
분류표는 대분류·중분류를 제거한 뒤 공식 NCS MCP exact 결과만 세분류 후보로 확정합니다.

### KSA 출처와 요구능력단위 잠금

직무기술서 표의 `필요지식`, `필요기술`, `직무수행태도`는 기관이 작성한 직무 맥락이며
공식 KSA 원문으로 취급하지 않습니다. Kordoc 표 파서는 `page`, `table_index`,
`label_cell`, `value_cell`, `row_span`, `column_span`과 해당 행의 채용분야·세분류 범위를
보존하고, `요구능력단위` 셀을 세분류별로 묶어 사람 검토 화면에 표시합니다.
화면 좌표는 행·열을 사람이 읽는 1부터 시작하는 번호로 변환해 보여주되 응답 원본은
0부터 시작합니다. `page=0`이거나 HTML/Markdown에서 표를 복구한 좌표는 원본 페이지를
확정하지 않고 `원본 페이지 미확정(복구 좌표)`으로 표시합니다. 범위가 잘못된 좌표는
임의 보정하지 않고 오류로 표시하며, 세분류에 연결되지 않은 능력단위는 확정 근거와
분리해 `세분류 미연결`로 둡니다.

검토 화면은 정확 매핑만 있을 때는 경고하지 않습니다. 대신 문서의 NCS 매핑 없음·미개발
선언, 현행 공식 목록 미등재, 공식명·코드·세분류 범위 모호성, PDF 표에서 세분류 열만
확인된 상태, 빈 세분류 셀, OCR 미적용·빈 파서 출력 같은 추출 실패를 서로 다른 상태로
표시합니다. 채용공고문 자체, 번역 직무, 의료 다직종, 일반 직무기술서에 세분류가
명시되지 않은 경우도 파싱 실패와 분리합니다.

검토가 끝나면 Vercel 앱은 DB 파일을 직접 열지 않고 `NCS_MCP_URL`의 `ncs_search`로
확정 세분류의 공식 능력단위를 조회합니다. 화면의 능력단위 선택지는 정확 세분류 조회
결과만 표시하며 비정밀 추천 결과는 섞지 않습니다. 문서 추출값은 공백·목록번호·NCS
코드 같은 표시 장식만 제거한 뒤 공식 능력단위명과 정규화 정확 일치할 때만 미리
선택됩니다. 미일치 추출값은 선택·KSA 대상에서 제외되지만 세분류 확정은 계속됩니다.
API에서 검토 능력단위를 직접 보낸 경우 하나라도 일치하지 않으면
`422 ncs_required_ability_unit_mismatch`로 중단하며 유사 능력단위로 자동 대체하지
않습니다. 선택한 능력단위 코드에 대해서만 구성된 NCS MCP의 `ncs_unit_detail`을
`include=["elements", "criteria", "ksa"]`, `text_version="raw"`로 호출하고, 그 응답의
K/S/A 행만 MCP 계약상 `factorSource=ncs-mcp`, `ksaStatus=official` 근거로 사용합니다. 성공 응답은
`required_ability_units_reviewed`, `required_ability_unit_lock_applied` 및 각 능력단위의
`requiredAbilityUnitName`, `requiredAbilityUnitMatch=exact`를 반환합니다.
배포용 경량 카탈로그와 원격 MCP 검색 결과는 모두 `usage_yn=Y`인 현행 1,094개
세분류의 코드 범위로 제한합니다. 비활성 세분류 15개와 그 연결 능력단위 153개는
`현행 공식`으로 승격하지 않으며, 서로 다른 현행 세분류에 같은 능력단위명이 있는
195개 정규화 이름도 세분류 코드 범위가 하나로 확정되지 않으면 KSA 조회 전에 거부합니다.

ALIO 조회는 `POST /api/alio/attachments`로 공고의 표 구획까지 읽어 공고문과 직무기술서를 구분하고, `POST /api/alio/attachment`로 선택한 공개 파일을 크기·redirect·host 제한 안에서 가져옵니다. 가져온 파일은 기존 업로드 칸과 동일한 Kordoc 파싱·서명된 사람 검토 흐름으로만 넘어가며 세분류를 자동 확정하거나 파일을 곧바로 모델에 전송하지 않습니다.

### ALIO 공고문·직무기술서 대량 코퍼스

최근 공고뿐 아니라 과거 등록일 구간을 31일 창으로 나눠 페이지를 순회하고, 각 공고의 `공고문`과 `직무기술서` 표 행에 연결된 파일을 모두 수집하는 재개 가능한 수집기를 제공합니다. 기본 결과는 Git에서 제외되는 `.local/alio_corpus`에 SQLite 코퍼스와 요약, 세분류 어휘 프로필로 저장됩니다.

```powershell
# 최근 1년, 최대 1,000개 공고(기본값)
python scripts/collect_alio_documents.py

# JOB-ALIO 과거 범위를 제한 없이 순회하고 원본 공개 첨부도 보관
python scripts/collect_alio_documents.py --start-date 2011-01-01 --limit 0 --keep-files
```

프로필 학습에는 문서가 세분류를 명시하고, 그 값이 로컬 공식 NCS 목록에 정확히 매칭되며, 한 문서에 세분류가 하나뿐인 사례만 사용합니다. 세분류가 없는 새 문서에는 일치 어휘와 학습 문서 수를 포함한 `review_required` 후보만 제안합니다. `NCS 미개발/매핑 없음`이 명시된 문서는 제안 대상에서 제외됩니다. 다른 위치의 프로필을 쓰려면 `ALIO_SCLASS_PROFILE_PATH`를 설정합니다.

### 저장 직무기술서 회귀·블라인드 품질 게이트

로컬 공개 코퍼스 206개(고유 내용 198개)는 Git에 넣지 않습니다. 평가는 세 층으로
분리합니다. 핵심 세분류 층은 파싱 성공, 현행 공식 세분류 인식, 세분류 매핑 상태와
문서 단위 완전일치를 봅니다. 심화 진단 층은 능력단위 범위·코드 후보와 KSA 가용성을
별도로 기록합니다. 별도로 동결한 36개
source-only 블라인드 세트는 최신 파서 출력 없이 세 명의 에이전트가 원문 표만 판정한 뒤
한 번 비교했습니다. 이 기준표는 사람 검토나 골드 데이터가 아니며, 결과도 그 한계를
명시합니다.

릴리스 판정은 운영 코퍼스의 세분류 지표와 홀드아웃의 세분류 코드 precision/recall,
문서 단위 세분류 완전일치만 사용합니다. 능력단위 범위·코드와 KSA 지표는
`downstream_advisory`에 계속 기록하지만 세분류 코어 릴리스를 막지 않습니다. 다만
기준표 provenance, 해시, 건수, 범위, 분모 무결성은 이전과 같이 fail-closed입니다.

동결 결과는 `tests/fixtures/stored_jd_final_blind_{reference,freeze,freeze_addendum,result,comparison_ledger}.json`
에 있습니다. 결과 JSON은 점수·게이트·벤치마크 원본의 실제 해시를 검증하는 생성기로만
만들며, 비교 원장은 같은 동결셋의 다른 비교 결과를 거부합니다. 전체 코퍼스 값은 독립
정확도가 아닌 운영 진단용 후보 커버리지로 별도 표시합니다. 첫 비교 결과는 세분류 코드 P 97.44/R 100, 세분류 문서 완전일치 95.83,
능력단위-세분류 범위 P 97.04/R 97.62, 공식 능력단위 코드 P 91.67/R 99.59,
확정 코드 KSA 반환 100%입니다. 이후 실전 공고에서 발견한 번호가 붙은 능력단위 장식을
정확히 해석하는 로직이 동결셋 1건의 코드 출력을 바꿨으므로, 이 첫 결과는 변경 전 1회
비교 기록으로만 보존합니다. 현 코드 재검사는 세분류 코드 P 96.20/R 100으로 모든
게이트를 통과하지만 독립 블라인드 점수로 주장하지 않습니다. 생성기도 이 상태에서 새
블라인드 결과 생성을 의도적으로 거부합니다.

```powershell
$env:NCS_MCP_URL='http://127.0.0.1:8766/mcp'
python scripts/benchmark_stored_jd_corpus.py

python scripts/score_stored_jd_holdout.py `
  tests/fixtures/stored_jd_final_blind_reference.json `
  tmp/stored_jd_benchmark/<latest>.csv `
  --selection-manifest-json tmp/stored_jd_final_blind/final_blind_manifest.json `
  --require-selection-manifest

python scripts/check_stored_jd_quality_gate.py `
  tmp/stored_jd_benchmark/<latest>.json `
  --holdout-score-json tmp/stored_jd_holdout_score/<latest>.json `
  --expected-holdout-records 36

# 논리 표 좌표 계약: 206개/고유 198개, 좌표 형상과 직접·복구 좌표를 분리 집계
python scripts/audit_stored_jd_coordinate_contract.py `
  --input-dir tmp/alio_jd_200_mcp `
  --expected-files 206 `
  --expected-unique-contents 198

# KSA 강한 계약: benchmark-selected probe 집합의 지식·기술·태도 3종과 수행준거 확인
python scripts/audit_stored_jd_ksa_contract.py `
  tmp/stored_jd_benchmark/stored_jd_benchmark_20260825_163108.csv `
  --expected-unit-codes 555 `
  --expected-benchmark-rows 206 `
  --expected-benchmark-sha256 c28e9b935f3c7c7115886b7ac33f88b06e9d09053f81d4d916ff3c4dbdbac96e `
  --expected-catalog-sha256 8f7bdc665b06ea560d2414c4acb6e1e4088fac37455b8ae8ba864775c13b0357 `
  --expected-code-set-sha256 5de2eacab7202672ffb31711276acdc3eb324d95ab257ac9bd22a4becd05f61e `
  --expected-client-sha256 bbafa3813e06d9e2b09c2c20621114aefb6bfe11de2f171119875a6b23bf7430 `
  --expected-audit-script-sha256 b2ce756a9cfb39d68b27db4d6027bf93326d9475f65c1fb56cd98a891be5629d `
  --max-runtime-seconds 600 `
  --require-input-digests

# 배포 active catalog 13,282개 능력단위 전체를 같은 계약으로 확인
python scripts/audit_stored_jd_ksa_contract.py `
  --all-active-catalog-units `
  --expected-unit-codes 13282 `
  --expected-catalog-sha256 8f7bdc665b06ea560d2414c4acb6e1e4088fac37455b8ae8ba864775c13b0357 `
  --expected-code-set-sha256 cc09e69442e780b319fb772a7459fd49066f1f57790bf9140e8243e29d066a01 `
  --expected-client-sha256 bbafa3813e06d9e2b09c2c20621114aefb6bfe11de2f171119875a6b23bf7430 `
  --expected-audit-script-sha256 b2ce756a9cfb39d68b27db4d6027bf93326d9475f65c1fb56cd98a891be5629d `
  --max-runtime-seconds 600 `
  --require-input-digests `
  --max-factors-per-unit 3
```

최신 저장 코퍼스가 선택한 고유 probe 코드 555개를 대상으로 각 코드당 최대 12개
요인을 조회한 KSA 계약 감사에서는 555/555개 모두 지식·기술·태도 3종을 갖췄고,
반환된 6,652/6,652건에 수행준거와 configured NCS MCP client contract marker가
있었고, 요청 능력단위 코드와 MCP 응답 코드도 6,652/6,652건 일치했습니다. 이 555개에는 문서에서 능력단위를 직접 추출하지 못한 경우 벤치마크가
가용성 확인용으로 고른 코드도 포함되며, 그중 14개는 그런 probe에서만 나타납니다.
따라서 이는 "실제 추출된 555개 코드" 정확도나 upstream 공식 DB의 독립 provenance
인증이 아닙니다. 입력 CSV·206행·코드 집합·active catalog·MCP client 해시를 함께
고정한 configured `ncs_unit_detail` 응답 계약입니다.

별도의 active catalog 전체 감사에서는 13,282/13,282개 능력단위 모두 지식·기술·태도
3종을 반환했고, 1종당 1개씩 선택한 39,846/39,846행 모두 수행준거, 정확한 요청-응답
능력단위 코드 결합, configured client contract marker를 가졌습니다. 이 결과도
catalog·13,282개 코드 집합·client·감사 스크립트
해시에 결박되지만, 현재 MCP 계약이 upstream DB 식별자나 버전을 노출하지 않으므로 해당
DB 자체의 독립 provenance 인증으로 해석하지 않습니다.

v1.4.12의 206건 파싱, 비인간 동결셋 점수, 선택 probe 및 active catalog 전수 감사의
공개 가능한 집계·해시는 [release evidence](reports/ncscope_1_4_12_release_evidence.json)에
묶었습니다. 원문 문서와 MCP 응답 본문은 포함하지 않습니다.

현재 좌표 회귀는 세분류·능력단위 2,197/2,197건의 논리 좌표 형상과 `raw_cell_text`의
실제 `value_cell` 연결이 모두 유효합니다. 그중 Kordoc이 직접 준 표 좌표는 1,491건
(67.87%)이며, 나머지 706건은 코드 앵커 Kordoc 블록과 HTML/Markdown 논리 표 복구
좌표로 분리됩니다. 최종 고유
능력단위명과 직접·복구 논리 좌표 전체의 문서 내 정확 이름 연결 진단 87.55%는
목록·중복·표 밖 항목을 분모에서 제거하지 않은 비재현율 진단값이며, 직접 표 좌표
연결률이나 추출 recall로 해석하지 않습니다.

### NCS 현재 공고 실전 진단

NCS 채용공고 목록·상세·첨부를 현재 사이트에서 읽어 실제 배포 API와 같은 경로로
검사할 수 있습니다. 결과에는 파일명·공고명·원문·예측값을 남기지 않고 비공개 HMAC
식별자와 집계만 기록합니다. 공고 상세 API가 선언한 세분류 합집합을 분모로 삼은 값은
공고 단위 진단이며, 개별 첨부가 그 모든 세분류를 반드시 포함한다는 골드 정답은
아닙니다. 첨부별 일치율은 정확도 근거가 아닌 진단으로만 표시합니다.
아래 `KSA probe 가용 x/x`는 공고별 능력단위 코드 조회(중복 포함)에서 코드당 최대
1개 KSA 요소를 요청해 1행 이상 반환된 횟수입니다. 고유 능력단위 수, K/S/A 세 유형
전체, 또는 전체 KSA 원문 행 완전성을 뜻하지 않습니다.

```powershell
$env:NCS_RECRUITMENT_LIVE_DIGEST_KEY='<32바이트 이상의 비공개 임시키>'
$env:NCS_MCP_URL='http://127.0.0.1:8766/mcp'
python scripts/benchmark_ncs_recruitment_live.py `
  --max-postings 20 `
  --page-limit 8

# 같은 시점의 상위 20개를 건너뛴 비중복 검증 창
python scripts/benchmark_ncs_recruitment_live.py `
  --max-postings 20 `
  --skip-postings 20 `
  --page-limit 8
```

#### 공정채용 신규 사람 골드셋 준비

사람 골드는 기존 튜닝 문서와 겹치지 않는 신규 공고를 사용합니다. 일반 실전 진단
보고서는 계속 HMAC 식별자와 집계만 남깁니다. 골드 검수용 원문이 필요할 때만
`--private-gold-source-dir`를 명시하면 다운로드 문서와 `case_id` source index를
Git에서 제외되는 `tmp/` 아래에 별도로 보관합니다. 원래 파일명·공고명·자동 예측값은
검수 양식에 넣지 않습니다.

```powershell
$env:NCS_RECRUITMENT_LIVE_DIGEST_KEY='<32바이트 이상의 비공개 임시키>'
$env:NCS_MCP_URL='http://127.0.0.1:8766/mcp'

# 신규 공고 진단과 동시에 비공개 검수 원문/source index 보관
python scripts/benchmark_ncs_recruitment_live.py `
  --max-postings 100 `
  --page-limit 20 `
  --private-gold-source-dir

$benchmark = Get-ChildItem tmp/ncs_recruitment_live/ncs_recruitment_live_*.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

# Reviewer A/B 블라인드 양식, 조정 양식, do-not-tune 원장, 무결성 해시 생성
python scripts/prepare_ncs_recruitment_goldset.py `
  $benchmark.FullName `
  tmp/ncs_recruitment_goldset/source_documents/source_index.local.csv `
  --output-dir tmp/ncs_recruitment_goldset/seed `
  --tuning-manifest tmp/<tuning-eval-manifest-1>.csv `
  --tuning-manifest tmp/<tuning-eval-manifest-2>.csv `
  --exclude-tuning-overlap

# 자동 예측·split·원본 경로를 제외한 source-only 패킷 생성
python scripts/prepare_ncs_recruitment_source_packets.py `
  tmp/ncs_recruitment_goldset/seed/goldset_review_manifest.local.json `
  --output-dir tmp/ncs_recruitment_goldset/seed
```

출력은 `reviewer_a.local.csv`, `reviewer_b.local.csv`, `adjudication.local.csv`,
`do_not_tune.local.csv`, `goldset_review_manifest.local.json`, `integrity.local.json`입니다.
리뷰어 CSV는 `item_id`, 문서 해시와 빈 답안 필드만 포함합니다. validation/holdout split,
로컬 원본 경로, 출처 URL, 공고명, 파일명과 NCScope 자동 예측은 리뷰어에게 제공하지
않습니다. 두 리뷰어는 상대 답을 보지 않고 source-only 패킷에서 `mapping_state`, 공식
세분류명·코드와 정확한 원문 근거를 기입합니다. 분할은 문서 SHA-256과 공고 ID의 연결
요소로 결정되어 같은 내용이나 같은 공고의 다른 첨부가 validation과 holdout에 동시에
들어가지 않습니다. 튜닝 문서와 연결된 공고의 모든 첨부도 함께 제외하고 감사 원장에
문서·공고·split-group 해시를 남깁니다.

후보 수집 범위 밖의 튜닝 문서까지 연결하려면 CSV/JSON 튜닝 매니페스트의 각 문서
해시 행에 `posting_id` 또는 `posting_ids`를 함께 넣어야 합니다. 식별자가 없는 외부
튜닝 해시가 있으면 component-wide 제외를 조용히 축소하지 않고 준비 단계가 실패합니다.
튜닝 매니페스트를 지정하면서 `--exclude-tuning-overlap`를 생략하는 것도 실패합니다.
`posting_id`는 비어 있지 않은 단일 문자열, `posting_ids`는 JSON에서는 중복·공백이 없는
문자열 배열, CSV에서는 같은 형식의 유효한 JSON 배열이어야 하며 손상된 값은 거부합니다.
원본 benchmark·source-index·각 tuning manifest와 정규화된 문서/공고 identity 집합,
제외 후 records는 `candidate_exclusions.local.json`에 해시로 기록되고 seed integrity의
여섯 번째 artifact로 봉인됩니다. 제외를 적용하지 않은 seed도 현재 integrity version과
`applied=false` binding을 명시합니다. finalizer는 binding 제거를 legacy downgrade로
수락하지 않으며, 검증한 제외 provenance를 최종 reference와 integrity에 다시 봉인합니다.

두 답이 완료되면 다음처럼 일치 항목을 잠그고 불일치만 제3자에게 전달합니다.

```powershell
python scripts/prepare_ncs_recruitment_adjudication.py `
  --manifest tmp/ncs_recruitment_goldset/seed/goldset_review_manifest.local.json `
  --seed-integrity tmp/ncs_recruitment_goldset/seed/integrity.local.json `
  --do-not-tune tmp/ncs_recruitment_goldset/seed/do_not_tune.local.csv `
  --reviewer-a tmp/ncs_recruitment_goldset/seed/reviewer_a_completed.local.csv `
  --reviewer-b tmp/ncs_recruitment_goldset/seed/reviewer_b_completed.local.csv `
  --adjudication-template tmp/ncs_recruitment_goldset/seed/adjudication.local.csv `
  --packet-index tmp/ncs_recruitment_goldset/seed/source_packet_index.local.json `
  --packet-integrity tmp/ncs_recruitment_goldset/seed/source_packet_integrity.local.json `
  --output-dir tmp/ncs_recruitment_goldset/seed/adjudication_work

# 제3자는 split·로컬 경로가 없는 adjudicator_decisions.local.csv만 작성
python scripts/apply_ncs_recruitment_adjudication.py `
  --worklist tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudication_ready.local.csv `
  --disputes tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudication_disputes.local.json `
  --decisions tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudicator_decisions.local.csv `
  --worklist-integrity tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudication_worklist_integrity.local.json `
  --output-dir tmp/ncs_recruitment_goldset/seed/adjudication_work/completed

python scripts/finalize_ncs_recruitment_goldset.py `
  --manifest tmp/ncs_recruitment_goldset/seed/goldset_review_manifest.local.json `
  --seed-integrity tmp/ncs_recruitment_goldset/seed/integrity.local.json `
  --do-not-tune tmp/ncs_recruitment_goldset/seed/do_not_tune.local.csv `
  --reviewer-a tmp/ncs_recruitment_goldset/seed/reviewer_a_completed.local.csv `
  --reviewer-b tmp/ncs_recruitment_goldset/seed/reviewer_b_completed.local.csv `
  --adjudication tmp/ncs_recruitment_goldset/seed/adjudication_work/completed/adjudication_completed.local.csv `
  --adjudication-integrity tmp/ncs_recruitment_goldset/seed/adjudication_work/completed/adjudication_decisions_integrity.local.json `
  --adjudication-worklist-integrity tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudication_worklist_integrity.local.json `
  --adjudication-disputes tmp/ncs_recruitment_goldset/seed/adjudication_work/adjudication_disputes.local.json `
  --source-packet-index tmp/ncs_recruitment_goldset/seed/source_packet_index.local.json `
  --source-packet-integrity tmp/ncs_recruitment_goldset/seed/source_packet_integrity.local.json `
  --output-dir tmp/ncs_recruitment_goldset/final `
  --reviewer-a-kind ai_agent `
  --reviewer-b-kind ai_agent `
  --reviewer-a-provenance '<source-only reviewer A provenance>' `
  --reviewer-b-provenance '<source-only reviewer B provenance>' `
  --adjudicator-kind ai_agent `
  --adjudicator-provenance '<disagreement-only adjudicator provenance>'
```

봉인기는 두 답의 exact consensus만 자동 채택하고, 불일치는 서로 다른 제3자의 완료
판정이 없으면 실패합니다. 제3자 양식도 split, 로컬 원본 경로와 자동 예측을 포함하지
않으며, 완료 결정은 원래 내부 worklist와 별도 무결성 단계에서 병합됩니다. 최종 근거는
봉인된 source packet에 실제 존재하는 정확한 인용문이어야 합니다. 현행 공식
상태는 배포 카탈로그의 정확한 8자리 코드·표기
쌍만 허용하며, 현행 공식명만 적고 `legacy_or_nonstandard`로 분류하는 답도 거부합니다.
AI 에이전트가 검수한 결과는 항상
`independent_ai_agent_adjudicated_reference_not_human_gold`로 표시되고 사람 골드가
되지 않습니다. 현재 source packet은 점수 계산 경로와 같은 Kordoc 추출 계열이므로
`human_gold_eligible=false`로 봉인됩니다. 두 독립 원문 렌더/OCR 채널과 사람 교차검증을
추가하기 전에는 사람 리뷰어를 지정해도 최종 사람 골드로 봉인할 수 없습니다. 향후 이
독립 추출 요건을 충족한 뒤에도 두 독립 리뷰어와 조정자가 모두 실제 사람이고 명시적
attestation을 제공해야 합니다. 인간 골드 attestation은 최종 reference와
`final_integrity.local.json`에 함께 저장되고 canonical JSON SHA-256으로 봉인되며, 누락·변조 시
점수 계산기가 fail-closed로 중단합니다. AI 또는 혼합 검수는 긍정적인 인간 골드
attestation을 가질 수 없습니다. 이 attestation은 변조 탐지를 위한 자체 선언형 운영
증빙이며, 검수자의 외부 신원 인증이나 전자서명을 대신하지 않습니다.

봉인한 로컬 기준표는 실제 API를 통해 다시 파싱해 점수화합니다. HTTP 429나 전송 장애는
문서 오답으로 넣지 않고 전체 실행을 중단하거나 제한된 `Retry-After` 재시도를 수행합니다.
정확도 실행은 loopback 서버의 `local_node_subprocess` Kordoc과 직접 TXT만 허용합니다.
인증된 운영 bridge나 HWP/PDF fallback으로 실행 mode가 바뀌면 문서 오답으로 낮추지 않고
전체 점수화를 중단합니다. 채점기는 비공개 문서 bytes를 전송하기 전에
`/api/jd/parse-review/runtime-policy`를 호출해 동일 runtime attestation과 `local-only`
지원 여부를 확인하고, 이후 모든 요청에 강제 정책 헤더를 보냅니다. 서버는 이 헤더가
있으면 원격 Kordoc bridge 호출을 금지합니다. 결과 writer도 비어 있지 않은 local-only
parser execution identity 원장을 다시 검증한 뒤에만 점수 artifact를 씁니다.
핵심 지표는 기준 상태가 `official_current`인 문서만의 세분류명·코드와 `(세분류명, 코드)`
쌍 P/R/F1, 문서 exact입니다. 이름 집합과 코드 집합이 각각 같아도 쌍이 뒤바뀌면 오답이며,
legacy·자체개발·미명시·모호·판독불가는 별도 all-state 진단으로 남깁니다.

```powershell
python scripts/score_ncs_recruitment_goldset.py `
  --reference-json tmp/ncs_recruitment_goldset/final/ncs_recruitment_final_reference.local.json `
  --reference-csv tmp/ncs_recruitment_goldset/final/ncs_recruitment_final_reference.local.csv `
  --reference-integrity tmp/ncs_recruitment_goldset/final/final_integrity.local.json `
  --source-dir tmp/ncs_recruitment_goldset/source_documents `
  --output-dir tmp/ncs_recruitment_goldset/score `
  --base-url http://127.0.0.1:8000

# 최초 전체 확인 뒤의 개선 루프: 전체 봉인 해시는 검증하되 holdout은 읽거나 파싱하지 않음
python scripts/score_ncs_recruitment_goldset.py `
  --reference-json tmp/ncs_recruitment_goldset/final/ncs_recruitment_final_reference.local.json `
  --reference-csv tmp/ncs_recruitment_goldset/final/ncs_recruitment_final_reference.local.csv `
  --reference-integrity tmp/ncs_recruitment_goldset/final/final_integrity.local.json `
  --source-dir tmp/ncs_recruitment_goldset/source_documents `
  --source-index tmp/ncs_recruitment_goldset/source_documents/source_index.local.csv `
  --output-dir tmp/ncs_recruitment_goldset/score-validation `
  --base-url http://127.0.0.1:8000 `
  --split gold_validation
```

2026-08-25(KST) 독립 AI 2인+제3자 조정으로 봉인한 첫 52문서 참고 기준표는 47건
exact consensus, 5건 제3자 조정이며 사람 골드가 아닙니다. 현행 공식 세분류 43문서의
API 재평가는 파싱 성공 52/52, 세분류명 P/R/F1 97.52/97.52/97.52, 코드
P/R/F1 100/97.52/98.74, 문서 exact 97.67%(42/43)였습니다. validation의 현행 공식
34문서는 명칭·코드·문서 exact가 모두 100%, 한 번만 확인한 holdout 현행 공식 9문서는
명칭 F1 85.71, 코드 F1 92.31, 문서 exact 88.89%(8/9)입니다. holdout은 이 확인 뒤
규칙 튜닝에 사용하지 않습니다.
이 52문서와 아래 198문서의 split 성능은 과거 v1 `document_sha256` split을
점수화한 역사적 결과입니다. v2 connected-component split의 현행 성능으로
해석하면 안 되며, v2의 새 validation/holdout 점수는 사람 골드 봉인 후에만
공개합니다.

같은 날 게시판 앞 구간과 충분히 떨어진 신규 80개 공고에서 고유 직무기술서 198개
(199 cases)를 다시 봉인했습니다. 알려진 튜닝·기존 평가 문서 SHA-256 238개와 겹치는
문서는 0개이며, 두 독립 AI 리뷰어가 171건에 exact consensus를 이루고 나머지 27건은
세 번째 AI가 source-only 원문으로 조정했습니다. 이 기준표도
`independent_ai_agent_adjudicated_reference_not_human_gold`이며 사람 골드가 아닙니다.

과거 v1 split에서 튜닝 전 한 번만 실행한 전체 기준선은 198/198 파싱 성공, 현행 공식 세분류 170문서의
명칭 F1 96.40, 코드 F1 98.56, 문서 exact 84.12%(143/170)였습니다. 전체 상태 진단은
명칭 F1 94.29, 코드 F1 95.81, 문서 exact 74.75%(148/198)입니다. 이 최초 실행 뒤
holdout 39문서의 개별 오류는 열람하거나 규칙 수정에 사용하지 않았습니다.

과거 v1 validation 159문서에서만 일반화 가능한 문서 구조 오류를 고친 재평가는 현행 공식
137문서의 명칭 F1 99.76, 코드 F1 99.88, 문서 exact 99.27%(136/137)였고 전체 상태
진단은 명칭 F1 96.94, 코드 F1 96.80, 문서 exact 87.42%(139/159)였습니다. 남은 공식
1건은 정확한 세분류 코드 후보가 능력단위 근거와 함께 제시되지만 자동 확정 금지 정책으로
보수적으로 제외된 경우입니다. 비공식 상태가 섞인 문서는 현재 문서 단위 단일 상태 스키마와
제품의 라벨별 상태 표현이 다르므로 전체 상태 값은 진단용으로만 사용합니다.

2026-08-25(KST) 현재 20개 공고·52개 직무기술서 실전 진단은 처리 실패 0건, 공고 선언
합집합 기준 세분류 P 98.17/R 82.31, 공고 완전일치 60%(12/20)입니다. 이 수치는 같은
실전 표본에서 발견한 장식 규칙을 반영한 튜닝 확인값이므로 독립 홀드아웃 성능으로
주장하지 않습니다. 좌표 형상은 418/418, 최종 고유 능력단위의 논리 좌표 정확 이름
연결 진단은 272/308(88.31%), KSA probe 가용은 1,278/1,278입니다.

같은 날 기존 20개를 건너뛴 비중복 20개 공고·68개 직무기술서는 처리 실패 0건,
공고 선언 합집합 기준 P 95.15/R 93.33, 완전일치 65%(13/20), 좌표 형상
293/293, KSA probe 가용 1,187/1,187이었습니다. 기존 튜닝 표본과 공고 ID는 겹치지 않지만
같은 게시판의 인접 날짜를 순서대로 고른 표본이므로, 시기·기관 분포까지 독립인
통계 표본으로 과장하지 않습니다.

상위 40개를 건너뛴 또 다른 비중복 20개 공고·54개 직무기술서도 처리 실패 0건,
P 95.77/R 91.28, 완전일치 55%(11/20), 좌표 형상 680/680, KSA probe 가용
1,623/1,623이었습니다. 두 비중복 창 40개를 합치면 TP 234/FP 11/FN 20,
P 95.51/R 92.13, 완전일치 60%(24/40)입니다.

상위 60개를 건너뛴 세 번째 비중복 20개 공고·23개 직무기술서는 P 97.83/R 88.24,
완전일치 70%(14/20), 좌표 형상 95/95, KSA probe 가용 644/644였습니다. 튜닝에 사용하지
않은 세 창 60개를 합치면 TP 279/FP 12/FN 26, P 95.88/R 91.48, 완전일치
63.33%(38/60)입니다. 실전 명령은 기본적으로 P 90/R 80/완전일치 50, 좌표 형상
100, KSA 100을 모두 요구하며 미달하면 종료 코드 1을 반환합니다.

세 비중복 창의 완전일치 실패 22건을 사후 분류하면 공고 API의 세분류 합집합과 개별
직무기술서 범위가 다른 사례 12건, 문서가 공고 API보다 더 많은 값을 명시한 사례 3건,
파서·정규화 누락 가능성이 높은 사례 4건, 공식 매핑 모호성 2건, 빈 세분류 1건입니다.
이 분류는 성능을 다시 계산하는 정답 보정이 아니라 오류 원인을 분리한 진단입니다. 해당
60개에 맞춘 기관·세분류 예외나 임계값 튜닝은 하지 않으며, 공고 합집합 정확도와 개별
문서 추출 정확도를 별도 지표로 유지합니다.

같은 고정 로직으로 상위 80개를 건너뛴 네 번째 비중복 20개 공고·35개 직무기술서를
추가 검증한 결과도 처리 실패 0건, P 97.30/R 93.51, 완전일치 65%(13/20), 좌표 형상
211/211, KSA probe 가용 1,032/1,032로 게이트를 통과했습니다. 네 창 80개를 합치면
TP 351/FP 14/FN 31, P 96.16/R 91.88, 완전일치 63.75%(51/80)입니다. 이 네 창은
서로 공고 ID가 겹치지 않지만 같은 게시판과 인접 시기에서 순서로 선택했으므로 완전한
통계적 독립 표본으로 주장하지 않습니다.

상위 100개를 건너뛴 다섯 번째 비중복 20개 공고·33개 직무기술서도 처리 실패 0건,
P 95.12/R 86.67, 완전일치 70%(14/20), 좌표 형상 477/477, KSA probe 가용
1,010/1,010으로 통과했습니다. 다섯 창 전체는 100개 공고·213개 문서, TP 429/FP
18/FN 43, P 95.97/R 90.89, 완전일치 65%(65/100), 좌표 형상
1,756/1,756, KSA probe 가용 5,496/5,496입니다. 이 합계 역시 같은 게시판·인접 시기의
순차 비중복 검증이며 기관·시기 분포까지 독립인 무작위 표본은 아닙니다.
완전일치 실패 35건의 진단 분류는 공고 합집합과 문서 범위 불일치 16건, 파서·정규화
누락 가능성 9건, 문서에는 있으나 공고 합집합에 없는 값 5건, 공식 매핑 모호성 2건,
빈 값·매핑 없음 3건입니다. 따라서 공고 단위 완전일치 실패 전체를 파서 실패율로
해석하지 않습니다.

새 set-relation 진단을 켠 뒤 상위 120개를 건너뛴 여섯 번째 비중복 20개 공고·37개
직무기술서도 처리 실패 0건, P 100/R 98.48, 완전일치 95%(19/20), 좌표 형상
92/92, KSA probe 가용 853/853으로 통과했습니다. 불일치 1건은 원인을 단정하지 않는
`source_union_superset_possible`로 기록됐습니다. 여섯 창 전체는 120개 공고·250개
문서, TP 494/FP 18/FN 44, P 96.48/R 91.82, 완전일치 70%(84/120), 좌표 형상
1,848/1,848, KSA probe 가용 6,349/6,349입니다.

문서 상태 집계까지 켠 현재 게시판 정렬 141~160번째(상위 140개를 건너뜀) 일곱 번째
비중복 20개 공고·44개 직무기술서도
P 96.81/R 88.35, 완전일치 60%(12/20), 좌표 형상 221/221, KSA probe 가용
1,190/1,190으로 통과했습니다. 불일치 8건은 합집합 우세 가능 5건, 문서 extra 2건,
교차 불일치 검토 1건으로 분리됐고 문서 상태 44건은 공식 exact 42건, 공식 매핑 모호
1건, NCS 표만 감지 1건으로 합계가 일치합니다. 정렬 1~20번째 튜닝 표본은 이 누적에서
제외하므로 일곱 비중복 창(21~160번째) 전체는 140개 공고·294개 문서,
TP 585/FP 21/FN 56, P 96.53/R 91.26, 완전일치 68.57%(96/140), 좌표 형상
2,069/2,069, KSA probe 가용 7,539/7,539입니다.

같은 시점에 상위 160개를 건너뛴 다음 창은 공고 0건을 반환해, 감사 경로로 조회 가능한
현재 게시판 160개를 모두 소진했음을 확인했습니다. 튜닝에 사용한 정렬 1~20번째까지
단순 합산한 전체 게시판 관측값은 160개 공고·346개 문서, TP 692/FP 23/FN 79,
P 96.78/R 89.75, 완전일치 67.50%(108/160), 좌표 형상 2,487/2,487,
KSA probe 가용 8,817/8,817입니다. 이 합계는 첫 20개 튜닝 표본을 포함하고 같은 날의
순차 게시판 전수 관측이므로 독립 홀드아웃이나 장기 성능 추정치로 사용하지 않습니다.

같은 날 18:48 KST에 [NCS 공정채용 채용공고](https://www.ncs.go.kr/blind/bl04/RecrtNotifList.do)
게시판이 갱신된 뒤 상위 20개 공고를 다시 캡처했습니다. 공개 직무기술서 68/68개를
현재 v1.4.12 로컬 Kordoc으로 처리했고 공고 합집합 기준 P 99.18/R 94.53,
완전일치 60%(12/20), 좌표 형상 407/407, KSA probe 1,515/1,515였습니다.
동일한 순차 loopback 실행은 총 10.91 MiB를 53.08초에 처리해 문서당 평균 781ms,
p50 675ms, p95 1.49초였습니다. HWP 9건은 평균 305ms·최대 384ms, PDF 59건은
평균 853ms·p95 1.62초였고, PDF 초기화가 포함된 단일 최댓값은 5.65초였습니다. 이는
운영 네트워크 지연이 아니라 동일 머신의 파서 실행시간이며 Vercel 응답시간과 섞지 않습니다.
기존 평가 후보 250개와 문서 SHA-256 중복은 0개였으며, 공고-문서 연결요소 기준으로
validation 44개와 holdout 24개를 교차 누출 없이 준비했습니다. 자동 예측과 split을
숨긴 source-only 패킷도 68개 생성했지만 같은 Kordoc 추출 계열이므로
`human_gold_eligible=false`입니다. 이는 신규 사람 검수 후보의 준비 현황이지 사람 골드
정확도나 앞선 게시판 전수 관측과 직접 합산할 장기 추정치가 아닙니다.

이 68개 source-only 패킷을 서로의 답을 보지 않은 두 AI 에이전트가 독립 검토한 뒤,
65건 exact consensus와 3건의 별도 AI 중재로 참고 기준표를 봉인했습니다. 현행 공식
세분류 57문서에서 명칭 P/R/F1은 99.39/100/99.69, 코드와 `(명칭, 코드)` 쌍 F1은
모두 100%, 문서 exact는 98.25%(56/57)였습니다. 한 번만 확인한 holdout의 현행 공식
21문서는 명칭 P/R/F1 98.89/100/99.44, 코드·쌍 F1 100%, 문서 exact
95.24%(20/21)였습니다. 전체 68문서의 상태 포함 진단은 명칭 F1 97.97, 코드·쌍 F1
92.57, 상태·문서 exact 83.82%입니다. 후자는 현행 공식 라벨과 자체개발·구버전 라벨이
한 문서에 섞인 경우를 문서 단위 단일 상태로 표현하는 스키마 차이까지 포함합니다.
이 결과 역시 `independent_ai_agent_adjudicated_reference_not_human_gold`이며 holdout
개별 오류는 규칙 수정에 사용하지 않습니다.

로컬/운영 동일성은 문서명과 본문을 보고서에 남기지 않는 HMAC 계약으로 검사합니다.
원격 문서 업로드가 발생하므로 공개 문서 처리 승인을 확인한 뒤에만 명시적 플래그를
사용합니다.

```powershell
$env:NCSCOPE_PARITY_DIGEST_KEY='<32바이트 이상의 비공개 임시키>'
python scripts/verify_stored_jd_local_vercel_parity.py `
  --allow-remote-document-upload
```

### Vercel 릴리스 안전 규칙

`scripts/prepare_vercel_build_sandbox.py`는 추적 파일이 수정·staged 된 상태를 거부하고,
필수 런타임 서비스와 두 경량 NCS 카탈로그가 Git 스냅샷에 포함됐는지 검사합니다.
Windows에서 만든 `.vercel/output`에는 `sharp` 같은 네이티브 Node 의존성이 Windows용으로
묶일 수 있으므로 운영에는 `vercel deploy --prebuilt`를 사용하지 않습니다. 커밋 뒤 깨끗한
샌드박스에서 `vercel deploy --prod --force --yes`로 Vercel의 Linux 원격 소스 빌드를
실행하고, 실제 Kordoc 문서 smoke와 206개 로컬/운영 동일성 검사를 통과한 배포만 유지합니다.
Vercel Python 함수는 Node 설치에 사용한 `package-lock.json` 자체를 번들에서 제거하므로,
`app/data/node_package_lock_attestation.json`에 잠금파일 전체의 Git-text SHA-256과 Kordoc의
정확한 버전·integrity를 함께 봉인합니다. 로컬·CI는 줄바꿈을 LF로 정규화해 이 스냅샷을
원본 잠금파일과 매번 대조하고, 운영 runtime attestation은 번들에 남은 동일 스냅샷의
해시를 검증합니다. Vercel이 함수 조립 중 다시 쓰는 `vercel.json`도 같은 원칙으로
`app/data/vercel_config_attestation.json`에 원본 Git-text SHA-256을 봉인합니다.

## API 실패 계약

| 상태 | 코드 | 의미 |
|---|---|---|
| 400 | `openai_api_key_required` | 요청 단위 OpenAI 키 없음 |
| 401 | `openai_api_authentication_failed` | 입력 키 인증 실패 |
| 422 | `ncs_required_ability_unit_mismatch` | 검토한 요구능력단위가 확정 세분류의 공식 능력단위와 정확히 일치하지 않음 |
| 429 | `openai_api_usage_limit_reached` | OpenAI 사용량 또는 요청 한도 |
| 502 | `openai_api_quality_rejected` | 재생성 뒤에도 독립 품질검수 실패 |
| 503 | `openai_api_unreachable` | 네트워크 연결 실패 |
| 503 | `ncs_mcp_unavailable` | 공식 세분류·능력단위 조회 장애(느슨한 매핑으로 강등하지 않음) |
| 504 | `openai_api_timeout` | 요청 예산 또는 공급자 시간 초과 |

실패 응답에는 질문 원문, API 키, 공급자 예외 문자열을 포함하지 않습니다. 화면은 업로드·세분류·면접형태 선택 상태를 유지하므로 키나 입력을 확인한 뒤 즉시 재시도할 수 있습니다.

## 검증

```powershell
python -m py_compile app/main.py app/services/ai_question_quality_review.py app/services/hwp_text_fallback.py
python -m json.tool vercel.json
pytest -q tests/test_ai_question_quality_review.py tests/test_auxiliary_ai_quality_review.py tests/test_openai_byok_contract.py
python scripts/verify_ncs_interviewer_guide_reference.py "C:\path\to\면접관+기본심화.pdf" --expected-metadata-json app/resources/ncs_interviewer_guide_2020.json
```

실모델 canary는 승인된 테스트 키와 개인정보가 없는 합성 입력으로만 수행하세요. 운영 지표는 품질 재생성률, 최종 거부율, p50/p95 지연시간, fallback 반환 0건입니다.
