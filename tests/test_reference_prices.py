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
        csv_content = "keyword,category,price_min,price_max,currency,notes\ntaladro,Electrónica,30,80,USD,test row\n"
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
            {"keyword": "taladro", "category": "Electrónica", "price_min": "30", "price_max": "80", "currency": "USD"},
            {"keyword": "tornillo", "category": "Materiales de construcción", "price_min": "0.10", "price_max": "5", "currency": "USD"},
            {"keyword": "valvula", "category": "Maquinaria", "price_min": "50", "price_max": "200", "currency": "EUR"},
        ]

    def test_matches_keyword_in_description(self):
        match = find_match("Taladro eléctrico inalámbrico 18V", "Electrónica", self.prices)
        assert match is not None
        assert match.row["keyword"] == "taladro"
        assert match.confidence == "specific"
        assert match.specificity_score >= 1

    def test_matches_keyword_in_category(self):
        """Broad fallback when keyword matches category but not description."""
        match = find_match("Válvula industrial DN50", "Maquinaria", self.prices)
        assert match is not None
        assert match.row["keyword"] == "valvula"
        assert match.confidence == "specific"
        # "valvula" is in "valvula industrial dn50" → description match

    def test_category_only_fallback(self):
        """When keyword is NOT in description but IS in category → broad match."""
        prices = [
            {"keyword": "repuesto", "category": "Vehículos/Repuestos", "price_min": "10", "price_max": "500"},
        ]
        match = find_match("Llantas 225/60R17", "Vehículos/Repuestos", prices)
        assert match is not None
        assert match.confidence == "broad"
        assert match.specificity_score == 0

    def test_no_match_returns_none(self):
        match = find_match("Algo completamente diferente", "Otro", self.prices)
        assert match is None

    def test_case_insensitive_match(self):
        match = find_match("TALADRO PERCUTOR", "Electrónica", self.prices)
        assert match is not None
        assert match.row["keyword"] == "taladro"

    def test_empty_prices_list(self):
        match = find_match("taladro", "Electrónica", [])
        assert match is None

    def test_prefers_multi_token_specificity(self):
        """FR3.4 specificity: 'taladro percutor' (2 tokens) beats 'taladro' (1 token)
        for description containing both words."""
        prices = [
            {"keyword": "taladro", "category": "", "price_min": "10", "price_max": "20"},
            {"keyword": "taladro percutor", "category": "", "price_min": "30", "price_max": "80"},
        ]
        match = find_match("Taladro percutor profesional", "Electrónica", prices)
        assert match is not None
        assert match.row["keyword"] == "taladro percutor"
        assert match.confidence == "specific"
        assert match.specificity_score == 2

    def test_specific_beats_broad_category_fallback(self):
        """A description-match row beats a category-only fallback even if
        the broad row appears first."""
        prices = [
            {"keyword": "repuesto", "category": "Vehículos/Repuestos", "price_min": "10", "price_max": "500"},
            {"keyword": "motor diesel", "category": "Vehículos/Repuestos", "price_min": "3000", "price_max": "15000"},
        ]
        match = find_match("Motor diesel 6.7L Cummins reconstruido", "Vehículos/Repuestos", prices)
        assert match is not None
        assert match.row["keyword"] == "motor diesel"
        assert match.confidence == "specific"

    def test_broad_fallback_when_no_description_match(self):
        """When no row matches the description, fall back to category match."""
        prices = [
            {"keyword": "repuesto", "category": "Vehículos/Repuestos", "price_min": "10", "price_max": "500"},
            {"keyword": "filtro", "category": "Vehículos/Repuestos", "price_min": "10", "price_max": "100"},
        ]
        match = find_match("Empaque de culata", "Vehículos/Repuestos", prices)
        assert match is not None
        assert match.confidence == "broad"
        assert match.specificity_score == 0
        assert match.row["keyword"] == "repuesto"


class TestBuildReferencePriceBlock:
    def test_returns_formatted_block(self):
        match = {"keyword": "taladro", "category": "Electrónica", "price_min": "30", "price_max": "80", "currency": "USD"}
        block = build_reference_price_block(match)
        assert "30" in block
        assert "80" in block
        assert "USD" in block
        assert "taladro" not in block


class TestFormatReferenceSource:
    def test_includes_keyword(self):
        match = {"keyword": "taladro"}
        assert format_reference_source(match) == "reference_csv:taladro"

    def test_unknown_keyword(self):
        match = {"keyword": ""}
        assert format_reference_source(match) == "reference_csv:unknown"
