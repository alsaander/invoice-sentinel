import sqlite3

from invoicesentinel.models import SCHEMA_SQL, create_tables


def test_create_tables_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]
    assert "invoices" in tables
    assert "line_items" in tables
    assert "llm_calls" in tables


def test_invoices_columns():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    cur = conn.execute("PRAGMA table_info(invoices)")
    cols = {row[1]: row[2] for row in cur.fetchall()}
    assert cols["id"] == "INTEGER"
    assert cols["filename"] == "TEXT"
    assert cols["file_sha256"] == "TEXT"
    assert cols["received_at"] == "TEXT"
    assert cols["raw_text_chars"] == "INTEGER"
    assert cols["extraction_method"] == "TEXT"
    assert cols["status"] == "TEXT"
    assert cols["moved_to_path"] == "TEXT"
    assert cols["processed_at"] == "TEXT"


def test_line_items_columns():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    cur = conn.execute("PRAGMA table_info(line_items)")
    cols = {row[1]: row[2] for row in cur.fetchall()}
    expected = {
        "id": "INTEGER",
        "invoice_id": "INTEGER",
        "quantity": "REAL",
        "unit_price": "REAL",
        "currency": "TEXT",
        "description": "TEXT",
        "category": "TEXT",
        "category_raw": "TEXT",
        "est_market_low": "REAL",
        "est_market_high": "REAL",
        "deviation_pct": "REAL",
        "severity": "TEXT",
        "reference_source": "TEXT",
        "justification": "TEXT",
        "analyst_verdict": "TEXT",
        "created_at": "TEXT",
    }
    for col_name, col_type in expected.items():
        assert col_name in cols, f"Missing column: {col_name}"
        assert cols[col_name] == col_type, f"Column {col_name}: expected {col_type}, got {cols[col_name]}"


def test_llm_calls_columns():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    cur = conn.execute("PRAGMA table_info(llm_calls)")
    cols = {row[1]: row[2] for row in cur.fetchall()}
    expected = {
        "id": "INTEGER",
        "invoice_id": "INTEGER",
        "line_item_id": "INTEGER",
        "call_type": "TEXT",
        "prompt_version": "TEXT",
        "model": "TEXT",
        "raw_response": "TEXT",
        "latency_ms": "INTEGER",
        "created_at": "TEXT",
    }
    for col_name, col_type in expected.items():
        assert col_name in cols, f"Missing column: {col_name}"
        assert cols[col_name] == col_type, f"Column {col_name}: expected {col_type}, got {cols[col_name]}"


def test_file_sha256_unique():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    conn.execute(
        "INSERT INTO invoices (filename, file_sha256, received_at) VALUES (?, ?, ?)",
        ("a.pdf", "abc123", "2025-01-01T00:00:00"),
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invoices (filename, file_sha256, received_at) VALUES (?, ?, ?)",
            ("b.pdf", "abc123", "2025-01-01T00:00:00"),
        )


def test_create_tables_idempotent():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    create_tables(conn)
    cur = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
    assert cur.fetchone()[0] == 3
