import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from invoicesentinel.extract import (
    _parse_extraction_response,
    _try_strip_markdown,
    _validate_category,
    build_extraction_prompt,
    build_extraction_retry_prompt,
    build_retry_prompt,
    extract_line_items,
    load_prompt_template,
)
from invoicesentinel.llm_client import OllamaClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


class TestPromptHelpers:
    def test_load_prompt_template(self):
        content = load_prompt_template("extraction_v1")
        assert "international commercial invoice" in content.lower()
        assert "{raw_text}" in content

    def test_build_extraction_prompt(self):
        result = build_extraction_prompt("INVOICE TEXT HERE")
        assert "{raw_text}" not in result
        assert "INVOICE TEXT HERE" in result

    def test_build_retry_prompt(self):
        result = build_retry_prompt()
        assert "Your previous response was not valid JSON" in result

    def test_build_retry_does_not_contain_raw_text_placeholder(self):
        result = build_retry_prompt()
        assert "{raw_text}" not in result

    def test_build_extraction_retry_prompt(self):
        result = build_extraction_retry_prompt("INVOICE WITH ITEMS")
        assert "{raw_text}" not in result
        assert "INVOICE WITH ITEMS" in result
        assert "items" in result
        assert "quantity" in result


class TestTryStripMarkdown:
    def test_no_markdown(self):
        assert _try_strip_markdown('{"a": 1}') == '{"a": 1}'

    def test_strips_json_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert _try_strip_markdown(text) == '{"a": 1}'

    def test_strips_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert _try_strip_markdown(text) == '{"a": 1}'

    def test_strips_array_fence(self):
        text = '```json\n[{"a": 1}]\n```'
        assert _try_strip_markdown(text) == '[{"a": 1}]'

    def test_whitespace_handling(self):
        text = '  \n```json\n[1, 2, 3]\n```  \n'
        assert _try_strip_markdown(text) == '[1, 2, 3]'


class TestParseExtractionResponse:
    def test_valid_array(self):
        """Bare array (backwards compat) → returned as-is"""
        result = _parse_extraction_response('[{"a": 1}, {"a": 2}]')
        assert len(result) == 2

    def test_items_wrapped(self):
        """Canonical shape {"items": [...]} → returns the array"""
        result = _parse_extraction_response('{"items": [{"x": 1}]}')
        assert len(result) == 1
        assert result[0]["x"] == 1

    def test_markdown_wrapped(self):
        result = _parse_extraction_response('```json\n{"items": [{"x": 1}]}\n```')
        assert len(result) == 1
        assert result[0]["x"] == 1

    def test_single_object_wraps_in_list(self):
        """Bare object (no 'items' key) — wraps in list for lenient handling"""
        result = _parse_extraction_response('{"not": "array"}')
        assert result == [{"not": "array"}]

    def test_items_key_not_array_raises(self):
        with pytest.raises(ValueError, match="items.*array"):
            _parse_extraction_response('{"items": "not_an_array"}')

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_extraction_response("{bad json")


class TestValidateCategory:
    VOCAB = [
        "Electronics", "Construction Materials", "Textiles",
        "Industrial Machinery", "Food & Beverage", "Chemicals",
        "Automotive & Spare Parts", "Other",
    ]

    def test_valid_category(self):
        cat, raw = _validate_category("Electronics", self.VOCAB)
        assert cat == "Electronics"
        assert raw is None

    def test_other_mapped_to_other(self):
        cat, raw = _validate_category("Sportswear", self.VOCAB)
        assert cat == "Other"
        assert raw == "Sportswear"

    def test_empty_category(self):
        cat, raw = _validate_category("", self.VOCAB)
        assert cat == "Other"
        assert raw == ""

    def test_case_sensitive(self):
        cat, raw = _validate_category("electronics", self.VOCAB)
        assert cat == "Other"
        assert raw == "electronics"


