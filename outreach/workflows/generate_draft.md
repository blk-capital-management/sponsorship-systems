# Generate a draft

**Objective.** Turn a research artifact plus a template into an email a human can
review, where every firm-specific sentence traces to a source URL.

## Inputs

- A research artifact with at least one alignment hook
- A contact, or a decision to draft without one
- `config/blk_facts.json` for any BLK number
- A template chosen by relationship status

## Tool

```
python drafts/generate.py --slug general_atlantic
python drafts/generate.py --slug general_atlantic --paragraph-file para.txt
```

It takes the **slug**, not the firm name, because the slug is the join key that
lines up with `research/out/<slug>.json`. Get it from `common/slugify.py::firm_slug`
or from the pipeline row.

Dashboard: the "Draft" action on a pipeline row.

## Templates

`drafts/routing.py` picks one by `contact_status`:

| Status | Template |
|---|---|
| `cold_prospect` | `templates/cold_prospect.md` |
| `existing_partner` | `templates/warm_renewal.md` |
| `lapsed_partner` | `templates/recovery.md` |

## The checklist

Every one of these is enforced in code, not by convention. A draft that fails any
of them is not written.

- [ ] **Every firm claim traces to a `source_url`.** `common/provenance.py`
      rejects any sentence whose entities do not map to a sourced alignment hook.
- [ ] **No em dashes.** `assert_no_em_dash` runs on every generated string.
- [ ] **No invented BLK numbers.** Every statistic comes from `blk_facts.json`.
      Preflight blocks on null facts.
- [ ] **No auto-send.** This stage writes a file. Nothing more.
- [ ] **Research and drafting stay separate.** `generate.py` takes a JSON path,
      never a URL. This is what stops hallucinated firm facts.

## A note on the model

`compose_firm_paragraph` is the only LLM call site in the system and it is
**deliberately unwired** (see the `del anthropic_client` line in `generate.py`).
Firm paragraphs are composed deterministically from the sourced hook sentences.

For a cold prospect the dashboard asks you to write the firm-specific paragraph
yourself. That is the intended workflow, not a missing feature: a human who has
read the research writes better and safer copy than a paraphrase of it.

## Output

`review/drafts/<slug>.json` and `<slug>.txt`, with an evidence block listing every
claim and the URL behind it. Status starts at `pending_review`.

## Edge cases

| Situation | What happens |
|---|---|
| No usable hooks on a cold target | `NoUsableHooksError`. Queued for manual review, no draft written. |
| A validator fails | `DraftGenerationError`. Nothing is written. |
| Drafting for another owner's target | Requires the cross-owner flow and a typed confirmation. |

## Next

[review_and_send.md](review_and_send.md).
