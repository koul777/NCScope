from __future__ import annotations

import pytest

from app.services import kordoc_parser
from app.services.kordoc_parser import (
    KordocStructureLimitError,
    _looks_like_detail_candidate,
    _loads_kordoc_json,
    _split_ability_unit_entries,
    structure_job_description,
    structure_job_notice,
)


@pytest.mark.parametrize("span", ["257", "9" * 5000])
def test_html_table_span_above_safe_limit_is_rejected(span: str) -> None:
    markdown = f"<table><tr><td colspan=\"{span}\">detail</td></tr></table>"

    with pytest.raises(KordocStructureLimitError, match="span exceeds"):
        structure_job_description({"markdown": markdown}, filename="oversized-table.txt")


def test_html_table_logical_cell_budget_is_rejected() -> None:
    raw_table = "".join(
        "<tr><td colspan=\"256\">detail</td></tr>" for _ in range(40)
    )

    with pytest.raises(KordocStructureLimitError, match="size exceeds"):
        kordoc_parser._html_table_position_grid(raw_table)


def test_html_table_document_aggregate_budget_is_rejected() -> None:
    table = "<table>" + "".join(
        "<tr><td colspan=\"256\">detail</td></tr>" for _ in range(20)
    ) + "</table>"

    with pytest.raises(KordocStructureLimitError, match="document table size exceeds"):
        structure_job_description(
            {"markdown": table + table},
            filename="many-expanded-tables.txt",
        )


def test_detail_candidate_evidence_normalizes_each_source_surface_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_norm = kordoc_parser._norm
    norm_calls = 0

    def counted_norm(value) -> str:
        nonlocal norm_calls
        norm_calls += 1
        return original_norm(value)

    monkeypatch.setattr(kordoc_parser, "_norm", counted_norm)
    details = [f"detail-{index:03}" for index in range(400)]
    sections = {
        "ncs_detail": [
            {"text": detail, "source": "test", "page": 1, "line": index + 1}
            for index, detail in enumerate(details)
        ]
    }
    markdown = "\n".join(f"| detail | {detail} |" for detail in details)

    evidence = kordoc_parser._detail_candidate_evidence(
        details,
        sections,
        markdown,
        "explicit",
    )

    assert len(evidence) == len(details)
    assert norm_calls < 10_000


def _evidence_by_detail(result: dict) -> dict[str, dict]:
    return {
        row["detail"]: row
        for row in result["fields"].get("ncs_detail_candidate_evidence", [])
    }


def _assert_contextual_evidence_uses_source_snippet(result: dict) -> None:
    candidates = result["fields"]["ncs_detail_candidates"]
    evidence_rows = result["fields"]["ncs_detail_candidate_evidence"]

    assert len(evidence_rows) == len(candidates)
    for candidate, evidence in zip(candidates, evidence_rows):
        assert evidence["detail"] == candidate
        assert evidence["source"] == "contextual"
        assert evidence["snippet"]
        assert evidence["snippet"] != candidate


def test_structure_job_description_extracts_detail_from_html_table() -> None:
    markdown = """
<table>
<tr><td rowspan="5">분류체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>사업관리</td><td>사업관리</td><td>프로젝트관리</td><td>프로젝트관리</td></tr>
<tr><td rowspan="2">정보통신</td><td rowspan="2">정보기술</td><td>정보기술전략·계획</td><td>정보기술전략</td></tr>
<tr><td>정보기술기획</td></tr>
<tr><td>기관주요업무</td><td colspan="4">ICT R&D 기술기획</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "프로젝트관리",
        "정보기술전략",
        "정보기술기획",
    ]


def test_self_developed_detail_is_preserved_but_explicitly_classified() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>(자체개발) 공동주택 시설관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="custom.hwp")

    assert result["fields"]["ncs_detail_candidates"] == [
        "(자체개발) 공동주택 시설관리"
    ]
    assert result["fields"]["ncs_self_developed_detail_candidates"] == [
        "(자체개발) 공동주택 시설관리"
    ]
    assert result["fields"]["ncs_detail_candidate_evidence"][0][
        "mapping_state"
    ] == "source_declared_self_developed"


def test_structure_job_description_preserves_exact_kordoc_table_coordinates_and_scope() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "pageNumber": 3,
                "bbox": [10, 20, 500, 700],
                "rows": [
                    [
                        {"text": "채용분야"},
                        {"text": "일반행정", "colSpan": 3},
                    ],
                    [
                        {"text": "NCS 세분류명"},
                        {"text": "사무행정", "colSpan": 3},
                    ],
                    [
                        {"text": "필요지식"},
                        {"text": "문서관리 규정과 자료 분류 원칙", "colSpan": 3},
                    ],
                    [
                        {"text": "필요기술"},
                        {"text": "문서작성 및 자료검색 기술", "colSpan": 3},
                    ],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.hwpx")
    items = result["fields"]["positioned_items"]
    knowledge = next(item for item in items if item["section"] == "knowledge")

    assert knowledge["page"] == 3
    assert knowledge["table_index"] == 0
    assert knowledge["label_cell"] == {
        "row": 2,
        "column": 0,
        "row_span": 1,
        "column_span": 1,
    }
    assert knowledge["value_cell"] == {
        "row": 2,
        "column": 1,
        "row_span": 1,
        "column_span": 3,
    }
    assert knowledge["scope"] == {
        "job_fields": ["일반행정"],
        "ncs_details": ["사무행정"],
        "status": "single_detail",
        "review_required": False,
    }
    assert knowledge["header_path"] == ["일반행정", "사무행정", "필요지식"]
    assert result["fields"]["table_coordinate_contract"]["index_base"] == 0


def test_positioned_ncs_hierarchy_detail_header_promotes_exact_official_value() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "NCS 분류체계", "rowSpan": 2},
                        {"text": "대분류"},
                        {"text": "중분류"},
                        {"text": "소분류"},
                        {"text": "세분류"},
                    ],
                    [
                        {"text": ""},
                        {"text": "02.경영·회계·사무"},
                        {"text": "02.총무·인사"},
                        {"text": "03.일반사무"},
                        {"text": "02.사무행정"},
                    ],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="positioned-detail.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
    detail_item = next(
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ncs_detail"
    )
    assert detail_item["source"] == "kordoc_table"
    assert detail_item["layout"] == "column_header_value"


def test_positioned_ncs_hierarchy_detail_header_keeps_colspan_value_coordinates() -> None:
    markdown = """
<table>
<tr><th rowspan="2">NCS 분류체계</th><th>대분류</th><th>중분류</th><th>소분류</th><th colspan="2">세분류</th></tr>
<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>03.일반사무</td><td colspan="2">02.사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="colspan-detail.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
    detail_item = next(
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ncs_detail"
    )
    assert detail_item["value_cell"] == {
        "row": 1,
        "column": 4,
        "row_span": 1,
        "column_span": 2,
    }


