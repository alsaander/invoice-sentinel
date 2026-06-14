import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from invoicesentinel.config import Config
from invoicesentinel.extract import extract_line_items
from invoicesentinel.grounding import all_ungrounded, check_grounding, is_any_ungrounded
from invoicesentinel.ingest import compute_sha256, extract_text
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.models import create_tables
from invoicesentinel.reason import reason_line_item
from invoicesentinel.reference_prices import load_reference_prices, find_match
from invoicesentinel.store import persist_extraction_results, persist_reasoning_results


LogEvent = Dict[str, Any]


def _llm_error_message(e: Exception, cfg: Config) -> str:
    msg = str(e).lower()
    if "connect" in msg or "refused" in msg or "econnrefused" in msg:
        return (
            f"Could not connect to Ollama at {cfg.model.ollama_host} — "
            f"is `ollama serve` running?"
        )
    if "not found" in msg or "model" in msg:
        return (
            f"Model '{cfg.model.name}' not found — try `ollama pull {cfg.model.name}`"
        )
    return f"LLM error: {e}"


def _severities_from_items(items: list) -> List[str]:
    return [li.severity for li in items if li.severity != "PARSE_ERROR"]


def _currencies_from_items(items: list) -> List[str]:
    return [li.currency for li in items if li.currency]


def _route_from_severities(
    severities: List[str],
    currencies: List[str],
    cfg: Config,
) -> tuple:
    if any(s == "HIGH" for s in severities):
        return "MANUAL_REVIEW", cfg.paths.review_high
    if any(s == "UNGROUNDED" for s in severities):
        return "MANUAL_REVIEW", cfg.paths.review_extraction_failed
    if any(s == "UNKNOWN" for s in severities) or any(c in ("UNKNOWN", "", None) for c in currencies):
        return "MANUAL_REVIEW_LOW_PRIORITY", cfg.paths.review_moderate
    if any(s == "MODERATE" for s in severities):
        return "MANUAL_REVIEW_LOW_PRIORITY", cfg.paths.review_moderate
    return "CLEARED", cfg.paths.processed


