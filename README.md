# BLK Capital Management — Sponsorship Systems

Internal automation for BLK Capital Management's Sponsorships team. Two independent
systems live in this repo, plus the source data they read from.

| System | Folder | What it does |
|---|---|---|
| Resume Book Builder | [`resume-book-builder/`](resume-book-builder/) | Turns member resumes + a roster spreadsheet into branded, compressed PDF resume books — one general book and one per sponsor. |
| Sponsor Outreach System | [`outreach/`](outreach/) | Researches target firms, finds recruiting contacts, and drafts personalized outreach emails for human review and sending. |

They are siblings, not a pipeline — each runs on its own and neither imports from the
other. The only thing they share is a Google OAuth client
(`resume-book-builder/credentials.json`, gitignored, not in this repo).

---

## Resume Book Builder

A local Python CLI for the Sponsorships team's biggest recurring task: producing
professional resume books without a manual PDF-merging marathon before every deadline.

**Pipeline:** reads member data from a Google Sheet/Excel roster → downloads resumes
from Google Drive → extracts each resume's first page → merges with Canva-designed
cover and transition pages → compresses to an emailable size → writes a CSV error
report and run summary.

**Two output modes:**
- **General resume book** — every member, sorted by graduation year.
- **Sponsor-specific resume books** — filtered by company interest or a configurable
  minimum interest rating, one PDF per sponsor.

```bash
cd resume-book-builder
python -m src.main doctor --config config/settings.yaml          # verify setup
python -m src.main validate --config config/settings.yaml        # check data quality
python -m src.main build-general --config config/settings.yaml   # general book
python -m src.main build-sponsors --config config/settings.yaml  # per-sponsor books
```

Full setup, requirements, and troubleshooting: [`resume-book-builder/README.md`](resume-book-builder/README.md)
and [`resume-book-builder/INSTRUCTIONS.md`](resume-book-builder/INSTRUCTIONS.md).

---

## Sponsor Outreach System

Researches target firms from public sources, identifies recruiting/human-capital
contacts, and generates personalized outreach emails for human review — with a
dashboard for running the pipeline and reviewing drafts.

**Pipeline:**

```
targets.csv / dashboard intake
   -> research/fetch.py     crawl firm domain -> research/out/<slug>.json
                             no citable hooks -> manual queue, stop
   -> contacts/discover.py  title targeting + email-pattern inference
   -> contacts/verify.py    provider verification
   -> drafts/generate.py    sourced facts + template -> draft
   -> review                human approves or rejects (dashboard)
   -> a human sends         approved draft opens a prefilled Gmail compose URL
```

**Six non-negotiable rules**, enforced in code and tested:

1. **No invented facts** — every firm-specific claim traces to a `source_url`.
2. **No auto-send, ever** — the system produces Gmail drafts only; a human clicks send.
3. **No LinkedIn scraping** — contact discovery uses pattern inference + licensed
   verification APIs only.
4. **No em dashes** in generated copy (house style).
5. **No invented BLK numbers** — all BLK stats come from `config/blk_facts.json`.
6. **Research and drafting are separate** — research writes JSON, drafting only reads it.

Full detail on the rules, enforcement, and pipeline: [`outreach/CLAUDE.md`](outreach/CLAUDE.md).
Step-by-step SOPs for each stage: [`outreach/workflows/`](outreach/workflows/).

```bash
cd outreach
uvicorn app:app --reload   # runs the dashboard locally
```

---

## Supporting data

| Path | Contents |
|---|---|
| `meeting-notes/` | Notes from sponsor conversations, by firm |
| `sponsor-crm/` | Sponsor CRM spreadsheet (pipeline, leads, contact history) |
| `legacy/` | Original resume book scripts and a reference resume book, kept for history |
| `BLK Capital Prospectus 26-27 (1).pdf`, `BLK EMEA Prospectus 26-27.pdf` | Sponsor-facing prospectus decks |

This is real sponsor and student data, not sample data. Treat repo access the same way
you'd treat access to the underlying CRM: core Sponsorships team only.

---

## Secrets

Never committed, enforced by `.gitignore` at both the root and inside `outreach/`:
`.env`, `.env.local`, `credentials.json`, `token.json`, `config/gmail_token.json`.
Copy the relevant `.env.example` in each system folder and fill in your own values —
do not ask a teammate for their `.env` file, request your own API keys/OAuth client.

---

## Repository layout

```
resume-book-builder/   Resume book CLI (Python) — see its own README
outreach/               Sponsor outreach pipeline + dashboard (Python/FastAPI)
meeting-notes/          Per-firm sponsor meeting notes
sponsor-crm/            Sponsor CRM spreadsheet
legacy/                 Superseded scripts and reference material
```
