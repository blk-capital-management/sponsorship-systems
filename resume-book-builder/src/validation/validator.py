"""Pre-flight validation for the BLK Resume Book Builder.

Runs all checks against the loaded DataFrame and settings before any PDF is
downloaded or generated. Does NOT download resumes or build PDFs.

Checks performed:
  Config:
    - auth.mode is "oauth" or "service_account"
    - compression.per_resume_tier and final_book_tier are valid strings
    - max_pdf_mb is numeric and > 0

  Data source:
    - Excel file exists (when source = excel)
    - spreadsheet_id is non-empty (when source = sheets)
    - Spreadsheet/Excel can be loaded

  Column mapping:
    - All required fields (first_name, last_name, graduation_year, resume_link)
      resolve through column_mapping.yaml

  Per-row checks (only when DataFrame loaded successfully):
    - Missing first_name
    - Missing last_name
    - Missing graduation_year
    - Graduation year is numeric / can be coerced to int
    - Missing resume_link
    - Invalid Google Drive link (can't extract file ID)
    - Duplicate students (same first + last + grad year)

  Transition pages (when transition_pages_dir is configured):
    - Transition page PDF exists for each unique graduation year in the data

  Sponsors (when sponsors.yaml is non-empty):
    - Each sponsor's column exists in the loaded DataFrame
    - When mode=threshold, min_rating is set and > 0
    - Sponsors with no eligible students → warning (not error)

All findings are appended to an ErrorReportBuilder passed in by the caller.
The caller is responsible for writing the CSV.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from src.clients.drive_client import normalize_resume_reference
from src.loaders.column_resolver import REQUIRED_FIELDS, assert_required_fields
from src.processing.pdf_merger import load_transition_pages
from src.reporting.error_report import (
    ErrorReportBuilder,
    CAT_CONFIG,
    CAT_DATA_SOURCE,
    CAT_COLUMN_MAPPING,
    CAT_MISSING_FIELD,
    CAT_INVALID_LINK,
    CAT_DUPLICATE,
    CAT_GRAD_YEAR,
    CAT_TRANSITION_PAGE,
    CAT_SPONSOR,
)
from src.utils.logging import get_logger

log = get_logger("validation.validator")

VALID_COMPRESSION_TIERS = {"high_quality", "email_quality", "small_size"}
VALID_AUTH_MODES = {"oauth", "service_account"}


# ── Top-level entry point ─────────────────────────────────────────────────────

def run_validation(
    settings: dict,
    config_dir: Path,
    column_mapping: dict,
    sponsors_config: dict,
    reporter: ErrorReportBuilder,
) -> dict:
    """Run all pre-flight checks. Returns a stats dict for the run summary.

    Stats dict keys:
        total_rows, valid_rows, unique_grad_years, sponsor_columns_detected,
        data_source_type, df (the loaded DataFrame or None)
    """
    stats = {
        "total_rows": 0,
        "valid_rows": 0,
        "unique_grad_years": 0,
        "sponsor_columns_detected": 0,
        "data_source_type": settings.get("data", {}).get("source", "excel"),
        "df": None,
        "resolved": {},
    }

    _check_config(settings, reporter)
    df, resolved = _load_data(settings, config_dir, column_mapping, reporter)

    if df is None or df.empty:
        log.warning("DataFrame could not be loaded; skipping per-row and downstream checks.")
        return stats

    stats["df"] = df
    stats["resolved"] = resolved
    stats["total_rows"] = len(df)

    _check_column_mapping(resolved, reporter)

    # Per-row checks only if required columns resolved (otherwise every row would
    # fire redundant errors)
    missing_required = assert_required_fields(resolved)
    if not missing_required:
        valid_rows, unique_years = _check_rows(df, resolved, reporter)
        stats["valid_rows"] = valid_rows
        stats["unique_grad_years"] = len(unique_years)

        _check_transition_pages(settings, config_dir, unique_years, reporter)
    else:
        log.warning(
            "Skipping per-row checks because required fields are unresolved: %s",
            missing_required,
        )

    n_sponsor_cols = _check_sponsors(df, resolved, sponsors_config, reporter)
    stats["sponsor_columns_detected"] = n_sponsor_cols

    return stats


# ── Config checks ─────────────────────────────────────────────────────────────

def _check_config(settings: dict, reporter: ErrorReportBuilder) -> None:
    auth_mode = settings.get("auth", {}).get("mode", "oauth")
    if auth_mode not in VALID_AUTH_MODES:
        reporter.error(
            CAT_CONFIG,
            issue=f"auth.mode is '{auth_mode}'; must be 'oauth' or 'service_account'.",
            recommended_fix="Edit config/settings.yaml: set auth.mode to 'oauth' or 'service_account'.",
        )

    comp = settings.get("compression", {})
    for key in ("per_resume_tier", "final_book_tier"):
        tier = comp.get(key, "email_quality")
        if tier not in VALID_COMPRESSION_TIERS:
            reporter.error(
                CAT_CONFIG,
                issue=f"compression.{key} is '{tier}'; must be one of {sorted(VALID_COMPRESSION_TIERS)}.",
                recommended_fix=f"Edit config/settings.yaml: set compression.{key} to 'email_quality', 'high_quality', or 'small_size'.",
            )

    max_mb = comp.get("max_pdf_mb", 20)
    try:
        if float(max_mb) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        reporter.error(
            CAT_CONFIG,
            issue=f"compression.max_pdf_mb is '{max_mb}'; must be a positive number.",
            recommended_fix="Edit config/settings.yaml: set compression.max_pdf_mb to a positive number (e.g. 20).",
        )


# ── Data source loading ───────────────────────────────────────────────────────

def _load_data(
    settings: dict,
    config_dir: Path,
    column_mapping: dict,
    reporter: ErrorReportBuilder,
) -> tuple[Optional[pd.DataFrame], dict]:
    source = settings.get("data", {}).get("source", "excel")

    if source == "excel":
        return _load_excel(settings, config_dir, column_mapping, reporter)
    elif source == "sheets":
        return _load_sheets(settings, config_dir, column_mapping, reporter)
    else:
        reporter.error(
            CAT_DATA_SOURCE,
            issue=f"data.source is '{source}'; must be 'excel' or 'sheets'.",
            recommended_fix="Edit config/settings.yaml: set data.source to 'excel' or 'sheets'.",
        )
        return None, {}


def _load_excel(
    settings: dict,
    config_dir: Path,
    column_mapping: dict,
    reporter: ErrorReportBuilder,
) -> tuple[Optional[pd.DataFrame], dict]:
    from src.loaders.excel_loader import load_excel
    from src.utils.file_utils import resolve_path

    raw_path = settings.get("data", {}).get("excel_file", "")
    if not raw_path:
        reporter.error(
            CAT_DATA_SOURCE,
            issue="data.excel_file is not set in settings.yaml.",
            recommended_fix="Edit config/settings.yaml: set data.excel_file to the path of your Excel file.",
        )
        return None, {}

    excel_path = resolve_path(raw_path, config_dir.parent)
    if not excel_path.exists():
        reporter.error(
            CAT_DATA_SOURCE,
            issue=f"Excel file not found: {excel_path}",
            recommended_fix=(
                f"Place your Excel file at '{excel_path}', or update data.excel_file "
                "in config/settings.yaml to point to the correct path."
            ),
        )
        return None, {}

    sheet_name = settings.get("data", {}).get("sheet_name") or None

    try:
        df, resolved = load_excel(excel_path, column_mapping, sheet_name=sheet_name)
        return df, resolved
    except Exception as exc:
        reporter.error(
            CAT_DATA_SOURCE,
            issue=f"Could not read Excel file: {exc}",
            recommended_fix="Ensure the file is a valid .xlsx file and is not open in Excel.",
        )
        return None, {}


def _load_sheets(
    settings: dict,
    config_dir: Path,
    column_mapping: dict,
    reporter: ErrorReportBuilder,
) -> tuple[Optional[pd.DataFrame], dict]:
    spreadsheet_id = settings.get("data", {}).get("spreadsheet_id", "")
    sheet_name = settings.get("data", {}).get("sheet_name", "Form Responses 1")

    if not spreadsheet_id:
        reporter.error(
            CAT_DATA_SOURCE,
            issue="data.spreadsheet_id is not set in settings.yaml.",
            recommended_fix=(
                "Edit config/settings.yaml: paste your Google Sheet ID into data.spreadsheet_id. "
                "The Sheet ID is the long string in your Sheet URL between /d/ and /edit."
            ),
        )
        return None, {}

    try:
        from src.auth.google_auth import build_credentials
        from src.clients.sheets_client import SheetsClient
        from src.loaders.sheets_loader import load_sheet

        creds = build_credentials(settings, config_dir)
        client = SheetsClient(creds)
        df, resolved = load_sheet(client, spreadsheet_id, sheet_name, column_mapping)
        return df, resolved
    except Exception as exc:
        reporter.error(
            CAT_DATA_SOURCE,
            issue=f"Could not read Google Sheet: {exc}",
            recommended_fix=(
                "Check that your credentials are configured correctly and that the Sheet is "
                "shared with your Google account. Re-run to trigger the OAuth browser flow if needed."
            ),
        )
        return None, {}


# ── Column mapping checks ─────────────────────────────────────────────────────

def _check_column_mapping(resolved: dict, reporter: ErrorReportBuilder) -> None:
    for field_name in REQUIRED_FIELDS:
        if not resolved.get(field_name):
            reporter.error(
                CAT_COLUMN_MAPPING,
                field=field_name,
                issue=f"Required field '{field_name}' could not be matched to any column in the spreadsheet.",
                recommended_fix=(
                    f"Open config/column_mapping.yaml and add the exact column header for '{field_name}' "
                    "to its aliases list. Column names are case-insensitive."
                ),
            )


# ── Per-row checks ────────────────────────────────────────────────────────────

def _student_name(row: pd.Series, resolved: dict) -> str:
    first = row.get(resolved.get("first_name", ""), "")
    last = row.get(resolved.get("last_name", ""), "")
    name = f"{first} {last}".strip()
    return name if name else "(unknown)"


def _check_rows(
    df: pd.DataFrame,
    resolved: dict,
    reporter: ErrorReportBuilder,
) -> tuple[int, set]:
    """Check each row for missing fields, invalid links, duplicates, and bad grad years.

    Returns (valid_row_count, set_of_unique_grad_years).
    """
    col_first = resolved.get("first_name")
    col_last = resolved.get("last_name")
    col_year = resolved.get("graduation_year")
    col_link = resolved.get("resume_link")

    row_errors: list[int] = []  # 0-based row indices with at least one error
    seen_students: dict[tuple, int] = {}  # (first_lower, last_lower, year) → first row_number
    unique_years: set = set()

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-based + header row = +2
        has_error = False

        first = row.get(col_first, "").strip() if col_first else ""
        last = row.get(col_last, "").strip() if col_last else ""
        year_raw = row.get(col_year, "").strip() if col_year else ""
        link = row.get(col_link, "").strip() if col_link else ""
        name = f"{first} {last}".strip() or "(unknown)"

        # Missing first name
        if not first:
            reporter.error(
                CAT_MISSING_FIELD,
                row_number=row_num,
                student_name=name,
                graduation_year=year_raw,
                field="first_name",
                issue="First name is missing.",
                recommended_fix="Fill in the first name in the spreadsheet for this row.",
            )
            has_error = True

        # Missing last name
        if not last:
            reporter.error(
                CAT_MISSING_FIELD,
                row_number=row_num,
                student_name=name,
                graduation_year=year_raw,
                field="last_name",
                issue="Last name is missing.",
                recommended_fix="Fill in the last name in the spreadsheet for this row.",
            )
            has_error = True

        # Missing / invalid graduation year
        year_int: Optional[int] = None
        if not year_raw:
            reporter.error(
                CAT_MISSING_FIELD,
                row_number=row_num,
                student_name=name,
                graduation_year=year_raw,
                field="graduation_year",
                issue="Graduation year is missing.",
                recommended_fix="Fill in the graduation year (e.g. 2026) in the spreadsheet for this row.",
            )
            has_error = True
        else:
            try:
                year_int = int(float(year_raw))
                unique_years.add(year_int)
            except (ValueError, TypeError):
                reporter.error(
                    CAT_GRAD_YEAR,
                    row_number=row_num,
                    student_name=name,
                    graduation_year=year_raw,
                    field="graduation_year",
                    issue=f"Graduation year '{year_raw}' is not a valid year.",
                    recommended_fix="Update the graduation year to a 4-digit numeric year (e.g. 2026).",
                )
                has_error = True

        # Missing resume link
        if not link:
            reporter.error(
                CAT_MISSING_FIELD,
                row_number=row_num,
                student_name=name,
                graduation_year=year_raw,
                field="resume_link",
                issue="Resume link is missing.",
                recommended_fix="Ask the student to submit their Google Drive resume link.",
            )
            has_error = True
        else:
            # Invalid Drive link
            file_id = normalize_resume_reference(link)
            if not file_id:
                reporter.error(
                    CAT_INVALID_LINK,
                    row_number=row_num,
                    student_name=name,
                    graduation_year=year_raw,
                    field="resume_link",
                    issue=f"Could not extract a Google Drive file ID from: {link}",
                    recommended_fix=(
                        "Ask the student to provide a valid Google Drive share link. "
                        "It should look like: https://drive.google.com/file/d/FILE_ID/view"
                    ),
                )
                has_error = True

        # Duplicate detection (only when we have enough fields to identify a student)
        if first and last and year_int is not None:
            key = (first.lower(), last.lower(), year_int)
            if key in seen_students:
                first_row = seen_students[key]
                reporter.warning(
                    CAT_DUPLICATE,
                    row_number=row_num,
                    student_name=name,
                    graduation_year=year_raw,
                    issue=f"Duplicate student entry. First seen at row {first_row}.",
                    recommended_fix=(
                        f"Remove the duplicate row. Keep the most recent submission "
                        f"(row {row_num}) and delete row {first_row}, or vice versa."
                    ),
                )
                has_error = True
            else:
                seen_students[key] = row_num

        if not has_error:
            pass  # counted below

    # valid = rows with no errors
    # We track per-row errors by re-counting from the reporter
    # Simple approach: total - rows_that_had_at_least_one_error
    # We don't have a set of errored rows, so count based on reporter state before
    # this function vs after is impractical. Instead count directly:
    errored_rows = set()
    for rec in reporter.records:
        if rec.row_number is not None and rec.severity == "error":
            errored_rows.add(rec.row_number)

    valid_rows = len(df) - len(errored_rows)
    return valid_rows, unique_years


# ── Transition page checks ────────────────────────────────────────────────────

def _check_transition_pages(
    settings: dict,
    config_dir: Path,
    unique_years: set,
    reporter: ErrorReportBuilder,
) -> None:
    transitions_dir_raw = settings.get("input", {}).get("transition_pages_dir", "")
    if not transitions_dir_raw:
        return

    from src.utils.file_utils import resolve_path
    transitions_dir = resolve_path(transitions_dir_raw, config_dir.parent)

    if not transitions_dir.exists():
        reporter.warning(
            CAT_TRANSITION_PAGE,
            issue=f"Transition pages directory not found: {transitions_dir}",
            recommended_fix=(
                "Create the directory and add Canva-exported PDFs named like "
                "'2025 Transition.pdf', '2026 Transition.pdf', etc."
            ),
        )
        return

    transitions = load_transition_pages(transitions_dir)

    for year in sorted(unique_years):
        year_str = str(year)
        found = any(year_str in stem for stem in transitions)
        if not found:
            reporter.warning(
                CAT_TRANSITION_PAGE,
                graduation_year=year_str,
                issue=f"No transition page found for graduation year {year}.",
                recommended_fix=(
                    f"Add a PDF named like '{year} Transition.pdf' to {transitions_dir}. "
                    "Export it from Canva."
                ),
            )


# ── Sponsor checks ────────────────────────────────────────────────────────────

def _check_sponsors(
    df: pd.DataFrame,
    resolved: dict,
    sponsors_config: dict,
    reporter: ErrorReportBuilder,
) -> int:
    sponsors = sponsors_config.get("sponsors", [])
    if not sponsors:
        return 0

    df_cols_lower = {c.lower(): c for c in df.columns}
    col_year = resolved.get("graduation_year")
    found_count = 0

    for sponsor in sponsors:
        name = sponsor.get("name", "(unnamed sponsor)")
        column = sponsor.get("column", "")
        mode = sponsor.get("mode", "threshold")
        min_rating = sponsor.get("min_rating")

        if not column:
            reporter.error(
                CAT_SPONSOR,
                sponsor=name,
                issue=f"Sponsor '{name}' has no 'column' set in config/sponsors.yaml.",
                recommended_fix=(
                    f"Edit config/sponsors.yaml: add the exact column header for '{name}' "
                    "as the 'column' field."
                ),
            )
            continue

        actual_col = df_cols_lower.get(column.lower())
        if actual_col is None:
            reporter.error(
                CAT_SPONSOR,
                sponsor=name,
                issue=f"Sponsor column '{column}' for '{name}' not found in the spreadsheet.",
                recommended_fix=(
                    f"Open the spreadsheet and check the exact column header for '{name}'. "
                    "Update config/sponsors.yaml to match it exactly (case-insensitive)."
                ),
            )
            continue

        found_count += 1

        # Validate min_rating for threshold mode
        if mode == "threshold":
            if min_rating is None:
                reporter.error(
                    CAT_SPONSOR,
                    sponsor=name,
                    issue=f"Sponsor '{name}' uses mode=threshold but 'min_rating' is not set.",
                    recommended_fix=(
                        f"Edit config/sponsors.yaml: add 'min_rating: 4' (or your preferred "
                        "threshold) under the '{name}' entry."
                    ),
                )
            else:
                try:
                    min_rating_val = int(min_rating)
                    if min_rating_val <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    reporter.error(
                        CAT_SPONSOR,
                        sponsor=name,
                        issue=f"Sponsor '{name}' min_rating='{min_rating}' is not a positive integer.",
                        recommended_fix="Edit config/sponsors.yaml: set min_rating to a positive integer (e.g. 4).",
                    )

        # Check for eligible students — warning only
        eligible = _count_eligible(df, actual_col, mode, min_rating)
        if eligible == 0:
            reporter.warning(
                CAT_SPONSOR,
                sponsor=name,
                issue=f"Sponsor '{name}': no eligible students found in column '{actual_col}' (mode={mode}).",
                recommended_fix=(
                    "Verify that students have submitted ratings for this sponsor. "
                    "If mode=threshold, consider lowering min_rating."
                ),
            )

    return found_count


def _count_eligible(df: pd.DataFrame, column: str, mode: str, min_rating) -> int:
    col_data = df[column]
    if mode == "all_interested":
        return int((col_data.str.strip() != "").sum())
    elif mode == "threshold":
        count = 0
        try:
            threshold = int(min_rating) if min_rating is not None else 4
        except (TypeError, ValueError):
            threshold = 4
        for val in col_data:
            try:
                if int(float(str(val).strip())) >= threshold:
                    count += 1
            except (ValueError, TypeError):
                pass
        return count
    return 0
