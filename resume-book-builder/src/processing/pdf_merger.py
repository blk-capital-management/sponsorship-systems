"""Merge multiple PDF byte blobs into a single PDF.

Also provides helpers for loading Canva transition/cover pages from disk,
and a hybrid resolver that falls back to auto-generated divider pages when
a Canva PDF is missing for a graduation year.

Lifted and generalised from legacy scripts:
- merge_pdf_bytes_list() from custom_sponsor_resume_book_builder.py L209-L217
- load_local_transitions() from blk_resume_book_builder.py L120-L130
"""

import io
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter

from src.utils.logging import get_logger

log = get_logger("processing.pdf_merger")

# Tracks the source of each divider returned by resolve_divider_for_year.
# Keys are graduation year ints; values are "canva" | "generated" | "missing".
_divider_source_log: dict[int, str] = {}


def merge_pdfs(pdf_bytes_list: list[bytes]) -> bytes:
    """Merge an ordered list of PDF byte blobs into a single PDF.

    Args:
        pdf_bytes_list: Each element is the bytes of one PDF page or multi-page PDF.

    Returns:
        Merged PDF bytes.

    Raises:
        ValueError: if pdf_bytes_list is empty.
    """
    if not pdf_bytes_list:
        raise ValueError("merge_pdfs called with an empty list.")

    writer = PdfWriter()
    for i, data in enumerate(pdf_bytes_list):
        if not data:
            log.warning("Skipping empty bytes at index %d during merge.", i)
            continue
        try:
            reader = PdfReader(io.BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as exc:
            log.error("Failed to read PDF at index %d during merge: %s", i, exc)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def load_transition_pages(transitions_dir: Path) -> dict[str, bytes]:
    """Load Canva-exported transition/divider PDFs from a folder.

    Files should be named like "2025 Transition.pdf", "2026 Cover.pdf", etc.
    The key is the full stem (filename without .pdf extension), lowercased.
    Callers match by checking if the graduation year appears in the key.

    Args:
        transitions_dir: Path to input/transition_pages/ or similar.

    Returns:
        Dict of {stem_lower: pdf_bytes}. Empty dict if folder is missing or empty.
    """
    result: dict[str, bytes] = {}
    if not transitions_dir.exists():
        log.warning("Transition pages directory not found: %s", transitions_dir)
        return result

    for pdf_path in sorted(transitions_dir.glob("*.pdf")):
        try:
            result[pdf_path.stem.lower()] = pdf_path.read_bytes()
            log.debug("Loaded transition page: %s", pdf_path.name)
        except Exception as exc:
            log.error("Could not read transition page '%s': %s", pdf_path.name, exc)

    log.info("Loaded %d transition page(s) from %s.", len(result), transitions_dir)
    return result


def find_transition_for_year(transitions: dict[str, bytes], year: int) -> Optional[bytes]:
    """Return the transition page bytes for a given graduation year, or None.

    Matches any key that contains the year as a substring (e.g. "2026 transition").
    """
    year_str = str(year)
    for stem, data in transitions.items():
        if year_str in stem:
            return data
    return None


def resolve_divider_for_year(
    transitions: dict[str, bytes],
    year: int,
) -> tuple[Optional[bytes], str]:
    """Return (divider_bytes, source) for *year*.

    source is one of:
      "canva"     — loaded from a manually supplied Canva PDF
      "generated" — auto-generated because no Canva PDF existed
      "missing"   — neither source was available (reportlab not installed)

    Manual Canva PDFs always take priority over generated fallbacks.
    Generated dividers are written to a temp buffer only; they never overwrite
    any file on disk.
    """
    canva = find_transition_for_year(transitions, year)
    if canva is not None:
        _divider_source_log[year] = "canva"
        log.debug("Divider for %d: using Canva/manual PDF.", year)
        return canva, "canva"

    from src.processing.divider_generator import generate_divider_page
    generated = generate_divider_page(year)
    if generated is not None:
        _divider_source_log[year] = "generated"
        log.info(
            "Divider for %d: GENERATED_TRANSITION_PAGE_FALLBACK "
            "(no Canva PDF found — auto-generated clean divider).",
            year,
        )
        return generated, "generated"

    _divider_source_log[year] = "missing"
    log.warning(
        "Divider for %d: no Canva PDF and reportlab unavailable — divider omitted.",
        year,
    )
    return None, "missing"


def get_divider_source_log() -> dict[int, str]:
    """Return a copy of the per-year divider-source log accumulated this run."""
    return dict(_divider_source_log)
