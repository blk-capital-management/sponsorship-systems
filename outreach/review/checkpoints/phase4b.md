# Phase 4b Checkpoint: Warm Renewal and Recovery Templates

Date: 2026-08-14

Status: complete and awaiting human approval. None of these drafts is approved.

## Integrity and verification

- `config/blk_facts.json` remained read only. Its human-approved `member_count` is `1500+`.
- The package-wide AST guard rejects write-mode access to `blk_facts.json`.
- The CRM workbook was opened read only and remained unchanged.
- `data/targets.csv` now carries the exact CRM-derived relationship fields and exact CRM row reference for each relationship target.
- `needs_warm_template.csv` and `needs_recovery_template.csv` each contain zero unresolved rows. They now receive only targets missing required relationship content.
- All 394 tests passed.
- Direct validator replay against all three on-disk JSON records passed.
- Each JSON `email_body` matches its `.txt` output.
- Each subject is `required_but_unset`.
- Each draft and contact record is owner-scoped to `jamari`.

The validator checks recorded as passing for every draft are:

1. No em dash, en dash, horizontal bar, or ASCII double-hyphen substitute.
2. Every BLK fact matches `config/blk_facts.json` exactly.
3. No contact provenance leaks into the email body.
4. No outcome-promise language.
5. No attachment claim.
6. Subject remains required but unset.
7. Contact and target owner lanes match.
8. Relationship claims trace to CRM-derived fields in `data/targets.csv`.
9. The internal CRM source reference does not appear in the email body.

## Sixth Street

Template: `templates/warm_renewal.md`

CRM source: `Jamari!r21`

Relationship claim trace:

- Greeting and addressed contact: `relationship_contact_name` and `relationship_contact_email`
- Gold partnership: `relationship_tier`
- Continuing relationship and Active status: `relationship_status`
- Internal evidence metadata: `relationship_record_id`, `relationship_expiration`, and `relationship_crm_source`

BLK claims trace separately to `member_count`, `universities`, `fall_conference.dates`, and `fall_conference.host` in `config/blk_facts.json`.

Validator result: PASS

Subject status: `required_but_unset`

```text
Hi Maddy,

Thank you for continuing Sixth Street's Gold partnership with BLK Capital Management. Our records list the partnership as Active, and I am reaching out to discuss the coming cycle with you.

BLK connects 1500+ members across 200+ universities in the US and EMEA. Continuing the partnership would keep your team connected to this community through year-round programming and our Fall Conference on November 12 and 13 in New York at Wells Fargo.

We would welcome the chance to discuss the next cycle. If it would be useful, I am happy to follow up with our full sponsorship prospectus ahead of a call.

Thank you for your time and consideration.

Best,
Jamari Myers
Co-Chair of Sponsorships, BLK Capital Management
```

## Advent International

Template: `templates/warm_renewal.md`

CRM source: `Jamari!r13`

Relationship claim trace:

- Greeting and addressed contact: `relationship_contact_name` and `relationship_contact_email`
- Platinum partnership: `relationship_tier`
- Continuing relationship and Active status: `relationship_status`
- Internal evidence metadata: `relationship_record_id`, `relationship_expiration`, and `relationship_crm_source`

BLK claims trace separately to `member_count`, `universities`, `fall_conference.dates`, and `fall_conference.host` in `config/blk_facts.json`.

Validator result: PASS

Subject status: `required_but_unset`

```text
Hi Kirsten,

Thank you for continuing Advent International's Platinum partnership with BLK Capital Management. Our records list the partnership as Active, and I am reaching out to discuss the coming cycle with you.

BLK connects 1500+ members across 200+ universities in the US and EMEA. Continuing the partnership would keep your team connected to this community through year-round programming and our Fall Conference on November 12 and 13 in New York at Wells Fargo.

We would welcome the chance to discuss the next cycle. If it would be useful, I am happy to follow up with our full sponsorship prospectus ahead of a call.

Thank you for your time and consideration.

Best,
Jamari Myers
Co-Chair of Sponsorships, BLK Capital Management
```

## Point72 Asset Management

Template: `templates/recovery.md`

CRM source: `Archive!r11`

Relationship claim trace:

- Greeting and addressed contact: `relationship_contact_name` and `relationship_contact_email`
- Prior Diamond partnership: `relationship_tier`
- Direct Not Renewing acknowledgment: `relationship_status`
- Budget-cut reason and inability to re-sponsor: `relationship_decline_reason`
- Internal evidence metadata: `relationship_expiration` and `relationship_crm_source`

BLK claims trace separately to `member_count`, `universities`, `fall_conference.dates`, and `fall_conference.host` in `config/blk_facts.json`.

Validator result: PASS

Subject status: `required_but_unset`

```text
Hi Kelli,

I am reaching out with Point72 Asset Management's prior Diamond partnership with BLK Capital Management in mind. Our records mark the relationship as Not Renewing and note: Budget cuts; cannot re-sponsor. I wanted to acknowledge that directly rather than write as if this were a first conversation.

BLK connects 1500+ members across 200+ universities in the US and EMEA. Our partners engage with this community through year-round programming and our Fall Conference on November 12 and 13 in New York at Wells Fargo.

If circumstances have changed, we would welcome a conversation about what re-engagement could look like. If it would be useful, I am happy to follow up with our full sponsorship prospectus ahead of a call.

Thank you for your time and consideration.

Best,
Jamari Myers
Co-Chair of Sponsorships, BLK Capital Management
```

## Required gate

Phase F must not begin until a human creates `review/checkpoints/phase4b_APPROVED.md`.
