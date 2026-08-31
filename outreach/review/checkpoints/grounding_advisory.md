# Checkpoint: research grounding becomes advisory

Date: 2026-08-31
Branch: `loosen-draft-grounding-guardrails`
Scope: the "Generate for {firm}" cold-prospect draft workflow.

## Why

Fola was blocked from creating drafts by two gates firing far more often than
intended. Research legitimately returns nothing for firms with a thin public
footprint, and the paragraph-to-hook matcher produced constant false positives on
paraphrased language written by a person who already knows the firm. The tool was
adding friction to a ninety-second task.

## Guardrails relaxed

| Guardrail | Was | Now |
|---|---|---|
| Hook requirement | Zero sourced hooks raised `NoUsableHooksError`, wrote a `manual_queue.csv` row, and returned HTTP 422. The client blocked submit before sending. | Zero hooks is a supported state. A draft is produced and recorded as `no_research_available`. No manual-queue write from the draft path. |
| Explicit empty hook selection | `resolve_selected_hooks` raised `ResearchHookSelectionError`. | Resolves to no hooks. `None` still selects all for CLI callers. |
| Paragraph-to-hook matching | An untraceable claim was a `violation` and refused the draft. | An advisory. The full sentence-level message is kept and surfaced; it does not block. |
| Paragraph has no sourced sentence at all | `validate_email_body` refused with "contains no claim traceable to a firm_claim_source". | Replaced by `grounding_status: "ungrounded"`. |
| Sentence count 2 to 3 | A `violation`. | An advisory. A one-sentence paragraph generates. |
| Contact discovery on a zero-hook artifact | `contacts/discover.py` and the contact-run eligibility filter skipped the firm, so a no-research firm could never get a contact and the draft stalled anyway. | Only low confidence or a missing artifact skips. A neutral notice says discovery ran without stored research hooks. |

### The surviving confidence threshold, on the record

`contacts/discover.py:371` is now the only filter standing between a
zero-research firm and contact discovery. It is a **string enum comparison**,
`artifact.get("confidence") == "low"`, not a numeric score. The number behind
that enum comes from `research/fetch.py::score_confidence`, which counts
**distinct non-landing page categories that yielded citable content**, against
the thresholds in `config/settings.yaml:38-41`:

```yaml
confidence:
  high_min_categories: 3
  medium_min_categories: 2
  low_max_categories: 1
```

So the surviving gate is: **at least 2 distinct citable page categories, and no
blocking reason.** `"low"` means 0 or 1 categories, or any blocking reason (for
example a domain identity mismatch), which pins the artifact to low regardless
of how much text was extracted.

What it was implicitly gated behind before: `not artifact.get("alignment_hooks")`,
that is, the presence of at least one alignment hook of **any** kind. Note that
the old discovery gate counted raw `alignment_hooks`, unlike `drafts/generate.py`,
which counted only hooks carrying a `firm_claim_source`. The two checks were
largely redundant, since hooks are extracted from the same evidence categories,
but not entirely: an artifact can reach `medium` on 2 categories and still yield
0 hooks when extraction finds no usable sentence. That gap is precisely the case
this change unblocks, and confidence is now the only thing holding the line.


The validator check label `firm_claims_trace_to_research_hooks` was replaced with
`human_authored_firm_paragraph`, because the old label would have shown a green
tick on an ungrounded draft.

## Guardrails preserved

- **No auto-send.** No transmit path added. `tests/test_no_send_path.py` unchanged
  and passing. The last step is still a Gmail compose URL a person clicks.
- **The firm paragraph stays human-authored.** `compose_firm_paragraph` remains
  unwired and the `del anthropic_client` line stands. Zero hooks yields an empty
  field waiting on a human, never model-written filler.
- **`blk_facts.json` write-guard.** `tests/test_blk_facts_immutable.py` unchanged.
- **Strict merge-field resolution.** `{member_count}`, `{university_count}`,
  `{applications_last_cycle}`, `{conference_dates}`, `{conference_city}` and
  `{conference_venue}` still resolve from `blk_facts.json`, and an unresolved or
  drifted field still halts generation. Covered by two new tests on the zero-hook
  path specifically, so the relaxation cannot leak into stat validation.
