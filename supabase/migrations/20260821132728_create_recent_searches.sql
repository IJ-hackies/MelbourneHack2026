create table if not exists public.recent_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  label text not null,
  address text,
  lat double precision,
  lon double precision,
  searched_at timestamptz not null default now(),
  unique (user_id, label)
);

create index if not exists recent_searches_user_searched_at_idx
  on public.recent_searches (user_id, searched_at desc);

alter table public.recent_searches enable row level security;

grant select, insert, update on public.recent_searches to authenticated;

drop policy if exists "recent_searches_select_own" on public.recent_searches;
create policy "recent_searches_select_own" on public.recent_searches
  for select to authenticated
  using ( (select auth.uid()) = user_id );

drop policy if exists "recent_searches_insert_own" on public.recent_searches;
create policy "recent_searches_insert_own" on public.recent_searches
  for insert to authenticated
  with check ( (select auth.uid()) = user_id );

drop policy if exists "recent_searches_update_own" on public.recent_searches;
create policy "recent_searches_update_own" on public.recent_searches
  for update to authenticated
  using ( (select auth.uid()) = user_id )
  with check ( (select auth.uid()) = user_id );
