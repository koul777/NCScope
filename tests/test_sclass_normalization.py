from __future__ import annotations

from pathlib import Path

from app.main import _find_sclass_code_tuple
from app.services import jd_strategy
from app.services.jd_strategy import lookup_ncs_codes_by_sclass


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
