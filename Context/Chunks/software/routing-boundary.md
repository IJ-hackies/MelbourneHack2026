---
id: software/routing-boundary
title: Geocoding and route-provider boundary
sources:
  - src/app/api/geocode/route.ts
  - src/app/api/plan-routes/route.ts
  - api/shade.py
  - src/lib/providers/types.ts
  - src/lib/providers/route-provider.ts
  - src/lib/providers/condition-provider.ts
  - src/lib/routing/route-client.ts
  - src/lib/routing/route-client-browser.ts
  - src/lib/ml/ml-client.ts
  - src/lib/use-live-location.ts
  - src/components/route-map.tsx
  - api/route-planner.py
  - api/crowd-inference.py
  - api/_shared/router.py
  - api/_shared/graph_loader.py
  - api/_shared/shade_lookup.py
  - api/_shared/crowd_model.py
  - api/_shared/feature_lookup.py
  - supabase/migrations/20260823210000_community_impact_rpc.sql
links: [heatroute, software/frontend-shell, ml/planned-forecasting, ml/model-handoff]
verified: 7cdd997
---

## What this is

Routing is real end-to-end. `api/route-planner.py` runs Dijkstra
(`api/_shared/router.py`) over a checksum-verified graph of the City of
Melbourne Pedestrian Network (`ml/routing/`), and returns up to **three** real
candidates per query — `fastest`, `shaded`, `quieter` — plus a top-level
`heat_context`. A candidate is only included when it's a *meaningfully*
different real trade-off from every candidate already accepted
(`_meaningfully_different`: distance AND its headline metric both have to move
by a real threshold), specifically to prevent two near-identical paths (common
on short trips) from showing up as fake-looking duplicate cards.

- `shaded` biases Dijkstra toward higher-canopy-density edges
  (`router.SHADE_BIAS`, edges carry a real `canopy_density` weight baked into
  `graph.json` from `ml/routing/scripts/build_shade_grid.py`'s tree-canopy
  grid). The bias itself is **heat-adaptive**: `_heat_context()` fetches the
  real current Melbourne CBD temperature and scales the bias up on a hot day
  (`HEAT_ADVISORY_TEMP_C`/`HEAT_EXTREME_TEMP_C`), which is the app's actual
  climate-adaptation behaviour, not just a UI label — the plan page shows a
  "Heat advisory" banner sourced from the same `heat_context`.
- `quieter` penalises graph nodes near currently-busy live pedestrian sensors
  within a padded corridor around the origin-destination bbox
  (`_build_crowd_penalty`), using real per-sensor predictions from
  `api/_shared/crowd_model.py` (shared with `crowd-inference.py` so both call
  sites use one model-loading/prediction path).
- Every candidate also carries `canopy_density_avg` and
  `pedestrian_flow_avg_per_hour` — real, sampled-along-the-path figures (see
  Invariants) used to decide tags (`most_shaded`/`least_crowded`/`fastest`)
  and shown on the route detail page.

Routing is client-driven from the plan page (`/api/plan-routes`, a Next Route
Handler wrapping `routeProvider.listRoutes` — see `software/frontend-shell`)
specifically so the origin is the user's real live-location fix, not a static
default; the same resolved origin is carried to `/route/[id]` via URL params.

Conditions (`condition-provider.ts`) call three real live signals: weather
(`/api/weather`), crowd (`api/crowd-inference.py`), and shade
(`api/shade.py`, backed by the same canopy grid as routing). There is no
placeholder condition left. Weather's "Feels ___" label is derived from the
real temperature (cold/cool/mild/warm/hot bands), not hardcoded. Crowd's
displayed value compares the live prediction against that sensor's own real
rolling-168h average ("Busier/Quieter/Typical than usual") rather than
showing a bare, hard-to-interpret rate.

Destination search (`api/geocode/route.ts`) proxies **Photon**
(`photon.komoot.io`), not Nominatim — Nominatim's tokenizer failed on
mid-word partial queries and on a missing apostrophe in a real street name
("Abeckett" vs "A'Beckett"), both verified directly against both APIs; Photon
indexes n-grams and handles both.

The marketing page's community-impact counter calls `community_impact()`, a
`security definer` SQL function (migration above) returning only the
aggregate walk count/emissions sum across all users — no individual rows or
`user_id`s — granted to `anon` since the marketing page is unauthenticated.