@pytest.mark.parametrize(
    "data_row",
    [
        "<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>03.일반사무</td><td>-</td></tr>",
        "<tr><td>현재 NCS에 Mapping 가능한 직무(세분류)가 없어 별도 분석</td><td></td><td></td><td>사무행정</td></tr>",
        "<tr><th>능력단위</th><td colspan=\"3\">01.문서작성</td><td>사무행정</td></tr>",
    ],
)
def test_positioned_ncs_hierarchy_detail_header_rejects_non_detail_values(
    data_row: str,
) -> None:
    markdown = f"""
<table>
<tr><th>NCS 분류체계</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
{data_row}
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="rejected-detail.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_scopes_horizontal_matrix_cells_by_row() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th>NCS 세분류명</th><th>요구능력단위</th><th>필요지식</th><th>필요기술</th></tr>
<tr><td>행정</td><td>사무행정</td><td>문서 작성</td><td>문서관리 규정</td><td>자료검색 기술</td></tr>
<tr><td>회계</td><td>세무</td><td>적격증빙관리</td><td>세법과 회계기준</td><td>세무신고 기술</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="matrix.pdf")
    items = result["fields"]["positioned_items"]
    knowledge = [item for item in items if item["section"] == "knowledge"]

    assert [item["text"] for item in knowledge] == ["문서관리 규정", "세법과 회계기준"]
    assert knowledge[0]["value_cell"]["row"] == 1
    assert knowledge[0]["value_cell"]["column"] == 3
    assert knowledge[0]["scope"]["job_fields"] == ["행정"]
    assert knowledge[0]["scope"]["ncs_details"] == ["사무행정"]
    assert knowledge[1]["scope"]["job_fields"] == ["회계"]
    assert knowledge[1]["scope"]["ncs_details"] == ["세무"]
    assert result["fields"]["ability_units"] == ["문서 작성", "적격증빙관리"]
    assert result["fields"]["ability_units_by_detail"] == {
        "사무행정": ["문서 작성"],
        "세무": ["적격증빙관리"],
    }


def test_structure_job_description_splits_grouped_ability_units_and_keeps_detail_scope() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><td>경영지원</td></tr>
<tr><th>NCS 세분류명</th><td>사무행정, 총무</td></tr>
<tr><th>요구능력단위</th><td>(사무행정) 01.문서 작성, 02.문서 관리, 03.자료 관리; (총무) 04.비품관리, 07.업무지원</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="grouped.hwp")

    assert result["fields"]["ability_units"] == [
        "문서 작성",
        "문서 관리",
        "자료 관리",
        "비품관리",
        "업무지원",
    ]
    assert result["fields"]["ability_units_by_detail"] == {
        "사무행정": ["문서 작성", "문서 관리", "자료 관리"],
        "총무": ["비품관리", "업무지원"],
    }
    positioned_units = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert [item["ability_unit_ordinal"] for item in positioned_units] == [
        "01",
        "02",
        "03",
        "04",
        "07",
    ]


def test_numbered_ability_units_join_layout_wraps_inside_official_names() -> None:
    rows = _split_ability_unit_entries(
        "(한식조리) 07.음청\n류 조리 08.한식 재료\n관리 09.한식 안전관리"
    )

    assert [row["text"] for row in rows] == [
        "음청 류 조리",
        "한식 재료 관리",
        "한식 안전관리",
    ]
    assert [row["ordinal"] for row in rows] == ["07", "08", "09"]
    assert all(row["detail_hint"] == "한식조리" for row in rows)


def test_structure_job_description_keeps_comma_inside_numbered_ability_unit_name() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>임상병리</td></tr>
<tr><th>요구능력단위</th><td>(임상병리) 01.검사준비 02.채혈,접수 업무 03.일반혈액 검사</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="clinical.hwp")

    assert result["fields"]["ability_units"] == ["검사준비", "채혈,접수 업무", "일반혈액 검사"]
    assert result["fields"]["ability_units_by_detail"] == {
        "임상병리": ["검사준비", "채혈,접수 업무", "일반혈액 검사"]
    }


def test_numbered_official_unit_splits_trailing_defined_custom_unit() -> None:
    rows = _split_ability_unit_entries(
        "(사무행정(기록물)) 08.사무환경조성, "
        "기록물관리(생산·분류·편철·이관·보존·폐기)"
    )

    assert [row["text"] for row in rows] == [
        "사무환경조성",
        "기록물관리(생산·분류·편철·이관·보존·폐기)",
    ]
    assert [row["ordinal"] for row in rows] == ["08", ""]
    assert all(row["detail_hint"] == "사무행정(기록물)" for row in rows)


def test_numbered_official_comma_name_is_not_split() -> None:
    rows = _split_ability_unit_entries("(위험물관리) 01.제1류, 제6류 위험물 취급")

    assert [row["text"] for row in rows] == ["제1류, 제6류 위험물 취급"]


def test_exact_base_detail_hint_scopes_qualified_institution_label() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>사무행정</td></tr>
<tr><th>능력단위</th><td>(사무행정(기록물)) 02.문서관리, 03.자료관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="qualified.hwp")

    assert result["fields"]["ability_units_by_detail"] == {
        "사무행정": ["문서관리", "자료관리"]
    }
    units = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert all(
        item["scope"]["source"] == "embedded_exact_base_detail_hint"
        for item in units
    )


def test_generic_bullet_separator_does_not_leave_concatenated_duplicate() -> None:
    rows = _split_ability_unit_entries(
        "종합적으로 법률을 해석하고 법적 쟁점을 분석하는 능력 "
        "○ 법적 문제의 합리적인 문제해결 및 대안 제시 능력"
    )

    assert [row["text"] for row in rows] == [
        "종합적으로 법률을 해석하고 법적 쟁점을 분석하는 능력 "
        "○ 법적 문제의 합리적인 문제해결 및 대안 제시 능력",
    ]


def test_native_ability_rows_outrank_joined_html_fallback_evidence() -> None:
    first = "종합적으로 법률을 해석하고 법적 쟁점을 분석하는 능력"
    second = "법적 문제의 합리적인 문제해결 및 대안 제시 능력"
    parsed = {
        "markdown": f"""
<table>
<tr><th>세분류</th><td>법무</td></tr>
<tr><th>능력단위</th><td>○ {first} ○ {second}</td></tr>
</table>
""",
        "blocks": [
            {
                "type": "table",
                "pageNumber": 1,
                "rows": [
                    [{"text": "세분류"}, {"text": "법무"}],
                    [{"text": "능력단위"}, {"text": f"○ {first}\n○ {second}"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="native-plus-html.hwpx")

    assert result["fields"]["ability_units"] == [first, second]
    section_rows = result["sections"]["ability_units"]
    assert all(row["table_index"] == 0 for row in section_rows)
    assert all("\n" in row["raw_cell_text"] for row in section_rows)


def test_job_field_row_uses_only_immediate_value_before_ncs_hierarchy() -> None:
    parsed = {
        "markdown": "| 세분류 | 사무행정 |",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "채용분야", "rowSpan": 2},
                        {"text": "통계품질(진단기획)", "rowSpan": 2},
                        {"text": "NCS 분류체계", "rowSpan": 2},
                        {"text": "대분류"},
                        {"text": "중분류"},
                        {"text": "소분류"},
                        {"text": "세분류"},
                    ],
                    [
                        {"text": ""},
                        {"text": ""},
                        {"text": ""},
                        {"text": "02.경영·회계·사무"},
                        {"text": "02.총무·인사"},
                        {"text": "03.일반사무"},
                        {"text": "02.사무행정"},
                    ],
                    [{"text": "능력단위"}, {"text": "01.문서작성", "colSpan": 6}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="job-field-scope.hwp")
    unit = next(
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    )

    assert unit["scope"]["job_fields"] == ["통계품질(진단기획)"]
    assert unit["header_path"][0] == "통계품질(진단기획)"


def test_job_unit_hierarchy_recovers_only_exact_official_leaf_detail() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "직무단위"},
                        {"text": "보건의료 – 의료 – 임상의학 - 양의학치료"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="medical-job-unit.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["양의학치료"]


def test_job_unit_hierarchy_rejects_non_ncs_and_incomplete_paths() -> None:
    for value in (
        "본부 – 팀 – 담당 – 사무행정",
        "보건의료 – 의료 – 양의학치료",
    ):
        parsed = {
            "markdown": "",
            "blocks": [
                {
                    "type": "table",
                    "rows": [[{"text": "직무단위"}, {"text": value}]],
                }
            ],
        }

        result = structure_job_description(parsed, filename="non-ncs-path.hwp")

        assert result["fields"]["ncs_detail_candidates"] == []


def test_versioned_training_row_recovers_unit_by_exact_base_code_and_name() -> None:
    parsed = {
        "markdown": "| 세분류 | 경영기획 |",
        "blocks": [
            {
                "type": "table",
                "pageNumber": 1,
                "rows": [
                    [
                        {"text": "직무교육과정"},
                        {"text": "020101010122v2"},
                        {"text": ""},
                        {"text": "01.사업환경 분석"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="training-row.pdf")

    assert result["fields"]["ability_units_by_detail"] == {
        "경영기획": ["사업환경 분석"]
    }
    unit = next(
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    )
    assert unit["source_unit_code"] == "020101010122v2"
    assert unit["resolved_unit_code"] == "0201010101_22v3"
    assert unit["source"] == "kordoc_code_anchored_training_recovery"
    assert unit["coordinate_source"] == "kordoc_table"
    assert unit["value_cell"] == {
        "row": 0,
        "column": 3,
        "row_span": 1,
        "column_span": 1,
    }
    assert next(
        cell
        for cell in unit["row_context_cells"]
        if cell["column"] == unit["value_cell"]["column"]
    )["text"] == "01.사업환경 분석"


def test_markdown_training_rows_keep_matched_cells_and_separate_table_indexes() -> None:
    parsed = {
        "markdown": """
| 세분류 | 경영기획 |
<table><tr><td>직무교육과정</td><td>020101010122v2</td><td></td><td>01.사업환경 분석</td></tr></table>
<table><tr><td>직무교육과정</td><td>020101010222v2</td><td>02.경영방침 수립</td></tr></table>
""",
        "blocks": [],
    }

    result = structure_job_description(parsed, filename="training-row-markdown.pdf")
    units = [
        item
        for item in result["fields"]["positioned_items"]
        if item.get("layout") == "markdown_code_anchored_training_row"
    ]

    assert [item["text"] for item in units] == ["사업환경 분석", "경영방침 수립"]
    assert [item["table_index"] for item in units] == [0, 1]
    assert [item["value_cell"]["column"] for item in units] == [3, 2]
    assert [
        next(
            cell["text"]
            for cell in item["row_context_cells"]
            if cell["column"] == item["value_cell"]["column"]
        )
        for item in units
    ] == ["01.사업환경 분석", "02.경영방침 수립"]


def test_code_anchored_recovery_fails_closed_outside_exact_training_scope() -> None:
    base_block = {
        "type": "table",
        "rows": [
            [
                {"text": "참고코드"},
                {"text": "020101010122v2"},
                {"text": "01.사업환경 분석"},
            ]
        ],
    }
    result = structure_job_description(
        {"markdown": "| 세분류 | 경영기획 |", "blocks": [base_block]},
        filename="not-training.pdf",
    )
    assert result["fields"]["ability_units"] == []

    wrong_detail_block = {
        "type": "table",
        "rows": [
            [
                {"text": "직무교육과정"},
                {"text": "020101010122v2"},
                {"text": "01.사업환경 분석"},
            ]
        ],
    }
    result = structure_job_description(
        {"markdown": "| 세분류 | 사무행정 |", "blocks": [wrong_detail_block]},
        filename="wrong-detail.pdf",
    )
    assert result["fields"]["ability_units"] == []

    wrong_name_block = {
        "type": "table",
        "rows": [
            [
                {"text": "직무교육과정"},
                {"text": "020101010122v2"},
                {"text": "01.사업환경 조사"},
            ]
        ],
    }
    result = structure_job_description(
        {"markdown": "| 세분류 | 경영기획 |", "blocks": [wrong_name_block]},
        filename="wrong-name.pdf",
    )
    assert result["fields"]["ability_units"] == []


def test_kordoc_rowspan_placeholders_do_not_shift_ncs_hierarchy_values() -> None:
    parsed = {
        "markdown": "| 세분류 | 사무행정 |",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "채용분야", "rowSpan": 2},
                        {"text": "행정", "rowSpan": 2},
                        {"text": "NCS 분류체계", "rowSpan": 2},
                        {"text": "대분류"},
                        {"text": "중분류"},
                        {"text": "소분류"},
                        {"text": "세분류"},
                    ],
                    [
                        {"text": ""},
                        {"text": ""},
                        {"text": ""},
                        {"text": "02.경영·회계·사무"},
                        {"text": "02.총무·인사"},
                        {"text": "03.일반사무"},
                        {"text": "02.사무행정"},
                    ],
                    [
                        {"text": "능력단위"},
                        {
                            "text": "01.문서작성, 02.문서관리, 06.회의운영",
                            "colSpan": 6,
                        },
                    ],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="rowspan-placeholders.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
    assert result["fields"]["ability_units_by_detail"] == {
        "사무행정": ["문서작성", "문서관리", "회의운영"]
    }


    positioned_units = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert len(positioned_units) == 3
    assert all(item["page"] == 0 for item in positioned_units)
    assert all(item["table_index"] == 0 for item in positioned_units)
    assert all(
        item["label_cell"]
        == {
            "row": 2,
            "column": 0,
            "row_span": 1,
            "column_span": 1,
        }
        for item in positioned_units
    )
    assert all(
        item["value_cell"]
        == {
            "row": 2,
            "column": 1,
            "row_span": 1,
            "column_span": 6,
        }
        for item in positioned_units
    )


def test_structure_job_description_scopes_grouped_units_across_separate_tables() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>인사</td><td>총무</td></tr>
</table>
<table>
<tr><th>능력단위</th><td>ㅇ (인사) 03.인력채용, 07.교육훈련운영 (총무) 04.비품관리, 07.업무지원</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="separate-tables.pdf")

    assert result["fields"]["ability_units_by_detail"] == {
        "인사": ["인력채용", "교육훈련운영"],
        "총무": ["비품관리", "업무지원"],
    }


def test_embedded_exact_detail_hint_overrides_conflicting_positional_scope() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>공공조달관리</td></tr>
</table>
<table>
<tr><th>NCS 세분류명</th><td>회계·감사</td></tr>
<tr><th>능력단위</th><td>(공공조달관리) 01.입찰실행 관리, 02.계약일반관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="conflict.pdf")

    assert result["fields"]["ability_units_by_detail"] == {
        "공공조달관리": ["입찰실행 관리", "계약일반관리"]
    }
    units = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert all(item["scope"]["source"] == "embedded_exact_detail_hint" for item in units)
    assert all(item["scope"]["positional_ncs_details"] == ["회계·감사"] for item in units)


def test_column_header_does_not_capture_wide_values_originating_before_it() -> None:
    markdown = """
