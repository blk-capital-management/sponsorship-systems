"""Load member data from a local Excel (.xlsx) file.

Returns a normalized pandas DataFrame with resolved column names.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from src.loaders.column_resolver import resolve_columns, assert_required_fields
from src.utils.logging import get_logger

log = get_logger("loaders.excel_loader")


def load_excel(
    excel_path: Path,
    column_mapping: dict,
    sheet_name: Optional[str] = None,
) -> tuple[pd.DataFrame, dict[str, Optional[str]]]:
    """Read the Excel file and resolve internal field → actual column mappings.

    Args:
        excel_path: Path to the .xlsx file.
        column_mapping: Parsed column_mapping.yaml dict.
        sheet_name: Tab to read. Workbooks with multiple tabs default to the
                    first tab when this is None, which may not be the tab
                    with clean headers.

    Returns:
        Tuple of (DataFrame with original columns, resolved column map).

    Raises:
        FileNotFoundError: if the Excel file doesn't exist.
        ValueError: if required fields can't be resolved (reported, not raised here —
                    validation stage handles that).
    """
    if not excel_path.exists():
        raise FileNotFoundError(
            f"Excel file not found: {excel_path}\n"
            "Update data.excel_file in config/settings.yaml."
        )

    log.info("Loading Excel file: %s (sheet=%s)", excel_path, sheet_name or "<first tab>")
    df = pd.read_excel(excel_path, sheet_name=sheet_name or 0, dtype=str)
    df.fillna("", inplace=True)

    # Strip leading/trailing whitespace from all string cells
    df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)

    log.info("Excel loaded: %d rows × %d columns.", len(df), len(df.columns))
    log.debug("Columns: %s", df.columns.tolist())

    resolved = resolve_columns(df.columns.tolist(), column_mapping)
    missing = assert_required_fields(resolved)
    if missing:
        log.warning(
            "Could not resolve required field(s): %s. "
            "Update config/column_mapping.yaml with the correct column aliases.",
            missing,
        )

    return df, resolved
