import hashlib
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pdfplumber
import PyPDF2

from invoicesentinel.config import Config
from invoicesentinel.models import Invoice

logger = logging.getLogger(__name__)


def compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(path: str, min_text_chars: int = 50) -> Tuple[str, str]:
    method = "failed"
    text = ""

    try:
        with pdfplumber.open(path) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            text = "\n".join(pages_text).strip()
            if len(text) >= min_text_chars:
                return text, "pdfplumber"
    except Exception:
        logger.debug("pdfplumber failed", exc_info=True)

    try:
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages_text = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            text = "\n".join(pages_text).strip()
            if len(text) >= min_text_chars:
                return text, "pypdf2"
    except Exception:
        logger.debug("PyPDF2 also failed", exc_info=True)

    return "", "failed"


def _is_duplicate(conn: sqlite3.Connection, sha256: str) -> bool:
    cur = conn.execute("SELECT 1 FROM invoices WHERE file_sha256 = ?", (sha256,))
    return cur.fetchone() is not None


def _insert_invoice(conn: sqlite3.Connection, inv: Invoice) -> Invoice:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO invoices (filename, file_sha256, received_at, raw_text_chars,
                                extraction_method, status, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (inv.filename, inv.file_sha256, now, inv.raw_text_chars,
         inv.extraction_method, inv.status, now),
    )
    inv.id = cur.lastrowid
    inv.received_at = now
    inv.processed_at = now
    conn.commit()
    return inv


def process_single_pdf(path: str, cfg: Config, conn: sqlite3.Connection) -> Optional[Invoice]:
    filename = os.path.basename(path)
    logger.info("Processing %s", filename)

    sha256 = compute_sha256(path)

    if _is_duplicate(conn, sha256):
        logger.info("Skipping duplicate: %s (sha256=%s)", filename, sha256[:16])
        return None

    text, method = extract_text(path, min_text_chars=cfg.extraction.min_text_chars)

    if method == "failed":
        inv = Invoice(
            filename=filename,
            file_sha256=sha256,
            raw_text_chars=0,
            extraction_method="failed",
            status="EXTRACTION_FAILED",
        )
        inv = _insert_invoice(conn, inv)
        dest_dir = cfg.paths.review_extraction_failed
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        shutil.move(path, dest)
        inv.moved_to_path = dest
        conn.execute("UPDATE invoices SET moved_to_path = ? WHERE id = ?", (dest, inv.id))
        conn.commit()
        return inv

    inv = Invoice(
        filename=filename,
        file_sha256=sha256,
        raw_text_chars=len(text),
        extraction_method=method,
        status="PENDING",
    )
    inv = _insert_invoice(conn, inv)
    return inv


def process_inbox(cfg: Config, conn: sqlite3.Connection) -> List[Invoice]:
    inbox_path = cfg.paths.inbox
    if not os.path.isdir(inbox_path):
        logger.warning("Inbox directory %s does not exist", inbox_path)
        return []

    results = []
    for fname in sorted(os.listdir(inbox_path)):
        if not fname.lower().endswith(".pdf"):
            continue
        fpath = os.path.join(inbox_path, fname)
        if not os.path.isfile(fpath):
            continue
        inv = process_single_pdf(fpath, cfg, conn)
        if inv is not None:
            results.append(inv)
    return results
