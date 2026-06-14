import os
import tempfile

from invoicesentinel.reference_prices import (
    build_reference_price_block,
    find_match,
    format_reference_source,
    load_reference_prices,
)


class TestLoadReferencePrices:
    def test_loads_csv(self):
        csv_content = "keyword,category,price_min,price_max,currency,notes,english_keyword\ntaladro,Electronics,30,80,USD,test row,drill\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            path = f.name
        try:
            rows = load_reference_prices(path)
            assert len(rows) == 1
            assert rows[0]["keyword"] == "taladro"
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        rows = load_reference_prices("/nonexistent/path.csv")
        assert rows == []

    def test_empty_csv_returns_empty(self):
        csv_content = "keyword,category,price_min,price_max,currency,notes\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            path = f.name
        try:
            rows = load_reference_prices(path)
            assert rows == []
        finally:
            os.unlink(path)


class TestFindMatch:
    def setup_method(self):
        self.prices = [
            {"keyword": "taladro", "category": "Electronics", "price_min": "30", "price_max": "80", "currency": "USD"},
            {"keyword": "tornillo", "category": "Construction Materials", "price_min": "0.10", "price_max": "5", "currency": "USD"},
            {"keyword": "valvula", "category": "Industrial Machinery", "price_min": "50", "price_max": "200", "currency": "EUR"},
        ]

    def test_matches_keyword_in_description(self):
        match = find_match("Taladro eléctrico inalámbrico 18V", "Electronics", self.prices)
        assert match is not None
        assert match.row["keyword"] == "taladro"
        assert match.confidence == "specific"
        assert match.specificity_score >= 1

    def test_matches_keyword_in_category(self):
        """Broad fallback when keyword matches category but not description."""
        match = find_match("Válvula industrial DN50", "Industrial Machinery", self.prices)
        assert match is not None
        assert match.row["keyword"] == "valvula"
        assert match.confidence == "specific"
        # "valvula" is in "valvula industrial dn50" → description match

    def test_category_only_fallback(self):
        """When keyword is NOT in description but IS in category → broad match."""
        prices = [
            {"keyword": "repuesto", "category": "Automotive & Spare Parts", "price_min": "10", "price_max": "500"},
        ]
        match = find_match("Llantas 225/60R17", "Automotive & Spare Parts", prices)
        assert match is not None
        assert match.confidence == "broad"
        assert match.specificity_score == 0

    def test_no_match_returns_none(self):
        match = find_match("Algo completamente diferente", "Other", self.prices)
        assert match is None

    def test_case_insensitive_match(self):
        match = find_match("TALADRO PERCUTOR", "Electronics", self.prices)
        assert match is not None
        assert match.row["keyword"] == "taladro"

    def test_empty_prices_list(self):
        match = find_match("taladro", "Electronics", [])
        assert match is None

    def test_prefers_multi_token_specificity(self):
        """FR3.4 specificity: 'taladro percutor' (2 tokens) beats 'taladro' (1 token)
        for description containing both words."""
        prices = [
            {"keyword": "taladro", "category": "", "price_min": "10", "price_max": "20"},
            {"keyword": "taladro percutor", "category": "", "price_min": "30", "price_max": "80"},
        ]
        match = find_match("Taladro percutor profesional", "Electronics", prices)
        assert match is not None
        assert match.row["keyword"] == "taladro percutor"
        assert match.confidence == "specific"
        assert match.specificity_score == 2

    def test_specific_beats_broad_category_fallback(self):
        """A description-match row beats a category-only fallback even if
        the broad row appears first."""
        prices = [
            {"keyword": "repuesto", "category": "Automotive & Spare Parts", "price_min": "10", "price_max": "500"},
            {"keyword": "motor diesel", "category": "Automotive & Spare Parts", "price_min": "3000", "price_max": "15000"},
        ]
        match = find_match("Motor diesel 6.7L Cummins reconstruido", "Automotive & Spare Parts", prices)
        assert match is not None
        assert match.row["keyword"] == "motor diesel"
        assert match.confidence == "specific"

    def test_broad_fallback_when_no_description_match(self):
        """When no row matches the description, fall back to category match."""
        prices = [
            {"keyword": "repuesto", "category": "Automotive & Spare Parts", "price_min": "10", "price_max": "500"},
            {"keyword": "filtro", "category": "Automotive & Spare Parts", "price_min": "10", "price_max": "100"},
        ]
        match = find_match("Empaque de culata", "Automotive & Spare Parts", prices)
        assert match is not None
        assert match.confidence == "broad"
        assert match.specificity_score == 0
        assert match.row["keyword"] == "repuesto"

    def test_stop_word_not_counted(self):
        """Stop-words like 'de' are not counted as matching tokens.
        'disco de corte' against 'Hidroxido de sodio' should NOT match
        via description — falls to category fallback."""
        prices = [
            {"keyword": "disco de corte", "category": "Industrial Machinery",
             "price_min": "5", "price_max": "50"},
            {"keyword": "acero", "category": "Chemicals",
             "price_min": "500", "price_max": "2000"},
        ]
        match = find_match("Hidroxido de sodio", "Chemicals", prices)
        assert match is not None
        # 'disco de corte' has only 'de' matching → filtered out → falls to cat match
        assert match.confidence == "broad"
        assert match.specificity_score == 0
        assert match.row["keyword"] == "acero"

    def test_valid_stop_word_description_still_matches(self):
        """Stop-words are filtered out but remaining significant tokens still match.
        'disco de corte' against 'Disco de corte 7 pulgadas' → match."""
        prices = [
            {"keyword": "disco de corte", "category": "Industrial Machinery",
             "price_min": "5", "price_max": "50"},
        ]
        match = find_match("Disco de corte 7 pulgadas", "Industrial Machinery", prices)
        assert match is not None
        assert match.confidence == "specific"
        assert match.specificity_score == 2  # "disco" + "corte" = 2 significant tokens


class TestBuildReferencePriceBlock:
    def test_returns_formatted_block(self):
        match = {"keyword": "taladro", "category": "Electronics", "price_min": "30", "price_max": "80", "currency": "USD"}
        block = build_reference_price_block(match)
        assert "30" in block
        assert "80" in block
        assert "USD" in block
        assert "taladro" not in block


class TestFormatReferenceSource:
    def test_uses_english_keyword_when_present(self):
        match = {"keyword": "taladro", "english_keyword": "drill"}
        assert format_reference_source(match) == "reference_csv:drill"

    def test_fallback_to_spanish_keyword(self):
        match = {"keyword": "taladro"}
        assert format_reference_source(match) == "reference_csv:taladro"

    def test_unknown_keyword(self):
        match = {"keyword": ""}
        assert format_reference_source(match) == "reference_csv:unknown"
