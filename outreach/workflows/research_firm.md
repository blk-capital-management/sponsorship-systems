# Research a firm

**Objective.** Produce a structured, fully sourced artifact about a firm, so that
drafting has something citable to work from. Drafting never browses; this is the
only stage that touches the open web.

## Inputs

A target with a verified `domain`. Nothing else.

## Tool

```
python research/fetch.py --firm "General Atlantic"     # one firm
python research/fetch.py --all                          # every row in targets.csv
python research/fetch.py --firm "..." --refresh         # ignore the crawl cache
```

Dashboard: select firms in the pipeline, then "2. Run research".

## What it does

Crawls the firm's **own domain only**, taking the single best matching page per
category and going no deeper. Four categories by design: `careers`,
`university_recruiting`, `values_culture`, `news`. Rate limited to 1 request per
second per domain, and it respects robots.txt.

Output goes to `research/out/<slug>.json`, validated against
`research/schema.json`.

## Output

The fields that matter downstream:

- `alignment_hooks` — up to 3 claims, each with a `source_url`. **These are the
  only things drafting may say about the firm.**
- `confidence` — `high`, `medium`, or `low`, scored by how many distinct
  categories yielded citable content.
- `gaps` — what was not found, in plain language. This is what you read when
  deciding whether to chase a firm manually.

## Edge cases

| Situation | What happens |
|---|---|
| Low confidence, or zero hooks | Routed to the manual queue and **stopped**. No draft can be written. |
| Site blocks the crawler | Recorded as a gap. Try `--refresh`, and check `common/identity.py` for the bot-block signals. |
| JS-only site | Text extraction returns almost nothing. Playwright fallback exists but is off by default; enable `research.playwright_fallback` in settings. |
| No domain | Cannot run. Resolve the intake manual-queue item first. |
| Batch, one firm fails | The rest still complete. The failure is recorded per target in the manual queue. |

A low-confidence result is a real answer, not an error. Writing a generic
paragraph for a firm with nothing citable is exactly what rule 1 forbids, so the
system routes it to a human instead.

## Cost

Free. No paid API is involved, and crawls are cached on disk by content hash, so
re-running costs nothing but time.

## Next

[find_contacts.md](find_contacts.md).
