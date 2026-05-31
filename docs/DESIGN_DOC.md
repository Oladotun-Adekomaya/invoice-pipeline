# Design doc: telecom invoice processing pipeline v1

**Author:** Invoice Pipeline Project  
**Date:** May 2026  
**Status:** Built and tested  

---

## Problem statement

Telecom and IT expense management involves processing large volumes of vendor invoices — PDFs from carriers like Verizon, AT&T, and T-Mobile — that arrive in varying formats with inconsistent layouts. Today this work is largely manual: an operator receives a PDF, reads it, keys the data into a system, checks it against expected values, and routes it for approval.

This is slow, doesn't scale, and introduces transcription errors. A single missed digit in a total amount or a miscoded vendor can cause payment disputes, audit failures, or incorrect client reporting.

This pipeline automates the full intake-to-approval workflow for standard invoices, with human review reserved for exceptions and full auditability at every step.

---

## Goals

- Reduce manual data entry for standard invoices to zero touches
- Detect anomalies — duplicates, unusual amounts, unknown vendors, bad dates — before approval
- Produce an immutable audit record of every invoice and every routing decision
- Keep every failure recoverable: no invoice is ever silently lost
- Make the system observable enough that any failure can be diagnosed in under 15 minutes using only logs and the database

---

## Non-goals (v1)

- Direct ERP write-back (NetSuite / SAP) — the system is designed for this but does not implement it
- Multi-tenant isolation per client — single pipeline, single database
- ML-based extraction — rule-based regex only; sufficient for well-structured PDFs
- High-volume throughput — designed for tens of invoices per day, not thousands

---

## Architecture

### Pipeline stages

The pipeline is a linear sequence of five stages. Each stage has a single responsibility and a clearly defined input and output type. This makes each stage independently testable and replaceable.

```
FileRecord → RawInvoiceFields → Invoice → ValidationReport → RoutingOutcome
  (intake)    (extraction)    (normalization)  (validation)    (routing)
```

**Intake** (`src/intake/`)  
Watches a local directory for new PDF files. On detection, copies the file to a UUID-named staging directory and computes a SHA-256 checksum. Returns a `FileRecord`. The staging directory acts as crash recovery — anything in staging but not in the database on startup was mid-processing and can be re-queued.

**Extraction** (`src/extraction/`)  
Converts the PDF to images using `pdf2image` (Poppler backend) at 300 DPI, then runs Tesseract OCR on each page. The raw text is parsed with regular expressions into a `RawInvoiceFields` TypedDict. All fields are optional at this stage — OCR is imperfect and any field may be missing.

**Normalization** (`src/normalization/`)  
Transforms `RawInvoiceFields` (raw strings) into a typed `Invoice` dataclass. Amount strings become `Decimal`, date strings become `datetime.date`, service period ranges are split into start/end dates. Cross-field validation runs in `__post_init__` — due date must be after invoice date, service period end must be after start. Raises `NormalizationError` with field-level detail on failure.

**Validation** (`src/validation/`)  
Runs five business rules against the normalised invoice. All rules run regardless of individual failures — you want the full picture, not just the first problem. Returns a `ValidationReport` listing every rule's pass/fail status and reason.

**Routing** (`src/routing/`)  
Uses the validation report to decide the outcome, writes to PostgreSQL, and fires a Slack alert for non-auto-approved invoices. Three paths: auto-approve, send to review, dead-letter. The database write always happens before the Slack notification — a failed alert never causes data loss.

---

## Key architecture decisions

### Why Tesseract instead of AWS Textract or Google Document AI?

Tesseract is free, runs locally, and has no per-page cost or network dependency. For a single-operator demo environment processing tens of invoices per day, cloud OCR APIs are operational overhead without proportionate benefit.

The extraction interface (`extract_text(path) -> str`) is intentionally thin. Swapping Tesseract for Textract means replacing `ocr_client.py` only — the parser, normaliser, and everything downstream are unaffected.

Accuracy trade-off: Tesseract at 300 DPI performs well on clean, digitally-generated PDFs. For scanned or handwritten invoices, cloud document AI would be meaningfully better. That's the v2 upgrade path.

### Why Prefect instead of Airflow or raw Python?

Airflow requires a separate metadata database, a scheduler process, and DAG files written in a specific way. For a single-developer project the operational overhead is disproportionate.

Prefect runs locally with zero infrastructure beyond the Python package. The `@flow` and `@task` decorators require almost no code changes to an existing Python script. The built-in UI provides run history, per-task state, and structured logs out of the box — everything needed to demo observability without running Kubernetes.

In production at MBG's scale, Prefect Cloud or a self-hosted Prefect server would replace the local server used here. The pipeline code requires no changes for that upgrade.

### Why Postgres for dead-letters instead of a message queue?

A message queue (RabbitMQ, SQS) would be the typical choice for dead-letter handling in a distributed system. For this pipeline, Postgres is better for three reasons:

1. **Queryability.** `SELECT * FROM dead_letter_invoices WHERE error_stage = 'validation'` is simpler than consuming from a DLQ and filtering. Ops staff can investigate failures with SQL they already know.

