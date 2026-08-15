"""Backward-compatibility tests for Excel/Sheets config loading."""

import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import MagicMock, patch


SAMPLE_COLUMN_MAPPING = {
    "fields": {
        "first_name": ["First Name", "first name"],
        "last_name": ["Last Name", "last name"],
        "graduation_year": ["Graduation Year", "grad year"],
        "resume_link": [
            "Updated Resume", "Resume Link", "Resume Upload", "Upload Your Resume",
        ],
    }
}


class TestColumnResolver:

    def test_resolves_standard_columns(self):
        from src.loaders.column_resolver import resolve_columns
        cols = ["First Name", "Last Name", "Graduation Year", "Updated Resume"]
        resolved = resolve_columns(cols, SAMPLE_COLUMN_MAPPING)
        assert resolved["first_name"] == "First Name"
        assert resolved["last_name"] == "Last Name"
        assert resolved["graduation_year"] == "Graduation Year"
        assert resolved["resume_link"] == "Updated Resume"

    def test_resolves_upload_aliases(self):
        from src.loaders.column_resolver import resolve_columns
        cols = ["First Name", "Last Name", "Graduation Year", "Resume Upload"]
        resolved = resolve_columns(cols, SAMPLE_COLUMN_MAPPING)
        assert resolved["resume_link"] == "Resume Upload"

    def test_resolves_upload_your_resume_alias(self):
        from src.loaders.column_resolver import resolve_columns
        cols = ["First Name", "Last Name", "Graduation Year", "Upload Your Resume"]
        resolved = resolve_columns(cols, SAMPLE_COLUMN_MAPPING)
        assert resolved["resume_link"] == "Upload Your Resume"

    def test_case_insensitive_match(self):
        from src.loaders.column_resolver import resolve_columns
        cols = ["first name", "last name", "graduation year", "resume link"]
        resolved = resolve_columns(cols, SAMPLE_COLUMN_MAPPING)
        assert resolved["first_name"] == "first name"

    def test_unresolved_returns_none(self):
        from src.loaders.column_resolver import resolve_columns
        resolved = resolve_columns(["Other Column"], SAMPLE_COLUMN_MAPPING)
        assert resolved.get("resume_link") is None

    def test_assert_required_fields_all_present(self):
        from src.loaders.column_resolver import assert_required_fields
        resolved = {
            "first_name": "First Name",
            "last_name": "Last Name",
            "graduation_year": "Graduation Year",
            "resume_link": "Updated Resume",
        }
        assert assert_required_fields(resolved) == []

    def test_assert_required_fields_missing(self):
        from src.loaders.column_resolver import assert_required_fields
        resolved = {
            "first_name": "First Name",
            "last_name": "Last Name",
            "graduation_year": None,
            "resume_link": None,
        }
        missing = assert_required_fields(resolved)
        assert "graduation_year" in missing
        assert "resume_link" in missing


class TestExcelLoaderBackwardCompat:

    def test_load_excel_returns_dataframe(self, tmp_path):
        """Excel loader still produces a DataFrame and resolved map."""
        # Create a minimal xlsx
        df_in = pd.DataFrame({
            "First Name": ["Alice"],
            "Last Name": ["Smith"],
            "Graduation Year": [2026],
            "Updated Resume": ["https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view"],
        })
        xlsx_path = tmp_path / "data.xlsx"
        df_in.to_excel(xlsx_path, index=False)

        from src.loaders.excel_loader import load_excel
        df_out, resolved = load_excel(xlsx_path, SAMPLE_COLUMN_MAPPING)

        assert len(df_out) == 1
        assert resolved["first_name"] == "First Name"
        assert resolved["resume_link"] == "Updated Resume"

    def test_load_excel_with_upload_column(self, tmp_path):
        """Upload column alias resolves correctly."""
        df_in = pd.DataFrame({
            "First Name": ["Bob"],
            "Last Name": ["Jones"],
            "Graduation Year": [2027],
            "Resume Upload": ["https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view"],
        })
        xlsx_path = tmp_path / "data2.xlsx"
        df_in.to_excel(xlsx_path, index=False)

        from src.loaders.excel_loader import load_excel
        df_out, resolved = load_excel(xlsx_path, SAMPLE_COLUMN_MAPPING)

        assert resolved["resume_link"] == "Resume Upload"
