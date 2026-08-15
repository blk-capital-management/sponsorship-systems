"""Stage 4 — Build sponsor-specific resume books.

For each sponsor defined in config/sponsors.yaml:
  1. Filter members whose rating column meets the sponsor's threshold.
  2. Sort by graduation year (ascending by default — most senior first).
  3. Download + merge resumes (no transition pages; sponsors get a flat list).
  4. Compress and write to output/sponsor_books/<filename>.pdf.
  5. Optionally upload to Google Drive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

from src.auth.google_auth import build_credentials
from src.clients.drive_client import DriveClient
from src.processing.pdf_merger import merge_pdfs
from src.processing.resume_downloader import download_resume
from src.utils.file_utils import ensure_dir, write_bytes, file_size_mb, safe_stem
from src.utils.logging import get_logger

log = get_logger("builders.sponsor_builder")


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class SponsorBookResult:
    sponsor_name: str
    success: bool
    output_path: Optional[Path] = None
    drive_link: Optional[str] = None
    included_count: int = 0
    skipped_count: int = 0
    eligible_count: int = 0
    error_detail: Optional[str] = None


@dataclass
class SponsorBuildResult:
    success: bool
    sponsor_results: list[SponsorBookResult] = field(default_factory=list)
    error_detail: Optional[str] = None


# ── Eligibility helpers ───────────────────────────────────────────────────────

def _filter_eligible(df: pd.DataFrame, column: str, mode: str, min_rating) -> pd.DataFrame:
    """Return the subset of df whose sponsor column meets the eligibility criteria."""
    if mode == "all_interested":
        mask = df[column].str.strip() != ""
    elif mode == "threshold":
        threshold = 4
        try:
            threshold = int(min_rating) if min_rating is not None else 4
        except (TypeError, ValueError):
            threshold = 4

        def _meets(val: str) -> bool:
            try:
                return int(float(val.strip())) >= threshold
            except (ValueError, TypeError):
                return False

        mask = df[column].apply(_meets)
    else:
        log.warning("Unknown sponsor mode '%s'; including all non-empty.", mode)
        mask = df[column].str.strip() != ""

    return df[mask].copy()


# ── Per-sponsor book builder ──────────────────────────────────────────────────

def _build_one_sponsor(
    sponsor: dict,
    df: pd.DataFrame,
    resolved: dict,
    drive_client: DriveClient,
    settings: dict,
    sponsor_books_dir: Path,
    df_cols_lower: dict,
) -> SponsorBookResult:
    name = sponsor.get("name", "(unnamed)")
    column_key = sponsor.get("column", "")
    mode = sponsor.get("mode", "threshold")
    min_rating = sponsor.get("min_rating")
    output_filename = sponsor.get("output_filename") or f"{safe_stem(name)}_Resume_Book.pdf"

    processing_cfg = settings.get("processing", {})
    first_page_only: bool = processing_cfg.get("first_page_only", True)
    sort_ascending: bool = (
        processing_cfg.get("grad_year_sort_order", "ascending").lower() != "descending"
    )

    compression_cfg = settings.get("compression", {})
    final_tier = compression_cfg.get("final_book_tier", "email_quality")
    max_mb = float(compression_cfg.get("max_pdf_mb", 20))

    output_cfg = settings.get("output", {})
    drive_folder_id: str = output_cfg.get("drive_upload_folder_id", "")

    col_first = resolved.get("first_name")
    col_last = resolved.get("last_name")
    col_year = resolved.get("graduation_year")
    col_link = resolved.get("resume_link")

    # Resolve actual column (case-insensitive)
    actual_col = df_cols_lower.get(column_key.lower())
    if not actual_col:
        return SponsorBookResult(
            sponsor_name=name, success=False,
            error_detail=f"Column '{column_key}' not found in DataFrame.",
        )

    # Filter + sort
    eligible_df = _filter_eligible(df, actual_col, mode, min_rating)
    eligible_count = len(eligible_df)
    if eligible_count == 0:
        log.warning("Sponsor '%s': no eligible members; skipping.", name)
        return SponsorBookResult(
            sponsor_name=name, success=False, eligible_count=0,
            error_detail="No eligible members found.",
        )

    eligible_df["_year_int"] = pd.to_numeric(eligible_df[col_year], errors="coerce")
    eligible_df = eligible_df.sort_values("_year_int", ascending=sort_ascending, na_position="last")

    log.info("Sponsor '%s': %d eligible members.", name, eligible_count)

    # Download loop
    pdf_parts: list[bytes] = []
    skipped = 0

    for idx, row in tqdm(eligible_df.iterrows(), total=eligible_count, desc=f"  {name}"):
        first = row.get(col_first, "").strip() if col_first else ""
        last = row.get(col_last, "").strip() if col_last else ""
        year_raw = row.get(col_year, "").strip() if col_year else ""
        link = row.get(col_link, "").strip() if col_link else ""
        member_name = f"{first} {last}".strip() or "(unknown)"

        result = download_resume(drive_client, link, first_page_only=first_page_only)
        if result.success and result.pdf_bytes:
            pdf_parts.append(result.pdf_bytes)
            log.debug("    + %s (%s)", member_name, year_raw)
        else:
            skipped += 1
            log.warning("    SKIP %s (%s): [%s] %s",
                        member_name, year_raw, result.error_type, result.error_detail)

    included_count = len(pdf_parts)
    if included_count == 0:
        return SponsorBookResult(
            sponsor_name=name, success=False,
            eligible_count=eligible_count, included_count=0, skipped_count=skipped,
            error_detail="All downloads failed for this sponsor.",
        )

    # Merge
    merged = merge_pdfs(pdf_parts)

    # Compress
    compressed = _try_compress(merged, final_tier, settings)
    final_bytes = compressed if compressed else merged

    # Write to disk
    ensure_dir(sponsor_books_dir)
    out_path = sponsor_books_dir / output_filename
    write_bytes(out_path, final_bytes)
    size_mb = file_size_mb(out_path)
    log.info("  Saved: %s  (%.2f MB, %d resumes)", out_path.name, size_mb, included_count)

    if size_mb > max_mb:
        log.warning(
            "Sponsor book '%s' (%.2f MB) exceeds max_pdf_mb=%.0f.",
            name, size_mb, max_mb,
        )

    # Optional Drive upload
    drive_link: Optional[str] = None
    if drive_folder_id:
        try:
            meta = drive_client.upload_pdf(final_bytes, output_filename, drive_folder_id)
            if meta:
                drive_link = meta.get("webViewLink")
                log.info("  Uploaded to Drive: %s", drive_link)
        except Exception as exc:
            log.error("  Drive upload failed for '%s': %s", name, exc)

    return SponsorBookResult(
        sponsor_name=name,
        success=True,
        output_path=out_path,
        drive_link=drive_link,
        included_count=included_count,
        skipped_count=skipped,
        eligible_count=eligible_count,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def build_sponsors(
    settings: dict,
    config_dir: Path,
    df: pd.DataFrame,
    resolved: dict,
    sponsors_config: dict,
) -> SponsorBuildResult:
    """Build all sponsor-specific resume books.

    Args:
        settings: Parsed settings.yaml.
        config_dir: Directory containing settings.yaml.
        df: Member DataFrame (loaded and validated).
        resolved: Internal field → actual column name map.
        sponsors_config: Parsed sponsors.yaml.

    Returns:
        SponsorBuildResult with per-sponsor outcomes.
    """
    sponsors = sponsors_config.get("sponsors", [])
    if not sponsors:
        return SponsorBuildResult(
            success=False,
            error_detail="No sponsors defined in config/sponsors.yaml.",
        )

    output_cfg = settings.get("output", {})
    sponsor_books_dir = Path(output_cfg.get("sponsor_books_dir", "output/sponsor_books"))

    # Pre-build lowercase column lookup once
    df_cols_lower = {c.lower(): c for c in df.columns}

    try:
        creds = build_credentials(settings, config_dir)
        drive_client = DriveClient(creds)
    except Exception as exc:
        return SponsorBuildResult(
            success=False,
            error_detail=f"Google authentication failed: {exc}",
        )

    sponsor_results: list[SponsorBookResult] = []
    for sponsor in sponsors:
        result = _build_one_sponsor(
            sponsor=sponsor,
            df=df,
            resolved=resolved,
            drive_client=drive_client,
            settings=settings,
            sponsor_books_dir=sponsor_books_dir,
            df_cols_lower=df_cols_lower,
        )
        sponsor_results.append(result)

    successful = sum(1 for r in sponsor_results if r.success)
    log.info(
        "Sponsor build complete: %d/%d books generated.",
        successful, len(sponsors),
    )

    return SponsorBuildResult(
        success=successful > 0,
        sponsor_results=sponsor_results,
    )


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
