# BLK Resume Book Builder — Runbook

> **Superseded by [INSTRUCTIONS.md](INSTRUCTIONS.md)**
>
> INSTRUCTIONS.md is the canonical operating guide. It covers the same ground as this file and has been updated to reflect all current features (doctor command, auto-generated dividers, compression fallback, and the direct-upload intake roadmap). If you are a new Sponsorship Chair, read INSTRUCTIONS.md instead.
>
> This file is kept for historical reference only. Do not update it.

---

Step-by-step instructions for Sponsorship Chairs who are not technical.  
If something breaks, this document tells you what to do.

---

## Table of contents

1. [One-time setup](#1-one-time-setup)
2. [Before every run](#2-before-every-run)
3. [Running the tool](#3-running-the-tool)
4. [Reading the reports](#4-reading-the-reports)
5. [Common errors and fixes](#5-common-errors-and-fixes)
6. [Updating the sponsor list](#6-updating-the-sponsor-list)
7. [Changing the column names](#7-changing-the-column-names)
8. [Getting help](#8-getting-help)

---

## 1. One-time setup

Do this once when you first take over the project.

### 1a. Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/).
2. Download Python 3.11 or higher.
3. Run the installer. On Windows, check "Add Python to PATH".
4. Open Terminal (Mac) or Command Prompt (Windows) and type:
   ```
   python3 --version
   ```
   You should see something like `Python 3.11.x`.

### 1b. Install Ghostscript (for PDF compression)

**Mac:**
```bash
brew install ghostscript
```
If you don't have Homebrew: [brew.sh](https://brew.sh)

**Windows:**
Download from [ghostscript.com/releases](https://www.ghostscript.com/releases/gsdnld.html) and install.

### 1c. Install project dependencies

Open Terminal in the `resume-book-builder/` folder and run:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On Windows replace the second line with:
```
venv\Scripts\activate
```

### 1d. Set up Google credentials

You need a `credentials.json` file from Google Cloud Console. This file lets the tool log in to Google Drive and Sheets.

Ask your predecessor or follow these steps:
1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Create a project (or use the existing BLK one).
3. Enable the **Google Drive API** and **Google Sheets API**.
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**.
5. Choose **Desktop app**.
6. Download the JSON file and rename it `credentials.json`.
7. Place it in the `resume-book-builder/` folder.

### 1e. Create your .env file

```bash
cp .env.example .env
```
Open `.env` and make sure `GOOGLE_CREDENTIALS_FILE=credentials.json`.

### 1f. First login

The first time you run any command, a browser window will open asking you to sign in with your Google account. Sign in and allow access. A `token.json` file will be saved automatically — you won't have to log in again unless the token expires (~1 week).

---

## 2. Before every run

1. **Export your Google Sheet to Excel** if using Excel mode, or confirm the Sheet ID in `config/settings.yaml`.
2. **Drop transition pages** into `input/transition_pages/`. Files must be named exactly like:
   - `2025 Transition.pdf`
   - `2026 Transition.pdf`
   - `2027 Transition.pdf`
   - etc.
3. **Drop cover pages** into `input/cover_pages/` if needed.
4. Open `config/settings.yaml` and confirm:
   - `data.excel_file` or `data.spreadsheet_id` is correct.
   - `output.drive_upload_folder_id` is set if you want auto-upload to Drive.
5. Open `config/sponsors.yaml` and confirm the sponsor list is current.

---

## 3. Running the tool

Open Terminal in the `resume-book-builder/` folder.

Activate the virtual environment first (every time):
```bash
source venv/bin/activate
```

### Step 1: Validate your data

Always run this first. It checks for missing names, bad links, duplicates, and missing transition pages **before** downloading anything.

```bash
python -m src.main validate --config config/settings.yaml
```

Open `output/reports/error_report_*.csv` and fix any errors before continuing.

### Step 2: Build the general resume book

```bash
python -m src.main build-general --config config/settings.yaml
```

Output: `output/general/BLK_Resume_Book_<date>.pdf`

### Step 3: Build sponsor-specific resume books

```bash
python -m src.main build-sponsors --config config/settings.yaml
```

Output: one PDF per sponsor in `output/sponsor_books/`.

### Step 4: Check the run summary

Open `output/reports/run_summary_*.csv` to see:
- How many resumes were processed.
- How many were included vs skipped.
- File sizes and compression ratios.
- Any warnings.

---

## 4. Reading the reports

### error_report.csv

Each row is one problem. Columns:
- `row` — the row number in your spreadsheet where the issue was found.
- `student` — the student's name.
- `error_type` — what went wrong (e.g., `MISSING_RESUME_LINK`, `FAILED_DOWNLOAD`).
- `detail` — a plain-English explanation.
- `severity` — `warning` (student skipped but run continues) or `error` (run stopped).

### run_summary.csv

One row per run. Columns include total processed, total included, failures, final file size, compression ratio, and output path.

---

## 5. Common errors and fixes

| Error | What it means | Fix |
|---|---|---|
| `MISSING_RESUME_LINK` | A student's row has no Drive link | Ask the student to re-submit their resume |
| `BROKEN_PERMISSIONS` | Drive link exists but you can't open it | Ask the student to set sharing to "Anyone with the link" |
| `FAILED_DOWNLOAD` | Resume downloaded 0 bytes | Check your internet; retry the student manually |
| `UNSUPPORTED_FILE_TYPE` | Resume is not PDF, Google Doc, or Word | Ask the student to re-submit as PDF or Google Doc |
| `MISSING_TRANSITION_PAGE` | No transition PDF for a graduation year | Export a Canva transition page for that year and drop it in `input/transition_pages/` |
| `DUPLICATE_ENTRY` | Same student appears twice in the sheet | Remove the duplicate row from the sheet |
| `FINAL_PDF_OVERSIZED` | Final book exceeds 20MB | Change `compression.final_book_tier: small_size` in settings.yaml and re-run |
| `token.json expired` | Google login timed out | Delete `token.json` and re-run; a browser window will open for login |

---

## 6. Updating the sponsor list

Open `config/sponsors.yaml`. Add a block for each new sponsor:

```yaml
- name: "Firm Name"
  column: "Firm Name"        # must match the exact column header in your spreadsheet
  mode: threshold
  min_rating: 4
  output_filename: "Firm_Name_Resume_Book.pdf"
```

Use `mode: all_interested` to include anyone who rated the firm at all (even a 1).

---

## 7. Changing the column names

If your Google Form ever changes a question (e.g., renames "Updated Resume" to "Resume Link"), open `config/column_mapping.yaml` and add the new name to the right field's alias list:

```yaml
  resume_link:
    - "Updated Resume"
    - "Resume Link"           # add new alias here
```

Do not remove old aliases — they help if you ever process old exports.

---

## 8. Getting help

- First, read the error message in the console and check the error report CSV.
- Check this runbook's Common Errors table above.
- If the tool produces a `debug_*.log` file in `output/reports/`, that file has more detail.
- Contact Jamari or the technical lead for BLK Sponsorships.
