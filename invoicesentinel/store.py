import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from invoicesentinel.config import Config
from invoicesentinel.models import Invoice, LineItem, insert_llm_call, insert_line_item

logger = logging.getLogger(__name__)


def persist_extraction_results(
    conn: sqlite3.Connection,
    invoice_id: int,
    line_items: List[LineItem],
    llm_calls: list,
) -> None:
    conn.execute("BEGIN")
    try:
        for li in line_items:
            li.invoice_id = invoice_id
            insert_line_item(conn, li, commit=False)
        for call in llm_calls:
            call.invoice_id = invoice_id
            insert_llm_call(conn, call, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def persist_reasoning_results(
    conn: sqlite3.Connection,
    line_item: LineItem,
    llm_call: Optional[object],
) -> None:
    conn.execute("BEGIN")
    try:
        from invoicesentinel.models import update_line_item

        update_line_item(conn, line_item, commit=False)
        if llm_call is not None:
            llm_call.line_item_id = line_item.id
            insert_llm_call(conn, llm_call, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_pending_invoice_ids(conn: sqlite3.Connection) -> List[int]:
    rows = conn.execute(
        "SELECT id FROM invoices WHERE status = 'PENDING' ORDER BY id"
    ).fetchall()
    return [r[0] for r in rows]


def get_invoice_line_items(
    conn: sqlite3.Connection, invoice_id: int
) -> List[LineItem]:
    rows = conn.execute(
        "SELECT id, invoice_id, quantity, unit_price, currency, description, "
        "category, category_raw, est_market_low, est_market_high, deviation_pct, "
        "severity, reference_source, justification, analyst_verdict, created_at "
        "FROM line_items WHERE invoice_id = ? ORDER BY id",
        (invoice_id,),
    ).fetchall()
    return [LineItem(*r) for r in rows]


def compute_run_summary(
    conn: sqlite3.Connection,
    invoice_ids: List[int],
    top_n: int = 10,
) -> Dict:
    counts: Dict[str, int] = {
        "MANUAL_REVIEW": 0,
        "MANUAL_REVIEW_LOW_PRIORITY": 0,
        "CLEARED": 0,
        "EXTRACTION_FAILED": 0,
        "PENDING": 0,
        "total": 0,
    }

    for iid in invoice_ids:
        row = conn.execute(
            "SELECT status FROM invoices WHERE id = ?", (iid,)
        ).fetchone()
        if row:
            counts["total"] += 1
            st = row[0]
            if st in counts:
                counts[st] += 1
            else:
                counts[st] = 1

    all_items: List[Dict] = []
    for iid in invoice_ids:
        items = get_invoice_line_items(conn, iid)
        for li in items:
            if li.deviation_pct is not None:
                all_items.append({
                    "invoice_id": li.invoice_id,
                    "line_item_id": li.id,
                    "description": li.description,
                    "deviation_pct": li.deviation_pct,
                    "abs_deviation_pct": abs(li.deviation_pct),
                    "severity": li.severity,
                })

    all_items.sort(key=lambda x: x["abs_deviation_pct"], reverse=True)
    top_items = all_items[:top_n]

    return {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "top_line_items": top_items,
    }


def write_run_summary_json(summary: Dict, output_dir: str = ".") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"run_summary_{ts}.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Run summary written to %s", path)
    return path


def print_run_summary_console(summary: Dict) -> None:
    counts = summary["counts"]
    print(f"\n{'='*50}")
    print(f"  InvoiceSentinel Run Summary")
    print(f"  Timestamp: {summary['run_timestamp']}")
    print(f"{'='*50}")
    print(f"  Total invoices processed:  {counts.get('total', 0)}")
    for status in ("CLEARED", "MANUAL_REVIEW", "MANUAL_REVIEW_LOW_PRIORITY",
                   "EXTRACTION_FAILED", "PENDING"):
        c = counts.get(status, 0)
        if c:
            print(f"    {status:40s} {c}")
    print()

    top = summary.get("top_line_items", [])
    if top:
        print(f"  Top {len(top)} line items by absolute deviation:")
        print(f"  {'ID':>4s} {'Inv#':>5s} {'Dev%':>8s} {'Severity':12s}  Description")
        print(f"  {'-'*4} {'-'*5} {'-'*8} {'-'*12}  {'-'*30}")
        for item in top:
            print(
                f"  {item['line_item_id']:>4d} {item['invoice_id']:>5d} "
                f"{item['deviation_pct']:>+7.1f}% "
                f"{item['severity']:12s}  {item['description'][:50]}"
            )
    print(f"{'='*50}\n")
