import re
from typing import TypedDict

from src.observability.logger import get_logger

logger = get_logger(__name__)


class RawInvoiceFields(TypedDict, total=False):
    vendor_name: str
    invoice_number: str
    invoice_date: str
    due_date: str
    total_amount: str
    currency: str
    account_number: str
    service_period: str
    tax_amount: str


def _find(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if match:
        return match.group(1).strip()
    return None


def parse_text(raw_text: str) -> RawInvoiceFields:
    fields: RawInvoiceFields = {}

    vendor = _find(r"^([A-Z][A-Za-z0-9\s&,\.]+(?:Corp|Inc|LLC|Ltd|Co)\.?)", raw_text, re.MULTILINE)
    if vendor:
        fields["vendor_name"] = vendor.strip()

    invoice_number = _find(r"invoice\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9\-]+)", raw_text)
    if invoice_number:
        fields["invoice_number"] = invoice_number

    invoice_date = _find(r"invoice\s*date\s*[:\-]?\s*([A-Za-z0-9,\s]+(?:\d{4}))", raw_text)
    if invoice_date:
        fields["invoice_date"] = invoice_date.strip()

    due_date = _find(r"due\s*date\s*[:\-]?\s*([A-Za-z0-9,\s]+(?:\d{4}))", raw_text)
    if due_date:
        fields["due_date"] = due_date.strip()

    total = _find(r"total\s*(?:amount\s*)?(?:due\s*)?[:\-]?\s*\$?([\d,]+\.?\d{0,2})", raw_text)
    if total:
        fields["total_amount"] = total
        fields["currency"] = "USD"

    tax = _find(r"(?:tax|vat)\s*[:\-]?\s*\$?([\d,]+\.?\d{0,2})", raw_text)
    if tax:
        fields["tax_amount"] = tax

    account = _find(r"account\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9\-]+)", raw_text)
    if account:
        fields["account_number"] = account

    period = _find(r"service\s*period\s*[:\-]?\s*([A-Za-z0-9\s,\-–]+(?:\d{4}))", raw_text)
    if period:
        fields["service_period"] = period.strip()

    found = list(fields.keys())
    logger.info("parsing complete", fields_found=found, total_fields=len(found))

    return fields