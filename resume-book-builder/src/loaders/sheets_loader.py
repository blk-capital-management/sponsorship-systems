"""Load member data from a Google Sheet.

Returns a normalized pandas DataFrame with resolved column names.
"""

from typing import Optional

import pandas as pd

from src.clients.sheets_client import SheetsClient
from src.loaders.column_resolver import resolve_columns, assert_required_fields
from src.utils.logging import get_logger

log = get_logger("loaders.sheets_loader")


def load_sheet(
    sheets_client: SheetsClient,
    spreadsheet_id: str,
    sheet_name: str,
    column_mapping: dict,
) -> tuple[pd.DataFrame, dict[str, Optional[str]]]:
    """Read a Google Sheet tab and resolve internal field → actual column mappings.

    Args:
        sheets_client: Authenticated SheetsClient instance.
        spreadsheet_id: Google Sheet ID.
        sheet_name: Tab name.
        column_mapping: Parsed column_mapping.yaml dict.

    Returns:
        Tuple of (DataFrame with original columns, resolved column map).
    """
    df = sheets_client.read_to_dataframe(spreadsheet_id, sheet_name)

    if df.empty:
        log.warning("Sheet '%s' returned an empty DataFrame.", sheet_name)
        return df, {}

    # Normalise all cells to stripped strings
    df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)
    df.fillna("", inplace=True)

    resolved = resolve_columns(df.columns.tolist(), column_mapping)
    missing = assert_required_fields(resolved)
    if missing:
        log.warning(
            "Could not resolve required field(s): %s. "
            "Update config/column_mapping.yaml with the correct column aliases.",
            missing,
        )

    return df, resolved
