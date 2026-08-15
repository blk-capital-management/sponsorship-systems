"""Setup-check logic for the `doctor` command.

Each check returns a DoctorResult with status PASS / WARNING / ERROR and
a fix message the chair can act on immediately.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Status = Literal["PASS", "WARNING", "ERROR"]

REQUIRED_DIRS = [
    "input",
    "input/cover_pages",
    "input/transition_pages",
    "output",
    "output/general",
    "output/sponsor_books",
    "output/reports",
]

REQUIRED_PACKAGES = [
    "yaml",           # PyYAML
    "pypdf",
    "pandas",
    "openpyxl",
    "tqdm",
    "dotenv",         # python-dotenv
]

OPTIONAL_PACKAGES = {
    "pikepdf": "Python-based PDF compression fallback (install: pip install pikepdf)",
}


@dataclass
class DoctorResult:
    check: str
    status: Status
    detail: str
    fix: str = ""


def _pass(check: str, detail: str) -> DoctorResult:
    return DoctorResult(check=check, status="PASS", detail=detail)


def _warn(check: str, detail: str, fix: str = "") -> DoctorResult:
    return DoctorResult(check=check, status="WARNING", detail=detail, fix=fix)


def _error(check: str, detail: str, fix: str = "") -> DoctorResult:
    return DoctorResult(check=check, status="ERROR", detail=detail, fix=fix)


# ── Individual checks ─────────────────────────────────────────────────────────

def check_python_version() -> DoctorResult:
    v = sys.version_info
    if v >= (3, 10):
        return _pass("python_version", f"Python {v.major}.{v.minor}.{v.micro}")
    return _error(
        "python_version",
        f"Python {v.major}.{v.minor}.{v.micro} detected.",
        "Upgrade to Python 3.10 or later: https://www.python.org/downloads/",
    )


def check_required_packages() -> list[DoctorResult]:
    results = []
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append(_pass(f"package:{pkg}", f"{pkg} importable"))
        except ImportError:
            results.append(_error(
                f"package:{pkg}",
                f"Required package '{pkg}' not installed.",
                f"Run: pip install -r requirements.txt",
            ))
    return results


def check_optional_packages() -> list[DoctorResult]:
    results = []
    for pkg, description in OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(pkg)
            results.append(_pass(f"optional_package:{pkg}", f"{pkg} available"))
        except ImportError:
            results.append(_warn(
                f"optional_package:{pkg}",
                f"Optional package '{pkg}' not installed. {description}",
                f"pip install {pkg}",
            ))
    return results


def check_ghostscript() -> DoctorResult:
    for candidate in ("gs", "gswin64c", "gswin32c"):
        path = shutil.which(candidate)
        if path:
            return _pass("ghostscript", f"Found at {path}")
    return _warn(
        "ghostscript",
        "Ghostscript not found on PATH. PDF compression will use Python fallback (lower quality).",
        "macOS: brew install ghostscript  |  Linux: sudo apt install ghostscript",
    )


def check_required_folders(project_root: Path) -> list[DoctorResult]:
    results = []
    for rel in REQUIRED_DIRS:
        p = project_root / rel
        if p.exists():
            results.append(_pass(f"folder:{rel}", f"{p} exists"))
        else:
            results.append(_warn(
                f"folder:{rel}",
                f"Folder missing: {p}",
                f"mkdir -p {p}",
            ))
    return results


def check_config_files(config_dir: Path) -> list[DoctorResult]:
    results = []
    for name in ("settings.yaml", "sponsors.yaml", "column_mapping.yaml"):
        p = config_dir / name
        if not p.exists():
            results.append(_error(
                f"config:{name}",
                f"Config file missing: {p}",
                f"Copy the template from the repo and place it at {p}",
            ))
            continue
        try:
            with p.open() as f:
                parsed = yaml.safe_load(f)
            if parsed is None:
                results.append(_warn(
                    f"config:{name}",
                    f"{name} exists but is empty.",
                    f"Edit {p} and fill in the required fields.",
                ))
            else:
                results.append(_pass(f"config:{name}", f"{name} parsed successfully"))
        except yaml.YAMLError as exc:
            results.append(_error(
                f"config:{name}",
                f"{name} has a YAML syntax error: {exc}",
                f"Open {p} and fix the indentation/syntax error.",
            ))
    return results


def check_google_credentials(settings: dict, config_dir: Path) -> list[DoctorResult]:
    results = []
    auth = settings.get("auth", {})
    mode = auth.get("mode", "oauth")

    creds_path_raw = auth.get("credentials_file", "../credentials.json")
    creds_path = (config_dir / creds_path_raw).resolve()

    if creds_path.exists():
        results.append(_pass("google_credentials_file", f"credentials.json found at {creds_path}"))
    else:
        results.append(_warn(
            "google_credentials_file",
            f"Google credentials file not found: {creds_path}",
            "Download credentials.json from Google Cloud Console → APIs & Services → Credentials. "
            "Place it at the project root. See INSTRUCTIONS.md §Google Setup.",
        ))

    if mode == "oauth":
        token_path_raw = auth.get("token_file", "../token.json")
        token_path = (config_dir / token_path_raw).resolve()
        if token_path.exists():
            results.append(_pass("google_token_file", f"token.json cached at {token_path}"))
        else:
            results.append(_warn(
                "google_token_file",
                "token.json not present (OAuth token not yet cached).",
                "Run any build command for the first time — a browser window will open for Google login.",
            ))
    elif mode == "service_account":
        sa_file = auth.get("service_account_file") or ""
        if not sa_file:
            results.append(_error(
                "service_account_file",
                "auth.mode=service_account but GOOGLE_SERVICE_ACCOUNT_FILE is not set in .env.",
                "Add GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service_account.json to your .env file.",
            ))
        else:
            sa_path = Path(sa_file)
            if sa_path.exists():
                results.append(_pass("service_account_file", f"Service account file found: {sa_path}"))
            else:
                results.append(_error(
                    "service_account_file",
                    f"Service account file not found: {sa_path}",
                    "Ensure the path in GOOGLE_SERVICE_ACCOUNT_FILE points to a valid JSON key file.",
                ))
    return results


def check_data_source(settings: dict, config_dir: Path) -> list[DoctorResult]:
    results = []
    source = settings.get("data", {}).get("source", "excel")

    if source == "excel":
        excel_raw = settings.get("data", {}).get("excel_file", "")
        if not excel_raw:
            results.append(_error(
                "data_source:excel_file",
                "data.excel_file is not set in settings.yaml.",
                "Edit config/settings.yaml: set data.excel_file to the path of your .xlsx file.",
            ))
        else:
            excel_path = (config_dir.parent / excel_raw).resolve()
            if excel_path.exists():
                results.append(_pass("data_source:excel_file", f"Excel file found: {excel_path}"))
            else:
                results.append(_warn(
                    "data_source:excel_file",
                    f"Excel file not found at configured path: {excel_path}",
                    f"Place your .xlsx export at that path, or update data.excel_file in settings.yaml.",
                ))
    elif source == "sheets":
        sid = settings.get("data", {}).get("spreadsheet_id", "")
        if not sid:
            results.append(_error(
                "data_source:spreadsheet_id",
                "data.spreadsheet_id is not set in settings.yaml.",
                "Edit config/settings.yaml: paste your Google Sheet ID into data.spreadsheet_id.",
            ))
        else:
            results.append(_pass("data_source:spreadsheet_id", f"spreadsheet_id is set: {sid[:12]}…"))
    else:
        results.append(_error(
            "data_source:mode",
            f"data.source is '{source}'; must be 'excel' or 'sheets'.",
            "Edit config/settings.yaml: set data.source to 'excel' or 'sheets'.",
        ))
    return results


def check_cover_page(settings: dict, config_dir: Path) -> DoctorResult:
    raw = settings.get("input", {}).get("cover_pages_dir", "")
    if not raw:
        return _warn(
            "cover_page",
            "input.cover_pages_dir is not set — no cover page will be added.",
            "Set input.cover_pages_dir in settings.yaml and place cover.pdf inside it.",
        )
    cover_dir = (config_dir.parent / raw).resolve()
    candidates = ["cover.pdf", "title page.pdf", "title_page.pdf", "cover page.pdf"]
    if cover_dir.exists():
        for f in cover_dir.glob("*.pdf"):
            if f.name.lower() in candidates:
                return _pass("cover_page", f"Cover page found: {f.name}")
        return _warn(
            "cover_page",
            f"Cover pages directory exists but no recognised cover PDF found in {cover_dir}.",
            f"Add a PDF named 'cover.pdf' to {cover_dir}. Export it from Canva.",
        )
    return _warn(
        "cover_page",
        f"Cover pages directory not found: {cover_dir}",
        f"Create {cover_dir} and place cover.pdf inside it.",
    )


def check_transition_pages(settings: dict, config_dir: Path) -> DoctorResult:
    raw = settings.get("input", {}).get("transition_pages_dir", "")
    if not raw:
        return _warn(
            "transition_pages",
            "input.transition_pages_dir is not set — divider pages will be auto-generated.",
            "Optionally set input.transition_pages_dir and supply Canva-exported PDFs.",
        )
    trans_dir = (config_dir.parent / raw).resolve()
    if not trans_dir.exists():
        return _warn(
            "transition_pages",
            f"Transition pages directory not found: {trans_dir} — dividers will be auto-generated.",
            f"Create {trans_dir} and add Canva-exported PDFs named like '2025 Transition.pdf'.",
        )
    pdfs = list(trans_dir.glob("*.pdf"))
    if not pdfs:
        return _warn(
            "transition_pages",
            f"Transition pages directory is empty: {trans_dir} — dividers will be auto-generated.",
            "Export divider PDFs from Canva and place them here.",
        )
    return _pass("transition_pages", f"{len(pdfs)} transition PDF(s) found in {trans_dir}")


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_all_checks(settings: dict, config_dir: Path) -> list[DoctorResult]:
    project_root = config_dir.parent
    results: list[DoctorResult] = []

    results.append(check_python_version())
    results.extend(check_required_packages())
    results.extend(check_optional_packages())
    results.append(check_ghostscript())
    results.extend(check_required_folders(project_root))
    results.extend(check_config_files(config_dir))
    results.extend(check_google_credentials(settings, config_dir))
    results.extend(check_data_source(settings, config_dir))
    results.append(check_cover_page(settings, config_dir))
    results.append(check_transition_pages(settings, config_dir))

    return results
