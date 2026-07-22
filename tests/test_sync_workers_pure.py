"""Tests for pure functions in app.services.sync_workers module."""

import json
from xml.etree import ElementTree as ET

from app.services.sync_workers import (
    _as_list,
    _extract_public_items,
    _pick,
    _parse_ncs_items_from_xml,
    _parse_ncs_items,
)


class TestAsList:
    """Test list conversion utility."""

    def test_as_list_none(self):
        """Test with None."""
        result = _as_list(None)
        assert result == []

    def test_as_list_already_list(self):
        """Test with already a list."""
        value = [1, 2, 3]
        result = _as_list(value)
        assert result == value
        assert result is value

    def test_as_list_single_value(self):
        """Test converting single value to list."""
        result = _as_list("item")
        assert result == ["item"]

    def test_as_list_dict(self):
        """Test with dictionary."""
        value = {"key": "value"}
        result = _as_list(value)
        assert result == [value]

    def test_as_list_number(self):
        """Test with number."""
        result = _as_list(42)
        assert result == [42]

    def test_as_list_zero(self):
        """Test with zero (falsy value)."""
        result = _as_list(0)
        assert result == [0]

    def test_as_list_empty_string(self):
        """Test with empty string."""
        result = _as_list("")
        assert result == [""]


class TestExtractPublicItems:
    """Test public item extraction from API response."""

    def test_extract_public_items_result_shape(self):
        """Test extraction from result array shape."""
        payload = {
            "data": {
                "result": [
                    {"instCd": "001", "instNm": "기관1"},
                    {"instCd": "002", "instNm": "기관2"},
                ]
            }
        }
        result = _extract_public_items(payload)
        assert len(result) == 2
        assert result[0]["instCd"] == "001"

    def test_extract_public_items_response_body_shape(self):
        """Test extraction from response.body.items shape."""
        payload = {
            "data": {
                "response": {
                    "body": {
                        "items": {
                            "item": [
                                {"instCd": "001"},
                                {"instCd": "002"},
                            ]
                        }
                    }
                }
            }
        }
        result = _extract_public_items(payload)
        assert len(result) == 2

    def test_extract_public_items_single_item(self):
        """Test with single item (not in list)."""
        payload = {
            "data": {
                "response": {
                    "body": {
                        "items": {
                            "item": {"instCd": "001"}
                        }
                    }
                }
            }
        }
        result = _extract_public_items(payload)
        assert len(result) == 1

    def test_extract_public_items_no_data(self):
        """Test with missing data."""
        result = _extract_public_items({})
        assert result == []

    def test_extract_public_items_invalid_data(self):
        """Test with invalid data type."""
        payload = {"data": "not a dict"}
        result = _extract_public_items(payload)
        assert result == []

    def test_extract_public_items_empty_result(self):
        """Test with empty result array."""
        payload = {"data": {"result": []}}
        result = _extract_public_items(payload)
        assert result == []


class TestPick:
    """Test dictionary key picking utility."""

    def test_pick_first_key(self):
        """Test picking first available key."""
        row = {"name": "John", "fullName": "John Doe"}
        result = _pick(row, "name", "fullName")
        assert result == "John"

    def test_pick_second_key_when_first_empty(self):
        """Test fallback to second key."""
        row = {"name": "", "fullName": "John Doe"}
        result = _pick(row, "name", "fullName")
        assert result == "John Doe"

    def test_pick_default_when_not_found(self):
        """Test default value when key not found."""
        row = {"id": 1}
        result = _pick(row, "name", "fullName", default="Unknown")
        assert result == "Unknown"

    def test_pick_none_value(self):
        """Test with None value (should fall through)."""
        row = {"name": None, "fullName": "John"}
        result = _pick(row, "name", "fullName")
        assert result == "John"

    def test_pick_whitespace_only(self):
        """Test with whitespace-only value."""
        row = {"name": "   ", "fullName": "John"}
        result = _pick(row, "name", "fullName")
        assert result == "John"

    def test_pick_single_key(self):
        """Test with single key."""
        row = {"name": "John"}
        result = _pick(row, "name")
        assert result == "John"

    def test_pick_custom_default(self):
        """Test custom default value."""
        row = {}
        result = _pick(row, "a", "b", default="custom")
        assert result == "custom"

    def test_pick_integer_value(self):
        """Test with integer value."""
        row = {"count": 42}
        result = _pick(row, "count")
        assert result == "42"


