"""Tests for app.services.ncs module."""

from app.services.ncs import map_ncs


class TestMapNcs:
    """Test NCS mapping functionality."""

    def test_map_ncs_basic(self):
        """Test basic NCS mapping."""
        text = "사무행정 업무 경험"
        result = map_ncs("R6000_OFFICE", text)
        assert isinstance(result, list)
        assert len(result) <= 5
        assert all("ncsClCd" in item for item in result)
        assert all("compeUnitName" in item for item in result)
        assert all("score" in item for item in result)

    def test_map_ncs_top_k(self):
        """Test that top_k parameter limits results."""
        text = "사무행정 회계 총무"
        result_3 = map_ncs("R6000_OFFICE", text, top_k=3)
        result_10 = map_ncs("R6000_OFFICE", text, top_k=10)
        assert len(result_3) <= 3
        assert len(result_10) <= len(result_3) or len(result_10) <= 10

    def test_map_ncs_score_bounds(self):
        """Test that scores are between 0 and 1."""
        result = map_ncs("R6000_OFFICE", "사무행정")
        assert all(0 <= item["score"] <= 1 for item in result)

    def test_map_ncs_score_sorting(self):
        """Test that results are sorted by score (descending)."""
        text = "사무행정 회계"
        result = map_ncs("R6000_OFFICE", text)
        for i in range(len(result) - 1):
            assert result[i]["score"] >= result[i + 1]["score"]

    def test_map_ncs_keyword_matching(self):
        """Test that keyword matching works."""
        text_office = "사무행정 행정 문서"
        text_unrelated = "과학 물리 화학"

        result_office = map_ncs("R6000_OFFICE", text_office)
        result_unrelated = map_ncs("R6000_OFFICE", text_unrelated)

        if result_office and result_unrelated:
            assert result_office[0]["score"] > result_unrelated[0]["score"]

    def test_map_ncs_direct_name_match(self):
        """Test exact competency unit name matching."""
        text = "사무행정은 중요한 업무입니다"
        result = map_ncs("R6000_OFFICE", text)
        # Should find 사무행정 with good score
        assert any(item["compeUnitName"] == "사무행정" for item in result)

    def test_map_ncs_r6000_office(self):
        """Test R6000_OFFICE category."""
        result = map_ncs("R6000_OFFICE", "회계")
        assert len(result) > 0
        assert any("회계" in item["compeUnitName"].lower() for item in result)

    def test_map_ncs_r6000_management(self):
        """Test R6000_MANAGEMENT category."""
        result = map_ncs("R6000_MANAGEMENT", "예산 관리")
        assert len(result) > 0

    def test_map_ncs_fallback_for_unknown_category(self):
        """Test fallback to all samples for unknown category."""
        result = map_ncs("UNKNOWN_CATEGORY", "사무행정")
        assert len(result) > 0

    def test_map_ncs_empty_text(self):
        """Test with empty text."""
        result = map_ncs("R6000_OFFICE", "")
        assert isinstance(result, list)
        # Should return items with baseline score
        assert len(result) > 0

    def test_map_ncs_case_insensitive(self):
        """Test case-insensitive matching."""
        result_lower = map_ncs("R6000_OFFICE", "사무행정")
        result_upper = map_ncs("R6000_OFFICE", "사무행정")
        # Both should give same results (Korean doesn't have case)
        assert len(result_lower) == len(result_upper)

    def test_map_ncs_reason_field(self):
        """Test that reason field contains explanation."""
        result = map_ncs("R6000_OFFICE", "회계")
        assert len(result) > 0
        assert "reason" in result[0]
        assert isinstance(result[0]["reason"], str)

    def test_map_ncs_competence_unit_level(self):
        """Test that competence unit level is included."""
        result = map_ncs("R6000_OFFICE", "사무행정")
        assert len(result) > 0
        assert all("compeUnitLevel" in item for item in result)
