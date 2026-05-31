import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.observability.logger import setup_logging, get_logger
from src.extraction.ocr_client import extract_text
from src.extraction.parser import parse_text
from src.normalization.transformer import transform, NormalizationError
from src.validation.engine import validate
from src.routing.approver import route
from src.routing.notifier import notify

from contextlib import asynccontextmanager
from src.db import run_migrations


@asynccontextmanager
async def lifespan(app_instance):
    run_migrations()
    yield


app = FastAPI(title="Invoice Pipeline", lifespan=lifespan)

setup_logging()
logger = get_logger(__name__)

app = FastAPI(title="Invoice Pipeline")

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/upload")
async def upload_invoice(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    file_id = uuid.uuid4()
    dest = UPLOAD_DIR / f"{file_id}.pdf"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info("invoice uploaded via web", filename=file.filename, file_id=str(file_id))

    try:
        text = extract_text(dest)
        raw = parse_text(text)
        invoice = transform(raw, file_id=file_id)
        report = validate(invoice)
        outcome = route(invoice, report)
        notify(invoice, report, outcome)
    except NormalizationError as e:
        logger.error("normalization failed", error=str(e))
        return JSONResponse(status_code=422, content={
            "status": "dead_lettered",
            "reason": str(e),
            "fields": {},
        })
    except Exception as e:
        logger.error("pipeline failed", error=str(e))
        return JSONResponse(status_code=500, content={
            "status": "error",
            "reason": str(e),
            "fields": {},
        })
    finally:
        dest.unlink(missing_ok=True)

    failed_rules = [
        {"rule": r.rule_name, "message": r.message}
        for r in report.failed_rules()
    ]

    return JSONResponse(content={
        "status": outcome.action.value,
        "reason": outcome.reason,
        "fields": {
            "invoice_id": str(invoice.invoice_id),
            "vendor_name": invoice.vendor_name,
            "invoice_number": invoice.invoice_number,
            "invoice_date": str(invoice.invoice_date),
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "total_amount": str(invoice.total_amount),
            "currency": invoice.currency,
            "tax_amount": str(invoice.tax_amount) if invoice.tax_amount else None,
            "account_number": invoice.account_number,
            "service_period_start": str(invoice.service_period_start) if invoice.service_period_start else None,
            "service_period_end": str(invoice.service_period_end) if invoice.service_period_end else None,
        },
        "failed_rules": failed_rules,
    })


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice Pipeline</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f8f8f7; color: #1a1a1a; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center;
         padding: 48px 16px; }
  .card { background: #fff; border: 1px solid #e5e5e3; border-radius: 12px;
          padding: 40px; width: 100%; max-width: 560px; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
  .sub { font-size: 14px; color: #6b6b6b; margin-bottom: 32px; }
  .drop-zone { border: 2px dashed #d0d0ce; border-radius: 8px; padding: 40px 24px;
               text-align: center; cursor: pointer; transition: all .15s;
               background: #fafaf9; }
  .drop-zone:hover, .drop-zone.drag { border-color: #1a1a1a; background: #f3f3f1; }
  .drop-zone input { display: none; }
  .drop-icon { font-size: 32px; margin-bottom: 12px; }
  .drop-label { font-size: 15px; color: #3d3d3a; }
  .drop-hint { font-size: 12px; color: #9b9b98; margin-top: 4px; }
  .file-selected { font-size: 13px; color: #1a1a1a; margin-top: 12px;
                   padding: 8px 12px; background: #f3f3f1;
                   border-radius: 6px; display: none; }
  .btn { width: 100%; margin-top: 16px; padding: 12px; font-size: 15px;
         font-weight: 500; background: #1a1a1a; color: #fff; border: none;
         border-radius: 8px; cursor: pointer; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .spinner { display: none; text-align: center; padding: 24px 0; color: #6b6b6b;
             font-size: 14px; }
  .result { margin-top: 24px; display: none; }
  .result-header { display: flex; align-items: center; gap: 10px;
                   padding: 14px 16px; border-radius: 8px; margin-bottom: 16px; }
  .result-header.approved { background: #eaf3de; }
  .result-header.review { background: #faeeda; }
  .result-header.dead_lettered, .result-header.error { background: #fcebeb; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .approved .status-dot { background: #3b6d11; }
  .review .status-dot { background: #854f0b; }
  .dead_lettered .status-dot, .error .status-dot { background: #a32d2d; }
  .status-text { font-size: 14px; font-weight: 500; }
  .approved .status-text { color: #27500a; }
  .review .status-text { color: #633806; }
  .dead_lettered .status-text, .error .status-text { color: #791f1f; }
  .fields-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1px;
                 background: #e5e5e3; border: 1px solid #e5e5e3;
                 border-radius: 8px; overflow: hidden; }
  .field { background: #fff; padding: 12px 14px; }
  .field-label { font-size: 11px; color: #9b9b98; text-transform: uppercase;
                 letter-spacing: .04em; margin-bottom: 3px; }
  .field-value { font-size: 14px; color: #1a1a1a; font-weight: 500;
                 word-break: break-all; }
  .field-value.empty { color: #c0c0bc; font-weight: 400; font-style: italic; }
  .failed-rules { margin-top: 14px; padding: 12px 14px; background: #fcebeb;
                  border-radius: 8px; }
  .failed-rules-title { font-size: 12px; font-weight: 500; color: #791f1f;
                        margin-bottom: 8px; }
  .rule-item { font-size: 12px; color: #a32d2d; padding: 2px 0; }
  .reason-box { margin-top: 14px; padding: 12px 14px; background: #f3f3f1;
                border-radius: 8px; font-size: 13px; color: #6b6b6b; }
</style>
</head>
<body>
<div class="card">
  <h1>Invoice Pipeline</h1>
  <p class="sub">Upload a telecom or IT invoice PDF to process it through the pipeline.</p>

  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <div class="drop-icon">📄</div>
    <div class="drop-label">Drop a PDF here or click to browse</div>
    <div class="drop-hint">PDF files only</div>
    <input type="file" id="fileInput" accept=".pdf">
  </div>
  <div class="file-selected" id="fileSelected"></div>

  <button class="btn" id="submitBtn" disabled onclick="submitFile()">
    Process Invoice
  </button>

  <div class="spinner" id="spinner">⏳ Processing — this takes about 5 seconds…</div>

  <div class="result" id="result"></div>
</div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileSelected = document.getElementById('fileSelected');
const submitBtn = document.getElementById('submitBtn');
let selectedFile = null;

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) selectFile(fileInput.files[0]);
});

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('drag');
  if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
});

function selectFile(file) {
  selectedFile = file;
  fileSelected.textContent = '📎 ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
  fileSelected.style.display = 'block';
  submitBtn.disabled = false;
}

async function submitFile() {
  if (!selectedFile) return;
  submitBtn.disabled = true;
  document.getElementById('spinner').style.display = 'block';
  document.getElementById('result').style.display = 'none';

  const form = new FormData();
  form.append('file', selectedFile);

  try {
    const res = await fetch('/upload', { method: 'POST', body: form });
    const data = await res.json();
    renderResult(data);
  } catch (err) {
    renderResult({ status: 'error', reason: err.message, fields: {}, failed_rules: [] });
  }

  document.getElementById('spinner').style.display = 'none';
  submitBtn.disabled = false;
}

function val(v) {
  return v ? v : '<span class="empty">—</span>';
}

function renderResult(data) {
  const statusLabels = {
    auto_approved: '✓ Auto-approved',
    sent_to_review: '⚠ Sent to review',
    dead_lettered: '✕ Dead-lettered',
    error: '✕ Pipeline error',
  };

  const f = data.fields || {};
  const fieldsHtml = [
    ['Vendor', f.vendor_name],
    ['Invoice #', f.invoice_number],
    ['Invoice date', f.invoice_date],
    ['Due date', f.due_date],
    ['Total amount', f.total_amount ? '$' + f.total_amount : null],
    ['Tax amount', f.tax_amount ? '$' + f.tax_amount : null],
    ['Currency', f.currency],
    ['Account #', f.account_number],
    ['Period start', f.service_period_start],
    ['Period end', f.service_period_end],
  ].map(([label, value]) => `
    <div class="field">
      <div class="field-label">${label}</div>
      <div class="field-value">${val(value)}</div>
    </div>`).join('');

  const failedHtml = data.failed_rules && data.failed_rules.length > 0 ? `
    <div class="failed-rules">
      <div class="failed-rules-title">Failed rules</div>
      ${data.failed_rules.map(r => `<div class="rule-item">• ${r.rule}: ${r.message}</div>`).join('')}
    </div>` : '';

  const statusClass = data.status.replace('_', '_');

  document.getElementById('result').innerHTML = `
    <div class="result-header ${data.status}">
      <div class="status-dot"></div>
      <div class="status-text">${statusLabels[data.status] || data.status}</div>
    </div>
    <div class="fields-grid">${fieldsHtml}</div>
    ${failedHtml}
    <div class="reason-box">${data.reason || ''}</div>
  `;
  document.getElementById('result').style.display = 'block';
}
</script>
</body>
</html>
"""