-- Home/Work are single slots per user (see the partial unique indexes in
-- 20260821132136_create_saved_places.sql). Replacing one from the client was
-- a delete-then-insert across two separate calls, so a failure between them
-- could wipe a user's slot with nothing to replace it. Wrapping both in one
-- function call makes the swap atomic — the whole thing is one transaction.
create or replace function public.replace_saved_slot(
  p_kind text,
  p_label text,
  p_address text,
  p_lat double precision,
  p_lon double precision
)
returns public.saved_places
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_row public.saved_places;
begin
  if p_kind not in ('home', 'work') then
    raise exception 'replace_saved_slot only supports home/work slots, got %', p_kind;
  end if;

  delete from public.saved_places
  where user_id = (select auth.uid()) and kind = p_kind;

  insert into public.saved_places (user_id, kind, label, address, lat, lon)
  values ((select auth.uid()), p_kind, p_label, p_address, p_lat, p_lon)
  returning * into v_row;

  return v_row;
end;
$$;

revoke all on function public.replace_saved_slot(text, text, text, double precision, double precision) from public;
grant execute on function public.replace_saved_slot(text, text, text, double precision, double precision) to authenticated;
