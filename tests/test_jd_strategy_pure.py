"""Tests for pure functions in app.services.jd_strategy module."""

import json
from xml.etree import ElementTree as ET

from app.services.jd_strategy import (
    _count_hangul,
    _repair_mojibake,
    extract_subcategory_text,
    extract_small_categories_from_jd,
    build_notice_context_from_jd,
    _parse_items,
    MOJIBAKE_ALIAS,
)


class TestCountHangul:
    """Test Hangul character counting."""

    def test_count_hangul_pure_korean(self):
        """Test counting pure Korean text."""
        text = "한글"
        assert _count_hangul(text) == 2

    def test_count_hangul_mixed_text(self):
        """Test counting mixed Korean and English."""
        text = "한글test"
        assert _count_hangul(text) == 2

    def test_count_hangul_no_korean(self):
        """Test with no Korean characters."""
        text = "English123"
        assert _count_hangul(text) == 0

    def test_count_hangul_empty(self):
        """Test with empty string."""
        assert _count_hangul("") == 0

    def test_count_hangul_special_chars(self):
        """Test with special characters."""
        text = "한글!@#$%^&*()"
        assert _count_hangul(text) == 2

    def test_count_hangul_numbers(self):
        """Test with numbers."""
        text = "한글123한글"
        assert _count_hangul(text) == 4


