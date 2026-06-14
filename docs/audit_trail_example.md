# Audit Trail Walkthrough

This document traces one invoice end-to-end to demonstrate how an AML analyst or
compliance officer would reconstruct the decision trail for a flagged line item
(§11 audit risk mitigation, G5, NFR4).

---

## Sample Invoice: `02_high_overpriced.pdf`

```
INVOICE - Beta Corp S.A.
Date: 2026-06-02
Quantity: 5  Description: 20V Industrial drill
Unit Price: 5000.00 USD
Quantity: 100  Description: Gearbox
Unit Price: 350.00 USD
```

---

## Step 1: Ingestion (`invoices` row)

| Column | Value |
|---|---|
| `id` | 2 |
| `filename` | `02_high_overpriced.pdf` |
| `file_sha256` | `sha256_02_high_overpriced.pdf` (hex) |
| `extraction_method` | `pdfplumber` |
| `status` | `MANUAL_REVIEW` (after routing) |
| `moved_to_path` | `review/high/02_high_overpriced.pdf` |

**Key audit observation**: `file_sha256` ensures dedup — same file resubmitted
would map to the same hash and be skipped.

---

## Step 2: Extraction LLM Call (`llm_calls` — extraction)

| Column | Value |
|---|---|
| `call_type` | `extraction` |
| `prompt_version` | `extraction_v1.txt` |
| `model` | `llama3:8b` |
| `latency_ms` | 1234 (example) |
| `raw_response` | See below |

Extraction prompt sent the full raw text (from pdfplumber). Raw response:

```json
[
  {
    "quantity": 5,
    "unit_price": 5000,
    "currency": "USD",
    "description": "20V Industrial drill",
    "category": "Electronics"
  },
  {
    "quantity": 100,
    "unit_price": 350,
    "currency": "USD",
    "description": "Gearbox",
    "category": "Machinery"
  }
]
```

**Validation**: `category` values checked against vocabulary — both pass.
No retry needed.

---

## Step 3: Line Items Created (`line_items`)

### Line item 1 — 20V Industrial drill

| Column | Value (before reasoning) |
|---|---|
| `description` | `20V Industrial drill` |
| `quantity` | 5.0 |
| `unit_price` | 5000.0 |
| `currency` | `USD` |
| `category` | `Electronics` |
| `category_raw` | NULL |
| `severity` | `PENDING` |

### Line item 2 — Gearbox

| Column | Value (before reasoning) |
|---|---|
| `description` | `Gearbox` |
| `quantity` | 100.0 |
| `unit_price` | 350.0 |
| `currency` | `USD` |
| `category` | `Machinery` |
| `category_raw` | NULL |
| `severity` | `PENDING` |

---

## Step 4: Reasoning LLM Call (`llm_calls` — reasoning)

Two separate reasoning calls, one per line item. Traceable via `line_item_id`.

### Reasoning for line item 1 (`line_item_id = 1`)

| Column | Value |
|---|---|
| `call_type` | `reasoning` |
| `prompt_version` | `reasoning_v1.txt` |
| `model` | `llama3:8b` |
| `latency_ms` | 2100 (example) |
| `raw_response` | See below |

Reasoning prompt sent:
```
- Description: 20V Industrial drill
- Category: Electronics
- Quantity: 5
- Unit price: 5000 USD
```

Raw LLM response:
```json
{
  "price_min": 150,
  "price_max": 500,
  "midpoint": 325,
  "significant_deviation": true,
  "justification": "The price of 5000 USD for a 20V industrial drill is extremely high. In the international market, industrial drills of this specification typically cost between 150 and 500 USD depending on the brand. A price of 5000 USD represents a deviation of more than 1000% from the midpoint of the estimated range, which is highly suspicious and consistent with over-invoicing in TBML."
}
```

### Deterministic severity computation (Python, not LLM):
```
unit_price = 5000
midpoint = 325
deviation_pct = (5000 - 325) / 325 * 100 = +1438.46%
abs_deviation = 1438.46% > 200 → severity = HIGH
```

### Reasoning for line item 2 (`line_item_id = 2`)

| Column | Value |
|---|---|
| `call_type` | `reasoning` |
| `prompt_version` | `reasoning_v1.txt` |
| `model` | `llama3:8b` |
| `latency_ms` | 1800 (example) |
| `raw_response` | See below |

```json
{
  "price_min": 200,
  "price_max": 600,
  "midpoint": 400,
  "significant_deviation": false,
  "justification": "The price of 350 USD for a gearbox is within the expected market range of 200-600 USD."
}
```

```
deviation_pct = (350 - 400) / 400 * 100 = -12.5%
abs_deviation = 12.5% ≤ 100 → severity = NORMAL
```

---

## Step 5: Updated Line Items After Reasoning

### Line item 1 — Drill (HIGH)

| Column | Updated value |
|---|---|
| `est_market_low` | 150.0 |
| `est_market_high` | 500.0 |
| `deviation_pct` | +1438.46 |
| `severity` | `HIGH` |
| `reference_source` | `llm_estimate` |
| `justification` | Full LLM CoT text verbatim |

### Line item 2 — Gearbox (NORMAL)

| Column | Updated value |
|---|---|
| `est_market_low` | 200.0 |
| `est_market_high` | 600.0 |
| `deviation_pct` | -12.5 |
| `severity` | `NORMAL` |
| `reference_source` | `llm_estimate` |
| `justification` | Full LLM CoT text verbatim |

---

## Step 6: Routing Decision

Routing rules (FR4.2):
- Line item 1: severity = `HIGH`
- Line item 2: severity = `NORMAL`
- Any HIGH? **Yes** → route to `review/high/`, status = `MANUAL_REVIEW`

File moved: `inbox/02_high_overpriced.pdf` → `review/high/02_high_overpriced.pdf`

---

## Step 7: Analyst Review (Dashboard)

Analyst opens Streamlit dashboard, sees invoice in Review Queue:

```
🚩 02_high_overpriced.pdf  [MANUAL_REVIEW]
  ├─ 20V Industrial drill  (HIGH, +1438.46%)
  │   Justification: The price of 5000 USD...
  └─ Gearbox  (NORMAL, -12.5%)
```

Analyst clicks **⚠ Escalate** → `analyst_verdict = 'REVIEWED_ESCALATE'` written
to `line_items.id=1`.

---

## Regulatory Q&A

**Q: Why was this invoice flagged?**  
A: Because the LLM estimated a fair market range of 150–500 USD for a "20V
Industrial drill", but the invoice priced it at 5000 USD. The deterministic
computation gave a deviation of +1438%, which exceeded the 200% HIGH threshold.

**Q: How do I know the system didn't just make this up?**  
A: The raw LLM response is stored verbatim in `llm_calls` (row with
`line_item_id=1`, `call_type='reasoning'`), along with the prompt version
(`reasoning_v1.txt`) and the model name (`llama3:8b`). You can replay the exact
prompt against the same model version to verify reproducibility.

**Q: What if the reference_prices.csv had a matching row?**  
A: It would have overridden the LLM's estimate for the deterministic bucket.
The `reference_source` column would read `reference_csv:<row>` instead of
`llm_estimate`, and the decision would be anchored to the user-maintained
price table.
