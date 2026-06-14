import os
import sqlite3
import sys
import tempfile

import streamlit as st

from invoicesentinel.config import load_config
from invoicesentinel.dashboard_queries import (
    get_cleared_invoices,
    get_flagged_line_items,
    get_llm_calls_for_line_item,
    get_review_invoices,
    set_analyst_verdict,
)
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.single_run import run_single_pipeline

st.set_page_config(page_title="InvoiceSentinel", layout="wide")

DB_PATH = os.environ.get(
    "INVOICESENTINEL_DB",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        load_config().database.path,
    ),
)


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def render_line_items_tab(conn: sqlite3.Connection, invoice_id: int) -> None:
    items = get_flagged_line_items(conn, invoice_id)
    if not items:
        st.caption("No line items.")
        return

    for li in items:
        border = "2px solid #ff4b4b" if li.severity == "HIGH" else "2px solid #e65100" if li.severity == "UNKNOWN" else "1px solid #ddd"
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])

            with col1:
                cat_style = (
                    f"background-color: #fff3cd; padding: 0 4px; border-radius: 4px; font-weight: bold; color: #856404; display: inline"
                    if li.category == "Otro"
                    else ""
                )
                desc_html = f"<strong>{li.description}</strong>"
                cat_html = f"<span style='{cat_style}'>{li.category}</span>" if li.category == "Otro" else li.category
                st.markdown(
                    f"{desc_html} &mdash; Category: {cat_html}",
                    unsafe_allow_html=True,
                )

                qty = li.quantity if li.quantity is not None else "N/A"
                up = f"{li.currency} {li.unit_price:.2f}" if li.unit_price is not None else "N/A"
                mk = f"{li.currency} {li.est_market_low:.2f} – {li.est_market_high:.2f}" if li.est_market_low is not None and li.est_market_high is not None else "N/A"
                meta = [
                    f"Qty: {qty}",
                    f"Unit: {up}",
                    f"Market: {mk}",
                ]
                st.caption(" | ".join(meta))

                dev = li.deviation_pct or 0.0
                sev_color = "#ff4b4b" if li.severity == "HIGH" else "#e65100" if li.severity == "UNKNOWN" else "#ffa726" if li.severity == "MODERATE" else "#2e7d32"
                if li.severity == "UNKNOWN":
                    st.markdown(
                        f"⚠ <span style='color:{sev_color}; font-weight:bold'>Could not evaluate</span> &nbsp;|&nbsp; Severity: <span style='color:{sev_color}'>{li.severity}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"Deviation: <span style='color:{sev_color}; font-weight:bold'>{dev:+.1f}%</span> &nbsp;|&nbsp; Severity: <span style='color:{sev_color}'>{li.severity}</span>",
                        unsafe_allow_html=True,
                    )
                if li.reference_source:
                    st.caption(f"Reference: {li.reference_source}")

            with col2:
                col2a, col2b = st.columns(2)
                with col2a:
                    if st.button("✓ Reviewed OK", key=f"ok_{li.id}", use_container_width=True):
                        set_analyst_verdict(conn, li.id, "REVIEWED_OK")
                        st.success(f"Line item {li.id} marked REVIEWED_OK")
                        st.rerun()
                with col2b:
                    if st.button("⚠ Escalate", key=f"esc_{li.id}", use_container_width=True):
                        set_analyst_verdict(conn, li.id, "REVIEWED_ESCALATE")
                        st.warning(f"Line item {li.id} marked REVIEWED_ESCALATE")
                        st.rerun()

            with st.expander("Justification"):
                st.text(li.justification)

            verdict = li.analyst_verdict
            if verdict:
                label = {"REVIEWED_OK": "✓ Reviewed OK", "REVIEWED_ESCALATE": "⚠ Escalated"}.get(verdict, verdict)
                st.info(f"**Verdict:** {label}")


def render_line_items_readonly(conn: sqlite3.Connection, invoice_id: int) -> None:
    items = get_flagged_line_items(conn, invoice_id)
    if not items:
        st.caption("No line items.")
        return

    for li in items:
        with st.container(border=True):
            dev = li.deviation_pct or 0.0
            sev_color = "#ff4b4b" if li.severity == "HIGH" else "#e65100" if li.severity == "UNKNOWN" else "#ffa726" if li.severity == "MODERATE" else "#2e7d32"
            if li.severity == "UNKNOWN":
                st.markdown(
                    f"**{li.description}** &mdash; ⚠ <span style='color:{sev_color}; font-weight:bold'>Could not evaluate</span> ({li.severity})",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"**{li.description}** &mdash; Deviation: <span style='color:{sev_color}'>{dev:+.1f}%</span> ({li.severity})",
                    unsafe_allow_html=True,
                )
            qty = li.quantity if li.quantity is not None else "N/A"
            up = f"{li.currency} {li.unit_price:.2f}" if li.unit_price is not None else "N/A"
            mk = f"{li.currency} {li.est_market_low:.2f}–{li.est_market_high:.2f}" if li.est_market_low is not None and li.est_market_high is not None else "N/A"
            st.caption(f"Cat: {li.category} | Qty: {qty} | Unit: {up} | Market: {mk}")
            verdict = li.analyst_verdict
            if verdict:
                label = {"REVIEWED_OK": "✓ Reviewed OK", "REVIEWED_ESCALATE": "⚠ Escalated"}.get(verdict, verdict)
                st.caption(f"Verdict: {label}")


