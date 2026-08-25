from __future__ import annotations

import csv


from app.main import (
    NCS_SCLASS_CSV,
    _canonicalize_detail_lookup_terms,
    _find_sclass_code_tuple,
    _norm_detail_coverage_key,
    _parse_sclass_terms,
)
from app.services import jd_strategy
from app.services.jd_strategy import lookup_ncs_codes_by_sclass
from app.services.sclass_pipeline import extract_sclass_from_text
from app.services.sclass_pipeline import (
    _clean_pdf_detail_cell,
    _extract_detail_candidates_from_tables,
    _has_detail_table_header,
    _pdf_sclass_page_limit,
    _split_candidate_blob,
)


def test_detail_term_splitters_preserve_official_acronym_slash_names():
    assert _parse_sclass_terms("QM/QC관리, 총무/사무행정") == [
        "QM/QC관리",
        "총무",
        "사무행정",
    ]


def test_detail_term_splitter_preserves_delimiters_inside_official_parentheses():
    assert _parse_sclass_terms("조선비계(족장, 발판, scaffolding), 사무행정") == [
        "조선비계(족장, 발판, scaffolding)",
        "사무행정",
    ]
    assert _canonicalize_detail_lookup_terms(
        ["조선비계(족장, 발판, scaffolding)"]
    ) == ["조선비계(족장, 발판, scaffolding)"]


def test_pdf_sclass_page_limit_covers_multi_role_job_descriptions(monkeypatch):
    monkeypatch.delenv("PDF_SCLASS_MAX_PAGES", raising=False)
    assert _pdf_sclass_page_limit() == 24

    monkeypatch.setenv("PDF_SCLASS_MAX_PAGES", "2")
    assert _pdf_sclass_page_limit() == 6

    monkeypatch.setenv("PDF_SCLASS_MAX_PAGES", "100")
    assert _pdf_sclass_page_limit() == 40
    assert _split_candidate_blob("01.QM/QC관리; 02.총무/사무행정") == [
        ("QM/QC관리", False),
        ("총무", False),
        ("사무행정", False),
    ]


def test_lookup_sclass_space_equivalent(monkeypatch):
    rows = [
        {
            "NCS_CODE_NO": "200103",
            "NCS_LCLAS_CD": "20",
            "NCS_LCLAS_CDNM": "정보통신",
            "NCS_MCLAS_CD": "01",
            "NCS_MCLAS_CDNM": "정보기술",
            "NCS_SCLAS_CD": "03",
            "NCS_SCLAS_CDNM": "정보기술운영",
        }
    ]
    monkeypatch.setitem(jd_strategy.__dict__, "_ncs_sclass_rows_cache", rows)

    out = lookup_ncs_codes_by_sclass(["정보기술 운영"])
    assert len(out) == 1
    assert out[0]["ncs_code_no"] == "200103"
    assert out[0]["sclass_name"] == "정보기술운영"


def test_lookup_sclass_dot_equivalent(monkeypatch):
    rows = [
        {
            "NCS_CODE_NO": "110101",
            "NCS_LCLAS_CD": "11",
            "NCS_LCLAS_CDNM": "경비청소",
            "NCS_MCLAS_CD": "01",
            "NCS_MCLAS_CDNM": "경비",
            "NCS_SCLAS_CD": "01",
            "NCS_SCLAS_CDNM": "경비·경호",
        }
    ]
    monkeypatch.setitem(jd_strategy.__dict__, "_ncs_sclass_rows_cache", rows)

    out = lookup_ncs_codes_by_sclass(["경비경호"])
    assert len(out) == 1
    assert out[0]["ncs_code_no"] == "110101"
    assert out[0]["sclass_name"] == "경비·경호"


def test_lookup_sclass_query_dedup_by_normalized_key(monkeypatch):
    rows = [
        {
            "NCS_CODE_NO": "200103",
            "NCS_LCLAS_CD": "20",
            "NCS_LCLAS_CDNM": "정보통신",
            "NCS_MCLAS_CD": "01",
            "NCS_MCLAS_CDNM": "정보기술",
            "NCS_SCLAS_CD": "03",
            "NCS_SCLAS_CDNM": "정보기술운영",
        }
    ]
    monkeypatch.setitem(jd_strategy.__dict__, "_ncs_sclass_rows_cache", rows)

    out = lookup_ncs_codes_by_sclass(["정보기술 운영", "정보기술운영"])
    assert len(out) == 1
    assert out[0]["ncs_code_no"] == "200103"


