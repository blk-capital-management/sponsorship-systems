"""Stage 3 — Build the general BLK resume book.

Structure of the output PDF:
  [cover page]                  (optional — loaded from input/cover_pages/)
  [transition page for year N]  (from input/transition_pages/)
  [resume 1 — page 1 only]
  [resume 2 — page 1 only]
  ...
  [transition page for year N+1]
  ...

Members are sorted by graduation year (ascending by default).
Cover page: if a file named "cover.pdf" (case-insensitive) is found in
input/cover_pages/ it is prepended. The legacy "Title Page.pdf" is also
accepted as a cover page if it lives in that directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

from src.auth.google_auth import build_credentials
from src.clients.drive_client import DriveClient
from src.loaders.column_resolver import REQUIRED_FIELDS
from src.processing.pdf_merger import (
    merge_pdfs,
    load_transition_pages,
    resolve_divider_for_year,
    get_divider_source_log,
)
from src.processing.resume_downloader import download_resume
from src.utils.file_utils import ensure_dir, write_bytes, file_size_mb
from src.utils.logging import get_logger

log = get_logger("builders.general_builder")


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class MemberResult:
    row_number: int
    student_name: str
    graduation_year: str
    success: bool
    error_type: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class GeneralBuildResult:
    success: bool
    output_path: Optional[Path] = None
    total_members: int = 0
    included_count: int = 0
    skipped_count: int = 0
    member_results: list[MemberResult] = field(default_factory=list)
    error_detail: Optional[str] = None


# ── Cover page loader ─────────────────────────────────────────────────────────

def _load_cover_page(settings: dict, config_dir: Path) -> Optional[bytes]:
    """Return bytes of the cover page PDF, or None if not found."""
    raw = settings.get("input", {}).get("cover_pages_dir", "")
    if not raw:
        return None

    from src.utils.file_utils import resolve_path
    cover_dir = resolve_path(raw, config_dir.parent)
    if not cover_dir.exists():
        log.warning("Cover pages directory not found: %s", cover_dir)
        return None

    # Acceptable filenames, in priority order
    candidates = ["cover.pdf", "title page.pdf", "title_page.pdf", "cover page.pdf"]
    for pdf_path in sorted(cover_dir.glob("*.pdf")):
        if pdf_path.name.lower() in candidates:
            try:
                data = pdf_path.read_bytes()
                log.info("Cover page loaded: %s", pdf_path.name)
                return data
            except Exception as exc:
                log.error("Could not read cover page '%s': %s", pdf_path.name, exc)
    log.info("No cover page found in %s (looked for: %s).", cover_dir, candidates)
    return None


# ── Main builder ──────────────────────────────────────────────────────────────

def build_general(
    settings: dict,
    config_dir: Path,
    df: pd.DataFrame,
    resolved: dict,
) -> GeneralBuildResult:
    """Download all member resumes and compile the general resume book.

    Args:
        settings: Parsed settings.yaml.
        config_dir: Directory containing settings.yaml.
        df: Member DataFrame (already loaded and validated).
        resolved: Internal field → actual column name map.

    Returns:
        GeneralBuildResult describing outcome and output path.
    """
    processing_cfg = settings.get("processing", {})
    first_page_only: bool = processing_cfg.get("first_page_only", True)
    sort_ascending: bool = (
        processing_cfg.get("grad_year_sort_order", "ascending").lower() != "descending"
    )

    output_cfg = settings.get("output", {})
    general_dir = Path(output_cfg.get("general_dir", "output/general"))
    keep_raw: bool = output_cfg.get("keep_raw", False)

    col_first = resolved.get("first_name")
    col_last = resolved.get("last_name")
    col_year = resolved.get("graduation_year")
    col_link = resolved.get("resume_link")

    # ── Sort DataFrame ────────────────────────────────────────────────────────
    working = df.copy()
    working["_year_int"] = pd.to_numeric(working[col_year], errors="coerce")
    working = working.sort_values("_year_int", ascending=sort_ascending, na_position="last")

    total_members = len(working)
    log.info("Building general resume book for %d members (sort=%s).",
             total_members, "asc" if sort_ascending else "desc")

    # ── Auth + Drive client ───────────────────────────────────────────────────
    try:
        creds = build_credentials(settings, config_dir)
        drive_client = DriveClient(creds)
    except Exception as exc:
        return GeneralBuildResult(
            success=False,
            total_members=total_members,
            error_detail=f"Google authentication failed: {exc}",
        )

    # ── Load assets ───────────────────────────────────────────────────────────
    cover_bytes = _load_cover_page(settings, config_dir)

    input_cfg = settings.get("input", {})
    transitions_raw = input_cfg.get("transition_pages_dir", "")
    transitions: dict[str, bytes] = {}
    if transitions_raw:
        from src.utils.file_utils import resolve_path
        transitions_dir = resolve_path(transitions_raw, config_dir.parent)
        transitions = load_transition_pages(transitions_dir)

    # ── Download loop ─────────────────────────────────────────────────────────
    # resume_cache: ordered list of (year_int, pdf_bytes)
    resume_cache: list[tuple[int, bytes]] = []
    member_results: list[MemberResult] = []

    for idx, row in tqdm(working.iterrows(), total=total_members, desc="Downloading resumes"):
        row_num = int(idx) + 2
        first = row.get(col_first, "").strip() if col_first else ""
        last = row.get(col_last, "").strip() if col_last else ""
        year_raw = row.get(col_year, "").strip() if col_year else ""
        link = row.get(col_link, "").strip() if col_link else ""
        name = f"{first} {last}".strip() or "(unknown)"

        year_int: Optional[int] = None
        try:
            year_int = int(float(year_raw)) if year_raw else None
        except (ValueError, TypeError):
            pass

        result = download_resume(drive_client, link, first_page_only=first_page_only)

        if result.success and result.pdf_bytes:
            resume_cache.append((year_int, result.pdf_bytes))
            member_results.append(MemberResult(
                row_number=row_num, student_name=name,
                graduation_year=year_raw, success=True,
            ))
            log.debug("  + %s (%s)", name, year_raw)
        else:
            member_results.append(MemberResult(
                row_number=row_num, student_name=name,
                graduation_year=year_raw, success=False,
                error_type=result.error_type,
                error_detail=result.error_detail,
            ))
            log.warning("  SKIP %s (%s): [%s] %s",
                        name, year_raw, result.error_type, result.error_detail)

    included_count = len(resume_cache)
    skipped_count = total_members - included_count
    log.info("Downloaded %d/%d resumes.", included_count, total_members)

    if included_count == 0:
        return GeneralBuildResult(
            success=False,
            total_members=total_members,
            included_count=0,
            skipped_count=skipped_count,
            member_results=member_results,
            error_detail="No resumes were successfully downloaded.",
        )

    # ── Assemble PDF ──────────────────────────────────────────────────────────
    pdf_parts: list[bytes] = []

    if cover_bytes:
        pdf_parts.append(cover_bytes)

    current_year: Optional[int] = None
    for year_int, pdf_bytes in resume_cache:
        if year_int != current_year:
            current_year = year_int
            if year_int is not None:
                divider, source = resolve_divider_for_year(transitions, year_int)
                if divider:
                    pdf_parts.append(divider)
                    log.debug("  -> Divider for %d [%s]", year_int, source)
        pdf_parts.append(pdf_bytes)

    divider_log = get_divider_source_log()
    generated_years = [y for y, s in divider_log.items() if s == "generated"]
    if generated_years:
        log.warning(
            "GENERATED_TRANSITION_PAGE_FALLBACK: auto-generated dividers for years %s. "
            "Add Canva PDFs to input/transition_pages/ to use branded dividers instead.",
            sorted(generated_years),
        )

    log.info("Merging %d PDF parts…", len(pdf_parts))
    merged = merge_pdfs(pdf_parts)

    # ── Compress ──────────────────────────────────────────────────────────────
    compression_cfg = settings.get("compression", {})
    final_tier = compression_cfg.get("final_book_tier", "email_quality")
    max_mb = float(compression_cfg.get("max_pdf_mb", 20))

    compressed = _compress(merged, final_tier, settings)
    final_bytes = compressed if compressed else merged

    # ── Write output ──────────────────────────────────────────────────────────
    ensure_dir(general_dir)
    out_path = general_dir / "BLK_Resume_Book.pdf"

    if keep_raw and compressed:
        raw_path = general_dir / "BLK_Resume_Book_raw.pdf"
        write_bytes(raw_path, merged)
        log.info("Raw (uncompressed) copy saved: %s", raw_path)

    write_bytes(out_path, final_bytes)

    size_mb = file_size_mb(out_path)
    log.info("General resume book saved: %s  (%.2f MB)", out_path, size_mb)

    if size_mb > max_mb:
        auto_downgrade = compression_cfg.get("auto_downgrade", False)
        tiers = ["high_quality", "email_quality", "small_size"]
        if auto_downgrade and final_tier in tiers:
            next_tier_idx = tiers.index(final_tier) + 1
            if next_tier_idx < len(tiers):
                next_tier = tiers[next_tier_idx]
                log.warning(
                    "Book (%.2f MB) exceeds max_pdf_mb=%.0f. "
                    "auto_downgrade=true — retrying with tier '%s'.",
                    size_mb, max_mb, next_tier,
                )
                retry = _compress(merged, next_tier, settings)
                if retry:
                    write_bytes(out_path, retry)
                    size_mb = file_size_mb(out_path)
                    log.info("Re-compressed to %.2f MB using tier '%s'.", size_mb, next_tier)
        else:
            log.warning(
                "Final book (%.2f MB) exceeds max_pdf_mb=%.0f. "
                "Consider enabling auto_downgrade or using a lower tier.",
                size_mb, max_mb,
            )

    return GeneralBuildResult(
        success=True,
        output_path=out_path,
        total_members=total_members,
        included_count=included_count,
        skipped_count=skipped_count,
        member_results=member_results,
    )


# ── Compression helper ────────────────────────────────────────────────────────

def _compress(pdf_bytes: bytes, tier: str, settings: dict) -> Optional[bytes]:
    """Attempt Ghostscript compression; return compressed bytes or None on failure."""
    compression_cfg = settings.get("compression", {})
    backend = compression_cfg.get("backend", "ghostscript")
    if backend != "ghostscript":
        log.warning("Compression backend '%s' not supported yet; skipping.", backend)
        return None
    try:
        from src.processing.pdf_compressor import compress_pdf_ghostscript
        return compress_pdf_ghostscript(pdf_bytes, tier)
    except Exception as exc:
        log.warning("PDF compression failed (%s); using uncompressed PDF. Error: %s", tier, exc)
        return None
