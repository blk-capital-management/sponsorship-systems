# Phase G Checkpoint: Hosted Dashboard and Owner Isolation

Date: 2026-08-14

Status: complete. Phase H and Gmail are untouched.

## Hosted services

- Supabase project: `blk-bridge` (`roylctgijprlostabrtm`), East US.
- Vercel project: `blk-capital-management1/blk-bridge`.
- Canonical deployment: https://blk-bridge.vercel.app
- Production deployment: `dpl_6a8AJFye2sWTCg4Ada2sWfiAPTTJ`, target
  `production`, status `Ready`.
- Protected preview: https://blk-bridge-9s91z9cet-blk-capital-management1.vercel.app
- Preview deployment: `dpl_E6MMDREvMCb1R5Xcu9EPMfsoeWcR`, target
  `preview`, status `Ready`.
- The preview requires BLK's Vercel team SSO before the app's own Supabase login.

Vercel automatically assigned the project's first deployment to production
without Production environment variables. That caused `/api/config` to return
503 at the canonical URL. On user acceptance testing, the four existing Phase G
runtime variables were copied from Preview to Production and the corrected
build was deployed. The login client now stays disabled until it receives and
validates the public Supabase configuration, so a configuration failure cannot
be masked by a null-property exception.

## Authentication boundary

- Public Supabase signup is disabled.
- Anonymous sign-in remains disabled.
- Exactly two Auth users exist:
  `jamari@blkcapitalmanagement.org` and
  `folakunmi@blkcapitalmanagement.org`.
- Exactly two matching `profiles` rows exist, mapped to `jamari` and `fola`.
- Both final credentials are stored in the macOS login Keychain under
  the service name `BLK Bridge Preview`. No password appears in source, shell
  output, this checkpoint, or Vercel logs.
- Both final credentials returned HTTP 200 from Supabase password login.
- The app exposes only the Supabase URL and publishable key to the browser. The
  legacy `service_role` key is stored as a sensitive Vercel Preview and
  Production variable.
  It is required because this project's newly issued `sb_secret` key returned
  HTTP 401 from PostgREST in both supported header forms during live testing.

To retrieve a credential, open Keychain Access and search for
`BLK Bridge Preview`, then choose the entry for the required email address.

## Migration and seeded data

Supabase migration history reports local `001` and remote `001` as applied.
The idempotent seed imported the reviewed local artifacts and left the CRM
workbook unchanged.

| Table | Live rows |
| --- | ---: |
| `allowed_users` | 2 |
| `profiles` | 2 |
| `targets` | 28 |
| `crm_records` | 139 |
| `research_artifacts` | 27 |
| `contacts` | 2 |
| `manual_queue` | 9 |
| `action_runs` | 0 |
| `cross_owner_confirmations` | 0 |
| `drafts` | 4 |
| `review_events` | 1 |
| `hunter_usage` | 9 |

The first seed imported eight Hunter audit rows. Authenticated preview
validation added one zero-credit `account` balance check, which produced the
ninth audited row. No contact-search credit was spent.

An immediate seed replay preserved all existing records and reported zero new
drafts, zero new manual-queue rows, and zero new Hunter rows.

## Live RLS proof

The denial proof used real access tokens issued to both hosted Auth users.

| Caller | Own targets | Other-owner targets | Cross-owner insert | Persisted probe rows |
| --- | ---: | ---: | ---: | ---: |
| Jamari | 19 | 0 | HTTP 403 | 0 |
| Fola | 9 | 0 | HTTP 403 | 0 |

The hosted dashboard then called `/api/state` with Jamari's token. It
returned the Jamari profile, 19 targets whose only owner was `jamari`, four
drafts, four visible manual-queue rows, and zero cross-owner target rows.

## Deployment validation

- `/health` returned `{"status":"ok","service":"blk-bridge"}`.
- `/` served the BLK Bridge password-login page.
- `/api/config` returned the expected Supabase URL and a non-empty publishable
  key. The response did not contain a service key.
- `/api/state` succeeded with a real Jamari JWT and preserved the owner lane.
- Direct anonymous access to the preview redirects to Vercel SSO.
- The canonical production URL completed Supabase password authentication and
  returned the same owner-scoped state with zero cross-owner targets.

## Interface acceptance

The interface was rebuilt around a guided five-step workflow: overview, firm
pipeline, draft review, manual queue, and controlled cross-owner exceptions.
Local and production browser checks confirmed:

- versioned CSS and JavaScript assets returned HTTP 200;
- native HTML hidden state prevents the authenticated workspace from appearing
  before CSS or authentication is ready;
- the login and all five workspace views rendered with their intended styles;
- the overview recommended the next action from live owner-lane counts;
- pipeline search, status filters, selection-aware batch controls, and the
  draft modal worked without changing live records;
- desktop and mobile layouts had no horizontal page overflow;
- mobile navigation opened and closed correctly;
- Jamari's authenticated production view showed 19 targets and three pending
  draft decisions; and
- the browser reported no page or console errors.

## Final verification

- Full acceptance suite: 437 passed, 1 deprecation warning.
- The existing `.venv` initially could not create pytest capture files because
  the disk was at 100% capacity. After removing only the 37 MB temporary
  Supabase CLI source clone created during this task, the suite passed under the
  bundled Python 3.12 runtime while reusing the project's installed packages.
- Remote schema dumping was not used because the Supabase CLI requires Docker
  Desktop for `db dump`. Hosted migration history, exact live table counts,
  real JWT reads, denied writes, and zero persisted probes provide the remote
  evidence instead.
- Local embedded PostgreSQL evidence remains: twelve RLS-enabled tables,
  twenty-three policies, exact allowlisting, one-time cross-owner confirmation,
  and denied direct cross-owner access.

Phase H, Gmail draft creation, Gmail OAuth, and all send behavior remain
unchanged and unconfigured in Vercel.
