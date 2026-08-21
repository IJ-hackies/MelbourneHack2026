create table if not exists public.saved_places (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('home', 'work', 'favorite')),
  label text not null,
  address text,
  lat double precision,
  lon double precision,
  created_at timestamptz not null default now()
);

-- Only one Home and one Work slot per user; unlimited favorites.
create unique index if not exists saved_places_one_home_per_user
  on public.saved_places (user_id) where (kind = 'home');
create unique index if not exists saved_places_one_work_per_user
  on public.saved_places (user_id) where (kind = 'work');

alter table public.saved_places enable row level security;

grant select, insert, update, delete on public.saved_places to authenticated;

drop policy if exists "saved_places_select_own" on public.saved_places;
create policy "saved_places_select_own" on public.saved_places
  for select to authenticated
  using ( (select auth.uid()) = user_id );

drop policy if exists "saved_places_insert_own" on public.saved_places;
create policy "saved_places_insert_own" on public.saved_places
  for insert to authenticated
  with check ( (select auth.uid()) = user_id );

drop policy if exists "saved_places_update_own" on public.saved_places;
create policy "saved_places_update_own" on public.saved_places
  for update to authenticated
  using ( (select auth.uid()) = user_id )
  with check ( (select auth.uid()) = user_id );

drop policy if exists "saved_places_delete_own" on public.saved_places;
create policy "saved_places_delete_own" on public.saved_places
  for delete to authenticated
  using ( (select auth.uid()) = user_id );