<table>
<tr><th>직무</th><td>헬스키퍼</td><th colspan="2">능력단위분류번호</th><th colspan="2">능력단위</th></tr>
<tr><th>직무설명</th><td colspan="5">안마서비스를 제공한다.</td></tr>
<tr><th>수행과업</th><td>안마서비스</td><td colspan="4">피로도 진단과 맞춤형 압력 조절</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="wide-cells.pdf")

    assert result["fields"]["ability_units"] == []


def test_grouped_ability_unit_split_removes_trailing_bullet_separator() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>공공조달관리</td><td>인사</td></tr>
<tr><th>능력단위</th><td colspan="2">(공공조달관리) 01.입찰실행 관리 02.전자조달시스템 활용 • (인사) 03.임금관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="bullet.pdf")

    assert result["fields"]["ability_units"] == [
        "입찰실행 관리",
        "전자조달시스템 활용",
        "임금관리",
    ]


def test_grouped_units_keep_commas_inside_parenthetical_examples() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류명</th><td>통계조사</td><td>연구개발</td></tr>
<tr><th>능력단위</th><td colspan="2">(통계조사) 자료처리, 보고서작성 • (연구개발) 문서작성, 보고서작성, 사무용 프로그램(엑셀, 한글, 파워포인트 등) 활용</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="parenthetical.pdf")

    assert result["fields"]["ability_units_by_detail"] == {
        "통계조사": ["자료처리", "보고서작성"],
        "연구개발": [
            "문서작성",
            "보고서작성",
            "사무용 프로그램(엑셀, 한글, 파워포인트 등) 활용",
        ],
    }


def test_major_ability_unit_row_strips_explicit_ten_digit_codes() -> None:
    markdown = """
<table>
<tr><td rowspan="2">02. 사무행정</td><th colspan="2">주요능력단위</th><td>0202030201.<br>문서작성</td><td>0202030202.<br>문서관리</td></tr>
<tr><th colspan="2">필요지식</th><td colspan="2">문서관리 기준</td></tr>
</table>
"""

    result = structure_job_description(
        {"markdown": "| 세분류 | 사무행정 |\n" + markdown},
        filename="coded-units.hwp",
    )

    assert result["fields"]["ability_units"] == ["문서작성", "문서관리"]
    assert result["fields"]["ability_units_by_detail"] == {
        "사무행정": ["문서작성", "문서관리"]
    }


def test_structure_job_description_does_not_force_one_detail_for_multi_detail_table() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="4">시설관리</th></tr>
<tr><th>세분류</th><td>전기설비운영</td><td>전기안전관리</td><td colspan="2"></td></tr>
<tr><th>요구능력단위</th><td>전기설비 운영계획</td><td>전기안전 점검</td><td colspan="2"></td></tr>
<tr><th>필요지식</th><td colspan="4">전기설비와 안전관리 법령</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="multi.pdf")
    knowledge = next(
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "knowledge"
    )

    assert knowledge["scope"]["ncs_details"] == ["전기설비운영", "전기안전관리"]
    assert knowledge["scope"]["status"] == "multi_detail"
    assert knowledge["scope"]["review_required"] is True
    assert result["fields"]["ability_units_by_detail"] == {
        "전기설비운영": ["전기설비 운영계획"],
        "전기안전관리": ["전기안전 점검"],
    }


def test_structure_job_description_merges_detail_from_kordoc_table_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "구분"}, {"text": "내용"}],
                    [{"text": "NCS 세분류명"}, {"text": "사무행정"}],
                    [{"text": "담당업무"}, {"text": "문서 접수 및 보고자료 작성"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
    assert result["fields"]["ncs_detail_source"] == "explicit"
    assert result["fields"]["ncs_detail_candidate_evidence"][0]["detail"] == "사무행정"
    assert result["fields"]["ncs_detail_candidate_evidence"][0]["source"] == "kordoc"
    assert "사무행정" in result["fields"]["ncs_detail_candidate_evidence"][0]["snippet"]
    assert result["fields"]["duties"] == ["문서 접수 및 보고자료 작성"]


@pytest.mark.parametrize(
    "label",
    [
        "세분류명",
        "NCS 세분류명",
        "세분류(직무명)",
        "세분류(직무)",
    ],
)
def test_structure_job_description_extracts_plain_text_detail_aliases(label: str) -> None:
    result = structure_job_description(
        {"markdown": f"{label}: 프로젝트관리\n담당업무: 사업 일정 및 이해관계자 관리"},
        filename="plain-text-jd.txt",
    )

    assert result["fields"]["ncs_detail_candidates"] == ["프로젝트관리"]
    evidence = result["fields"]["ncs_detail_candidate_evidence"][0]
    assert evidence["source"] == "kordoc"
    assert "프로젝트관리" in evidence["snippet"]


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("010101 프로젝트관리", "프로젝트관리"),
        ("010101프로젝트관리", "프로젝트관리"),
        ("(010101) 프로젝트관리", "프로젝트관리"),
        ("01-01-01 프로젝트관리", "프로젝트관리"),
        ("01.01.01 프로젝트관리", "프로젝트관리"),
        ("[02020302] 사무행정", "사무행정"),
        ("02020302사무행정", "사무행정"),
        ("01010101-01 인사", "인사"),
        ("01010101-01인사", "인사"),
        ("010101:프로젝트관리", "프로젝트관리"),
        ("010101QM/QC관리", "QM/QC관리"),
        ("010101 3D프린터개발", "3D프린터개발"),
        ("1903113D프린터개발", "3D프린터개발"),
        # A two-digit table ordinal remains supported and is not mistaken for
        # a six-to-ten-digit NCS classification code.
        ("01. 프로젝트관리", "프로젝트관리"),
    ],
)
def test_structure_job_description_strips_full_ncs_code_prefix(
    raw_value: str,
    expected: str,
) -> None:
    result = structure_job_description(
        {"markdown": f"NCS 세분류명: {raw_value}"},
        filename="coded-jd.txt",
    )

    assert result["fields"]["ncs_detail_candidates"] == [expected]


@pytest.mark.parametrize(
    "raw_value",
    [
        "3D프린터개발",
        "CO₂배출관리",
        "2024채용관리",
        "123456비공식직무",
    ],
)
def test_structure_job_description_preserves_digit_leading_non_code_names(
    raw_value: str,
) -> None:
    result = structure_job_description(
        {"markdown": f"NCS 세분류명: {raw_value}"},
        filename="digit-leading-jd.txt",
    )

    assert result["fields"]["ncs_detail_candidates"] == [raw_value]


@pytest.mark.parametrize(
    "raw_value",
    [
        "010101",
        "(010101)",
        "01-01-01",
        "NCS 01010101",
    ],
)
def test_structure_job_description_rejects_code_only_detail_values(raw_value: str) -> None:
    result = structure_job_description(
        {"markdown": f"NCS 세분류명: {raw_value}"},
        filename="code-only-jd.txt",
    )

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_cleans_codes_from_kordoc_table_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "NCS 세분류명"},
                        {"text": "010101 프로젝트관리"},
                        {"text": "(02020302) 사무행정"},
                        {"text": "01. 총무"},
                        {"text": "010101"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="kordoc-block-jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == [
        "프로젝트관리",
        "사무행정",
        "총무",
    ]


