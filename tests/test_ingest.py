import os
import sqlite3
from unittest.mock import patch

import pytest

from invoicesentinel.ingest import (
    _is_duplicate,
    compute_sha256,
    extract_text,
    process_inbox,
    process_single_pdf,
)


class TestComputeSha256:
    def test_sha256_known_file(self, tmp_path):
        p = tmp_path / "test.pdf"
        p.write_bytes(b"hello world")
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert compute_sha256(str(p)) == expected

    def test_sha256_different_file_different_hash(self, tmp_path):
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_bytes(b"content a")
        b.write_bytes(b"content b")
        assert compute_sha256(str(a)) != compute_sha256(str(b))


class TestExtractText:
    def test_extract_text_valid_pdf(self, tmp_path):
        path = str(tmp_path / "valid.pdf")
        text = "Factura de prueba\nTaladro electrico\nCantidad: 10\nPrecio: 45.50 USD"
        from tests.conftest import make_text_pdf
        make_text_pdf(path, text)
        result, method = extract_text(path, min_text_chars=10)
        assert "Taladro" in result
        assert method == "pdfplumber"

    def test_extract_text_empty_pdf(self, tmp_path):
        path = str(tmp_path / "empty.pdf")
        from tests.conftest import make_empty_pdf
        make_empty_pdf(path)
        result, method = extract_text(path, min_text_chars=10)
        assert result == ""
        assert method == "failed"

    def test_extract_text_below_min_chars(self, tmp_path):
        path = str(tmp_path / "short.pdf")
        from tests.conftest import make_text_pdf
        make_text_pdf(path, "Hi")
        result, method = extract_text(path, min_text_chars=100)
        assert result == ""
        assert method == "failed"

    def test_extract_text_fallback_to_pypdf2(self, tmp_path):
        path = str(tmp_path / "fallback.pdf")
        from tests.conftest import make_text_pdf
        make_text_pdf(path, "PyPDF2 fallback test content here")

        with patch("invoicesentinel.ingest.pdfplumber.open", side_effect=Exception("pdfplumber crash")):
            result, method = extract_text(path, min_text_chars=5)
            assert "PyPDF2" in result
            assert method == "pypdf2"


class TestIsDuplicate:
    def test_no_duplicate(self, db_conn):
        assert _is_duplicate(db_conn, "nonexistent_hash") is False

    def test_duplicate_found(self, db_conn):
        db_conn.execute(
            "INSERT INTO invoices (filename, file_sha256, received_at) VALUES (?, ?, ?)",
            ("test.pdf", "abc123", "2025-01-01T00:00:00"),
        )
        db_conn.commit()
        assert _is_duplicate(db_conn, "abc123") is True


