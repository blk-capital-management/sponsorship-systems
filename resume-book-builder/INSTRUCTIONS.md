# BLK Capital Management — Resume Book Builder
## Complete Operating Instructions

> Who this is for: the current Sponsorships Chair (or anyone covering for them).
> No prior coding experience required for the day-to-day workflow.
> One-time setup takes about 20 minutes.

---

## Table of Contents

1. [What this tool does](#1-what-this-tool-does)
2. [One-time setup](#2-one-time-setup)
3. [What to prepare before every run](#3-what-to-prepare-before-every-run)
4. [Running the tool — step by step](#4-running-the-tool--step-by-step)
5. [Understanding the output files](#5-understanding-the-output-files)
6. [Configuration reference](#6-configuration-reference)
7. [Common errors and how to fix them](#7-common-errors-and-how-to-fix-them)
8. [Updating the sponsor list each semester](#8-updating-the-sponsor-list-each-semester)
9. [If a Google Form column name changes](#9-if-a-google-form-column-name-changes)
10. [Resume intake: Drive links vs file uploads](#10-resume-intake-drive-links-vs-file-uploads)
11. [Project file map](#11-project-file-map)

---

## 1. What this tool does

The Resume Book Builder takes your member registration spreadsheet and automatically:

1. Validates every row for missing names, bad Drive links, and duplicates — before touching any PDFs.
2. Downloads each member's resume from Google Drive.
3. Extracts only the first page.
4. Assembles a polished resume book: cover page → year-divider pages → resumes sorted by graduation year.
5. Compresses the final PDF to email-ready size.
6. Produces a **general book** (every member) and **per-sponsor books** (filtered by interest rating).
7. Optionally uploads sponsor books to a shared Google Drive folder.

Every run generates a timestamped error report and run summary in `output/reports/` so you always have a record.

---

## 2. One-time setup

Do this once when you first take over. Your predecessor may have already done some of these steps — check with them first.

### 2a. Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download Python **3.11 or higher**.
2. Run the installer. On Windows, check **"Add Python to PATH"** before clicking Install.
3. Verify in Terminal (Mac) or Command Prompt (Windows):
   ```
   python3 --version
   ```
   Expected output: `Python 3.11.x` or higher.

### 2b. Install Ghostscript (PDF compression — recommended)

Ghostscript gives the best compression quality. If it is not installed, the tool automatically falls back to a Python-based compressor (`pikepdf`) and logs a warning. The build will still complete either way.

**Mac (recommended):**
```bash
brew install ghostscript
```
No Homebrew? Install it first from [brew.sh](https://brew.sh), then run the command above.

**Windows:**
Download and install from [ghostscript.com/releases](https://www.ghostscript.com/releases/gsdnld.html). Choose the 64-bit version.

Verify it works:
```bash
gs --version
```

> **Tip:** The `doctor` command (see Step 0 below) will tell you if Ghostscript is missing and what fallback will be used.

### 2c. Set up the Python environment

Open Terminal in the `resume-book-builder/` folder.

> **Important:** macOS ships with Python 3.9, which is too old. Use `python3.11` or `python3.12` explicitly (whichever you installed in step 2a). Do **not** use `python3` on its own on a Mac — it may resolve to the system 3.9.

```bash
python3.12 -m venv .venv        # or python3.11 if 3.12 is not installed
source .venv/bin/activate       # Mac/Linux
# .venv\Scripts\activate        # Windows

pip install -r requirements.txt
```

You will need to run `source .venv/bin/activate` (Mac) or `.venv\Scripts\activate` (Windows) **every time you open a new terminal window** before running the tool.

### 2d. Get Google credentials

The tool needs permission to read Google Drive and Sheets on your behalf. This is a one-time Google Cloud setup.

Ask your predecessor if a `credentials.json` already exists. If not:

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Create a new project (name it something like "BLK Resume Builder").
3. In the left sidebar, click **APIs & Services → Library**.
4. Search for and enable:
   - **Google Drive API**
   - **Google Sheets API**
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
6. Choose **Desktop app**. Name it anything.
7. Click **Download JSON**. Rename the file to `credentials.json`.
8. Place it in the `resume-book-builder/` folder (same level as `config/`).

### 2e. Create your .env file

```bash
cp .env.example .env
```

Open `.env`. It should already contain:
```
GOOGLE_CREDENTIALS_FILE=credentials.json
```

That's all you need for the default OAuth setup.

### 2f. First login (one-time browser prompt)

The first time you run any command, a browser window will open asking you to sign in with a Google account that has access to the relevant Drive and Sheets. Sign in and click **Allow**.

A `token.json` file is saved automatically. You will not have to log in again for roughly one week, after which the token auto-refreshes silently as long as you keep using it.

If you ever see a login error, delete `token.json` and re-run — the browser window will appear again.

---

## 3. What to prepare before every run

Before generating any PDFs, gather the following:

### 3a. Member data spreadsheet

**Option A — Excel (default):**
Export your Google Form responses as `.xlsx` and save it to:
```
resume-book-builder/input/Member Registration _ Fall 2025 (Responses).xlsx
```
Then confirm the path in `config/settings.yaml` under `data.excel_file`.

**Option B — Google Sheets (live):**
In `config/settings.yaml`, change `data.source` to `sheets` and paste your Sheet ID under `data.spreadsheet_id`. The Sheet ID is the long string in the URL between `/d/` and `/edit`.

### 3b. Cover page (title page)

Export your Canva cover page as a PDF and place it at:
```
resume-book-builder/input/cover_pages/Title Page.pdf
```
Accepted filenames (case-insensitive): `cover.pdf`, `title page.pdf`, `title_page.pdf`.

The `legacy/Title Page.pdf` from the Spring 2026 book is a reference for what this should look like.

### 3c. Transition / divider pages

Divider pages separate the resume book by graduation year. You have two options:

**Option A — Canva-exported PDFs (preferred for branded output):**
Export one divider per graduation year and place them in:
```
resume-book-builder/input/transition_pages/
```
Name each file with the year somewhere in the filename:
```
2025 Transition.pdf
2026 Transition.pdf
2027 Transition.pdf
2028 Transition.pdf
```
The tool matches the year number anywhere in the filename — `2026_Divider.pdf` also works.

**Option B — Auto-generated fallback (no action needed):**
If a Canva divider PDF is missing for a graduation year, the tool automatically generates a clean black-and-white divider showing the year and "BLK Capital Management". The build log will show `GENERATED_TRANSITION_PAGE_FALLBACK` for any year where this happened.

You can mix both: supply Canva PDFs for years that have them, and let the tool generate the rest. Canva PDFs always take priority over generated ones.

### 3d. Update the sponsor list

Open `config/sponsors.yaml` and confirm it reflects the current semester's sponsors. See [Section 8](#8-updating-the-sponsor-list-each-semester) for instructions.

---

## 4. Running the tool — step by step

Open Terminal in `resume-book-builder/` and activate the environment:

```bash
source .venv/bin/activate
```

### Step 0 — Doctor (run this first on any new machine or after setup changes)

```bash
python -m src.main doctor --config config/settings.yaml
```

This checks your entire setup — Python version, required packages, Ghostscript, folder structure, config files, and Google credentials — **without touching any member data**. It prints a PASS / WARNING / ERROR checklist and writes a timestamped report to `output/reports/doctor_report_*.csv`.

Fix any `ERROR` items before running the commands below. `WARNING` items are non-blocking but should be reviewed.

### Step 1 — Validate (always run this first)

```bash
python -m src.main validate --config config/settings.yaml
```

This checks every row in your spreadsheet for problems **without downloading a single file**. It is fast and safe to run repeatedly.

Open the output file it prints (e.g., `output/reports/validation_errors_20260519_143022.csv`) and fix any rows marked `error` before continuing. Rows marked `warning` are logged but will not stop the build.

**Do not proceed to Step 2 until this reports 0 errors.**

### Step 2 — Build the general resume book

```bash
python -m src.main build-general --config config/settings.yaml
```

This downloads all member resumes, assembles the book, and compresses it.

Output: `output/general/BLK_Resume_Book.pdf`

Expected runtime: 2–8 minutes depending on the number of members and your internet speed.

### Step 3 — Build sponsor-specific resume books

```bash
python -m src.main build-sponsors --config config/settings.yaml
```

Output: one PDF per sponsor in `output/sponsor_books/`.

### Step 4 — Check the run summary

Open `output/reports/run_summary_*.csv` to confirm:
- How many resumes were included vs skipped.
- Final file size.
- Any skipped members and why.

If many members were skipped, check the error report CSV for the reason (typically broken Drive permissions).

### Optional — Compress an existing PDF

If a book came out too large, compress it without rebuilding:

```bash
python -m src.main compress-only \
  --input output/general/BLK_Resume_Book.pdf \
  --tier small_size
```

Tiers: `high_quality` (300 dpi) | `email_quality` (150 dpi, default) | `small_size` (72 dpi)

---

## 5. Understanding the output files

| File | What it is |
|------|-----------|
| `output/general/BLK_Resume_Book.pdf` | The general resume book for all members |
| `output/sponsor_books/<Firm>_Resume_Book.pdf` | Per-sponsor book (one per entry in sponsors.yaml) |
| `output/reports/validation_errors_*.csv` | Every error and warning found in your data |
| `output/reports/validation_summary_*.csv` | One-row stats summary per run (rows, errors, skips) |
| `output/reports/debug_*.log` | Full debug log — send this to your tech lead if something breaks |

Files are timestamped so old runs are never overwritten.

---

## 6. Configuration reference

### config/settings.yaml — main settings

| Key | What it controls |
|-----|-----------------|
| `data.source` | `excel` (local file) or `sheets` (live Google Sheet) |
| `data.excel_file` | Path to your `.xlsx` file |
| `data.spreadsheet_id` | Google Sheet ID (only needed when source = sheets) |
| `processing.first_page_only` | `true` = take only page 1 of each resume (strongly recommended) |
| `processing.grad_year_sort_order` | `ascending` (2025 first) or `descending` (2028 first) |
| `compression.final_book_tier` | `high_quality`, `email_quality`, or `small_size` |
| `compression.max_pdf_mb` | Warning threshold — logs a warning if the final PDF exceeds this |
| `compression.auto_downgrade` | `true` = automatically try a smaller tier if book exceeds max_pdf_mb |
| `output.drive_upload_folder_id` | Google Drive folder ID to auto-upload sponsor books to (leave blank to skip) |
| `output.keep_raw` | `true` = also save an uncompressed copy alongside the final book |

### config/sponsors.yaml — sponsor list

```yaml
sponsors:
  - name: "Goldman Sachs"
    column: "Goldman Sachs"       # exact column header in your spreadsheet
    mode: threshold
    min_rating: 4                 # include members who rated this firm >= 4
    output_filename: "Goldman_Sachs_Resume_Book.pdf"

  - name: "Jane Street"
    column: "Jane Street"
    mode: all_interested          # include anyone who rated this firm at all
    output_filename: "Jane_Street_Resume_Book.pdf"
```

**mode options:**
- `threshold` — only members whose rating for this sponsor is >= `min_rating` (typically 4 or 5)
- `all_interested` — any member who left a non-empty rating

### config/column_mapping.yaml — column aliases

Maps the internal field names the code uses to whatever your spreadsheet actually calls them. If a column header ever changes (e.g., Google Form question wording), add the new name to the right alias list here.

---

## 7. Common errors and how to fix them

| Error in report | What it means | Fix |
|-----------------|---------------|-----|
| `MISSING_RESUME_LINK` | Member row has no Drive link | Ask the member to re-submit their resume via the form |
| `BROKEN_PERMISSIONS` | Drive link exists but the tool can't open it | Ask the member to change Drive sharing to **"Anyone with the link → Viewer"** |
| `FAILED_DOWNLOAD` | File downloaded 0 bytes | Check internet connection; retry. Could be a transient Drive error |
| `UNSUPPORTED_FILE_TYPE` | Resume is a Pages, image, or other unsupported format | Ask the member to re-submit as PDF, Google Doc, or Word (.docx) |
| `FAILED_FIRST_PAGE_EXTRACTION` | PDF appears corrupt | Ask the member to re-export and resubmit their resume |
| `DUPLICATE_ENTRY` | Same member appears twice | Delete the older duplicate row in the spreadsheet |
| `INVALID_DRIVE_LINK` | Link is not a recognisable Google Drive URL | Ask the member to copy their link directly from Google Drive's Share dialog |
| `column_mapping error` | A required spreadsheet column could not be matched | Open `config/column_mapping.yaml` and add the exact column header as an alias |
| `token.json expired` | Google login timed out | Delete `token.json` and re-run — browser login prompt will appear |
| Final PDF > 20 MB | Book is too large for email | Add `compression.final_book_tier: small_size` to settings.yaml and re-run, or use `compress-only` |

---

## 8. Updating the sponsor list each semester

1. Open `config/sponsors.yaml`.
2. Remove sponsors who are no longer partners.
3. Add a new block for each new sponsor:

```yaml
- name: "Firm Name Here"
  column: "Firm Name Here"     # copy the exact column header from your Google Form
  mode: threshold
  min_rating: 4
  output_filename: "Firm_Name_Here_Resume_Book.pdf"
```

4. Make sure the `column` value exactly matches the column header in your spreadsheet (the tool is case-insensitive, but spelling must match).
5. Run `validate` to confirm the tool can find the column before building.

---

## 9. If a Google Form column name changes

When a form question is reworded, the exported spreadsheet will have a new column header. The tool will report a `column_mapping error` for that field.

Fix:
1. Open `config/column_mapping.yaml`.
2. Find the field that broke (e.g., `resume_link`).
3. Add the new column name to its alias list:
   ```yaml
   resume_link:
     - "Updated Resume"
     - "Resume Link"
     - "Your New Question Wording Here"   # add this line
   ```
4. Do not remove old aliases — they let you reprocess older exports without breaking anything.

> **Important:** Do not casually rename Google Form questions. The sponsor filtering logic depends on stable column names in `config/sponsors.yaml`. If you rename a sponsor's question in the form, you must also update the `column` field for that sponsor in `config/sponsors.yaml` and add the old name as an alias in `config/column_mapping.yaml` so that old exports still work.

---

## 10. Resume intake: Drive links vs file uploads

### Current workflow (manually pasted Drive links)

Members paste a Google Drive share link into the form. This is what the tool currently reads from the `resume_link` column.

**Common problem:** Members sometimes share files with restricted permissions. The tool will report `BROKEN_PERMISSIONS` for those rows. Ask the member to change sharing to **"Anyone with the link → Viewer"**.

### Future workflow (direct file uploads)

The long-term plan is to use Google Forms' built-in file upload feature, which eliminates the broken-permissions problem entirely. When that switch happens:

- The `column_mapping.yaml` file already includes aliases for likely future upload column names (`Resume Upload`, `Upload Your Resume`, `Resume File`, etc.).
- The tool normalises all resume references — Drive share URLs, Forms-upload URLs, and bare file IDs — through a single function, so the transition should require only a `column_mapping.yaml` update, not a code change.
- Do not switch the form to file uploads until confirmed with your tech lead, as it requires verifying that the BLK Google Drive folder has adequate storage and that the upload destination is correctly shared with the service account or OAuth user running the tool.

---

## 11. Project file map

```
resume-book-builder/
│
├── config/
│   ├── settings.yaml          Main config — edit this each semester
│   ├── sponsors.yaml          Sponsor list — edit each semester
│   └── column_mapping.yaml    Column aliases — edit if form questions change
│
├── input/
│   ├── cover_pages/           Drop cover/title page PDF here
│   └── transition_pages/      Drop year-divider PDFs here (one per grad year)
│
├── legacy/                    Original scripts + Spring 2026 book (reference only)
│
├── output/
│   ├── general/               General resume book PDF
│   ├── sponsor_books/         Per-sponsor PDFs
│   └── reports/               Error reports, run summaries, debug logs
│
├── src/
│   ├── auth/                  Google OAuth + service account login
│   ├── builders/              build-general and build-sponsors logic
│   ├── clients/               Google Drive + Sheets API wrappers
│   ├── doctor/                Setup health-check (doctor command)
│   ├── loaders/               Excel and Sheets data loading
│   ├── processing/            PDF download, convert, compress, merge, divider generation
│   ├── reporting/             Error report and run summary writers
│   ├── utils/                 Logging and file helpers
│   ├── validation/            Pre-flight data checks
│   └── main.py                CLI entrypoint (run this)
│
├── INSTRUCTIONS.md            This file
├── README.md                  Developer overview
├── RUNBOOK.md                 Legacy runbook (superseded by this file)
├── requirements.txt           Python dependencies
└── .env.example               Template for your .env file
```

---

*Last updated: May 2026. Maintained by the BLK Capital Management Sponsorships team.*
