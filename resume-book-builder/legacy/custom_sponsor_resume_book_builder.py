#!/usr/bin/env python3
"""
Resume Book Builder

Requirements:
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib pandas pypdf tqdm

Before running:
 - Create OAuth credentials JSON (you said you already did). Save path to CREDENTIALS_FILE.
 - Set SPREADSHEET_ID to your Google Sheet ID.
 - Set SHEET_NAME to the tab name (e.g. "Form Responses 1" or whatever).
 - Set OUTPUT_FOLDER_ID to the Drive folder ID where books should be uploaded.

This script will:
 - Read the sheet
 - Detect "Updated Resume" (drive links), "Graduation Year", names, and company rating columns
 - For each company with ratings, select rating >= 4, sort by grad year desc
 - Download/convert resumes and take only the first page of each resume
 - Merge into a single PDF for that company and upload to Drive
"""

import io
import os
import re
import tempfile
import sys
from typing import Optional, Tuple, List, Dict
from pprint import pprint

import pandas as pd
from tqdm import tqdm
from pypdf import PdfReader, PdfWriter

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ======= CONFIG - EDIT THESE ========
SPREADSHEET_ID = "1KVA-46MdbulU9hhZw0TmTM72QvrQOuzA39uxwNkJcl4"
SHEET_NAME = "Form Responses 1"  # change to your sheet/tab name
CREDENTIALS_FILE = "C:\\Users\\M\\OneDrive - softromic\\Documents\\BLK Capital Management\\Sponsor Resume Books\\client_secret_900585995866-affi155bmnmmrob0q8cm93rtr9n31p02.apps.googleusercontent.com.json"  # path to your OAuth client credentials
TOKEN_FILE = "token.json"
OUTPUT_FOLDER_ID = "1IprIxpKlzv6LmCt_mV7zOlkCdDQSTW6q"  # Drive folder to upload resume books
WRITE_BACK_LINKS = False  # set True if you want to write uploaded Drive link back to sheet
# ====================================

# If you change scopes, remove token.json and re-authenticate
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly"  # read only for sheet; set read/write if writing back
]
if WRITE_BACK_LINKS:
    SCOPES.append("https://www.googleapis.com/auth/spreadsheets")

# Regex to extract Drive file id from common link formats
DRIVE_FILE_ID_RE = re.compile(r"(?:/d/|id=)([a-zA-Z0-9_-]{10,})")

def authenticate(creds_path: str, token_path: str, scopes: List[str]):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)
        # Save the credentials
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds

def get_sheets_service(creds):
    return build("sheets", "v4", credentials=creds)

def get_drive_service(creds):
    return build("drive", "v3", credentials=creds)

def read_sheet_to_df(sheets_service, spreadsheet_id: str, sheet_name: str) -> pd.DataFrame:
    range_name = f"{sheet_name}"  # fetch whole sheet
    resp = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    values = resp.get("values", [])
    if not values:
        raise ValueError("No data found in sheet.")
    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)
    # try to coerce numeric columns to numbers where possible
    for col in df.columns:
        # attempt int conversion for rating and year columns
        try:
            df[col] = pd.to_numeric(df[col], errors='ignore')
        except Exception:
            pass
    return df

def extract_file_id_from_link(link: str) -> Optional[str]:
    if not isinstance(link, str):
        return None
    m = DRIVE_FILE_ID_RE.search(link)
    if m:
        return m.group(1)
    # sometimes link is just an ID
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", link):
        return link
    return None

