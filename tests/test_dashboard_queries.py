import sqlite3
from datetime import datetime, timezone

import pytest

from invoicesentinel.dashboard_queries import (
    get_cleared_invoices,
    get_llm_calls_for_line_item,
    get_review_invoices,
    set_analyst_verdict,
)
from invoicesentinel.models import (
    SCHEMA_SQL,
    insert_llm_call,
    insert_line_item,
    update_invoice_status,
)
from invoicesentinel.models import Invoice, LineItem, LlmCall


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def _insert_invoice(conn: sqlite3.Connection, filename: str, status: str) -> Invoice:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO invoices (filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status) VALUES (?, ?, ?, 100, 'test', ?)",
        (filename, f"sha256_{filename}", now, status),
    )
    conn.commit()
    inv = Invoice(
        id=cur.lastrowid,
        filename=filename,
        file_sha256=f"sha256_{filename}",
        received_at=now,
        raw_text_chars=100,
        extraction_method="test",
        status=status,
    )
    return inv


def _insert_line_item(
    conn: sqlite3.Connection, invoice_id: int, description: str, deviation_pct: float
) -> LineItem:
    now = datetime.now(timezone.utc).isoformat()
    li = LineItem(
        invoice_id=invoice_id,
        quantity=1.0,
        unit_price=100.0,
        currency="USD",
        description=description,
        category="Test",
        est_market_low=50.0,
        est_market_high=80.0,
        deviation_pct=deviation_pct,
        severity="HIGH" if abs(deviation_pct) > 200 else "MODERATE",
        reference_source="manual",
        justification=f"Test justification for {description}",
    )
    return insert_line_item(conn, li)


class TestGetReviewInvoices:
    def test_review_invoices_sorted_by_max_deviation(self, conn: sqlite3.Connection):
        inv1 = _insert_invoice(conn, "moderate.pdf", "MANUAL_REVIEW_LOW_PRIORITY")
        _insert_line_item(conn, inv1.id, "Low item", 50.0)

        inv2 = _insert_invoice(conn, "high.pdf", "MANUAL_REVIEW")
        _insert_line_item(conn, inv2.id, "High item", 250.0)

        inv3 = _insert_invoice(conn, "medium.pdf", "MANUAL_REVIEW")
        _insert_line_item(conn, inv3.id, "Medium item", 150.0)

        results = get_review_invoices(conn)
        assert len(results) == 3
        assert results[0].id == inv2.id  # highest deviation first
        assert results[1].id == inv3.id  # medium deviation next

    def test_cleared_invoices_not_in_review(self, conn: sqlite3.Connection):
        _insert_invoice(conn, "cleared.pdf", "CLEARED")
        results = get_review_invoices(conn)
        assert len(results) == 0

    def test_pending_not_in_review(self, conn: sqlite3.Connection):
        _insert_invoice(conn, "pending.pdf", "PENDING")
        results = get_review_invoices(conn)
        assert len(results) == 0

    def test_empty_db_returns_empty(self, conn: sqlite3.Connection):
        results = get_review_invoices(conn)
        assert results == []

    def test_multiple_high_items_same_invoice(self, conn: sqlite3.Connection):
        inv = _insert_invoice(conn, "multi.pdf", "MANUAL_REVIEW")
        _insert_line_item(conn, inv.id, "High item", 250.0)
        _insert_line_item(conn, inv.id, "Normal item", 10.0)
        results = get_review_invoices(conn)
        assert len(results) == 1
        assert results[0].id == inv.id


class TestGetClearedInvoices:
    def test_returns_cleared_only(self, conn: sqlite3.Connection):
        _insert_invoice(conn, "cleared1.pdf", "CLEARED")
        _insert_invoice(conn, "cleared2.pdf", "CLEARED")
        _insert_invoice(conn, "review.pdf", "MANUAL_REVIEW")
        results = get_cleared_invoices(conn)
        assert len(results) == 2
        assert all(i.status == "CLEARED" for i in results)

    def test_empty_returns_empty(self, conn: sqlite3.Connection):
        assert get_cleared_invoices(conn) == []


class TestSetAnalystVerdict:
    def test_write_and_read_back(self, conn: sqlite3.Connection):
        inv = _insert_invoice(conn, "test.pdf", "MANUAL_REVIEW")
        li = _insert_line_item(conn, inv.id, "Test item", 250.0)
        set_analyst_verdict(conn, li.id, "REVIEWED_ESCALATE")
        row = conn.execute(
            "SELECT analyst_verdict FROM line_items WHERE id = ?", (li.id,)
        ).fetchone()
        assert row[0] == "REVIEWED_ESCALATE"

    def test_multiple_verdicts(self, conn: sqlite3.Connection):
        inv = _insert_invoice(conn, "test.pdf", "MANUAL_REVIEW")
        li1 = _insert_line_item(conn, inv.id, "Item 1", 250.0)
        li2 = _insert_line_item(conn, inv.id, "Item 2", 150.0)
        set_analyst_verdict(conn, li1.id, "REVIEWED_OK")
        set_analyst_verdict(conn, li2.id, "REVIEWED_ESCALATE")
        rows = conn.execute(
            "SELECT id, analyst_verdict FROM line_items ORDER BY id"
        ).fetchall()
        assert dict(rows) == {li1.id: "REVIEWED_OK", li2.id: "REVIEWED_ESCALATE"}

    def test_empty_on_fresh_insert(self, conn: sqlite3.Connection):
        inv = _insert_invoice(conn, "test.pdf", "MANUAL_REVIEW")
        li = _insert_line_item(conn, inv.id, "Test item", 250.0)
        row = conn.execute(
            "SELECT analyst_verdict FROM line_items WHERE id = ?", (li.id,)
        ).fetchone()
        assert row[0] is None


class TestGetLlmCallsForLineItem:
    def test_returns_calls_for_line_item(self, conn: sqlite3.Connection):
        inv = _insert_invoice(conn, "test.pdf", "MANUAL_REVIEW")
        li = _insert_line_item(conn, inv.id, "Test item", 250.0)
        call = LlmCall(
            invoice_id=inv.id,
            line_item_id=li.id,
            call_type="price_estimate",
            prompt_version="price_estimate_v1",
            model="test-model",
            raw_response='{"deviation": 250}',
            latency_ms=150,
        )
        insert_llm_call(conn, call)
        calls = get_llm_calls_for_line_item(conn, li.id)
        assert len(calls) == 1
        assert calls[0]["prompt_version"] == "price_estimate_v1"
        assert calls[0]["model"] == "test-model"
        assert calls[0]["raw_response"] == '{"deviation": 250}'
        assert calls[0]["latency_ms"] == 150

    def test_no_calls_returns_empty(self, conn: sqlite3.Connection):
        assert get_llm_calls_for_line_item(conn, 999) == []
