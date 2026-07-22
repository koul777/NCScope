# Third Party Notices

NCScope는 외부 오픈소스와 API를 조합해 동작합니다. 이 문서는 사용자가 특히 확인해야 할 제3자 구성요소를 정리합니다.

## Kordoc

- 프로젝트: [chrisryugj/kordoc](https://github.com/chrisryugj/kordoc)
- 라이선스: [MIT License](https://github.com/chrisryugj/kordoc/blob/main/LICENSE)
- NCScope에서의 역할: 직무기술서와 공고문 파일의 본문, 표, 메타데이터를 파싱해 사람 검토용 후보를 만듭니다.
- 연동 위치: `package.json`의 npm dependency와 `scripts/kordoc_parse.mjs`
- 검토 원칙: Kordoc 결과는 NCS 세분류나 면접 질문의 최종 확정값이 아닙니다. NCScope는 사람이 세분류를 검토·확정한 뒤에만 로컬 NCS DB 조회와 질문 생성을 진행합니다.

NCScope 저장소는 Kordoc 소스 저장소가 아니며, Kordoc의 권리와 라이선스는 해당 프로젝트의 고지를 따릅니다.

## OpenAI API

NCScope는 사용자가 제공한 OpenAI API key 또는 서버 환경변수 `OPENAI_API_KEY`를 사용해 구조화 면접 질문 초안을 생성할 수 있습니다. API key와 업로드 문서 처리 기준은 [`SECURITY.md`](SECURITY.md)를 확인하세요.

## 기타 의존성

Python과 Node.js 의존성은 각각 [`requirements.txt`](requirements.txt), [`package.json`](package.json), [`package-lock.json`](package-lock.json)에 명시되어 있습니다. 운영 배포 전 각 기관의 오픈소스 사용 기준과 보안 기준에 따라 의존성 검토를 수행해야 합니다.
