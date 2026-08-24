-- Counts a guest's completed walk toward the public community-impact
-- counter even if they never sign in. No user linkage, destination, or
-- route id is stored here -- just enough to sum distance/emissions, since
-- this table only ever feeds the aggregate community_impact() RPC below,
-- never a per-row read.
create table if not exists public.guest_walk_stats (
  id uuid primary key default gen_random_uuid(),
  distance_km numeric(5, 2) not null,
  emissions_kg numeric(5, 2) not null,
  created_at timestamptz not null default now(),
  -- Same real-world bound a walking app can actually produce; guards
  -- against a junk/abusive insert skewing the public total by orders of
  -- magnitude. Not a hard security boundary (this is a public stat, not
  -- sensitive data) -- just a sanity clamp.
  constraint guest_walk_stats_distance_km_check check (distance_km > 0 and distance_km <= 100),
  constraint guest_walk_stats_emissions_kg_check check (emissions_kg >= 0 and emissions_kg <= 50)
);

alter table public.guest_walk_stats enable row level security;

-- No RLS policy at all -- neither anon nor authenticated can select,
-- insert, update, or delete this table directly. All access goes through
-- the two security definer functions below, so a plain PostgREST insert
-- can never return the new row's id (Postgres RLS applies the SELECT
-- policy to RETURNING too, and there deliberately isn't one), and nothing
-- can ever list or read back an individual row.
create or replace function public.create_guest_walk_stat(p_distance_km numeric, p_emissions_kg numeric)
returns uuid
language sql
security definer
set search_path = public
as $$
  insert into public.guest_walk_stats (distance_km, emissions_kg)
  values (p_distance_km, p_emissions_kg)
  returning id;
$$;

revoke execute on function public.create_guest_walk_stat(numeric, numeric) from public;
grant execute on function public.create_guest_walk_stat(numeric, numeric) to anon, authenticated;

-- Lets a later sign-in "claim" (lib/actions/walks.ts's logWalk, once the
-- walk is re-inserted into public.walks under the new account) remove the
-- matching guest-stat row first, so the same walk is never counted twice
-- toward the public total. security definer since neither anon nor
-- authenticated has a delete policy on this table -- the random uuid
-- itself (only ever known to the client that inserted it) is the only
-- thing gating which row gets removed.
create or replace function public.claim_guest_walk_stat(p_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.guest_walk_stats where id = p_id;
$$;

revoke execute on function public.claim_guest_walk_stat(uuid) from public;
grant execute on function public.claim_guest_walk_stat(uuid) to anon, authenticated;

-- Supersedes the community_impact() from 20260823210000: same aggregate,
-- now also summing public.guest_walk_stats (anonymous walks that were
-- never claimed into an account) and returning a total-distance figure
-- alongside emissions, since the marketing page now shows both. Postgres
-- won't let `create or replace` change a function's return row shape, so
-- the old two-column signature has to go first.
drop function if exists public.community_impact();

create function public.community_impact()
returns table (total_walks bigint, total_emissions_kg numeric, total_distance_km numeric)
language sql
security definer
set search_path = public
stable
as $$
  select
    (select count(*) from public.walks) + (select count(*) from public.guest_walk_stats),
    coalesce((select sum(emissions_kg) from public.walks), 0)
      + coalesce((select sum(emissions_kg) from public.guest_walk_stats), 0),
    coalesce((select sum(distance_km) from public.walks), 0)
      + coalesce((select sum(distance_km) from public.guest_walk_stats), 0);
$$;

grant execute on function public.community_impact() to anon, authenticated;
