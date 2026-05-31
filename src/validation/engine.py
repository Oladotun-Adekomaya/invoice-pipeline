from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from src.config import settings
from src.normalization.schema import Invoice
from src.observability.logger import get_logger
from src.validation.rules import (
    RuleResult,
    check_amount_is_positive,
    check_amount_threshold,
    check_dates_sensible,
    check_required_fields,
    check_vendor_known,
)

logger = get_logger(__name__)


@dataclass
class ValidationReport:
    invoice_id: UUID
    results: list[RuleResult]
    passed: bool
    validated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def failed_rules(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed]


def validate(invoice: Invoice) -> ValidationReport:
    logger.info("validating invoice", invoice_id=str(invoice.invoice_id))

    results: list[RuleResult] = []

    results.append(check_required_fields(invoice))
    results.append(check_amount_is_positive(invoice))
    results.append(check_amount_threshold(invoice, settings.auto_approve_threshold))
    results.append(check_vendor_known(invoice))
    results.append(check_dates_sensible(invoice))

    passed = all(r.passed for r in results)

    for result in results:
        if not result.passed:
            logger.warning(
                "rule failed",
                rule=result.rule_name,
                message=result.message,
                invoice_id=str(invoice.invoice_id),
            )

    logger.info(
        "validation complete",
        invoice_id=str(invoice.invoice_id),
        passed=passed,
        failed_count=len([r for r in results if not r.passed]),
        total_rules=len(results),
    )

    return ValidationReport(
        invoice_id=invoice.invoice_id,
        results=results,
        passed=passed,
    )