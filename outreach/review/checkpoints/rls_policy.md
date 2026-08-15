# Phase G Supabase RLS policy

Status: complete. SQL passed embedded PostgreSQL verification, migration `001`
is applied to the hosted `blk-bridge` project, and live denial evidence is
recorded below.

## Identity boundary

- `allowed_users` contains exactly two configured identities:
  `jamari@blkcapitalmanagement.org` maps to `jamari`, and
  `folakunmi@blkcapitalmanagement.org` maps to `fola`.
- An `auth.users` trigger rejects every identity absent from that allowlist and
  creates or refreshes its matching `profiles` row.
- The browser has password login only. There is no signup route or signup UI.
  Public email signup must also remain disabled in Supabase Auth settings.
- `current_owner()` resolves the caller's lane from `auth.uid()` and `profiles`.

## Table policies

| Table | Authenticated restriction |
|---|---|
| `allowed_users` | No authenticated or anonymous table grant. Server provisioning only. |
| `profiles` | A user can select only the row whose `user_id = auth.uid()`. No direct insert, update, or delete. |
| `targets` | Select, insert, update, and delete require `owner = current_owner()`. Insert also requires `created_by = auth.uid()`. |
| `crm_records` | No authenticated or anonymous table grant and no policy. Read-only server status-derivation snapshot. |
| `research_artifacts` | Select, insert, update, and delete require `owner = current_owner()`. A trigger also requires the row owner to equal its target owner. |
| `contacts` | Select, insert, update, and delete require `owner = current_owner()`. A trigger also requires the row owner to equal its target owner. |
| `manual_queue` | Select, insert, and update require `owner = current_owner()`. There is no authenticated delete grant. Target-linked rows must match the target owner. |
| `action_runs` | Select requires `owner = current_owner()`. Insert and update also require `actor_id = auth.uid()`. There is no authenticated delete grant. |
| `cross_owner_confirmations` | A caller can select only confirmations they created. Insert requires `actor_id = auth.uid()`, `actor_owner = current_owner()`, a different `target_owner`, a server-current timestamp, blank access/consumption timestamps, and the exact confirmation sentence constructed from both owners and the target slug. No authenticated update or delete grant. |
| `drafts` | A caller can select only `owner = current_owner()`. There is no direct authenticated insert, update, or delete grant; validator-passing creation and review happen only through restricted functions. |
| `review_events` | A caller can select only `owner = current_owner()`. There is no direct authenticated write grant. The review function writes the event and a trigger enforces the draft owner. |
| `hunter_usage` | No authenticated or anonymous table grant and no policy. The server-only audited Hunter guard writes this table. |

RLS is enabled on all twelve tables. `anon` has all table privileges revoked.
The server secret may use `service_role` only for explicit seed/provisioning,
read-only CRM status derivation, and Hunter audit operations. Normal dashboard
data requests carry the caller's Auth JWT so Postgres RLS remains the boundary.

## Restricted functions

- `save_validated_draft(target_id, payload)` requires the target in the caller's
  lane, exact owner and firm slug agreement, and validator status `pass`.
- `review_draft(draft_id, action, reason)` requires the draft in the caller's
  lane, permits only pending drafts, and requires a reason for rejection.
- `cross_owner_draft_context(confirmation_id)` requires a caller-created,
  unaccessed and unused confirmation no older than ten minutes, records its
  single context access, and returns only the explicitly confirmed target's
  drafting context.
- `save_cross_owner_draft(confirmation_id, payload)` repeats the identity and
  validator checks, stores the draft in the target owner's lane, and consumes
  the confirmation exactly once.
- Execute privilege for these functions is revoked from `public` and `anon` and
  granted only to `authenticated`.

## Verification evidence

- Local PostgreSQL parser: accepted all 135 top-level migration statements.
- Embedded PostgreSQL execution: migration applied successfully with twelve
  RLS-enabled tables and twenty-three policies. The Auth trigger created exactly
  the two expected profiles. Under Jamari's authenticated role, the direct Fola
  target query returned zero rows. PostgreSQL also denied a third Auth identity,
  a direct Jamari-to-Fola target insert, direct authenticated draft insertion,
  a non-exact cross-owner confirmation, and a second access using the same
  confirmation. The explicitly confirmed context returned only the named Fola
  target, as designed. A validator-passing own-lane draft stored successfully;
  rejection without a reason was denied and a reasoned rejection was logged.
  A confirmed cross-owner draft stored in Fola's lane, was invisible to Jamari,
  visible to Fola, retained Jamari as the creating actor, and rejected a second
  save attempt with the consumed confirmation.
- Local acceptance suite: 436 passed, including complete RLS-table coverage,
  exact two-account allowlisting, no anonymous grants, no direct draft writes,
  cross-owner expiration/consumption, and defense-in-depth rejection of a
  deliberately leaked cross-owner row. Batch failures also preserve successful
  rows, finalize their action log, and route owned failures to the visible
  manual queue.
- Live Supabase migration: local `001` and remote `001` match in the Supabase
  migration history.
- Live Auth boundary: public signup is disabled. `auth.users` contains exactly
  `jamari@blkcapitalmanagement.org` and
  `folakunmi@blkcapitalmanagement.org`, with exactly two matching profiles.
- Live cross-owner reads: Jamari's JWT returned 19 Jamari targets and zero Fola
  targets. Fola's JWT returned 9 Fola targets and zero Jamari targets.
- Live cross-owner writes: direct Jamari-to-Fola and Fola-to-Jamari target
  inserts both returned HTTP 403. A service-role follow-up found zero persisted
  probe rows.
- Hosted dashboard verification: `/api/state` on the protected Vercel preview
  returned Jamari's identity, 19 Jamari targets, and no cross-owner target rows.
