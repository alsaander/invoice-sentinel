import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Invoice:
    id: Optional[int] = None
    filename: str = ""
    file_sha256: str = ""
    received_at: str = ""
    raw_text_chars: int = 0
    extraction_method: str = ""
    status: str = ""
    moved_to_path: Optional[str] = None
    processed_at: Optional[str] = None


@dataclass
class LineItem:
    id: Optional[int] = None
    invoice_id: Optional[int] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    currency: str = ""
    description: str = ""
    category: str = ""
    category_raw: Optional[str] = None
    est_market_low: Optional[float] = None
    est_market_high: Optional[float] = None
    deviation_pct: Optional[float] = None
    severity: str = ""
    reference_source: str = ""
    reference_confidence: str = ""
    justification: str = ""
    analyst_verdict: Optional[str] = None
    grounded: str = "true"
    created_at: str = ""


@dataclass
class LlmCall:
    id: Optional[int] = None
    invoice_id: Optional[int] = None
    line_item_id: Optional[int] = None
    call_type: str = ""
    prompt_version: str = ""
    model: str = ""
    raw_response: str = ""
    latency_ms: int = 0
    created_at: str = ""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    file_sha256 TEXT UNIQUE NOT NULL,
    received_at TEXT NOT NULL,
    raw_text_chars INTEGER NOT NULL DEFAULT 0,
    extraction_method TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    moved_to_path TEXT,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    quantity REAL,
    unit_price REAL,
    currency TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    category_raw TEXT,
    est_market_low REAL,
    est_market_high REAL,
    deviation_pct REAL,
    severity TEXT NOT NULL DEFAULT '',
    reference_source TEXT NOT NULL DEFAULT '',
    reference_confidence TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    analyst_verdict TEXT,
    grounded TEXT NOT NULL DEFAULT 'true',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER REFERENCES invoices(id),
    line_item_id INTEGER REFERENCES line_items(id),
    call_type TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    raw_response TEXT NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_line_items_invoice_id ON line_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_invoice_id ON llm_calls(invoice_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_line_item_id ON llm_calls(line_item_id);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def update_invoice_status(
    conn: sqlite3.Connection,
    invoice_id: int,
    status: str,
    moved_to_path: Optional[str] = None,
    processed_at: Optional[str] = None,
    commit: bool = True,
) -> None:
    now = processed_at or (datetime.now(timezone.utc).isoformat())
    conn.execute(
        """UPDATE invoices SET status=?, moved_to_path=?, processed_at=?
           WHERE id=?""",
        (status, moved_to_path, now, invoice_id),
    )
    if commit:
        conn.commit()


def update_line_item(conn: sqlite3.Connection, item: LineItem, commit: bool = True) -> LineItem:
    conn.execute(
        """UPDATE line_items SET quantity=?, unit_price=?, currency=?,
           description=?, category=?, category_raw=?,
           est_market_low=?, est_market_high=?,
           deviation_pct=?, severity=?, reference_source=?,
           reference_confidence=?, justification=?, analyst_verdict=?, grounded=?
           WHERE id=?""",
        (item.quantity, item.unit_price, item.currency,
         item.description, item.category, item.category_raw,
         item.est_market_low, item.est_market_high,
         item.deviation_pct, item.severity, item.reference_source,
         item.reference_confidence, item.justification, item.analyst_verdict, item.grounded,
         item.id),
    )
    if commit:
        conn.commit()
    return item


def insert_line_item(conn: sqlite3.Connection, item: LineItem, commit: bool = True) -> LineItem:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO line_items (invoice_id, quantity, unit_price, currency,
                                   description, category, category_raw,
                                   est_market_low, est_market_high,
                                   deviation_pct, severity, reference_source,
                                   reference_confidence, justification, analyst_verdict, grounded,
                                   created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (item.invoice_id, item.quantity, item.unit_price, item.currency,
         item.description, item.category, item.category_raw,
         item.est_market_low, item.est_market_high,
         item.deviation_pct, item.severity, item.reference_source,
         item.reference_confidence, item.justification, item.analyst_verdict, item.grounded,
         now),
    )
    item.id = cur.lastrowid
    item.created_at = now
    if commit:
        conn.commit()
    return item


def insert_llm_call(conn: sqlite3.Connection, call: LlmCall, commit: bool = True) -> LlmCall:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO llm_calls (invoice_id, line_item_id, call_type,
                                  prompt_version, model, raw_response,
                                  latency_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (call.invoice_id, call.line_item_id, call.call_type,
         call.prompt_version, call.model, call.raw_response,
         call.latency_ms, now),
    )
    call.id = cur.lastrowid
    call.created_at = now
    if commit:
        conn.commit()
    return call