def test_structure_job_description_recovers_aks_electric_jd_from_flattened_pdf_text() -> None:
    """A PDF conversion can drop page 1 while flattening page 2 table labels."""

    markdown = """
수행업무(전기기기유지보수) 1. 전기안전관리자의 직무(산업통상자원부고시 제2022-128호) 수행 2. 전기설비(수변전설비 및 발전기, 분전반 등) 운전 및 조작 업무 3. 전기설비 점검 및 기록, 보존 관리 4. 전기설비 유지보수 5. 전기설비 사고 응급조치 및 사후 복구 업무 ○ (전기기기 및 관련 시설물 관리) 자동제어설비의 조작 및 유지보수 ○ (소방안전관리) 소화활동설비의 조작 및 유지보수, 피난설비의 조작 및 유지보수필요지식·전기도면 지식 ·수전설비 구성형태 ·전기안전관리 법령 및 관련 지식 ·한국전기설비규정(KEC) ·소방설비관련 기본지식
2
※ 본 직무수행 내용은 기관의 고유직무를 반영하여 NCS에서 제시한 내용을 수정하였습니다.
필요기술·전기도면 판독능력 ·전기설비 점검 및 보수 능력 ·보호계전기 적용 기술 ·소방시설 점검 및 매뉴얼 분석
직무수행태도안전관리 및 법규 준수 의지, 안전사고 예방 및 대처 능력, 책임감
필요자격전기 분야 기능사 이상의 자격증 전기안전관리법 시행규칙 [별표8]의 안전관리보조원 이상 선임 기준 충족자
직업기초능력의사소통능력, 수리능력, 문제해결능력, 기술능력, 직업윤리
"""

    result = structure_job_description({"markdown": markdown}, filename="03. ncs 기반 채용 직무 설명자료.pdf")
    fields = result["fields"]

    assert fields["ncs_detail_candidates"] == [
        "전기기기유지보수",
        "전기설비운영",
        "전기안전관리",
        "소방안전관리",
    ]
    assert fields["ncs_detail_source"] == "contextual"
    assert fields["knowledge"] == [
        "·전기도면 지식 ·수전설비 구성형태 ·전기안전관리 법령 및 관련 지식 ·한국전기설비규정(KEC) ·소방설비관련 기본지식"
    ]
    assert fields["skills"] == [
        "·전기도면 판독능력 ·전기설비 점검 및 보수 능력 ·보호계전기 적용 기술 ·소방시설 점검 및 매뉴얼 분석"
    ]
    assert fields["attitudes"] == [
        "안전관리 및 법규 준수 의지, 안전사고 예방 및 대처 능력, 책임감"
    ]
    assert fields["qualifications"] == [
        "전기 분야 기능사 이상의 자격증 전기안전관리법 시행규칙 [별표8]의 안전관리보조원 이상 선임 기준 충족자"
    ]
    assert fields["basic_competencies"] == [
        "의사소통능력, 수리능력, 문제해결능력, 기술능력, 직업윤리"
    ]
    assert all("필요지식" not in duty and "필요기술" not in duty for duty in fields["duties"])
    assert all(item != "2" and "본 직무수행 내용" not in item for values in fields.values() if isinstance(values, list) for item in values if isinstance(item, str))


def test_structure_job_description_reads_required_qualification_from_kordoc_table_block() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "필요\n자격"},
                        {"text": "전기 분야 기능사 이상의 자격증"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.hwp")

    assert result["fields"]["qualifications"] == ["전기 분야 기능사 이상의 자격증"]


def test_structure_job_description_splits_numbered_detail_cells_from_kordoc_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "세분류명"}, {"text": "유원시설운영관리"}, {"text": "02.객실관리"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["유원시설운영관리", "객실관리"]


def test_structure_job_description_preserves_real_aksr_pdf_detail_cells_without_fragments() -> None:
    """Regression for ``03. ncs 기반 채용 직무 설명자료.pdf`` page 1."""
    markdown = """
<table>
<tr><td>세분류</td><td>02.<br>프로젝트관리</td><td>03.<br>산학협력관리</td><td>01.<br>경영기획</td><td>02.<br>경영평가</td><td>01.<br>총무</td><td>01.<br>인사</td><td>01. 비서<br>(글로벌경영사무<br>지원)</td><td>01. 예산</td></tr>
</table>
"""
    parsed = {
        "markdown": markdown,
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "세분류"},
                        {"text": "02.\n프로젝트관리"},
                        {"text": "03.\n산학협력관리"},
                        {"text": "01.\n경영기획"},
                        {"text": "02.\n경영평가"},
                        {"text": "01.\n총무"},
                        {"text": "01.\n인사"},
                        {"text": "01. 비서\n(글로벌경영사무\n지원)"},
                        {"text": "01. 예산"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="03. ncs 기반 채용 직무 설명자료.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "프로젝트관리",
        "산학협력관리",
        "경영기획",
        "경영평가",
        "총무",
        "인사",
        "비서 (글로벌경영사무 지원)",
        "예산",
    ]


def test_structure_job_description_rejects_number_parenthesis_and_page_fragments() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [
                        {"text": "세분류"},
                        {"text": "프로젝트관리 03."},
                        {"text": "페이지 1 / 4"},
                        {"text": "(글로벌경영사무"},
                        {"text": "지원)"},
                    ]
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="fragmented.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["프로젝트관리"]


def test_structure_job_description_does_not_recover_no_mapping_from_kordoc_blocks() -> None:
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "NCS 세분류명"}, {"text": "현재 NCS에 Mapping 가능한 직무가 없어 별도 분석"}],
                    [{"text": "중점 수행분야"}, {"text": "안전관리 및 사고예방"}],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"
    assert result["fields"]["ncs_detail_absence_declared_no_mapping"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is True


def test_loads_kordoc_json_recovers_after_stdout_warning() -> None:
    raw = 'Warning: Required "glyf" table is not found -- trying to recover.\n{"success": true, "markdown": "ok"}'

    result = _loads_kordoc_json(raw)

    assert result == {"success": True, "markdown": "ok"}


def test_structure_job_notice_extracts_duty_and_evaluation_windows() -> None:
    markdown = """
## 채용분야
경영기획 담당자 1명

## 담당업무
- 경영계획 수립 및 사업성과 분석
- 예산 운영 지원과 대내외 보고자료 작성

## 면접전형 평가항목
- 문제해결능력
- 의사소통능력
- 청렴성 및 조직적합도

## 기타사항
최종합격자는 임용 후 배치 예정
"""

    result = structure_job_notice({"markdown": markdown}, filename="notice.txt")

    assert "경영계획 수립" in result["fields"]["duty_text"]
    assert "문제해결능력" in result["fields"]["evaluation_text"]
    assert "기타사항" not in result["fields"]["evaluation_text"]


def test_structure_job_notice_prefers_interview_part_inside_selection_method() -> None:
    markdown = """
### 전형 방법

◦ 서류전형
- 평가 대상: 입사지원자 전원
- 전형 사항(평가 기준)
|항목|비고|
| --- | --- |
| 응시 요건의 적합성 | 채용 기준의 적합성, 블라인드 채용 기준의 위배 여부 등을 심사함. |
| 직무 수행 요건의 적합성 | 교육, 경력, 자격 요건 등이 채용 분야와 관련성이 있는지 여부를 심사함. |

◦ 필기전형
- 응시 대상: 서류전형 합격자(채용 예정 인원의 30배수 이내)
- 전형 사항(평가 기준)
- 가. 직업기초능력평가(NCS) 및 논술(1page보고서 작성)
|과 목|세부 내용|문항|비고|
| --- | --- | --- | --- |
| 직업기초능력평가(NCS) | 의사소통능력 | 15문항 | 60점 만점 |
| 직업기초능력평가(NCS) | 자원관리능력 | 15문항 | 60점 만점 |
- 나. 인적성 검사
인적성검사 결과는 필기전형 합격자의 면접전형 참고자료로만 활용함.
단, 인적성검사에 응시하지 않을 경우 면접전형 응시 불가

◦ 면접전형
- 응시 대상: 필기전형 합격자(채용 예정 인원의 5배수 이내)
- 면접 불참자는 불합격 처리함
- 전형 사항(평가 기준)
|항목|비고|
| --- | --- |
| 직무 역량 | 직무에 대한 이해도, 직무 수행에 필요한 전문지식, 창의력, 상황 대처 능력 등을 심사함. |
| 인성 및 자질 | 공직자로서 정신 자세, 인성, 태도, 표현력 등을 심사함. |

### 응시 원서 접수 및 전형 일정
- 면접 전형: 2025년 12월 초순 예정
"""

    result = structure_job_notice({"markdown": markdown}, filename="notice.txt")
    evaluation = result["fields"]["evaluation_text"]

    assert evaluation.startswith("◦ 면접전형")
    assert "직무 역량" in evaluation
    assert "인성 및 자질" in evaluation
    assert "서류전형" not in evaluation
    assert "직업기초능력평가" not in evaluation
    assert "응시 원서 접수" not in evaluation


def test_structure_job_description_filters_detail_label_noise() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 세분류 | 원자력발전설비운영 |
| 능력단위 | 원자력 발전설비 운전 |
| 주요사업 | 원자력 발전 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["원자력발전설비운영"]


@pytest.mark.parametrize("placeholder", ["해당사항 없음", "해당 없음", "없음", "미정"])
def test_structure_job_description_treats_no_detail_placeholders_as_absence(
    placeholder: str,
) -> None:
    result = structure_job_description(
        {"markdown": f"NCS 세분류명: {placeholder}"}, filename="jd.pdf"
    )

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_declared_no_mapping"] is True


def test_structure_job_description_preserves_long_current_official_detail_name() -> None:
    markdown = "| 세분류 | 지능형교통체계(ITS) 운영 및 유지관리 |"

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "지능형교통체계(ITS) 운영 및 유지관리"
    ]


