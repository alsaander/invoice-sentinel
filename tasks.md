# M2 — Ingestion (FR1): Task Breakdown

## Task 1: Install dependencies
Add `pdfplumber`, `PyPDF2`, `fpdf2` (test PDF generation) to requirements.txt and venv.

## Task 2: Implement ingest.py
- `compute_sha256(path: str) -> str`
- `extract_text(path: str, min_text_chars: int = 50) -> tuple[str, str]`
  - Try pdfplumber; if text < min_text_chars → fallback to PyPDF2
  - Returns (text, method) where method is `pdfplumber` | `pypdf2` | `failed`
- `process_single_pdf(path: str, cfg: Config, conn: sqlite3.Connection) -> Invoice`
  - Hash, extract, check dedup, insert row, handle extraction failure routing
- `process_inbox(cfg: Config, conn: sqlite3.Connection) -> list[Invoice]`
  - Iterate inbox/*.pdf, call process_single_pdf for each
- *Test:* unit tests for hash, extraction, dedup, routing

## Task 3: Create test PDF generators
- `tests/conftest.py` PDF generation helpers:
  - `make_text_pdf(path, text)` — minimal PDF with text layer
  - `make_empty_pdf(path)` — PDF with no text (simulates scanned/image-only)
  - `make_pdf_with_minimal_text(path, text)` — for fallback testing

## Task 4: Write test_ingest.py
- Valid text PDF → `extraction_method='pdfplumber'`, row inserted
- Empty/scanned PDF → `status='EXTRACTION_FAILED'`, moved to review/extraction_failed/
- pdfplumber→PyPDF2 fallback (mock pdfplumber to return short text)
- Duplicate hash → skipped, no second row
- Verify moved_to_path, file_sha256, raw_text_chars correctness

## Task 5: Run tests, verify §10 FR1 acceptance criteria
- Confirm valid PDF → invoices row with extraction_method='pdfplumber'
- Confirm empty PDF → status='EXTRACTION_FAILED', moved to review/extraction_failed/
- All tests pass; wait for go-ahead before M3
