from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from pathlib import Path

Path("fixtures/invoices").mkdir(parents=True, exist_ok=True)

c = canvas.Canvas("fixtures/invoices/sample_invoice.pdf", pagesize=letter)
c.setFont("Helvetica-Bold", 16)
c.drawString(50, 750, "Verizon Communications Inc")
c.setFont("Helvetica", 12)
c.drawString(50, 720, "Invoice Number: INV-2024-00892")
c.drawString(50, 700, "Invoice Date: January 15, 2024")
c.drawString(50, 680, "Due Date: February 15, 2024")
c.drawString(50, 660, "Account Number: 831-555-0192")
c.drawString(50, 640, "Service Period: Jan 1 - Jan 31, 2024")
c.drawString(50, 600, "Monthly Service Charge         $1,234.56")
c.drawString(50, 580, "Federal Tax                       $87.23")
c.drawString(50, 540, "Total Amount Due:              $1,321.79")
c.save()
print("sample invoice created at fixtures/invoices/sample_invoice.pdf")