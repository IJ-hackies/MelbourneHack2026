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
links: [heatroute, software/frontend-shell, ml/planned-forecasting, ml/model-handoff]
verified: edcfab5
---

## What this is

The application has a provider-agnostic TypeScript seam for future pedestrian
routing and environmental conditions. Route options are still three fixed
fixtures (minutes/distance/segments), but each now carries real `geometry`
(start/end coordinates from the resolved destination, optional `path`).
Conditions now mix two live signals (weather via `/api/weather`, crowd via
`api/crowd-inference.py`) with one remaining fixed placeholder (shade).
Destination search calls a server route that bounds Nominatim results to
greater Melbourne.
(`src/lib/providers/*.ts`, `src/app/api/geocode/route.ts`, `src/app/api/weather/route.ts`)

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
- `src/lib/providers/route-provider.ts` - `RouteProvider` contract and fixture
  implementation (now geometry-aware).
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

- The three route options' minutes/distance/segments still ignore destination,
  time, and preferences — only `geometry` reflects the real query now.
- Route ids are reused route-type strings; a real provider must resolve them
  together with the original query rather than as durable global identities.
- The geocoder is discovery only; it does not construct a walking graph or
  validate that a result is pedestrian-accessible.
- `RouteMap` (`software/frontend-shell`) draws a straight line when `path` is
  absent — this is intentionally not a real route, just a visualization of
  start/end.
