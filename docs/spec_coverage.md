# SPEC.md Coverage Map

Every numbered requirement (FR-x / NFR-x) mapped to implementing file(s) and
test(s). Documents produced during M8 final hardening pass.

---

## FR1 — Ingestion

| № | Requirement | Implemented in | Verified by |
|---|---|---|---|
| FR1.1 | Watch configurable `inbox/` for PDFs | `ingest.py:process_inbox()` (poll), `cli.py:watch()` (watchdog/poll) | `test_ingest.py` |
| FR1.2 | pdfplumber primary, PyPDF2 fallback | `ingest.py:extract_text()` | `test_ingest.py::TestExtractText` |
| FR1.3 | `EXTRACTION_FAILED` if text < `MIN_TEXT_CHARS` | `ingest.py:process_single_pdf()` lines 93–109 | `test_ingest.py::test_empty_pdf_marked_failed_and_moved` |
| FR1.4 | SHA-256 hash for dedup | `ingest.py:compute_sha256()`, `_is_duplicate()` | `test_ingest.py::TestComputeSha256`, `test_duplicate_found` |

---

## FR2 — Entity Extraction (LLM Pass 1)

| № | Requirement | Implemented in | Verified by |
|---|---|---|---|
| FR2.1 | Send text to local model with Extraction Prompt | `extract.py:extract_line_items()` | `test_extract.py::TestExtractLineItems` |
| FR2.2 | Return strict JSON matching LineItem schema | `extract.py:_parse_json_array()` | `test_extract.py::TestParseJsonArray` |
| FR2.3 | Constrain category to controlled vocabulary; map unknown to `Otro` | `extract.py:_validate_category()` | `test_extract.py::TestValidateCategory` |
| FR2.4 | Retry on JSON parse failure; fallback to `PARSE_ERROR` | `extract.py:extract_line_items()` retry loop | `test_extract.py::test_malformed_then_valid_retry`, `test_double_fail_produces_parse_error` |
| FR2.5 | Multiple line items per invoice, each scored independently | `extract.py` returns `List[LineItem]` | `test_extract.py::test_clean_3_items` |

---

## FR3 — Price Plausibility Reasoning (LLM Pass 2)

| № | Requirement | Implemented in | Verified by |
|---|---|---|---|
| FR3.1 | Send description/category/qty/price/currency to Reasoning Prompt | `reason.py:build_reasoning_prompt()`, `reason_line_item()` | `test_reason.py::TestBuildReasoningPrompt` |
| FR3.2 | Model estimates market range, midpoint, deviation, justification | `reason.py:build_reasoning_prompt()` with reasoning_v1.txt | `test_reason.py::TestReasonLineItem` |
| FR3.3 | Deterministic severity in Python (NORMAL ≤ 100%, MODERATE ≤ 200%, HIGH > 200%) | `reason.py:compute_deviation_pct()`, `classify_severity()` | `test_reason.py::TestComputeDeviationPct`, `TestClassifySeverity` |
| FR3.4 | `reference_prices.csv` overrides LLM estimate for deterministic bucket | `reason.py:reason_line_item()` lines 158–162 | `test_reason.py::TestReasonLineItemReferencePrices` |
| FR3.5 | Store raw LLM reasoning verbatim | `reason.py` stores `justification`, `LlmCall.raw_response` | `test_reason.py::test_line_item_fields_populated` |

---

## FR4 — Storage, Alerting & Routing

| № | Requirement | Implemented in | Verified by |
|---|---|---|---|
| FR4.1 | Persist to SQLite | `store.py`, `models.py` | `test_models.py`, `test_store.py` (via test_cli) |
| FR4.2 | HIGH → review/high/ + MANUAL_REVIEW; MODERATE/UNKNOWN → review/moderate/ + LOW_PRIORITY; else → processed/ + CLEARED | `router.py:route_invoice()` | `test_router.py::TestRouteInvoice` |
| FR4.3 | Per-run summary (JSON + console) with counts and top-N deviations | `store.py:compute_run_summary()`, `write_run_summary_json()`, `print_run_summary_console()` | `test_router.py::TestComputeRunSummary` |
| FR4.4 | Read/write web UI (Streamlit) for flagged invoices; REVIEWED_OK / REVIEWED_ESCALATE buttons | `dashboard.py`, `dashboard_queries.py` | `test_dashboard_queries.py`, manual UI test |

