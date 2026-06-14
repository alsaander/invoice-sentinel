from invoicesentinel.config import Config
from invoicesentinel.grounding import (
    _tokenize,
    all_ungrounded,
    check_grounding,
    is_any_ungrounded,
)
from invoicesentinel.models import LineItem


def _make_item(description: str, severity: str = "PENDING") -> LineItem:
    return LineItem(
        description=description,
        severity=severity,
        grounded="true",
    )


RAW_TEXT = (
    "Taladro percutor inalambrico 20V profesional\n"
    "Disco de corte diamantado 7\"\n"
    "Factura No. 001-002-003\n"
    "Fecha: 15/01/2026"
)


class TestTokenize:
    def test_lowercases(self):
        assert _tokenize("ABC") == "abc"

    def test_strips_accents(self):
        assert _tokenize("Electrónica") == "electronica"

    def test_removes_unicode(self):
        assert _tokenize("Corte diamantado") == "corte diamantado"


class TestCheckGrounding:
    def test_item_found_in_text_via_substring(self):
        cfg = Config()
        items = [_make_item("Taladro percutor inalambrico 20V profesional")]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].grounded == "true"
        assert result[0].severity == "PENDING"

    def test_item_not_found_is_ungrounded(self):
        cfg = Config()
        items = [_make_item("Camara de CCTV")]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].grounded == "false"
        assert result[0].severity == "UNGROUNDED"

    def test_partial_fuzzy_match_below_threshold(self):
        cfg = Config()
        cfg.thresholds.grounding_min_score = 80
        items = [_make_item("Camara de vigilancia")]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].grounded == "false"

    def test_skip_parse_error_items(self):
        cfg = Config()
        items = [_make_item("Taladro", severity="PARSE_ERROR")]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].severity == "PARSE_ERROR"

    def test_empty_description_flagged(self):
        cfg = Config()
        items = [_make_item("")]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].grounded == "false"
        assert result[0].severity == "UNGROUNDED"

    def test_mixed_grounded_and_ungrounded(self):
        cfg = Config()
        items = [
            _make_item("Taladro percutor inalambrico 20V profesional"),
            _make_item("Camara de CCTV"),
        ]
        result = check_grounding(items, RAW_TEXT, cfg)
        assert result[0].grounded == "true"
        assert result[1].grounded == "false"
        assert result[1].severity == "UNGROUNDED"


class TestUngroundedHelpers:
    def test_is_any_ungrounded_true(self):
        items = [_make_item("x", severity="UNGROUNDED")]
        assert is_any_ungrounded(items) is True

    def test_is_any_ungrounded_false(self):
        items = [_make_item("x", severity="NORMAL")]
        assert is_any_ungrounded(items) is False

    def test_all_ungrounded_true(self):
        items = [
            _make_item("a", severity="UNGROUNDED"),
            _make_item("b", severity="UNGROUNDED"),
        ]
        assert all_ungrounded(items) is True

    def test_all_ungrounded_false_when_one_grounded(self):
        items = [
            _make_item("a", severity="UNGROUNDED"),
            _make_item("b", severity="NORMAL"),
        ]
        assert all_ungrounded(items) is False

    def test_all_ungrounded_skips_parse_error(self):
        items = [
            _make_item("a", severity="PARSE_ERROR"),
            _make_item("b", severity="UNGROUNDED"),
        ]
        assert all_ungrounded(items) is True
