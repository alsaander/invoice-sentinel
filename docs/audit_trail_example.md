# Audit Trail Walkthrough

This document traces one invoice end-to-end to demonstrate how an AML analyst or
compliance officer would reconstruct the decision trail for a flagged line item
(§11 audit risk mitigation, G5, NFR4).

---

## Sample Invoice: `02_high_overpriced.pdf`

```
FACTURA - Empresa Beta S.A.
Fecha: 2026-06-02
Cantidad: 5  Descripcion: Taladro industrial 20V
Precio Unitario: 5000.00 USD
Cantidad: 100  Descripcion: Caja de engranajes
Precio Unitario: 350.00 USD
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
    "description": "Taladro industrial 20V",
    "category": "Electrónica"
  },
  {
    "quantity": 100,
    "unit_price": 350,
    "currency": "USD",
    "description": "Caja de engranajes",
    "category": "Maquinaria"
  }
]
```

**Validation**: `category` values checked against vocabulary — both pass.
No retry needed.

---

## Step 3: Line Items Created (`line_items`)

### Line item 1 — Taladro industrial 20V

| Column | Value (before reasoning) |
|---|---|
| `description` | `Taladro industrial 20V` |
| `quantity` | 5.0 |
| `unit_price` | 5000.0 |
| `currency` | `USD` |
| `category` | `Electrónica` |
| `category_raw` | NULL |
| `severity` | `PENDING` |

### Line item 2 — Caja de engranajes

| Column | Value (before reasoning) |
|---|---|
| `description` | `Caja de engranajes` |
| `quantity` | 100.0 |
| `unit_price` | 350.0 |
| `currency` | `USD` |
| `category` | `Maquinaria` |
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
- Descripcion: Taladro industrial 20V
- Categoria: Electronica
- Cantidad: 5
- Precio unitario: 5000 USD
```

Raw LLM response:
```json
{
  "precio_min": 150,
  "precio_max": 500,
  "punto_medio": 325,
  "desviacion_significativa": true,
  "justificacion": "El precio de 5000 USD por un taladro industrial de 20V es extremadamente alto. En el mercado internacional, taladros industriales de esta especificacion suelen costar entre 150 y 500 USD dependiendo de la marca. Un precio de 5000 USD representa una desviacion de mas del 1000% respecto al punto medio del rango estimado, lo cual es altamente sospechoso y consistente con sobrefacturacion en TBML."
}
```

### Deterministic severity computation (Python, not LLM):
```
unit_price = 5000
punto_medio = 325
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
  "precio_min": 200,
  "precio_max": 600,
  "punto_medio": 400,
  "desviacion_significativa": false,
  "justificacion": "El precio de 350 USD por una caja de engranajes esta dentro del rango de mercado esperado de 200-600 USD."
}
```

```
deviation_pct = (350 - 400) / 400 * 100 = -12.5%
abs_deviation = 12.5% ≤ 100 → severity = NORMAL
```

---

## Step 5: Updated Line Items After Reasoning

### Line item 1 — Taladro (HIGH)

| Column | Updated value |
|---|---|
| `est_market_low` | 150.0 |
| `est_market_high` | 500.0 |
| `deviation_pct` | +1438.46 |
| `severity` | `HIGH` |
| `reference_source` | `llm_estimate` |
| `justification` | Full LLM CoT text verbatim |

### Line item 2 — Caja de engranajes (NORMAL)

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
  ├─ Taladro industrial 20V  (HIGH, +1438.46%)
  │   Justification: El precio de 5000 USD...
  │   [✓ Reviewed OK]  [⚠ Escalate]
  └─ Caja de engranajes  (NORMAL, -12.5%)
```

Analyst clicks **⚠ Escalate** → `analyst_verdict = 'REVIEWED_ESCALATE'` written
to `line_items.id=1`.

---

## Regulatory Q&A

**Q: Why was this invoice flagged?**  
A: Because the LLM estimated a fair market range of 150–500 USD for a "Taladro
industrial 20V", but the invoice priced it at 5000 USD. The deterministic
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
