# Human Decision Required: BLK member-count fixture mismatch

Date detected: 2026-08-13

## Resolution

Resolved by Jamari Myers on 2026-08-13: keep the member count at `"1500+"` and
resume work. `config/blk_facts.json` remains unchanged. The stale shared test fixture
is authorized to be corrected to the human-approved value.

## Conflict

The human-maintained source of truth and an existing pytest fixture disagree:

- `config/blk_facts.json` has `member_count` set to `"1500+"`.
- `tests/conftest.py` has `member_count` set to `"300+"` in the shared `blk_facts` fixture.

The shipped preflight test also explicitly asserts that the authoritative value is
`"1500+"`, and the approved Balyasny draft uses `"1500+"`.

## Decision that was required

A human must determine how the stale shared fixture should be handled. No process in
this system may modify `config/blk_facts.json`, and this goal run must not edit either
the fact file or the disagreeing test merely to make them agree.

## Work halt

Phase 4b implementation had not begun when the conflict was detected. No templates,
routing logic, queues, generated drafts, or tests were changed before the human decision.
Work resumed only after the resolution above was provided.
