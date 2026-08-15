#!/usr/bin/env python3
"""
BLK Capital Management - Resume Book Builder
Reads member data from local Excel, downloads resumes from Google Drive,
and compiles into a single PDF resume book with year-based transition pages.
"""

import io
import os
import re
import sys
from pathlib import Path
from typing import Optional, Dict

import pandas as pd
from tqdm import tqdm
from pypdf import PdfReader, PdfWriter

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import Request

# ======= CONFIG ========
BASE_DIR = Path(__file__).parent

EXCEL_FILE     = BASE_DIR / "Member Registration _ Fall 2025 (Responses).xlsx"
TRANSITIONS_DIR = BASE_DIR / "Transitions"
CREDENTIALS_FILE = BASE_DIR / "..."
TOKEN_FILE     = BASE_DIR / "token.json"
OUTPUT_FILE    = BASE_DIR / "BLK_Capital_Management_Resume_Book.pdf"
# =======================

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DRIVE_FILE_ID_RE = re.compile(r"(?:/d/|id=)([a-zA-Z0-9_-]{10,})")


def authenticate() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def download_file_as_pdf(drive_service, file_id: str) -> Optional[bytes]:
    """Download a Drive file as PDF bytes (handles native PDF, Google Doc, and Word)."""
    try:
        meta = drive_service.files().get(fileId=file_id, fields="mimeType,name,trashed").execute()
        if meta.get("trashed"):
            return None
        mime = meta.get("mimeType", "")

        if mime == "application/pdf":
            req = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue()

        if mime == "application/vnd.google-apps.document":
            req = drive_service.files().export_media(fileId=file_id, mimeType="application/pdf")
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue()

        if mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ):
            copy_body = {"name": f"tmp-convert-{file_id}", "mimeType": "application/vnd.google-apps.document"}
            copied = drive_service.files().copy(fileId=file_id, body=copy_body).execute()
            tmp_id = copied["id"]
            try:
                req = drive_service.files().export_media(fileId=tmp_id, mimeType="application/pdf")
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return fh.getvalue()
            finally:
                drive_service.files().delete(fileId=tmp_id).execute()

    except Exception as e:
        print(f"  ERROR downloading {file_id}: {e}", file=sys.stderr)
    return None


def first_page_bytes(pdf_bytes: bytes) -> Optional[bytes]:
    """Extract only the first page from a PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as e:
        print(f"  ERROR extracting first page: {e}", file=sys.stderr)
    return None


def load_local_transitions(transitions_dir: Path) -> Dict[str, bytes]:
    """Load transition PDFs from local folder. Files named like '2026 Cover.pdf'."""
    transitions = {}
    for pdf_path in sorted(transitions_dir.glob("*.pdf")):
        # Extract year from filename (e.g. "2026 Cover.pdf" -> "2026")
        year_match = re.search(r"(20\d{2})", pdf_path.stem)
        if year_match:
            year_key = year_match.group(1)
            transitions[year_key] = pdf_path.read_bytes()
            print(f"  Loaded transition page for {year_key}: {pdf_path.name}")
    return transitions


def main():
    # --- Load Excel ---
    print("Reading Excel file...")
    df = pd.read_excel(EXCEL_FILE)
    df.columns = [str(c).strip() for c in df.columns]

    # Identify key columns
    col_lower = {c.lower(): c for c in df.columns}

    def find_col(*keywords):
        for k, real in col_lower.items():
            if all(w.lower() in k for w in keywords):
                return real
        for k, real in col_lower.items():
            if any(w.lower() in k for w in keywords):
                return real
        return None

    first_name_col = find_col("first name") or find_col("first")
    last_name_col  = find_col("last name")  or find_col("last")
    resume_col     = find_col("updated resume") or find_col("resume")
    grad_col       = find_col("graduation") or find_col("graduate") or find_col("when will you graduate")

    print(f"  First name column : {first_name_col}")
    print(f"  Last name column  : {last_name_col}")
    print(f"  Resume link column: {resume_col}")
    print(f"  Graduation column : {grad_col}")

    if not all([first_name_col, last_name_col, resume_col, grad_col]):
        sys.exit("ERROR: Could not detect all required columns. Check column names in Excel.")

    # --- Load local transition pages ---
    print("\nLoading local transition pages...")
    transitions = load_local_transitions(TRANSITIONS_DIR)

    # --- Authenticate with Google Drive ---
    print("\nAuthenticating with Google Drive...")
    creds = authenticate()
    drive_service = build("drive", "v3", credentials=creds)

    # --- Filter and sort members ---
    df[grad_col] = pd.to_numeric(df[grad_col], errors="coerce")
    members = df[df[resume_col].notna()].copy()
    members = members.sort_values(by=grad_col, ascending=True)  # Seniors first (2025 -> 2029)
    print(f"\nFound {len(members)} members with resume links.")

    # --- Download all resumes ---
    print(f"\nDownloading resumes from Google Drive...")
    resume_cache: Dict[int, bytes] = {}  # idx -> first-page PDF bytes

    for idx, row in tqdm(members.iterrows(), total=len(members), desc="Downloading"):
        link = str(row.get(resume_col, ""))
        m = DRIVE_FILE_ID_RE.search(link)
        if not m:
            name = f"{row.get(first_name_col, '')} {row.get(last_name_col, '')}"
            print(f"  SKIP {name}: no Drive file ID found in '{link}'")
            continue
        file_id = m.group(1)
        pdf_bytes = download_file_as_pdf(drive_service, file_id)
        if pdf_bytes:
            first_pg = first_page_bytes(pdf_bytes)
            if first_pg:
                resume_cache[idx] = first_pg
        else:
            name = f"{row.get(first_name_col, '')} {row.get(last_name_col, '')}"
            print(f"  SKIP {name}: download failed")

    print(f"\nSuccessfully downloaded {len(resume_cache)} resumes.")

    # --- Compile resume book ---
    print("\nCompiling resume book...")
    writer = PdfWriter()
    current_year = None
    added = 0

    for idx, row in members.iterrows():
        if idx not in resume_cache:
            continue

        grad_year = row[grad_col]
        year_str = str(int(grad_year)) if pd.notnull(grad_year) else "Unknown"

        # Insert transition page when year changes
        if year_str != current_year:
            if year_str in transitions:
                writer.add_page(PdfReader(io.BytesIO(transitions[year_str])).pages[0])
                print(f"  -> Added transition page for {year_str}")
            else:
                print(f"  -> No transition page found for year '{year_str}', skipping transition")
            current_year = year_str

        writer.add_page(PdfReader(io.BytesIO(resume_cache[idx])).pages[0])
        name = f"{row.get(first_name_col, '')} {row.get(last_name_col, '')}"
        print(f"  + {name} ({year_str})")
        added += 1

    if added == 0:
        sys.exit("No resumes were added. Check Drive permissions and resume links.")

    # --- Save output ---
    print(f"\nSaving resume book to: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "wb") as f:
        writer.write(f)

    total_pages = added + len([y for y in set(
        str(int(r[grad_col])) if pd.notnull(r[grad_col]) else "Unknown"
        for _, r in members[members.index.isin(resume_cache)].iterrows()
    ) if y in transitions])

    print(f"\nDone! Resume book saved.")
    print(f"  Resumes included : {added}")
    print(f"  Output file      : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
