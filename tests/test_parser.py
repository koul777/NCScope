"""Tests for app.services.parser module."""

from app.services.parser import split_sentences, extract_requirements


class TestSplitSentences:
    """Test sentence splitting functionality."""

    def test_split_sentences_basic(self):
        """Test basic sentence splitting on periods."""
        text = "첫 번째 문장. 두 번째 문장. 세 번째 문장."
        result = split_sentences(text)
        assert len(result) == 3
        assert result[0] == "첫 번째 문장"
        assert result[1] == "두 번째 문장"
        assert result[2] == "세 번째 문장"

    def test_split_sentences_with_newlines(self):
        """Test splitting with newlines."""
        text = "첫 번째 문장\n두 번째 문장\n세 번째 문장"
        result = split_sentences(text)
        assert len(result) == 3
        assert all("문장" in s for s in result)

    def test_split_sentences_with_various_delimiters(self):
        """Test splitting with various delimiters."""
        text = "첫 번째! 두 번째? 세 번째; 네 번째."
        result = split_sentences(text)
        assert len(result) == 4

    def test_split_sentences_empty_string(self):
        """Test with empty string."""
        result = split_sentences("")
        assert result == []

    def test_split_sentences_whitespace_only(self):
        """Test with whitespace only."""
        result = split_sentences("   \n\n  ")
        assert result == []

    def test_split_sentences_strips_whitespace(self):
        """Test that result sentences are stripped."""
        text = "  첫 번째  .  두 번째  ."
        result = split_sentences(text)
        assert result[0] == "첫 번째"
        assert result[1] == "두 번째"


class TestExtractRequirements:
    """Test requirement extraction functionality."""

    def test_extract_requirements_basic(self):
        """Test basic requirement extraction."""
        text = "필수 경험: 사무행정 업무. 우대 사항: 회계 경험."
        result = extract_requirements(text, "test")
        assert len(result) <= 30
        assert all(isinstance(req, dict) for req in result)
        assert all("item" in req and "source" in req and "weight" in req for req in result)

    def test_extract_requirements_keyword_scoring(self):
        """Test that requirements with KEY_MARKERS get higher scores."""
        text_with_marker = "필수 사항: 장시간 업무 담당 경험."
        text_without_marker = "일반 문장입니다."

        result_with = extract_requirements(text_with_marker, "test")
        result_without = extract_requirements(text_without_marker, "test")

        assert result_with[0]["weight"] > result_without[0]["weight"]

    def test_extract_requirements_length_scoring(self):
        """Test that longer sentences may get higher scores."""
        short_text = "짧은 문장"
        long_text = "아주 긴 문장으로 작성된 요구사항 항목입니다. 여러 세부사항이 포함되어 있습니다. 추가 정보도 있습니다."

        result_short = extract_requirements(short_text, "test")
        result_long = extract_requirements(long_text, "test")

        # Both have base score of 0.2, only long text (>35 chars) gets +0.2
        if result_short and result_long:
            assert result_long[0]["weight"] >= result_short[0]["weight"]

    def test_extract_requirements_max_items(self):
        """Test that result is limited to 30 items."""
        text = ". ".join([f"요구사항 {i}" for i in range(50)])
        result = extract_requirements(text, "test")
        assert len(result) <= 30

    def test_extract_requirements_text_truncation(self):
        """Test that each requirement is truncated to 180 chars."""
        long_text = "a" * 200
        result = extract_requirements(long_text, "test")
        assert all(len(req["item"]) <= 180 for req in result)

    def test_extract_requirements_sorted_by_weight(self):
        """Test that results are sorted by weight (descending)."""
        text = "일반 문장. 필수 요구사항 아주 긴 설명이 있습니다. 우대 사항입니다."
        result = extract_requirements(text, "test")
        # Weights should be in descending order
        for i in range(len(result) - 1):
            assert result[i]["weight"] >= result[i + 1]["weight"]

    def test_extract_requirements_source(self):
        """Test that source field is correctly set."""
        result = extract_requirements("테스트 문장", "custom_source")
        assert all(req["source"] == "custom_source" for req in result)

    def test_extract_requirements_weight_bounds(self):
        """Test that weights are between 0.2 and 1.0."""
        text = "짧은. 필수 요구사항으로 매우 긴 문장을 작성했습니다."
        result = extract_requirements(text, "test")
        assert all(0.2 <= req["weight"] <= 1.0 for req in result)
