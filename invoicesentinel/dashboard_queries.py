import sqlite3
from typing import Dict, List, Optional

from invoicesentinel.models import Invoice, LineItem, LlmCall
from invoicesentinel.store import get_invoice_line_items


def get_review_invoices(conn: sqlite3.Connection) -> List[Invoice]:
    rows = conn.execute(
        "SELECT id, filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status, moved_to_path, processed_at "
        "FROM invoices "
        "WHERE status IN ('MANUAL_REVIEW', 'MANUAL_REVIEW_LOW_PRIORITY') "
        "ORDER BY status, id"
    ).fetchall()
    invoices = [Invoice(*r) for r in rows]
    result: List[Invoice] = []
    for inv in invoices:
        items = get_invoice_line_items(conn, inv.id)
        max_dev = max(
            (abs(li.deviation_pct) for li in items if li.deviation_pct is not None),
            default=0.0,
        )
        result.append((max_dev, inv))
    result.sort(key=lambda x: (-x[0], x[1].status, x[1].id))
    return [inv for _, inv in result]


def get_cleared_invoices(conn: sqlite3.Connection) -> List[Invoice]:
    rows = conn.execute(
        "SELECT id, filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status, moved_to_path, processed_at "
        "FROM invoices WHERE status = 'CLEARED' ORDER BY id"
    ).fetchall()
    return [Invoice(*r) for r in rows]


def get_flagged_line_items(conn: sqlite3.Connection, invoice_id: int) -> List[LineItem]:
    return get_invoice_line_items(conn, invoice_id)


def set_analyst_verdict(
    conn: sqlite3.Connection, line_item_id: int, verdict: str
) -> None:
    conn.execute(
        "UPDATE line_items SET analyst_verdict = ? WHERE id = ?",
        (verdict, line_item_id),
    )
    conn.commit()


def get_llm_calls_for_line_item(
    conn: sqlite3.Connection, line_item_id: int
) -> List[Dict]:
    rows = conn.execute(
        "SELECT prompt_version, model, raw_response, latency_ms "
        "FROM llm_calls WHERE line_item_id = ? ORDER BY id",
        (line_item_id,),
    ).fetchall()
    return [
        {
            "prompt_version": r[0],
            "model": r[1],
            "raw_response": r[2],
            "latency_ms": r[3],
        }
        for r in rows
    ]


def get_invoice_by_id(conn: sqlite3.Connection, invoice_id: int) -> Optional[Invoice]:
    row = conn.execute(
        "SELECT id, filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status, moved_to_path, processed_at "
        "FROM invoices WHERE id = ?",
        (invoice_id,),
    ).fetchone()
    if row is None:
        return None
    return Invoice(*row)