def test_structure_job_description_recovers_numbered_official_detail_dangling_after_table() -> None:
    markdown = """
<table>
<tr><th>NCS 분류체계</th></tr>
<tr><td>세분류</td><td>04. 화공안전관리</td></tr>
</table>

05. 가스안전관리

#### 주요사업
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "화공안전관리",
        "가스안전관리",
    ]


def test_structure_job_description_trims_bullet_list_appended_to_official_detail_cell() -> None:
    markdown = """
<table>
<tr><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
<tr><td>10.영업판매</td><td>02.부동산</td><td>04.감정평가</td>
<td>01.부동산ㆍ동산 감정평가 ㅇ청약관리, ㅇ도시재생사업 ㅇ부동산 R&amp;D</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "부동산ㆍ동산 감정평가"
    ]


def test_structure_job_description_filters_job_definition_header_from_detail_values() -> None:
    markdown = """
| 세분류 | 직무정의 | 경영기획 | 사무행정 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["경영기획", "사무행정"]


def test_structure_job_description_filters_alio_flattened_neighbor_cell_noise() -> None:
    markdown = """
| 세분류 | 02 영상촬영 | 회계.감사 | 공단 소개 | 공단 주요 사업 | NCS기반 채용전형 절차 | 요구 능력 단위 | 요건 | 교육요건 | 서류접수 → 면접시험 | 공고문 참조 | 제한없음 | www.ncs.go.kr | 능력단위명칭 | 기술 명 NCS 참고 | 태도 명 \\ NCS 참고 | 핵심책무 | 직무 설명 |
"""

    result = structure_job_description({"markdown": markdown}, filename="alio-jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["영상촬영", "회계.감사"]


def test_structure_job_description_stops_flattened_detail_row_at_next_section() -> None:
    markdown = """
| 세분류 | 펀드운용 | 대체투자 | 직업공통능력 | 문제해결능력 | 의사소통능력 |
| 세분류 | 경영기획 | 능력단위명칭 | 사업환경 분석 | 경영방침 수립 |
"""

    result = structure_job_description({"markdown": markdown}, filename="flattened-jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "펀드운용",
        "대체투자",
        "경영기획",
    ]


def test_structure_job_description_extracts_abbreviated_detail_row_only_in_classification_table() -> None:
    markdown = """
<table>
<tr><th colspan="2">채용분야</th><th colspan="3">일반행정</th></tr>
<tr><td rowspan="4">분류체계</td><td>대</td><td>02.경영·회계·사무</td></tr>
<tr><td>중</td><td>01.기획사무</td><td>02.총무·인사</td></tr>
<tr><td>소</td><td>02.홍보·광고</td><td>03.일반사무</td></tr>
<tr><td>세</td><td>02.PR/광고</td><td>02.사무행정</td></tr>
<tr><td>능력단위</td><td>07.광고 집행 관리</td><td>01.문서작성</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="abbreviated.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["PR", "광고", "사무행정"]
    assert result["fields"]["ncs_detail_source"] == "explicit"


def test_structure_job_description_does_not_treat_standalone_se_as_detail_label() -> None:
    markdown = """
| 세 | 사무행정 |
| --- | --- |
| 담당업무 | 문서 작성 및 자료 정리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="ordinary-table.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_filters_combined_basic_competency_cell() -> None:
    markdown = """
| 세분류 | 펀드운용 | 문제해결능력, 의사소통능력, 디지털능력, 자기관리능력, 직업윤리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="basic-competency-jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["펀드운용"]


def test_structure_job_description_cleans_detail_candidate_punctuation() -> None:
    markdown = """
| 세분류 | 영상의학 (특화분류) | 임상병리 (특화분류) | 간호업무 보조/ | 재원환자 관리, |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "영상의학",
        "임상병리",
        "간호업무 보조",
        "재원환자 관리",
    ]
    assert result["fields"]["ncs_detail_source"] == "explicit"


def test_structure_job_description_extracts_detail_from_combined_specialized_header() -> None:
    markdown = """
| 항목 | 내용1 | 내용2 |
| --- | --- | --- |
| 소분류 세분류(특화분류) | 간호업무 보조 | 간호행정 보조 |
| 능력단위 | 환자 이송 지원 | 진료 행정 지원 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "간호업무 보조",
        "간호행정 보조",
    ]


def test_structure_job_description_filters_section_and_duty_sentence_noise() -> None:
    markdown = """
| 세분류 | 스포츠시설운영관리 | 개발 전 | 직무개요 | 세부직무및직무수행내용 | 02, 스포츠시설 운영관리 | 청소 및 환경미화 업무 ○ 잡역 등 부대업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["스포츠시설운영관리"]


def test_structure_job_description_expands_composite_cooking_detail_candidate() -> None:
    markdown = """
| 세분류 | 한식조리 | 일식· 복어・조리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "한식조리",
        "일식조리",
        "복어조리",
    ]

    evidence_by_detail = _evidence_by_detail(result)
    for candidate in result["fields"]["ncs_detail_candidates"]:
        evidence = evidence_by_detail[candidate]
        assert evidence["source"] in {"markdown", "kordoc"}
        assert evidence["snippet"]
        assert evidence["snippet"] != candidate


def test_structure_job_description_splits_comma_and_slash_detail_candidates() -> None:
    markdown = """
| 세분류 | 총무, 사무행정 | 한식조리/양식조리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "총무",
        "사무행정",
        "한식조리",
        "양식조리",
    ]


def test_structure_job_description_preserves_official_acronym_slash_detail() -> None:
    markdown = """
<table>
<tr><td rowspan="2">NCS<br>분류체계</td><td colspan="5">대분류 중분류 소분류 세분류</td></tr>
<tr><td colspan="2">02.경영·회계·사무</td><td>04.생산·품질관리</td><td>02.품질관리</td><td>01.QM/QC관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="quality.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["QM/QC관리"]
    assert "QM" not in result["fields"]["ncs_detail_candidates"]
    assert "QC관리" not in result["fields"]["ncs_detail_candidates"]


def test_structure_job_description_resolves_merged_hierarchy_cells_to_last_detail_column() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="5">기후대응 환경생태분야 전문연구원</th></tr>
<tr><td rowspan="5">NCS<br>분류체계</td><td colspan="5">대분류 중분류 소분류 세분류</td></tr>
<tr><td colspan="2" rowspan="2">14.건설</td><td rowspan="2">05.조경</td><td rowspan="2">01.조경</td><td>02.조경시공</td></tr>
<tr><td>03.조경관리</td></tr>
<tr><td colspan="2" rowspan="2">23.환경․에너지․안전</td><td rowspan="2">03.자연환경</td><td rowspan="2">01.생태복원⋅관리</td><td>01.생태복원</td></tr>
<tr><td>02.생태관리</td></tr>
<tr><td>중점수행분야</td><td colspan="5">도로 비탈면 생태복원 분야</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="ecology.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "조경시공",
        "조경관리",
        "생태복원",
        "생태관리",
    ]
    assert "조경" not in result["fields"]["ncs_detail_candidates"]
    assert "생태복원⋅관리" not in result["fields"]["ncs_detail_candidates"]


def test_structure_job_description_accepts_detail_job_header_with_rowspans() -> None:
    markdown = """
<table>
<tr><th rowspan="2">모집분야</th><th rowspan="2">운전</th><th colspan="2" rowspan="2">분류체계</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류(직무)</th></tr>
<tr><td>09. 운전·운송</td><td>01. 자동차운전·운송</td><td>01. 자동차운전·운송</td><td>01. 여객운송</td></tr>
<tr><td>주요사업</td><td colspan="7">항만시설 관리와 운영</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="driver.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["여객운송"]
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is False


def test_structure_job_description_accepts_detail_job_name_header() -> None:
    markdown = """
<table>
<tr><td>NCS 분류체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류(직무명)</td></tr>
<tr><td></td><td>02. 경영·회계·사무</td><td>02. 총무·인사</td><td>03. 일반사무</td><td>02. 사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="office.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]


def test_structure_job_description_rejects_ability_unit_list_mislabeled_as_detail() -> None:
    markdown = """
<table>
<tr><td colspan="4">■ NCS 분류체계</td></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>02. 경영·회계·사무</td><td>03. 일반사무</td><td>02. 사무행정</td><td>02. 문서 관리<br>03. 자료 관리<br>07. 사무행정 업무 관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="records.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_detail_candidate_filtered"


def test_structure_job_description_splits_long_numbered_official_detail_cell() -> None:
    markdown = """
<table>
<tr><th rowspan="2">직무분야</th><th colspan="4">NCS 분류체계</th></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>기계</td><td>15.기계</td><td>01.기계설계</td><td>01.설계기획</td><td>01.기계설계기획<br>01.냉동공조설계<br>03.냉동공조유지보수관리<br>04.보일러설치정비</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="mechanical.hwp")

    assert result["fields"]["ncs_detail_candidates"] == [
        "기계설계기획",
        "냉동공조설계",
        "냉동공조유지보수관리",
        "보일러설치정비",
    ]


