-- Phase G.1: record that an approved draft was actually sent by a human.
--
-- Bridge still does not transmit email. A person copies the approved draft into
-- Gmail, sends it, and then marks it here. Without this record a 19 firm push
-- has no way to tell who was already contacted, and the
-- send.daily_cap_per_mailbox guard in config/settings.yaml is unenforceable.

begin;

-- 'sent' joins the existing status vocabulary. 'gmail_created' is retained for
-- the future Phase H path that creates real Gmail drafts.
alter table public.drafts drop constraint if exists drafts_status_check;
alter table public.drafts add constraint drafts_status_check check (status in (
  'pending_review', 'approved', 'rejected', 'gmail_created', 'sent'
));

alter table public.drafts add column if not exists sent_at timestamptz;
alter table public.drafts add column if not exists sent_by uuid references auth.users(id);

alter table public.review_events drop constraint if exists review_events_action_check;
alter table public.review_events add constraint review_events_action_check check (
  action in ('approved', 'rejected', 'sent')
);

-- Mirrors public.review_draft: owner lane enforced through current_owner(),
-- row locked for update, and an illegal source status raises rather than
-- silently no-opping.
create or replace function public.mark_draft_sent(p_draft_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  draft_row public.drafts%rowtype;
begin
  select * into draft_row
  from public.drafts
  where id = p_draft_id and owner = public.current_owner()
  for update;
  if not found then
    raise exception 'Draft is not visible in the caller owner lane.';
  end if;
  if draft_row.status = 'sent' then
    raise exception 'This draft is already marked as sent.';
  end if;
  if draft_row.status <> 'approved' then
    raise exception 'Only an approved draft may be marked as sent.';
  end if;

  update public.drafts set
    status = 'sent', sent_by = auth.uid(), sent_at = now()
  where id = p_draft_id returning * into draft_row;

  insert into public.review_events (draft_id, owner, actor_id, action, reason)
  values (draft_row.id, draft_row.owner, auth.uid(), 'sent', null);
  return to_jsonb(draft_row);
end;
$$;

revoke execute on function public.mark_draft_sent(uuid) from public, anon;
grant execute on function public.mark_draft_sent(uuid) to authenticated;

commit;
