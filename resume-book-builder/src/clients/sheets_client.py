"""Google Sheets API client wrapper.

Provides:
- build_sheets_service()   — authenticated Sheets API service
- SheetsClient             — read sheet data to a raw list-of-rows

Lifted and cleaned up from legacy/custom_sponsor_resume_book_builder.py L75-L97.
"""

import pandas as pd
from googleapiclient.discovery import build

from src.utils.logging import get_logger

log = get_logger("clients.sheets_client")


def build_sheets_service(credentials):
    """Return an authenticated Sheets API v4 service object."""
    return build("sheets", "v4", credentials=credentials)


class SheetsClient:
    """Thin wrapper for reading Google Sheets data."""

    def __init__(self, credentials):
        self.service = build_sheets_service(credentials)

    def read_to_dataframe(self, spreadsheet_id: str, sheet_name: str) -> pd.DataFrame:
        """Read a sheet tab and return a pandas DataFrame.

        The first row of the sheet is used as column headers.

        Args:
            spreadsheet_id: The Sheet ID from the URL (/spreadsheets/d/<ID>/).
            sheet_name: The tab name (e.g. "Form Responses 1").

        Returns:
            DataFrame with string columns. Empty cells become empty strings.

        Raises:
            Exception: propagates googleapiclient errors so callers can report them.
        """
        log.info("Reading sheet '%s' from spreadsheet %s…", sheet_name, spreadsheet_id)
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=sheet_name)
            .execute()
        )
        rows = result.get("values", [])
        if not rows:
            log.warning("Sheet '%s' returned no data.", sheet_name)
            return pd.DataFrame()

        headers = rows[0]
        data_rows = rows[1:]

        # Pad short rows so every row has the same width as the header
        padded = [row + [""] * (len(headers) - len(row)) for row in data_rows]

        df = pd.DataFrame(padded, columns=headers)
        log.info("Sheet loaded: %d rows × %d columns.", len(df), len(df.columns))
        return df
