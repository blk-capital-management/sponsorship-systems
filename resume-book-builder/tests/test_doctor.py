"""Tests for the doctor setup-check command."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from src.doctor.checks import (
    check_python_version,
    check_ghostscript,
    check_required_folders,
    check_config_files,
    check_data_source,
    check_cover_page,
    check_transition_pages,
    run_all_checks,
)


def test_python_version_pass():
    result = check_python_version()
    # We're running on 3.10+ (required to even import the tool)
    assert result.status == "PASS"


def test_ghostscript_present(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gs" if x == "gs" else None)
    result = check_ghostscript()
    assert result.status == "PASS"
    assert "/usr/bin/gs" in result.detail


def test_ghostscript_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda x: None)
    result = check_ghostscript()
    assert result.status == "WARNING"
    assert result.fix != ""


def test_required_folders_all_missing(tmp_path):
    results = check_required_folders(tmp_path)
    statuses = {r.check: r.status for r in results}
    # All folders are missing → all WARNING
    assert all(s == "WARNING" for s in statuses.values())


def test_required_folders_all_present(tmp_path):
    dirs = [
        "input", "input/cover_pages", "input/transition_pages",
        "output", "output/general", "output/sponsor_books", "output/reports",
    ]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    results = check_required_folders(tmp_path)
    assert all(r.status == "PASS" for r in results)


def test_config_files_missing(tmp_path):
    results = check_config_files(tmp_path)
    assert all(r.status == "ERROR" for r in results)


def test_config_files_present(tmp_path):
    for name in ("settings.yaml", "sponsors.yaml", "column_mapping.yaml"):
        (tmp_path / name).write_text("key: value\n")
    results = check_config_files(tmp_path)
    assert all(r.status == "PASS" for r in results)


def test_config_files_bad_yaml(tmp_path):
    (tmp_path / "settings.yaml").write_text("key: [\n")   # invalid YAML
    (tmp_path / "sponsors.yaml").write_text("ok: true\n")
    (tmp_path / "column_mapping.yaml").write_text("ok: true\n")
    by_check = {r.check: r for r in check_config_files(tmp_path)}
    assert by_check["config:settings.yaml"].status == "ERROR"
    assert by_check["config:sponsors.yaml"].status == "PASS"


def test_data_source_excel_missing_path(tmp_path):
    settings = {"data": {"source": "excel", "excel_file": ""}}
    results = check_data_source(settings, tmp_path)
    assert any(r.status == "ERROR" for r in results)


def test_data_source_excel_file_not_found(tmp_path):
    settings = {"data": {"source": "excel", "excel_file": "input/data.xlsx"}}
    results = check_data_source(settings, tmp_path)
    # File doesn't exist → WARNING (not ERROR, so build can still be attempted in dev mode)
    assert any(r.status == "WARNING" for r in results)


def test_data_source_excel_file_exists(tmp_path):
    # check_data_source resolves excel_file relative to config_dir.parent (project root).
    # Simulate: project_root/input/data.xlsx, config_dir=project_root/config
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    xlsx = tmp_path / "input" / "data.xlsx"
    xlsx.parent.mkdir(parents=True)
    xlsx.write_bytes(b"fake")
    settings = {"data": {"source": "excel", "excel_file": "input/data.xlsx"}}
    results = check_data_source(settings, config_dir)
    assert any(r.status == "PASS" for r in results)


def test_data_source_sheets_no_id(tmp_path):
    settings = {"data": {"source": "sheets", "spreadsheet_id": ""}}
    results = check_data_source(settings, tmp_path)
    assert any(r.status == "ERROR" for r in results)


def test_data_source_sheets_with_id(tmp_path):
    settings = {"data": {"source": "sheets", "spreadsheet_id": "abc123XYZ"}}
    results = check_data_source(settings, tmp_path)
    assert any(r.status == "PASS" for r in results)


def test_cover_page_dir_missing(tmp_path):
    settings = {"input": {"cover_pages_dir": "input/cover_pages"}}
    result = check_cover_page(settings, tmp_path)
    assert result.status == "WARNING"


def test_cover_page_found(tmp_path):
    # check_cover_page resolves cover_pages_dir relative to config_dir.parent
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cover_dir = tmp_path / "input" / "cover_pages"
    cover_dir.mkdir(parents=True)
    (cover_dir / "cover.pdf").write_bytes(b"%PDF-1.4")
    settings = {"input": {"cover_pages_dir": "input/cover_pages"}}
    result = check_cover_page(settings, config_dir)
    assert result.status == "PASS"


def test_transition_pages_dir_missing(tmp_path):
    settings = {"input": {"transition_pages_dir": "input/transition_pages"}}
    result = check_transition_pages(settings, tmp_path)
    assert result.status == "WARNING"


def test_transition_pages_found(tmp_path):
    # check_transition_pages resolves transition_pages_dir relative to config_dir.parent
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    td = tmp_path / "input" / "transition_pages"
    td.mkdir(parents=True)
    (td / "2026 Transition.pdf").write_bytes(b"%PDF-1.4")
    settings = {"input": {"transition_pages_dir": "input/transition_pages"}}
    result = check_transition_pages(settings, config_dir)
    assert result.status == "PASS"


def test_run_all_checks_no_crash(tmp_path):
    """run_all_checks must complete without raising even when nothing is configured."""
    settings = {
        "auth": {"mode": "oauth", "credentials_file": "../credentials.json", "token_file": "../token.json"},
        "data": {"source": "excel", "excel_file": ""},
        "input": {"cover_pages_dir": "", "transition_pages_dir": ""},
        "output": {"reports_dir": str(tmp_path / "output/reports")},
        "compression": {},
    }
    # config_dir is tmp_path itself; project root is its parent
    results = run_all_checks(settings, tmp_path)
    assert isinstance(results, list)
    assert len(results) > 0
