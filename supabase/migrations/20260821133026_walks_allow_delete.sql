grant delete on public.walks to authenticated;

drop policy if exists "walks_delete_own" on public.walks;
create policy "walks_delete_own" on public.walks
  for delete to authenticated
  using ( (select auth.uid()) = user_id );
