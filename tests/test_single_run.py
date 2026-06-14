import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fpdf import FPDF

from invoicesentinel.config import Config
from invoicesentinel.single_run import run_single_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class MockOllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="llama3:8b"):
        self.base_url = base_url
        self.model = model

    def generate(self, prompt: str, system: str = None) -> str:
        prompt_lower = prompt.lower()
        if "product/service" in prompt_lower:
            return (FIXTURES_DIR / "extraction_response_1.json").read_text()
        return (FIXTURES_DIR / "reasoning_response_1.json").read_text()

    def close(self):
        pass


class MockFailingExtractionClient:
    def __init__(self, base_url="http://localhost:11434", model="llama3:8b"):
        self.base_url = base_url
        self.model = model
        self._call_count = 0

    def generate(self, prompt: str, system: str = None) -> str:
        self._call_count += 1
        return "NOT VALID JSON"

    def close(self):
        pass


class MockConnectErrorClient:
    def __init__(self, base_url="http://remote.example.com:11434", model="llama3:8b"):
        self.base_url = base_url
        self.model = model

    def generate(self, prompt: str, system: str = None) -> str:
        import httpx
        raise httpx.ConnectError("Connection refused")

    def close(self):
        pass


def make_test_pdf(path: str, text: str = None) -> str:
    fname = os.path.basename(path)
    if text is None:
        text = (
            f"INVOICE {fname}\n"
            "Supplier: Company XYZ S.A.\n"
            "Cordless 18V electric drill\n"
            "Quantity: 10  Price: 45.50 USD\n"
            "Stainless steel M8 x 30mm hex bolt\n"
            "Quantity: 200  Price: 2.30 USD\n"
        )
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in text.split("\n"):
        pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.paths.inbox = str(tmp_path / "inbox")
    c.paths.processed = str(tmp_path / "processed")
    c.paths.review_high = str(tmp_path / "review/high")
    c.paths.review_moderate = str(tmp_path / "review/moderate")
    c.paths.review_extraction_failed = str(tmp_path / "review/extraction_failed")
    c.model.name = "test-model"
    c.database.path = str(tmp_path / "test.db")
    for p in [c.paths.inbox, c.paths.processed, c.paths.review_high,
              c.paths.review_moderate, c.paths.review_extraction_failed]:
        os.makedirs(p, exist_ok=True)
    return c


class TestSingleRunLogEvents:
    def test_log_events_sequence(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "test.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        events = list(run_single_pipeline(pdf, cfg, client))
        types = [e["type"] for e in events]
        assert "info" in types
        assert "item" in types
        assert "routing" in types
        assert "complete" in types
        complete = [e for e in events if e["type"] == "complete"]
        assert len(complete) == 1
        assert "items" in complete[0]["data"]
        assert "llm_calls" in complete[0]["data"]

    def test_items_in_log(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "test.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        events = list(run_single_pipeline(pdf, cfg, client))
        item_events = [e for e in events if e["type"] == "item"]
        assert len(item_events) == 2
        assert "Cordless" in item_events[0]["message"]
        assert "hex bolt" in item_events[1]["message"]

    def test_routing_decision_in_events(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "test.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        events = list(run_single_pipeline(pdf, cfg, client))
        routing = [e for e in events if e["type"] == "routing"]
        assert len(routing) >= 1
        assert "processed" in routing[0]["message"] or "review" in routing[0]["message"]


class TestSingleRunDryRun:
    def test_no_db_rows_written(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "dry.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        list(run_single_pipeline(pdf, cfg, client, commit=False))
        assert not os.path.exists(cfg.database.path)

    def test_no_file_moved(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "stay.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        list(run_single_pipeline(pdf, cfg, client, commit=False))
        assert os.path.isfile(pdf)

    def test_log_events_still_produced(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "log.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        events = list(run_single_pipeline(pdf, cfg, client, commit=False))
        assert len(events) > 0
        assert events[-1]["type"] == "complete"


class TestSingleRunCommit:
    def test_db_rows_written(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "commit.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        list(run_single_pipeline(pdf, cfg, client, commit=True))
        conn = sqlite3.connect(cfg.database.path)
        inv_count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        li_count = conn.execute("SELECT COUNT(*) FROM line_items").fetchone()[0]
        llm_count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        conn.close()
        assert inv_count == 1
        assert li_count >= 1
        assert llm_count >= 1

    def test_file_moved(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "moved.pdf")
        make_test_pdf(pdf)
        client = MockOllamaClient()
        list(run_single_pipeline(pdf, cfg, client, commit=True))
        assert not os.path.isfile(pdf)


class TestSingleRunParseFailure:
    def test_parse_error_events(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "bad.pdf")
        make_test_pdf(pdf)
        client = MockFailingExtractionClient()
        events = list(run_single_pipeline(pdf, cfg, client))
        types = [e["type"] for e in events]
        assert "error" in types
        assert "raw" in types
        assert events[-1]["type"] == "complete"
        assert events[-1]["data"]["status"] == "PARSE_ERROR"


class TestSingleRunConnectionError:
    def test_friendly_error_message(self, cfg: Config):
        pdf = os.path.join(cfg.paths.inbox, "conn.pdf")
        make_test_pdf(pdf)
        client = MockConnectErrorClient()
        events = list(run_single_pipeline(pdf, cfg, client))
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) >= 1
        msg = errors[0]["message"].lower()
        assert "ollama" in msg or "connect" in msg or "refused" in msg