def test_structure_job_description_does_not_promote_information_technology_small_category() -> None:
    markdown = """
<table>
<tr><td rowspan="6">NCS<br>분류체계</td><td colspan="5">대분류 중분류 소분류 세분류</td></tr>
<tr><td colspan="2" rowspan="4">20. 정보통신</td><td rowspan="4">01. 정보기술</td><td>01. 정보기술전략·계획</td><td>05. 빅데이터분석</td></tr>
<tr><td rowspan="2">02. 정보기술개발</td><td>03. 임베디드SW엔지니어링</td></tr>
<tr><td>17. AIoT운영플랫폼 구축</td></tr>
<tr><td>07. 인공지능</td><td>03. 인공지능모델링</td></tr>
<tr><td colspan="2">14. 건설</td><td>01. 건설공사관리</td><td>04. 스마트건설관리</td><td>02. 스마트건설정보관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="ai.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "빅데이터분석",
        "임베디드SW엔지니어링",
        "AIoT운영플랫폼 구축",
        "인공지능모델링",
        "스마트건설정보관리",
    ]
    assert "정보기술전략·계획" not in result["fields"]["ncs_detail_candidates"]
    assert "스마트건설관리" not in result["fields"]["ncs_detail_candidates"]


def test_structure_job_description_rejects_collapsed_four_level_hierarchy_cell() -> None:
    markdown = """
<table>
<tr><td rowspan="4">NCS<br>분류체계</td><td>대분류</td><td colspan="4" rowspan="4">02. 경영·회계·사무 20. 정보통신<br>01. 기획사무 02. 총무·인사 01. 정보기술<br>01. 경영 03. 일반 07. 인공지능<br>01. 경영기획 02. 사무행정 07. 생성형AI엔지니어링</td></tr>
<tr><td>중분류</td></tr>
<tr><td>소분류</td></tr>
<tr><td>세분류</td></tr>
<tr><td>요건</td><td colspan="5">공고문 참고</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="collapsed.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is True


def test_structure_job_description_rejects_ncs_detail_description_heading() -> None:
    markdown = """
<table>
<tr><th colspan="3">NCS 세분류 직무 설명</th></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="heading.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_does_not_promote_small_category_when_detail_cell_is_blank() -> None:
    markdown = """
<table>
<tr><th rowspan="4">채용분야</th><th rowspan="4">보건직</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
<tr><td>06.보건·의료</td><td>02.의료</td><td>05.보건</td><td>-</td></tr>
<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>01.총무</td><td>01.총무</td></tr>
<tr><td>02.경영·회계·사무</td><td>02.총무·인사</td><td>03.일반사무</td><td>02.사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["총무", "사무행정"]


def test_structure_job_description_uses_detail_column_not_small_category_in_html_header_table() -> None:
    markdown = """
<table>
<tr><th rowspan="3">직무분야</th><th rowspan="3">간호조무</th><th colspan="4">NCS 분류체계</th></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>06.보건/의료</td><td>02.의료</td><td>05.간호조무</td><td>01.간호업무 보조<br>(특화분류)</td></tr>
<tr><td>주요사업</td><td colspan="5">환자 지원 업무</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["간호업무 보조"]
    assert "간호조무" not in result["fields"]["ncs_detail_candidates"]


def test_structure_job_description_reads_detail_from_classification_marker_row() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th></th><th rowspan="2">기간제직원수시채용</th><th>연구원</th><th>대분류</th><th>중분류</th><th>소분류</th><th>세분류</th></tr>
<tr><td>채용분야</td><td>분류체계</td><td>공공분야</td><td>공공정책연구개발</td><td colspan="2">문화〮관광정책</td></tr>
<tr><td>담당업무</td><td colspan="6">정책연구, 조사, 평가</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "kcti.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["문화〮관광정책"]
    assert result["fields"]["ncs_detail_source"] == "explicit"


def test_structure_job_description_stops_html_detail_backfill_at_required_ability_row() -> None:
    markdown = """
<table>
<tr><th rowspan="3">채용분야</th><th rowspan="3">기술직</th><th colspan="4">NCS 분류체계</th></tr>
<tr><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>14.건설</td><td>03.건설기계운전·정비</td><td>03.건설기계정비</td><td>01.건설기계정비</td></tr>
<tr><td>필요능력</td><td colspan="5">공사감독 및 안전관리 분야의 관련 법령 이해</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["건설기계정비"]


def test_structure_job_description_does_not_extract_when_table_declares_no_ncs_mapping() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="6">안전순찰원</th></tr>
<tr><td rowspan="2">NCS<br>분류<br>체계</td><td colspan="2">대분류</td><td>중분류</td><td colspan="2">소분류</td><td>세분류</td></tr>
<tr><td colspan="5">현재 NCS에 Mapping 가능한 직무(세분류)가 없어,<br>별도 분석을 통해 내용 도출</td><td>안전순찰원</td></tr>
<tr><td>중점<br>수행분야</td><td colspan="6">안전관리 및 사고예방</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_marks_undeveloped_ncs_classification_as_no_mapping() -> None:
    markdown = """
# 직무기술서 : 인증 및 환자안전
<table>
<tr><th rowspan="4">채용분야</th><th colspan="2" rowspan="4">인증 및 환자안전</th><th>대분류</th><th rowspan="4">현재 NCS 분류체계 미개발 분야</th></tr>
<tr><td>중분류</td></tr>
<tr><td>소분류</td></tr>
<tr><td>세분류</td></tr>
<tr><td>직무내용</td><td colspan="4">인증제도 운영 및 환자안전사고 보고 자료 관리</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_treats_inline_undeveloped_detail_as_no_mapping() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 | 세분류 |
| --- | --- | --- | --- | --- |
| 채용분야 | 연구직 | 연구 | 연구 | 연구(미개발) |
| 직무수행내용 | 연구과제 기획 및 수행 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "no_ncs_mapping_declared"


def test_structure_job_description_marks_blank_detail_cell_state() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 | 세분류 |
| --- | --- | --- | --- | --- |
| 채용분야 | 경영·회계·사무 | 총무ㆍ인사 | 일반사무 | - |
| 직무수행내용 | 자료 취합 및 문서 관리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_detail_cell_blank_or_dash"
    assert result["fields"]["ncs_detail_absence_saw_ncs_table"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is True
    assert result["fields"]["ncs_detail_absence_blank_or_dash_detail_cell"] is True


def test_structure_job_description_marks_filtered_detail_candidate_state() -> None:
    markdown = """
| NCS 분류체계 | 세분류 |
| --- | --- |
| 채용분야 | 자료 취합 및 문서 관리 업무를 수행하고 대내외 보고자료 작성 및 부대업무를 담당 |
| 직무수행내용 | 자료 취합 및 문서 관리 |
| 2026 | 1234 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_detail_candidate_filtered"
    filtered_reasons = result["fields"]["ncs_detail_absence_filtered_candidate_reason"].split("; ")
    assert "filtered_candidate_not_detail_like" in filtered_reasons
    assert "value_too_long" in filtered_reasons
    assert "filtered_candidate_reason=value_too_long" in result["fields"]["ncs_detail_absence_state"]


def test_structure_job_description_marks_ncs_table_without_detail_header() -> None:
    markdown = """
| NCS 분류체계 | 대분류 | 중분류 | 소분류 |
| --- | --- | --- | --- |
| 채용분야 | 사업관리 | 사업관리 | 프로젝트관리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "ncs_table_without_detail_header"
    assert result["fields"]["ncs_detail_absence_saw_ncs_table"] is True
    assert result["fields"]["ncs_detail_absence_saw_detail_header"] is False


def test_structure_job_description_marks_job_document_without_explicit_ncs_detail() -> None:
    markdown = """
# 직무소개서
| 채용분야 | 업무지원직 |
| --- | --- |
| 세부직무 | 배치부서 업무지원 |
| 업무내용 | 우편물 관리, 환자 안내, 환경관리, 공연 전시 업무보조 |
| 직무요건 | [지식] 병원 환경 관리에 대한 이해 [기술] 문서작성 및 사무기기 활용 |
"""

    result = structure_job_description({"markdown": markdown}, filename="job-intro.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "job_document_markers_without_ncs_classification"
    )
    assert "직무소개서" in result["fields"]["ncs_detail_absence_evidence"]


def test_structure_job_description_does_not_treat_generic_taxonomy_as_ncs() -> None:
    markdown = """
# 직무기술서
<table>
<tr><th>분류체계</th><th>모집분야</th><th>인공지능 정책 연구</th></tr>
<tr><td>세부모집분야</td><td colspan="2">이용자보호 정책 연구</td></tr>
<tr><td>참고사이트</td><td colspan="2">www.ncs.go.kr</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="연구직무.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert (
        result["fields"]["ncs_detail_absence_reason"]
        == "job_document_without_explicit_ncs_detail"
    )


def test_structure_job_description_marks_recruitment_notice_not_jd() -> None:
    markdown = """
# 2026년 공개채용 공고
입사지원서 접수기간과 전형절차를 안내합니다.
필기시험은 NCS 분야를 포함하며 분야별 직무기술서는 별표를 참조합니다.
"""

    result = structure_job_description({"markdown": markdown}, filename="채용공고문.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert (
        result["fields"]["ncs_detail_absence_reason"]
        == "recruitment_notice_not_job_description"
    )
    assert "recruitment_notice_markers" in result["fields"]["ncs_detail_absence_state"]


def test_structure_job_description_marks_unapplied_ocr_as_extraction_failure() -> None:
    parsed = {
        "markdown": "![image](image_001.png)",
        "warnings": [{"code": "NEEDS_OCR", "message": "OCR required"}],
    }

    result = structure_job_description(parsed, filename="스캔_직무기술서.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert (
        result["fields"]["ncs_detail_absence_reason"]
        == "ocr_required_extraction_failure"
    )
    assert result["fields"]["ncs_detail_absence_state"] == "ocr_required_without_ocr_output"


