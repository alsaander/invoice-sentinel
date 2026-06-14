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
    required_keys = {"precio_min", "precio_max", "punto_medio", "desviacion_significativa", "justificacion"}
    assert required_keys.issubset(reasoning_response_json.keys())
    assert isinstance(reasoning_response_json["precio_min"], (int, float))
    assert isinstance(reasoning_response_json["precio_max"], (int, float))
    assert isinstance(reasoning_response_json["punto_medio"], (int, float))
    assert isinstance(reasoning_response_json["desviacion_significativa"], bool)
    assert isinstance(reasoning_response_json["justificacion"], str)
