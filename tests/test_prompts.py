from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def test_extraction_v1_exists():
    path = PROMPTS_DIR / "extraction_v1.txt"
    assert path.exists(), "extraction_v1.txt not found"
    content = path.read_text()
    assert "Eres un sistema de extracción de datos de facturas" in content
    assert "{raw_text}" in content


def test_reasoning_v1_exists():
    path = PROMPTS_DIR / "reasoning_v1.txt"
    assert path.exists(), "reasoning_v1.txt not found"
    content = path.read_text()
    assert "Actúas como un experto en comercio internacional" in content
    assert "{description}" in content
    assert "{category}" in content
    assert "{quantity}" in content
    assert "{unit_price}" in content
    assert "{reference_price_block}" in content
    assert "precio_min" in content
    assert "precio_max" in content
    assert "punto_medio" in content


def test_json_retry_v1_exists():
    path = PROMPTS_DIR / "json_retry_v1.txt"
    assert path.exists(), "json_retry_v1.txt not found"
    content = path.read_text()
    assert "Tu respuesta anterior no era JSON válido" in content


def test_extraction_retry_v1_exists():
    path = PROMPTS_DIR / "extraction_retry_v1.txt"
    assert path.exists(), "extraction_retry_v1.txt not found"
    content = path.read_text()
    assert "esquema de extracción" in content
    assert "quantity" in content
    assert "unit_price" in content
    assert "category" in content


def test_reasoning_retry_v1_exists():
    path = PROMPTS_DIR / "reasoning_retry_v1.txt"
    assert path.exists(), "reasoning_retry_v1.txt not found"
    content = path.read_text()
    assert "esquema de razonamiento" in content
    assert "precio_min" in content
    assert "justificacion" in content
