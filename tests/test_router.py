import os
import sqlite3
from pathlib import Path

import pytest

from invoicesentinel.models import create_tables
from invoicesentinel.router import route_invoice, route_invoices
from invoicesentinel.store import compute_run_summary, get_pending_invoice_ids


def _insert_invoice(conn, **kw) -> int:
    defaults = dict(
        filename="test.pdf",
        file_sha256="abc",
        received_at="2025-01-01T00:00:00",
        raw_text_chars=100,
        extraction_method="pdfplumber",
        status="PENDING",
    )
    defaults.update(kw)
    cur = conn.execute(
        """INSERT INTO invoices (filename, file_sha256, received_at,
                                 raw_text_chars, extraction_method, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (defaults["filename"], defaults["file_sha256"], defaults["received_at"],
         defaults["raw_text_chars"], defaults["extraction_method"], defaults["status"]),
    )
    conn.commit()
    return cur.lastrowid


def _insert_line_item(conn, invoice_id: int, **kw) -> int:
    defaults = dict(
        severity="NORMAL", currency="USD", description="Item",
        quantity=1, unit_price=10.0, category="Otro",
    )
    defaults.update(kw)
    cur = conn.execute(
        """INSERT INTO line_items (invoice_id, quantity, unit_price, currency,
                                   description, category, severity, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, defaults["quantity"], defaults["unit_price"],
         defaults["currency"], defaults["description"], defaults["category"],
         defaults["severity"], "2025-01-01T00:00:00"),
    )
    conn.commit()
    return cur.lastrowid


