import json

import pytest


def test_extraction_fixture_is_valid_json(extraction_response_json):
    assert isinstance(extraction_response_json, list)
    assert len(extraction_response_json) > 0
    for item in extraction_response_json:
        assert "quantity" in item
        assert "unit_price" in item
        assert "currency" in item
        assert "description" in item
        assert "category" in item


def test_reasoning_fixture_is_valid_json(reasoning_response_json):
    required_keys = {"min_price", "max_price", "midpoint", "significant_deviation", "justification"}
    assert required_keys.issubset(reasoning_response_json.keys())
    assert isinstance(reasoning_response_json["min_price"], (int, float))
    assert isinstance(reasoning_response_json["max_price"], (int, float))
    assert isinstance(reasoning_response_json["midpoint"], (int, float))
    assert isinstance(reasoning_response_json["significant_deviation"], bool)
    assert isinstance(reasoning_response_json["justification"], str)