## Key files

- `src/lib/providers/route-provider.ts` - `RouteProvider` contract;
  `listRoutes` now returns `{routes, heatContext}`; maps real backend tag ids
  to `RouteTag` labels; falls back to a straight-line "Estimated" route only
  when every backend candidate is unavailable.
- `src/app/api/plan-routes/route.ts` - client-callable Route Handler wrapping
  `routeProvider.listRoutes` so the browser can supply a real origin.
- `src/lib/routing/route-client.ts` - server-only typed fetch wrapper for
  `api/route-planner.py`, returns the full `PlannedRoute[]` + `HeatContext`.
- `src/lib/routing/route-client-browser.ts` - client-safe sibling (no
  `next/headers`) used only by `route-map.tsx`'s mid-walk re-routing; always
  tracks the `"fastest"` candidate, not the plan-time selection.
- `api/route-planner.py` - candidate generation, dedup thresholds
  (`MIN_DISTANCE_DIFFERENCE_FRACTION`, `MIN_SHADE_DIFFERENCE`,
  `MIN_CROWD_DIFFERENCE_FRACTION`), and `_heat_context()`.
- `api/_shared/router.py` - Dijkstra with optional `shade_bias` (edge
  multiplier) and `node_penalty`/`penalty_weight` (crowd additive cost);
  `path_avg_shade` for length-weighted route scoring.
- `api/_shared/crowd_model.py` - shared XGBoost load/predict, used by both
  `crowd-inference.py` and `route-planner.py`'s corridor sampling.
- `api/_shared/shade_lookup.py`, `api/shade.py` - point canopy-density query
  against the same promoted grid `route-planner.py` uses per-edge.
- `src/lib/providers/condition-provider.ts` - `ConditionProvider`; all three
  conditions (weather, crowd, shade) are real, no placeholders remain.
- `src/app/api/geocode/route.ts` - Photon-backed, Melbourne-biased geocoder.

## Invariants

- UI callers depend on exported interfaces/types, not concrete provider classes.
- A candidate route is only tagged with a superlative when it's genuinely,
  meaningfully different — never fabricate differentiation between routes
  that would otherwise round to the same displayed numbers.
- `canopy_density_avg`/`pedestrian_flow_avg_per_hour` are null (not zero-filled)
  when they can't be computed from real data; crowd sampling dedupes by
  resolved sensor id before predicting, and Open-Meteo/sensor-location lookups
  are cached (per-hour / 1h / 5min respectively) — this was a real ~47s→~10s
  latency fix, not just an optimisation.
- Fixed-counter ML outputs must not be treated as route-edge density without
  the calibration described by the ML handoffs; `CrowdSignal`/`TrafficSignal`
  never carry a fabricated confidence value.
- `xgboost-cpu` in root `requirements.txt` must stay pinned to the exact
  version the promoted crowd model was trained with (`ml/crowd/models/
  all-history-v1/metadata.json`'s `xgboost_version`) — a version mismatch
  (3.0.5 vs the trained 3.4.1) silently produced predictions ~1000x too low
  while still reporting a plausible-looking `degraded` status. Verify any
  future bump with a real prediction, not just successful model loading.

## How to extend

Traffic inference (`api/traffic-inference.py`) still always reports
`quality.status: "unavailable"` — SCATS/Transport Activity have no live query
endpoint, only periodic batch archives (see `ml/data/catalog.json`).

## Gotchas

- The pedestrian graph (`ml/routing/`) covers only the City of Melbourne
  municipality bbox; an OSM-based supplement for wider coverage is a
  documented, unimplemented fast-follow (`ml/routing/README.md`).
- `canopy_density` is a tree-canopy-centroid density proxy (grid cell count,
  normalised to a 95th-percentile), not a solar-shade calculation — labelled
  "Shade"/"Tree canopy nearby" in the UI, deliberately not claiming
  sun-angle precision.
- `route-map.tsx`'s mid-walk re-routing (`callRoutePlannerFromBrowser`) always
  re-targets the `"fastest"` candidate on movement, not whichever candidate
  was originally selected on the plan page — a deliberate simplification, not
  a bug.
