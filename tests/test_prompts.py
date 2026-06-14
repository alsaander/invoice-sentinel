from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def test_extraction_v1_exists():
    path = PROMPTS_DIR / "extraction_v1.txt"
    assert path.exists(), "extraction_v1.txt not found"
    content = path.read_text()
    assert "international commercial invoice" in content.lower()
    assert "{raw_text}" in content


def test_reasoning_v1_exists():
    path = PROMPTS_DIR / "reasoning_v1.txt"
    assert path.exists(), "reasoning_v1.txt not found"
    content = path.read_text()
    assert "international trade" in content.lower()
    assert "{description}" in content
    assert "{category}" in content
    assert "{quantity}" in content
    assert "{unit_price}" in content
    assert "{reference_price_block}" in content
    assert "min_price" in content
    assert "max_price" in content
    assert "midpoint" in content


def test_json_retry_v1_exists():
    path = PROMPTS_DIR / "json_retry_v1.txt"
    assert path.exists(), "json_retry_v1.txt not found"
    content = path.read_text()
    assert "Your previous response was not valid JSON" in content


def test_extraction_retry_v1_exists():
    path = PROMPTS_DIR / "extraction_retry_v1.txt"
    assert path.exists(), "extraction_retry_v1.txt not found"
    content = path.read_text()
    assert "extraction schema" in content.lower()
    assert "quantity" in content
    assert "unit_price" in content
    assert "category" in content


def test_reasoning_retry_v1_exists():
    path = PROMPTS_DIR / "reasoning_retry_v1.txt"
    assert path.exists(), "reasoning_retry_v1.txt not found"
    content = path.read_text()
    assert "reasoning schema" in content.lower()
    assert "min_price" in content
    assert "justification" in content
