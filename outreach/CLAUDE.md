# BLK Sponsor Outreach System

Researches target firms from public sources, identifies recruiting and human capital
contacts, and generates personalized outreach emails for human review and sending.

Sibling of `resume-book-builder/`. The two are independent. Do not modify, move, or
refactor the resume book from here. The only shared asset is the Google OAuth client
(`../resume-book-builder/credentials.json`).

---

## The six non-negotiable rules

Treat these as hard constraints. Violating any one of them makes the output unusable.

1. **No invented facts.** Every firm-specific claim in a generated email must trace to a
   `source_url` captured during research. No URL means the claim does not go in the email.
   If research produces nothing citable, route the firm to the manual queue rather than
   writing a generic paragraph.

2. **No auto-send, ever.** The system produces Gmail drafts and a review queue. A human
   sends. There is no code path that transmits email without explicit human action.

3. **No LinkedIn scraping.** It violates their terms. Contact discovery uses email-pattern
   inference plus licensed verification APIs only.

4. **No em dashes anywhere** in generated copy. House style rule, no exceptions.

5. **Do not invent BLK's own numbers.** All BLK statistics come from
   `config/blk_facts.json`. If a number is not in that file, it does not appear in an email.

6. **Separate research from drafting.** Research writes a structured JSON artifact.
   Drafting reads only that artifact. Never let a single prompt both browse and write copy,
   because that is where hallucinated firm facts come from.

### How the rules are enforced in code

| Rule | Enforcement |
|---|---|
| 1 | `common/provenance.py` rejects any draft sentence whose entities do not trace to an `alignment_hook` with a `source_url`. |
| 2 | `tests/test_no_send_path.py` AST-scans the whole package for send endpoints. Gmail scope is `gmail.compose`, which cannot send. |
| 3 | No LinkedIn fetch exists. `common/http.py` refuses `linkedin.com` at the request layer. |
| 4 | `common/provenance.py::assert_no_em_dash`, run on every generated string. |
| 5 | Preflight blocks on null facts. The template fact-consistency check catches drift between `template.md` and `blk_facts.json`. |
| 6 | `research/fetch.py` is the only module allowed to make outbound web requests. `drafts/generate.py` takes a JSON path, never a URL. |

---

## House tone

Professional, warm, direct. Conversational but not casual. Never self-deprecating on
behalf of the organization. No over-explaining and no signaling eagerness. Short sentences.
No em dashes.

This applies to generated email copy. It also applies to anything written into the review
queue that Jamari reads at speed.

---

## Pipeline

```
targets.csv
   -> research/fetch.py     crawls firm domain, writes research/out/<slug>.json
                            low confidence or no hooks -> review/manual_queue.csv, stop
   -> contacts/discover.py  title targeting + pattern inference
   -> contacts/verify.py    provider verification, drops below threshold
   -> drafts/generate.py    artifact + template -> drafts/out/<slug>.md + evidence block
   -> review/queue.csv      human approves or rejects
   -> send/to_gmail_drafts.py   creates Gmail drafts, stops
   -> send/log_to_crm.py    writes back to the sponsor workbook
```

Each stage reads the previous stage's file output. No stage calls the next one.

---

## Conventions

- Python 3.11+. Style follows `resume-book-builder/src/`: module docstring with a usage
  example, `get_logger("<module.path>")` for logging, type hints on public functions.
- Config paths in `settings.yaml` are relative to the config file's directory, matching the
  resume book's contract.
- Secrets come from environment variables only. Never commit a key. `.env` is gitignored.
- Every module is runnable standalone with `--help`.
- Tests use pytest. `pythonpath = .` in `pytest.ini`, same as the resume book.
- Slugs are produced by `common/slugify.py::firm_slug` and are the join key across every
  stage. Do not derive slugs ad hoc.

## Data locations

| What | Where |
|---|---|
| Target firms | `data/targets.csv`, seeded from the CRM Pipeline & Leads tab |
| Sponsor CRM | `../sponsor-crm/Sponsor_CRM_2026-27_UPDATED (2).xlsx` |
| EMEA prospectus | `../BLK EMEA Prospectus 26-27.pdf` |
| Google OAuth client | `../resume-book-builder/credentials.json` |
| Outreach token cache | `config/gmail_token.json` (separate from the resume book's `token.json`) |
