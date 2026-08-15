-- Let a human close a manual-queue item they handled outside Bridge.
--
-- Rows were only ever cleared automatically, when a later research run for the
-- same target succeeded. Anything resolved by hand (a contact found manually, a
-- firm deliberately dropped) stayed in the queue forever. A queue that only
-- grows stops being read, which defeats the point of routing work to it.

begin;

alter table public.manual_queue add column if not exists resolved_by uuid references auth.users(id);
alter table public.manual_queue add column if not exists resolution_note text;

create or replace function public.resolve_manual_queue_item(
  p_item_id uuid,
  p_note text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  item_row public.manual_queue%rowtype;
begin
  if nullif(btrim(coalesce(p_note, '')), '') is null then
    raise exception 'A resolution note is required so the queue stays auditable.';
  end if;

  select * into item_row
  from public.manual_queue
  where id = p_item_id and owner = public.current_owner()
  for update;
  if not found then
    raise exception 'Manual queue item is not visible in the caller owner lane.';
  end if;
  if item_row.resolved_at is not null then
    raise exception 'This item is already resolved.';
  end if;

  update public.manual_queue set
    resolved_at = now(), resolved_by = auth.uid(), resolution_note = btrim(p_note)
  where id = p_item_id returning * into item_row;
  return to_jsonb(item_row);
end;
$$;

revoke execute on function public.resolve_manual_queue_item(uuid, text) from public, anon;
grant execute on function public.resolve_manual_queue_item(uuid, text) to authenticated;

commit;
