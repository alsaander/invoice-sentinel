import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fpdf import FPDF

from invoicesentinel.cli import _open_db, run_pipeline
from invoicesentinel.config import Config, load_config
from invoicesentinel.models import LineItem
from invoicesentinel.store import get_invoice_line_items

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_reference_prices():
    with patch("invoicesentinel.cli.load_reference_prices", return_value=[]):
        yield


class MockOllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="llama3:8b"):
        self.base_url = base_url
        self.model = model
        self._call_count = 0

    def generate(self, prompt: str, system: str = None) -> str:
        prompt_lower = prompt.lower()
        self._call_count += 1
        if "product/service" in prompt_lower:
            return (
                FIXTURES_DIR / "extraction_response_1.json"
            ).read_text()
        return (
            FIXTURES_DIR / "reasoning_response_1.json"
        ).read_text()

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
    cfg = Config()
    cfg.paths.inbox = str(tmp_path / "inbox")
    cfg.paths.processed = str(tmp_path / "processed")
    cfg.paths.review_high = str(tmp_path / "review/high")
    cfg.paths.review_moderate = str(tmp_path / "review/moderate")
    cfg.paths.review_extraction_failed = str(tmp_path / "review/extraction_failed")
    cfg.model.name = "test-model"
    for p in vars(cfg.paths).values():
        if isinstance(p, str):
            os.makedirs(p, exist_ok=True)
    cfg.database.path = str(tmp_path / "test.db")
    return cfg


class TestRunPipeline:
    def test_run_pipeline_clears_invoice(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "test_invoice.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client)
            assert summary["counts"]["total"] == 1
            assert summary["counts"]["CLEARED"] == 1
            assert os.path.isfile(
                os.path.join(cfg.paths.processed, "test_invoice.pdf")
            )
            row = conn.execute(
                "SELECT status, moved_to_path FROM invoices WHERE filename = ?",
                ("test_invoice.pdf",),
            ).fetchone()
            assert row is not None
            assert row[0] == "CLEARED"
        finally:
            client.close()
            conn.close()

    def test_run_dry_run_skips_file_moves(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "dry_test.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client, dry_run=True)
            assert summary.get("dry_run") is True
            assert summary["counts"]["total"] == 1
            row = conn.execute(
                "SELECT status FROM invoices WHERE filename = ?",
                ("dry_test.pdf",),
            ).fetchone()
            assert row is not None
            assert row[0] == "PENDING"
            assert not os.path.isfile(
                os.path.join(cfg.paths.processed, "dry_test.pdf")
            )
            assert os.path.isfile(os.path.join(cfg.paths.inbox, "dry_test.pdf"))
        finally:
            client.close()
            conn.close()

    def test_run_line_items_persisted_with_deviation(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "items_test.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            run_pipeline(cfg, conn, client)
            rows = conn.execute(
                "SELECT id FROM invoices WHERE filename = ?",
                ("items_test.pdf",),
            ).fetchall()
            assert len(rows) == 1
            inv_id = rows[0][0]
            items = get_invoice_line_items(conn, inv_id)
            assert len(items) == 2
            assert items[0].description == "Cordless 18V electric drill"
            assert items[0].deviation_pct is not None
            assert items[0].category == "Electronics"
        finally:
            client.close()
            conn.close()

    def test_run_dry_populates_db(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "dry_db.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            run_pipeline(cfg, conn, client, dry_run=True)
            rows = conn.execute(
                "SELECT id FROM invoices WHERE filename = ?",
                ("dry_db.pdf",),
            ).fetchall()
            assert len(rows) == 1
            inv_id = rows[0][0]
            items = get_invoice_line_items(conn, inv_id)
            assert len(items) >= 1
            assert items[0].deviation_pct is not None
        finally:
            client.close()
            conn.close()

    def test_empty_inbox(self, cfg: Config):
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client)
            assert summary["counts"]["total"] == 0
        finally:
            client.close()
            conn.close()


class TestRunSummary:
    def test_summary_has_top_items(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "summary_test.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client)
            assert "top_line_items" in summary
            assert "run_timestamp" in summary
            assert "counts" in summary
            assert len(summary["top_line_items"]) > 0
        finally:
            client.close()
            conn.close()


class TestWatchMode:
    def test_watch_picks_up_new_file(self, cfg: Config):
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client)
            assert summary["counts"]["total"] == 0
            make_test_pdf(os.path.join(cfg.paths.inbox, "new_watch.pdf"))
            summary2 = run_pipeline(cfg, conn, client)
            assert summary2["counts"]["total"] == 1
        finally:
            client.close()
            conn.close()

    def test_watch_routes_second_batch(self, cfg: Config):
        make_test_pdf(os.path.join(cfg.paths.inbox, "batch1.pdf"))
        conn = _open_db(cfg)
        client = MockOllamaClient()
        try:
            summary = run_pipeline(cfg, conn, client)
            assert summary["counts"]["total"] == 1
            make_test_pdf(os.path.join(cfg.paths.inbox, "batch2.pdf"))
            summary2 = run_pipeline(cfg, conn, client)
            assert summary2["counts"]["total"] == 1
        finally:
            client.close()
            conn.close()


class TestNFR1RemoteGuard:
    def test_remote_host_raises_clear_error(self, tmp_path):
        cfg_path = tmp_path / "remote_config.yaml"
        db_path = str(tmp_path / "test.db")
        cfg_path.write_text(
            "model:\n"
            '  name: "llama3:8b"\n'
            '  ollama_host: "http://remote.example.com:11434"\n'
            f"database:\n"
            f'  path: "{db_path}"\n'
        )
        cfg = load_config(str(cfg_path))
        from invoicesentinel.llm_client import OllamaClient
        with pytest.raises(RuntimeError) as excinfo:
            OllamaClient(base_url=cfg.model.ollama_host, model=cfg.model.name)
        msg = str(excinfo.value)
        assert "NFR1" in msg
        assert "remote" in msg.lower()
        assert "ALLOW_REMOTE_LLM" in msg

    def test_remote_host_allowed_with_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALLOW_REMOTE_LLM", "true")
        cfg_path = tmp_path / "remote_config.yaml"
        cfg_path.write_text(
            "model:\n"
            '  name: "llama3:8b"\n'
            '  ollama_host: "http://remote.example.com:11434"\n'
            f"database:\n"
            f'  path: "{tmp_path}/test.db"\n'
        )
        cfg = load_config(str(cfg_path))
        from invoicesentinel.llm_client import OllamaClient
        client = OllamaClient(base_url=cfg.model.ollama_host, model=cfg.model.name)
        client.close()
