# Demo Dataset Walkthrough

10 synthetic invoice PDFs in `tests/fixtures/demo_inbox/` covering the full range of
expected routing outcomes. Use with `invoicesentinel run --dry-run` to see expected
outcomes without moving files.

---

### `01_normal_cleared.pdf`
- **Lines**: 18V Electric drill (10×45.50 USD), M8 Screw (200×2.30 USD)
- **Expected**: Both items → NORMAL → invoice → `CLEARED` → `processed/`

### `02_high_overpriced.pdf`
- **Lines**: 20V Industrial drill (5×5000 USD), Gearbox (100×350 USD)
- **Expected**: Drill at 5000 USD → HIGH deviation → invoice → `MANUAL_REVIEW` → `review/high/`

### `03_moderate.pdf`
- **Lines**: 10mm Electrical cable (50×18 USD), Thermomagnetic switch (25×85 USD)
- **Expected**: Deviation moderately above market → MODERATE (no HIGH) → invoice → `MANUAL_REVIEW_LOW_PRIORITY` → `review/moderate/`

### `04_unknown_currency.pdf`
- **Lines**: Gate valve (100×45.00 XYZ) — currency `XYZ` is not a known ISO code
- **Expected**: Currency UNKNOWN → route by `currency` rule → `MANUAL_REVIEW_LOW_PRIORITY` → `review/moderate/`

### `05_high_moderate_mixed.pdf`
- **Lines**: 50HP Motor (1×15000 USD, potentially HIGH), SKF Bearing (500×12 USD), Nut (1000×0.50 USD)
- **Expected**: At least one HIGH → `MANUAL_REVIEW` → `review/high/` (HIGH overrides MODERATE)

### `06_multiple_normal.pdf`
- **Lines**: Cement (1000×8.50 USD), Sand (500×35 USD), Lime (200×5.20 USD) — all commodity items with tight market ranges
- **Expected**: All NORMAL → `CLEARED` → `processed/`

### `07_high_electronics.pdf`
- **Lines**: PCB Board (20×2500 USD — likely HIGH), Temp sensor (100×0.50 USD — potentially underpriced)
- **Expected**: HIGH item exists → `MANUAL_REVIEW` → `review/high/`

### `08_single_item_normal.pdf`
- **Lines**: Technical consulting (1×2500 USD) — service item, hard to price
- **Expected**: Service items may get moderate deviation or UNKNOWN → depends on LLM estimate; likely → `CLEARED` or `MANUAL_REVIEW_LOW_PRIORITY`

### `09_high_many_items.pdf`
- **Lines**: Compressor (3×8500 USD), Filter (10×150 USD), Hydraulic hose (100×3200 USD — extremely high)
- **Expected**: Hose at 3200 USD → HIGH → `MANUAL_REVIEW` → `review/high/`

### `10_unknown_currency_moderate.pdf`
- **Lines**: Galvanized steel sheet (200×120.00 XXX)
- **Expected**: Currency UNKNOWN → `MANUAL_REVIEW_LOW_PRIORITY` → `review/moderate/`

---

## Summary Table

| # | File | Expected Status | Route |
|---|---|---|---|
| 1 | `01_normal_cleared.pdf` | `CLEARED` | `processed/` |
| 2 | `02_high_overpriced.pdf` | `MANUAL_REVIEW` | `review/high/` |
| 3 | `03_moderate.pdf` | `MANUAL_REVIEW_LOW_PRIORITY` | `review/moderate/` |
| 4 | `04_unknown_currency.pdf` | `MANUAL_REVIEW_LOW_PRIORITY` | `review/moderate/` |
| 5 | `05_high_moderate_mixed.pdf` | `MANUAL_REVIEW` | `review/high/` |
| 6 | `06_multiple_normal.pdf` | `CLEARED` | `processed/` |
| 7 | `07_high_electronics.pdf` | `MANUAL_REVIEW` | `review/high/` |
| 8 | `08_single_item_normal.pdf` | `CLEARED` or `MANUAL_REVIEW_LOW_PRIORITY` | `processed/` or `review/moderate/` |
| 9 | `09_high_many_items.pdf` | `MANUAL_REVIEW` | `review/high/` |
| 10 | `10_unknown_currency_moderate.pdf` | `MANUAL_REVIEW_LOW_PRIORITY` | `review/moderate/` |

*Actual outcomes depend on the LLM model's market price estimates. Run with `--dry-run` first.*
