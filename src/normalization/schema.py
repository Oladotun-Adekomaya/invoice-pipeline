from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass
class LineItem:
    description: str
    total: Decimal
    quantity: Decimal | None = None
    unit_price: Decimal | None = None


@dataclass
class Invoice:
    invoice_id: UUID
    file_id: UUID
    vendor_name: str
    total_amount: Decimal
    currency: str
    invoice_date: date
    extracted_at: datetime
    normalized_at: datetime
    status: InvoiceStatus = InvoiceStatus.PENDING
    vendor_id: str | None = None
    account_number: str | None = None
    tax_amount: Decimal | None = None
    due_date: date | None = None
    service_period_start: date | None = None
    service_period_end: date | None = None
    invoice_number: str | None = None
    line_items: list[LineItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.due_date and self.due_date < self.invoice_date:
            raise ValueError(
                f"due_date {self.due_date} is before invoice_date {self.invoice_date}"
            )
        if (
            self.service_period_start
            and self.service_period_end
            and self.service_period_end < self.service_period_start
        ):
            raise ValueError("service_period_end is before service_period_start")
        if self.total_amount < Decimal("0"):
            raise ValueError("total_amount cannot be negative")