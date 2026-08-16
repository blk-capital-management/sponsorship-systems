# Review and send

**Objective.** Read the complete record, approve or reject, and send it yourself.

## The one rule that shapes this whole stage

**Bridge never sends email.** There is no code path that transmits mail, the Gmail
scope is `gmail.compose` which cannot send, and `tests/test_no_send_path.py`
AST-scans the package on every test run to keep it that way.

What you get is a prefilled Gmail compose window. You read it, you click send.

Ignore any older description of a `send/to_gmail_drafts.py` stage. It never
existed.

## Inputs

A draft with status `pending_review`.

## How to run it

Dashboard → Draft review. For each draft you see the recipient, the full body,
the evidence block, and the validation results.

**Read the evidence block before approving.** It lists every firm-specific claim
next to the URL it came from. That is the check the whole system is built around,
and it is the one thing a machine cannot do for you.

Then either:

- **Approve.** Unlocks the compose link.
- **Reject**, with a reason. The reason is recorded in `review_events`.

## Sending

Once approved, the draft shows a compose link. It opens Gmail with the recipient,
subject, and body already filled in. Attach the regional prospectus if the firm
is EMEA. Send it.

Then click **Mark as sent** in Bridge. This is what keeps the pipeline honest:
`sent_at` and `sent_by` are recorded, and the send counts against the daily cap.

## Daily cap

40 per mailbox per day, from `send.daily_cap_per_mailbox`. This is a hard stop,
not a warning. Exceeding it damages domain reputation, which would degrade
deliverability on live sponsor threads that matter more than any new outreach.

## Draft states

```
pending_review  ->  approved   ->  sent
                ->  rejected
```

Every transition writes a row to `review_events` with the actor and the reason.

## Edge cases

| Situation | What happens |
|---|---|
| Compose link before approval | Locked. Approval is the gate. |
| Approving another owner's draft | Requires the cross-owner flow and a typed confirmation. |
| Sent outside Bridge | Still mark it sent, or the cap and the pipeline drift from reality. |
| Rejected draft | Fix the input and regenerate. Drafts are not edited in place. |