2. **Auditability.** Dead-letter rows are never deleted — they're an immutable record of every failure. Message queues are designed to be consumed and emptied.

3. **Operational simplicity.** One less infrastructure component. The pipeline already depends on Postgres; adding a queue would add a second operational dependency for a non-critical path.

The trade-off: if invoice volume grew to thousands per day, a queue would provide back-pressure and fan-out that Postgres can't easily match. That's the point at which this decision should be revisited.

### Why `Decimal` instead of `float` for amounts?

Floating point arithmetic is not exact. `0.1 + 0.2` in Python is `0.30000000000000004`. For invoice amounts this matters: rounding errors compound across thousands of invoices and can cause reconciliation failures that are painful to debug.

`Decimal` stores exact values and performs exact arithmetic. It is the correct type for any financial calculation. The `NUMERIC(12,2)` column type in Postgres is the SQL equivalent for the same reason.

### Why separate `parse_date_required` and `parse_date_optional`?

An earlier version used a single `parse_date(raw, field_name, required=bool)` function. This caused a type error: the function's return type had to be `date | None` to satisfy the optional case, but the `Invoice` dataclass expects `invoice_date: date` (not optional). The type checker couldn't verify that `required=True` guaranteed a non-None return.

Splitting into two functions with unambiguous return types (`-> date` and `-> date | None`) resolves this cleanly. The type checker can now verify correctness statically, and the intent of each call site is explicit.

### Why `structlog` instead of Python's built-in `logging`?

Two reasons: context variables and JSON output.

`structlog.contextvars.bind_contextvars(pipeline_run_id=run_id)` binds the run ID once at the start of the Prefect flow. Every subsequent log call in any module — across any number of function calls — automatically includes that ID. With stdlib logging you'd have to thread the run ID through every function signature or use a thread-local, both of which are more error-prone.

JSON output is machine-readable. In production, structured logs can be indexed and queried (e.g. "show me all runs where extraction took more than 30 seconds"). Plain text logs cannot.

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

This distinction is deliberate. A large invoice is a normal thing that needs human sign-off. An invoice from an unknown vendor or with a future date is a data quality problem that should not enter the approval workflow.

---

## Data schema

### `invoices` table
Stores every invoice that passed validation (approved or review). Uses `NUMERIC(12,2)` for amounts, `TIMESTAMPTZ` for all timestamps, `UUID` primary key. Status values: `pending`, `approved`, `review`, `rejected`.

### `dead_letter_invoices` table
Stores every invoice that failed validation or normalisation. Contains the full error detail as `JSONB` including which rules failed and why. **Rows are never deleted** — this table is an immutable audit log.

### `pipeline_metrics` table
Reserved for SLO tracking. Designed to record counters (invoices processed, dead-lettered) and durations (extraction time, end-to-end time) for operational dashboards.

---

## Failure handling summary

| Failure | Stage | Behaviour |
|---------|-------|-----------|
| PDF is corrupt or unreadable | Extraction | Retry 3× with backoff, then dead-letter |
| Poppler not installed | Extraction | Immediate error with clear message |
| Required field missing after OCR | Normalization | `NormalizationError` → dead-letter |
| Amount can't be parsed | Normalization | `NormalizationError` → dead-letter |
| Service period unparseable | Normalization | Returns `None, None` — non-fatal, continues |
| Validation rule fails | Validation | Logged, included in report, routing decides outcome |
| Database write fails | Routing | Exception propagates, Prefect marks task Failed, retries |
| Slack webhook fails | Routing | Caught, logged, pipeline continues — notifications are best-effort |

---

## What I'd build next (v2 proposals)

### 1. Vendor master sync
The current `check_vendor_known` rule uses a simple blocklist. In production this should query a `vendors` table populated by a nightly sync from the client's ERP system. This eliminates manual vendor additions and enables vendor-specific validation rules (e.g. different amount thresholds per carrier).

### 2. ERP write-back connector
For auto-approved invoices, POST to the client's NetSuite or SAP REST API to create a bill directly. This is the highest-value unlock — zero human touch for standard invoices end to end. The routing stage already has a clear hook point for this.

### 3. Duplicate detection
The current pipeline does not check for duplicate invoice submissions (same vendor + invoice number + amount already in the database). This is the most common real-world problem in AP automation and should be added as a validation rule querying the `invoices` table.

### 4. Improved OCR for scanned invoices
Some telecom invoices arrive as scanned images rather than digitally-generated PDFs. Tesseract accuracy degrades significantly on these. Upgrading the `ocr_client.py` to use AWS Textract or Google Document AI for scanned documents (detected by low confidence scores or low character density) would handle the long tail of invoice formats.

### 5. Metrics and SLO dashboard
The `pipeline_metrics` table is designed but not populated. Filling it in and building a simple SQL dashboard (or connecting to Grafana) would give visibility into: invoices processed per day, dead-letter rate, p95 extraction latency, and SLO compliance.