#!/usr/bin/env python3
"""Generate tests/fixtures/monthly.pdf -- a SYNTHETIC monthly PDF report.

Mirrors the *structure* of the PDF-era ENS monthly reports (a ruled table of
fields x measures with a grouped header and a unit row) so the pdfplumber-based
parser is exercised without a real download. Numbers are made up.

Requires reportlab (dev-only; not a runtime dependency). Regenerate with:

    python -m pip install reportlab
    python tests/fixtures/make_pdf_fixture.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

OUT = Path(__file__).resolve().parent / "monthly.pdf"

# Header: a field column + oil/gas/water/water-injection, with a unit row.
HEADER = [
    ["Field", "Oil", "Sales gas", "Water", "Water inj."],
    ["", "1000 m3", "mio. Nm3", "1000 m3", "1000 m3"],
]
ROWS = [
    ["Dan", "118.7", "58.2", "905.3", "402.1"],
    ["Gorm", "59.9", "27.4", "415.0", "150.0"],
    ["Halfdan", "798.1", "236.5", "2438.7", "889.4"],
    ["Tyra", "12.3", "402.8", "300.5", "0.0"],
    ["Total", "989.0", "724.9", "4059.5", "1441.5"],
]


def main() -> None:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            topMargin=20 * mm, leftMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Production January 2024 (SI Units)", styles["Title"]),
        Paragraph("Preliminary figures, Danish part of the North Sea.",
                  styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    data = HEADER + ROWS
    table = Table(data, repeatRows=2)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 1), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    story.append(table)
    doc.build(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
