import argparse
from pathlib import Path

from reportlab.lib.colors import black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def main() -> None:
    parser = argparse.ArgumentParser(description="Place a ChArUco image on an A4 PDF at exact size.")
    parser.add_argument("--board-image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = A4
    pdf = canvas.Canvas(str(args.output), pagesize=A4, pageCompression=1)
    pdf.setTitle("Crab measurement ChArUco calibration board")
    pdf.setAuthor("Crab measurement project")
    pdf.setFillColor(black)

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawCentredString(page_width / 2, page_height - 12 * mm, "METRIC CHARUCO CALIBRATION BOARD")
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 18 * mm,
        "5 x 7 squares | square 30.00 mm | marker 22.00 mm | DICT_4X4_50",
    )
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 24 * mm,
        "PRINT AT ACTUAL SIZE / 100% - DO NOT FIT OR SCALE",
    )

    board_width = 150 * mm
    board_height = 210 * mm
    board_x = (page_width - board_width) / 2
    board_y = 43 * mm
    pdf.drawImage(
        str(args.board_image),
        board_x,
        board_y,
        width=board_width,
        height=board_height,
        preserveAspectRatio=False,
        mask="auto",
    )

    ruler_y = 25 * mm
    ruler_x = (page_width - 100 * mm) / 2
    pdf.setLineWidth(0.35 * mm)
    pdf.line(ruler_x, ruler_y, ruler_x + 100 * mm, ruler_y)
    for value_mm in range(0, 101, 10):
        x = ruler_x + value_mm * mm
        tick = 3.5 * mm if value_mm in (0, 50, 100) else 2.0 * mm
        pdf.line(x, ruler_y - tick / 2, x, ruler_y + tick / 2)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(page_width / 2, 17 * mm, "REFERENCE LENGTH: 100.00 mm")
    pdf.setFont("Helvetica", 7)
    pdf.drawCentredString(
        page_width / 2,
        12 * mm,
        "Verify this line and one 30 mm square with a ruler or caliper before calibration.",
    )

    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    main()
