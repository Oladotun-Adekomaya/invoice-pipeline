CREATE TABLE IF NOT EXISTS invoices (
    invoice_id          UUID PRIMARY KEY,
    file_id             UUID NOT NULL,
    vendor_name         TEXT NOT NULL,
    vendor_id           TEXT,
    account_number      TEXT,
    total_amount        NUMERIC(12, 2) NOT NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'USD',
    tax_amount          NUMERIC(12, 2),
    invoice_date        DATE NOT NULL,
    due_date            DATE,
    service_period_start DATE,
    service_period_end   DATE,
    status              TEXT NOT NULL DEFAULT 'pending',
    extracted_at        TIMESTAMPTZ NOT NULL,
    normalized_at       TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS line_items (
    id          UUID PRIMARY KEY,
    invoice_id  UUID NOT NULL REFERENCES invoices(invoice_id),
    description TEXT NOT NULL,
    quantity    NUMERIC(10, 4),
    unit_price  NUMERIC(12, 2),
    total       NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS dead_letter_invoices (
    id                UUID PRIMARY KEY,
    file_id           UUID NOT NULL,
    original_filename TEXT,
    error_stage       TEXT NOT NULL,
    error_detail      JSONB NOT NULL,
    raw_ocr_output    JSONB,
    received_at       TIMESTAMPTZ NOT NULL,
    failed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_metrics (
    id          BIGSERIAL PRIMARY KEY,
    metric_name TEXT NOT NULL,
    value       NUMERIC,
    labels      JSONB,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);