# Phase G Storage Migration Checkpoint

Date: 2026-08-14

Status: architecture decision recorded before any pipeline integration code was changed.

## Decision

Supabase will become the concurrent source of truth for dashboard targets, CRM-derived relationship records, research artifacts, contacts, manual-queue entries, drafts, review events, Hunter usage, action runs, and cross-owner confirmations.

`data/targets.csv` remains the reviewed seed and an export-compatible local artifact. The initial migration imports it without changing its values. Existing command-line entry points continue to operate against local files so the validated offline workflow remains available.

This migration is necessary because Vercel Functions do not provide durable local writes and two users cannot safely share mutable CSV and JSON files across concurrent requests.

## Runtime shape

- A FastAPI application runs as a Vercel Python Function and serves the dashboard plus authenticated pipeline endpoints.
- Supabase Auth provides exactly two allowed identities: `jamari@blkcapitalmanagement.org` and `folakunmi@blkcapitalmanagement.org`.
- Public signup and anonymous sign-in remain disabled. A database allowlist and profile constraints prevent an unconfigured third identity from receiving an application owner lane even if an administrator creates an extra Auth user.
- Dashboard requests carry the signed-in user's Supabase access token. Normal database reads and writes use that user token so RLS, not application filtering, enforces owner isolation.
- A service credential is reserved for initial provisioning and read-only access to the CRM snapshot required by the existing status-derivation algorithm. It is never exposed to the browser and is not used for normal owner-scoped CRUD.
- Supabase's "automatically expose new tables" setting remains off. The migration grants only the exact operations required by the authenticated role, while `anon` receives no table privileges.

## Pipeline preservation

Only storage adapters and dashboard orchestration are added. The authoritative decision logic remains in its existing modules:

- Research continues through `research.fetch.crawl_firm()`, `build_artifact()`, and `validate_artifact()` with the existing identity checks, crawl budget, source namespaces, and manual-queue rule.
- Contact status continues through `scripts.derive_target_status.derive()` using read-only CRM rows.
- The pre-Hunter decision continues through `contacts.gate.evaluate_pre_hunter_gate()`.
- Contact discovery continues to use the existing title ranking, same-domain identity checks, licensed-provider guard, per-run Hunter cap, and verification threshold.
- Draft routing continues through `drafts.routing.route_target()`.
- Draft creation continues through `drafts.generate.generate_draft()`. Cold firm-specific paragraphs remain mandatory human input. Warm and recovery relationship fields remain CRM-derived.
- `config/blk_facts.json` remains the only BLK-fact source and is never opened in write mode.

The dashboard adapters pass Supabase records into these existing functions and persist returned records to Supabase. They do not recreate the rules in JavaScript or SQL.

## RLS boundary

Every user-facing table carries `owner` with the only valid values `jamari` and `fola`. Policies compare that field to the owner mapped to `auth.uid()` in the user's profile. Direct cross-owner reads and writes are denied.

Cross-owner draft generation is the sole planned exception. It requires a fresh, explicit confirmation record containing the actor, actor owner, target owner, action, target slug, and timestamp. Security-definer RPCs expose only the one confirmed draft context and accept only the resulting validated draft. They do not create a general cross-owner read path.

## Migration and rollback

1. Apply the idempotent SQL migration.
2. Create or invite the two allowed Auth users only.
3. Run the seed command, which imports current local artifacts and reads the CRM workbook without modifying it.
4. Compare row counts and owner counts to the local source files.
5. Test direct Jamari-to-Fola and Fola-to-Jamari reads; both must return zero rows or a permission denial.

Until those checks pass, the local CSV/JSON artifacts remain authoritative and no local file is deleted. Rollback is disabling the dashboard deployment and dropping the new tables; the validated offline pipeline remains intact.
