import asyncio
import json
import queue
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.db import run_migrations
from src.normalization.transformer import NormalizationError
from src.observability.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app_instance):
    run_migrations()
    yield


app = FastAPI(title="Invoice Pipeline", lifespan=lifespan)

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Active log queues keyed by run_id
_log_queues: dict[str, queue.Queue] = {}


def _run_pipeline(file_path: Path, run_id: str) -> None:
    from src.pipeline import run_pipeline, _thread_queue as _pipeline_thread_queue

    q = _log_queues[run_id]
    _pipeline_thread_queue.queue = q

    def emit(level: str, event: str) -> None:
        q.put({"level": level, "event": event})

    try:
        emit("info", "📄 File received, starting pipeline")
        result = run_pipeline(str(file_path))
        q.put(result)
    except NormalizationError as e:
        emit("error", f"✗ Normalization failed: {e}")
        q.put({"status": "dead_lettered", "reason": str(e), "fields": {}, "failed_rules": []})
    except Exception as e:
        emit("error", f"✗ Pipeline error: {e}")
        q.put({"status": "error", "reason": str(e), "fields": {}, "failed_rules": []})
    finally:
        _pipeline_thread_queue.queue = None
        file_path.unlink(missing_ok=True)
        q.put(None)  # sentinel — close the stream

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/upload")
async def upload_invoice(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    run_id = str(uuid.uuid4())
    file_id = uuid.uuid4()
    dest = UPLOAD_DIR / f"{file_id}.pdf"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info("invoice uploaded via web", filename=file.filename, run_id=run_id)

    _log_queues[run_id] = queue.Queue()

    thread = threading.Thread(target=_run_pipeline, args=(dest, run_id), daemon=True)
    thread.start()

    return JSONResponse(content={"run_id": run_id})

@app.get("/stream/{run_id}")
async def stream_logs(run_id: str, request: Request):
    if run_id not in _log_queues:
        raise HTTPException(status_code=404, detail="run not found")

    async def event_generator():
        q = _log_queues[run_id]
        result = None
        try:
            while True:
                try:
                    item = q.get(timeout=0.1)
                    if item is None:
                        if result:
                            yield {"data": json.dumps({"type": "result", **result})}
                        yield {"data": json.dumps({"type": "done"})}
                        break
                    elif "status" in item:
                        result = item
                    else:
                        yield {"data": json.dumps({"type": "log", **item})}
                except queue.Empty:
                    await asyncio.sleep(0.05)
        finally:
            _log_queues.pop(run_id, None)

    response = EventSourceResponse(event_generator())
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
@app.get("/result/{run_id}")
async def get_result(run_id: str):
    """Polling fallback — returns result once pipeline is complete."""
    return JSONResponse(content={"status": "processing"})


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
          padding: 40px; width: 100%; max-width: 560px; margin-bottom: 16px; }
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

  /* Log stream */
  .log-card { background: #0f0f0f; border-radius: 12px; padding: 20px;
              width: 100%; max-width: 560px; display: none; margin-bottom: 16px; }
  .log-title { font-size: 11px; color: #666; text-transform: uppercase;
               letter-spacing: .06em; margin-bottom: 12px; }
  .log-lines { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px;
               line-height: 1.7; max-height: 240px; overflow-y: auto; }
  .log-line { padding: 1px 0; }
  .log-line.info { color: #a8d8a8; }
  .log-line.error { color: #f08080; }
  .log-line.warning { color: #ffd580; }
  .cursor { display: inline-block; width: 8px; height: 13px;
            background: #666; animation: blink 1s infinite; vertical-align: middle; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

  /* Result */
  .result { display: none; width: 100%; max-width: 560px; }
  .result-header { display: flex; align-items: center; gap: 10px;
                   padding: 14px 16px; border-radius: 8px; margin-bottom: 16px; }
  .result-header.auto_approved { background: #eaf3de; }
  .result-header.sent_to_review { background: #faeeda; }
  .result-header.dead_lettered, .result-header.error { background: #fcebeb; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .auto_approved .status-dot { background: #3b6d11; }
  .sent_to_review .status-dot { background: #854f0b; }
  .dead_lettered .status-dot, .error .status-dot { background: #a32d2d; }
  .status-text { font-size: 14px; font-weight: 500; }
  .auto_approved .status-text { color: #27500a; }
  .sent_to_review .status-text { color: #633806; }
  .dead_lettered .status-text, .error .status-text { color: #791f1f; }
  .fields-grid { background: #fff; border: 1px solid #e5e5e3; border-radius: 12px;
                 padding: 40px; }
  .fields-inner { display: grid; grid-template-columns: 1fr 1fr; gap: 1px;
                  background: #e5e5e3; border: 1px solid #e5e5e3;
                  border-radius: 8px; overflow: hidden; }
  .field { background: #fff; padding: 12px 14px; }
  .field-label { font-size: 11px; color: #9b9b98; text-transform: uppercase;
                 letter-spacing: .04em; margin-bottom: 3px; }
  .field-value { font-size: 14px; color: #1a1a1a; font-weight: 500; word-break: break-all; }
  .field-value.empty { color: #c0c0bc; font-weight: 400; font-style: italic; }
  .failed-rules { margin-top: 14px; padding: 12px 14px; background: #fcebeb;
                  border-radius: 8px; }
  .failed-rules-title { font-size: 12px; font-weight: 500; color: #791f1f; margin-bottom: 8px; }
  .rule-item { font-size: 12px; color: #a32d2d; padding: 2px 0; }
  .reason-box { margin-top: 14px; padding: 12px 14px; background: #f3f3f1;
                border-radius: 8px; font-size: 13px; color: #6b6b6b; }
  .reset-btn { width: 100%; margin-top: 12px; padding: 10px; font-size: 14px;
               background: transparent; color: #6b6b6b; border: 1px solid #e5e5e3;
               border-radius: 8px; cursor: pointer; }
  .reset-btn:hover { background: #f3f3f1; }
</style>
</head>
<body>

<div class="card" id="uploadCard">
  <h1>Invoice Pipeline</h1>
  <p class="sub">Upload a telecom or IT invoice PDF to process it through the pipeline.</p>
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <div class="drop-icon">📄</div>
    <div class="drop-label">Drop a PDF here or click to browse</div>
    <div class="drop-hint">PDF files only</div>
    <input type="file" id="fileInput" accept=".pdf">
  </div>
  <div class="file-selected" id="fileSelected"></div>
  <button class="btn" id="submitBtn" disabled onclick="submitFile()">Process Invoice</button>
</div>

<div class="log-card" id="logCard">
  <div class="log-title">Pipeline log</div>
  <div class="log-lines" id="logLines"><span class="cursor"></span></div>
</div>

<div class="result" id="result"></div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileSelected = document.getElementById('fileSelected');
const submitBtn = document.getElementById('submitBtn');
let selectedFile = null;

fileInput.addEventListener('change', () => { if (fileInput.files[0]) selectFile(fileInput.files[0]); });
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

function appendLog(event, level) {
  const logLines = document.getElementById('logLines');
  const cursor = logLines.querySelector('.cursor');
  if (cursor) cursor.remove();
  const div = document.createElement('div');
  div.className = 'log-line ' + (level || 'info');
  div.textContent = event;
  logLines.appendChild(div);
  const newCursor = document.createElement('span');
  newCursor.className = 'cursor';
  logLines.appendChild(newCursor);
  logLines.scrollTop = logLines.scrollHeight;
}

async function submitFile() {
  if (!selectedFile) return;
  submitBtn.disabled = true;

  // Show log card, hide result
  document.getElementById('logCard').style.display = 'block';
  document.getElementById('result').style.display = 'none';
  document.getElementById('logLines').innerHTML = '<span class="cursor"></span>';

  const form = new FormData();
  form.append('file', selectedFile);

  try {
    const res = await fetch('/upload', { method: 'POST', body: form });
    const { run_id } = await res.json();

    // Open SSE stream
    const es = new EventSource('/stream/' + run_id);
    let finalResult = null;

    es.onmessage = (e) => {
      const data = JSON.parse(e.data);

      if (data.type === 'log') {
        appendLog(data.event, data.level);
      } else if (data.type === 'result') {
        finalResult = data;
      } else if (data.type === 'done') {
        es.close();
        // Remove cursor
        const cursor = document.getElementById('logLines').querySelector('.cursor');
        if (cursor) cursor.remove();
        if (finalResult) renderResult(finalResult);
        submitBtn.disabled = false;
      }
    };

    es.onerror = () => {
      es.close();
      appendLog('Connection closed', 'warning');
      submitBtn.disabled = false;
    };

  } catch (err) {
    appendLog('Error: ' + err.message, 'error');
    submitBtn.disabled = false;
  }
}

function val(v) { return v ? v : '<span class="empty">—</span>'; }

function renderResult(data) {
  const statusLabels = {
    auto_approved: '✓ Auto-approved',
    sent_to_review: '⚠ Sent to review',
    dead_lettered: '✕ Dead-lettered',
    error: '✕ Pipeline error',
  };
  const f = data.fields || {};
  const fieldsHtml = [
    ['Vendor', f.vendor_name], ['Invoice #', f.invoice_number],
    ['Invoice date', f.invoice_date], ['Due date', f.due_date],
    ['Total amount', f.total_amount ? '$' + f.total_amount : null],
    ['Tax amount', f.tax_amount ? '$' + f.tax_amount : null],
    ['Currency', f.currency], ['Account #', f.account_number],
    ['Period start', f.service_period_start], ['Period end', f.service_period_end],
    ['Service type', f.service_type],
    ['Circuit ID', f.circuit_id],
    ['Site ID', f.site_id],
    ['PO number', f.po_number],
    ['Cost center', f.cost_center],
  ].map(([label, value]) => `
    <div class="field">
      <div class="field-label">${label}</div>
      <div class="field-value">${val(value)}</div>
    </div>`).join('');

  const failedHtml = data.failed_rules && data.failed_rules.length ? `
    <div class="failed-rules">
      <div class="failed-rules-title">Failed rules</div>
      ${data.failed_rules.map(r => `<div class="rule-item">• ${r.rule}: ${r.message}</div>`).join('')}
    </div>` : '';

  document.getElementById('result').innerHTML = `
    <div class="fields-grid">
      <div class="result-header ${data.status}">
        <div class="status-dot"></div>
        <div class="status-text">${statusLabels[data.status] || data.status}</div>
      </div>
      <div class="fields-inner">${fieldsHtml}</div>
      ${failedHtml}
      <div class="reason-box">${data.reason || ''}</div>
      <button class="reset-btn" onclick="location.reload()">Process another invoice</button>
    </div>
  `;
  document.getElementById('result').style.display = 'block';
}
</script>
</body>
</html>
"""