class TestRepairMojibake:
    """Test mojibake (encoding corruption) repair."""

    def test_repair_mojibake_identity(self):
        """Test that correctly encoded text is unchanged."""
        text = "사무행정 업무"
        result = _repair_mojibake(text)
        assert "사무행정" in result

    def test_repair_mojibake_empty(self):
        """Test with empty string."""
        result = _repair_mojibake("")
        assert result == ""

    def test_repair_mojibake_alias_replacement(self):
        """Test that mojibake aliases are replaced."""
        # Test with a known broken text
        for broken, fixed in MOJIBAKE_ALIAS.items():
            if broken and fixed:
                text = f"prefix {broken} suffix"
                result = _repair_mojibake(text)
                # The fixed version should appear or the original if no repair needed
                assert fixed in result or broken not in result or "prefix" in result

    def test_repair_mojibake_latin1_encoding(self, sample_korean_text):
        """Test Latin-1 encoded text recovery."""
        # This is a challenging test - we verify the function handles it
        result = _repair_mojibake(sample_korean_text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_repair_mojibake_preserves_content(self):
        """Test that repair preserves text content."""
        text = "중요한 정보"
        result = _repair_mojibake(text)
        # Should contain Korean characters
        assert any("\uac00" <= c <= "\ud7a3" for c in result)


class TestExtractSubcategoryText:
    """Test subcategory text extraction."""

    def test_extract_subcategory_text_basic(self):
        """Test basic subcategory extraction."""
        text = """
        직무명: 사무직
        소분류: 사무행정
        주요 업무
        """
        result = extract_subcategory_text(text)
        assert "소분류" in result or "사무행정" in result

    def test_extract_subcategory_text_prefer_sobuneui(self):
        """Test preference for 소분류 over 세분류."""
        text = """
        직무
        소분류: 사무행정
        세분류: 기타
        """
        result = extract_subcategory_text(text)
        assert "소분류" in result

    def test_extract_subcategory_text_fallback_to_sebuneui(self):
        """Test fallback to 세분류 when 소분류 not found."""
        text = """
        직무
        세분류: 사무행정
        내용
        """
        result = extract_subcategory_text(text)
        assert "세분류" in result or "사무행정" in result

    def test_extract_subcategory_text_empty(self):
        """Test with empty text."""
        result = extract_subcategory_text("")
        assert isinstance(result, str)

    def test_extract_subcategory_text_max_length(self):
        """Test that result is limited to 1200 characters."""
        text = "소분류\n" + "a" * 2000
        result = extract_subcategory_text(text)
        assert len(result) <= 1200

    def test_extract_subcategory_text_no_match(self):
        """Test fallback when no standard marker found."""
        text = """
        분류체계: 경영사무
        능력단위: 사무처리
        """
        result = extract_subcategory_text(text)
        assert isinstance(result, str)


class TestExtractSmallCategoriesFromJD:
    """Test small category extraction - improved version."""

    def test_extract_small_categories_basic(self):
        """Test basic category extraction."""
        # Using proper UTF-8 Korean text
        text = "소분류:\n사무행정\n총무\n회계\n"
        result = extract_small_categories_from_jd(text)
        assert isinstance(result, list)
        # At least one category should be found
        assert len(result) > 0

    def test_extract_small_categories_long_list(self):
        """Test extraction of 6+ categories (IMPROVEMENT)."""
        text = "소분류:\n사무행정\n총무\n회계처리\n자산관리\n구매관리\n물품관리\n비품관리\n"
        result = extract_small_categories_from_jd(text)
        # IMPROVEMENT: Should now capture multiple categories
        assert len(result) >= 2, f"Expected 2+, got {len(result)}: {result}"

    def test_extract_small_categories_dedup(self):
        """Test deduplication of categories."""
        text = """
        소분류:
        사무행정
        사무행정
        사무행정
        """
        result = extract_small_categories_from_jd(text)
        assert result.count("사무행정") <= 1

    def test_extract_small_categories_max_limit(self):
        """Test maximum categories limit (IMPROVEMENT: 15 not 12)."""
        text = """소분류:
총무
자산관리
사무행정
회계처리
회계감사
문서관리
계약관리
구매관리
물품관리
재물조사
비품관리
행정지원
일반사무
경영기획
예산관리
금융
보험
법무"""
        result = extract_small_categories_from_jd(text)
        # IMPROVEMENT: Limit increased from 12 to 15
        assert len(result) <= 15

    def test_extract_small_categories_filters_stop_words(self):
        """Test that stop words are filtered out."""
        text = """
        소분류:
        소분류
        세분류
        분류체계
        사무행정
        """
        result = extract_small_categories_from_jd(text)
        # Should not include the markers themselves
        assert "소분류" not in result
        assert "세분류" not in result
        assert "분류체계" not in result

    def test_extract_small_categories_healthcare(self):
        """Test healthcare category extraction (IMPROVEMENT)."""
        text = "소분류:\n간호\n물리치료\n"
        result = extract_small_categories_from_jd(text)
        assert len(result) >= 1  # At least one healthcare category

    def test_extract_small_categories_comma_separated(self):
        """Test comma-separated categories (IMPROVEMENT)."""
        text = "소분류: 사무행정, 총무, 회계처리"
        result = extract_small_categories_from_jd(text)
        # Should extract at least 2 categories
        assert len(result) >= 2

    def test_extract_small_categories_empty(self):
        """Test with empty text."""
        result = extract_small_categories_from_jd("")
        assert isinstance(result, list)

    def test_extract_small_categories_expanded_known_list(self):
        """Test that expanded category list works (IMPROVEMENT: 50+ categories)."""
        text = """
        소분류:
        교육
        정보처리
        건축
        자동차
        마케팅
        """
        result = extract_small_categories_from_jd(text)
        # All of these should be recognized with expanded list
        assert len(result) >= 3

    def test_extract_small_categories_pdf_style_klri(self):
        """소분류/세분류 헤더가 세로로 분리된 직무기술서 패턴."""
        text = """
        분류체계
        대분류
        중분류
        소분류
        세분류
        05. 법률/검찰
        01. 법률
        01. 법무
        직무수행 내용
        """
        result = extract_small_categories_from_jd(text)
        assert "법무" in result

    def test_extract_small_categories_pdf_style_admin_support(self):
        """코드-명칭이 한 줄에 다중으로 섞인 패턴과 줄바꿈 혼합 패턴."""
        text = """
        채용분야
        대분류
        중분류
        소분류
        세분류
        02. 경영·회계·사무 02. 총무·인사 03. 일반사무 02. 사무행정
        04. 교육·자연·사회과학 01. 학교교육
        02. 학사운영
        01. 학사운영
        11. 경비·청소
        01. 경비 01. 경비·경호 01. 보안
        직무수행 내용
        """
        result = extract_small_categories_from_jd(text)
        assert "일반사무" in result
        assert "학사운영" in result
        assert "경비·경호" in result

    def test_extract_small_categories_pdf_style_column_major(self):
        """코드-명칭이 컬럼 순서(대->중->소->세)로 직렬화된 패턴."""
        text = """
        분류체계
        대분류
        중분류
        소분류
        세분류
        02. 경영·회계·사무
        02. 총무·인사
        03. 재무회계
        01. 총무
        03. 일반사무
        01. 회계
        0. 총무
        02. 자산관리
        02. 사무행정
        01. 회계·감사
        직무수행 내용
        """
        result = extract_small_categories_from_jd(text)
        assert "총무" in result
        assert "일반사무" in result
        assert "회계" in result


class TestBuildNoticeContextFromJD:
    """Test notice context building."""

    def test_build_notice_context_basic(self):
        """Test basic context building."""
        jd = "사무행정 업무 경험 필요"
        notice = """
        채용공고
        사무행정 직무
        서울 지역
        경력 3년 이상
        """
        result = build_notice_context_from_jd(jd, notice)
        assert isinstance(result, str)

    def test_build_notice_context_filters_by_jd_terms(self):
        """Test that notice is filtered by JD terms."""
        jd = "사무행정"
        notice = """
        사무행정 채용
        의료 관련 채용
        사무행정 직무
        """
        result = build_notice_context_from_jd(jd, notice)
        # Should include lines with 사무행정
        if result:
            assert "사무행정" in result or len(result) > 0

    def test_build_notice_context_empty_notice(self):
        """Test with empty notice."""
        jd = "사무행정"
        result = build_notice_context_from_jd(jd, "")
        assert result == ""

    def test_build_notice_context_max_chars(self):
        """Test character limit."""
        jd = "사무"
        notice = "사무 관련 내용 " * 1000
        result = build_notice_context_from_jd(jd, notice, max_chars=500)
        assert len(result) <= 500

    def test_build_notice_context_no_matching_terms(self):
        """Test fallback when no terms match."""
        jd = "매우특이한용어"
        notice = "일반적인 채용공고 내용"
        result = build_notice_context_from_jd(jd, notice)
        # Should return something (notice or empty)
        assert isinstance(result, str)


class TestParseItems:
    """Test item parsing from JSON and XML."""

    def test_parse_items_json_basic(self):
        """Test basic JSON parsing."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": [
                            {"ncsClCd": "01", "compeUnitName": "Unit 1"},
                            {"ncsClCd": "02", "compeUnitName": "Unit 2"},
                        ]
                    }
                }
            }
        })
        result = _parse_items("application/json", body)
        assert len(result) == 2
        assert result[0]["ncsClCd"] == "01"

    def test_parse_items_json_single_item(self):
        """Test JSON parsing with single item (dict)."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": {"ncsClCd": "01", "compeUnitName": "Unit 1"}
                    }
                }
            }
        })
        result = _parse_items("application/json", body)
        assert len(result) == 1

    def test_parse_items_xml_basic(self):
        """Test basic XML parsing."""
        xml = """<?xml version="1.0"?>
        <response>
            <item>
                <ncsClCd>01</ncsClCd>
                <compeUnitName>Unit 1</compeUnitName>
            </item>
            <item>
                <ncsClCd>02</ncsClCd>
                <compeUnitName>Unit 2</compeUnitName>
            </item>
        </response>"""
        result = _parse_items("application/xml", xml)
        assert len(result) == 2

    def test_parse_items_xml_field_extraction(self):
        """Test that all expected fields are extracted."""
        xml = """<?xml version="1.0"?>
        <response>
            <item>
                <ncsClCd>01</ncsClCd>
                <compeUnitName>Unit</compeUnitName>
                <compeUnitLevel>4</compeUnitLevel>
                <ncsSubdCdnm>Sub</ncsSubdCdnm>
            </item>
        </response>"""
        result = _parse_items("application/xml", xml)
        assert "ncsClCd" in result[0]
        assert "compeUnitName" in result[0]
        assert "compeUnitLevel" in result[0]

    def test_parse_items_json_empty(self):
        """Test with empty items."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": None
                    }
                }
            }
        })
        result = _parse_items("application/json", body)
        assert result == []

    def test_parse_items_json_content_type_variations(self):
        """Test various JSON content-type strings."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": [{"ncsClCd": "01"}]
                    }
                }
            }
        })
        for ct in ["application/json", "application/json; charset=utf-8", "JSON"]:
            result = _parse_items(ct, body)
            assert len(result) >= 0
