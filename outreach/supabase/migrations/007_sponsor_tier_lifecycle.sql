-- Sponsorship tier is a current-contract attribute, never a prospect label.
-- relationship_expiration is the existing targets expiration-date column.

begin;

alter table public.targets
  alter column sponsorship_tier drop not null,
  alter column sponsorship_tier drop default;

-- One-time cleanup. Future-dated agreements retain their current tier.
update public.targets
set sponsorship_tier = null
where nullif(btrim(sponsorship_tier), '') is null
   or relationship_expiration is null
   or relationship_expiration <= current_date;

create or replace function public.enforce_current_sponsorship_tier()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.sponsorship_tier := nullif(btrim(new.sponsorship_tier), '');
  if new.relationship_expiration is null
     or new.relationship_expiration <= current_date then
    new.sponsorship_tier := null;
  end if;
  return new;
end;
$$;

drop trigger if exists targets_current_sponsorship_tier on public.targets;
create trigger targets_current_sponsorship_tier
  before insert or update of sponsorship_tier, relationship_expiration
  on public.targets
  for each row execute procedure public.enforce_current_sponsorship_tier();

revoke execute on function public.enforce_current_sponsorship_tier()
  from public, anon, authenticated;

comment on column public.targets.sponsorship_tier is
  'Current sponsorship tier. Valid only while relationship_expiration > current_date.';

commit;
