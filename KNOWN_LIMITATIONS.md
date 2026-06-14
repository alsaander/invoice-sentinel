# InvoiceSentinel — Known Limitations & Strategic Roadmap

This document outlines the current technical limitations of InvoiceSentinel v1.0 and provides a risk-mitigation framework. In an AML/Compliance production environment, understanding tool limitations is critical to preventing false negatives and managing alert fatigue.

---

## 1. Unit of Measure (UOM) & Packaging Discrepancies

### The Challenge
InvoiceSentinel's blind reasoning pipeline (FR3.1) sends the item `description`, `category`, and `quantity` to the local LLM or references the `reference_prices.csv` to establish a fair-market price range. However, commercial invoices often use ambiguous or non-standard packaging descriptions.

* **Scenario:** An invoice lists `Quantity: 1`, `Description: Rebuilt Diesel Engine`, `Unit Price: $120,000`. 
* **The Issue:** The local reference dataset or the LLM might estimate a single engine at `$3,000–$15,000`, triggering a massive **HIGH** severity alert (+700% deviation). In reality, the line item might represent a "Pack of 10 units" or a crate containing associated installation kits, which wasn't explicitly parsed into the numeric quantity field.

### Current Mitigation (v1.0)
* **Analyst Triage Context:** The Streamlit dashboard surfaces the verbatim `justification` and the original invoice text block so the human analyst can quickly verify if the price anomaly is a real TBML indicator or a packaging/UOM misunderstanding.
* **Fuzzy/Broad Matches:** Broad reference matches inject a caution flag into the justification if deviations cross extreme thresholds (>1000%), alerting the analyst to look for bulk packaging indicators.

### Roadmap (v1.1)
* Implement a **UOM Normalization Layer** using text-embedding models. This layer will extract and standardize units (e.g., *box, crate, metric ton, pack of X*) and mathematically adjust the evaluated unit price before triggering deterministic Python routing rules.

---

## 2. Foreign Exchange (FX) Risk & Static Currency Matching

### The Challenge
TBML schemes frequently exploit cross-border transactions involving multiple currencies to obfuscate value movement. To remain **100% local and offline** (NFR1), InvoiceSentinel v1.0 does not call external live FX rate APIs.

* **The Issue:** The `reference_prices.csv` catalog is primarily denominated in USD. If an invoice lists items in EUR, GBP, or AED, comparing the raw numbers directly against a USD reference table will corrupt the `% deviation` math, causing critical false positives or dangerous false negatives.

### Current Mitigation (v1.0)
* **Fail-Safe Routing (FR4.2):** Any line item with a currency not explicitly matching the reference dataset or failing to parse is immediately tagged as `UNKNOWN` currency. 
* **Strict Isolation:** `UNKNOWN` items are automatically routed to `review/moderate/` under the status `MANUAL_REVIEW_LOW_PRIORITY`. They are *never* silently cleared, ensuring no cross-currency anomaly slips through automated triage.

### Roadmap (v1.1)
* Introduce a locally maintained, static `fx_rates.json` config file that analysts can update weekly or monthly. The pipeline will deterministically convert all parsed currencies to a base currency (USD/EUR) prior to running the price-checking logic.

---

## 3. Scope Limitation: Strategic Goods & Dual-Use Items

### The Challenge
While InvoiceSentinel is highly effective at detecting **economic manipulation** (over-invoicing/under-invoicing), TBML and proliferation financing often involve **Strategic Goods / Dual-Use Goods** (e.g., specific industrial valves, high-spec civilian drones, or chemicals that can be diverted for military use). An invoice might have perfectly normal market pricing but still violate international sanctions or export control laws.

### Current Mitigation (v1.0)
* InvoiceSentinel is strictly an economic screening tool designed to complement—not replace—traditional Sanctions/PEP/Dual-Use screening engines. 

### Roadmap (v1.2)
* Expand the controlled vocabulary schema to include a `high_risk_dual_use` boolean flag. This will cross-reference extracted item descriptions against a local keyword dictionary mapped to the EU/UN dual-use export control lists, automatically escalating items matching high-risk technical nomenclature regardless of price validity.