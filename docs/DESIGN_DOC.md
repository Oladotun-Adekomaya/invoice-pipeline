# Design doc: telecom invoice processing pipeline v1

**Author:** Invoice Pipeline Project  
**Date:** May 2026  
**Status:** Built, tested, and deployed  

---

## Problem statement

Telecom and IT expense management involves processing large volumes of vendor invoices — PDFs from carriers like Verizon, AT&T, Canada Post, and cloud providers like AWS — that arrive in varying formats with inconsistent layouts. Today this work is largely manual: an operator receives a PDF, reads it, keys the data into a system, checks it against expected values, and routes it for approval.

This is slow, doesn't scale, and introduces transcription errors. A single missed digit in a total amount or a miscoded vendor can cause payment disputes, audit failures, or incorrect client reporting.

This pipeline automates the full intake-to-approval workflow for standard invoices, with human review reserved for exceptions and full auditability at every step.

---

## Goals

- Reduce manual data entry for standard invoices to zero touches
- Handle invoices from any vendor without format-specific code
- Detect anomalies — unusual amounts, unknown vendors, bad dates — before approval
- Produce an immutable audit record of every invoice and every routing decision
- Keep every failure recoverable: no invoice is ever silently lost
- Make the system observable enough that any failure can be diagnosed in under 15 minutes

---

## Non-goals (v1)

- Direct ERP write-back (NetSuite / SAP) — designed for, not implemented
- Multi-tenant isolation per client — single pipeline, single database
- High-volume throughput — designed for tens of invoices per day, not thousands

---

## Architecture

### Pipeline stages

```
FileRecord → RawInvoiceFields → Invoice → ValidationReport → RoutingOutcome
  (intake)    (extraction)    (normalization)  (validation)    (routing)
```

**Intake** (`src/intake/`)  
Watches a local directory for new PDF files. On detection, copies the file to a UUID-named staging directory and computes a SHA-256 checksum. Returns a `FileRecord`. The staging directory acts as crash recovery — anything in staging but not in the database on startup was mid-processing and can be re-queued.

**Extraction** (`src/extraction/`)  
Two-step process: first converts the PDF to images using pdf2image (Poppler backend) at 300 DPI and runs Tesseract OCR to produce raw text. Then sends that raw text to Claude AI which returns structured JSON fields. If the Claude API is unavailable, a regex fallback handles common formats.

**Normalization** (`src/normalization/`)  
Transforms `RawInvoiceFields` (raw strings from Claude) into a typed `Invoice` dataclass. Amount strings become `Decimal`, date strings become `datetime.date`. Cross-field validation runs in `__post_init__` — due date must be after invoice date, service period end must be after start.

**Validation** (`src/validation/`)  
Runs five business rules against the normalised invoice. All rules run regardless of individual failures. Returns a `ValidationReport` listing every rule's pass/fail status and reason.

**Routing** (`src/routing/`)  
Uses the validation report to decide the outcome, writes to PostgreSQL, and fires a Slack alert for non-auto-approved invoices. Three paths: auto-approve, send to review, dead-letter.

**Web interface** (`src/api/`)  
FastAPI application serving an upload page. Accepts PDF uploads, runs the full pipeline synchronously, and returns a formatted result showing extracted fields and routing outcome.

---

## Key architecture decisions

### Why AI extraction instead of regex?

The initial implementation used regular expressions to parse invoice fields. During testing against real invoices this approach broke immediately:

- Verizon uses `Invoice Date: January 15, 2024`
- Canada Post uses `Invoice date (Y-M-D)  2025-06-21`
- AWS uses `VAT Invoice Date:  August 3, 2018`

Each new vendor format required new regex patterns. This doesn't scale — MBG processes invoices from dozens of carriers, each with their own layout conventions.

Replacing the parser with a Claude API call solved all of these simultaneously. The prompt asks Claude to extract fields and return ISO-format dates regardless of input format. Testing against Verizon, Canada Post, and AWS invoices all produced correct structured output without any vendor-specific code.

The trade-off is cost ($0.00025/invoice at current Haiku pricing) and an external API dependency. The regex fallback mitigates the dependency risk — if the API is down the pipeline degrades gracefully rather than failing completely.

**This is the right architectural decision.** Invoice parsing is fundamentally a document understanding problem. Regex treats it as a pattern matching problem. Those are different problems and regex is the wrong tool at scale.

### Why Tesseract for OCR rather than a cloud document AI service?

Tesseract is free, runs locally, and has no per-page cost or network dependency for the OCR step. The AI extraction layer (Claude) already handles the field understanding that cloud document AI services like AWS Textract or Google Document AI provide.

The architecture separates concerns cleanly:
- Tesseract: PDF → text (commodity OCR, any tool works)
- Claude: text → structured fields (semantic understanding)

Using Textract for OCR would add cost and a second cloud dependency without meaningful benefit, since Claude handles the hard part regardless.

For scanned or handwritten invoices, Tesseract accuracy degrades. That's the v2 upgrade path — detect low-confidence OCR output and route those invoices to a higher-quality OCR service before AI extraction.

### Why Prefect instead of Airflow?

Airflow requires a separate metadata database, a scheduler process, and DAG files in a specific format. For a single-developer project the operational overhead is disproportionate.

Prefect runs locally with zero infrastructure beyond the Python package. The `@flow` and `@task` decorators require almost no changes to existing Python code. The built-in UI provides run history, per-task state, and structured logs without running Kubernetes.

In production at MBG's scale, Prefect Cloud or a self-hosted Prefect server would replace the local server. The pipeline code requires no changes for that upgrade.

