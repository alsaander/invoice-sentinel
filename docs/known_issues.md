# Known Issues

## 1. LLM produces schema-invalid JSON after retry (Partially Mitigated)

**Status**: Partially mitigated by context-aware retry prompts and `format:json`
in Ollama API requests.

**History**: During a live M6b test, the reasoning retry produced
`{"respuesta": "Nueva respuesta válida en formato JSON"}` and `[]` — both
valid JSON but not matching the expected reasoning schema. This resulted in
severity=UNKNOWN for both line items. The bug then cascaded: the router issued
no check for UNKNOWN severity, so the invoice was moved to `processed/` with
status=CLEARED.

**Fixes applied**:
1. **Part A (routing)**: FR4.2 updated — UNKNOWN severity now routes to
   `review/moderate/` (MANUAL_REVIEW_LOW_PRIORITY), never CLEARED.
2. **Part B (prompts)**: `extraction_retry_v1.txt` and `reasoning_retry_v1.txt`
   include the exact target JSON schema so the retry message is self-contained.
   `llm_client.py` now sends `"format": "json"` in the Ollama request body,
   which constrains output to valid JSON for most Ollama models.

**Residual risk**: If both the initial and retry responses are valid JSON but
fail the schema check (e.g. wrong keys, wrong type), the item will still get
severity=UNKNOWN. This is now a safe failure: the item is routed to review
instead of being silently cleared.

## 2. Dependency Deprecation Warning

PyPDF2 emits a deprecation warning at import time, recommending migration to
`pypdf`. This is cosmetic and does not affect functionality.
