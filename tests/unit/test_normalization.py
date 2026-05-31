import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from src.normalization.transformer import (
    parse_amount,
    parse_date_required,
    parse_date_optional,
    parse_service_period,
    transform,
    NormalizationError,
)
from src.extraction.parser import RawInvoiceFields


# --- parse_amount ---

def test_parse_amount_standard():
    assert parse_amount("$1,234.56", "total") == Decimal("1234.56")


def test_parse_amount_no_symbol():
    assert parse_amount("1234.56", "total") == Decimal("1234.56")


def test_parse_amount_no_decimal():
    assert parse_amount("500", "total") == Decimal("500")


def test_parse_amount_with_commas():
    assert parse_amount("1,000,000.00", "total") == Decimal("1000000.00")


def test_parse_amount_missing_raises():
    with pytest.raises(NormalizationError) as exc:
        parse_amount(None, "total_amount")
    assert exc.value.field == "total_amount"


def test_parse_amount_non_numeric_raises():
    with pytest.raises(NormalizationError):
        parse_amount("N/A", "total_amount")


# --- parse_date_required ---

def test_parse_date_iso_format():
    assert parse_date_required("2024-01-15", "invoice_date") == date(2024, 1, 15)


def test_parse_date_verbose_format():
    assert parse_date_required("January 15, 2024", "invoice_date") == date(2024, 1, 15)


def test_parse_date_short_format():
    assert parse_date_required("Jan 15, 2024", "invoice_date") == date(2024, 1, 15)


def test_parse_date_missing_raises():
    with pytest.raises(NormalizationError) as exc:
        parse_date_required(None, "invoice_date")
    assert exc.value.field == "invoice_date"


# --- parse_date_optional ---

def test_parse_date_optional_returns_none_when_missing():
    assert parse_date_optional(None, "due_date") is None


def test_parse_date_optional_parses_when_present():
    assert parse_date_optional("February 15, 2024", "due_date") == date(2024, 2, 15)


# --- parse_service_period ---

def test_parse_service_period_standard():
    start, end = parse_service_period("Jan 1 - Jan 31, 2024")
    assert start == date(2024, 1, 1)
    assert end == date(2024, 1, 31)


def test_parse_service_period_missing_returns_none():
    start, end = parse_service_period(None)
    assert start is None
    assert end is None


def test_parse_service_period_unparseable_returns_none():
    start, end = parse_service_period("not a date range at all")
    assert start is None
    assert end is None


# --- transform ---

def test_transform_produces_invoice():
    raw: RawInvoiceFields = {
        "vendor_name": "Verizon Communications Inc",
        "invoice_number": "INV-2024-001",
        "invoice_date": "January 15, 2024",
        "due_date": "February 15, 2024",
        "total_amount": "1,321.79",
        "currency": "USD",
        "tax_amount": "87.23",
        "account_number": "831-555-0192",
        "service_period": "Jan 1 - Jan 31, 2024",
    }
    invoice = transform(raw, file_id=uuid4())
    assert invoice.vendor_name == "Verizon Communications Inc"
    assert invoice.total_amount == Decimal("1321.79")
    assert invoice.invoice_date == date(2024, 1, 15)
    assert invoice.due_date == date(2024, 2, 15)
    assert invoice.tax_amount == Decimal("87.23")


def test_transform_missing_vendor_raises():
    raw: RawInvoiceFields = {
        "total_amount": "100.00",
        "invoice_date": "January 15, 2024",
    }
    with pytest.raises(NormalizationError) as exc:
        transform(raw, file_id=uuid4())
    assert exc.value.field == "vendor_name"


def test_transform_missing_total_raises():
    raw: RawInvoiceFields = {
        "vendor_name": "Verizon Communications Inc",
        "invoice_date": "January 15, 2024",
    }
    with pytest.raises(NormalizationError) as exc:
        transform(raw, file_id=uuid4())
    assert exc.value.field == "total_amount"