def _create_inbox_pdf(cfg, filename: str = "test.pdf", content: bytes = b"dummy pdf content") -> str:
    path = os.path.join(cfg.paths.inbox, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()


class TestRouteInvoice:
    def test_high_severity_moves_to_review_high(self, cfg, db_conn):
        """FR4: HIGH line item → MANUAL_REVIEW, moved to review/high/"""
        iid = _insert_invoice(db_conn, filename="high_inv.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="HIGH")
        _create_inbox_pdf(cfg, "high_inv.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv is not None
        assert inv.status == "MANUAL_REVIEW"
        assert inv.moved_to_path is not None
        assert cfg.paths.review_high in inv.moved_to_path
        assert not os.path.exists(os.path.join(cfg.paths.inbox, "high_inv.pdf"))
        assert os.path.exists(inv.moved_to_path)

    def test_cleared_invoice_moves_to_processed(self, cfg, db_conn):
        """FR4: All NORMAL → CLEARED, moved to processed/"""
        iid = _insert_invoice(db_conn, filename="clear_inv.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL")
        _create_inbox_pdf(cfg, "clear_inv.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv is not None
        assert inv.status == "CLEARED"
        assert cfg.paths.processed in inv.moved_to_path
        assert os.path.exists(inv.moved_to_path)

    def test_moderate_without_high_goes_to_review_moderate(self, cfg, db_conn):
        """FR4.2: Pure MODERATE (no HIGH, no UNKNOWN) → LOW_PRIORITY"""
        iid = _insert_invoice(db_conn, filename="mod_inv.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="MODERATE")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL")
        _create_inbox_pdf(cfg, "mod_inv.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv is not None
        assert inv.status == "MANUAL_REVIEW_LOW_PRIORITY"
        assert cfg.paths.review_moderate in inv.moved_to_path

    def test_unknown_currency_triggers_low_priority(self, cfg, db_conn):
        """All NORMAL but currency=UNKNOWN → MANUAL_REVIEW_LOW_PRIORITY"""
        iid = _insert_invoice(db_conn, filename="unk_inv.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL", currency="UNKNOWN")
        _create_inbox_pdf(cfg, "unk_inv.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW_LOW_PRIORITY"
        assert cfg.paths.review_moderate in inv.moved_to_path

    def test_already_routed_invoice_skipped(self, cfg, db_conn):
        """idempotency: already-routed invoice is not moved again"""
        iid = _insert_invoice(db_conn, filename="done.pdf",
                              status="CLEARED", moved_to_path="/already/moved.pdf")
        _create_inbox_pdf(cfg, "done.pdf")
        inv = route_invoice(db_conn, iid, cfg)
        assert inv is not None
        assert inv.status == "CLEARED"
        assert os.path.exists(os.path.join(cfg.paths.inbox, "done.pdf")), "file should NOT have been moved"

    def test_nonexistent_invoice_returns_none(self, cfg, db_conn):
        inv = route_invoice(db_conn, 9999, cfg)
        assert inv is None

    def test_high_overrides_moderate(self, cfg, db_conn):
        """HIGH + MODERATE → still MANUAL_REVIEW (HIGH takes precedence)"""
        iid = _insert_invoice(db_conn, filename="mixed.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="HIGH")
        _insert_line_item(db_conn, invoice_id=iid, severity="MODERATE")
        _create_inbox_pdf(cfg, "mixed.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW"
        assert cfg.paths.review_high in inv.moved_to_path

    def test_unknown_severity_triggers_low_priority(self, cfg, db_conn):
        """FR4.2: UNKNOWN severity (reasoning failed) → review, never CLEARED.
        Regression: previously fell through to else/CLEARED because UNKNOWN
        wasn't checked before MODERATE."""
        iid = _insert_invoice(db_conn, filename="unknown_sev.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNKNOWN", currency="USD")
        _create_inbox_pdf(cfg, "unknown_sev.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW_LOW_PRIORITY"
        assert cfg.paths.review_moderate in inv.moved_to_path
        assert inv.status != "CLEARED"

    def test_unknown_severity_overrides_moderate(self, cfg, db_conn):
        """FR4.2: UNKNOWN + MODERATE (no HIGH) → UNKNOWN takes
        precedence over MODERATE in routing order."""
        iid = _insert_invoice(db_conn, filename="mixed_unk_mod.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNKNOWN", currency="USD")
        _insert_line_item(db_conn, invoice_id=iid, severity="MODERATE", currency="USD")
        _create_inbox_pdf(cfg, "mixed_unk_mod.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW_LOW_PRIORITY"
        assert cfg.paths.review_moderate in inv.moved_to_path

    def test_unknown_severity_does_not_clear_with_normal(self, cfg, db_conn):
        """FR4.2: All items NORMAL except one UNKNOWN → routed to review,
        not CLEARED."""
        iid = _insert_invoice(db_conn, filename="mixed_normal_unk.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL", currency="USD")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNKNOWN", currency="USD")
        _create_inbox_pdf(cfg, "mixed_normal_unk.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW_LOW_PRIORITY"
        assert cfg.paths.review_moderate in inv.moved_to_path
        assert inv.status != "CLEARED"

    def test_ungrounded_severity_triggers_manual_review(self, cfg, db_conn):
        """FR4.2: UNGROUNDED severity → MANUAL_REVIEW, review/extraction_failed/"""
        iid = _insert_invoice(db_conn, filename="ungrounded.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNGROUNDED", currency="USD")
        _create_inbox_pdf(cfg, "ungrounded.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW"
        assert cfg.paths.review_extraction_failed in inv.moved_to_path

    def test_ungrounded_overrides_unknown(self, cfg, db_conn):
        """FR4.2: UNGROUNDED takes priority over UNKNOWN in routing"""
        iid = _insert_invoice(db_conn, filename="ungrounded_unk.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNGROUNDED", currency="USD")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNKNOWN", currency="USD")
        _create_inbox_pdf(cfg, "ungrounded_unk.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW"
        assert cfg.paths.review_extraction_failed in inv.moved_to_path

    def test_ungrounded_does_not_override_high(self, cfg, db_conn):
        """FR4.2: HIGH takes precedence over UNGROUNDED"""
        iid = _insert_invoice(db_conn, filename="high_ungrounded.pdf")
        _insert_line_item(db_conn, invoice_id=iid, severity="HIGH", currency="USD")
        _insert_line_item(db_conn, invoice_id=iid, severity="UNGROUNDED", currency="USD")
        _create_inbox_pdf(cfg, "high_ungrounded.pdf")

        inv = route_invoice(db_conn, iid, cfg)
        assert inv.status == "MANUAL_REVIEW"
        assert cfg.paths.review_high in inv.moved_to_path


class TestRouteInvoices:
    def test_routes_multiple_invoices(self, cfg, db_conn):
        iid1 = _insert_invoice(db_conn, filename="a.pdf", file_sha256="a")
        iid2 = _insert_invoice(db_conn, filename="b.pdf", file_sha256="b")
        _insert_line_item(db_conn, invoice_id=iid1, severity="NORMAL")
        _insert_line_item(db_conn, invoice_id=iid2, severity="HIGH")
        _create_inbox_pdf(cfg, "a.pdf")
        _create_inbox_pdf(cfg, "b.pdf")

        results = route_invoices(db_conn, [iid1, iid2], cfg)
        assert len(results) == 2
        statuses = {r.id: r.status for r in results}
        assert statuses[iid1] == "CLEARED"
        assert statuses[iid2] == "MANUAL_REVIEW"


class TestGetPendingInvoiceIds:
    def test_returns_only_pending(self, db_conn):
        _insert_invoice(db_conn, filename="a.pdf", file_sha256="a", status="PENDING")
        _insert_invoice(db_conn, filename="b.pdf", file_sha256="b", status="CLEARED")
        _insert_invoice(db_conn, filename="c.pdf", file_sha256="c", status="MANUAL_REVIEW")
        pending = get_pending_invoice_ids(db_conn)
        assert len(pending) == 1
        assert pending[0] > 0


class TestComputeRunSummary:
    def test_summary_counts(self, cfg, db_conn):
        iid1 = _insert_invoice(db_conn, filename="a.pdf", file_sha256="a", status="CLEARED")
        iid2 = _insert_invoice(db_conn, filename="b.pdf", file_sha256="b", status="MANUAL_REVIEW")
        iid3 = _insert_invoice(db_conn, filename="c.pdf", file_sha256="c", status="EXTRACTION_FAILED")

        summary = compute_run_summary(db_conn, [iid1, iid2, iid3])
        assert summary["counts"]["total"] == 3
        assert summary["counts"]["CLEARED"] == 1
        assert summary["counts"]["MANUAL_REVIEW"] == 1
        assert summary["counts"]["EXTRACTION_FAILED"] == 1

    def test_top_line_items(self, cfg, db_conn):
        iid = _insert_invoice(db_conn, filename="a.pdf", file_sha256="a", status="MANUAL_REVIEW")
        _insert_line_item(db_conn, invoice_id=iid, severity="NORMAL", description="Cheap item")
        li2 = _insert_line_item(db_conn, invoice_id=iid, severity="HIGH", description="Expensive item")
        db_conn.execute(
            "UPDATE line_items SET deviation_pct=? WHERE id=?",
            (50.0, li2 - 1),
        )
        db_conn.execute(
            "UPDATE line_items SET deviation_pct=? WHERE id=?",
            (8981.8, li2),
        )
        db_conn.commit()

        summary = compute_run_summary(db_conn, [iid], top_n=10)
        assert len(summary["top_line_items"]) == 2
        top = summary["top_line_items"]
        assert top[0]["deviation_pct"] == 8981.8
        assert top[0]["severity"] == "HIGH"
        assert top[1]["deviation_pct"] == 50.0
