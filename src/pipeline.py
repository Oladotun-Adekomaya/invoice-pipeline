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


@task(name="stage-file", retries=2, retry_delay_seconds=5)
def stage_file_task(path: str) -> FileRecord:
    return stage(Path(path))


@task(name="extract", retries=3, retry_delay_seconds=10)
def extract_task(record: FileRecord) -> RawInvoiceFields:
    text = extract_text(record.staged_path)
    return parse_text(text)


@task(name="normalize")
def normalize_task(raw: RawInvoiceFields, file_id: UUID) -> Invoice:
    return transform(raw, file_id)


@task(name="validate")
def validate_task(invoice: Invoice) -> ValidationReport:
    return validate(invoice)


@task(name="route")
def route_task(invoice: Invoice, report: ValidationReport) -> RoutingOutcome:
    outcome = route(invoice, report)
    notify(invoice, report, outcome)
    return outcome


@flow(name="invoice-pipeline", log_prints=True)
def run_pipeline(file_path: str) -> str:
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

    return outcome.action.value


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.pipeline <path-to-pdf>")
        sys.exit(1)
    result = run_pipeline(sys.argv[1])
    print(f"result: {result}")