def run_single_pipeline(
    pdf_path: str,
    cfg: Config,
    llm_client: OllamaClient,
    reference_prices: Optional[List[Dict[str, str]]] = None,
    commit: bool = False,
) -> Generator[LogEvent, None, None]:
    filename = os.path.basename(pdf_path)
    sha256 = compute_sha256(pdf_path)
    yield {"type": "info", "message": f"File: {filename} | SHA-256: {sha256[:16]}..."}

    yield {"type": "info", "message": "Extracting text from PDF..."}
    try:
        text, method = extract_text(pdf_path, cfg.extraction.min_text_chars)
    except Exception as e:
        yield {"type": "error", "message": f"PDF extraction failed: {e}"}
        return

    if method == "failed":
        msg = (
            f"pdfplumber returned 0 chars, falling back to PyPDF2... "
            f"EXTRACTION FAILED — length ({len(text)}) < min_text_chars "
            f"({cfg.extraction.min_text_chars}). PDF may be scanned/image-only."
        )
        yield {"type": "error", "message": msg}
        yield {"type": "routing", "message": "review/extraction_failed/ — status=EXTRACTION_FAILED"}
        if commit:
            _write_extraction_failed(pdf_path, cfg, filename, sha256)
        yield {"type": "complete", "data": {"items": [], "llm_calls": [], "status": "EXTRACTION_FAILED"}}
        return

    yield {"type": "info", "message": f"Text extracted via {method} ({len(text)} chars)"}

    if reference_prices is None:
        ref_path = os.path.join(os.path.dirname(pdf_path), "reference_prices.csv")
        if not os.path.exists(ref_path):
            ref_path = "reference_prices.csv"
        reference_prices = load_reference_prices(ref_path)

    conn: Optional[sqlite3.Connection] = None
    invoice_id: Optional[int] = None

    if commit:
        conn = sqlite3.connect(cfg.database.path)
        create_tables(conn)
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO invoices (filename, file_sha256, received_at, raw_text_chars, "
            "extraction_method, status) VALUES (?, ?, ?, ?, ?, 'PENDING')",
            (filename, sha256, now, len(text), method),
        )
        invoice_id = cur.lastrowid
        conn.commit()

    yield {"type": "info", "message": f"Sending to LLM for entity extraction (prompt: extraction_v1, model: {cfg.model.name})..."}

    try:
        items, calls = extract_line_items(
            invoice_id if invoice_id is not None else -1,
            text, cfg, llm_client,
        )
    except Exception as e:
        yield {"type": "error", "message": _llm_error_message(e, cfg)}
        if conn is not None:
            conn.close()
        return

    extraction_retried = any(c.call_type == "retry" for c in calls)
    extraction_parse_error = items and items[0].severity == "PARSE_ERROR"

    for c in calls:
        latency = c.latency_ms
        retry_label = " (retry)" if c.call_type == "retry" else ""
        yield {"type": "info", "message": f"Extraction response in {latency}ms{retry_label}"}

    if extraction_parse_error:
        yield {"type": "error", "message": "JSON parse failed after retry. Raw response below."}
        yield {"type": "raw", "message": calls[-1].raw_response[:2000]}
        yield {"type": "complete", "data": {"items": items, "llm_calls": calls, "status": "PARSE_ERROR"}}
        if conn is not None:
            conn.close()
        return

    yield {"type": "info", "message": f"Parsed {len(items)} line item(s)"}

    # --- Grounding check ---
    items = check_grounding(items, text, cfg)
    for li in items:
        if li.grounded == "false":
            yield {
                "type": "error",
                "message": (
                    f"Item '{li.description}' not found in source document text — "
                    f"possible hallucination. Flagged for manual review."
                ),
            }

    if all_ungrounded(items):
        yield {
            "type": "error",
            "message": "NO extracted items passed the grounding check — invoice flagged for manual review (EXTRACTION_UNGROUNDED).",
        }
        yield {"type": "complete", "data": {"items": items, "llm_calls": calls, "status": "EXTRACTION_UNGROUNDED"}}
        if commit and conn is not None and invoice_id is not None:
            from invoicesentinel.models import update_invoice_status
            dest_dir = cfg.paths.review_extraction_failed
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, filename)
            shutil.move(pdf_path, dest_path)
            now = datetime.now(timezone.utc).isoformat()
            update_invoice_status(conn, invoice_id, "EXTRACTION_UNGROUNDED", dest_path, now)
            persist_extraction_results(conn, invoice_id, items, calls)
            yield {"type": "info", "message": f"File moved to {dest_path}"}
        if conn is not None:
            conn.close()
        return

    for i, li in enumerate(items, 1):
        qty = li.quantity if li.quantity is not None else "N/A"
        up = f"{li.unit_price}" if li.unit_price is not None else "N/A"
        yield {
            "type": "item",
            "message": f"Item {i}: {li.description} | qty={qty} | unit_price={up} {li.currency} | category={li.category} | grounded={li.grounded}",
            "data": {"item": li, "index": i},
        }

    if commit and conn is not None and invoice_id is not None:
        persist_extraction_results(conn, invoice_id, items, calls)

    for i, li in enumerate(items, 1):
        if li.severity == "UNGROUNDED":
            yield {"type": "info", "message": f"Item {i}: skipped reasoning (UNGROUNDED)"}
            continue

        yield {"type": "info", "message": f"Evaluating price for item {i} ({li.description})..."}

        try:
            updated_li, call = reason_line_item(li, cfg, llm_client, reference_prices)
        except Exception as e:
            yield {"type": "error", "message": _llm_error_message(e, cfg)}
            if conn is not None:
                conn.close()
            return

        items[i - 1] = updated_li
        if call is not None:
            calls.append(call)
            if call.call_type == "retry":
                yield {"type": "info", "message": f"Reasoning JSON parse failed for item {i}. Retried."}
                yield {"type": "raw", "message": call.raw_response[:2000]}

        if updated_li.severity == "UNKNOWN":
            reason = "currency unknown" if updated_li.currency in ("UNKNOWN", "", None) else "LLM could not produce numeric estimate"
            yield {"type": "info", "message": f"Item {i}: {reason} — severity=UNKNOWN"}
        elif updated_li.deviation_pct is not None:
            yield {
                "type": "info",
                "message": (
                    f"Item {i}: range {updated_li.currency} {updated_li.est_market_low:.2f}–"
                    f"{updated_li.est_market_high:.2f} | deviation: {updated_li.deviation_pct:+.1f}% | "
                    f"severity: {updated_li.severity} | source: {updated_li.reference_source}"
                ),
            }
        else:
            yield {"type": "info", "message": f"Item {i}: severity=UNKNOWN"}

        if updated_li.justification:
            yield {"type": "justification", "message": updated_li.justification}

        if commit and conn is not None:
            persist_reasoning_results(conn, updated_li, call)

    severities = _severities_from_items(items)
    currencies = _currencies_from_items(items)

    status, dest_dir = _route_from_severities(severities, currencies, cfg)

    yield {"type": "routing", "message": f"Route: {dest_dir}/ — status={status}"}

    if commit and conn is not None and invoice_id is not None:
        from invoicesentinel.models import update_invoice_status
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)
        shutil.move(pdf_path, dest_path)
        now = datetime.now(timezone.utc).isoformat()
        update_invoice_status(conn, invoice_id, status, dest_path, now)
        yield {"type": "info", "message": f"File moved to {dest_path}"}

    if conn is not None:
        conn.close()

    yield {
        "type": "complete",
        "data": {
            "items": items,
            "llm_calls": calls,
            "status": status,
        },
    }


def _write_extraction_failed(
    pdf_path: str, cfg: Config, filename: str, sha256: str
) -> None:
    import sqlite3
    conn = sqlite3.connect(cfg.database.path)
    create_tables(conn)
    now = datetime.now(timezone.utc).isoformat()
    dest_dir = cfg.paths.review_extraction_failed
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    shutil.move(pdf_path, dest_path)
    conn.execute(
        "INSERT INTO invoices (filename, file_sha256, received_at, raw_text_chars, "
        "extraction_method, status, moved_to_path, processed_at) "
        "VALUES (?, ?, ?, 0, 'failed', 'EXTRACTION_FAILED', ?, ?)",
        (filename, sha256, now, dest_path, now),
    )
    conn.commit()
    conn.close()
