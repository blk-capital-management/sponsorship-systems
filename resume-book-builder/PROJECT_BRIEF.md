# BLK Capital Management Resume Book Builder — Project Brief

## Objective

Build a durable, repeatable resume book automation system for BLK Capital Management’s Sponsorships team.

The goal is to replace a fragile, prompt-dependent workflow with a clean Python-based system that future non-technical sponsorship chairs can run with minimal setup.

This system should generate:
1. A general BLK resume book.
2. Sponsor-specific resume books.
3. Compressed, sponsor-ready PDF files that can be sent by email.
4. Error reports showing missing links, broken permissions, failed conversions, oversized PDFs, and other issues.

## Current Workflow

The current workflow uses:
- Student resume uploads from a Google Form.
- A Google Sheet or Excel file with student names, graduation years, resume links, and sponsor/company interest or rating columns.
- Canva-created cover pages and transition/divider pages.
- Python scripts that download resumes from Google Drive, convert files to PDFs when needed, extract resume pages, merge PDFs, and create resume books.
- Claude prompts to adjust scripts manually when needed.

This works, but it is too manual and fragile.

## Legacy Scripts

There are two inherited scripts in the `legacy/` folder.

### 1. `blk_resume_book_builder.py`

Purpose:
- Builds one general resume book.
- Reads a local Excel file.
- Downloads resumes from Google Drive links.
- Converts Google Docs and Word documents to PDF when possible.
- Extracts the first page of each resume.
- Sorts students by graduation year.
- Adds Canva-created transition pages.
- Outputs a single combined PDF.

### 2. `custom_sponsor_resume_book_builder.py`

Purpose:
- Builds customized sponsor-specific resume books.
- Reads data from a Google Sheet.
- Detects sponsor/company rating columns.
- Filters students based on sponsor/company interest or rating.
- Downloads and converts resumes.
- Extracts the first page of each resume.
- Builds separate PDF resume books for sponsors.
- Uploads finished PDFs to Google Drive.

## Key System Requirements

### Resume Inputs

Students upload resumes through a Google Form. Most resumes should be one page, but the system should still extract only the first page by default to keep the resume book consistent.

### Resume Book Types

The system should support two main modes:

1. General Resume Book
   - Includes broad member/resume data.
   - Organized by graduation year.
   - Includes Canva cover and transition pages.

2. Sponsor-Specific Resume Books
   - Includes students who are relevant to each sponsor.
   - Some books may include all interested students.
   - Other books may apply a minimum sponsor rating threshold.
   - The threshold should be configurable.

### Canva Pages

For now, Canva remains the design source for:
- Main cover pages.
- Transition/divider pages.
- Sponsor-specific cover pages if needed.

The system should ingest those Canva-exported PDFs rather than trying to generate designs automatically.

### Compression

PDF size is a major requirement.

Many sponsors may not want to use Google Drive, OneDrive, or external access links. The finished PDFs should be small enough to email while maintaining professional readability.

Default max attachment target:
- 20MB per final resume book, unless changed later.

The system should:
- Compress individual resume PDFs after download/conversion/extraction.
- Compress the final merged resume book.
- Support multiple compression tiers:
  - high_quality
  - email_quality
  - small_size
- Track file sizes before and after compression.
- Warn if the final file is still above the configured size threshold.
- Never sacrifice readability unnecessarily.

### Output Requirements

The system should generate:
- Final compressed resume book PDFs.
- Optional raw/uncompressed versions for internal backup.
- CSV error report.
- CSV run summary.
- Console logs that are readable by non-technical users.

### Error Reporting

The system should identify and report:
- Missing first name.
- Missing last name.
- Missing graduation year.
- Missing resume link.
- Invalid Google Drive link.
- Broken Google Drive permissions.
- Unsupported file type.
- Failed download.
- Failed conversion.
- Failed first-page extraction.
- Missing transition page.
- Duplicate student entry.
- Sponsor/company with no eligible students.
- Final PDF above target file size.

## Users

The system must eventually be usable by:
- Jamari.
- Fola.
- Future non-technical BLK Sponsorships Chairs.

Therefore, the system should prioritize:
- Clear setup instructions.
- Simple commands.
- Strong error messages.
- Minimal manual editing.
- Transferability.

## Current Development Setup

Initial version:
- Personal GitHub repo.
- Local development through VS Code and Claude Code.
- Python-based tool.
- Google Drive and Google Sheets APIs.

Future versions may move to:
- BLK-owned GitHub or Drive.
- Google Cloud Run.
- GitHub Actions.
- Lightweight internal web app.

But the first priority is to build a reliable local tool.

## Build Philosophy

Do not build a fragile AI coworker workflow.

Build a deterministic resume book pipeline that uses AI only for development, review, documentation, and future improvements.

The code should be the source of truth.

Claude Code should:
1. Inspect the legacy scripts.
2. Identify shared logic.
3. Refactor into reusable modules.
4. Add validation.
5. Add compression.
6. Add reporting.
7. Add clear documentation.

## Desired CLI Commands

The final tool should support commands similar to:

```bash
python -m src.main validate --config config/settings.yaml

python -m src.main build-general --config config/settings.yaml

python -m src.main build-sponsors --config config/settings.yaml

python -m src.main compress-only --input output/raw.pdf --tier email_quality