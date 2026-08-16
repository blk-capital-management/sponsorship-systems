-- Capture what a human already knows when a lead is first sourced.
--
-- Intake previously accepted firm, domain, region, firm_type, tier_target,
-- priority, and notes. That is not the shape of real sourcing. You usually find
-- a firm's website and LinkedIn first, sometimes a confirmed email format, and
-- often the domain only later. Anything the system could not store was retyped
-- elsewhere or lost.
--
-- email_format matters most. When the format is already known, contacts/gate.py
-- can skip Hunter's domain-search entirely and spend credits only on
-- verification. Rule 1 applies to it exactly as it applies to a firm claim: a
-- pattern without a source URL is not a weaker pattern, it is not a pattern. The
-- check constraint below enforces that pairing at the storage layer rather than
-- trusting every caller to remember it.
--
-- All columns are additive with defaults, so existing rows, scripts/seed_supabase.py,
-- and data/targets.csv keep working untouched.

begin;

alter table public.targets
  add column if not exists website                 text not null default '',
  add column if not exists linkedin_url            text not null default '',
  add column if not exists email_format            text not null default '',
  add column if not exists email_format_source_url text not null default '';

comment on column public.targets.website is
  'Firm site as found while sourcing. domain is derived from it when blank.';
comment on column public.targets.linkedin_url is
  'Reference only. Never fetched: common/http.py refuses linkedin.com at the request layer (rule 3).';
comment on column public.targets.email_format is
  'Confirmed address pattern, e.g. {f}{last}@acme.com. Lets contacts/gate.py skip Hunter domain-search.';
comment on column public.targets.email_format_source_url is
  'Public page the format was read off. Required whenever email_format is set (rule 1).';

-- An unsourced pattern is not a pattern.
alter table public.targets drop constraint if exists targets_email_format_sourced;
alter table public.targets add constraint targets_email_format_sourced check (
  email_format = '' or email_format_source_url <> ''
);

commit;