def render_single_run_results(events: list, commit_mode: bool) -> None:
    for ev in events:
        t = ev["type"]
        if t == "info":
            st.write(ev["message"])
        elif t == "error":
            st.error(ev["message"])
        elif t == "item":
            st.info(f"📦 {ev['message']}")
        elif t == "routing":
            st.success(f"🚚 {ev['message']}")
        elif t == "justification":
            with st.expander("View justification"):
                st.text(ev["message"])
        elif t == "raw":
            with st.expander("Raw LLM Response"):
                st.code(ev["message"], language="text")

    complete = [e for e in events if e["type"] == "complete"]
    if complete:
        data = complete[0]["data"]
        items = data.get("items", [])
        calls = data.get("llm_calls", [])
        status = data.get("status", "")

        if status == "EXTRACTION_FAILED":
            st.error("**Pipeline finished: EXTRACTION_FAILED**")
            return

        st.divider()
        st.subheader("Results Table")

        if items:
            table_data = []
            for li in items:
                dev = f"{li.deviation_pct:+.1f}%" if li.deviation_pct is not None else "N/A"
                mk = f"{li.est_market_low:.2f}–{li.est_market_high:.2f}" if li.est_market_low is not None and li.est_market_high is not None else "N/A"
                table_data.append({
                    "Description": li.description,
                    "Category": li.category,
                    "Qty": li.quantity or "N/A",
                    "Unit Price": f"{li.currency} {li.unit_price}" if li.unit_price is not None else "N/A",
                    "Est. Range": mk,
                    "Deviation": dev,
                    "Severity": li.severity,
                    "Source": li.reference_source,
                })
            st.dataframe(table_data, use_container_width=True)

            for li in items:
                with st.expander(f"Justification: {li.description}"):
                    st.text(li.justification)

        st.divider()
        st.subheader("Raw LLM Responses")
        with st.expander(f"View {len(calls)} LLM call(s)"):
            for i, c in enumerate(calls, 1):
                st.markdown(f"**Call #{i}** — `{c.prompt_version}` on `{c.model}` — {c.latency_ms}ms")
                st.code(c.raw_response[:3000], language="json")

        st.info(f"**Final routing:** {status}")


def render_test_tab() -> None:
    st.header("Test an Invoice")
    st.caption("Upload a single PDF to run it through the full pipeline and see step-by-step diagnostics.")

    cfg = load_config()

    commit_mode = st.checkbox(
        "Add this result to the database / move file per routing rules",
        value=False,
        help="When OFF (default), this is a pure dry-run — no data is saved and no file is moved.",
    )

    uploaded_file = st.file_uploader("Choose a PDF invoice", type="pdf")

    if not uploaded_file:
        if "test_events" in st.session_state:
            del st.session_state["test_events"]
        return

    st.caption(f"Uploaded: {uploaded_file.name} ({uploaded_file.size} bytes)")

    if st.button("Run Pipeline", type="primary", use_container_width=True):
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded_file.getbuffer())
                tmp_path = tmp.name

            llm_client = OllamaClient(
                base_url=cfg.model.ollama_host,
                model=cfg.model.name,
            )

            with st.status("Running pipeline...", expanded=True) as status:
                events = list(run_single_pipeline(
                    tmp_path, cfg, llm_client,
                    commit=commit_mode,
                ))
                st.session_state["test_events"] = events
                status.update(label="Pipeline complete", state="complete")

            llm_client.close()

        except Exception as e:
            st.error(f"Unexpected error: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if "test_events" in st.session_state:
        st.divider()
        st.subheader("Pipeline Log")
        log_container = st.container()
        with log_container:
            render_single_run_results(st.session_state["test_events"], commit_mode)


def main() -> None:
    conn = get_conn()
    tab_review, tab_cleared, tab_audit, tab_test = st.tabs(
        ["Review Queue", "Cleared", "Audit", "Test an Invoice"]
    )

    with tab_review:
        st.header("Invoices Awaiting Review")
        invoices = get_review_invoices(conn)
        if not invoices:
            st.info("No invoices pending review.")
        else:
            st.caption(f"{len(invoices)} invoice(s) awaiting review")
            for inv in invoices:
                label = f"🚩 {inv.filename}" if inv.status == "MANUAL_REVIEW" else f"🟡 {inv.filename}"
                with st.expander(label):
                    st.markdown(f"**Status:** `{inv.status}`")
                    if inv.moved_to_path:
                        st.caption(f"Source: {inv.moved_to_path}")
                    render_line_items_tab(conn, inv.id)

    with tab_cleared:
        st.header("Cleared Invoices (Audit)")
        invoices = get_cleared_invoices(conn)
        if not invoices:
            st.info("No cleared invoices.")
        else:
            st.caption(f"{len(invoices)} cleared invoice(s)")
            for inv in invoices:
                with st.expander(f"✅ {inv.filename}"):
                    st.markdown(f"**Status:** `{inv.status}`")
                    if inv.moved_to_path:
                        st.caption(f"Source: {inv.moved_to_path}")
                    render_line_items_readonly(conn, inv.id)

    with tab_audit:
        st.header("LLM Call Audit (NFR4)")
        li_id = st.number_input(
            "Enter Line Item ID to trace",
            min_value=1,
            step=1,
            value=None,
            placeholder="e.g. 1",
        )
        if li_id:
            calls = get_llm_calls_for_line_item(conn, li_id)
            if not calls:
                st.info(f"No LLM calls found for line item {li_id}.")
            else:
                for i, call in enumerate(calls, 1):
                    with st.expander(f"Call #{i} — {call['prompt_version']} on {call['model']}"):
                        st.markdown(f"**Prompt version:** `{call['prompt_version']}`")
                        st.markdown(f"**Model:** `{call['model']}`")
                        st.markdown(f"**Latency:** {call['latency_ms']} ms")
                        st.text_area(
                            "Raw Response",
                            call["raw_response"],
                            height=200,
                            key=f"raw_{li_id}_{i}",
                        )

    with tab_test:
        render_test_tab()

    conn.close()


if __name__ == "__main__":
    main()
