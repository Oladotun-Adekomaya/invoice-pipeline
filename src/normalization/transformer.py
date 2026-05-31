import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from dateutil import parser as dateutil_parser

from src.extraction.parser import RawInvoiceFields
from src.normalization.schema import Invoice, InvoiceStatus
from src.observability.logger import get_logger

logger = get_logger(__name__)


class NormalizationError(Exception):
    def __init__(self, field: str, raw_value: str | None, reason: str):
        self.field = field
        self.raw_value = raw_value
        self.reason = reason
        super().__init__(f"failed to normalize '{field}': {reason} (raw='{raw_value}')")


def parse_amount(raw: str | None, field_name: str) -> Decimal:
    if not raw:
        raise NormalizationError(field_name, raw, "missing required amount field")
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        raise NormalizationError(field_name, raw, "no numeric characters found")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise NormalizationError(field_name, raw, "could not convert to Decimal")


def parse_date_required(raw: str | None, field_name: str) -> date:
    if not raw:
        raise NormalizationError(field_name, raw, "missing required date field")
    try:
        return dateutil_parser.parse(raw, fuzzy=True).date()
    except Exception:
        raise NormalizationError(field_name, raw, "could not parse as a date")


def parse_date_optional(raw: str | None, field_name: str) -> date | None:
    if not raw:
        return None
    try:
        return dateutil_parser.parse(raw, fuzzy=True).date()
    except Exception:
        raise NormalizationError(field_name, raw, "could not parse as a date")

def parse_service_period(raw: str | None):
    if not raw:
        return None, None

    parts = re.split(r"\s+[-–—]\s+|\s+to\s+", raw, maxsplit=1)

    if len(parts) != 2:
        logger.warning("could not split service period", raw=raw, parts=parts)
        return None, None

    try:
        end = dateutil_parser.parse(parts[1].strip(), fuzzy=True).date()

        # If the start part has no year, append the end date's year
        # so "Jan 1" becomes "Jan 1 2024" instead of defaulting to today's year
        start_raw = parts[0].strip()
        if not re.search(r"\d{4}", start_raw):
            start_raw = f"{start_raw} {end.year}"

        start = dateutil_parser.parse(start_raw, fuzzy=True).date()
        return start, end
    except Exception as e:
        logger.warning("could not parse service period dates", raw=raw, error=str(e))
        return None, None

def transform(raw: RawInvoiceFields, file_id: UUID) -> Invoice:
    from uuid import uuid4

    now = datetime.now(timezone.utc)

    logger.info("normalizing invoice", file_id=str(file_id))

    vendor_name = raw.get("vendor_name")
    if not vendor_name:
        raise NormalizationError("vendor_name", None, "missing vendor name")

    total_amount = parse_amount(raw.get("total_amount"), "total_amount")
    invoice_date = parse_date_required(raw.get("invoice_date"), "invoice_date")
    due_date = parse_date_optional(raw.get("due_date"), "due_date")
    tax_amount_raw = raw.get("tax_amount")
    tax_amount = parse_amount(tax_amount_raw, "tax_amount") if tax_amount_raw else None

    period_start, period_end = parse_service_period(raw.get("service_period"))

    invoice = Invoice(
        invoice_id=uuid4(),
        file_id=file_id,
        vendor_name=vendor_name,
        vendor_id=None,
        account_number=raw.get("account_number"),
        invoice_number=raw.get("invoice_number"),
        total_amount=total_amount,
        currency=raw.get("currency", "USD"),
        tax_amount=tax_amount,
        invoice_date=invoice_date,
        due_date=due_date,
        service_period_start=period_start,
        service_period_end=period_end,
        extracted_at=now,
        normalized_at=now,
        status=InvoiceStatus.PENDING,
    )

    logger.info(
        "normalization complete",
        invoice_id=str(invoice.invoice_id),
        vendor=invoice.vendor_name,
        total=str(invoice.total_amount),
        invoice_date=str(invoice.invoice_date),
    )

    return invoice