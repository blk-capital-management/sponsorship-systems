"""Extract only the first page from a PDF.

Lifted and cleaned up from legacy/custom_sponsor_resume_book_builder.py L192-L207.
"""

from typing import Optional
import io

from pypdf import PdfReader, PdfWriter

from src.utils.logging import get_logger

log = get_logger("processing.first_page_extractor")


def extract_first_page(pdf_bytes: bytes) -> Optional[bytes]:
    """Return a single-page PDF containing only the first page of pdf_bytes.

    Args:
        pdf_bytes: Raw bytes of a valid PDF file.

    Returns:
        PDF bytes for page 1 only, or None if extraction fails.
    """
    if not pdf_bytes:
        log.warning("extract_first_page called with empty bytes.")
        return None

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) == 0:
            log.warning("PDF has 0 pages.")
            return None

        writer = PdfWriter()
        writer.add_page(reader.pages[0])

        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        log.error("Failed to extract first page: %s", exc)
        return None


def count_pages(pdf_bytes: bytes) -> int:
    """Return the page count of a PDF, or 0 on error."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception:
        return 0