def download_file_as_pdf(drive_service, file_id: str) -> Optional[bytes]:
    """
    Return PDF bytes (converted if necessary). Handles:
      - PDF files
      - Google Docs -> exported to PDF
      - Office docs (.docx) -> attempts to copy -> google doc -> export to PDF (best-effort)
    Returns None on failure.
    """
    try:
        meta = drive_service.files().get(fileId=file_id, fields="mimeType,name,trashed").execute()
    except Exception as e:
        print(f"ERROR fetching metadata for {file_id}: {e}", file=sys.stderr)
        return None
    if meta.get("trashed"):
        print(f"File {file_id} is trashed, skipping.", file=sys.stderr)
        return None
    mime = meta.get("mimeType", "")
    name = meta.get("name", "file")
    # If it's already a PDF:
    if mime == "application/pdf":
        fh = io.BytesIO()
        request = drive_service.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue()
    # If it's a Google Doc, export
    if mime == "application/vnd.google-apps.document":
        fh = io.BytesIO()
        request = drive_service.files().export_media(fileId=file_id, mimeType="application/pdf")
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue()
    # If it's a Word document (.docx), try to copy it to a Google Doc then export
    if mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/msword"):
        try:
            # Copy the file and set the mimeType to Google Docs to force conversion
            copy_body = {"name": f"tmp-convert-{file_id}", "mimeType": "application/vnd.google-apps.document"}
            copied = drive_service.files().copy(fileId=file_id, body=copy_body).execute()
            tmp_id = copied["id"]
            try:
                # export copied google doc to PDF
                fh = io.BytesIO()
                request = drive_service.files().export_media(fileId=tmp_id, mimeType="application/pdf")
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return fh.getvalue()
            finally:
                # delete the temporary copied file
                try:
                    drive_service.files().delete(fileId=tmp_id).execute()
                except Exception:
                    pass
        except Exception as e:
            print(f"Failed to convert docx to google doc for {file_id}: {e}", file=sys.stderr)
            return None
    # Other common types: images? ignore. For unknown types, try to download raw bytes (may be office files)
    try:
        fh = io.BytesIO()
        request = drive_service.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw_bytes = fh.getvalue()
        # try to read as PDF - if not PDF then skip
        try:
            _ = PdfReader(io.BytesIO(raw_bytes))
            return raw_bytes
        except Exception:
            print(f"Downloaded file {name} ({mime}) is not a PDF and conversion wasn't possible; skipping.", file=sys.stderr)
            return None
    except Exception as e:
        print(f"Failed to download file {file_id}: {e}", file=sys.stderr)
        return None

def first_page_pdf_bytes(pdf_bytes: bytes) -> Optional[bytes]:
    """
    Extract the first page of a PDF and return bytes of a 1-page PDF.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) == 0:
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as e:
        print(f"Error extracting first page: {e}", file=sys.stderr)
        return None

def merge_pdf_bytes_list(pdf_bytes_list: List[bytes]) -> bytes:
    writer = PdfWriter()
    for b in pdf_bytes_list:
        reader = PdfReader(io.BytesIO(b))
        for p in reader.pages:
            writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def upload_bytes_to_drive(drive_service, data_bytes: bytes, name: str, parent_folder_id: str) -> Dict:
    """
    Upload bytes as a PDF to Drive. Returns file resource dict.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(data_bytes)
        tf.flush()
        media = MediaFileUpload(tf.name, mimetype="application/pdf")
        body = {"name": name, "parents": [parent_folder_id]}
        file = drive_service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    return file

