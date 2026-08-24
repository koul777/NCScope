from __future__ import annotations

import io
import struct
import zlib
import zipfile

import pytest

from app.services.hwp_text_fallback import (
    HwpTextExtractionError,
    _bounded_raw_deflate,
    _hwp_record_paragraphs,
    extract_hwpx_text,
    extract_linear_ncs_classification_terms,
)


def test_hwp_paragraph_record_reader_removes_inline_control_payload() -> None:
    text = "총무" + chr(1) + "123456" + chr(1) + " 문서관리"
    payload = text.encode("utf-16le")
    header = (len(payload) << 20) | 0x43

    assert _hwp_record_paragraphs(struct.pack("<I", header) + payload) == ["총무 문서관리"]


def test_hwp_raw_deflate_reader_rejects_expansion_over_limit() -> None:
    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed = compressor.compress(b"A" * 2048) + compressor.flush()

    with pytest.raises(HwpTextExtractionError, match="decompression limit"):
        _bounded_raw_deflate(compressed, limit=128)


def test_hwpx_reader_preserves_paragraph_boundaries() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "Contents/section0.xml",
            """<hs:sec xmlns:hs="urn:sec" xmlns:hp="urn:para">
            <hp:p><hp:run><hp:t>세분류</hp:t></hp:run></hp:p>
            <hp:p><hp:run><hp:t>사무행정</hp:t></hp:run></hp:p>
            </hs:sec>""",
        )

    assert extract_hwpx_text(stream.getvalue()) == "세분류\n사무행정"


def test_hwpx_reader_rejects_entity_declarations() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "Contents/section0.xml",
            "<!DOCTYPE x [<!ENTITY boom 'unsafe'>]><x><p><t>&boom;</t></p></x>",
        )

    with pytest.raises(HwpTextExtractionError, match="declarations"):
        extract_hwpx_text(stream.getvalue())


def test_linear_hwp_classification_terms_require_explicit_block_and_exclude_upper_levels() -> None:
    text = """
    분류체계
    대분류
    중분류
    소분류
    세분류(직무)
    02. 경영·회계·사무
    02. 총무·인사
    01. 총무
    01. 총무
    03. 일반사무
    02. 사무행정
    주요사업
    01. 문서작성
    """

    assert extract_linear_ncs_classification_terms(
        text,
        excluded_hierarchy_names=["경영·회계·사무", "총무·인사"],
    ) == ["총무", "일반사무", "사무행정"]


def test_linear_hwp_classification_terms_handle_abbreviated_headers_and_plain_cells() -> None:
    text = """
    분류체계
    대
    02.경영·회계·사무
    중
    01.기획사무
    소
    02.홍보·광고
    세
    PR/광고
    사무행정
    능력단위
    문서작성
    """

    assert extract_linear_ncs_classification_terms(
        text,
        excluded_hierarchy_names=["경영·회계·사무", "기획사무"],
    ) == ["PR", "광고", "사무행정"]


def test_linear_hwp_classification_terms_join_numbered_cells_split_over_paragraphs() -> None:
    text = """
    분류체계
    대분류
    중분류
    소분류
    세분류
    01.
    총무
    02.
    자산
    관리
    01.
    인사
    직무수행내용
    예산을 편성한다
    """

    assert extract_linear_ncs_classification_terms(text) == ["총무", "자산 관리", "인사"]


def test_linear_hwp_classification_terms_keep_context_across_wide_hierarchy_table() -> None:
    text = """
    대분류
    02. 경영·회계·사무
    중분류
    01. 기획사무
    02. 총무·인사
    03. 재무·회계
    소분류
    01. 경영기획
    02. 홍보·광고
    01. 총무
    02. 인사·조직
    03. 일반사무
    01.
    재무
    02.
    회계
    세분류
    01.경영기획
    01.
    총무
    02.
    자산
    관리
    01.
    인사
    02.
    노무관리
    01.
    비서
    02.
    사무행정
    01.
    예산
    01.
    회계·감사
    02.세무
    기관 주요사업
    """

    assert extract_linear_ncs_classification_terms(
        text,
        excluded_hierarchy_names=["경영·회계·사무", "기획사무", "총무·인사", "재무·회계"],
    ) == ["경영기획", "총무", "자산 관리", "인사", "노무관리", "비서", "사무행정", "예산", "회계·감사", "세무"]


def test_linear_hwp_classification_terms_do_not_scan_plain_duty_prose() -> None:
    text = """
    주요업무
    PR/광고 및 사무행정 업무를 수행합니다.
    자산
    관리
    """

    assert extract_linear_ncs_classification_terms(text) == []


def test_linear_hwp_classification_terms_stop_at_split_section_heading() -> None:
    text = """
    대분류
    중분류
    소분류
    세분류
    사회복지정책
    정책연구
    직무
    수행
    내용
    조사 통계 및 빅데이터분석 업무를 수행합니다.
    """

    assert extract_linear_ncs_classification_terms(text) == ["사회복지정책", "정책연구"]
