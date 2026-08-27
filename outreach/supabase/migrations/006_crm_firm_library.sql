-- BLK Bridge CRM foundation: additive status dimensions, non-destructive
-- pipeline membership, meeting notes, and durable status audit history.
--
-- targets remains the master firm record so every existing target_id and child
-- foreign key stays stable. No data is deleted or destructively renamed.

begin;

alter table public.targets
  add column if not exists relationship_status_auto text not null default 'Cold Prospect',
  add column if not exists relationship_status_override text,
  add column if not exists relationship_override_at timestamptz,
  add column if not exists relationship_status_auto_source text not null default 'legacy_migration',
  add column if not exists pipeline_stage_auto text not null default 'Researching',
  add column if not exists pipeline_stage_override text,
  add column if not exists pipeline_stage_override_at timestamptz,
  add column if not exists pipeline_stage_auto_source text not null default 'legacy_migration',
  add column if not exists pipeline_active boolean not null default true,
  add column if not exists partnership_scope text not null default '',
  add column if not exists partnership_type text not null default '',
  add column if not exists assigned_owner text not null default '',
  add column if not exists sponsorship_tier text not null default '',
  add column if not exists renewal_notes text not null default '',
  add column if not exists last_touchpoint text not null default '',
  add column if not exists email_chain_notes text not null default '',
  add column if not exists contact_verified_status text not null default '',
  add column if not exists next_step text not null default '',
  add column if not exists next_step_due date;

alter table public.targets drop constraint if exists targets_relationship_status_auto_check;
alter table public.targets add constraint targets_relationship_status_auto_check check (
  relationship_status_auto in (
    'Cold Prospect', 'Existing Partner', 'Global Partner',
    'Expired / Former Partner', 'Not Renewing', 'Not Interested', 'Archived'
  )
);
alter table public.targets drop constraint if exists targets_relationship_status_override_check;
alter table public.targets add constraint targets_relationship_status_override_check check (
  relationship_status_override is null or relationship_status_override in (
    'Cold Prospect', 'Existing Partner', 'Global Partner',
    'Expired / Former Partner', 'Not Renewing', 'Not Interested', 'Archived'
  )
);
alter table public.targets drop constraint if exists targets_pipeline_stage_auto_check;
alter table public.targets add constraint targets_pipeline_stage_auto_check check (
  pipeline_stage_auto in (
    'Researching', 'Contact Ready', 'Draft Ready', 'Outreach Sent',
    'Follow-Up Due', 'Responded', 'Meeting Scheduled', 'Re-engagement',
    'Renewal / In Conversation', 'Proposal / Contract', 'Stalled',
    'Closed / Partner', 'Closed / No Active Workflow'
  )
);
alter table public.targets drop constraint if exists targets_pipeline_stage_override_check;
alter table public.targets add constraint targets_pipeline_stage_override_check check (
  pipeline_stage_override is null or pipeline_stage_override in (
    'Researching', 'Contact Ready', 'Draft Ready', 'Outreach Sent',
    'Follow-Up Due', 'Responded', 'Meeting Scheduled', 'Re-engagement',
    'Renewal / In Conversation', 'Proposal / Contract', 'Stalled',
    'Closed / Partner', 'Closed / No Active Workflow'
  )
);

-- Preserve and normalize the existing distinctions. The raw relationship_status
-- column is intentionally retained as source evidence.
update public.targets set relationship_status_auto = case
  when lower(btrim(relationship_status)) in ('global partner', 'global sponsorship')
    then 'Global Partner'
  when lower(btrim(relationship_status)) in ('not interested', 'declined', 'do not contact')
    then 'Not Interested'
  when lower(btrim(relationship_status)) in ('archived', 'archive')
    then 'Archived'
  when lower(btrim(relationship_status)) in ('not renewing', 'non-renewing', 'did not renew')
    then 'Not Renewing'
  when lower(btrim(relationship_status)) in ('expired', 'lapsed', 'former partner', 'inactive')
    then 'Expired / Former Partner'
  when contact_status = 'existing_partner' then 'Existing Partner'
  when contact_status = 'lapsed_partner' then 'Expired / Former Partner'
  else 'Cold Prospect'
end,
relationship_status_auto_source = 'legacy_migration',
partnership_scope = case when partnership_scope = '' then region else partnership_scope end,
partnership_type = case when partnership_type = '' then relationship else partnership_type end,
assigned_owner = case when assigned_owner = '' then owner else assigned_owner end,
sponsorship_tier = case
  when sponsorship_tier <> '' then sponsorship_tier
  when relationship_tier <> '' then relationship_tier
  else tier_target
end;

update public.targets set pipeline_stage_auto = case
  when lower(notes) like '%re-engag%' or lower(notes) like '%win-back%'
    then 'Re-engagement'
  when lower(crm_status) = 'inherited warm lead' then 'Contact Ready'
  when lower(crm_status) = '1st call' then 'Meeting Scheduled'
  when lower(crm_status) in ('active', 'renewed') then 'Closed / Partner'
  when relationship_status_auto in ('Not Interested', 'Archived')
    then 'Closed / No Active Workflow'
  when relationship_status_auto = 'Global Partner' then 'Closed / Partner'
  else 'Researching'