def test_canonicalize_detail_lookup_terms_uses_official_spacing_only_for_exact_match(monkeypatch):
    rows = [
        {
            "NCS_CODE_NO": "010101",
            "NCS_LCLAS_CD": "01",
            "NCS_LCLAS_CDNM": "사업관리",
            "NCS_MCLAS_CD": "01",
            "NCS_MCLAS_CDNM": "사업관리",
            "NCS_SCLAS_CD": "01",
            "NCS_SCLAS_CDNM": "프로젝트관리",
        }
    ]
    monkeypatch.setitem(jd_strategy.__dict__, "_ncs_sclass_rows_cache", rows)

    equivalent_document_spellings = [
        "프로젝트 관리",
        "프로젝트-관리",
        "프로젝트·관리",
        "(프로젝트관리)",
        "01. 프로젝트 관리",
        "NCS 세분류명: 프로젝트 관리",
        "010101 프로젝트 관리",
        "(010101) 프로젝트 관리",
        "01-01-01 프로젝트 관리",
        "010101.프로젝트 관리",
        "NCS 세분류명: (01010101) 프로젝트 관리",
    ]
    for spelling in equivalent_document_spellings:
        assert _canonicalize_detail_lookup_terms([spelling]) == ["프로젝트관리"]
    assert _canonicalize_detail_lookup_terms(["프로젝트 운영"]) == ["프로젝트 운영"]


def test_canonicalize_detail_lookup_terms_rejects_code_only_and_preserves_other_syntax():
    assert _canonicalize_detail_lookup_terms(
        ["010101", "(02010101)", "01-01-01", "NCS 02010101"]
    ) == []
    assert _canonicalize_detail_lookup_terms(["010101 QM/QC관리"]) == ["QM/QC관리"]
    assert _canonicalize_detail_lookup_terms(["01. 미등록직무"]) == ["미등록직무"]
    assert _canonicalize_detail_lookup_terms(["010101 미등록직무"]) == ["미등록직무"]


def test_all_official_sclass_names_have_unique_normalized_keys_and_round_trip():
    with NCS_SCLASS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        official_names = {
            str(row.get("NCS_SCLAS_CDNM", "")).strip()
            for row in csv.DictReader(handle)
            if str(row.get("NCS_SCLAS_CDNM", "")).strip()
        }

    names_by_key: dict[str, set[str]] = {}
    for name in official_names:
        names_by_key.setdefault(_norm_detail_coverage_key(name), set()).add(name)

    collisions = {
        key: sorted(names)
        for key, names in names_by_key.items()
        if len(names) > 1
    }
    assert collisions == {}

    for name in sorted(official_names):
        transport_spelling = " ".join(name)
        assert _canonicalize_detail_lookup_terms([transport_spelling]) == [name]


def test_current_detail_catalog_surface_wins_over_legacy_sclass_spelling():
    assert _canonicalize_detail_lookup_terms(["문화·예술경영"]) == ["문화·예술경영"]


def test_all_282_official_code_and_name_pairs_round_trip_without_code_leakage():
    with NCS_SCLASS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        official_pairs = [
            (
                str(row.get("NCS_CODE_NO", "")).strip(),
                str(row.get("NCS_SCLAS_CDNM", "")).strip(),
            )
            for row in csv.DictReader(handle)
            if str(row.get("NCS_CODE_NO", "")).strip()
            and str(row.get("NCS_SCLAS_CDNM", "")).strip()
        ]

    assert len(official_pairs) == 282
    assert len(set(official_pairs)) == 282
    for code, name in official_pairs:
        assert _canonicalize_detail_lookup_terms([f"{code} {name}"]) == [name]
        assert _canonicalize_detail_lookup_terms([f"{code}{name}"]) == [name]


def test_find_sclass_code_tuple_space_equivalent(monkeypatch, tmp_path):
    csv_path = tmp_path / "ncs_sclass_codes_for_test.csv"
    csv_path.write_text(
        (
            "NCS_CODE_NO,NCS_LCLAS_CD,NCS_LCLAS_CDNM,NCS_MCLAS_CD,NCS_MCLAS_CDNM,NCS_SCLAS_CD,NCS_SCLAS_CDNM\n"
            "200103,20,정보통신,01,정보기술,03,정보기술운영\n"
        ),
        encoding="utf-8",
    )

    import app.main as main_mod

    monkeypatch.setattr(main_mod, "NCS_SCLASS_CSV", csv_path)
    out = _find_sclass_code_tuple("정보기술 운영")

    assert out is not None
    assert out["ncs_lclass_code"] == "20"
    assert out["ncs_mclass_code"] == "01"
    assert out["ncs_sclass_code"] == "03"


