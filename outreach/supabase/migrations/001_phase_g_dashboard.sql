begin;

create extension if not exists pgcrypto;

create table if not exists public.allowed_users (
  email text primary key check (email = lower(email)),
  owner text not null unique check (owner in ('jamari', 'fola')),
  display_name text not null,
  gmail_sender text not null check (gmail_sender = lower(gmail_sender)),
  created_at timestamptz not null default now()
);

insert into public.allowed_users (email, owner, display_name, gmail_sender)
values
  ('jamari@blkcapitalmanagement.org', 'jamari', 'Jamari Myers', 'jamari@blkcapitalmanagement.org'),
  ('folakunmi@blkcapitalmanagement.org', 'fola', 'Folakunmi Awofisayo', 'folakunmi@blkcapitalmanagement.org')
on conflict (email) do update set
  owner = excluded.owner,
  display_name = excluded.display_name,
  gmail_sender = excluded.gmail_sender;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique check (email = lower(email)),
  owner text not null unique check (owner in ('jamari', 'fola')),
  display_name text not null,
  gmail_sender text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.targets (
  id uuid primary key default gen_random_uuid(),
  owner text not null check (owner in ('jamari', 'fola')),
  firm text not null,
  firm_slug text not null unique check (firm_slug ~ '^[a-z0-9_]+$'),
  domain text not null,
  region text not null default 'US',
  firm_type text not null default '',
  tier_target text not null default '',
  crm_status text not null default 'New Lead',
  priority smallint not null default 3 check (priority between 1 and 3),
  relationship text not null default 'prospect' check (relationship in ('prospect', 'existing')),
  notes text not null default '',
  contact_status text not null default 'cold_prospect'
    check (contact_status in ('cold_prospect', 'existing_partner', 'lapsed_partner')),
  has_known_contact boolean not null default false,
  contact_needs_refresh boolean not null default false,
  relationship_record_id text not null default '',
  relationship_tier text not null default '',
  relationship_status text not null default '',
  relationship_contact_name text not null default '',
  relationship_contact_email text not null default '',
  relationship_expiration date,
  relationship_decline_reason text not null default '',
  relationship_crm_source text not null default '',
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists targets_domain_lower_idx
  on public.targets (lower(domain)) where btrim(domain) <> '';

create table if not exists public.crm_records (
  id uuid primary key default gen_random_uuid(),
  firm text not null,
  firm_normalized text not null,
  tab text not null,
  row_number integer not null,
  status text not null default '',
  expiration_raw text not null default '',
  expiration date,
  contacts jsonb not null default '[]'::jsonb,
  emails jsonb not null default '[]'::jsonb,
  is_ledger boolean not null default false,
  record_id text not null default '',
  tier text not null default '',
  decline_reason text not null default '',
  imported_at timestamptz not null default now(),
  unique (tab, row_number)
);

create index if not exists crm_records_firm_normalized_idx
  on public.crm_records (firm_normalized);

create table if not exists public.research_artifacts (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null unique references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  confidence text not null check (confidence in ('high', 'medium', 'low')),
  hook_count smallint not null default 0 check (hook_count between 0 and 3),
  artifact jsonb not null,
  gaps jsonb not null default '[]'::jsonb,
  researched_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.contacts (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  name text not null,
  title text not null default '',
  title_rank integer,
  email text not null default '',
  pattern text not null default '',
  pattern_confidence numeric(4, 3),
  verification_score integer check (verification_score between 0 and 100),
  verification_status text not null default '',
  verification_provider text not null default '',
  contact_provenance jsonb not null default '{"internal_only":true}'::jsonb,
  dropped boolean not null default false,
  drop_reason text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists contacts_target_email_idx
  on public.contacts (target_id, email, name);

create table if not exists public.manual_queue (
  id uuid primary key default gen_random_uuid(),
  target_id uuid references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  firm text not null,
  firm_slug text not null,
  domain text not null default '',
  confidence text not null default 'low',
  reason text not null,
  gaps jsonb not null default '[]'::jsonb,
  source_stage text not null default 'research',
  resolved_at timestamptz,
  queued_at timestamptz not null default now()
);

create unique index if not exists manual_queue_open_target_stage_idx
  on public.manual_queue (target_id, source_stage) where resolved_at is null;

create table if not exists public.action_runs (
  id uuid primary key default gen_random_uuid(),
  owner text not null check (owner in ('jamari', 'fola')),
  actor_id uuid not null default auth.uid() references auth.users(id),
  action_type text not null check (action_type in (
    'batch_intake', 'derive_status', 'research', 'contact_preview',
    'contact_discovery', 'draft_generation'
  )),
  status text not null default 'running' check (status in (
    'waiting_confirmation', 'running', 'completed', 'failed', 'skipped'
  )),
  target_ids jsonb not null default '[]'::jsonb,
  credits_min integer not null default 0,
  credits_max integer not null default 0,
  credits_spent integer not null default 0,
  hunter_balance_before integer,
  details jsonb not null default '{}'::jsonb,
  error text not null default '',
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists public.cross_owner_confirmations (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid not null default auth.uid() references auth.users(id),
  actor_owner text not null check (actor_owner in ('jamari', 'fola')),
  target_owner text not null check (target_owner in ('jamari', 'fola')),
  action text not null check (action = 'generate_draft'),
  target_slug text not null,
  confirmation_text text not null,
  confirmed_at timestamptz not null default now(),
  context_accessed_at timestamptz,
  consumed_at timestamptz,
  check (actor_owner <> target_owner),
  check (target_slug ~ '^[a-z0-9_]+$'),
  check (
    confirmation_text = 'I confirm that ' || actor_owner ||
      ' is generating a draft for ' || target_owner ||
      '''s target ' || target_slug || '.'
  )
);

create table if not exists public.drafts (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  created_by uuid references auth.users(id),
  cross_owner_confirmation_id uuid references public.cross_owner_confirmations(id),
  firm text not null,
  firm_slug text not null,
  contact_status text not null check (contact_status in (
    'cold_prospect', 'existing_partner', 'lapsed_partner'
  )),
  contact jsonb not null,
  subject text,
  subject_status text not null default 'required_but_unset',
  email_body text not null,
  evidence_block text not null,
  validator_results jsonb not null,
  fields jsonb not null,
  firm_specific_paragraph text,
  status text not null default 'pending_review' check (status in (
    'pending_review', 'approved', 'rejected', 'gmail_created'
  )),
  rejection_reason text,
  approved_by uuid references auth.users(id),
  approved_at timestamptz,
  rejected_by uuid references auth.users(id),
  rejected_at timestamptz,
  gmail_draft_id text,
  gmail_web_url text,
  gmail_created_at timestamptz,
  generated_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists drafts_owner_status_idx
  on public.drafts (owner, status, generated_at desc);

create table if not exists public.review_events (
  id uuid primary key default gen_random_uuid(),
  draft_id uuid not null references public.drafts(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  actor_id uuid not null default auth.uid() references auth.users(id),
  action text not null check (action in ('approved', 'rejected')),
  reason text,
  created_at timestamptz not null default now()
);

create table if not exists public.hunter_usage (
  id uuid primary key default gen_random_uuid(),
  owner text check (owner in ('jamari', 'fola')),
  actor_id uuid references auth.users(id),
  timestamp timestamptz not null default now(),
  endpoint text not null,
  domain text not null default '',
  credits integer not null default 0,
  run_id text not null,
  note text not null default ''
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function public.handle_blk_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  allowed public.allowed_users%rowtype;
begin
  select * into allowed
  from public.allowed_users
  where email = lower(new.email);

  if not found then
    raise exception 'This Supabase project permits only the configured BLK Bridge accounts.';
  end if;

  insert into public.profiles (user_id, email, owner, display_name, gmail_sender)
  values (new.id, lower(new.email), allowed.owner, allowed.display_name, allowed.gmail_sender)
  on conflict (user_id) do update set
    email = excluded.email,
    owner = excluded.owner,
    display_name = excluded.display_name,
    gmail_sender = excluded.gmail_sender,
    updated_at = now();
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_blk_bridge on auth.users;
create trigger on_auth_user_created_blk_bridge
  after insert or update of email on auth.users
  for each row execute procedure public.handle_blk_auth_user();

insert into public.profiles (user_id, email, owner, display_name, gmail_sender)
select u.id, lower(u.email), a.owner, a.display_name, a.gmail_sender
from auth.users u
join public.allowed_users a on a.email = lower(u.email)
on conflict (user_id) do update set
  email = excluded.email,
  owner = excluded.owner,
  display_name = excluded.display_name,
  gmail_sender = excluded.gmail_sender,
  updated_at = now();

create or replace function public.current_owner()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select owner from public.profiles where user_id = auth.uid()
$$;

create or replace function public.enforce_target_owner()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  expected_owner text;
begin
  select owner into expected_owner from public.targets where id = new.target_id;
  if expected_owner is null or new.owner <> expected_owner then
    raise exception 'Child record owner must match target owner.';
  end if;
  return new;
end;
$$;

create or replace function public.enforce_review_owner()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  expected_owner text;
begin
  select owner into expected_owner from public.drafts where id = new.draft_id;
  if expected_owner is null or new.owner <> expected_owner then
    raise exception 'Review owner must match draft owner.';
  end if;
  return new;
end;
$$;

drop trigger if exists targets_updated_at on public.targets;
create trigger targets_updated_at before update on public.targets
  for each row execute procedure public.set_updated_at();
drop trigger if exists research_artifacts_updated_at on public.research_artifacts;
create trigger research_artifacts_updated_at before update on public.research_artifacts
  for each row execute procedure public.set_updated_at();
drop trigger if exists contacts_updated_at on public.contacts;
create trigger contacts_updated_at before update on public.contacts
  for each row execute procedure public.set_updated_at();
drop trigger if exists drafts_updated_at on public.drafts;
create trigger drafts_updated_at before update on public.drafts
  for each row execute procedure public.set_updated_at();

drop trigger if exists research_artifacts_target_owner on public.research_artifacts;
create trigger research_artifacts_target_owner before insert or update on public.research_artifacts
  for each row execute procedure public.enforce_target_owner();
drop trigger if exists contacts_target_owner on public.contacts;
create trigger contacts_target_owner before insert or update on public.contacts
  for each row execute procedure public.enforce_target_owner();
drop trigger if exists manual_queue_target_owner on public.manual_queue;
create trigger manual_queue_target_owner before insert or update on public.manual_queue
  for each row when (new.target_id is not null) execute procedure public.enforce_target_owner();
drop trigger if exists drafts_target_owner on public.drafts;
create trigger drafts_target_owner before insert or update on public.drafts
  for each row execute procedure public.enforce_target_owner();
drop trigger if exists review_events_draft_owner on public.review_events;
create trigger review_events_draft_owner before insert or update on public.review_events
  for each row execute procedure public.enforce_review_owner();

alter table public.allowed_users enable row level security;
alter table public.profiles enable row level security;
alter table public.targets enable row level security;
alter table public.crm_records enable row level security;
alter table public.research_artifacts enable row level security;
alter table public.contacts enable row level security;
alter table public.manual_queue enable row level security;
alter table public.action_runs enable row level security;
alter table public.cross_owner_confirmations enable row level security;
alter table public.drafts enable row level security;
alter table public.review_events enable row level security;
alter table public.hunter_usage enable row level security;

drop policy if exists profiles_select_self on public.profiles;
create policy profiles_select_self on public.profiles
  for select to authenticated using (user_id = auth.uid());

drop policy if exists targets_select_own on public.targets;
create policy targets_select_own on public.targets
  for select to authenticated using (owner = public.current_owner());
drop policy if exists targets_insert_own on public.targets;
create policy targets_insert_own on public.targets
  for insert to authenticated with check (
    owner = public.current_owner() and created_by = auth.uid()
  );
drop policy if exists targets_update_own on public.targets;
create policy targets_update_own on public.targets
  for update to authenticated using (owner = public.current_owner())
  with check (owner = public.current_owner());
drop policy if exists targets_delete_own on public.targets;
create policy targets_delete_own on public.targets
  for delete to authenticated using (owner = public.current_owner());

drop policy if exists research_select_own on public.research_artifacts;
create policy research_select_own on public.research_artifacts
  for select to authenticated using (owner = public.current_owner());
drop policy if exists research_insert_own on public.research_artifacts;
create policy research_insert_own on public.research_artifacts
  for insert to authenticated with check (owner = public.current_owner());
drop policy if exists research_update_own on public.research_artifacts;
create policy research_update_own on public.research_artifacts
  for update to authenticated using (owner = public.current_owner())
  with check (owner = public.current_owner());
drop policy if exists research_delete_own on public.research_artifacts;
create policy research_delete_own on public.research_artifacts
  for delete to authenticated using (owner = public.current_owner());

drop policy if exists contacts_select_own on public.contacts;
create policy contacts_select_own on public.contacts
  for select to authenticated using (owner = public.current_owner());
drop policy if exists contacts_insert_own on public.contacts;
create policy contacts_insert_own on public.contacts
  for insert to authenticated with check (owner = public.current_owner());
drop policy if exists contacts_update_own on public.contacts;
create policy contacts_update_own on public.contacts
  for update to authenticated using (owner = public.current_owner())
  with check (owner = public.current_owner());
drop policy if exists contacts_delete_own on public.contacts;
create policy contacts_delete_own on public.contacts
  for delete to authenticated using (owner = public.current_owner());

drop policy if exists manual_queue_select_own on public.manual_queue;
create policy manual_queue_select_own on public.manual_queue
  for select to authenticated using (owner = public.current_owner());
drop policy if exists manual_queue_insert_own on public.manual_queue;
create policy manual_queue_insert_own on public.manual_queue
  for insert to authenticated with check (owner = public.current_owner());
drop policy if exists manual_queue_update_own on public.manual_queue;
create policy manual_queue_update_own on public.manual_queue
  for update to authenticated using (owner = public.current_owner())
  with check (owner = public.current_owner());

drop policy if exists action_runs_select_own on public.action_runs;
create policy action_runs_select_own on public.action_runs
  for select to authenticated using (owner = public.current_owner());
drop policy if exists action_runs_insert_own on public.action_runs;
create policy action_runs_insert_own on public.action_runs
  for insert to authenticated with check (
    owner = public.current_owner() and actor_id = auth.uid()
  );
drop policy if exists action_runs_update_own on public.action_runs;
create policy action_runs_update_own on public.action_runs
  for update to authenticated using (
    owner = public.current_owner() and actor_id = auth.uid()
  ) with check (owner = public.current_owner() and actor_id = auth.uid());

drop policy if exists confirmations_select_actor on public.cross_owner_confirmations;
create policy confirmations_select_actor on public.cross_owner_confirmations
  for select to authenticated using (actor_id = auth.uid());
drop policy if exists confirmations_insert_actor on public.cross_owner_confirmations;
create policy confirmations_insert_actor on public.cross_owner_confirmations
  for insert to authenticated with check (
    actor_id = auth.uid()
    and actor_owner = public.current_owner()
    and target_owner <> public.current_owner()
    and confirmed_at between now() - interval '1 minute' and now() + interval '1 minute'
    and context_accessed_at is null
    and consumed_at is null
  );

drop policy if exists drafts_select_own on public.drafts;
create policy drafts_select_own on public.drafts
  for select to authenticated using (owner = public.current_owner());

drop policy if exists review_events_select_own on public.review_events;
create policy review_events_select_own on public.review_events
  for select to authenticated using (owner = public.current_owner());

create or replace function public.save_validated_draft(
  p_target_id uuid,
  p_payload jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  target_row public.targets%rowtype;
  new_id uuid;
begin
  select * into target_row
  from public.targets
  where id = p_target_id and owner = public.current_owner();
  if not found then
    raise exception 'Target is not visible in the caller owner lane.';
  end if;
  if p_payload->>'owner' <> target_row.owner
     or p_payload->>'firm_slug' <> target_row.firm_slug then
    raise exception 'Draft owner or target identity mismatch.';
  end if;
  if coalesce(p_payload->'validator_results'->>'status', '') <> 'pass' then
    raise exception 'Only a validator-passing draft may be stored.';
  end if;

  insert into public.drafts (
    target_id, owner, created_by, firm, firm_slug, contact_status, contact,
    subject, subject_status, email_body, evidence_block, validator_results,
    fields, firm_specific_paragraph, generated_at
  ) values (
    target_row.id, target_row.owner, auth.uid(), p_payload->>'firm',
    p_payload->>'firm_slug', p_payload->>'contact_status', p_payload->'contact',
    nullif(p_payload->>'subject', ''), p_payload->>'subject_status',
    p_payload->>'email_body', p_payload->>'evidence_block',
    p_payload->'validator_results', p_payload->'fields',
    nullif(p_payload->'fields'->>'firm_specific_paragraph', ''),
    coalesce((p_payload->>'generated_at')::timestamptz, now())
  ) returning id into new_id;
  return new_id;
end;
$$;

create or replace function public.review_draft(
  p_draft_id uuid,
  p_action text,
  p_reason text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  draft_row public.drafts%rowtype;
begin
  if p_action not in ('approved', 'rejected') then
    raise exception 'Review action must be approved or rejected.';
  end if;
  if p_action = 'rejected' and nullif(btrim(coalesce(p_reason, '')), '') is null then
    raise exception 'A rejection reason is required.';
  end if;

  select * into draft_row
  from public.drafts
  where id = p_draft_id and owner = public.current_owner()
  for update;
  if not found then
    raise exception 'Draft is not visible in the caller owner lane.';
  end if;
  if draft_row.status <> 'pending_review' then
    raise exception 'Only a pending draft may be reviewed.';
  end if;

  if p_action = 'approved' then
    update public.drafts set
      status = 'approved', approved_by = auth.uid(), approved_at = now(),
      rejection_reason = null, rejected_by = null, rejected_at = null
    where id = p_draft_id returning * into draft_row;
  else
    update public.drafts set
      status = 'rejected', rejection_reason = btrim(p_reason),
      rejected_by = auth.uid(), rejected_at = now(),
      approved_by = null, approved_at = null
    where id = p_draft_id returning * into draft_row;
  end if;

  insert into public.review_events (draft_id, owner, actor_id, action, reason)
  values (draft_row.id, draft_row.owner, auth.uid(), p_action,
          case when p_action = 'rejected' then btrim(p_reason) else null end);
  return to_jsonb(draft_row);
end;
$$;

create or replace function public.cross_owner_draft_context(p_confirmation_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  confirmation_row public.cross_owner_confirmations%rowtype;
  target_row public.targets%rowtype;
  artifact_row public.research_artifacts%rowtype;
  contact_row public.contacts%rowtype;
begin
  select * into confirmation_row
  from public.cross_owner_confirmations
  where id = p_confirmation_id and actor_id = auth.uid()
  for update;
  if not found
     or confirmation_row.actor_owner <> public.current_owner()
     or confirmation_row.action <> 'generate_draft'
     or confirmation_row.context_accessed_at is not null
     or confirmation_row.consumed_at is not null
     or confirmation_row.confirmed_at < now() - interval '10 minutes' then
    raise exception 'Cross-owner confirmation is missing, expired, or already used.';
  end if;

  select * into target_row
  from public.targets
  where owner = confirmation_row.target_owner
    and firm_slug = confirmation_row.target_slug;
  if not found then
    raise exception 'Confirmed cross-owner target was not found.';
  end if;

  select * into artifact_row from public.research_artifacts
  where target_id = target_row.id;
  select * into contact_row from public.contacts
  where target_id = target_row.id and not dropped
  order by verification_score desc nulls last, created_at
  limit 1;

  update public.cross_owner_confirmations
  set context_accessed_at = now()
  where id = p_confirmation_id;

  return jsonb_build_object(
    'target', to_jsonb(target_row),
    'artifact', artifact_row.artifact,
    'contact', case when contact_row.id is null then null else to_jsonb(contact_row) end
  );
end;
$$;

create or replace function public.save_cross_owner_draft(
  p_confirmation_id uuid,
  p_payload jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  confirmation_row public.cross_owner_confirmations%rowtype;
  target_row public.targets%rowtype;
  new_id uuid;
begin
  select * into confirmation_row
  from public.cross_owner_confirmations
  where id = p_confirmation_id and actor_id = auth.uid()
  for update;
  if not found
     or confirmation_row.actor_owner <> public.current_owner()
     or confirmation_row.context_accessed_at is null
     or confirmation_row.consumed_at is not null
     or confirmation_row.confirmed_at < now() - interval '10 minutes' then
    raise exception 'Cross-owner confirmation is missing, expired, or already used.';
  end if;

  select * into target_row from public.targets
  where owner = confirmation_row.target_owner
    and firm_slug = confirmation_row.target_slug;
  if not found then
    raise exception 'Confirmed cross-owner target was not found.';
  end if;
  if p_payload->>'owner' <> target_row.owner
     or p_payload->>'firm_slug' <> target_row.firm_slug
     or coalesce(p_payload->'validator_results'->>'status', '') <> 'pass' then
    raise exception 'Cross-owner draft identity or validation mismatch.';
  end if;

  insert into public.drafts (
    target_id, owner, created_by, cross_owner_confirmation_id, firm, firm_slug,
    contact_status, contact, subject, subject_status, email_body, evidence_block,
    validator_results, fields, firm_specific_paragraph, generated_at
  ) values (
    target_row.id, target_row.owner, auth.uid(), confirmation_row.id,
    p_payload->>'firm', p_payload->>'firm_slug', p_payload->>'contact_status',
    p_payload->'contact', nullif(p_payload->>'subject', ''),
    p_payload->>'subject_status', p_payload->>'email_body',
    p_payload->>'evidence_block', p_payload->'validator_results',
    p_payload->'fields', nullif(p_payload->'fields'->>'firm_specific_paragraph', ''),
    coalesce((p_payload->>'generated_at')::timestamptz, now())
  ) returning id into new_id;

  update public.cross_owner_confirmations
  set consumed_at = now()
  where id = p_confirmation_id;
  return new_id;
end;
$$;

revoke all on public.allowed_users, public.profiles, public.targets,
  public.crm_records, public.research_artifacts, public.contacts,
  public.manual_queue, public.action_runs, public.cross_owner_confirmations,
  public.drafts, public.review_events, public.hunter_usage from anon;

revoke all on public.allowed_users, public.crm_records, public.hunter_usage
  from authenticated;
grant select on public.profiles to authenticated;
grant select, insert, update, delete on public.targets to authenticated;
grant select, insert, update, delete on public.research_artifacts to authenticated;
grant select, insert, update, delete on public.contacts to authenticated;
grant select, insert, update on public.manual_queue to authenticated;
grant select, insert, update on public.action_runs to authenticated;
grant select, insert on public.cross_owner_confirmations to authenticated;
grant select on public.drafts to authenticated;
grant select on public.review_events to authenticated;

revoke execute on function public.set_updated_at() from public, anon, authenticated;
revoke execute on function public.handle_blk_auth_user() from public, anon, authenticated;
revoke execute on function public.enforce_target_owner() from public, anon, authenticated;
revoke execute on function public.enforce_review_owner() from public, anon, authenticated;
revoke execute on function public.current_owner() from public, anon;
revoke execute on function public.save_validated_draft(uuid, jsonb) from public, anon;
revoke execute on function public.review_draft(uuid, text, text) from public, anon;
revoke execute on function public.cross_owner_draft_context(uuid) from public, anon;
revoke execute on function public.save_cross_owner_draft(uuid, jsonb) from public, anon;

grant execute on function public.current_owner() to authenticated;
grant execute on function public.save_validated_draft(uuid, jsonb) to authenticated;
grant execute on function public.review_draft(uuid, text, text) to authenticated;
grant execute on function public.cross_owner_draft_context(uuid) to authenticated;
grant execute on function public.save_cross_owner_draft(uuid, jsonb) to authenticated;

grant all on public.allowed_users, public.profiles, public.targets,
  public.crm_records, public.research_artifacts, public.contacts,
  public.manual_queue, public.action_runs, public.cross_owner_confirmations,
  public.drafts, public.review_events, public.hunter_usage to service_role;

commit;
