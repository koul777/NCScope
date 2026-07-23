# ALIO JD Benchmark - 20260724_034457

Source: https://job.alio.go.kr/recruit.do

- Samples attempted: 28
- Parsed documents: 25
- Documents with detail candidates: 19
- Documents with detail candidates but no MCP match: 2
- Documents with detail candidates skipped because MCP URL is not configured: 0
- Documents with MCP connection errors: 0
- Notice pages with duty text candidates: 28
- Notice pages with evaluation text candidates: 25
- Detail-no-match documents with manual NCS suggestions: 2
- Total detail candidates: 65
- Documents with unit-name detail recovery: 1
- Unit-name recovered detail labels: 2
- Documents with partial detail MCP matches: 3
- Unmatched detail candidates: 14
- Detail candidates with diagnostics skipped because MCP URL is not configured: 0
- Detail match diagnostic counts: exact_detail=49; specialized_healthcare_label_unserved_by_mcp=14; unit_name_only=2
- Parsed-no-detail category counts: declared_no_ncs_mapping=2; no_explicit_ncs_detail=4
- Parsed-no-detail reason counts: job_document_without_explicit_ncs_detail=2; multi_role_healthcare_document_without_explicit_ncs_detail=1; no_ncs_mapping_declared=2; translation_role_without_explicit_ncs_detail=1
- Parsed-no-detail state counts: [declared_no_mapping + saw_detail_header + blank_or_dash_detail_cell + filtered_candidate_reason=value_too_long + saw_ncs_table + filtered_candidate_reason=filtered_candidate_not_detail_like]=1; [declared_no_mapping + saw_detail_header + filtered_candidate_reason=undeveloped_ncs_value + saw_ncs_table]=1; [job_document_markers_without_ncs_classification]=2; [multi_role_healthcare_markers_without_ncs_detail]=1; [translation_role_markers_without_ncs_detail]=1
- Average parse time: 655 ms
- MCP URL configured: True

