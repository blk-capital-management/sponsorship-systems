"""Tests for normalize_resume_reference — the unified Drive reference parser."""

import pytest

from src.clients.drive_client import normalize_resume_reference, extract_file_id_from_link

# A realistic-looking fake file ID (33 chars, alphanumeric + dash/underscore)
FAKE_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"


class TestNormalizeResumeReference:

    def test_standard_drive_share_url(self):
        url = f"https://drive.google.com/file/d/{FAKE_ID}/view?usp=sharing"
        assert normalize_resume_reference(url) == FAKE_ID

    def test_drive_open_url(self):
        url = f"https://drive.google.com/open?id={FAKE_ID}"
        assert normalize_resume_reference(url) == FAKE_ID

    def test_drive_uc_export_url(self):
        url = f"https://drive.google.com/uc?id={FAKE_ID}&export=download"
        assert normalize_resume_reference(url) == FAKE_ID

    def test_forms_file_upload_url(self):
        # Google Forms file-upload links use the same /file/d/ structure
        url = f"https://drive.google.com/file/d/{FAKE_ID}/view"
        assert normalize_resume_reference(url) == FAKE_ID

    def test_bare_file_id(self):
        assert normalize_resume_reference(FAKE_ID) == FAKE_ID

    def test_bare_id_with_whitespace(self):
        assert normalize_resume_reference(f"  {FAKE_ID}  ") == FAKE_ID

    def test_invalid_returns_none(self):
        assert normalize_resume_reference("https://www.example.com/not-a-drive-link") is None

    def test_empty_string_returns_none(self):
        assert normalize_resume_reference("") is None

    def test_none_input_returns_none(self):
        assert normalize_resume_reference(None) is None  # type: ignore[arg-type]

    def test_short_id_not_matched_as_bare(self):
        # IDs shorter than 28 chars should not be treated as bare file IDs
        short = "abc123"
        assert normalize_resume_reference(short) is None

    def test_backward_compat_extract_file_id_from_link(self):
        """extract_file_id_from_link still works for standard URLs."""
        url = f"https://drive.google.com/file/d/{FAKE_ID}/view"
        assert extract_file_id_from_link(url) == FAKE_ID

    def test_extract_file_id_from_link_none_on_bare_id(self):
        """extract_file_id_from_link does NOT match bare IDs (by design — use normalize instead)."""
        assert extract_file_id_from_link(FAKE_ID) is None
