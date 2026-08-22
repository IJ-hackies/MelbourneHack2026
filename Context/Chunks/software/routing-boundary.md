---
id: software/routing-boundary
title: Geocoding and route-provider boundary
sources:
  - src/app/api/geocode/route.ts
  - src/lib/providers/types.ts
  - src/lib/providers/route-provider.ts
  - src/lib/providers/condition-provider.ts
  - src/lib/routes.ts
  - src/app/route/[id]/page.tsx
  - src/app/api/weather/route.ts
  - src/lib/base-url.ts
  - src/lib/ml/ml-client.ts
  - src/lib/routing/route-client.ts
  - src/lib/use-live-location.ts
  - src/components/route-map.tsx
links: [heatroute, software/frontend-shell, ml/planned-forecasting, ml/model-handoff]
verified: edcfab5
---

## What this is

The application has a provider-agnostic TypeScript seam for pedestrian
routing and environmental conditions. Routing is now real: `route-provider.ts`
calls `api/route-planner.py` (a checksum-verified graph over the City of
Melbourne Pedestrian Network, `ml/routing/`) for a genuine shortest-path route
with real geometry/distance/time; there is now only **one** route per query
(not three), since per-edge heat/crowd/traffic costs don't exist to honestly
differentiate "comfort/direct/quiet" — see `ml/model-handoff` for why. If the
query falls outside the graph's coverage, it falls back to a straight-line
estimate explicitly labelled "Estimated," never presented as equivalent to a
real route (`RouteOption.quality`).

Conditions mix two live signals (weather via `/api/weather`, crowd via
`api/crowd-inference.py`) with one remaining fixed placeholder (shade).
Destination search calls a server route that bounds Nominatim results to
greater Melbourne.
(`src/lib/providers/*.ts`, `src/app/api/geocode/route.ts`, `src/app/api/weather/route.ts`)

The route-detail map now also does live location tracking: `use-live-location.ts`
wraps `navigator.geolocation.watchPosition` (never fabricates a position — every
non-tracking state is a distinct, honest reason there isn't one), and
`route-map.tsx` replaces the static start marker with a live one once a fix
arrives, auto-follows the camera (suspending on manual pan/zoom with a
Recenter control), and recomputes distance/ETA-remaining live via
`live-progress-context.tsx`, consumed by `ActiveWalk`.

`conditionProvider` now calls `ml-client.ts`'s `callCrowdInference` with the
destination's raw lat/lon; the Python endpoint resolves the nearest live
pedestrian-counting sensor itself (`feature_lookup.resolve_nearest_crowd_sensor`,
a live Opendatasoft query against the sensor-locations dataset, not a
committed snapshot) before predicting. A resolved-but-distant sensor (>0.5km)
adds a quality warning rather than being silently presented as "at" the
destination; no sensors found → `quality.status: "unavailable"`, surfaced in
the UI as "Unavailable" rather than a fabricated number. Traffic inference
(`api/traffic-inference.py`) remains unwired — see "How to extend".

## Key files

- `src/lib/providers/types.ts` - place, coordinate, route geometry, preference,
  route, segment, condition, and raw ML signal (`CrowdSignal`/`TrafficSignal`,
  deliberately no confidence field) shapes shared by UI and provider implementations.
- `src/lib/providers/route-provider.ts` - `RouteProvider` contract; calls
  `callRoutePlanner` for one real route, falls back to a labelled
  straight-line estimate on failure/out-of-coverage.
- `src/lib/routing/route-client.ts` - typed fetch wrapper for
  `api/route-planner.py`, degrading to `path: null` on any failure.
- `src/lib/use-live-location.ts`, `src/components/route-map.tsx` - live
  geolocation tracking and camera-follow on the route map.
- `src/lib/providers/condition-provider.ts` - `ConditionProvider` contract;
  `LiveConditionProvider` calls `/api/weather` and `callCrowdInference` for two
  real conditions, and still returns one fixed placeholder for shade.
- `src/app/api/geocode/route.ts` - Melbourne-bounded Nominatim proxy with a
  five-second timeout and 60-second Next fetch revalidation.
- `src/app/api/weather/route.ts` - Open-Meteo current-conditions proxy (no key),
  same resilience conventions as geocode but a 600-second cache.
- `src/lib/base-url.ts` - resolves this deployment's own origin
  (`VERCEL_URL` in production, localhost in dev) for server-to-server calls
  to `/api/weather` and the Python `/api/*-inference` functions.
- `src/lib/ml/ml-client.ts` - typed fetch wrappers for `crowd-inference/v1`
  (called from `condition-provider.ts` with `{lat, lon}`) and
  `traffic-inference/v1` (not yet called from any provider).
- `src/lib/routes.ts` - shared departure-time presentation helper.

## Invariants

- UI callers depend on exported interfaces/types, not concrete provider classes.
- `RouteQueryInput.destination` requires resolved coordinates (`ResolvedPlace`);
  callers must not invoke providers with only a label — `src/app/page.tsx` and
  `route/[id]/page.tsx` now gate on `hasCoordinates`/valid `lat`/`lon` before
  calling either provider.
- Fixed-counter ML outputs must not be treated as route-edge density or traffic
  without the calibration described by the ML handoffs; `CrowdSignal`/
  `TrafficSignal` never carry a fabricated confidence value.
- Nominatim queries shorter than three characters return no results and upstream
  failures surface as a controlled 502 response; the weather route follows the
  same failure-shape convention.

## How to extend

Implement remaining providers behind the current interfaces or version the
interfaces when real geometry/confidence/freshness requires richer types.
Traffic inference (`api/traffic-inference.py`) always reports
`quality.status: "unavailable"` today — SCATS/Transport Activity have no live
query endpoint, only periodic batch archives (see `ml/data/catalog.json`); a
real traffic signal needs a scheduled ingestion job, unlike crowd's live
sensor-location + counts lookup.

## Gotchas

- The single route still ignores preferences/departure time — only geometry,
  distance, and time are now real.
- The route id is a fixed constant (`"walking-route"`), not a durable
  per-query identity — old bookmarked `/route/comfort` etc. links now 404.
- The geocoder is discovery only; it does not validate that a result is
  pedestrian-accessible.
- `RouteMap` draws a straight line when `path` is absent (out-of-coverage
  fallback) — this is intentionally not a real route.
- The pedestrian graph (`ml/routing/`) covers only the City of Melbourne
  municipality bbox; an OSM-based supplement for wider coverage is a
  documented, unimplemented fast-follow (`ml/routing/README.md`).