- **Em dash (rule 4) and house tone.** Still violations. Still block.
- **Empty or whitespace-only paragraph.** Still blocks, now checked explicitly
  before rendering.
- **Verified contact requirement.** Unchanged in both `generate_draft` and
  `generate_owner_draft`.
- **Verbatim-copy guard.** `find_verbatim_hook_run` still blocks an 8+ word run
  copied from a hook. It catches copying, not grounding.
- **Provenance URL-leak guard, attachment denylist, outcome-promise denylist.**
- **Artifact integrity.** An unknown or duplicated `research_hook_id` is still
  rejected. A browser still cannot submit a replacement URL or hook body.
- **Two-user lane discipline.** Owner match, `X-Blk-Lane`, and RLS untouched.
- **Research-stage manual-queue routing** in `research/fetch.py` is unchanged and
  remains the informational signal that a firm has nothing citable.

## Audit trail

Every generated cold review item now carries, under
`fields.firm_paragraph_provenance`:

- `hook_count` (integer, may be 0) — sourced hooks available in the artifact
- `hooks_used` (list, may be empty) — the hook IDs actually selected
- `grounding_status` — `grounded`, `ungrounded`, or `no_research_available`
- `advisories` — the demoted messages, in full

`grounding_status` is mirrored on `validator_results` for display and shown as a
label on the draft card and in the review detail header. `validator_results.status`
stays `"pass"`; `save_validated_draft` still refuses anything else. No migration
was needed: the metadata rides in the existing `fields` jsonb column.

## Two adjacent risks checked before ship

**`None` vs `[]` in the hook resolver.** `resolve_selected_hooks` reads `None` as
"select every sourced hook" (the CLI contract) and `[]` as "select none". These
fail in opposite directions. There is exactly one production caller,
`drafts/generate.py:721`. The API path is `app.py:378` ->
`generate_owner_draft` -> `_generate_record` -> `generate_draft`, and
`DraftRequest.supporting_hook_ids` is `Field(default_factory=list)`, so it can
never emit `None`. No caller passes `None` meaning "no hooks" today. The two
dashboard functions did still default the parameter to `None`, however, which
would have turned a future omitted argument into "attach every hook". Both now
collapse to `[]` on entry, with a regression test.

**Merge fields are preview-only in the modal.** The literal `{member_count}` and
friends visible in the generate modal are client-side display: `renderDraftPreview`
writes them into `#draft-preview` innerHTML wrapped in `.preview-auto` spans, and
the POST body carries only `target_id`, `contact_id`, `firm_specific_paragraph`
and `supporting_hook_ids`. The preview is never submitted. Server-side,
`render_email_body` uses `str.format(**fields)`, so a template token with no
matching key raises `DraftGenerationError`, and `_dig` raises `KeyError` for any
`blk_facts.json` path that is absent. A rendered `email_body` contains no `{` at
all. An unresolved merge field cannot reach a generated draft.

## Tests

`.venv/bin/python -m pytest` — 560 passed.

Inverted: `test_zero_hook_artifact_produces_no_draft`,
`test_hooks_with_no_firm_claim_source_count_as_zero_hooks`,
`test_invented_firm_fact_fails`, `test_invented_number_fails`,
`test_hooks_without_source_url_block_everything`, `test_sentence_count_bounds`,
`test_citadel_unsupported_claims_fail_with_actionable_sentence_message`,
`test_unsupported_healthcare_claim_reports_actual_spans`,
`test_unsupported_largest_reports_phrase_and_separate_rule_type`.

Added coverage for: zero hooks plus a valid paragraph succeeds; hooks present plus a
non-matching paragraph succeeds as `ungrounded`; a matching paragraph is `grounded`;
an empty paragraph still fails; a missing contact still fails; an unresolved and a
drifted merge field still fail on the zero-hook path; a one-sentence paragraph is
allowed; an empty hook selection resolves cleanly; an unknown hook ID still raises;
the zero-hook path sends nothing; and the client carries no hook gate and no
confirmation step in the draft submit handler.
