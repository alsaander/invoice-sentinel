import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import typer

from invoicesentinel.config import Config, load_config
from invoicesentinel.extract import extract_line_items
from invoicesentinel.ingest import extract_text, process_inbox
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.reason import reason_line_item
from invoicesentinel.reference_prices import load_reference_prices
from invoicesentinel.router import route_invoices
from invoicesentinel.store import (
    get_pending_invoice_ids,
    persist_extraction_results,
    persist_reasoning_results,
    compute_run_summary,
    print_run_summary_console,
    write_run_summary_json,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="invoicesentinel",
    help="InvoiceSentinel — TBML invoice anomaly detection tool",
    no_args_is_help=True,
)


def _common_config(
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Process without moving files or writing routing status"),
) -> Config:
    return load_config(config)


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml", show_default=True),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Dry-run mode (skip file moves and routing)", show_default=True),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["dry_run"] = dry_run


def _open_db(cfg: Config) -> sqlite3.Connection:
    path = cfg.database.path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    from invoicesentinel.models import create_tables
    create_tables(conn)
    return conn


def _compute_dry_run_counts(
    conn: sqlite3.Connection, invoice_ids: List[int]
) -> Dict[str, int]:
    counts: Dict[str, int] = {
        "CLEARED": 0,
        "MANUAL_REVIEW": 0,
        "MANUAL_REVIEW_LOW_PRIORITY": 0,
        "EXTRACTION_FAILED": 0,
        "PENDING": 0,
        "total": 0,
    }
    for iid in invoice_ids:
        row = conn.execute("SELECT status FROM invoices WHERE id = ?", (iid,)).fetchone()
        if row is None:
            continue
        st = row[0]
        counts["total"] += 1
        if st in counts:
            counts[st] += 1
            continue
        items = conn.execute(
            "SELECT severity, currency FROM line_items WHERE invoice_id = ?",
            (iid,),
        ).fetchall()
        severities = [r[0] for r in items]
        currencies = [r[1] for r in items]
        if any(s == "HIGH" for s in severities):
            counts["MANUAL_REVIEW"] += 1
        elif any(s == "UNGROUNDED" for s in severities):
            counts["MANUAL_REVIEW"] += 1
        elif any(s == "UNKNOWN" for s in severities) or any(
            c == "UNKNOWN" for c in currencies
        ):
            counts["MANUAL_REVIEW_LOW_PRIORITY"] += 1
        elif any(s == "MODERATE" for s in severities):
            counts["MANUAL_REVIEW_LOW_PRIORITY"] += 1
        else:
            counts["CLEARED"] += 1
    return counts


def run_pipeline(
    cfg: Config,
    conn: sqlite3.Connection,
    llm_client: OllamaClient,
    dry_run: bool = False,
) -> Dict:
    invoices = process_inbox(cfg, conn)
    reference_prices = load_reference_prices("reference_prices.csv")

    for inv in invoices:
        if inv.status != "PENDING":
            continue
        src = os.path.join(cfg.paths.inbox, inv.filename)
        if not os.path.isfile(src):
            logger.warning("Source file %s not found for invoice %d", src, inv.id)
            continue
        text, _ = extract_text(src, cfg.extraction.min_text_chars)
        if not text.strip():
            logger.warning("Empty text for invoice %d (%s)", inv.id, inv.filename)
            continue
        line_items, llm_calls = extract_line_items(
            inv.id, text, cfg, llm_client,
        )
        persist_extraction_results(conn, inv.id, line_items, llm_calls)
        for li in line_items:
            updated_li, call = reason_line_item(
                li, cfg, llm_client, reference_prices,
            )
            persist_reasoning_results(conn, updated_li, call)

    processed_ids = [inv.id for inv in invoices if inv.status == "PENDING"]

    if dry_run:
        summary = compute_run_summary(conn, processed_ids)
        summary["counts"] = _compute_dry_run_counts(conn, processed_ids)
        summary["dry_run"] = True
    else:
        pending_ids = get_pending_invoice_ids(conn)
        route_invoices(conn, pending_ids, cfg)
        summary = compute_run_summary(conn, pending_ids)

    return summary


@app.command()
def run(
    ctx: typer.Context,
) -> None:
    cfg = load_config(ctx.obj["config_path"])
    dry_run = ctx.obj["dry_run"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    conn = _open_db(cfg)
    llm_client = OllamaClient(
        base_url=cfg.model.ollama_host,
        model=cfg.model.name,
    )

    try:
        summary = run_pipeline(cfg, conn, llm_client, dry_run=dry_run)
        if dry_run:
            summary["dry_run"] = True
            print("\n  [DRY RUN] No files moved — simulated routing shown below")
        print_run_summary_console(summary)
        write_run_summary_json(summary, output_dir=".")
    finally:
        llm_client.close()
        conn.close()


@app.command()
def watch(
    ctx: typer.Context,
    poll_interval: int = typer.Option(5, "--interval", "-i", help="Poll interval in seconds", show_default=True),
) -> None:
    cfg = load_config(ctx.obj["config_path"])
    dry_run = ctx.obj["dry_run"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import time as time_module

    conn = _open_db(cfg)
    llm_client = OllamaClient(
        base_url=cfg.model.ollama_host,
        model=cfg.model.name,
    )

    inbox = cfg.paths.inbox
    os.makedirs(inbox, exist_ok=True)
    seen: set = set()
    for fname in os.listdir(inbox):
        if fname.lower().endswith(".pdf"):
            seen.add(fname)

    label = " [DRY RUN]" if dry_run else ""
    print(f"Watching {inbox}/ for new PDFs (poll every {poll_interval}s){label}...")
    sys.stdout.flush()

    try:
        while True:
            time_module.sleep(poll_interval)
            current = set()
            for fname in os.listdir(inbox):
                if fname.lower().endswith(".pdf"):
                    current.add(fname)
            new_files = current - seen
            if new_files:
                for fname in sorted(new_files):
                    print(f"  New file detected: {fname}")
                    sys.stdout.flush()
                run_pipeline(cfg, conn, llm_client, dry_run=dry_run)
                seen = current
    except KeyboardInterrupt:
        print("\nWatch stopped.")
    finally:
        llm_client.close()
        conn.close()


@app.command()
def dashboard(
    ctx: typer.Context,
    port: int = typer.Option(8501, "--port", "-p", help="Streamlit port", show_default=True),
) -> None:
    cfg = load_config(ctx.obj["config_path"])
    db_path = os.path.abspath(cfg.database.path)

    import subprocess
    dashboard_path = Path(__file__).parent / "dashboard.py"
    env = os.environ.copy()
    env["INVOICESENTINEL_DB"] = db_path
    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run",
            str(dashboard_path),
            "--server.port", str(port),
        ],
        env=env,
    )


def entry_point() -> None:
    app()


if __name__ == "__main__":
    entry_point()
