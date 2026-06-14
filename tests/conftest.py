import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fpdf import FPDF

from invoicesentinel.config import Config
from invoicesentinel.models import LineItem, LlmCall, create_tables, insert_line_item, insert_llm_call

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def make_text_pdf(path: str, text: str = "Test invoice\nCordless 18V electric drill\nQuantity: 10\nPrice: 45.50 USD") -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in text.split("\n"):
        pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path


def make_empty_pdf(path: str) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.output(path)
    return path


@pytest.fixture
def extraction_response_json() -> list:
    path = FIXTURES_DIR / "extraction_response_1.json"
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    if isinstance(data, list):
        return data
    raise TypeError(f"Unexpected extraction fixture shape: {type(data)}")


@pytest.fixture
def reasoning_response_json() -> dict:
    path = FIXTURES_DIR / "reasoning_response_1.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def mock_ollama_response_extraction() -> str:
    path = FIXTURES_DIR / "extraction_response_1.json"
    with open(path) as f:
        return f.read()


@pytest.fixture
def mock_ollama_response_reasoning() -> str:
    path = FIXTURES_DIR / "reasoning_response_1.json"
    with open(path) as f:
        return f.read()


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def invoice_id(db_conn) -> int:
    cur = db_conn.execute(
        "INSERT INTO invoices (filename, file_sha256, received_at) VALUES (?, ?, ?)",
        ("test.pdf", "abc123def", "2025-01-01T00:00:00"),
    )
    db_conn.commit()
    return cur.lastrowid


@pytest.fixture
def cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.paths.inbox = str(tmp_path / "inbox")
    cfg.paths.processed = str(tmp_path / "processed")
    cfg.paths.review_high = str(tmp_path / "review/high")
    cfg.paths.review_moderate = str(tmp_path / "review/moderate")
    cfg.paths.review_extraction_failed = str(tmp_path / "review/extraction_failed")
    os.makedirs(cfg.paths.inbox, exist_ok=True)
    os.makedirs(cfg.paths.processed, exist_ok=True)
    os.makedirs(cfg.paths.review_high, exist_ok=True)
    os.makedirs(cfg.paths.review_moderate, exist_ok=True)
    os.makedirs(cfg.paths.review_extraction_failed, exist_ok=True)
    return cfg
