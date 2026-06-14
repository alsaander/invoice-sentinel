import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from invoicesentinel.config import Config
from invoicesentinel.models import Invoice, update_invoice_status

logger = logging.getLogger(__name__)


def route_invoice(
    conn: sqlite3.Connection,
    invoice_id: int,
    cfg: Config,
) -> Optional[Invoice]:
    row = conn.execute(
        "SELECT id, filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status, moved_to_path, processed_at "
        "FROM invoices WHERE id = ?",
        (invoice_id,),
    ).fetchone()

    if row is None:
        return None

    inv = Invoice(*row)

    if inv.status not in ("PENDING", ""):
        return inv

    cur = conn.execute(
        "SELECT severity, currency FROM line_items WHERE invoice_id = ?",
        (invoice_id,),
    )
    items = cur.fetchall()

    severities = [r[0] for r in items]
    currencies = [r[1] for r in items]

    if any(s == "HIGH" for s in severities):
        status = "MANUAL_REVIEW"
        dest_dir = cfg.paths.review_high
    elif any(s == "UNGROUNDED" for s in severities):
        status = "MANUAL_REVIEW"
        dest_dir = cfg.paths.review_extraction_failed
    elif (
        any(s == "UNKNOWN" for s in severities)
        or any(c == "UNKNOWN" for c in currencies)
    ):
        status = "MANUAL_REVIEW_LOW_PRIORITY"
        dest_dir = cfg.paths.review_moderate
    elif any(s == "MODERATE" for s in severities):
        status = "MANUAL_REVIEW_LOW_PRIORITY"
        dest_dir = cfg.paths.review_moderate
    else:
        status = "CLEARED"
        dest_dir = cfg.paths.processed

    src = os.path.join(cfg.paths.inbox, inv.filename)
    moved_to_path: Optional[str] = None
    if os.path.isfile(src):
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, inv.filename)
        shutil.move(src, dest)
        moved_to_path = dest
    else:
        moved_to_path = inv.moved_to_path

    now = datetime.now(timezone.utc).isoformat()
    update_invoice_status(conn, invoice_id, status, moved_to_path, now)

    inv.status = status
    inv.moved_to_path = moved_to_path
    inv.processed_at = now
    return inv


def route_invoices(
    conn: sqlite3.Connection,
    invoice_ids: List[int],
    cfg: Config,
) -> List[Invoice]:
    results: List[Invoice] = []
    for iid in invoice_ids:
        inv = route_invoice(conn, iid, cfg)
        if inv is not None:
            results.append(inv)
    return results
