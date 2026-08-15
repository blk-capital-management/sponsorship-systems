"""Build the Franklin Templeton resume book — a single class-of-2029 section.

Franklin Templeton asked for one cohort only: graduation dates between
December 2028 and June 2029. There is no per-firm rating column and no
career-interest filter for this book, so eligibility is graduation year plus
the region rule (EMEA students are handled under a separate EMEA prospectus).

Structure of the output PDF:
  [cover page]
  [Class of 2029 divider]
  [resume 1] [resume 2] ...
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from pypdf import PdfReader, PdfWriter
from tqdm import tqdm

from src.auth.google_auth import build_credentials
from src.clients.drive_client import DriveClient
from src.processing.audit_filter import filter_recent_and_dedupe, parse_graduation_year
from src.processing.pdf_merger import merge_pdfs
from src.processing.resume_downloader import download_resume
from src.utils.file_utils import ensure_dir, write_bytes, file_size_mb
from src.utils.logging import get_logger

log = get_logger("builders.franklin_templeton_builder")

# ── Audit-form column headers (specific to the BLK Undergraduate Audit form) ──

TIMESTAMP_COL = "Timestamp"
BLK_ID_COL = "BLK ID:"
GRAD_YEAR_COL = "When will you graduate?"
GRAD_YEAR_FALLBACK_COL = "When will/did you graduate?"
REGION_COL = "What Region are you in?"

# ── Eligibility criteria confirmed with the Sponsorships Chair ────────────────

TARGET_GRAD_YEAR = 2029   # "graduation dates between December 2028 and June 2029"

EXCLUDED_REGIONS = ["EMEA"]   # exclude students whose universities are in these regions

COVER_DIVIDER_PDF_NAME = "Franklin Templeton Resume Book Covers.pdf"
OUTPUT_FILENAME = "Franklin_Templeton_Resume_Book.pdf"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class FranklinTempletonBuildResult:
    success: bool
    output_path: Optional[Path] = None
    eligible_count: int = 0
    included_count: int = 0
    skipped_count: int = 0
    error_detail: Optional[str] = None


# ── Cover/divider asset loader ────────────────────────────────────────────────

def _load_cover_and_divider(settings: dict, config_dir: Path) -> Optional[tuple[bytes, bytes]]:
    """Split the 2-page Franklin Templeton PDF into (cover, class-of-2029 divider)."""
    raw = settings.get("input", {}).get("cover_pages_dir", "")
    if not raw:
        return None

    from src.utils.file_utils import resolve_path
    cover_dir = resolve_path(raw, config_dir.parent)
    pdf_path = cover_dir / COVER_DIVIDER_PDF_NAME
    if not pdf_path.exists():
        log.error("Franklin Templeton cover/divider PDF not found: %s", pdf_path)
        return None

    reader = PdfReader(str(pdf_path))
    if len(reader.pages) < 2:
        log.error(
            "Franklin Templeton cover/divider PDF has %d page(s); expected 2 (cover, Class of 2029).",
            len(reader.pages),
        )
        return None

    def _page_bytes(page) -> bytes:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    return _page_bytes(reader.pages[0]), _page_bytes(reader.pages[1])


# ── Eligibility ────────────────────────────────────────────────────────────────

def _compute_eligibility(df: pd.DataFrame) -> pd.DataFrame:
    """Return the deduped, recency-filtered rows for the class-of-2029 cohort."""
    working = filter_recent_and_dedupe(df, TIMESTAMP_COL, BLK_ID_COL, min_year=2026)

    working["_grad_year"] = working.apply(
        lambda row: parse_graduation_year(row, GRAD_YEAR_COL, GRAD_YEAR_FALLBACK_COL), axis=1
    )
    not_excluded_region = ~working[REGION_COL].isin(EXCLUDED_REGIONS)

    working["_eligible"] = (working["_grad_year"] == TARGET_GRAD_YEAR) & not_excluded_region
    return working


# ── Resume download ───────────────────────────────────────────────────────────

def _download_section(
    section_df: pd.DataFrame,
    resolved: dict,
    drive_client: DriveClient,
    first_page_only: bool,
) -> tuple[list[bytes], int]:
    col_first = resolved.get("first_name")
    col_last = resolved.get("last_name")
    col_link = resolved.get("resume_link")

    pdf_parts: list[bytes] = []
    skipped = 0

    section_df = section_df.sort_values(by=[col_last, col_first]) if col_last and col_first else section_df

    for _, row in tqdm(section_df.iterrows(), total=len(section_df), desc="  Class of 2029"):
        first = row.get(col_first, "").strip() if col_first else ""
        last = row.get(col_last, "").strip() if col_last else ""
        link = row.get(col_link, "").strip() if col_link else ""
        name = f"{first} {last}".strip() or "(unknown)"

        result = download_resume(drive_client, link, first_page_only=first_page_only)
        if result.success and result.pdf_bytes:
            pdf_parts.append(result.pdf_bytes)
            log.debug("    + %s", name)
        else:
            skipped += 1
            log.warning("    SKIP %s: [%s] %s", name, result.error_type, result.error_detail)

    return pdf_parts, skipped


# ── Compression helper ────────────────────────────────────────────────────────

def _try_compress(pdf_bytes: bytes, tier: str, settings: dict) -> Optional[bytes]:
    compression_cfg = settings.get("compression", {})
    backend = compression_cfg.get("backend", "ghostscript")
    if backend != "ghostscript":
        return None
    try:
        from src.processing.pdf_compressor import compress_pdf_ghostscript
        return compress_pdf_ghostscript(pdf_bytes, tier)
    except Exception as exc:
        log.warning("Compression failed (%s): %s", tier, exc)
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def build_franklin_templeton(
    settings: dict,
    config_dir: Path,
    df: pd.DataFrame,
    resolved: dict,
) -> FranklinTempletonBuildResult:
    for required_col in (TIMESTAMP_COL, BLK_ID_COL, GRAD_YEAR_COL, REGION_COL):
        if required_col not in df.columns:
            return FranklinTempletonBuildResult(
                success=False,
                error_detail=f"Expected audit column not found in spreadsheet: '{required_col}'.",
            )

    assets = _load_cover_and_divider(settings, config_dir)
    if assets is None:
        return FranklinTempletonBuildResult(
            success=False,
            error_detail=(
                f"Could not load '{COVER_DIVIDER_PDF_NAME}' from input/cover_pages/. "
                "It must be a 2-page PDF: cover, Class of 2029 divider."
            ),
        )
    cover_bytes, divider_bytes = assets

    working = _compute_eligibility(df)
    eligible_df = working[working["_eligible"]]

    if len(eligible_df) == 0:
        return FranklinTempletonBuildResult(
            success=False,
            error_detail=f"No eligible class-of-{TARGET_GRAD_YEAR} students found.",
        )

    try:
        creds = build_credentials(settings, config_dir)
        drive_client = DriveClient(creds)
    except Exception as exc:
        return FranklinTempletonBuildResult(
            success=False,
            eligible_count=len(eligible_df),
            error_detail=f"Google authentication failed: {exc}",
        )

    processing_cfg = settings.get("processing", {})
    first_page_only: bool = processing_cfg.get("first_page_only", True)

    log.info("Franklin Templeton book: %d class-of-%d students eligible.", len(eligible_df), TARGET_GRAD_YEAR)

    pdfs, skipped = _download_section(eligible_df, resolved, drive_client, first_page_only)

    if not pdfs:
        return FranklinTempletonBuildResult(
            success=False,
            eligible_count=len(eligible_df),
            skipped_count=skipped,
            error_detail="All resume downloads failed.",
        )

    # ── Assemble ──────────────────────────────────────────────────────────────
    merged = merge_pdfs([cover_bytes, divider_bytes, *pdfs])

    compression_cfg = settings.get("compression", {})
    final_tier = compression_cfg.get("final_book_tier", "email_quality")
    max_mb = float(compression_cfg.get("max_pdf_mb", 20))

    compressed = _try_compress(merged, final_tier, settings)
    final_bytes = compressed if compressed else merged

    output_cfg = settings.get("output", {})
    sponsor_books_dir = Path(output_cfg.get("sponsor_books_dir", "output/sponsor_books"))
    ensure_dir(sponsor_books_dir)
    out_path = sponsor_books_dir / OUTPUT_FILENAME
    write_bytes(out_path, final_bytes)

    size_mb = file_size_mb(out_path)
    log.info("Franklin Templeton resume book saved: %s (%.2f MB)", out_path, size_mb)
    if size_mb > max_mb:
        log.warning("Franklin Templeton book (%.2f MB) exceeds max_pdf_mb=%.0f.", size_mb, max_mb)

    return FranklinTempletonBuildResult(
        success=True,
        output_path=out_path,
        eligible_count=len(eligible_df),
        included_count=len(pdfs),
        skipped_count=skipped,
    )