def test_structure_job_description_marks_empty_parser_output_as_failure() -> None:
    result = structure_job_description({"markdown": ""}, filename="upload.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert (
        result["fields"]["ncs_detail_absence_reason"]
        == "empty_document_extraction_failure"
    )
    assert result["fields"]["ncs_detail_absence_state"] == "empty_parser_output"


def test_structure_job_description_continues_after_no_ncs_mapping_row_for_later_explicit_detail() -> None:
    markdown = """
<table>
<tr><td rowspan="2">NCS<br>분류<br>체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td colspan="3">현재 NCS에 Mapping 가능한 직무(세분류)가 없어 별도 분석</td><td>안전순찰원</td></tr>
<tr><td>세분류</td><td colspan="4">사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]


def test_structure_job_description_cleans_html_table_detail_candidate_punctuation() -> None:
    markdown = """
<table>
<tr><td>세분류</td><td>영상의학 (특화분류)</td><td>임상병리 (특화분류)</td><td>간호업무 보조/</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == [
        "영상의학",
        "임상병리",
        "간호업무 보조",
    ]


def test_structure_job_description_infers_high_confidence_wastewater_detail_when_no_label() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 채용분야 | 수탁운영(하수도 시설운영 지원) |
| 근무예정부서 | 보령권지사 장항물재생센터 |
| 직무내용(세부업무) | 채수, 수질검사, 실험실 일지관리 등 수질실험실 운영을 위한 보조업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["하수처리시설운영관리"]
    assert result["fields"]["duties"] == [
        "채수, 수질검사, 실험실 일지관리 등 수질실험실 운영을 위한 보조업무"
    ]


def test_structure_job_description_does_not_infer_translation_as_ncs_detail() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 직무명 | 한국어-영어 통·번역사 |
| 직무내용(세부업무) | 한영 번역과 회의 시 통역 업무 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "translation_role_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "translation_role_markers_without_ncs_detail"
    )


