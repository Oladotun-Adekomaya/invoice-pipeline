import threading
import structlog.contextvars
from pathlib import Path
from uuid import uuid4, UUID

from prefect import flow, task

from src.observability.logger import setup_logging, get_logger
from src.intake.file_handler import stage, FileRecord
from src.extraction.ocr_client import extract_text
from src.extraction.parser import parse_text, RawInvoiceFields
from src.normalization.transformer import transform, NormalizationError
from src.normalization.schema import Invoice
from src.validation.engine import validate, ValidationReport
from src.routing.approver import route, RoutingOutcome
from src.routing.notifier import notify

setup_logging()
logger = get_logger(__name__)

# Thread-local SSE queue injected by the API layer during web-triggered runs.
_thread_queue: threading.local = threading.local()


def _emit(level: str, event: str) -> None:
    q = getattr(_thread_queue, "queue", None)
    if q is not None:
        q.put({"level": level, "event": event})


@task(name="stage-file", retries=2, retry_delay_seconds=5)
def stage_file_task(path: str) -> FileRecord:
    return stage(Path(path))


@task(name="extract", retries=3, retry_delay_seconds=10)
def extract_task(record: FileRecord) -> RawInvoiceFields:
    _emit("info", "🔍 Converting PDF to images and running OCR")
    text = extract_text(record.staged_path)
    _emit("info", f"✓ OCR complete — {len(text)} characters extracted")
    _emit("info", "🤖 Sending to Claude AI for field extraction")
    raw = parse_text(text)
    _emit("info", f"✓ Extracted {len(raw)} fields: {', '.join(raw.keys())}")
    return raw


@task(name="normalize")
def normalize_task(raw: RawInvoiceFields, file_id: UUID) -> Invoice:
    _emit("info", "🔧 Normalizing fields")
    invoice = transform(raw, file_id)
    _emit("info", f"✓ Normalized — vendor: {invoice.vendor_name}, total: ${invoice.total_amount}")
    return invoice


@task(name="validate")
def validate_task(invoice: Invoice) -> ValidationReport:
    _emit("info", "✅ Running validation rules")
    report = validate(invoice)
    for r in report.results:
        icon = "✓" if r.passed else "✗"
        _emit("info" if r.passed else "warning", f"  {icon} {r.rule_name}: {r.message or 'passed'}")
    return report


@task(name="route")
def route_task(invoice: Invoice, report: ValidationReport) -> RoutingOutcome:
    _emit("info", "📬 Routing invoice")
    outcome = route(invoice, report)
    notify(invoice, report, outcome)
    _emit("info", f"✓ Done — {outcome.action.value}")
    return outcome


@flow(name="invoice-pipeline", log_prints=True)
def run_pipeline(file_path: str) -> dict:
    run_id = str(uuid4())

    structlog.contextvars.bind_contextvars(pipeline_run_id=run_id)

    logger.info("pipeline started", file_path=file_path, run_id=run_id)

    record = stage_file_task(file_path)
    raw = extract_task(record)
    invoice = normalize_task(raw, record.file_id)
    report = validate_task(invoice)
    outcome = route_task(invoice, report)

    logger.info(
        "pipeline complete",
        run_id=run_id,
        action=outcome.action.value,
        invoice_id=str(outcome.invoice_id),
    )

    return {
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
            "service_type": raw.get("service_type"),
            "circuit_id": raw.get("circuit_id"),
            "site_id": raw.get("site_id"),
            "po_number": raw.get("po_number"),
            "cost_center": raw.get("cost_center"),
        },
        "failed_rules": [
            {"rule": r.rule_name, "message": r.message}
            for r in report.failed_rules()
        ],
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.pipeline <path-to-pdf>")
        sys.exit(1)
    result = run_pipeline(sys.argv[1])
    print(f"result: {result['status']}")