class TestProcessSinglePdf:
    def test_valid_pdf_stores_invoice(self, tmp_path, cfg, db_conn):
        path = str(tmp_path / "invoice1.pdf")
        from tests.conftest import make_text_pdf
        make_text_pdf(path, "Valid invoice with plenty of text for extraction purposes here - over fifty chars")
        inv = process_single_pdf(path, cfg, db_conn)
        assert inv is not None
        assert inv.id is not None
        assert inv.filename == "invoice1.pdf"
        assert inv.extraction_method == "pdfplumber"
        assert inv.status == "PENDING"
        assert inv.file_sha256 is not None
        assert inv.raw_text_chars > 0

        row = db_conn.execute("SELECT * FROM invoices WHERE id = ?", (inv.id,)).fetchone()
        assert row is not None
        assert row[1] == "invoice1.pdf"

    def test_empty_pdf_marked_failed_and_moved(self, tmp_path, cfg, db_conn):
        path = str(tmp_path / "scanned.pdf")
        from tests.conftest import make_empty_pdf
        make_empty_pdf(path)
        inv = process_single_pdf(path, cfg, db_conn)
        assert inv is not None
        assert inv.status == "EXTRACTION_FAILED"
        assert inv.extraction_method == "failed"
        assert inv.raw_text_chars == 0
        assert not os.path.exists(path), "source PDF should be moved"
        assert os.path.exists(inv.moved_to_path)

        dest_dir = cfg.paths.review_extraction_failed
        assert os.path.dirname(inv.moved_to_path) == dest_dir

    def test_duplicate_file_skipped(self, tmp_path, cfg, db_conn):
        path = str(tmp_path / "dup.pdf")
        dst = str(tmp_path / "dup_copy.pdf")
        from tests.conftest import make_text_pdf
        make_text_pdf(path, "Some text content for dedup test that is long enough to extract properly")
        import shutil
        shutil.copy(path, dst)
        inv1 = process_single_pdf(path, cfg, db_conn)
        assert inv1 is not None

        inv2 = process_single_pdf(dst, cfg, db_conn)
        assert inv2 is None, "duplicate should return None"

    def test_pdf_with_same_hash_skipped_even_if_renamed(self, tmp_path, cfg, db_conn):
        content = b"same content same hash"
        p1 = tmp_path / "original_name.pdf"
        p2 = tmp_path / "renamed_copy.pdf"
        p1.write_bytes(content)
        p2.write_bytes(content)

        inv1 = process_single_pdf(str(p1), cfg, db_conn)
        assert inv1 is not None

        inv2 = process_single_pdf(str(p2), cfg, db_conn)
        assert inv2 is None

    def test_extraction_method_and_raw_text_chars_correct(self, tmp_path, cfg, db_conn):
        path = str(tmp_path / "detailed.pdf")
        text = "Line item 1: Widget A x10 @ 5.00 USD\nLine item 2: Widget B x5 @ 12.50 USD - this text is long enough to exceed fifty characters threshold"
        from tests.conftest import make_text_pdf
        make_text_pdf(path, text)
        inv = process_single_pdf(path, cfg, db_conn)
        assert inv.extraction_method == "pdfplumber"
        assert inv.raw_text_chars == len(text)

    def test_pypdf2_fallback_on_process(self, tmp_path, cfg, db_conn):
        path = str(tmp_path / "fallback.pdf")
        from tests.conftest import make_text_pdf
        make_text_pdf(path, "Content readable by PyPDF2 when pdfplumber fails - this line is well above fifty chars long now")

        with patch("invoicesentinel.ingest.pdfplumber.open", side_effect=Exception("pdfplumber crash")):
            inv = process_single_pdf(path, cfg, db_conn)
        assert inv is not None
        assert inv.extraction_method == "pypdf2"
        assert inv.status == "PENDING"
        assert inv.raw_text_chars > 0


class TestProcessInbox:
    def test_processes_all_pdfs(self, cfg, db_conn):
        from tests.conftest import make_text_pdf
        make_text_pdf(os.path.join(cfg.paths.inbox, "a.pdf"), "Invoice A content here with enough text to pass the fifty char threshold for extraction")
        make_text_pdf(os.path.join(cfg.paths.inbox, "b.pdf"), "Invoice B content here with enough text to pass the fifty char threshold for extraction")
        make_empty = __import__("tests.conftest", fromlist=["make_empty_pdf"]).make_empty_pdf
        make_empty(os.path.join(cfg.paths.inbox, "c_scanned.pdf"))

        results = process_inbox(cfg, db_conn)
        assert len(results) == 3

        statuses = {r.filename: r.status for r in results}
        assert statuses["a.pdf"] == "PENDING"
        assert statuses["b.pdf"] == "PENDING"
        assert statuses["c_scanned.pdf"] == "EXTRACTION_FAILED"

    def test_skips_non_pdf_files(self, cfg, db_conn):
        from tests.conftest import make_text_pdf
        make_text_pdf(os.path.join(cfg.paths.inbox, "invoice.pdf"), "content")
        with open(os.path.join(cfg.paths.inbox, "readme.txt"), "w") as f:
            f.write("not a pdf")

        results = process_inbox(cfg, db_conn)
        assert len(results) == 1
        assert results[0].filename == "invoice.pdf"

    def test_duplicate_across_batch(self, cfg, db_conn):
        from tests.conftest import make_text_pdf
        path = os.path.join(cfg.paths.inbox, "dup.pdf")
        make_text_pdf(path, "Same content across batch")
        results1 = process_inbox(cfg, db_conn)
        assert len(results1) == 1
        results2 = process_inbox(cfg, db_conn)
        assert len(results2) == 0, "should skip duplicate on second pass"
