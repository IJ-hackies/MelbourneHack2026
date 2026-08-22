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
links: [heatroute, software/frontend-shell, ml/planned-forecasting, ml/model-handoff]
verified: 8c14561
---

## What this is

The application has a provider-agnostic TypeScript seam for future pedestrian
routing and environmental conditions. It currently returns three fixed route
fixtures and three fixed condition fixtures. Destination search calls a server
route that bounds Nominatim results to greater Melbourne.
(`src/lib/providers/*.ts`, `src/app/api/geocode/route.ts`)

## Key files

- `src/lib/providers/types.ts` - place, preference, route, segment, and condition
  shapes shared by UI and provider implementations.
- `src/lib/providers/route-provider.ts` - `RouteProvider` contract and fixture
  implementation.
- `src/lib/providers/condition-provider.ts` - `ConditionProvider` contract and
  fixture implementation.
- `src/app/api/geocode/route.ts` - Melbourne-bounded Nominatim proxy with a
  five-second timeout and 60-second Next fetch revalidation.
- `src/lib/routes.ts` - shared departure-time presentation helper.

## Invariants

- UI callers depend on exported interfaces/types, not concrete provider classes.
- A route query may carry coordinates, departure time, and partial user
  preferences; future implementations must define missing-data behavior.
- Fixed-counter ML outputs must not be treated as route-edge density or traffic
  without the calibration described by the ML handoffs.
- Nominatim queries shorter than three characters return no results and upstream
  failures surface as a controlled 502 response.

## How to extend

Implement providers behind the current interfaces or version the interfaces
when real geometry/confidence/freshness requires richer types. Define stable
route-segment identity and model-to-edge calibration before wiring promoted ML
models into route scoring.

## Gotchas

- The fixtures ignore destination, time, and preferences despite accepting them.
- Route ids are fixture-local strings, not durable graph or database identities.
- The geocoder is discovery only; it does not construct a walking graph or
  validate that a result is pedestrian-accessible.
