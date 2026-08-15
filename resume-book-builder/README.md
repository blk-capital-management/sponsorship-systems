# BLK Capital Management — Resume Book Builder

A local Python tool that builds professional resume books for BLK Capital Management's Sponsorships team.

---

## What it does

1. Reads member data from a Google Sheet or local Excel file.
2. Downloads resumes from Google Drive.
3. Extracts the first page of each resume.
4. Merges resumes with Canva-designed cover and transition pages.
5. Compresses the final PDF to email-ready size.
6. Produces a CSV error report and run summary after every run.

Two output modes:
- **General resume book** — all members, sorted by graduation year.
- **Sponsor-specific resume books** — filtered by company interest or rating threshold.

---

## Requirements

- macOS or Windows (macOS recommended)
- Python 3.11 or higher
- Ghostscript recommended for best PDF compression (`brew install ghostscript` on Mac); pikepdf used as Python fallback if Ghostscript is absent
- A Google Cloud project with Drive and Sheets APIs enabled
- OAuth credentials JSON (`credentials.json`) from Google Cloud Console

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for step-by-step setup.

---

## Quick setup

```bash
# 1. Clone the repo and enter the project folder
cd resume-book-builder

# 2. Create and activate a virtual environment (requires Python 3.11+)
python3.11 -m venv .venv        # use python3.12 or python3.11, NOT the macOS system python3
source .venv/bin/activate       # Mac/Linux
# .venv\Scripts\activate        # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the env template and fill in your values
cp .env.example .env

# 5. Edit config/settings.yaml to point to your spreadsheet and file paths

# 6. Drop your Canva-exported PDFs into:
#    input/transition_pages/  (named like "2025 Transition.pdf", "2026 Transition.pdf")
#    input/cover_pages/
```

---

## Commands

```bash
# 0. Verify your setup (run this first on any new machine)
python -m src.main doctor --config config/settings.yaml

# 1. Check data quality before generating any PDFs
python -m src.main validate --config config/settings.yaml

# 2. Build the general resume book
python -m src.main build-general --config config/settings.yaml

# 3. Build all sponsor-specific resume books
python -m src.main build-sponsors --config config/settings.yaml

# Optional: compress an existing PDF
python -m src.main compress-only --input output/general/resume_book.pdf --tier email_quality
```

Compression tiers: `high_quality` | `email_quality` (default) | `small_size`

Compression uses Ghostscript when available; falls back to pikepdf automatically if Ghostscript is not installed. The build always completes even if neither is available (uncompressed PDF is saved).

---

## Output files

| Path | Description |
|---|---|
| `output/general/` | General resume book PDF |
| `output/sponsor_books/` | One PDF per sponsor |
| `output/reports/error_report_*.csv` | Rows with problems |
| `output/reports/run_summary_*.csv` | Stats for the run |
| `output/reports/debug_*.log` | Full debug log |

---

## Configuration files

| File | Purpose |
|---|---|
| `config/settings.yaml` | Paths, data source, compression settings |
| `config/column_mapping.yaml` | Maps spreadsheet column headers to internal fields |
| `config/sponsors.yaml` | Sponsor list, rating thresholds, modes |

---

## Troubleshooting

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for common errors and how to fix them. Run `doctor` first — it identifies the most common setup issues before you start a build.

---

## Project structure

```
resume-book-builder/
  config/           — YAML configuration files
  input/            — Canva cover and transition PDFs go here
  legacy/           — Original scripts (kept for reference)
  output/           — Generated PDFs and reports
  src/              — Python source code
    auth/           — Google authentication
    builders/       — General and sponsor book builders
    clients/        — Google Drive and Sheets API wrappers
    doctor/         — Setup health-check (doctor command)
    loaders/        — Excel and Sheets data loaders
    processing/     — PDF download, convert, compress, merge, divider generation
    reporting/      — Error report and run summary writers
    utils/          — Logging and file utilities
    validation/     — Pre-flight data validation
```
