FROM python:3.12-slim

# Install system dependencies - Tesseract, Poppler, and build tools
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    gcc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies first (Docker layer caching -
# if your code changes but dependencies don't, this layer is reused
# and the build is much faster)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy the rest of the code
COPY . .

# Create directories the app needs at runtime
RUN mkdir -p uploads staging incoming fixtures/invoices

# Generate the sample invoice fixture so the demo always has something to test with
RUN pip install --no-cache-dir reportlab && python -c "
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
c = canvas.Canvas('fixtures/invoices/sample_invoice.pdf', pagesize=letter)
c.setFont('Helvetica-Bold', 16)
c.drawString(50, 750, 'Verizon Communications Inc')
c.setFont('Helvetica', 12)
c.drawString(50, 720, 'Invoice Number: INV-2024-00892')
c.drawString(50, 700, 'Invoice Date: January 15, 2024')
c.drawString(50, 680, 'Due Date: February 15, 2024')
c.drawString(50, 660, 'Account Number: 831-555-0192')
c.drawString(50, 640, 'Service Period: Jan 1 - Jan 31, 2024')
c.drawString(50, 600, 'Monthly Service Charge         \$1,234.56')
c.drawString(50, 580, 'Federal Tax                       \$87.23')
c.drawString(50, 540, 'Total Amount Due:              \$1,321.79')
c.save()
"

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]