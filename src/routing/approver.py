import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

import psycopg

from src.config import settings
from src.normalization.schema import Invoice, InvoiceStatus
from src.observability.logger import get_logger
from src.validation.engine import ValidationReport

logger = get_logger(__name__)


class RoutingAction(str, Enum):
    AUTO_APPROVED = "auto_approved"
    SENT_TO_REVIEW = "sent_to_review"
    DEAD_LETTERED = "dead_lettered"


@dataclass
class RoutingOutcome:
    invoice_id: UUID
    action: RoutingAction
    reason: str
    routed_at: datetime


def _get_connection():
    return psycopg.connect(settings.database_url)


def _write_invoice(conn, invoice: Invoice) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices (
                invoice_id, file_id, vendor_name, vendor_id,
                account_number, total_amount, currency, tax_amount,
                invoice_date, due_date, service_period_start,
                service_period_end, status, extracted_at,
                normalized_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                str(invoice.invoice_id),
                str(invoice.file_id),
                invoice.vendor_name,
                invoice.vendor_id,
                invoice.account_number,
                invoice.total_amount,
                invoice.currency,
                invoice.tax_amount,
                invoice.invoice_date,
                invoice.due_date,
                invoice.service_period_start,
                invoice.service_period_end,
                invoice.status.value,
                invoice.extracted_at,
                invoice.normalized_at,
            ),
        )
    conn.commit()


def _write_dead_letter(
    conn,
    invoice: Invoice,
    report: ValidationReport,
    file_id: UUID,
) -> None:
    failed = [
        {"rule": r.rule_name, "message": r.message}
        for r in report.failed_rules()
    ]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dead_letter_invoices (
                id, file_id, original_filename,
                error_stage, error_detail, received_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid4()),
                str(file_id),
                f"{invoice.vendor_name}_invoice",
                "validation",
                json.dumps({"failed_rules": failed}),
                datetime.now(timezone.utc),
            ),
        )
    conn.commit()


def route(invoice: Invoice, report: ValidationReport) -> RoutingOutcome:
    now = datetime.now(timezone.utc)

    logger.info(
        "routing invoice",
        invoice_id=str(invoice.invoice_id),
        passed=report.passed,
    )

    conn = _get_connection()

    try:
        if report.passed:
            invoice.status = InvoiceStatus.APPROVED
            _write_invoice(conn, invoice)
            action = RoutingAction.AUTO_APPROVED
            reason = "all validation rules passed"

        elif all(
            r.rule_name == "amount_threshold"
            for r in report.failed_rules()
        ):
            invoice.status = InvoiceStatus.REVIEW
            _write_invoice(conn, invoice)
            action = RoutingAction.SENT_TO_REVIEW
            reason = "amount exceeds auto-approve threshold"

        else:
            invoice.status = InvoiceStatus.REVIEW
            _write_dead_letter(conn, invoice, report, invoice.file_id)
            action = RoutingAction.DEAD_LETTERED
            reason = f"failed rules: {[r.rule_name for r in report.failed_rules()]}"

    finally:
        conn.close()

    outcome = RoutingOutcome(
        invoice_id=invoice.invoice_id,
        action=action,
        reason=reason,
        routed_at=now,
    )

    logger.info(
        "routing complete",
        invoice_id=str(invoice.invoice_id),
        action=action.value,
        reason=reason,
    )

    return outcome