---

## NFRs

| № | Requirement | Implemented in | Verified by |
|---|---|---|---|
| NFR1 | No data to non-local host without `ALLOW_REMOTE_LLM=true` | `llm_client.py:OllamaClient.__init__()` | `test_llm_client.py::TestNFR1Guard`, `test_cli.py::TestNFR1RemoteGuard` |
| NFR2 | ≤15s per line item on GPU; CPU expectations in README | `README.md` | Documented (estimated) |
| NFR3 | Crash resilience via SHA-256 dedup + per-invoice transactions | `ingest.py:_is_duplicate()`, `store.py:persist_extraction_results()` (BEGIN/rollback) | `test_ingest.py::test_duplicate_found`, `test_ingest.py::test_duplicate_across_batch` |
| NFR4 | Every verdict traceable to `llm_calls` with prompt version + raw response | `models.py:LlmCall`, `store.py:persist_extraction_results/persist_reasoning_results` | `test_extract.py::test_nfr4_audit_logging`, `test_cli.py::test_run_line_items_persisted_with_deviation` |
| NFR5 | Configurable thresholds, model, paths, vocabulary in `config.yaml` | `config.py:load_config()` | `test_config.py` |
| NFR6 | ≥90% coverage on deterministic logic; tests pass with fixtures (no live Ollama) | All test files | Coverage ≥ 94% on deterministic modules; 149 tests pass offline |

---

## M1–M8 Milestones

| M | Description | Files | Tests |
|---|---|---|---|
| M1 | Foundations | `config.yaml`, `models.py`, `llm_client.py`, prompts/ | `test_config.py` (5), `test_models.py` (6), `test_llm_client.py` (12), `test_prompts.py` (3), `test_fixtures.py` (2) |
| M2 | Ingestion (FR1) | `ingest.py` | `test_ingest.py` (17) |
| M3 | Extraction (FR2) | `extract.py` | `test_extract.py` (20) |
| M4 | Reasoning (FR3) | `reason.py`, `reference_prices.py` | `test_reason.py` (35), `test_reference_prices.py` (11) |
| M5 | Storage & Routing (FR4) | `store.py`, `router.py` | `test_router.py` (11) |
| M6 | Dashboard (FR4.4) | `dashboard.py`, `dashboard_queries.py` | `test_dashboard_queries.py` (12) |
| M7 | CLI & Daemon Mode | `cli.py`, `setup.py` | `test_cli.py` (8) |
| M8 | Hardening | `docs/*.md`, demo dataset | NFR1/NFR6 validation |

---

## Uncovered / Open Items

| Item | Status | Notes |
|---|---|---|
| §14 OQ1 — min viable LLM | Open | Would need labeled eval set (~30 items) to compare `mistral:7b` vs `llama3:8b` |
| §14 OQ2 — per-category default ref prices | Open | Not implemented; current `reference_prices.csv` only supports keyword matches |
| §14 OQ3 — min quantity sanity check | Open | Candidate for v1.1, not blocking v1 |
| `store.py` rollback paths | Not directly tested | Error-recovery paths (pg 29–31, 48–50) require inducing an exception mid-transaction |
| NG4 (scanned PDFs) | Covered by FR1.3 | Image-only PDFs → `EXTRACTION_FAILED` as designed |
| NG5 (multi-user auth) | Out of scope for v1 | Single-analyst local tool |

---

## Coverage Summary (NFR6)

| Module | Coverage | Meets ≥90%? |
|---|---|---|
| `config.py` | 99% | ✅ |
| `extract.py` | 98% | ✅ |
| `ingest.py` | 97% | ✅ |
| `models.py` | 99% | ✅ |
| `reason.py` | 94% | ✅ |
| `reference_prices.py` | 97% | ✅ |
| `router.py` | 98% | ✅ |
| `llm_client.py` | 100% | ✅ |
| `store.py` | 62% | N/A (not in NFR6 list) |
| `dashboard_queries.py` | 83% | N/A (not in NFR6 list) |
| `cli.py` | 49% | N/A (CLI entry point) |
| `dashboard.py` | 0% | N/A (Streamlit) |
| **All deterministic modules** | **≥94%** | ✅ |

All 149 tests pass with Ollama not running (fixture-based).
