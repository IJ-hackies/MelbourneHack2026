-- Public, privacy-safe aggregate for the marketing page's climate-impact
-- counter: total walks and total estimated avoided emissions across every
-- user, with no individual rows, destinations, or user_ids exposed. Real
-- data from the same walks table the app already writes on completion, not
-- a separate/fabricated figure.
create or replace function public.community_impact()
returns table (total_walks bigint, total_emissions_kg numeric)
language sql
security definer
set search_path = public
stable
as $$
  select count(*), coalesce(sum(emissions_kg), 0)
  from public.walks;
$$;

grant execute on function public.community_impact() to anon, authenticated;