### Why Postgres for dead-letters instead of a message queue?

Three reasons:

1. **Queryability.** `SELECT * FROM dead_letter_invoices WHERE error_stage = 'extraction'` is simpler than consuming from a DLQ. Ops staff can investigate with SQL.

2. **Auditability.** Dead-letter rows are never deleted — they're an immutable record of every failure. Message queues are designed to be consumed and emptied.

3. **Operational simplicity.** The pipeline already depends on Postgres. Adding a queue adds a second operational dependency for a non-critical path.

The trade-off: at thousands of invoices per day, a queue would provide back-pressure that Postgres can't match. That's the point at which this decision should be revisited.

### Why `Decimal` instead of `float` for amounts?

`0.1 + 0.2` in Python is `0.30000000000000004`. For invoice amounts this matters — rounding errors compound across thousands of invoices and cause reconciliation failures. `Decimal` is exact. `NUMERIC(12,2)` in Postgres is the SQL equivalent.

### Why two `parse_date` functions instead of one?

An earlier version used `parse_date(raw, field_name, required=bool)`. The return type had to be `date | None` for the optional case, but `Invoice.invoice_date` is declared `date` (not optional). The type checker couldn't verify that `required=True` guaranteed a non-None return.

Splitting into `parse_date_required() -> date` and `parse_date_optional() -> date | None` gives the type checker everything it needs to verify correctness statically. With AI extraction returning ISO format dates, date parsing is now simpler and more reliable than with the original regex approach.

---

## Extraction pipeline detail

```
PDF file
   │
   ▼
pdf2image (Poppler)          300 DPI rasterization
   │
   ▼
Tesseract OCR                pixel → text
   │
   ▼
Raw text string              messy, layout-dependent
   │
   ▼
Claude claude-haiku-4-5-20251001       semantic field extraction
   │                         returns JSON with ISO dates, numeric amounts
   ▼
RawInvoiceFields TypedDict   clean strings, ready for normalization
```

**Prompt design:**  
The extraction prompt instructs Claude to return only a JSON object with no markdown fences, convert all dates to YYYY-MM-DD, strip currency symbols from amounts, and detect currency from context. This means the normalization stage receives consistently formatted strings regardless of the original invoice layout.

**Error handling:**  
The parser catches `json.JSONDecodeError` (Claude occasionally wraps responses in markdown despite instructions) and strips common fence patterns before parsing. Any other Claude API failure falls back to regex. Fallback events are logged with `level=warning` so they're visible in monitoring.

---

## Validation rules

| Rule | Logic | Failure path |
|------|-------|-------------|
| `required_fields` | vendor_name, invoice_date, total_amount must be present | Dead-letter |
| `amount_positive` | total_amount > 0 | Dead-letter |
| `amount_threshold` | total_amount ≤ AUTO_APPROVE_THRESHOLD (default $5,000) | Review queue |
| `vendor_known` | vendor_name not in blocklist | Dead-letter |
| `dates_sensible` | invoice_date not in future, due_date ≥ invoice_date | Dead-letter |

**Routing logic:**
- All rules pass → `AUTO_APPROVED`
- Only `amount_threshold` fails → `SENT_TO_REVIEW` (expected business event, not an error)
- Any other rule fails → `DEAD_LETTERED`

---

## Failure handling summary

| Failure | Stage | Behaviour |
|---------|-------|-----------|
| PDF corrupt or unreadable | Extraction | Retry 3× with backoff, then dead-letter |
| Claude API unavailable | Extraction | Automatic regex fallback, logged as warning |
| Claude returns invalid JSON | Extraction | Strip fences, retry parse, then regex fallback |
| Required field missing | Normalization | `NormalizationError` → dead-letter |
| Amount can't be parsed | Normalization | `NormalizationError` → dead-letter |
| Validation rule fails | Validation | Logged, included in report, routing decides outcome |
| Database write fails | Routing | Exception propagates, Prefect marks task Failed, retries |
| Slack webhook fails | Routing | Caught, logged, pipeline continues |

---

## What I'd build next (v2 proposals)

### 1. Direct PDF text extraction for digital invoices
Many invoices are digitally generated PDFs with embedded selectable text. Extracting text directly with `pdfplumber` is faster and more accurate than rasterizing and running OCR. Detect whether a PDF has embedded text first; fall back to Tesseract only for scanned documents.

### 2. ERP write-back connector
For auto-approved invoices, POST to the client's NetSuite or SAP REST API to create a bill directly. This is the highest-value unlock — zero human touch for standard invoices end to end. The routing stage already has a clean hook point for this.

### 3. Duplicate detection
The current pipeline does not check for duplicate invoice submissions. Add a validation rule that queries the `invoices` table for matching vendor + invoice number + amount. This is the most common real-world problem in AP automation.

### 4. Vendor master sync
The current `check_vendor_known` rule uses a simple blocklist. In production this should query a `vendors` table populated by a nightly sync from the client's ERP. This enables vendor-specific rules (different amount thresholds per carrier) and eliminates manual vendor management.

### 5. Confidence scoring on extraction
Claude's extraction is reliable but not infallible. Adding a confidence pass — asking Claude to also return a confidence score per field — would let the pipeline route low-confidence extractions to human review rather than processing them as if they were certain. This is especially valuable for scanned or low-quality PDFs where OCR output is noisy.

### 6. Metrics dashboard
The `pipeline_metrics` table is designed but not populated. Filling it in and building a SQL dashboard would give visibility into: invoices processed per day, dead-letter rate, p95 extraction latency, AI vs regex fallback rate, and SLO compliance.