# NCS_MCP 세분류 매칭 공백 점검 - 2026-07-23

## 목적

JOB-ALIO 벤치마크에서 직무기술서 세분류는 추출됐지만 NCS_MCP exact 세분류 매칭이 0건인 사례가 확인됐다. 이 리포트는 해당 현상이 NCScope 파싱 실패인지, MCP serving DB의 분류 coverage/alias 문제인지 분리하기 위한 read-only 점검 기록이다.

## 확인 결과

| 검토어 | strict 세분류 매칭 | raw 검색 | 해석 |
| --- | ---: | ---: | --- |
| 경영기획 | 5 | 5 | 정상. 세분류 exact match와 KSA 조회 가능 |
| 원자력발전설비운영 | 5 | 5 | 정상. 세분류 exact match와 KSA 조회 가능 |
| 원자력발전기계설비정비 | 5 | 5 | 정상. 세분류 exact match와 KSA 조회 가능 |
| 간호수행 | 0 | 0 | serving DB에서 분류/능력단위 검색 불가 |
| 간호행정관리 | 0 | 0 | serving DB에서 분류/능력단위 검색 불가 |
| 임상병리 | 0 | 3 | raw 검색은 관련어를 찾지만 세분류 exact match가 아님 |

SQLite read-only 확인에서도 `classifications` 테이블에 `간호수행`, `간호행정관리`, `임상병리`, `간호` 명칭을 포함한 분류행은 없었다. `보건·의료` 대분류 자체는 존재하므로, 특정 기관 직무기술서의 분류명이 현재 serving DB의 세분류 명칭과 다르거나 DB coverage 밖에 있는 상태로 판단한다.

## 제품 판단

NCScope는 공식 KSA 근거가 없는 상태에서 면접 질문을 자동 생성하면 안 된다. 따라서 exact 세분류 매칭이 없을 때는 다음 흐름으로 회수한다.

1. 업로드 생성 경로는 질문 생성을 중단한다.
2. API는 422와 함께 `lookup_terms`, `suggested_ncs_units`, `next_step`을 구조화해 반환한다.
3. UI는 직접입력 모드로 전환하고 후보 NCS 능력단위를 표시한다.
4. 사람이 공식 NCS 능력단위를 확인·선택한 경우에만 KSA 조회와 질문 생성을 진행한다.

## 구현 반영

- `app.services.ncs_mcp_client.suggest_units_by_text`: exact 세분류 매칭이 아닌 후보 검색용 함수 추가
- `/api/ncs/units/options`: strict 결과가 없으면 `ncs-mcp-suggest` 후보 반환
- `/api/jd/strategy/upload`: strict 결과가 없으면 후보를 포함한 구조화 422 반환
- UI: 구조화 422를 받으면 직접입력 모드로 전환하고 후보 NCS를 사람이 선택하도록 표시

## 검증

- `python -m py_compile app\main.py app\services\ncs_mcp_client.py` → passed
- `python -m pytest tests\test_mcp_only_policy.py -q` → 15 passed
- TestClient + 실제 NCS_MCP smoke:
  - `GET /api/ncs/units/options?q=경영기획&limit=10` → `source: ncs-mcp`, `count: 8`
  - `GET /api/ncs/units/options?q=임상병리&limit=10` → `source: ncs-mcp-suggest`, `count: 3`
