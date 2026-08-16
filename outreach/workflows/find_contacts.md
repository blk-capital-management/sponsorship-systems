# Find contacts

**Objective.** Identify a named recruiting or human capital contact and a
deliverable address, spending as few Hunter credits as possible.

**This is the only stage that spends money. Read the gate section before running
it on a large selection.**

## Inputs

A target whose research produced at least one alignment hook and confidence above
`low`. The gate check is repeated inside the discovery core, so a low-confidence
firm cannot reach a provider even if a new orchestration path forgets to check.

## Tool

```
python contacts/discover.py --firm "General Atlantic"
python contacts/verify.py  --firm "General Atlantic"
```

Dashboard: select firms, then "3. Find contacts". You are shown a projected
credit range and must confirm a hard cap before anything runs.

## The pre-Hunter gate

Before any paid call, `contacts/gate.py` decides whether the firm needs one at
all. It **skips** when all three hold:

```
contact_status IN (existing_partner, lapsed_partner)
AND has_known_contact = TRUE
AND contact_needs_refresh = FALSE
```

A lapsed partner flagged for refresh still proceeds, because a contact who went
quiet after the contract ended is the one case where a credit is worth spending.
A missing or unrecognized `contact_status` also proceeds: a gate that failed
closed on bad data would silently stop finding contacts.

Every skip is logged to `contacts/hunter_usage.csv` as a zero-credit line, so the
file answers both "where did the credits go" and "where did they not go".

## Spending less

In order of preference, the pattern comes from:

1. **A format you confirmed at intake.** Free. Skips the provider lookup outright.
   See [add_leads.md](add_leads.md).
2. **The firm's own website.** Free. A published address on a team or press page.
3. **Hunter domain-search.** Costs a credit. Only reached when 1 and 2 find
   nothing.

Budget controls, all in `config/settings.yaml`:

- `contacts.hunter.max_calls_per_run` — a hard stop, not a warning. Override per
  run with `MAX_HUNTER_CALLS_PER_RUN`.
- `contacts.hunter.domain_search_departments` — server-side filter, default
  `hr,executive`. The free plan returns 10 addresses per domain, so an unfiltered
  call spends the whole allowance on investment professionals who are never the
  target.
- `contacts.verification.min_score` — default 80. Addresses below this are dropped.

`contacts/hunter_guard.py` enforces scope: domain-search and email-finder may
only be called with a domain that already has a target row. It writes its audit
row **before** the request, so a crash still leaves a record of the spend.

## Output

`contacts/out/<slug>_contacts.csv`, plus `_verified` and `_dropped` files. Each
row carries `title_rank`, `pattern_confidence`, `verification_score`, and a
`contact_provenance` string recording where it came from.

## Edge cases

| Situation | What happens |
|---|---|
| No pattern found anywhere | No address is produced. A guess is never written. |
| Conflicting patterns at one domain | Confidence is lowered and the conflict recorded. |
| Every address scores below threshold | All dropped, with reasons in `_dropped`. |
| HTTP 429 from Hunter | Recorded, **not retried**. The credit is spent. Wait before re-running. |
| No API key | The stage runs and simply produces fewer addresses. |

## Next

[generate_draft.md](generate_draft.md).
