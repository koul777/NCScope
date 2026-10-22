# NCScope 에이전트 팀 작업 규칙

Codex, Cursor Agent, Claude Code가 성능 개선 라운드를 나눠 맡을 때 따르는 규칙임.
KPI 정의, 기준선, 판정 방법은 `docs/PERFORMANCE_KPI_GUARDRAILS.md`에 있음.

## 역할

| 역할 | 담당 | 쓰기 범위 |
|---|---|---|
| 리드·판정 (Claude Code) | 라운드 과제 배정, KPI 측정, 가드레일 판정, 통합 브랜치 병합 | 통합 브랜치 `perf/kpi-guardrails-*` |
| 구현 A (Codex) | 배정받은 과제 구현과 회귀 테스트 | 자기 worktree와 작업 브랜치 |
| 구현 B (Cursor Agent) | 배정받은 과제 구현과 회귀 테스트 | 자기 worktree와 작업 브랜치 |
| 교차 검토 | 상대 구현자 변경을 읽기 전용으로 검토 | 없음 |

구현자는 병합 여부를 스스로 정하지 않음. 리드가 `scripts/perf_kpi.py`의 측정 결과와
diff 검사 결과로 판정함.

## 구현자 규칙

1. 배정받은 worktree 안의 파일만 수정함. main 체크아웃, 다른 worktree,
   `C:/workspace/NCS_MCP`는 읽기만 함.
2. 과제 하나에 가설 하나. 운영 코드 변경은 800줄 이내.
3. 고치기 전에 실패를 재현하는 회귀 테스트를 먼저 추가함. 기존 테스트의 삭제,
   이름 변경, skip·xfail 추가, 단언 완화는 금지.
4. 동결 파일은 수정하지 않음. 목록은 `scripts/perf_kpi.py`의 `PROTECTED_PATTERNS`
   (평가 기준표·홀드아웃·블라인드 픽스처, 측정 스크립트, 팀 규칙 문서, CI·배포 설정,
   의존성 파일).
5. 홀드아웃 문서로 규칙을 맞추지 않음. 문서별 분석은 리드가 넘겨준
   `stored_jd_dev_rows.csv`(홀드아웃 36건 제외)만 사용함.
   `tests/fixtures/stored_jd_final_blind_*`, `tmp/stored_jd_final_blind/`,
   `guardian_only/` 아래 내용을 열어 오류 사례를 찾지 않음.
6. 제품 불변 조건 유지
   - 세분류·능력단위·KSA는 NCS MCP 공식 결과만 사용함. 느슨한 매핑, 로컬 샘플,
     `NCS_DB.xlsx`로 강등하지 않음.
   - 공식명과 정확히 일치하지 않는 라벨은 진단으로 표시하고 비슷한 이름으로 자동
     확정하지 않음.
   - 블라인드 채용 안전 규칙과 공식 KSA 근거(`evidence_id`) 보존.
7. 외부 유료 API(OpenAI, OpenRouter, Anthropic)를 호출하지 않음. 네트워크는 로컬
   NCS MCP(`http://127.0.0.1:8778/mcp`)만 사용함.
8. `git push`, `git reset --hard`, `git clean`, 브랜치 삭제, main 체크아웃 변경은
   금지. 자기 작업 브랜치에 커밋만 함.
9. `.env` 값, API 키, 토큰을 출력하거나 파일에 쓰지 않음.
10. Windows 콘솔 창을 새로 띄우지 않음. `start`, `cmd /k`, 창을 여는
    `Start-Process`, `.cmd`·`.bat` 직접 실행 금지. 명령은 현재 셸에서 비대화식으로
    실행함.

## 완료 확인과 보고

작업을 마치면 아래 두 명령을 실행하고 결과를 보고함.

```
python -m pytest -q -p no:cacheprovider <관련 테스트 파일>
python scripts/perf_kpi.py diff-policy --base <배정 때 받은 기준 커밋>
```

최종 메시지에 넣을 항목

- 바꾼 파일과 이유
- 목표 KPI와 예상 변화
- 추가한 회귀 테스트 이름
- dev 세트에서 판정이 바뀌는 문서 예시(파일명, 바뀌기 전, 바뀐 뒤)
- 남은 위험과 확인하지 못한 부분