def main():
    # Authenticate
    creds = authenticate(CREDENTIALS_FILE, TOKEN_FILE, SCOPES)
    sheets_service = get_sheets_service(creds)
    drive_service = get_drive_service(creds)

    print("Reading sheet...")
    df = read_sheet_to_df(sheets_service, SPREADSHEET_ID, SHEET_NAME)
    # Normalize column names (strip whitespace)
    df.columns = [str(c).strip() for c in df.columns]

    # Attempt to find key columns by name (case-insensitive contains)
    col_candidates = {c.lower(): c for c in df.columns}

    def find_column_by_keywords(keywords):
        for k, real in col_candidates.items():
            if all(w.lower() in k for w in keywords):
                return real
        # fallback: try any column that contains any of the keywords
        for k, real in col_candidates.items():
            if any(w.lower() in k for w in keywords):
                return real
        return None

    first_name_col = find_column_by_keywords(["first name"]) or find_column_by_keywords(["first"])
    last_name_col = find_column_by_keywords(["last name"]) or find_column_by_keywords(["last"])
    resume_col = find_column_by_keywords(["resume", "updated resume", "link"])
    grad_col = find_column_by_keywords(["graduation", "grad", "year"])

    if not all([first_name_col, last_name_col, resume_col, grad_col]):
        print("Couldn't automatically find all key columns. Detected columns:", file=sys.stderr)
        print("First name:", first_name_col, "Last name:", last_name_col, "Resume:", resume_col, "Grad:", grad_col, file=sys.stderr)
        print("Available columns:", df.columns.tolist(), file=sys.stderr)
        raise SystemExit("Please update the script to point to the correct column names.")

    # Identify company columns: choose numeric columns (ints) excluding grad column if numeric.
    company_columns = []
    for c in df.columns:
        if c in (first_name_col, last_name_col, resume_col, grad_col):
            continue
        # consider numeric typed columns as rating columns
        # note: sheet values are strings if read raw; try to coerce
        try:
            # check sample non-null values for numeric
            sample = df[c].dropna().iloc[0]
            # If it can be converted to int and in 1..5 then consider it a company rating
            val = int(float(sample)) if pd.notnull(sample) else None
            if val is not None and 1 <= val <= 5:
                company_columns.append(c)
        except Exception:
            # not numeric-like, skip
            continue

    print("Detected columns:")
    print("  First name:", first_name_col)
    print("  Last name:", last_name_col)
    print("  Resume link col:", resume_col)
    print("  Graduation year:", grad_col)
    print(f"  Company rating columns ({len(company_columns)}):")
    pprint(company_columns)

    # For each company, build resume book
    results = []
    for company in company_columns:
        print("\nProcessing company:", company)
        # Filter rows where rating >= 4
        def rating_ge4(val):
            try:
                return float(val) >= 4
            except Exception:
                return False

        subset = df[df[company].apply(rating_ge4)].copy()
        if subset.empty:
            print("  No members with rating >= 4; skipping.")
            continue
        # Force graduation year numeric
        subset[grad_col] = pd.to_numeric(subset[grad_col], errors='coerce')
        subset = subset.sort_values(by=grad_col, ascending=False)
        print(f"  Found {len(subset)} members; preparing resumes...")

        single_page_pdfs = []
        failed_entries = []
        for idx, row in tqdm(subset.iterrows(), total=len(subset), desc=f"  downloading for {company}"):
            name = f"{row.get(first_name_col,'') or ''} {row.get(last_name_col,'') or ''}".strip()
            link = row.get(resume_col)
            file_id = extract_file_id_from_link(link)
            if not file_id:
                print(f"    WARNING: Could not extract Drive file id from {link} for {name}; skipping.", file=sys.stderr)
                failed_entries.append((name, link, "no-id"))
                continue
            pdf_bytes = download_file_as_pdf(drive_service, file_id)
            if not pdf_bytes:
                print(f"    WARNING: Could not obtain PDF bytes for {name} ({file_id}); skipping.", file=sys.stderr)
                failed_entries.append((name, link, "download-failed"))
                continue
            onepage = first_page_pdf_bytes(pdf_bytes)
            if not onepage:
                print(f"    WARNING: Could not extract first page for {name}; skipping.", file=sys.stderr)
                failed_entries.append((name, link, "first-page-failed"))
                continue
            single_page_pdfs.append((name, onepage))

        if len(single_page_pdfs) == 0:
            print(f"  No usable resumes for {company}; skipping upload.")
            continue

        # Optionally, if you'd like to add a title page for company, you'd create a PDF page here.
        # Merge the single-page PDFs in the sorted order:
        merged_bytes = merge_pdf_bytes_list([b for (_, b) in single_page_pdfs])

        # upload
        safe_company_name = re.sub(r"[^\w\s-]", "", company)[:120].strip().replace(" ", "_")
        out_filename = f"{safe_company_name}_ResumeBook.pdf"
        print(f"  Uploading merged resume book ({len(single_page_pdfs)} pages) as {out_filename} ...")
        uploaded = upload_bytes_to_drive(drive_service, merged_bytes, out_filename, OUTPUT_FOLDER_ID)
        file_id = uploaded.get("id")
        webViewLink = uploaded.get("webViewLink")
        print(f"  Uploaded: id={file_id}, webViewLink={webViewLink}")

        results.append({
            "company": company,
            "uploaded_file_id": file_id,
            "webViewLink": webViewLink,
            "num_resumes": len(single_page_pdfs),
            "failed": failed_entries
        })

        # Optional: write back link to sheet (if WRITE_BACK_LINKS True)
        if WRITE_BACK_LINKS:
            # Append a column or write somewhere - for safety this code is left as a placeholder
            # Example: write uploaded link into a new sheet or column for that company
            pass

    print("\nDone. Summary:")
    pprint(results)
    print("\nIf you want the script to write back links to the sheet, set WRITE_BACK_LINKS=True and\n"
          "implement the cells/range you want to write to (exercise caution when writing).")

if __name__ == "__main__":
    main()