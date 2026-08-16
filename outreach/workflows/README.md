# Workflows

One SOP per pipeline stage. Each states the objective, what it needs, which tool
runs it, what it produces, and what to do when it fails.

These exist because the stage order used to live only in a prose diagram in
`CLAUDE.md`, and that diagram drifted: it described a `send/to_gmail_drafts.py`
stage that never existed. A workflow that names the tool it calls stays honest,
because a wrong name is visible the moment someone tries to run it.

| # | Workflow | Tool it drives |
|---|---|---|
| 1 | [add_leads.md](add_leads.md) | `POST /api/intake`, `data/targets.csv` |
| 2 | [research_firm.md](research_firm.md) | `research/fetch.py` |
| 3 | [find_contacts.md](find_contacts.md) | `contacts/discover.py`, `contacts/verify.py` |
| 4 | [generate_draft.md](generate_draft.md) | `drafts/generate.py` |
| 5 | [review_and_send.md](review_and_send.md) | dashboard draft review |
| 6 | [resolve_manual_queue.md](resolve_manual_queue.md) | manual queue view |

Run them in order. Each stage reads the previous stage's output and none calls
the next one, so you can stop after any stage and pick it up later.

## Rules that outrank any workflow

The six non-negotiables in [../CLAUDE.md](../CLAUDE.md). Most relevant here:

- **No auto-send.** Nothing in these workflows transmits email. Step 5 ends at a
  prefilled Gmail compose URL that a person clicks.
- **No invented facts.** Every firm-specific claim traces to a `source_url`.
- **No LinkedIn scraping.** `common/http.py` refuses `linkedin.com` outright.

## Before spending credits

Hunter credits are finite and the free tier is small. Any workflow step that can
spend one says so, and the dashboard asks you to confirm a cap first. If you are
re-running something after a failure, check whether the previous attempt already
spent the credit: `contacts/hunter_usage.csv` records skips as well as calls.