| idx | status | attachment | parse_ms | archive files | detail candidates | no-detail category | no-detail reason | no-detail state | MCP configured | diagnostics skip reason | exact details | unit-name details | unmatched details | notice duty chars | notice eval chars | MCP units | MCP KSA | suggestions |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 302978 | no_jd_attachment |  | 0 | 0 |  |  |  |  | True |  | 0 | 0 | 0 | 45 | 614 | 0 | 0 | 0 |
| 302964 | parsed_no_detail | 직무소개서.pdf | 440 | 0 |  | no_explicit_ncs_detail | job_document_without_explicit_ncs_detail | job_document_markers_without_ncs_classification | True |  | 0 | 0 | 0 | 811 | 221 | 0 | 0 | 0 |
| 303006 | parsed_no_detail | 직무기술서(사무보조)_총무부.hwp | 198 | 0 |  | no_explicit_ncs_detail | job_document_without_explicit_ncs_detail | job_document_markers_without_ncs_classification | True |  | 0 | 0 | 0 | 121 | 0 | 0 | 0 | 0 |
| 302720 | parsed_no_detail | 직무기술서(초빙연구원-가 급).hwp | 196 | 0 |  | declared_no_ncs_mapping | no_ncs_mapping_declared | declared_no_mapping; saw_detail_header; filtered_candidate_reason=undeveloped_ncs_value; saw_ncs_table | True |  | 0 | 0 | 0 | 125 | 94 | 0 | 0 | 0 |
| 303000 | parsed_no_detail | 붙임3. 직무기술서(인증 및 환자안전).pdf | 487 | 0 |  | declared_no_ncs_mapping | no_ncs_mapping_declared | declared_no_mapping; saw_detail_header; blank_or_dash_detail_cell; filtered_candidate_reason=value_too_long; saw_ncs_table; filtered_candidate_reason=filtered_candidate_not_detail_like | True |  | 0 | 0 | 0 | 186 | 187 | 0 | 0 | 0 |
| 303012 | ok | NCS 기반 채용 직무 설명자료.hwp | 203 | 0 | 유원시설운영관리; 객실관리 |  |  |  | True |  | 2 | 0 | 0 | 50 | 91 | 22 | 6 | 0 |
| 303039 | ok_unit_name_resolved | 직무기술서.zip | 5444 | 12 | 스포츠시설운영관리; 보안; 구조물해체; 카지노 고객 지원; 식음료접객; 한식조리; 양식조리; 중식조리; 일식조리; 복어조리; 제과; 제빵; 카지노 영업 지원; 카지노운영관리; 사무행정 |  |  |  | True |  | 13 | 2 | 0 | 103 | 1909 | 184 | 6 | 2 |
| 303011 | no_jd_attachment |  | 0 | 0 |  |  |  |  | True |  | 0 | 0 | 0 | 157 | 420 | 0 | 0 | 0 |
| 303010 | ok | (직무기술서)보령권지사 장항물재생센터 단기계약근로자(하수도 시설운영 지원) 채용(휴직 대체인력).pdf | 442 | 0 | 하수처리시설운영관리 |  |  |  | True |  | 1 | 0 | 0 | 199 | 265 | 10 | 6 | 0 |
| 303004 | ok | 직무기술서(정비보조).hwp | 196 | 0 | 화력발전설비운영 |  |  |  | True |  | 1 | 0 | 0 | 115 | 0 | 15 | 6 | 0 |
| 303003 | ok | 2. 직무기술서.pdf | 478 | 0 | 총무; 사무행정 |  |  |  | True |  | 2 | 0 | 0 | 109 | 106 | 18 | 6 | 0 |
| 303002 | ok | 2. 직무기술서.pdf | 485 | 0 | 총무; 사무행정 |  |  |  | True |  | 2 | 0 | 0 | 91 | 176 | 18 | 6 | 0 |
| 303001 | ok | 2026년 안전본부 단기계약 근로자(대체인력) 채용 직무기술서.pdf | 440 | 0 | 보건교육; 산업보건관리 |  |  |  | True |  | 2 | 0 | 0 | 161 | 251 | 20 | 6 | 0 |
| 302998 | ok | 별첨 7. 직종별 직무기술서.pdf | 498 | 0 | 프로젝트관리; 정보기술전략; 정보기술기획; IT프로젝트관리; 총무; 환경미화 |  |  |  | True |  | 6 | 0 | 0 | 808 | 939 | 63 | 6 | 0 |
| 302997 | partial_detail_mcp_match | 직무기술서(2026년 제2차 단기 결원 대체 인력풀).zip | 567 | 3 | 간호업무 보조; 간호행정 보조; 재원환자 관리; 응급 환자 관리; 요양지원; 환경미화 |  |  |  | True |  | 2 | 0 | 4 | 151 | 432 | 18 | 6 | 0 |
| 302996 | partial_detail_mcp_match | 직무기술서.zip | 761 | 4 | 건축설계; 유지관리; 건설안전관리; 건축감리; 건축설비유지관리; 간호업무 보조; 간호행정 보조; 병원행정 |  |  |  | True |  | 6 | 0 | 2 | 103 | 431 | 75 | 6 | 0 |
| 302994 | ok | 첨부3_2026년도 제4차 신입사원(고졸수준 일반전형) 직무설명자료.pdf | 499 | 0 | 원자력발전설비운영; 원자력발전기계설비정비 |  |  |  | True |  | 2 | 0 | 0 | 122 | 0 | 29 | 6 | 0 |
| 302956 | no_jd_attachment |  | 0 | 0 |  |  |  |  | True |  | 0 | 0 | 0 | 45 | 362 | 0 | 0 | 0 |
| 302959 | ok | 직무기술서_부산울산경남지역협력단 특수직.pdf | 437 | 0 | 상수관로시설운영관리 |  |  |  | True |  | 1 | 0 | 0 | 133 | 257 | 14 | 6 | 0 |
| 302960 | detail_no_mcp_match | [붙임7] 직무기술서(간호직).pdf | 483 | 0 | 간호수행; 간호행정관리 |  |  |  | True |  | 0 | 0 | 2 | 469 | 903 | 0 | 0 | 0 |
| 302984 | detail_no_mcp_match | 직무설명서.zip | 1335 | 3 | 영상의학; 임상병리; 간호업무 보조; 간호행정 보조 |  |  |  | True |  | 0 | 0 | 4 | 53 | 289 | 0 | 0 | 1 |
| 302977 | parsed_no_detail | 직무명세서.pdf | 442 | 0 |  | no_explicit_ncs_detail | translation_role_without_explicit_ncs_detail | translation_role_markers_without_ncs_detail | True |  | 0 | 0 | 0 | 53 | 222 | 0 | 0 | 0 |
| 303013 | ok | 260723 [안산병원] 청년(체험형)인턴_장애전형 직무기술서.hwp | 188 | 0 | 병원행정 |  |  |  | True |  | 1 | 0 | 0 | 159 | 171 | 17 | 6 | 0 |
| 303014 | ok | 2. NCS기반 직무설명자료(식당파트타임).pdf | 441 | 0 | 한식조리; 양식조리; 중식조리; 일식조리 |  |  |  | True |  | 4 | 0 | 0 | 71 | 1780 | 67 | 6 | 0 |
| 303016 | ok | 직무기술서.pdf | 495 | 0 | 환경미화 |  |  |  | True |  | 1 | 0 | 0 | 492 | 401 | 8 | 6 | 0 |
| 303036 | parsed_no_detail | 강원대학교병원 채용 직무 설명자료.pdf | 854 | 0 |  | no_explicit_ncs_detail | multi_role_healthcare_document_without_explicit_ncs_detail | multi_role_healthcare_markers_without_ncs_detail | True |  | 0 | 0 | 0 | 102 | 1020 | 0 | 0 | 0 |
| 303034 | ok | 직무기술서(사회복지사).hwp | 197 | 0 | 사회복지 사례관리 |  |  |  | True |  | 1 | 0 | 0 | 117 | 66 | 1 | 3 | 0 |
| 303033 | partial_detail_mcp_match | 직무기술서(간호사).hwp | 193 | 0 | 간호수행; 간호행정관리; 총무; 사무행정 |  |  |  | True |  | 2 | 0 | 2 | 113 | 63 | 18 | 6 | 0 |

CSV: `reports\alio_jd_benchmark_20260724_034457.csv`
Detail diagnostics CSV: `reports\alio_jd_detail_diagnostics_20260724_034457.csv`