def test_structure_job_description_marks_multi_role_healthcare_document_without_detail() -> None:
    markdown = """
# 채용 직무 설명자료
강원대학교병원 직종별 설명자료
간호직, 의료기술직, 약무직, 업무협력직, 임상교수, 임상병리, 영상의학, 의무기록 업무를 포함한다.
직무내용: 병원 내 여러 직종의 진료지원, 행정, 검사, 시설 업무를 통합 안내한다.
"""

    result = structure_job_description({"markdown": markdown}, filename="hospital.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "multi_role_healthcare_document_without_explicit_ncs_detail"
    state = result["fields"]["ncs_detail_absence_state"]
    assert "multi_role_healthcare_markers_without_ncs_detail" in state
    assert "healthcare_marker_count=8" in state
    assert "강원대학교병원" in result["fields"]["ncs_detail_absence_evidence"]


def test_structure_job_description_does_not_treat_ability_unit_as_detail_classification() -> None:
    markdown = """
# 직무설명자료
| 항목 | 내용 |
| --- | --- |
| 직무내용 | 환자 예약과 검사 업무를 지원한다. |
| 능력단위 | 방사선 검사 업무, 환자 교육 및 관리, 장비 관리 |
"""

    result = structure_job_description({"markdown": markdown}, filename="hospital.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "job_document_markers_without_ncs_classification"
    )


def test_structure_job_description_uses_job_description_filename_for_sparse_no_detail_state() -> None:
    result = structure_job_description(
        {"markdown": "단기 업무 지원"},
        filename="붙임3_직무기술서.hwp",
    )

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert "직무기술서.hwp" in result["fields"]["ncs_detail_absence_evidence"]


def test_structure_job_description_does_not_infer_ambiguous_power_plant_detail() -> None:
    markdown = """
| 항목 | 내용 |
| --- | --- |
| 공사명 | 영흥 5호기 계획예방정비공사 |
| 직무내용(세부업무) | 전기설비 정비 업무 보조 및 발전설비 유지보수 지원 |
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_structure_job_description_infers_youngheung_thermal_power_detail_when_no_label() -> None:
    markdown = """
<table>
<tr><th>근무지</th><th colspan="2">○ 한전KPS 영흥사업처<br>- 주소 : 인천광역시 옹진군 영흥면</th></tr>
<tr><td>직무수행<br>내 용</td><td colspan="2">○ 2026년 영흥 5호기 계획예방정비공사 정비업무 보조<br>- 전기설비 정비 업무 보조</td></tr>
<tr><td>필요지식</td><td colspan="2">○ 발전설비에 대한 올바른 이해<br>○ 발전설비의 유지보수에 관한 기초 지식</td></tr>
<tr><td>필요기술</td><td colspan="2">○ 설비별, 기기별 정비 절차 이해<br>○ 안전 수칙 준수</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["화력발전설비운영"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_does_not_infer_power_detail_for_youngheung_office_assistant() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><td>사무보조</td></tr>
<tr><th>근무지</th><td>한전KPS 영흥사업처 총무부</td></tr>
<tr><th>직무수행 내용</th><td>5호기 계획예방정비공사 사무 업무 보조, 문서 작성, 전산 입력 지원</td></tr>
<tr><th>필요지식</th><td>사무업무에 대한 기본 지식</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="직무기술서(사무보조)_총무부.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""
    assert result["fields"]["ncs_detail_absence_reason"] == "job_document_without_explicit_ncs_detail"
    assert (
        result["fields"]["ncs_detail_absence_state"]
        == "job_document_markers_without_ncs_classification"
    )


def test_structure_job_description_infers_old_water_pipe_detail_when_no_label() -> None:
    markdown = """
<table>
<tr><th>채용분야</th><th colspan="3">기술관리_건설사업</th></tr>
<tr><td>근무예정부서</td><td>모집인원</td><td>근무지역</td></tr>
<tr><td>의령2 노후상수관망정비사업소</td><td>1명</td><td>의령군</td></tr>
<tr><td>직무내용</td><td colspan="3">- 노후상수도 정비사업 공사감독, 안전관리 및 사업관리<br>- 노후상수도 정비사업 관련, 누수탐사‧복구 및 민원처리 업무 등</td></tr>
<tr><td>필요능력</td><td colspan="3">건설기술 분야의 공사감독 및 안전관리, 사업관리 분야의 관련 법령</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="jd.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["상수관로시설운영관리"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_infers_health_education_and_industrial_health_when_no_label() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th>채용분야</th><td>의료보조(보건관리)</td></tr>
<tr><td>직무내용</td><td>
⚬ (보건교육) 개인과 집단의 질병예방 및 건강증진을 위하여 보건교육 요구도 진단 및 수립
⚬ (보건관리계획수립평가) 연간보건관리계획 수립
⚬ (사업장 건강증진) 산업안전보건법에 따른 건강진단 시행 및 사후관리
⚬ (작업환경측정 평가개선) 본사 부서 작업환경측정 지원
</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "health.pdf")

    assert result["fields"]["ncs_detail_candidates"] == ["보건교육", "산업보건관리"]
    assert result["fields"]["ncs_detail_source"] == "contextual"


def test_structure_job_description_does_not_infer_broad_health_management_label_only() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th>채용분야</th><td>의료보조(보건관리)</td></tr>
<tr><td>직무내용</td><td>보건관리 업무 지원 및 자료 정리</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, "health.pdf")

    assert result["fields"]["ncs_detail_candidates"] == []
    assert result["fields"]["ncs_detail_source"] == ""


def test_split_ability_unit_entries_keeps_unstructured_internal_bullet_prose() -> None:
    rows = _split_ability_unit_entries(
        "(법무) 종합적으로 법률을 해석하고 법적 쟁점을 분석하는 능력 ○ "
        "법적 문제의 합리적인 문제해결 및 대안 제시 능력"
    )

    assert [row["text"] for row in rows] == [
        "종합적으로 법률을 해석하고 법적 쟁점을 분석하는 능력 "
        "○ 법적 문제의 합리적인 문제해결 및 대안 제시 능력"
    ]
    assert [row["detail_hint"] for row in rows] == ["법무"]


def test_structure_job_description_scopes_base_detail_hint_and_split_custom_tail() -> None:
    markdown = """
<table>
<tr><th>NCS 세분류</th><td>사무행정</td></tr>
<tr><th>능력단위</th><td>(사무행정(기록물)) 02.문서관리, 03.자료관리, 08.사무환경조성, 기록물관리(생산·분류·편철·이관·보존·폐기)</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="records.hwp")

    assert result["fields"]["ability_units_by_detail"]["사무행정"] == [
        "문서관리",
        "자료관리",
        "사무환경조성",
        "기록물관리(생산·분류·편철·이관·보존·폐기)",
    ]
    positioned = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert all(item["scope"]["ncs_details"] == ["사무행정"] for item in positioned)
    assert all(
        item["scope"].get("source") == "embedded_exact_base_detail_hint"
        for item in positioned
    )


def test_structural_ability_cell_uses_declared_detail_and_source_boundaries() -> None:
    parsed = {
        "markdown": """
<table>
<tr><th>세분류</th><td>양약조제</td></tr>
<tr><th>능력단위</th><td>○ (처방조제) 의약품 조제 및 감사, 투약 및 복약설명<br>○ (교육) 직무 교육</td></tr>
</table>
<table>
<tr><th>세분류</th><td>임상병리사</td></tr>
<tr><th>능력단위</th><td>○ 채혈실 – 환자 안내 및 검사 설명, 능숙한 채혈<br>○ 응급검사실, 자동화검사실 – 장비 정도관리 및 점검<br>○ 혈핵은행 – 혈액형 검사 및 수혈적합성 검사</td></tr>
</table>
<table>
<tr><th>세분류</th><td>병원안내</td></tr>
<tr><th>능력단위</th><td>○ 병원 이용안내, 고객상담 및 민원응대, 응급·위급상황 대응, 통화 품질 및 데이터 관리, 서비스 품질 및 윤리준수 등</td></tr>
</table>
"""
    }

    result = structure_job_description(parsed, filename="structural.hwp")

    assert result["fields"]["ability_units_by_detail"] == {
        "양약조제": [
            "처방조제: 의약품 조제 및 감사",
            "처방조제: 투약 및 복약설명",
            "교육: 직무 교육",
        ],
        "임상병리사": ["채혈실", "응급검사실", "자동화검사실", "혈핵은행"],
        "병원안내": [
            "병원 이용안내",
            "고객상담 및 민원응대",
            "응급·위급상황 대응",
            "통화 품질 및 데이터 관리",
            "서비스 품질 및 윤리준수",
        ],
    }
    assert "능숙한 채혈" not in result["fields"]["ability_units"]
    assert "장비 정도관리 및 점검" not in result["fields"]["ability_units"]
    assert "혈액은행" not in result["fields"]["ability_units"]
    positioned = [
        item
        for item in result["fields"]["positioned_items"]
        if item["section"] == "ability_units"
    ]
    assert all(item["raw_cell_text"] for item in positioned)
    assert all(item["scope"]["status"] == "single_detail" for item in positioned)


def test_detail_header_does_not_promote_adjacent_ncs_code_header() -> None:
    markdown = """
<table>
<tr><td colspan="2">대분류</td><td colspan="8">중분류 소분류</td><td colspan="5">세분류</td><td colspan="4">NCS 코드</td></tr>
<tr><td colspan="2">06. 보건·의료</td><td colspan="8">01. 보건 02. 보건지원</td><td colspan="5">01. 병원행정</td><td colspan="4">06010201</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="wide-table.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["병원행정"]
    assert "NCS 코드" not in result["fields"]["ncs_detail_candidates"]


@pytest.mark.parametrize("header", ["NCS CODE", "NCS 분류코드", "NCS 코드(8자리)"])
def test_detail_header_rejects_common_ncs_code_header_variants(header: str) -> None:
    assert _looks_like_detail_candidate(header) is False


def test_detail_cell_trims_etc_only_after_exact_official_detail() -> None:
    markdown = """
<table>
<tr><td>대분류</td><td>12. 이용·숙박·여행·오락·스포츠</td></tr>
<tr><td>중분류</td><td>04. 스포츠</td></tr>
<tr><td>소분류</td><td>03. 스포츠경기·지도</td></tr>
<tr><td>세분류</td><td>06. 경기지원 등</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="etc-detail.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["경기지원"]


def test_general_job_information_detail_does_not_pollute_later_ncs_detail() -> None:
    markdown = """
<table>
<tr><td rowspan="2">일반직무정보</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>전산(IT)</td><td>정보시스템운영</td><td>정보시스템구축</td><td>정보시스템 설계‧개발</td></tr>
<tr><td rowspan="2">NCS 분류체계</td><td>대분류</td><td>중분류</td><td>소분류</td><td>세분류</td></tr>
<tr><td>정보통신</td><td>정보기술</td><td>정보기술개발</td><td>응용SW엔지니어링</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="dual-hierarchy.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["응용SW엔지니어링"]


def test_flattened_parenthesized_hierarchy_recovers_terminal_detail_only() -> None:
    markdown = """
<table>
<tr><td>국가NCS<br>www.ncs.go.kr</td><td>(대분류)11.경비청소 - (중분류)02.청소 - (소분류)01.청소 - (세분류)01.환경미화</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="flat-hierarchy.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["환경미화"]


def test_flattened_parenthesized_detail_requires_same_row_ncs_context() -> None:
    markdown = """
<table>
<tr><td>일반직무정보</td></tr>
<tr><td>(대분류)지원 - (중분류)경영 - (소분류)운영지원 - (세분류)환경미화</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="general-only.hwp")

    assert result["fields"]["ncs_detail_candidates"] == []


def test_flattened_parenthesized_detail_stops_before_ability_unit_tail() -> None:
    markdown = """
<table>
<tr><td>국가NCS</td><td>(대분류)11.경비청소 - (중분류)02.청소 - (소분류)01.청소 - (세분류)01.환경미화 (능력단위)01.청소현장현황파악 / 사무행정</td></tr>
</table>
"""

    result = structure_job_description({"markdown": markdown}, filename="flat-tail.hwp")

    assert result["fields"]["ncs_detail_candidates"] == ["환경미화"]


def test_large_ability_table_normalization_work_stays_near_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_norm = kordoc_parser._norm
    calls = 0

    def counted_norm(value):
        nonlocal calls
        calls += 1
        return original_norm(value)

    monkeypatch.setattr(kordoc_parser, "_norm", counted_norm)
    row_count = 500
    markdown = "# 능력단위\n" + "\n".join(
        f"- {index}. 합성 능력단위 {index}" for index in range(row_count)
    )

    result = structure_job_description(
        {"markdown": markdown, "blocks": []},
        filename="large-table.txt",
    )

    assert len(result["fields"]["ability_units"]) == row_count
    assert calls < 100_000


def test_large_coordinate_ability_table_normalization_stays_near_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_norm = kordoc_parser._norm
    calls = 0

    def counted_norm(value):
        nonlocal calls
        calls += 1
        return original_norm(value)

    monkeypatch.setattr(kordoc_parser, "_norm", counted_norm)
    row_count = 500
    parsed = {
        "markdown": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"text": "NCS 세분류명"}, {"text": "요구능력단위"}],
                    *[
                        [
                            {"text": "사무행정"},
                            {"text": f"합성 능력단위 {index}"},
                        ]
                        for index in range(row_count)
                    ],
                ],
            }
        ],
    }

    result = structure_job_description(parsed, filename="large-coordinate-table.hwp")

    assert len(result["fields"]["ability_units"]) == row_count
    assert calls < 50_000


def test_empty_detail_candidates_skip_evidence_surface_precomputation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_surface(_value: object):
        raise AssertionError("empty candidate evidence must return before preprocessing")

    monkeypatch.setattr(kordoc_parser, "_detail_search_surface", unexpected_surface)

    assert kordoc_parser._detail_candidate_evidence(
        [],
        {"ncs_detail": []},
        "\n".join(f"ordinary line {index}" for index in range(30_000)),
        "",
    ) == []


def test_markdown_table_logical_cell_budget_is_enforced() -> None:
    columns = 10
    row = "|" + "|".join(f"cell-{index}" for index in range(columns)) + "|"
    markdown = "\n".join([row] * (kordoc_parser._HTML_TABLE_MAX_LOGICAL_CELLS // columns + 1))

    with pytest.raises(
        kordoc_parser.KordocStructureLimitError,
        match="Markdown table size exceeds the safe limit",
    ):
        structure_job_description({"markdown": markdown}, filename="oversized-table.md")


@pytest.mark.parametrize("line_break", ["\n", "\r", "\x85", "\u2028"])
def test_document_line_budget_rejects_before_structural_extraction(
    monkeypatch: pytest.MonkeyPatch,
    line_break: str,
) -> None:
    monkeypatch.setattr(
        kordoc_parser,
        "_parser_provenance",
        lambda _parsed: (_ for _ in ()).throw(
            AssertionError("preflight must run before extraction")
        ),
    )
    markdown = line_break.join(
        f"ordinary line {index}"
        for index in range(kordoc_parser._DOCUMENT_MAX_TEXT_LINES + 1)
    )

    with pytest.raises(
        kordoc_parser.KordocStructureLimitError,
        match="document line count exceeds the safe limit",
    ):
        structure_job_description({"markdown": markdown}, filename="too-many-lines.md")


@pytest.mark.parametrize("line_count,line_width", [(1, 1_000_001), (5_000, 512)])
def test_document_markdown_character_budget_rejects_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
    line_count: int,
    line_width: int,
) -> None:
    monkeypatch.setattr(
        kordoc_parser,
        "_parser_provenance",
        lambda _parsed: (_ for _ in ()).throw(
            AssertionError("preflight must run before extraction")
        ),
    )
    markdown = "\n".join(["x" * line_width] * line_count)
    assert len(markdown) > kordoc_parser._DOCUMENT_MAX_MARKDOWN_CHARS

    with pytest.raises(
        kordoc_parser.KordocStructureLimitError,
        match="document Markdown size exceeds the safe limit",
    ):
        structure_job_description({"markdown": markdown}, filename="too-large.md")


def test_document_markdown_exact_character_budget_is_accepted() -> None:
    markdown = "x" * kordoc_parser._DOCUMENT_MAX_MARKDOWN_CHARS

    result = structure_job_description(
        {"markdown": markdown},
        filename="exact-character-budget.md",
    )

    assert len(result["document"]["markdown"]) == (
        kordoc_parser._DOCUMENT_MAX_MARKDOWN_CHARS
    )


def test_packed_single_cell_detail_candidate_group_is_bounded() -> None:
    packed = ",".join(
        f"detail-{index:03}"
        for index in range(kordoc_parser._DETAIL_COMPOSITE_MAX_SEGMENTS + 1)
    )
    markdown = f"| NCS 세분류명 |\n|---|\n| 세분류 | {packed} |"

    with pytest.raises(
        kordoc_parser.KordocStructureLimitError,
        match="NCS detail candidate group exceeds the safe limit",
    ):
        structure_job_description({"markdown": markdown}, filename="packed-details.md")


def test_long_non_candidate_evidence_line_does_not_trigger_candidate_budget() -> None:
    markdown = (
        "| NCS 세분류명 | 사무행정 |\n"
        "필요업무: "
        + ",".join(f"ordinary-duty-{index}" for index in range(300))
    )

    result = structure_job_description({"markdown": markdown}, filename="long-duty.md")

    assert result["fields"]["ncs_detail_candidates"] == ["사무행정"]