end,
pipeline_stage_auto_source = 'legacy_migration';

update public.targets set pipeline_active = false
where pipeline_stage_auto in ('Closed / Partner', 'Closed / No Active Workflow')
   or relationship_status_auto in ('Not Interested', 'Archived');

create table if not exists public.crm_audit_events (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  field_name text not null check (field_name in (
    'relationship_status', 'pipeline_stage', 'pipeline_active', 'meeting_note'
  )),
  prior_value text,
  new_value text,
  change_source text not null check (change_source in ('manual', 'automatic', 'imported')),
  actor_id uuid default auth.uid() references auth.users(id),
  reason text,
  created_at timestamptz not null default now()
);

create index if not exists crm_audit_events_target_created_idx
  on public.crm_audit_events (target_id, created_at desc);

create table if not exists public.meeting_notes (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null references public.targets(id) on delete cascade,
  owner text not null check (owner in ('jamari', 'fola')),
  interaction_date date not null,
  interaction_type text not null default 'Meeting',
  participants jsonb not null default '[]'::jsonb,
  notes text not null,
  next_step text not null default '',
  follow_up_date date,
  created_by uuid default auth.uid() references auth.users(id),
  updated_by uuid default auth.uid() references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists meeting_notes_target_date_idx
  on public.meeting_notes (target_id, interaction_date desc, created_at desc);

create or replace function public.audit_target_automatic_status()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.relationship_status_auto is distinct from new.relationship_status_auto then
    insert into public.crm_audit_events (
      target_id, owner, field_name, prior_value, new_value,
      change_source, actor_id, reason
    ) values (
      new.id, new.owner, 'relationship_status', old.relationship_status_auto,
      new.relationship_status_auto, 'automatic', auth.uid(),
      nullif(new.relationship_status_auto_source, '')
    );
  end if;
  if old.pipeline_stage_auto is distinct from new.pipeline_stage_auto then
    insert into public.crm_audit_events (
      target_id, owner, field_name, prior_value, new_value,
      change_source, actor_id, reason
    ) values (
      new.id, new.owner, 'pipeline_stage', old.pipeline_stage_auto,
      new.pipeline_stage_auto, 'automatic', auth.uid(),
      nullif(new.pipeline_stage_auto_source, '')
    );
  end if;
  return new;
end;
$$;

drop trigger if exists targets_automatic_status_audit on public.targets;
create trigger targets_automatic_status_audit
  after update of relationship_status_auto, pipeline_stage_auto on public.targets
  for each row execute procedure public.audit_target_automatic_status();

drop trigger if exists meeting_notes_updated_at on public.meeting_notes;
create trigger meeting_notes_updated_at before update on public.meeting_notes
  for each row execute procedure public.set_updated_at();
drop trigger if exists crm_audit_events_target_owner on public.crm_audit_events;
create trigger crm_audit_events_target_owner
  before insert or update on public.crm_audit_events
  for each row execute procedure public.enforce_target_owner();
drop trigger if exists meeting_notes_target_owner on public.meeting_notes;
create trigger meeting_notes_target_owner
  before insert or update on public.meeting_notes
  for each row execute procedure public.enforce_target_owner();

alter table public.crm_audit_events enable row level security;
alter table public.meeting_notes enable row level security;

-- The operational DELETE endpoint now closes pipeline membership. Prevent an
-- authenticated browser client from bypassing that behavior with raw REST.
drop policy if exists targets_delete_own on public.targets;
revoke delete on public.targets from authenticated;

drop policy if exists crm_audit_events_select_own on public.crm_audit_events;
create policy crm_audit_events_select_own on public.crm_audit_events
  for select to authenticated using (owner = public.current_owner());
drop policy if exists crm_audit_events_insert_own on public.crm_audit_events;

drop policy if exists meeting_notes_select_own on public.meeting_notes;
create policy meeting_notes_select_own on public.meeting_notes
  for select to authenticated using (owner = public.current_owner());
drop policy if exists meeting_notes_insert_own on public.meeting_notes;
create policy meeting_notes_insert_own on public.meeting_notes
  for insert to authenticated with check (
    owner = public.current_owner() and created_by = auth.uid() and updated_by = auth.uid()
  );
drop policy if exists meeting_notes_update_own on public.meeting_notes;
create policy meeting_notes_update_own on public.meeting_notes
  for update to authenticated using (owner = public.current_owner())
  with check (owner = public.current_owner() and updated_by = auth.uid());

-- Atomic override + audit RPC. Automation may continue changing the *_auto
-- value; the nullable manual value always remains the effective winner.
create or replace function public.set_target_status_override(
  p_target_id uuid,
  p_field text,
  p_value text default null,
  p_clear boolean default false,
  p_reason text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  target_row public.targets%rowtype;
  prior_effective text;
  new_effective text;
begin
  if p_field not in ('relationship_status', 'pipeline_stage') then
    raise exception 'Status field must be relationship_status or pipeline_stage.';
  end if;
  if not p_clear and nullif(btrim(coalesce(p_value, '')), '') is null then
    raise exception 'A status value is required unless the override is being cleared.';
  end if;

  select * into target_row from public.targets
  where id = p_target_id and owner = public.current_owner()
  for update;
  if not found then
    raise exception 'Firm is not visible in the caller owner lane.';
  end if;

  if p_field = 'relationship_status' then
    prior_effective := coalesce(target_row.relationship_status_override,
                                target_row.relationship_status_auto);
    if not p_clear and p_value not in (
      'Cold Prospect', 'Existing Partner', 'Global Partner',
      'Expired / Former Partner', 'Not Renewing', 'Not Interested', 'Archived'
    ) then
      raise exception 'Unrecognized relationship status.';
    end if;
    new_effective := case when p_clear then target_row.relationship_status_auto else p_value end;
    update public.targets set
      relationship_status_override = case when p_clear then null else p_value end,
      relationship_override_at = case when p_clear then null else now() end,
      contact_status = case
        when new_effective in ('Existing Partner', 'Global Partner') then 'existing_partner'
        when new_effective = 'Cold Prospect' then 'cold_prospect'
        else 'lapsed_partner'
      end
    where id = p_target_id returning * into target_row;
  else
    prior_effective := coalesce(target_row.pipeline_stage_override,
                                target_row.pipeline_stage_auto);
    if not p_clear and p_value not in (
      'Researching', 'Contact Ready', 'Draft Ready', 'Outreach Sent',
      'Follow-Up Due', 'Responded', 'Meeting Scheduled', 'Re-engagement',
      'Renewal / In Conversation', 'Proposal / Contract', 'Stalled',
      'Closed / Partner', 'Closed / No Active Workflow'
    ) then
      raise exception 'Unrecognized pipeline stage.';
    end if;
    new_effective := case when p_clear then target_row.pipeline_stage_auto else p_value end;
    update public.targets set
      pipeline_stage_override = case when p_clear then null else p_value end,
      pipeline_stage_override_at = case when p_clear then null else now() end,
      pipeline_active = case
        when not p_clear and p_value in (
          'Researching', 'Contact Ready', 'Draft Ready', 'Outreach Sent',
          'Follow-Up Due', 'Responded', 'Meeting Scheduled', 'Re-engagement',
          'Renewal / In Conversation', 'Proposal / Contract', 'Stalled'
        ) then true else pipeline_active end
    where id = p_target_id returning * into target_row;
  end if;

  insert into public.crm_audit_events (
    target_id, owner, field_name, prior_value, new_value,
    change_source, actor_id, reason
  ) values (
    target_row.id, target_row.owner, p_field, prior_effective, new_effective,
    'manual', auth.uid(), case
      when p_clear and nullif(btrim(coalesce(p_reason, '')), '') is null
        then 'Manual override cleared; returned to automatic.'
      else nullif(btrim(coalesce(p_reason, '')), '')
    end
  );
  return to_jsonb(target_row);
end;
$$;

create or replace function public.set_target_pipeline_active(
  p_target_id uuid,
  p_active boolean,
  p_reason text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  target_row public.targets%rowtype;
  prior_value boolean;
begin
  select * into target_row from public.targets
  where id = p_target_id and owner = public.current_owner()
  for update;
  if not found then
    raise exception 'Firm is not visible in the caller owner lane.';
  end if;
  prior_value := target_row.pipeline_active;
  update public.targets set pipeline_active = p_active
  where id = p_target_id returning * into target_row;
  insert into public.crm_audit_events (
    target_id, owner, field_name, prior_value, new_value,
    change_source, actor_id, reason
  ) values (
    target_row.id, target_row.owner, 'pipeline_active', prior_value::text,
    p_active::text, 'manual', auth.uid(),
    coalesce(nullif(btrim(coalesce(p_reason, '')), ''),
             case when p_active then 'Returned to Firm Pipeline.'
                  else 'Removed from Firm Pipeline; firm retained in Firm Library.' end)
  );
  return to_jsonb(target_row);
end;
$$;

alter table public.action_runs drop constraint if exists action_runs_action_type_check;
alter table public.action_runs add constraint action_runs_action_type_check check (action_type in (
  'batch_intake', 'derive_status', 'research', 'contact_preview',
  'contact_discovery', 'draft_generation', 'batch_relationship_status',
  'batch_pipeline_stage'
));

revoke all on public.crm_audit_events, public.meeting_notes from anon;
revoke all on public.crm_audit_events, public.meeting_notes from authenticated;
grant select on public.crm_audit_events to authenticated;
grant select, insert, update on public.meeting_notes to authenticated;
grant all on public.crm_audit_events, public.meeting_notes to service_role;

revoke execute on function public.set_target_status_override(uuid, text, text, boolean, text)
  from public, anon;
grant execute on function public.set_target_status_override(uuid, text, text, boolean, text)
  to authenticated;
revoke execute on function public.set_target_pipeline_active(uuid, boolean, text)
  from public, anon;
grant execute on function public.set_target_pipeline_active(uuid, boolean, text)
  to authenticated;
revoke execute on function public.audit_target_automatic_status()
  from public, anon, authenticated;

commit;
