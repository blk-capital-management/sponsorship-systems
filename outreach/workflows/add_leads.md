# Add leads

**Objective.** Get a sourced firm into your owner lane with whatever you know
about it, so research can start.

## Inputs

Only the firm name is required. Everything else is optional and can be filled in
later.

| Field | Why it matters |
|---|---|
| Firm name | Required. Becomes `firm_slug`, the join key across every stage. |
| Website | The domain is derived from this when you leave the domain blank. |
| Domain | **Research cannot run without it.** No domain routes the firm to the manual queue at intake. |
| LinkedIn | Reference only. Never fetched. |
| Category | Decides which contact titles get targeted first. See below. |
| Region | `US` or `EMEA`. Selects the prospectus later. |
| Tier target | Diamond / Platinum / Gold / Silver. Sponsorship level you are aiming at. |
| Priority | 1 highest to 3 lowest. Orders the pipeline view. |
| Email format | If you already know it, this saves a Hunter lookup. See below. |
| Notes | Anything a future you would want, such as a warm intro path. |

## How to run it

**Dashboard, one firm.** Firm pipeline → Add firms → "One firm". Use this when
you are researching a single lead and have detail to record.

**Dashboard, a batch.** Firm pipeline → Add firms → "Paste a batch". One firm per
line, pipe separated:

```
General Atlantic | generalatlantic.com | US | Growth Equity | 1
```

To use other columns or a different order, start with a header line:

```
firm | website | linkedin | category | email_format
```

Max 25 firms per batch.

**Command line.** Append rows to `data/targets.csv` directly. The dashboard and
the CSV are parallel stores; `scripts/seed_supabase.py` pushes the CSV into
Supabase idempotently.

## Category

Pick from the list in `config/firm_categories.json`. It is not cosmetic: firms
marked `human_capital_first` get Human Capital titles promoted above campus
recruiting titles, because at PE and credit firms Human Capital sits closer to
the budget.

Common spellings are folded automatically, so "private equity" becomes `PE`. A
category outside the list is still accepted and stored as you typed it, but you
get a warning, and title routing will not apply to it. If you find yourself
typing the same new category repeatedly, add it to the config file instead.

## Email format, and the credit it saves

If you have seen a real published address at the firm, record the pattern:

```
{f}{last}@generalatlantic.com
```

Tokens are `{first}`, `{last}`, `{f}`, `{l}` and their capitalized forms.

**A source URL is required.** Paste the public page you read the address off. An
unsourced pattern is not a weaker pattern, it is not a pattern, and it will be
rejected. This is rule 1 applied to contacts.

The payoff: a confirmed format outranks anything the system could infer, so
`find_contacts` skips the provider pattern lookup for that firm entirely. The
form shows you the address the pattern produces before you submit.

## Output

One row per firm in `targets`, with `crm_status = New Lead`,
`relationship = prospect`, `contact_status = cold_prospect`.

The result panel lists every row and what happened to it:

- **Added** to your lane.
- **Added with a warning**, when the firm looks similar to one you already have,
  or the category is unrecognized. Both are judgment calls, so the lead is added
  and you decide.
- **Skipped**, with the reason. The rest of the batch still goes in.

## Edge cases

| Situation | What happens |
|---|---|
| Firm already in your lane | Skipped. `firm_slug` drops generic suffixes, so "Sixth Street" and "Sixth Street Partners" are the same firm. |
| Domain already on another firm | Skipped. One domain, one target. |
| Same firm twice in one batch | The first is added, the second skipped. |
| No domain | Added, then routed to the manual queue. Find the domain and resolve the item. |
| Email format with no source | Rejected before anything is written. Add the source URL. |
| Malformed email format | Rejected, with the tokens listed. |
| LinkedIn URL that is not LinkedIn | Rejected. |

**One bad row never discards the batch.** Rows are inserted one at a time
precisely so that pasting thirty leads with one duplicate adds twenty nine.

## Next

[research_firm.md](research_firm.md), for every firm that has a domain.
