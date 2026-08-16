-- Phase G.2: lane switching for a small trusted team.
--
-- Jamari and Fola keep their two lanes exactly as they were: every domain
-- table's owner column, its CHECK constraints, and RLS policies are
-- untouched, because business-data ownership is not changing.
--
-- What changes is who may act inside a lane and how "which lane am I acting
-- in right now" gets resolved. Two more BLK accounts (Justin, Belayneh) get
-- login access as viewers with no lane of their own, and all four accounts
-- (the two owners and the two viewers) get standing, full-read/write access
-- to both lanes, switchable per request via an X-Blk-Lane header rather than
-- fixed per profile. This replaces the one-off cross_owner_confirmations
-- flow, which is removed below.

begin;

-- ---------------------------------------------------------------------
-- allowed_users / profiles: make room for accounts with no owned lane.
-- ---------------------------------------------------------------------

alter table public.allowed_users
  alter column owner drop not null,
  alter column gmail_sender drop not null,
  add column if not exists role text not null default 'owner' check (role in ('owner', 'viewer'));

alter table public.allowed_users drop constraint if exists allowed_users_owner_check;
alter table public.allowed_users add constraint allowed_users_owner_check
  check (owner is null or owner in ('jamari', 'fola'));

insert into public.allowed_users (email, role, owner, display_name, gmail_sender)
values
  ('justin@blkcapitalmanagement.org', 'viewer', null, 'Justin', null),
  ('belayneh@blkcapitalmanagement.org', 'viewer', null, 'Belayneh Barkley', null)
on conflict (email) do update set
  role = excluded.role,
  owner = excluded.owner,
  display_name = excluded.display_name,
  gmail_sender = excluded.gmail_sender;

alter table public.profiles
  alter column owner drop not null,
  alter column gmail_sender drop not null,
  add column if not exists role text not null default 'owner' check (role in ('owner', 'viewer'));

alter table public.profiles drop constraint if exists profiles_owner_check;
alter table public.profiles add constraint profiles_owner_check
  check (owner is null or owner in ('jamari', 'fola'));

-- ---------------------------------------------------------------------
-- profile_lane_access: explicit grant of which lanes a person may switch
-- into, mirroring the allowed_users explicit-allowlist pattern rather than
-- hardcoding "everyone gets both lanes" in application code.
-- ---------------------------------------------------------------------

create table if not exists public.profile_lane_access (
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  lane text not null check (lane in ('jamari', 'fola')),
  is_default boolean not null default false,
  primary key (user_id, lane)
);

create unique index if not exists profile_lane_access_one_default_idx
  on public.profile_lane_access (user_id) where is_default;

alter table public.profile_lane_access enable row level security;

drop policy if exists profile_lane_access_select_self on public.profile_lane_access;
create policy profile_lane_access_select_self on public.profile_lane_access
  for select to authenticated using (user_id = auth.uid());

revoke all on public.profile_lane_access from anon;
revoke all on public.profile_lane_access from authenticated;
grant select on public.profile_lane_access to authenticated;
grant all on public.profile_lane_access to service_role;

-- ---------------------------------------------------------------------
-- Sign-in trigger: seed both lanes for every account. Owners default to
-- their own lane; viewers default to jamari.
-- ---------------------------------------------------------------------

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

  insert into public.profiles (user_id, email, role, owner, display_name, gmail_sender)
  values (new.id, lower(new.email), allowed.role, allowed.owner, allowed.display_name, allowed.gmail_sender)
  on conflict (user_id) do update set
    email = excluded.email,
    role = excluded.role,
    owner = excluded.owner,
    display_name = excluded.display_name,
    gmail_sender = excluded.gmail_sender,
    updated_at = now();

  insert into public.profile_lane_access (user_id, lane, is_default)
  values
    (new.id, 'jamari', coalesce(allowed.owner, 'jamari') = 'jamari'),
    (new.id, 'fola',   coalesce(allowed.owner, 'jamari') = 'fola')
  on conflict (user_id, lane) do update set is_default = excluded.is_default;

  return new;
end;
$$;

-- Backfill for accounts that signed in before this migration.
insert into public.profile_lane_access (user_id, lane, is_default)
select p.user_id, lane_choice.lane, lane_choice.lane = coalesce(p.owner, 'jamari')
from public.profiles p
cross join (values ('jamari'), ('fola')) as lane_choice(lane)
on conflict (user_id, lane) do update set is_default = excluded.is_default;

-- ---------------------------------------------------------------------
-- current_owner(): resolves the caller's active lane for this request
-- instead of a fixed profile column. An explicit X-Blk-Lane header (read
-- via PostgREST's request.headers GUC) is honored only if the caller holds
-- that lane in profile_lane_access; otherwise the caller's default lane is
-- used. Every existing RLS policy and RPC already calls this one function,
-- so no other policy needs to change.
-- ---------------------------------------------------------------------

create or replace function public.current_owner()
returns text
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  requested text := lower(nullif(btrim(coalesce(
    current_setting('request.headers', true)::json->>'x-blk-lane', ''
  )), ''));
  fallback text;
begin
  if requested is not null then
    if not exists (
      select 1 from public.profile_lane_access
      where user_id = auth.uid() and lane = requested
    ) then
      raise exception 'Lane % is not permitted for this account.', requested;
    end if;
    return requested;
  end if;

  select lane into fallback
  from public.profile_lane_access
  where user_id = auth.uid() and is_default
  limit 1;
  return fallback;
end;
$$;

-- lane_identity(): lets a caller resolve a lane's display name / gmail
-- send-as address (needed for the compose panel) without granting them
-- direct SELECT on another person's profiles row.
create or replace function public.lane_identity(p_lane text)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  identity public.allowed_users%rowtype;
begin
  if not exists (
    select 1 from public.profile_lane_access
    where user_id = auth.uid() and lane = p_lane
  ) then
    raise exception 'Lane % is not permitted for this account.', p_lane;
  end if;

  select * into identity from public.allowed_users where owner = p_lane;
  if not found then
    raise exception 'Lane % has no configured identity.', p_lane;
  end if;

  return jsonb_build_object(
    'display_name', identity.display_name,
    'gmail_sender', identity.gmail_sender
  );
end;
$$;

revoke execute on function public.lane_identity(text) from public, anon;
grant execute on function public.lane_identity(text) to authenticated;

-- ---------------------------------------------------------------------
-- Remove the one-off cross-owner confirmation flow. The lane switcher
-- replaces it for these four trusted accounts.
-- ---------------------------------------------------------------------

drop function if exists public.save_cross_owner_draft(uuid, jsonb);
drop function if exists public.cross_owner_draft_context(uuid);
alter table public.drafts drop column if exists cross_owner_confirmation_id;
drop table if exists public.cross_owner_confirmations cascade;

commit;
