from dataclasses import dataclass
from decimal import Decimal
from datetime import date

from src.normalization.schema import Invoice
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RuleResult:
    rule_name: str
    passed: bool
    message: str | None = None


def check_amount_threshold(
    invoice: Invoice,
    threshold: Decimal,
) -> RuleResult:
    passed = invoice.total_amount <= threshold
    return RuleResult(
        rule_name="amount_threshold",
        passed=passed,
        message=None if passed else (
            f"amount {invoice.total_amount} exceeds threshold {threshold}"
        ),
    )


def check_vendor_known(invoice: Invoice) -> RuleResult:
    # For now a vendor is "known" if the name is not empty and not generic
    # In production this would query a vendor master table
    blocklist = {"unknown", "test", "sample", ""}
    name_lower = invoice.vendor_name.lower().strip()
    passed = name_lower not in blocklist
    return RuleResult(
        rule_name="vendor_known",
        passed=passed,
        message=None if passed else f"vendor '{invoice.vendor_name}' is not recognised",
    )


def check_dates_sensible(invoice: Invoice) -> RuleResult:
    today = date.today()

    if invoice.invoice_date > today:
        return RuleResult(
            rule_name="dates_sensible",
            passed=False,
            message=f"invoice_date {invoice.invoice_date} is in the future",
        )

    if invoice.due_date and invoice.due_date < invoice.invoice_date:
        return RuleResult(
            rule_name="dates_sensible",
            passed=False,
            message=f"due_date {invoice.due_date} is before invoice_date {invoice.invoice_date}",
        )

    return RuleResult(rule_name="dates_sensible", passed=True)


def check_amount_is_positive(invoice: Invoice) -> RuleResult:
    passed = invoice.total_amount > Decimal("0")
    return RuleResult(
        rule_name="amount_positive",
        passed=passed,
        message=None if passed else "total_amount must be greater than zero",
    )


def check_required_fields(invoice: Invoice) -> RuleResult:
    missing = []
    if not invoice.vendor_name:
        missing.append("vendor_name")
    if not invoice.invoice_date:
        missing.append("invoice_date")
    if not invoice.total_amount:
        missing.append("total_amount")

    passed = len(missing) == 0
    return RuleResult(
        rule_name="required_fields",
        passed=passed,
        message=None if passed else f"missing required fields: {missing}",
    )