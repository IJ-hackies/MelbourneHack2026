create table if not exists public.walks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  route_id text not null,
  destination text not null,
  minutes smallint not null,
  distance_km numeric(5, 2) not null,
  emissions_kg numeric(5, 2) not null,
  completed_at timestamptz not null default now()
);

create index if not exists walks_user_completed_at_idx
  on public.walks (user_id, completed_at desc);

alter table public.walks enable row level security;

grant select, insert on public.walks to authenticated;

drop policy if exists "walks_select_own" on public.walks;
create policy "walks_select_own" on public.walks
  for select to authenticated
  using ( (select auth.uid()) = user_id );

drop policy if exists "walks_insert_own" on public.walks;
create policy "walks_insert_own" on public.walks
  for insert to authenticated
  with check ( (select auth.uid()) = user_id );
