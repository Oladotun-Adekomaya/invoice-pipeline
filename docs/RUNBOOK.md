# Invoice pipeline — operations runbook

**Last updated:** May 2026  
**Author:** Invoice Pipeline Project  
**Purpose:** Operational reference for running, monitoring, and recovering the invoice processing pipeline.

---

## Overview

This pipeline ingests telecom and IT invoices as PDFs, extracts structured data using Tesseract OCR followed by Claude AI field extraction, normalises and validates the data, then routes each invoice to automatic approval or a human review queue. Every decision is persisted to PostgreSQL with a full audit trail.

```
PDF (folder drop or web upload)
      │
  [Intake]         Stage file, compute SHA-256, emit FileRecord
      │
[Extraction]       Convert PDF → images (pdf2image/Poppler)
                   Run Tesseract OCR → raw text
                   Send raw text to Claude AI → structured fields
      │
[Normalization]    Raw strings → typed Python: Decimal amounts, date objects, UUID invoice ID
      │
[Validation]       5 business rules: required fields, positive amount, threshold, vendor, dates
      │
   ┌──┴──┐
[Approve]      [Review]      [Dead-letter]
Postgres       Postgres       Postgres
               + Slack        + Slack
```

**Orchestrator:** Prefect  
**Database:** PostgreSQL 16 (Docker)  
**OCR engine:** Tesseract 5.5 + pdf2image (Poppler)  
**AI extraction:** Claude claude-haiku-4-5-20251001 (Anthropic API)  
**Language:** Python 3.12  

---

## Running the pipeline

### Prerequisites

Ensure the following are running before starting anything:

```bash
# Check Docker / Postgres is up
docker ps | grep invoice-db

# If not running, start it
docker start invoice-db

# Verify Tesseract is available
tesseract --version

# Verify Poppler is available
pdfinfo --version
```

### Environment variables required

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Claude API key for AI field extraction |
| `SLACK_WEBHOOK_URL` | Slack alerts for review/dead-letter events |
| `AUTO_APPROVE_THRESHOLD` | Max invoice amount for auto-approval (default $5,000) |

### Activate the environment

```bash
cd invoice-pipeline
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
```

### Run a single invoice manually

```bash
python -m src.pipeline path/to/invoice.pdf
```

Output will be one of:
- `result: auto_approved` — invoice written to `invoices` table, status = approved
- `result: sent_to_review` — invoice written to `invoices` table, status = review, Slack alert sent
- `result: dead_lettered` — invoice written to `dead_letter_invoices` table, Slack alert sent

### Run via the web interface

The FastAPI web interface is available at `https://invoice-pipeline.lameda-ai.xyz`. Upload any PDF invoice and the result is displayed on the page — extracted fields, routing outcome, and any failed validation rules.

### Run the folder watcher (continuous mode)

In one terminal, start Prefect server:
```bash
prefect server start
```

In a second terminal, start the watcher:
```bash
python src/intake/watcher.py
```

Drop any PDF into the `./incoming` folder. The watcher detects it and triggers the pipeline automatically.

Prefect UI available at: `http://127.0.0.1:4200` (local) or `https://invoice-pipeline-prefect.lameda-ai.xyz` (production)

### Run tests

```bash
pytest tests/unit -v               # unit tests only (no dependencies needed)
pytest tests/ --cov=src            # all tests with coverage
```

---

## Extraction architecture

The extraction stage uses a two-step approach:

**Step 1 — Tesseract OCR**  
Converts each PDF page to a 300 DPI image using pdf2image (Poppler backend), then runs Tesseract OCR to produce raw text. This handles both digitally-generated and scanned PDFs.

**Step 2 — Claude AI extraction**  
The raw OCR text is sent to `claude-haiku-4-5-20251001` with a structured prompt asking it to extract specific fields and return valid JSON. Claude handles format variations across vendors (different date formats, currency positions, label names) without any vendor-specific code.

**Fallback behaviour**  
If `ANTHROPIC_API_KEY` is not set, or if the Claude API returns an error or invalid JSON, the pipeline automatically falls back to regex-based extraction. The fallback is less accurate on unusual formats but ensures the pipeline never crashes due to API unavailability.

```
PDF → Tesseract → raw text → Claude API → JSON fields
                                  ↓ (on failure)
                             regex fallback → fields
```

**Extraction cost**  
Claude Haiku at current pricing costs approximately $0.00025 per invoice (roughly 2,000 input tokens). At 100 invoices per day this is under $1/month.

---

## Monitoring

### Prefect UI

Every pipeline run appears in the Prefect UI at `https://invoice-pipeline-prefect.lameda-ai.xyz` under "Flow Runs". Each run shows:
- Run name (auto-generated, e.g. `spectacular-dragonfly`)
- State: Completed / Failed / Retrying
- Per-task timing and state
- Full structured logs with `pipeline_run_id` on every line

### Database queries

**Check recent approved invoices:**
```sql
SELECT invoice_id, vendor_name, total_amount, status, created_at
FROM invoices
ORDER BY created_at DESC
LIMIT 20;
```

**Check review queue:**
```sql
SELECT invoice_id, vendor_name, total_amount, status, created_at
FROM invoices
WHERE status = 'review'
ORDER BY created_at DESC;
```

