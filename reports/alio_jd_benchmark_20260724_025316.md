# ALIO JD Benchmark - 20260724_025316

Source: https://job.alio.go.kr/recruit.do

- Samples attempted: 8
- Parsed documents: 6
- Documents with detail candidates: 2
- Documents with detail candidates but no MCP match: 0
- Documents with MCP connection errors: 0
- Notice pages with duty text candidates: 8
- Notice pages with evaluation text candidates: 7
- Detail-no-match documents with manual NCS suggestions: 1
- Total detail candidates: 17
- Documents with unit-name detail recovery: 1
- Unit-name recovered detail labels: 2
- Documents with partial detail MCP matches: 0
- Unmatched detail candidates: 0
- Detail match diagnostic counts: exact_detail=15; unit_name_only=2
- Parsed-no-detail category counts: declared_no_ncs_mapping=2; no_explicit_ncs_detail=2
- Parsed-no-detail reason counts: job_document_without_explicit_ncs_detail=2; no_ncs_mapping_declared=2
- Parsed-no-detail state counts: [declared_no_mapping + saw_detail_header + blank_or_dash_detail_cell + filtered_candidate_reason=value_too_long + saw_ncs_table + filtered_candidate_reason=filtered_candidate_not_detail_like]=1; [declared_no_mapping + saw_detail_header + filtered_candidate_reason=undeveloped_ncs_value + saw_ncs_table]=1; [job_document_markers_without_ncs_classification]=2
- Average parse time: 1141 ms
- MCP URL configured: True

| idx | status | attachment | parse_ms | archive files | detail candidates | no-detail category | no-detail reason | no-detail state | exact details | unit-name details | unmatched details | notice duty chars | notice eval chars | MCP units | MCP KSA | suggestions |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 302978 | no_jd_attachment |  | 0 | 0 |  |  |  |  | 0 | 0 | 0 | 45 | 614 | 0 | 0 | 0 |
| 302964 | parsed_no_detail | 직무소개서.pdf | 445 | 0 |  | no_explicit_ncs_detail | job_document_without_explicit_ncs_detail | job_document_markers_without_ncs_classification | 0 | 0 | 0 | 811 | 221 | 0 | 0 | 0 |
| 303006 | parsed_no_detail | 직무기술서(사무보조)_총무부.hwp | 197 | 0 |  | no_explicit_ncs_detail | job_document_without_explicit_ncs_detail | job_document_markers_without_ncs_classification | 0 | 0 | 0 | 121 | 0 | 0 | 0 | 0 |
| 302720 | parsed_no_detail | 직무기술서(초빙연구원-가 급).hwp | 197 | 0 |  | declared_no_ncs_mapping | no_ncs_mapping_declared | declared_no_mapping; saw_detail_header; filtered_candidate_reason=undeveloped_ncs_value; saw_ncs_table | 0 | 0 | 0 | 125 | 94 | 0 | 0 | 0 |
| 303000 | parsed_no_detail | 붙임3. 직무기술서(인증 및 환자안전).pdf | 465 | 0 |  | declared_no_ncs_mapping | no_ncs_mapping_declared | declared_no_mapping; saw_detail_header; blank_or_dash_detail_cell; filtered_candidate_reason=value_too_long; saw_ncs_table; filtered_candidate_reason=filtered_candidate_not_detail_like | 0 | 0 | 0 | 186 | 187 | 0 | 0 | 0 |
| 303012 | ok | NCS 기반 채용 직무 설명자료.hwp | 206 | 0 | 유원시설운영관리; 객실관리 |  |  |  | 2 | 0 | 0 | 50 | 91 | 22 | 6 | 0 |
| 303039 | ok_unit_name_resolved | 직무기술서.zip | 5338 | 12 | 스포츠시설운영관리; 보안; 구조물해체; 카지노 고객 지원; 식음료접객; 한식조리; 양식조리; 중식조리; 일식조리; 복어조리; 제과; 제빵; 카지노 영업 지원; 카지노운영관리; 사무행정 |  |  |  | 13 | 2 | 0 | 103 | 1909 | 184 | 6 | 2 |
| 303011 | no_jd_attachment |  | 0 | 0 |  |  |  |  | 0 | 0 | 0 | 157 | 420 | 0 | 0 | 0 |

CSV: `reports\alio_jd_benchmark_20260724_025316.csv`
Detail diagnostics CSV: `reports\alio_jd_detail_diagnostics_20260724_025316.csv`