class TestExtractLineItems:
    def _make_mock_client(self, side_effect):
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = side_effect
        return client

    def test_clean_3_items(self, invoice_id, cfg):
        raw = _load_fixture("extraction_response_3_items.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 3
        assert all(li.severity != "PARSE_ERROR" for li in items)
        assert [li.category for li in items] == ["Electronics", "Construction Materials", "Industrial Machinery"]
        assert len(calls) == 1
        assert calls[0].call_type == "extraction"
        assert calls[0].prompt_version == "extraction_v1"

    def test_malformed_then_valid_retry(self, invoice_id, cfg):
        malformed = _load_fixture("extraction_response_malformed.json")
        valid = _load_fixture("extraction_response_retry_valid.json")
        client = self._make_mock_client([malformed, valid])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        assert items[0].description == "Dell PowerEdge rack-mounted server"
        assert len(calls) == 2, "NFR4: original + retry both logged"
        assert calls[0].call_type == "extraction"
        assert calls[0].prompt_version == "extraction_v1"
        assert calls[1].call_type == "retry"
        assert calls[1].prompt_version == "extraction_retry_v1"
        assert calls[1].latency_ms >= 0
        assert calls[1].model == cfg.model.name

    def test_double_fail_produces_parse_error(self, invoice_id, cfg):
        malformed = _load_fixture("extraction_response_malformed.json")
        client = self._make_mock_client([malformed, malformed])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        assert items[0].severity == "PARSE_ERROR"
        assert items[0].description == "(parse error)"
        assert len(calls) == 2, "NFR4: both attempts logged even on double fail"

    def test_bad_category_mapped_to_otro(self, invoice_id, cfg):
        raw = _load_fixture("extraction_response_bad_category.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        li = items[0]
        assert li.category == "Other"
        assert li.category_raw == "Sportswear"
        assert li.description == "Premium cotton t-shirt"

    def test_markdown_wrapped_json(self, invoice_id, cfg):
        raw = _load_fixture("extraction_response_markdown.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        assert items[0].description == "220V 1500W electrical resistor"
        assert items[0].category == "Electronics"

    def test_line_item_fields_populated(self, invoice_id, cfg):
        raw = _load_fixture("extraction_response_3_items.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        li = items[0]
        assert li.invoice_id == invoice_id
        assert li.quantity == 10
        assert li.unit_price == 45.50
        assert li.currency == "USD"
        assert li.description == "Cordless 18V electric drill"
        assert li.category == "Electronics"
        assert li.category_raw is None
        assert li.severity == "PENDING"

    def test_nfr4_audit_logging(self, invoice_id, cfg):
        raw = _load_fixture("extraction_response_3_items.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(calls) == 1
        call = calls[0]
        assert call.invoice_id == invoice_id
        assert call.call_type == "extraction"
        assert call.prompt_version == "extraction_v1"
        assert call.model == cfg.model.name
        assert call.raw_response == raw
        assert call.latency_ms >= 0

    def test_null_fields_handled(self, invoice_id, cfg):
        raw = json.dumps([{
            "quantity": None,
            "unit_price": None,
            "currency": "UNKNOWN",
            "description": "Item with nulls",
            "category": "Other",
        }])
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        assert items[0].quantity is None
        assert items[0].unit_price is None
        assert items[0].currency == "UNKNOWN"

    def test_hallucination_regression_bare_object_single_shot(self, invoice_id, cfg):
        """Regression: bare JSON object (not wrapped in {"items":[...]}) returned
        by model describing a hallucinated item not in the source text.
        - Parser must handle bare object by wrapping in list (no crash).
        - Only one LlmCall logged (no retry needed since it's valid JSON)."""
        raw = _load_fixture("extraction_response_hallucination.json")
        client = self._make_mock_client([raw])
        items, calls = extract_line_items(invoice_id, "dummy text", cfg, client)
        assert len(items) == 1
        assert items[0].description == "CCTV camera"
        assert len(calls) == 1, "NFR4: single call logged"
        assert calls[0].call_type == "extraction"
        assert calls[0].prompt_version == "extraction_v1"
