# Resolve a manual queue item

**Objective.** Clear the exceptions, so the queue stays worth reading.

A queue that only grows stops being read, which defeats the point of routing work
to it. Items were once cleared only when a later run happened to succeed;
anything handled by hand stayed forever. This workflow is the way out.

## What lands here

| `source_stage` | Meaning |
|---|---|
| `intake` | Added with no domain. Research cannot start. |
| `research` | Low confidence, or no citable alignment hook. |
| `derive_status` | Contact status derivation failed for this target. |
| `contacts` | Contact discovery failed. |

Each item carries a `reason` and a `gaps` list saying what was missing. Read them
before acting; they are written to be read at speed.

## How to run it

Dashboard → Manual queue. For each item, do the underlying work first, then
resolve it with a note.

| Reason | What to do |
|---|---|
| Domain missing | Find the firm's real domain, correct the target, then resolve. |
| Research confidence low | Look at the site yourself. If there is something citable the crawler missed, note it. If there genuinely is not, that is a real answer: say so and resolve. |
| No alignment hooks | Same. A firm with nothing public to align to may not be worth outreach this cycle. |
| Stage failed with an error | Read the detail in `gaps`. Fix the cause, re-run the stage, then resolve. |

## The note is required

You cannot resolve an item without one. The database enforces it:
`resolve_manual_queue_item` raises if the note is blank. The note is the only
record of what a human decided and why, and it is the thing that makes the queue
auditable rather than just empty.

Write what you did, not that you did it. "Domain was bgo.com, not
bentallgreenoak.com; corrected and re-ran research" is useful. "Fixed" is not.

## Edge cases

| Situation | What happens |
|---|---|
| Already resolved | Raises. Items resolve once. |
| Another owner's item | Not visible. RLS scopes the queue to your lane. |
| Same target fails the same stage twice | Updates the open item rather than adding a second. One open item per target and stage. |
| Resolved but the underlying problem remains | It comes back on the next run. That is intended. |
