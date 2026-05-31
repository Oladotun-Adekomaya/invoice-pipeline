# Invoice pipeline — operations runbook

**Last updated:** May 2026  
**Author:** Invoice Pipeline Project  
**Purpose:** Operational reference for running, monitoring, and recovering the invoice processing pipeline.

---

## Overview

This pipeline ingests telecom and IT invoices as PDFs, extracts structured data using Tesseract OCR, normalises and validates the data, then routes each invoice to automatic approval or a human review queue. Every decision is persisted to PostgreSQL with a full audit trail.

```
PDF (folder drop)
      │
  [Intake]       Stage file, compute SHA-256, emit FileRecord
      │
[Extraction]     Convert PDF → images (pdf2image/Poppler), run Tesseract OCR, regex parse
      │
[Normalization]  Raw strings → typed Python: Decimal amounts, date objects, UUID invoice ID
      │
[Validation]     5 business rules: required fields, positive amount, threshold, vendor, dates
      │
   ┌──┴──┐
[Approve]    [Review]    [Dead-letter]
Postgres     Postgres     Postgres
             + Slack      + Slack
```

**Orchestrator:** Prefect  
**Database:** PostgreSQL 16 (Docker)  
**OCR engine:** Tesseract 5.5 + pdf2image (Poppler)  
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

Prefect UI is available at: `http://127.0.0.1:4200`

### Run tests

```bash
pytest tests/unit -v               # unit tests only (no dependencies needed)
pytest tests/ --cov=src            # all tests with coverage
```

---

## Monitoring

### Prefect UI

Every pipeline run appears in the Prefect UI at `http://127.0.0.1:4200` under "Flow Runs". Each run shows:
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

### Log structure

Every log event is structured JSON in production and coloured text in development. All events carry:

| Field | Description |
|-------|-------------|
| `timestamp` | ISO 8601 UTC |
| `level` | info / warning / error |
| `pipeline_run_id` | UUID bound at flow start — links all events for one run |
| `invoice_id` | UUID assigned at normalisation |
| `stage` | Which module emitted the log |
| `event` | Short description of what happened |

To find all logs for a specific run:
```bash
# If logging to a file, grep by pipeline_run_id
grep "pipeline_run_id=48127163" pipeline.log
```

---

## Common failure modes

### OCR produces empty or garbled text

**Symptom:** `characters=0` or very low character count in extraction log. Fields missing from parsed output.

**Cause:** PDF is scanned at low resolution, uses unusual fonts, or is image-only without embedded text.

**Fix:**
1. Check the raw PDF visually — open it and confirm the text is readable by a human
2. Try increasing DPI in `ocr_client.py` from 300 to 400 for low-quality scans
3. If the PDF is password-protected, it will produce no output — check with `pdfinfo invoice.pdf`
4. Invoice will land in `dead_letter_invoices` with `error_stage = extraction`

---

### Poppler not found

**Symptom:**
```
PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?
```

**Cause:** Poppler binaries are not installed or not in the system PATH.

**Fix (Windows):**
1. Download from https://github.com/oschwartz10612/poppler-windows/releases
2. Extract to `C:\Program Files\poppler`
3. Add `C:\Program Files\poppler\Library\bin` to system PATH
4. Open a new terminal (PATH changes don't apply to open terminals)
5. Verify: `pdfinfo --version`

**Fix (Linux):**
```bash
sudo apt install poppler-utils -y
```

---

### Vendor not recognised

**Symptom:** Invoice routed to review queue. Slack alert shows failed rule `vendor_known`.

**Cause:** OCR read the vendor name correctly but it matches the blocklist (`unknown`, `test`, `sample`) or is an empty string.

**Fix:**
1. Check the raw OCR output — was the vendor name actually extracted?
2. If yes and it's a legitimate vendor, the blocklist logic in `src/validation/rules.py` `check_vendor_known` may need updating
3. In production this would be a database lookup — see design doc for v2 proposal

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
3. If the threshold needs adjusting, update `AUTO_APPROVE_THRESHOLD` in `.env` and restart

---

### Service period dates parse incorrectly

**Symptom:** `NormalizationError: service_period_end is before service_period_start`

**Cause:** The service period string uses an unusual separator or format that the parser doesn't handle.

**Fix:**
1. Check what format the invoice uses — common alternatives: `01/01/2024 to 01/31/2024`, `2024-01-01 through 2024-01-31`
2. Add the pattern to `parse_service_period` in `src/normalization/transformer.py`
3. The split regex is: `r"\s+[-–—]\s+|\s+to\s+"` — extend this for new separators

**Note:** If the service period can't be parsed it returns `None, None` silently — it's optional. Only hard-fail if the format causes the start > end check in `Invoice.__post_init__` to trip.

---

### Database connection refused

**Symptom:**
```
psycopg.OperationalError: connection refused
```

**Cause:** Postgres Docker container is not running.

**Fix:**
```bash
docker start invoice-db
docker ps   # confirm it shows "Up"
```

If the container doesn't exist at all (first run or was deleted):
```bash
docker run --name invoice-db \
  -e POSTGRES_PASSWORD=dev \
  -e POSTGRES_DB=invoices \
  -p 5432:5432 \
  -d postgres:16

# Re-run migrations
docker exec -i invoice-db psql -U postgres -d invoices < migrations/001_initial_schema.sql
```

---

### Slack notifications not sending

**Symptom:** Pipeline completes but no Slack message received. Log shows:
```
[warning] slack webhook not configured, skipping notification
```
or
```
[error] slack notification failed
```

**Cause:** `SLACK_WEBHOOK_URL` in `.env` is still set to `placeholder`, or the webhook URL has been revoked.

**Fix:**
1. Go to https://api.slack.com/apps, find your app, check Incoming Webhooks
2. Copy the current webhook URL and update `.env`
3. Test the webhook directly:
```bash
python -c "
import httpx
import os
from dotenv import load_dotenv
load_dotenv()
r = httpx.post(os.getenv('SLACK_WEBHOOK_URL'), json={'text': 'test'})
print(r.status_code)
"
```

**Important:** A failed Slack notification never crashes the pipeline. The invoice is already safely in Postgres at this point. Notifications are best-effort.

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

3. Re-run the pipeline against the staged file:
```bash
python -m src.pipeline staging/<file_id>/<filename>.pdf
```

4. If the re-run succeeds, optionally mark the dead-letter record as resolved:
```sql
-- dead_letter_invoices has no status column by design — it is an immutable audit log.
-- Do not delete rows. Add a note in your incident log instead.
```

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
| pytesseract | 0.3.10+ |
| pdf2image | 1.17+ |
| psycopg | 3.1+ |
| httpx | 0.27+ |
| Tesseract | 5.5.0 |
| PostgreSQL | 16 |
| Poppler | 24.x |