def test_reverse_dictionary_prose_hit_is_not_promoted_to_sclass(monkeypatch):
    """A middle-category/duty prose hit must remain review-only."""
    import app.services.sclass_pipeline as pipeline

    monkeypatch.setattr(
        pipeline,
        "extract_small_category",
        lambda *args, **kwargs: {
            "topk": [
                {
                    "label": "회계",
                    "score": 6,
                    "evidence": [
                        {
                            "method": "reverse_dict",
                            "snippet": "중분류 03.재무회계",
                        }
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(pipeline, "extract_small_categories_from_jd", lambda *args, **kwargs: [])

    text = chr(10).join(
        [
            "NCS 분류체계",
            "소분류",
            "01.프로젝트관리",
            "세분류",
            "01.프로젝트 전략기획",
            "직무수행내용",
            "회계",
        ]
    )
    result = extract_sclass_from_text(text)

    assert result["matched"] == ["프로젝트관리"]
    assert "회계" not in result["matched"]


def test_pdf_detail_table_reads_separate_detail_column_not_small_category():
    tables = [
        (
            1,
            [
                ["분류체계", "대분류", "중분류", "소분류", "세분류"],
                ["", "02.경영·회계·사무", "02.총무·인사", "02.인사·조직", "01.인사"],
            ],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["인사"]


def test_pdf_detail_cell_strips_only_complete_ncs_code_prefixes():
    cases = {
        "02020201 인사": ["인사"],
        "02020201인사": ["인사"],
        "02-02-02-01 인사": ["인사"],
        "(02020201)인사": ["인사"],
        "[02020302] 사무행정": ["사무행정"],
        "02020302사무행정": ["사무행정"],
        "01010101-01 인사": ["인사"],
        "01010101-01인사": ["인사"],
        "010101프로젝트관리": ["프로젝트관리"],
        "010101QM/QC관리": ["QM/QC관리"],
        "010101 3D프린터개발": ["3D프린터개발"],
        "1903113D프린터개발": ["3D프린터개발"],
        "02020201": [],
        "(02020201)": [],
        "02-02-02-01": [],
        "01. 인사": ["인사"],
        "3D프린터개발": ["3D프린터개발"],
        "CO₂배출관리": ["CO₂배출관리"],
        "2024채용관리": ["2024채용관리"],
        "123456비공식직무": ["123456비공식직무"],
    }

    for raw_value, expected in cases.items():
        assert _clean_pdf_detail_cell(raw_value) == expected


def test_pdf_detail_table_accepts_ncs_detail_header_variant():
    tables = [
        (
            1,
            [
                ["분류체계", "NCS 세분류명"],
                ["", "01.노무관리"],
            ],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["노무관리"]


def test_pdf_detail_table_scans_explicit_header_after_sixteen_metadata_rows():
    tables = [
        (
            7,
            [
                *[[f"메타데이터 {index}", "기관 정보"] for index in range(16)],
                ["분류체계", "NCS 세분류명"],
                ["", "01.사무행정"],
            ],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert _has_detail_table_header(tables) is True
    assert [row["label"] for row in rows] == ["사무행정"]
    assert rows[0]["page"] == 7


def test_pdf_detail_table_does_not_treat_detail_description_title_as_header():
    tables = [
        (
            1,
            [
                ["NCS 세분류 직무 설명"],
                ["직무명", "사무행정"],
            ],
        )
    ]

    assert _has_detail_table_header(tables) is False
    assert _extract_detail_candidates_from_tables(tables) == []


def test_pdf_detail_table_preserves_all_detail_cells_in_flattened_row():
    tables = [
        (
            1,
            [
                [
                    "채용분야",
                    "분류체계",
                    "세분류",
                    "02.프로젝트\n관리",
                    "03.산학협력\n관리",
                    "01.경영\n기획",
                    "02.경영\n평가",
                    "01.총무",
                    "01.인사",
                    "01. 비서\n(글로벌\n경영사무\n지원)",
                    "01.예산",
                ],
            ],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == [
        "프로젝트 관리",
        "산학협력 관리",
        "경영 기획",
        "경영 평가",
        "총무",
        "인사",
        "비서 (글로벌 경영사무 지원)",
        "예산",
    ]


def test_pdf_detail_table_filters_job_definition_header_noise():
    tables = [
        (
            1,
            [
                ["분류체계", "세분류", "직무정의", "01.경영기획", "02.사무행정"],
            ],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["경영기획", "사무행정"]


def test_pdf_detail_table_filters_alio_neighbor_cell_noise():
    tables = [
        (
            1,
            [[
                "분류체계",
                "세분류",
                "01.경영기획",
                "공단 소개",
                "공단 주요 사업",
                "NCS기반 채용전형 절차",
                "요구 능력 단위",
                "교육요건",
                "공고문 참조",
                "www.ncs.go.kr",
                "능력단위명칭",
                "기술 명 NCS 참고",
                "핵심책무",
            ]],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["경영기획"]


def test_pdf_detail_table_stops_flattened_row_at_next_section_header():
    tables = [
        (
            1,
            [[
                "분류체계",
                "세분류",
                "경영기획",
                "능력단위명칭",
                "사업환경 분석",
                "경영방침 수립",
            ]],
        )
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["경영기획"]


def test_pdf_detail_table_does_not_promote_no_mapping_placeholder():
    tables = [
        (
            1,
            [
                ["분류체계", "대분류", "중분류", "소분류", "세분류"],
                ["", "05.법률", "01.법률", "01.법무", "해당사항 없음"],
            ],
        )
    ]

    assert _extract_detail_candidates_from_tables(tables) == []


def test_pdf_detail_table_collects_detail_rows_across_role_tables():
    tables = [
        (
            1,
            [
                ["분류체계", "대분류", "중분류", "소분류", "세분류"],
                ["", "05.법률", "01.법률", "01.법무", "해당사항 없음"],
            ],
        ),
        (
            2,
            [
                ["분류체계", "대분류", "중분류", "소분류", "세분류"],
                ["", "02.경영·회계·사무", "02.총무·인사", "02.인사·조직", "01.노무관리"],
            ],
        ),
    ]

    rows = _extract_detail_candidates_from_tables(tables)

    assert [row["label"] for row in rows] == ["노무관리"]
