import pytest
from decimal import Decimal
from datetime import date, timedelta
from uuid import uuid4
from unittest.mock import patch

from src.validation.rules import (
    check_amount_threshold,
    check_amount_is_positive,
    check_vendor_known,
    check_dates_sensible,
    check_required_fields,
)
from src.normalization.schema import Invoice, InvoiceStatus
from src.validation.engine import validate
from datetime import datetime, timezone


def make_invoice(**overrides) -> Invoice:
    """
    Helper that builds a valid invoice for testing.
    Override any field by passing it as a keyword argument.
    This is a common test pattern — a factory function with
    sensible defaults so each test only specifies what it cares about.
    """
    defaults = dict(
        invoice_id=uuid4(),
        file_id=uuid4(),
        vendor_name="Verizon Communications Inc",
        total_amount=Decimal("1321.79"),
        currency="USD",
        invoice_date=date(2024, 1, 15),
        due_date=date(2024, 2, 15),
        extracted_at=datetime.now(timezone.utc),
        normalized_at=datetime.now(timezone.utc),
        status=InvoiceStatus.PENDING,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


# --- amount threshold ---

def test_amount_below_threshold_passes():
    invoice = make_invoice(total_amount=Decimal("999.99"))
    result = check_amount_threshold(invoice, Decimal("1000.00"))
    assert result.passed is True


def test_amount_at_threshold_passes():
    invoice = make_invoice(total_amount=Decimal("1000.00"))
    result = check_amount_threshold(invoice, Decimal("1000.00"))
    assert result.passed is True


def test_amount_above_threshold_fails():
    invoice = make_invoice(total_amount=Decimal("1500.00"))
    result = check_amount_threshold(invoice, Decimal("1000.00"))
    assert result.passed is False
    assert "1500" in result.message


# --- amount positive ---

def test_positive_amount_passes():
    invoice = make_invoice(total_amount=Decimal("100.00"))
    result = check_amount_is_positive(invoice)
    assert result.passed is True


def test_zero_amount_fails():
    invoice = make_invoice(total_amount=Decimal("0.00"))
    result = check_amount_is_positive(invoice)
    assert result.passed is False


# --- vendor known ---

def test_known_vendor_passes():
    invoice = make_invoice(vendor_name="Verizon Communications Inc")
    result = check_vendor_known(invoice)
    assert result.passed is True


def test_unknown_vendor_fails():
    invoice = make_invoice(vendor_name="unknown")
    result = check_vendor_known(invoice)
    assert result.passed is False


# --- dates sensible ---

def test_past_invoice_date_passes():
    invoice = make_invoice(invoice_date=date(2024, 1, 15))
    result = check_dates_sensible(invoice)
    assert result.passed is True


def test_future_invoice_date_fails():
    future = date.today() + timedelta(days=10)
    invoice = make_invoice(invoice_date=future, due_date=None)
    result = check_dates_sensible(invoice)
    assert result.passed is False
    assert "future" in result.message


# --- required fields ---

def test_all_required_fields_present_passes():
    invoice = make_invoice()
    result = check_required_fields(invoice)
    assert result.passed is True


# --- full engine ---

def test_validate_all_pass():
    invoice = make_invoice()
    report = validate(invoice)
    assert report.passed is True
    assert len(report.failed_rules()) == 0


def test_validate_returns_all_results_even_when_one_fails():
    invoice = make_invoice(total_amount=Decimal("99999.00"))
    report = validate(invoice)
    assert len(report.results) == 5
    failed = report.failed_rules()
    assert any(r.rule_name == "amount_threshold" for r in failed)