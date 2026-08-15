"""Generate a minimal fallback divider page when no Canva PDF exists for a year.

The generated page is clean, professional, black-and-white, and uses only
standard PDF/ReportLab primitives — no unofficial logos or brand marks.

Usage:
    from src.processing.divider_generator import generate_divider_page
    pdf_bytes = generate_divider_page(2026)
"""

from __future__ import annotations

import io
from typing import Optional

from src.utils.logging import get_logger

log = get_logger("processing.divider_generator")


def generate_divider_page(year: int) -> Optional[bytes]:
    """Return PDF bytes for a simple divider page for *year*.

    Falls back gracefully if reportlab is unavailable (returns None so the
    caller can omit the divider rather than crash).
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        log.warning(
            "reportlab not installed — cannot generate divider page for %d. "
            "Install with: pip install reportlab",
            year,
        )
        return None

    buf = io.BytesIO()
    width, height = letter  # 8.5 × 11 inches
    c = rl_canvas.Canvas(buf, pagesize=letter)

    # ── Background ────────────────────────────────────────────────────────────
    c.setFillColorRGB(1, 1, 1)  # white
    c.rect(0, 0, width, height, fill=1, stroke=0)

    # ── Top accent bar ────────────────────────────────────────────────────────
    bar_height = 0.18 * inch
    c.setFillColorRGB(0, 0, 0)  # black
    c.rect(0, height - bar_height, width, bar_height, fill=1, stroke=0)

    # ── Bottom accent bar ─────────────────────────────────────────────────────
    c.rect(0, 0, width, bar_height, fill=1, stroke=0)

    # ── Organisation name ─────────────────────────────────────────────────────
    c.setFont("Helvetica", 13)
    c.setFillColorRGB(0, 0, 0)
    org_label = "BLK Capital Management"
    org_w = c.stringWidth(org_label, "Helvetica", 13)
    c.drawString((width - org_w) / 2, height * 0.60, org_label)

    # ── Thin horizontal rule ──────────────────────────────────────────────────
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.5)
    rule_y = height * 0.55
    margin = 1.5 * inch
    c.line(margin, rule_y, width - margin, rule_y)

    # ── Graduation year (large) ───────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 64)
    year_label = str(year)
    year_w = c.stringWidth(year_label, "Helvetica-Bold", 64)
    c.drawString((width - year_w) / 2, height * 0.40, year_label)

    # ── Sub-label ─────────────────────────────────────────────────────────────
    c.setFont("Helvetica", 11)
    sub_label = "Graduating Class"
    sub_w = c.stringWidth(sub_label, "Helvetica", 11)
    c.drawString((width - sub_w) / 2, height * 0.36, sub_label)

    c.save()
    return buf.getvalue()
