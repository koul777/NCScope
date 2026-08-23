from __future__ import annotations


from app.main import _find_sclass_code_tuple
from app.services import jd_strategy
from app.services.jd_strategy import lookup_ncs_codes_by_sclass
from app.services.sclass_pipeline import extract_sclass_from_text


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