**Check dead-letter queue:**
```sql
SELECT id, file_id, error_stage, error_detail, failed_at
FROM dead_letter_invoices
ORDER BY failed_at DESC
LIMIT 20;
```

**Pipeline volume today:**
```sql
SELECT status, COUNT(*) 
FROM invoices 
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;
```

Connect to the database:
```bash
docker exec -it invoice-db psql -U postgres -d invoices
```

---

## Common failure modes

### Claude API returns invalid JSON

**Symptom:** Log shows `claude returned invalid json, falling back to regex`. Pipeline continues with regex fallback.

**Cause:** Occasionally Claude returns a response wrapped in markdown fences despite instructions not to. The parser strips common fence patterns but edge cases may slip through.

**Fix:** The fallback handles this automatically. If regex fallback is also failing, check the raw OCR text quality — if Tesseract produced garbled output, Claude can't extract clean fields from it either.

---

### Claude API key missing or invalid

**Symptom:** Log shows `no anthropic api key configured, using regex fallback` or `gemini extraction failed, falling back to regex`.

**Cause:** `ANTHROPIC_API_KEY` environment variable is not set or has expired.

**Fix:**
1. Get a fresh API key from https://console.anthropic.com
2. Update the environment variable in `.env` (local) or Coolify environment variables (production)
3. Restart the app container

---

### OCR produces empty or garbled text

**Symptom:** `characters=0` or very low character count in extraction log. Claude receives poor input and returns incomplete fields.

**Cause:** PDF is scanned at low resolution, uses unusual fonts, or is image-only without embedded text.

**Fix:**
1. Check the raw PDF visually — open it and confirm the text is readable by a human
2. Try increasing DPI in `ocr_client.py` from 300 to 400 for low-quality scans
3. If the PDF is password-protected it will produce no output — check with `pdfinfo invoice.pdf`
4. Invoice will land in `dead_letter_invoices` with `error_stage = extraction`

---

### Poppler not found

**Symptom:**
```
PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?
```

**Fix (Windows):**
1. Download from https://github.com/oschwartz10612/poppler-windows/releases
2. Extract to `C:\Program Files\poppler`
3. Add `C:\Program Files\poppler\Library\bin` to system PATH
4. Open a new terminal and verify: `pdfinfo --version`

**Fix (Linux):**
```bash
sudo apt install poppler-utils -y
```

---

### Vendor not recognised

**Symptom:** Invoice routed to review queue. Slack alert shows failed rule `vendor_known`.

**Cause:** The vendor name extracted by Claude matches the blocklist (`unknown`, `test`, `sample`) or is an empty string — usually means Tesseract produced very poor output for that invoice.

**Fix:**
1. Check the raw OCR output quality for that file
2. If the vendor name is legitimate, update the blocklist logic in `src/validation/rules.py`

---

### Amount exceeds threshold — sent to review

**Symptom:** Invoice with status = `review`. Slack alert shows failed rule `amount_threshold`.

**Cause:** `total_amount` exceeds `AUTO_APPROVE_THRESHOLD` (default $5,000).

**This is expected behaviour, not a bug.** Large invoices require human sign-off.

**Fix:**
1. Human reviews the invoice in the review queue
2. If approved, update status manually:
```sql
UPDATE invoices SET status = 'approved' WHERE invoice_id = 'uuid-here';
```

---

### Database connection refused

**Symptom:**
```
psycopg.OperationalError: connection refused
```

**Fix:**
```bash
docker start invoice-db
docker ps   # confirm it shows "Up"
```

If the container doesn't exist:
```bash
docker run --name invoice-db \
  -e POSTGRES_PASSWORD=dev \
  -e POSTGRES_DB=invoices \
  -p 5432:5432 \
  -d postgres:16

docker exec -i invoice-db psql -U postgres -d invoices < migrations/001_initial_schema.sql
```

---

### Slack notifications not sending

**Symptom:** Log shows `slack webhook not configured, skipping notification` or `slack notification failed`.

**Fix:**
1. Go to https://api.slack.com/apps, find your app, check Incoming Webhooks
2. Copy the current webhook URL and update `SLACK_WEBHOOK_URL` in `.env` or Coolify
3. A failed Slack notification never crashes the pipeline — the invoice is already in Postgres

---

## Re-processing a dead-lettered invoice

1. Find the dead-letter record:
```sql
SELECT id, file_id, error_stage, error_detail FROM dead_letter_invoices ORDER BY failed_at DESC LIMIT 5;
```

2. Find the original staged file using `file_id`:
```bash
ls staging/<file_id>/
```

3. Re-run the pipeline:
```bash
python -m src.pipeline staging/<file_id>/<filename>.pdf
```

4. Dead-letter rows are never deleted — they are an immutable audit log.

---

## Dependency versions (as built)

| Package | Version |
|---------|---------|
| Python | 3.12 |
| Prefect | 3.7.2 |
| Pydantic | 2.13.4 |
| pydantic-settings | 2.14.1 |
| structlog | 25.5.0 |
| tenacity | 9.1.4 |
| anthropic | 0.28+ |
| pytesseract | 0.3.10+ |
| pdf2image | 1.17+ |
| psycopg | 3.1+ |
| httpx | 0.27+ |
| Tesseract | 5.5.0 |
| PostgreSQL | 16 |
| Poppler | 24.x |