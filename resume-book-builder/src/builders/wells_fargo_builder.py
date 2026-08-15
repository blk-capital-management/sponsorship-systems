"""Build the Wells Fargo resume book — a single PDF with two internal sections
(Juniors, Seniors) driven by graduation year + free-text career-interest matching.

Unlike src/builders/sponsor_builder.py, Wells Fargo has no per-firm rating
column in the audit spreadsheet — eligibility is graduation year plus a
keyword match against one free-text "career paths interest" field. The output
also threads section divider pages (Juniors / Seniors), which the generic
sponsor builder does not do.

Structure of the output PDF:
  [cover page]
  [Juniors divider]
  [Junior resume 1] [Junior resume 2] ...
  [Seniors divider]
  [Senior resume 1] [Senior resume 2] ...
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from pypdf import PdfReader, PdfWriter
from tqdm import tqdm

from src.auth.google_auth import build_credentials
from src.clients.drive_client import DriveClient
from src.processing.audit_filter import (
    filter_recent_and_dedupe,
    parse_graduation_year,
    matches_any_keyword,
)
from src.processing.pdf_merger import merge_pdfs
from src.processing.resume_downloader import download_resume
from src.utils.file_utils import ensure_dir, write_bytes, file_size_mb
from src.utils.logging import get_logger

log = get_logger("builders.wells_fargo_builder")

# ── Audit-form column headers (not part of the generic column_mapping.yaml
# abstraction — specific to the BLK Undergraduate Audit form) ─────────────────

TIMESTAMP_COL = "Timestamp"
BLK_ID_COL = "BLK ID:"
GRAD_YEAR_COL = "When will you graduate?"
GRAD_YEAR_FALLBACK_COL = "When will/did you graduate?"
INTEREST_COL = (
    "As of right now, what career paths interest you most? "
    "We shall do our best to target you with associated opportunities. "
)
TOP_FIRMS_COL = (
    "Feel free to let us know you top firms of interest so we can keep this in mind "
    "for current and future sponsors in the region. Please indicate if the firm "
    "isn't already a sponsor."
)
REGION_COL = "What Region are you in?"

# ── Eligibility criteria confirmed with the Sponsorships Chair ────────────────

JUNIOR_GRAD_YEAR = 2028   # "graduation dates between December 2027 and June 2028"
SENIOR_GRAD_YEAR = 2027   # "graduation dates between December 2026 and June 2027"

EXCLUDED_REGIONS = ["EMEA"]   # exclude students whose universities are in these regions

JUNIOR_KEYWORDS = [
    "Corporate Banking",
    "corporate & commercial banking",
    "Corporate Finance",
    "FP&A",
    "Wealth Management",
    "Asset Management",
]

SENIOR_KEYWORDS = [
    "Investment Banking",
    "Global Capital Markets",
    "Sales and Trading",
    "Quantitative Finance",
    "Quantitative Investing",
    "Real Estate",
]

COVER_DIVIDER_PDF_NAME = "Wells Fargo Resume Book Covers and Dividers.pdf"
OUTPUT_FILENAME = "Wells_Fargo_Resume_Book.pdf"


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class SectionResult:
    section_name: str
    eligible_count: int = 0
    included_count: int = 0
    skipped_count: int = 0


@dataclass
class WellsFargoBuildResult:
    success: bool
    output_path: Optional[Path] = None
    junior: SectionResult = field(default_factory=lambda: SectionResult("Juniors"))
    senior: SectionResult = field(default_factory=lambda: SectionResult("Seniors"))
    error_detail: Optional[str] = None


# ── Cover/divider asset loader ────────────────────────────────────────────────

def _load_cover_and_dividers(settings: dict, config_dir: Path) -> Optional[tuple[bytes, bytes, bytes]]:
    """Split the 3-page Wells Fargo cover/divider PDF into (cover, juniors, seniors)."""
    raw = settings.get("input", {}).get("cover_pages_dir", "")
    if not raw:
        return None

    from src.utils.file_utils import resolve_path
    cover_dir = resolve_path(raw, config_dir.parent)
    pdf_path = cover_dir / COVER_DIVIDER_PDF_NAME
    if not pdf_path.exists():
        log.error("Wells Fargo cover/divider PDF not found: %s", pdf_path)
        return None

    reader = PdfReader(str(pdf_path))
    if len(reader.pages) < 3:
        log.error(
            "Wells Fargo cover/divider PDF has %d page(s); expected 3 (cover, Juniors, Seniors).",
            len(reader.pages),
        )
        return None

    def _page_bytes(page) -> bytes:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    cover = _page_bytes(reader.pages[0])
    juniors_divider = _page_bytes(reader.pages[1])
    seniors_divider = _page_bytes(reader.pages[2])
    return cover, juniors_divider, seniors_divider


# ── Eligibility ────────────────────────────────────────────────────────────────

def _compute_eligibility(df: pd.DataFrame) -> pd.DataFrame:
    """Return a deduped, recency-filtered DataFrame with _grad_year/_junior_eligible/_senior_eligible columns."""
    working = filter_recent_and_dedupe(df, TIMESTAMP_COL, BLK_ID_COL, min_year=2026)

    working["_grad_year"] = working.apply(
        lambda row: parse_graduation_year(row, GRAD_YEAR_COL, GRAD_YEAR_FALLBACK_COL), axis=1
    )
    working["_mentions_wf"] = working[TOP_FIRMS_COL].apply(
        lambda v: matches_any_keyword(v, ["wells fargo", "wells-fargo"])
    )
    working["_junior_keyword_match"] = working[INTEREST_COL].apply(
        lambda v: matches_any_keyword(v, JUNIOR_KEYWORDS)
    )
    working["_senior_keyword_match"] = working[INTEREST_COL].apply(
        lambda v: matches_any_keyword(v, SENIOR_KEYWORDS)
    )

    not_excluded_region = ~working[REGION_COL].isin(EXCLUDED_REGIONS)

    working["_junior_eligible"] = (
        (working["_grad_year"] == JUNIOR_GRAD_YEAR)
        & (working["_junior_keyword_match"] | working["_mentions_wf"])
        & not_excluded_region
    )
    working["_senior_eligible"] = (
        (working["_grad_year"] == SENIOR_GRAD_YEAR)
        & (working["_senior_keyword_match"] | working["_mentions_wf"])
        & not_excluded_region
    )
    return working


# ── Per-section resume download ───────────────────────────────────────────────

def _download_section(
    section_df: pd.DataFrame,
    resolved: dict,
    drive_client: DriveClient,
    first_page_only: bool,
    section_label: str,
) -> tuple[list[bytes], int]:
    col_first = resolved.get("first_name")
    col_last = resolved.get("last_name")
    col_link = resolved.get("resume_link")

    pdf_parts: list[bytes] = []
    skipped = 0

    section_df = section_df.sort_values(by=[col_last, col_first]) if col_last and col_first else section_df

    for _, row in tqdm(section_df.iterrows(), total=len(section_df), desc=f"  {section_label}"):
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
            log.warning(
                "    SKIP %s: [%s] %s", name, result.error_type, result.error_detail,
            )

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

def build_wells_fargo(
    settings: dict,
    config_dir: Path,
    df: pd.DataFrame,
    resolved: dict,
) -> WellsFargoBuildResult:
    for required_col in (TIMESTAMP_COL, BLK_ID_COL, GRAD_YEAR_COL, INTEREST_COL, TOP_FIRMS_COL, REGION_COL):
        if required_col not in df.columns:
            return WellsFargoBuildResult(
                success=False,
                error_detail=f"Expected audit column not found in spreadsheet: '{required_col}'.",
            )

    assets = _load_cover_and_dividers(settings, config_dir)
    if assets is None:
        return WellsFargoBuildResult(
            success=False,
            error_detail=(
                f"Could not load '{COVER_DIVIDER_PDF_NAME}' from input/cover_pages/. "
                "It must be a 3-page PDF: cover, Juniors divider, Seniors divider."
            ),
        )
    cover_bytes, juniors_divider, seniors_divider = assets

    working = _compute_eligibility(df)

    junior_df = working[working["_junior_eligible"]]
    senior_df = working[working["_senior_eligible"]]

    junior_result = SectionResult("Juniors", eligible_count=len(junior_df))
    senior_result = SectionResult("Seniors", eligible_count=len(senior_df))

    if len(junior_df) == 0 and len(senior_df) == 0:
        return WellsFargoBuildResult(
            success=False,
            junior=junior_result,
            senior=senior_result,
            error_detail="No eligible students found for either the Juniors or Seniors section.",
        )

    try:
        creds = build_credentials(settings, config_dir)
        drive_client = DriveClient(creds)
    except Exception as exc:
        return WellsFargoBuildResult(
            success=False,
            junior=junior_result,
            senior=senior_result,
            error_detail=f"Google authentication failed: {exc}",
        )

    processing_cfg = settings.get("processing", {})
    first_page_only: bool = processing_cfg.get("first_page_only", True)

    log.info("Wells Fargo book: %d Junior-eligible, %d Senior-eligible.", len(junior_df), len(senior_df))

    junior_pdfs, junior_skipped = _download_section(
        junior_df, resolved, drive_client, first_page_only, "Juniors"
    )
    senior_pdfs, senior_skipped = _download_section(
        senior_df, resolved, drive_client, first_page_only, "Seniors"
    )

    junior_result.included_count = len(junior_pdfs)
    junior_result.skipped_count = junior_skipped
    senior_result.included_count = len(senior_pdfs)
    senior_result.skipped_count = senior_skipped

    if not junior_pdfs and not senior_pdfs:
        return WellsFargoBuildResult(
            success=False,
            junior=junior_result,
            senior=senior_result,
            error_detail="All resume downloads failed for both sections.",
        )

    # ── Assemble ──────────────────────────────────────────────────────────────
    pdf_parts: list[bytes] = [cover_bytes]
    if junior_pdfs:
        pdf_parts.append(juniors_divider)
        pdf_parts.extend(junior_pdfs)
    if senior_pdfs:
        pdf_parts.append(seniors_divider)
        pdf_parts.extend(senior_pdfs)

    merged = merge_pdfs(pdf_parts)

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
    log.info("Wells Fargo resume book saved: %s (%.2f MB)", out_path, size_mb)
    if size_mb > max_mb:
        log.warning("Wells Fargo book (%.2f MB) exceeds max_pdf_mb=%.0f.", size_mb, max_mb)

    return WellsFargoBuildResult(
        success=True,
        output_path=out_path,
        junior=junior_result,
        senior=senior_result,
    )
