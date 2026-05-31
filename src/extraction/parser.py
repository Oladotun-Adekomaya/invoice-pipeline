import json
import re
from typing import TypedDict

import anthropic

from src.config import settings
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
    service_type: str       # voice, data, cloud, mobility etc
    circuit_id: str         # telecom line/circuit identifier
    site_id: str            # location/site being billed
    po_number: str          # purchase order number
    cost_center: str        # internal department code


EXTRACTION_PROMPT = """You are a telecom and IT expense management specialist. 
Extract structured fields from the following invoice text.

Return ONLY a valid JSON object. Omit fields that are not found.

Fields to extract:
- vendor_name: the company issuing the invoice
- invoice_number: invoice or document number
- invoice_date: invoice date in YYYY-MM-DD format
- due_date: payment due date in YYYY-MM-DD format
- total_amount: final total as plain number e.g. "1321.79"
- currency: ISO 4217 code e.g. USD, EUR, GBP, CAD
- account_number: customer or account number
- service_period: billing period e.g. "July 1 - July 31, 2024"
- tax_amount: tax or VAT amount as plain number
- service_type: type of service e.g. "voice", "data", "cloud compute", "mobility"
- circuit_id: telecom circuit or line identifier if present
- site_id: location or site identifier if present
- po_number: purchase order number if present
- cost_center: cost center or department code if present

Rules:
- Dates always in YYYY-MM-DD format
- Amounts as plain numbers, no symbols or commas
- Return ONLY the JSON object, no markdown, no explanation

Invoice text:
"""

def parse_text(raw_text: str) -> RawInvoiceFields:
    if not settings.anthropic_api_key:
        logger.warning("no anthropic api key set, falling back to regex parser")
        return _regex_fallback(raw_text)

    logger.info("extracting fields with claude")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT + raw_text
                }
            ]
        )

        from anthropic.types import TextBlock

        text_blocks = [block for block in message.content if isinstance(block, TextBlock)]
        if not text_blocks:
            raise ValueError("claude returned no text content")
        response_text = text_blocks[0].text.strip()

        # Strip markdown code fences if Claude adds them despite instructions
        response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

        fields = json.loads(response_text)

        # Ensure all values are strings (Claude occasionally returns numbers)
        cleaned: RawInvoiceFields = {}
        for key, value in fields.items():
            if key in RawInvoiceFields.__annotations__ and value is not None:
                cleaned[key] = str(value)  # type: ignore

        found = list(cleaned.keys())
        logger.info(
            "ai extraction complete",
            fields_found=found,
            total_fields=len(found),
            model="claude-haiku",
        )

        return cleaned

    except json.JSONDecodeError as e:
        logger.error("claude returned invalid json, falling back to regex", error=str(e))
        return _regex_fallback(raw_text)
    except Exception as e:
        logger.error("claude extraction failed, falling back to regex", error=str(e))
        return _regex_fallback(raw_text)


def _regex_fallback(raw_text: str) -> RawInvoiceFields:
    """
    Simple regex fallback used when the AI extraction is unavailable.
    Less accurate but works without an API key.
    """
    logger.info("using regex fallback for extraction")
    fields: RawInvoiceFields = {}

    def _find(pattern: str, text: str = raw_text, flags: int = re.IGNORECASE) -> str | None:
        match = re.search(pattern, text, flags)
        return match.group(1).strip() if match else None

    vendor = _find(r"^(AMAZON\s+WEB\s+SERVICES[A-Za-z\s]*(?:SARL|LLC|Inc\.?))", flags=re.MULTILINE) or \
             _find(r"^(CANADA\s+POST\s+CORPORATION)", flags=re.MULTILINE | re.IGNORECASE) or \
             _find(r"^([A-Z][A-Za-z0-9\s&,\.]+(?:Corp|Inc|LLC|Ltd|Co|SARL)\.?)", flags=re.MULTILINE)
    if vendor:
        fields["vendor_name"] = vendor.strip()

    inv_num = _find(r"(?:vat\s+|tax\s+)?invoice\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9\-]+)")
    if inv_num:
        fields["invoice_number"] = inv_num

    inv_date = _find(r"(?:vat\s+|tax\s+)?invoice\s*date[^:]*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2})") or \
               _find(r"(?:vat\s+|tax\s+)?invoice\s*date\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})")
    if inv_date:
        fields["invoice_date"] = inv_date.strip()

    due = _find(r"(?:due\s*date|payment\s*is\s*due\s*by)[^:]*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2})")
    if due:
        fields["due_date"] = due.strip()

    total = _find(r"total\s*amount[^:]*[:\-]?\s*(?:USD|EUR|GBP|CAD)?\s*\$?\s*([\d,]+\.?\d{0,2})")
    if total:
        fields["total_amount"] = total.replace(",", "")
        if re.search(r"canada\s*post|canadian", raw_text, re.IGNORECASE):
            fields["currency"] = "CAD"
        elif re.search(r"selected\s+(EUR|GBP)\s+as\s+your\s+preferred", raw_text, re.IGNORECASE):
            m = re.search(r"selected\s+(EUR|GBP)\s+as", raw_text, re.IGNORECASE)
            fields["currency"] = m.group(1).upper() if m else "USD"
        else:
            fields["currency"] = "USD"

    account = _find(r"(?:account|customer)\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9\-]+)")
    if account:
        fields["account_number"] = account

    logger.info("regex fallback complete", fields_found=list(fields.keys()))
    return fields