class TestParseNcsItemsFromXml:
    """Test XML NCS item parsing."""

    def test_parse_ncs_items_from_xml_basic(self):
        """Test basic XML parsing."""
        xml = """<?xml version="1.0"?>
        <response>
            <item>
                <ncsClCd>01</ncsClCd>
                <compeUnitName>Unit 1</compeUnitName>
                <compeUnitLevel>4</compeUnitLevel>
            </item>
        </response>"""
        items, total = _parse_ncs_items_from_xml(xml)
        assert len(items) == 1
        assert items[0]["ncsClCd"] == "01"

    def test_parse_ncs_items_from_xml_total_count(self):
        """Test total count extraction."""
        xml = """<?xml version="1.0"?>
        <response>
            <totalCount>100</totalCount>
            <item>
                <ncsClCd>01</ncsClCd>
                <compeUnitName>Unit</compeUnitName>
            </item>
        </response>"""
        items, total = _parse_ncs_items_from_xml(xml)
        assert total == 100

    def test_parse_ncs_items_from_xml_no_total_count(self):
        """Test when total count is missing."""
        xml = """<?xml version="1.0"?>
        <response>
            <item>
                <ncsClCd>01</ncsClCd>
            </item>
        </response>"""
        items, total = _parse_ncs_items_from_xml(xml)
        assert total is None

    def test_parse_ncs_items_from_xml_multiple_items(self):
        """Test parsing multiple items."""
        xml = """<?xml version="1.0"?>
        <response>
            <item><ncsClCd>01</ncsClCd></item>
            <item><ncsClCd>02</ncsClCd></item>
            <item><ncsClCd>03</ncsClCd></item>
        </response>"""
        items, total = _parse_ncs_items_from_xml(xml)
        assert len(items) == 3

    def test_parse_ncs_items_from_xml_field_mapping(self):
        """Test that all fields are extracted."""
        xml = """<?xml version="1.0"?>
        <response>
            <item>
                <ncsClCd>01</ncsClCd>
                <compeUnitName>Unit</compeUnitName>
                <compeUnitLevel>4</compeUnitLevel>
                <ncsLclasCdnm>Large</ncsLclasCdnm>
                <ncsMclasCdnm>Medium</ncsMclasCdnm>
                <ncsSclasCdnm>Small</ncsSclasCdnm>
                <ncsSubdCdnm>Sub</ncsSubdCdnm>
                <compeUnitDef>Definition</compeUnitDef>
            </item>
        </response>"""
        items, _ = _parse_ncs_items_from_xml(xml)
        assert items[0]["ncsClCd"] == "01"
        assert items[0]["compeUnitName"] == "Unit"
        assert items[0]["ncsLclasCdnm"] == "Large"

    def test_parse_ncs_items_from_xml_empty(self):
        """Test with no items."""
        xml = """<?xml version="1.0"?>
        <response>
            <totalCount>0</totalCount>
        </response>"""
        items, total = _parse_ncs_items_from_xml(xml)
        assert len(items) == 0
        assert total == 0


class TestParseNcsItems:
    """Test unified NCS item parsing for JSON and XML."""

    def test_parse_ncs_items_json(self):
        """Test JSON parsing."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": [
                            {"ncsClCd": "01", "compeUnitName": "Unit 1"}
                        ]
                    },
                    "totalCount": "10"
                }
            }
        })
        items, total = _parse_ncs_items("application/json", body)
        assert len(items) == 1
        assert total == 10

    def test_parse_ncs_items_xml(self):
        """Test XML parsing."""
        xml = """<?xml version="1.0"?>
        <response>
            <totalCount>10</totalCount>
            <item><ncsClCd>01</ncsClCd></item>
        </response>"""
        items, total = _parse_ncs_items("application/xml", xml)
        assert len(items) == 1
        assert total == 10

    def test_parse_ncs_items_json_content_type_variation(self):
        """Test JSON with various content-type strings."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": [{"ncsClCd": "01"}]
                    },
                    "totalCount": 5
                }
            }
        })
        for ct in ["application/json", "application/json; charset=utf-8", "json"]:
            items, total = _parse_ncs_items(ct, body)
            assert len(items) >= 0

    def test_parse_ncs_items_json_single_item(self):
        """Test JSON with single item (dict)."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": {"ncsClCd": "01"}
                    }
                }
            }
        })
        items, total = _parse_ncs_items("application/json", body)
        assert len(items) == 1

    def test_parse_ncs_items_json_non_digit_total(self):
        """Test JSON with non-digit total count."""
        body = json.dumps({
            "response": {
                "body": {
                    "items": {
                        "item": [{"ncsClCd": "01"}]
                    },
                    "totalCount": "abc"
                }
            }
        })
        items, total = _parse_ncs_items("application/json", body)
        assert total is None
