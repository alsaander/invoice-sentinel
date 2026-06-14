# InvoiceSentinel

**AI-powered invoice anomaly detection for trade-based money laundering (TBML) prevention. Runs 100% locally — no data ever leaves your machine.**

InvoiceSentinel is an open-source tool that automatically checks commercial
invoices for price anomalies that may indicate trade-based money laundering.
It uses a local AI model (via [Ollama](https://ollama.ai)) to read PDF invoices,
extract line items, and cross-reference each item against independent market
price estimates and a curated reference table. Every step runs on your own
computer — sensitive invoice data is never sent to the cloud.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [How It Works, Step by Step](#how-it-works-step-by-step)
  - [The Anchoring Problem (and How We Fixed It)](#the-anchoring-problem-and-how-we-fixed-it)
- [ELI5: I Just Want to Try It](#eli5-i-just-want-to-try-it)
- [Complete Setup Guide](#complete-setup-guide)
- [Running InvoiceSentinel](#running-invoicesentinel)
- [Understanding the Results](#understanding-the-results)
- [The Reference Price System](#the-reference-price-system)
- [Dashboard](#dashboard)
- [Configuration](#configuration)
- [Testing](#testing)
- [Privacy & Security](#privacy--security)
- [Performance](#performance)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## Why This Exists

Trade-Based Money Laundering (TBML) is one of the largest money laundering
channels in the world. The technique is simple: a criminal network sets up a
fake company (or colludes with a real one), issues inflated invoices for
goods that are never delivered or are vastly overpriced, and uses the
legitimate-looking paperwork to move money across borders.

A few real-world examples of what TBML looks like on an invoice:

| Item | Invoiced Price | Real Market Value | Red Flag |
|------|---------------|-------------------|----------|
| Cordless power drill | $8,500 | $80–$300 | **106× over-invoice** |
| Rebuilt diesel engine | $95,000 | $3,000–$15,000 | **6–30× over-invoice** |
| Turbocharger | $18,500 | $500–$3,000 | **6–37× over-invoice** |
| Carton of cooking oil | $15,000 | $200–$400 | **37–75× over-invoice** |

Manual auditors can spot these, but they cannot review every invoice in a
large import/export operation. InvoiceSentinel automates the first-pass
screening: it flags suspicious invoices for human review, and passes
normal-looking ones through without wasting an analyst's time.

---

## How It Works, Step by Step

InvoiceSentinel processes each PDF invoice through a five-stage pipeline:

```
┌──────────────┐
│  inbox/*.pdf │  ← Drop a PDF invoice here
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  1. INGEST — Read the PDF                            │
│     Uses pdfplumber (with PyPDF2 fallback) to        │
│     extract raw text from the invoice. Computes a     │
│     SHA-256 hash to prevent processing the same       │
│     file twice.                                       │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  2. EXTRACT — AI reads the invoice                   │
│     Sends the raw text to a local LLM with a prompt  │
│     that asks: "Find all line items in this invoice  │
│     and return them as structured JSON." The model    │
│     returns items like:                               │
│       {"description": "Motor diesel 6.7L",            │
│        "quantity": 8, "unit_price": 95000,            │
│        "currency": "USD", "category": "Vehículos"}    │
│                                                        │
│     ✓ If JSON parsing fails → retries once with       │
│       a corrected prompt                              │
│     ✓ After parsing → checks each description is      │
│       actually found in the source document text      │
│       (prevents AI "hallucinations" that invent items)│
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  3. REASON — Price check (two-step, anti-anchoring)  │
│                                                        │
│   ┌─────────────────────────────────────────────┐     │
│   │  Step A: Blind market estimate               │     │
│   │  The LLM receives ONLY: description,          │     │
│   │  category, and quantity. It does NOT see      │     │
│   │  the invoiced price. It estimates a range:    │     │
│   │  {"precio_min": 3000, "precio_max": 15000}    │     │
│   └──────────────────────┬──────────────────────┘     │
│                          │                            │
│   ┌──────────────────────▼──────────────────────────┐ │
│   │  Step B: Python computes                       │ │
│   │  midpoint = (3000 + 15000) / 2 = 9000          │ │
│   │  deviation = (95000 - 9000) / 9000 × 100 = 956%│ │
│   │  severity = HIGH (because deviation > 200%)     │ │
│   │  ← ALL DETERMINISTIC, not LLM judgement        │ │
│   └───────────────────────────────────────────────┘ │
│                                                        │
│   ★ Optional: reference_prices.csv override            │
│     If the CSV has a row matching "motor diesel",      │
│     that range is used INSTEAD of calling the LLM.     │
│     The CSV is the authoritative source when a match   │
│     exists. See "Reference Price System" below.        │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  4. STORE — Save everything to SQLite                │
│     Invoice, line items, LLM call logs, severity,     │
│     and justification all written to invoicesentinel  │
│     .db for audit and dashboard access.               │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  5. ROUTE — Move the PDF by severity                 │
│                                                        │
│   processed/           ← All items are NORMAL         │
│   review/high/         ← Any item is HIGH             │
│   review/moderate/     ← MODERATE or UNKNOWN items    │
│   review/extraction_failed/  ← Could not read PDF    │
│                                                        │
│   You review the flagged files via the dashboard.     │
└──────────────────────────────────────────────────────┘
```

### The Anchoring Problem (and How We Fixed It)

This is the single most important design decision in InvoiceSentinel — and the
reason the tool exists.

**What NOT to do:** In early versions, the LLM was given the full item details
including the invoiced price and asked: "Is this reasonably priced?" The model
would anchor on the given price and return a convenient range that included it.
For an $8,500 drill, it said "$5,500–$10,000 — normal." The real market value
was $80–$300. The tool was a rubber stamp.

**The fix (what we do now):** The LLM estimates a price range **without ever
seeing the invoiced price**. It receives only the description, category, and
quantity — as if you asked a domain expert "What does a 6.7L Cummins diesel
engine typically cost?" without revealing what the buyer paid. Python then
compares the invoiced price against that blind estimate deterministically.

This is why InvoiceSentinel uses a **two-step** architecture instead of asking
the LLM for a final verdict. The LLM contributes domain knowledge (market
prices); Python applies the rules (severity thresholds). Neither trusts the
other's job.

---

## ELI5: I Just Want to Try It

> *"I'm not a developer, I just want to see if this thing can catch a fake
> invoice on my laptop."*

Here's what you do, in plain language:

**You need:** A computer with at least 8 GB of RAM (preferably an Apple Silicon
Mac or a PC with a GPU).

### Step 1: Install two things

Think of these like installing a game that has two parts:

1. **Ollama** — the engine that runs the AI model on your computer.
   - Go to [ollama.com](https://ollama.com) and click Download.
   - Install it like any other application.

2. **Python 3.11 or newer** — the programming language InvoiceSentinel is
   written in.
   - On macOS: open Terminal and type `brew install python@3.14`
   - On Windows: download from [python.org](https://python.org)
   - On Linux: `sudo apt install python3 python3-venv`

### Step 2: Pull the AI model

Open Terminal (Command Prompt on Windows) and paste this:

```bash
ollama pull mistral:7b-instruct
```

This downloads a 4.1 GB AI model. It takes a few minutes. The model runs
entirely on your computer — no internet needed after download.

### Step 3: Download and set up InvoiceSentinel

```bash
# Download
git clone https://github.com/YOUR-ORG/InvoiceSentinel.git
cd InvoiceSentinel

# Set up (one-time)
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Step 4: Run it on a test invoice

The project comes with 10 sample invoices. Let's run one:

```bash
cp "tests/fixtures/demo_inbox/01_taladro_high.pdf" inbox/
invoicesentinel run
```

You'll see output like:

```
Item 1: Taladro percutor inalambrico 20V profesional | deviation: +4373.7% | severity: HIGH
Route: review/high/ — status=MANUAL_REVIEW
```

This means: InvoiceSentinel found item 1 is priced 4,374% above market.
**That's the $8,500 drill that should cost $190.** The file is moved to
`review/high/` for human review.

### Step 5: See the results in a web dashboard

```bash
invoicesentinel dashboard
```

Then open `http://localhost:8501` in your browser. You'll see the flagged
invoice with all the details.

---

## Complete Setup Guide

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | Developed on 3.14 |
| Ollama | Latest | [ollama.com](https://ollama.com) |
| LLM model | — | See below |
| RAM | 8 GB minimum | 16 GB recommended for larger models |
| Disk | 5 GB free | For the model (~4.1 GB) |

### 1. Install Ollama

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** Download from [ollama.com/download](https://ollama.com/download).

### 2. Pull the default model

```bash
ollama pull mistral:7b-instruct
```

This downloads the model we use by default. You can use other models by
changing `config.yaml` (see [Configuration](#configuration)).

Verify it works:
```bash
ollama run mistral:7b-instruct "Hello, world!"
```
Type `/bye` to exit.

Make sure the Ollama service is running:
```bash
ollama serve
```
Keep this terminal window open (or run it in the background).

### 3. Download InvoiceSentinel

```bash
git clone https://github.com/YOUR-ORG/InvoiceSentinel.git
cd InvoiceSentinel
```

### 4. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate       # Linux/macOS
# or: .venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

### 5. Verify the installation

```bash
# Run the test suite (no Ollama needed — all tests use mock AI responses)
python -m pytest tests/ -q
```

You should see `194 passed` at the end.

---

## Running InvoiceSentinel

### Process invoices in your inbox

Place PDF invoices into the `inbox/` directory, then:

```bash
invoicesentinel run
```

The tool will:
1. Read each PDF and extract text
2. Send the text to the local LLM for line item extraction
3. Check each item for hallucinations (grounding check)
4. Price-check each item (either via reference CSV or LLM blind estimate)
5. Assign a severity (NORMAL / MODERATE / HIGH / UNKNOWN)
6. Move each PDF to the appropriate folder based on severity

### Watch mode (continuous monitoring)

```bash
invoicesentinel watch
```

This polls the `inbox/` directory every 5 seconds and processes new files
automatically. Useful for always-on setups.

### Dry run (test without moving files)

```bash
invoicesentinel --dry-run run
```

Processes everything, populates the database, but **does not move files**.
Use this to test new configuration or model changes without disrupting your
real data.

### Full CLI reference

```bash
invoicesentinel [OPTIONS] COMMAND [ARGS]

Options:
  --config, -c FILE   Path to config.yaml (default: ./config.yaml)
  --dry-run, -n       Process + log results but skip file moves

Commands:
  run         Process all pending invoices in inbox/
  watch       Watch inbox/ for new files (poll every N seconds)
  dashboard   Launch Streamlit review dashboard

Examples:
  invoicesentinel run
  invoicesentinel -n run
  invoicesentinel watch --interval 10
  invoicesentinel dashboard --port 8501
```

---

## Understanding the Results

### What the output tells you

When you run `invoicesentinel run`, each line item produces output like:

```
Item 1: Motor diesel 6.7L Cummins reconstruido | range USD 3,000.00–15,000.00 | deviation: +955.6% | severity: HIGH | source: reference_csv:motor diesel
```

Breaking this down:

| Part | Meaning |
|------|---------|
| `Motor diesel 6.7L Cummins reconstruido` | What the AI extracted from the invoice |
| `range USD 3,000.00–15,000.00` | The estimated fair market price range (from CSV or LLM) |
| `deviation: +955.6%` | The invoiced price is 955.6% above the market midpoint |
| `severity: HIGH` | >200% deviation → flagged for review |
| `source: reference_csv:motor diesel` | Which reference row was used (or `llm_estimate`) |

### Severity levels

| Severity | Threshold | What happens |
|----------|-----------|-------------|
| **NORMAL** | Deviation ≤ 100% | Invoice is cleared and moved to `processed/` |
| **MODERATE** | 100% < deviation ≤ 200% | Moved to `review/moderate/` for optional review |
| **HIGH** | Deviation > 200% | **Moved to `review/high/` — requires analyst attention** |
| **UNKNOWN** | Could not determine | Moved to `review/moderate/` — currency not recognized or estimate failed |
| **UNGROUNDED** | Hallucination detected | Moved to `review/extraction_failed/` — item may not exist in source |

### Reference confidence

When a match comes from the reference CSV, the confidence tells you how
specific the match was:

- **`specific`** — The item description matched a keyword in the CSV
  (e.g. "motor diesel" in the description matched the "motor diesel" row).
  This is the most reliable kind of match.
- **`broad`** — No keyword matched the description, but the item's category
  matched a broad row. The estimated range may be less accurate. When
  deviation is extreme (>1000%), a caveat is added to the justification.

---

## The Reference Price System

`reference_prices.csv` is your organization's curated price guide. When a row
in this file matches an item on an invoice, that row's price range is used
directly — the LLM is **not called** for that item. This makes the CSV the
primary, authoritative source: you control the reference data.

### How matching works (specificity-based scoring)

The matching algorithm is designed to prefer the most specific row available:

1. Each keyword in the CSV is split into **tokens** (e.g. `"motor diesel"`
   becomes `["motor", "diesel"]`)
2. For every row, the algorithm counts how many of its tokens appear in the
   item's description
3. The row with the highest token count wins
4. If no row matches the description at all, the algorithm falls back to
   checking whether any keyword matches the item's **category name** (this
   is a "broad" match)

**Example:** An item described as "Motor diesel 6.7L Cummins reconstruido"
with category "Vehículos/Repuestos":

| CSV Row | Tokens Matching | Score | Result |
|---------|----------------|-------|--------|
| `motor diesel,$3,000-$15,000` | "motor" ✓, "diesel" ✓ | **2** | ✅ Wins |
| `repuesto consumible,$10-$500` | none | 0 | ❌ Not selected |
| `filtro,$10-$100` | none | 0 | ❌ Not selected |

Even if "repuesto" matched the category "Vehículos/Repuestos", the
description-based match (score 2) always beats the category fallback (score 0).

### Design guidelines for your reference CSV

```
keyword,category,price_min,price_max,currency,notes
motor diesel,Vehículos/Repuestos,3000,15000,USD,Rebuilt diesel engine (6.7L class)
turbocargador,Vehículos/Repuestos,500,3000,USD,Turbocharger (Holset HE400VG class)
filtro,Vehículos/Repuestos,10,100,USD,Oil/air/fuel filter
repuesto consumible,Vehículos/Repuestos,10,500,USD,Broad fallback for generic parts
```

- **Use multi-token keywords** (`motor diesel`) — they beat single-token
  keywords (`repuesto`) for matching precision
- **Tier by value** — split a category into high-value, mid-value, and
  consumable rows with appropriate price bands
- **Broad catch-all is a last resort** — have one generic row per category,
  but prefer specific rows
- **Document with notes** — the `notes` column helps other analysts
  understand what each row represents

### The 37-row starter dataset

InvoiceSentinel ships with a pre-populated `reference_prices.csv` covering:

| Category | Example Rows |
|----------|-------------|
| Vehículos/Repuestos | motor diesel, turbocargador, transmision, alternador, filtro, banda, bujía, llanta, batería, repuesto consumible |
| Maquinaria | taladro, taladro percutor, disco de corte, sierra, motor industrial, bomba, compresor |
| Electrónica | laptop, monitor, camara, servidor, router, cable, transformador |
| Textiles | camiseta, uniforme |
| Materiales de construcción | tornillo, tuerca, cemento, ladrillo |
| Alimentos | aceite, arroz |
| Químicos | acero, pintura |

---

## Dashboard

InvoiceSentinel includes a web-based review dashboard built with Streamlit:

```bash
invoicesentinel dashboard
```

Opens at `http://localhost:8501` with four tabs:

| Tab | What it shows |
|-----|---------------|
| **Test an Invoice** | Run a single PDF through the pipeline with live logs, raw LLM responses, and a dry-run toggle. Great for experimenting with new configuration. |
| **Review Queue** | Invoices waiting for analyst review, sorted by severity. Each line item has REVIEWED_OK and REVIEWED_ESCALATE buttons that write back to the database. |
| **Cleared** | Archive of invoices that passed (all NORMAL), with full audit details. |
| **Audit** | Trace any LLM call by line item ID — see prompt version, model, raw response, and latency. |

---

## Configuration

All configuration is in `config.yaml` at the project root:

```yaml
model:
  name: "mistral:7b-instruct"        # Ollama model tag to use
  ollama_host: "http://localhost:11434"

thresholds:
  normal_upper: 100                   # Max deviation % for NORMAL
  moderate_upper: 200                 # Max deviation % for MODERATE
  grounding_min_score: 50             # Min similarity ratio for hallucination check

paths:
  inbox: "inbox"                      # Watch folder for incoming PDFs
  processed: "processed"              # Cleared invoice destination
  review_high: "review/high"          # HIGH severity → MANUAL_REVIEW
  review_moderate: "review/moderate"  # MODERATE/UNKNOWN → LOW_PRIORITY
  review_extraction_failed: "review/extraction_failed"

extraction:
  min_text_chars: 50                  # Minimum chars to consider extraction successful

category_vocabulary:                  # Valid line-item categories (Spanish)
  - "Electrónica"
  - "Materiales de construcción"
  - "Textiles"
  - "Maquinaria"
  - "Alimentos"
  - "Químicos"
  - "Vehículos/Repuestos"
  - "Otro"

database:
  path: "invoicesentinel.db"          # SQLite database path
```

### Changing the AI model

To use a different Ollama model:

1. Pull it: `ollama pull qwen2.5:7b-instruct`
2. Edit `config.yaml`: change `model.name` to `qwen2.5:7b-instruct`
3. Some models respond differently to JSON formatting — you may need to
   adjust prompt templates in `prompts/`

To benchmark different models against a standard set of test items:

```bash
python scripts/compare_models.py --models mistral:7b-instruct,qwen2.5:7b-instruct
```

---

## Testing

All 194 tests use **mock LLM responses** — no live Ollama connection is
required. This means tests are fast, deterministic, and work offline.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_reason.py -v

# Run a specific test
python -m pytest tests/test_reason.py::TestDrillOverinvoiceRegression -v

# Run with coverage
python -m pytest tests/ --cov=invoicesentinel
```

### What the tests cover

| Test file | What it verifies |
|-----------|-----------------|
| `test_reason.py` | Price estimate prompts, deterministic severity calculation, reference CSV override, drill/engine/turbo regression cases |
| `test_reference_prices.py` | CSV loading, specificity-based matching with token scoring, category fallback, confidence tiers |
| `test_extract.py` | JSON parsing, retry logic, schema handling (bare array, bare object, `{"items": [...]}`), audit logging |
| `test_grounding.py` | Word-level fuzzy matching for hallucination detection |
| `test_router.py` | Severity-based routing for all 5 severity levels |
| `test_cli.py` | Full pipeline integration, dry-run behavior, NFR1 remote host guard |
| `test_single_run.py` | Single-invoice pipeline events, DB persistence, file moves |
| `test_models.py` | SQLite schema creation, CRUD operations |
| `test_dashboard_queries.py` | Dashboard data queries (llm_calls by line item) |

### Canonical regression tests

These three tests are the most important behavioral guarantees:

| Test | What it ensures |
|------|----------------|
| `test_drill_with_reference_csv_match_must_be_high` | 50×$8,500 taladro drill + CSV row `taladro percutor` ($100–$350) → **HIGH** (not NORMAL). Catches the anchoring bug regression. |
| `test_engine_matches_specific_row_not_broad` | Motor diesel → matches `motor diesel` row ($3K-$15K), not generic `repuesto` ($10-$500). Verifies specificity-based matching. |
| `test_turbo_matches_specific_row_not_broad` | Turbo cargador → matches `turbocargador` row ($500-$3K), not generic `repuesto`. Same specificity verification for a different category. |

---

## Privacy & Security

InvoiceSentinel is designed for environments where invoice data is sensitive
and must not leave the organization's control.

**Hard local-only guard (NFR1):** The LLM client refuses to connect to any
host that is not `localhost` or a `127.x.x.x` address. Attempting to use a
remote Ollama instance produces a clear error:

```
RuntimeError: [NFR1] Refusing to send invoice data to remote host
remote.example.com:11434. Set ALLOW_REMOTE_LLM=true to override.
```

Override only if you understand the risk:
```bash
ALLOW_REMOTE_LLM=true invoicesentinel run
```

**No telemetry, no analytics, no phone-home.** InvoiceSentinel contains zero
tracking code, does not collect usage statistics, and makes no network requests
other than to your local Ollama instance.

**Full audit trail (NFR4):** Every LLM call is logged to SQLite with:
- Prompt version used
- Model name
- Raw response text
- Timestamp and latency
- Associated invoice and line item IDs

This provides a complete provenance chain for every decision the tool makes.

---

## Performance

All timing assumes `mistral:7b-instruct` via Ollama with GPU acceleration.

| Setup | Time per line item | Notes |
|-------|--------------------|-------|
| Apple M-series (Metal) | 3–8 seconds | M1/M2/M3/M4 |
| NVIDIA GPU (CUDA) | 2–6 seconds | Depends on VRAM |
| CPU-only | 15–45 seconds | Highly variable |
| Raspberry Pi | 60–120+ seconds | Not recommended |

A typical invoice with 2–5 line items takes 10–40 seconds on Apple Silicon.

### Tips for faster processing

- Use `invoicesentinel watch` instead of repeated `run` commands
- CPU-only setups should process invoices one at a time
- Larger models (e.g. `llama3:8b`, `qwen2.5:14b`) are more accurate but slower
- The reference CSV avoids LLM calls entirely for matched items — a
  well-populated CSV is the best optimization

---

## Project Structure

```
InvoiceSentinel/
├── config.yaml                       # Configuration (model, thresholds, paths)
├── reference_prices.csv              # 37-row curated reference price table
├── requirements.txt                  # Python dependencies
├── setup.py                          # Package installer + CLI entry point
├── prompts/                          # LLM prompt templates
│   ├── extraction_v1.txt             #   Extraction: JSON line-item parser
│   ├── extraction_retry_v1.txt       #   Retry with raw_text context
│   ├── price_estimate_v1.txt         #   Price estimate: BLIND (no invoice price)
│   ├── price_estimate_retry_v1.txt   #   Retry with item fields
│   ├── reasoning_v1.txt              #   [RETIRED] Caused anchoring bias
│   ├── reasoning_retry_v1.txt        #   [RETIRED] Superseded by price_estimate
│   └── json_retry_v1.txt             #   Generic retry prompt (backwards compat)
├── invoicesentinel/                  # Core Python package
│   ├── cli.py                        #   Typer CLI (run, watch, dashboard)
│   ├── config.py                     #   YAML config loader
│   ├── models.py                     #   SQLite schema + ORM dataclasses
│   ├── store.py                      #   DB persistence + run summary
│   ├── ingest.py                     #   PDF ingestion + SHA-256 dedup
│   ├── extract.py                    #   LLM extraction pass + JSON parsing
│   ├── reason.py                     #   Two-step reasoning (blind + deterministic)
│   ├── reference_prices.py           #   CSV matching with specificity scoring
│   ├── grounding.py                  #   Hallucination detection (FR2.6)
│   ├── router.py                     #   Severity-based file routing (FR4.2)
│   ├── llm_client.py                 #   Ollama wrapper with NFR1 guard
│   ├── dashboard.py                  #   Streamlit web UI
│   └── dashboard_queries.py          #   Dashboard data layer
├── scripts/
│   └── compare_models.py             #   Multi-model benchmarking
├── tests/
│   ├── conftest.py                   #   Shared pytest fixtures
│   ├── fixtures/                     #   Mock LLM responses + test PDFs
│   │   ├── extraction_response_*.json
│   │   ├── reasoning_response_*.json
│   │   ├── price_estimate_anchored.json
│   │   └── demo_inbox/               #   10 synthetic test PDFs
│   └── test_*.py                     #   194 tests (all mock-based)
├── docs/
│   ├── spec_coverage.md              #   FR/NFR to file mapping
│   ├── audit_trail_example.md        #   End-to-end audit trace
│   ├── demo_walkthrough.md           #   Expected demo outcomes
│   └── known_issues.md               #   Limitations + mitigations
├── inbox/                            # Drop PDF invoices here
├── processed/                        # CLEARED invoices
└── review/
    ├── high/                         # MANUAL_REVIEW (HIGH severity)
    ├── moderate/                     # LOW_PRIORITY (MODERATE/UNKNOWN)
    └── extraction_failed/            # Unreadable / hallucinated
```

---

## Contributing

Contributions are welcome. The project follows specification-driven
development — all functional requirements are documented in `spec.md`.

### Getting started

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Make your changes
4. Run the full test suite: `python -m pytest tests/ -q`
5. Submit a pull request

### Guidelines

- **Tests first:** Every behavioral change should be accompanied by tests.
  All tests must use mock LLM responses (no live Ollama in CI).
- **Spec-driven:** If you add a feature, update `spec.md` with the new
  FR/NFR requirement. The spec is the source of truth.
- **No telemetry:** Do not add analytics, tracking, or phone-home code.
- **Keep it local:** All LLM calls must go through `llm_client.py` with
  the NFR1 guard. Do not introduce cloud LLM dependencies.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
