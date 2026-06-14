import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.models import LineItem
from invoicesentinel.reason import (
    _parse_price_estimate_json,
    build_price_estimate_prompt,
    build_price_estimate_retry_prompt,
    classify_severity,
    compute_deviation_pct,
    reason_line_item,
)
from invoicesentinel.reference_prices import load_reference_prices

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


class TestBuildPriceEstimatePrompt:
    def test_all_fields_filled(self):
        prompt = build_price_estimate_prompt(
            description="Cordless electric drill",
            category="Electronics",
            quantity=10,
        )
        assert "Cordless electric drill" in prompt
        assert "Electronics" in prompt
        assert "10" in prompt
        assert "NO INVOICED PRICE" in prompt
        assert "5000.0" not in prompt
        assert "invoiced unit price" not in prompt.lower()
        assert "unit_price" not in prompt.lower()

    def test_null_values_use_na(self):
        prompt = build_price_estimate_prompt(
            description="Item",
            category="Other",
            quantity=None,
        )
        assert "N/A" in prompt
        assert "NO INVOICED PRICE" in prompt


class TestParsePriceEstimateJson:
    def test_valid_object(self):
        text = '{"min_price": 30, "max_price": 80}'
        result = _parse_price_estimate_json(text)
        assert result["min_price"] == 30
        assert result["max_price"] == 80

    def test_markdown_wrapped(self):
        text = '```json\n{"min_price": 30, "max_price": 80}\n```'
        result = _parse_price_estimate_json(text)
        assert result["min_price"] == 30

    def test_raises_on_array(self):
        with pytest.raises(ValueError, match="Expected JSON object"):
            _parse_price_estimate_json('[{"a": 1}]')

    def test_raises_on_invalid(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_price_estimate_json("not json")


class TestComputeDeviationPct:
    def test_overpriced(self):
        result = compute_deviation_pct(200, 100)
        assert result == 100.0

    def test_underpriced(self):
        result = compute_deviation_pct(50, 100)
        assert result == -50.0

    def test_exact_midpoint(self):
        result = compute_deviation_pct(100, 100)
        assert result == 0.0

    def test_extreme_overpriced(self):
        result = compute_deviation_pct(5000, 55)
        assert result == pytest.approx(8981.8, rel=0.01)


class TestClassifySeverity:
    def test_normal_exactly_100(self):
        assert classify_severity(100.0) == "NORMAL"

    def test_normal_below_100(self):
        assert classify_severity(50.0) == "NORMAL"
        assert classify_severity(-50.0) == "NORMAL"

    def test_moderate_above_100(self):
        assert classify_severity(150.0) == "MODERATE"

    def test_moderate_exactly_200(self):
        assert classify_severity(200.0) == "MODERATE"

    def test_high_above_200(self):
        assert classify_severity(250.0) == "HIGH"

    def test_high_extreme(self):
        assert classify_severity(8981.8) == "HIGH"

    def test_negative_deviation_moderate(self):
        assert classify_severity(-150.0) == "MODERATE"

    def test_boundary_100_dot_01(self):
        assert classify_severity(100.01) == "MODERATE"

    def test_boundary_200_dot_01(self):
        assert classify_severity(200.01) == "HIGH"

    def test_zero_deviation(self):
        assert classify_severity(0.0) == "NORMAL"


class TestReasonLineItem:
    def _make_mock_client(self, side_effect):
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = side_effect
        return client

    def _make_line_item(self, **kwargs):
        defaults = dict(
            id=1, invoice_id=1, quantity=10, unit_price=5000.0,
            currency="USD", description="Cordless 18V electric drill",
            category="Electronics",
        )
        defaults.update(kwargs)
        return LineItem(**defaults)

    def test_taladro_high_severity(self, cfg):
        """FR3 acceptance: Taladro, qty=10, unit_price=5000 USD → HIGH, llm_estimate"""
        raw = _load_fixture("reasoning_response_1.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "HIGH"
        assert result.reference_source == "llm_estimate"
        assert result.deviation_pct == pytest.approx(8981.8, rel=0.01)
        assert result.est_market_low == 30
        assert result.est_market_high == 80
        assert call is not None
        assert call.call_type == "price_estimate"
        assert call.prompt_version == "price_estimate_v1"

    def test_normal_severity(self, cfg):
        """unit_price within 100% of midpoint → NORMAL"""
        raw = _load_fixture("reasoning_response_normal.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item(unit_price=50.0)
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "NORMAL"
        assert result.deviation_pct == pytest.approx(-9.09, rel=0.01)
        assert result.reference_source == "llm_estimate"

    def test_moderate_severity(self, cfg):
        """unit_price >100% but ≤200% of midpoint → MODERATE"""
        raw = _load_fixture("reasoning_response_moderate.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item(unit_price=140.0)
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "MODERATE"
        assert result.deviation_pct == pytest.approx(154.55, rel=0.01)

    def test_malformed_then_valid_retry(self, cfg):
        """Malformed JSON first, retry succeeds"""
        malformed = _load_fixture("reasoning_response_malformed.json")
        valid = _load_fixture("reasoning_response_1.json")
        client = self._make_mock_client([malformed, valid])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "HIGH"
        assert call.call_type == "retry"
        assert call.prompt_version == "price_estimate_retry_v1"

    def test_double_fail_returns_unknown(self, cfg):
        """Both attempts fail → severity UNKNOWN"""
        malformed = _load_fixture("reasoning_response_malformed.json")
        client = self._make_mock_client([malformed, malformed])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "UNKNOWN"
        assert call is not None

    def test_wrong_key_then_empty_array_returns_unknown(self, cfg):
        """Regression: first response is valid JSON dict but wrong keys,
        second response is an empty array (not a dict). The first passes
        parsing but has no punto_medio → UNKNOWN. The second is never
        reached. No exception should occur."""
        wrong_key = _load_fixture("reasoning_response_wrong_key.json")
        empty_arr = _load_fixture("reasoning_response_empty_array.json")
        client = self._make_mock_client([wrong_key, empty_arr])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "UNKNOWN"
        assert call.call_type == "price_estimate"
        assert call.prompt_version == "price_estimate_v1"

    def test_retry_empty_array_then_wrong_key_returns_unknown(self, cfg):
        """First response is empty array (JSON parse fail → retry),
        retry response is dict with wrong keys (passes parse but no
        punto_medio → UNKNOWN). Verifies context-aware retry prompt is
        used."""
        empty_arr = _load_fixture("reasoning_response_empty_array.json")
        wrong_key = _load_fixture("reasoning_response_wrong_key.json")
        client = self._make_mock_client([empty_arr, wrong_key])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "UNKNOWN"
        assert call.call_type == "retry"
        assert call.prompt_version == "price_estimate_retry_v1"

    def test_unknown_currency_skips_llm(self, cfg):
        """currency=UNKNOWN → severity UNKNOWN, no LLM call"""
        client = self._make_mock_client([])
        item = self._make_line_item(currency="UNKNOWN")
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "UNKNOWN"
        assert call is None

    def test_null_price_range_returns_unknown(self, cfg):
        """LLM response with null precio_min/precio_max → UNKNOWN"""
        raw = _load_fixture("reasoning_response_unknown.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "UNKNOWN"
        assert result.justification != ""
        assert call is not None

    def test_negative_deviation_underpriced(self, cfg):
        """Underpriced (negative deviation) → severity still based on abs value"""
        raw = json.dumps({
            "min_price": 80,
            "max_price": 120,
            "midpoint": 100,
            "significant_deviation": False,
            "justification": "The price of 10 USD is below the market range.",
        })
        client = self._make_mock_client([raw])
        item = self._make_line_item(unit_price=10.0, quantity=1)
        result, call = reason_line_item(item, cfg, client, [])
        assert result.deviation_pct == -90.0
        assert result.severity == "NORMAL"

    def test_markdown_wrapped_response(self, cfg):
        """JSON wrapped in markdown fences is parsed correctly"""
        raw = "```json\n" + _load_fixture("reasoning_response_1.json") + "\n```"
        client = self._make_mock_client([raw])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])
        assert result.severity == "HIGH"

    def test_zero_unit_price(self, cfg):
        """unit_price=0 → deviation=-100%, severity=NORMAL"""
        raw = _load_fixture("reasoning_response_1.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item(unit_price=0.0)
        result, call = reason_line_item(item, cfg, client, [])
        assert result.deviation_pct == pytest.approx(-100.0, rel=0.01)
        assert result.severity == "NORMAL"


class TestDrillOverinvoiceRegression:
    """Canonical regression test for the economic anchoring fix.

    A 50×$8500 taladro drill MUST be flagged HIGH because
    reference_prices.csv now has a taladro row at $80-300 USD.
    """

    def _make_mock_client(self, side_effect):
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = side_effect
        return client

    def _make_line_item(self, **kwargs):
        defaults = dict(
            id=99, invoice_id=1, quantity=50, unit_price=8500.0,
            currency="USD",
            description="Taladro percutor inalambrico 20V profesional",
            category="Industrial Machinery",
        )
        defaults.update(kwargs)
        return LineItem(**defaults)

    def test_drill_with_reference_csv_match_must_be_high(self, cfg):
        """With reference CSV match → severity=HIGH, no LLM call needed.
        Reference midpoint = (80+300)/2 = 190, deviation = (8500-190)/190*100 ≈ 4373%."""
        ref_path = Path(__file__).resolve().parent.parent / "reference_prices.csv"
        ref_prices = load_reference_prices(str(ref_path))
        client = self._make_mock_client([])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, ref_prices)

        assert result.severity == "HIGH", (
            f"Expected HIGH for 50×$8500 taladro, got {result.severity}"
        )
        assert result.reference_source is not None
        assert "reference_csv" in result.reference_source
        assert result.deviation_pct > 200, (
            f"Deviation must be >200% for HIGH, got {result.deviation_pct}%"
        )
        assert call is None, "No LLM call should be made when CSV match exists"
        # "taladro percutor" (2 tokens, $100-350) beats "taladro" (1 token, $80-300)
        assert result.est_market_low == 100
        assert result.est_market_high == 350
        assert result.reference_confidence == "specific"

    def test_drill_without_reference_csv_falls_back_to_llm(self, cfg):
        """Without reference CSV match, LLM price estimate is used.
        Uses the anchored fixture (min_price=5500, max=10000) which gives
        midpoint=7750, deviation≈9.68% → NORMAL."""
        raw = _load_fixture("price_estimate_anchored.json")
        client = self._make_mock_client([raw])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, [])

        assert result.severity == "NORMAL"
        assert result.reference_source == "llm_estimate"
        assert call is not None
        assert call.call_type == "price_estimate"
        assert call.prompt_version == "price_estimate_v1"


class TestReasonLineItemReferencePrices:
    def _make_mock_client(self, side_effect):
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = side_effect
        return client

    def _make_line_item(self, **kwargs):
        defaults = dict(
            id=2, invoice_id=1, quantity=10, unit_price=5000.0,
            currency="USD", description="Taladro eléctrico inalámbrico 18V",
            category="Electronics",
        )
        defaults.update(kwargs)
        return LineItem(**defaults)

    def test_reference_csv_override_source(self, cfg):
        """FR3.4: reference_prices match → reference_source='reference_csv:taladro',
        no LLM call is made."""
        ref_prices = [
            {"keyword": "taladro", "category": "Electronics",
             "price_min": "20", "price_max": "40", "currency": "USD", "notes": ""},
        ]
        client = self._make_mock_client([])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, ref_prices)
        assert result.reference_source == "reference_csv:taladro"
        assert result.est_market_low == 20
        assert result.est_market_high == 40
        assert result.reference_confidence == "specific"
        assert call is None

    def test_reference_csv_changes_deviation_calculation(self, cfg):
        """FR3.4: deviation computed from ref midpoint (30), no LLM call."""
        ref_prices = [
            {"keyword": "taladro", "category": "Electronics",
             "price_min": "20", "price_max": "40", "currency": "USD", "notes": ""},
        ]
        client = self._make_mock_client([])
        item = self._make_line_item()
        result, call = reason_line_item(item, cfg, client, ref_prices)
        ref_midpoint = (20 + 40) / 2
        expected_dev = (5000 - ref_midpoint) / ref_midpoint * 100
        assert result.deviation_pct == pytest.approx(expected_dev, rel=0.01)
        assert result.reference_source == "reference_csv:taladro"
        assert result.reference_confidence == "specific"
        assert call is None


class TestEngineTurboRegression:
    """Regression: engine+turbo items must match specific rows, not broad 'repuesto'.

    Pre-Fix-3 behaviour: "Motor diesel 6.7L Cummins" matched generic
    "repuesto" (USD 10-500) via category fallback → +37154.9% deviation.

    Post-Fix-3: should match "motor diesel" (USD 3,000-15,000) via
    description specificity scoring → realistic deviation (~1 order of magnitude).
    """

    @pytest.fixture
    def ref_prices(self):
        ref_path = Path(__file__).resolve().parent.parent / "reference_prices.csv"
        return load_reference_prices(str(ref_path))

    def test_engine_matches_specific_row_not_broad(self, cfg, ref_prices):
        """Motor diesel → matches 'motor diesel' row, not 'repuesto consumible'."""
        item = LineItem(
            id=201, invoice_id=1, quantity=8, unit_price=95000.0,
            currency="USD",
            description="Motor diesel 6.7L Cummins reconstruido",
            category="Automotive & Spare Parts",
        )
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = []
        result, call = reason_line_item(item, cfg, client, ref_prices)

        assert result.reference_source is not None
        assert "reference_csv:" in result.reference_source
        assert "repuesto" not in result.reference_source
        assert result.reference_confidence == "specific"
        assert result.est_market_low == 3000
        assert result.est_market_high == 15000
        assert result.severity == "HIGH"
        # Realistic deviation: midpoint=9000, (95000-9000)/9000*100 ≈ 955%
        # This is ~1 order of magnitude, NOT 37154%
        assert result.deviation_pct is not None
        assert 500 < result.deviation_pct < 5000, (
            f"Deviation {result.deviation_pct:.1f}% should be in believable range, "
            f"not >30000%"
        )
        assert call is None

    def test_turbo_matches_specific_row_not_broad(self, cfg, ref_prices):
        """Turbo cargador → matches 'turbocargador' or 'turbo' row."""
        item = LineItem(
            id=202, invoice_id=1, quantity=16, unit_price=18500.0,
            currency="USD",
            description="Turbo cargador Holset HE400VG",
            category="Automotive & Spare Parts",
        )
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = []
        result, call = reason_line_item(item, cfg, client, ref_prices)

        assert result.reference_source is not None
        source_ok = "turbo" in result.reference_source
        assert source_ok, (
            f"Expected 'turbo' in reference_source, got {result.reference_source}"
        )
        assert result.reference_confidence == "specific"
        assert result.est_market_low == 500
        assert result.est_market_high == 3000
        assert result.severity == "HIGH"
        assert result.deviation_pct is not None
        # Realistic deviation: midpoint=1750, (18500-1750)/1750*100 ≈ 957%
        assert result.deviation_pct > 200
        assert 500 < result.deviation_pct < 5000, (
            f"Deviation {result.deviation_pct:.1f}% should be in believable range"
        )
        assert call is None
