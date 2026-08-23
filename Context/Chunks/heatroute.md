---
id: heatroute
title: HeatRoute product vision and execution model
sources:
  - package.json
  - README.md
  - src/app/layout.tsx
  - src/app/page.tsx
  - next.config.ts
  - tsconfig.json
  - scripts/context-drift.mjs
  - ml/README.md
  - ml/crowd/SOFTWARE_HANDOFF.md
  - ml/traffic/SOFTWARE_HANDOFF.md
  - ml/data/catalog.json
  - ml/scripts/fetch_datasets.py
links: [software/frontend-shell, software/routing-boundary, software/auth-persistence, software/tooling, ml/planned-forecasting, ml/data-acquisition, ml/crowd-processing, ml/crowd-training, ml/crowd-modeling, ml/model-handoff, ml/traffic-processing, ml/traffic-training, ml/traffic-modeling]
verified: 7cdd997
---

## What this is

LeafRoute (rebranded from HeatRoute) is a personalised Melbourne walking-route
planner, marketed explicitly as climate action: reducing emissions by making
walking the easy alternative to driving, and adapting to rising heat by
routing around it live. V1 is walking-only, on a Next.js web client.

The Next.js application provides an apex-host marketing surface (with a real
cross-user community-impact counter) plus a guest-accessible planner, Supabase
auth/onboarding, destination search, saved places, preferences, account
controls, and walk history. Routing and conditions are no longer fixtures:
`api/route-planner.py` returns up to three real, meaningfully-differentiated
routes (fastest/shaded/quieter) over a real pedestrian graph, all three
conditions (weather/crowd/shade) are live, and shaded routing is
heat-adaptive. The ML lane's crowd model now actually serves both conditions
and route scoring, not just offline evaluation. (`src/app/page.tsx`,
`src/lib/providers/route-provider.ts`, `api/route-planner.py`)

The `software` workstream owns the application/routing surface; `ml` owns
forecasting and data science. Their interface must be explicit before integration.

## Key files

- `src/app/`, `src/components/`, `src/lib/`, `src/proxy.ts` - current App Router
  experience, Supabase-backed user flows, geocoding, and provider boundaries.
- `package.json` and root config files - Next.js 16, React 19, TypeScript,
  Tailwind CSS 4, Playwright, Supabase, build, and quality settings.
- `scripts/context-drift.mjs` - validates chunk structure and source freshness.
- `ml/README.md`, `ml/data/catalog.json`, `ml/scripts/fetch_datasets.py` - ML
  acquisition, profiles, licences, source URLs, and executable fetch boundary.
- `ml/model-handoff` - ready releases, integration contracts, compute, and
  publication; `ml/crowd-training`/`ml/traffic-training`/`-modeling` - per-domain
  target, feature, and CUDA evaluation contracts.
- `software/INDEX.md`, `ml/INDEX.md` - workstream context indexes.
- Commands: `npm run dev|lint|build|context:drift`; `npm run start` serves production.

## Invariants

### Route model

- Represent walkable streets as a pedestrian graph and convert environmental
  conditions into personalised costs for each segment.
- Balance distance/walking time against crowd density, vehicle traffic,
  temperature, humidity, wind, tree canopy, building shade, and sun exposure.
- Shade must vary with journey time by combining date/time and solar position
  with building geometry and tree canopy.
- Recommendations may trade a small delay for substantially greater comfort.

### Personalisation and history

- Support walking speed, speed-versus-comfort balance, heat/sun sensitivity,
  preference for quieter or less crowded routes, and lower-traffic preference.
- Personalisation is optional: new users get useful routes via sensible
  defaults. Lightweight history may track distance, time, or journey count.

### Current and future conditions

- Current sensors and weather describe conditions now; forecasts estimate what
  conditions will be when the user leaves later.
- Destination coordinates and departure time now flow into real provider
  calls (`api/route-planner.py`); `preferences` is still accepted by
  `RouteQueryInput` but not read by either provider implementation.
- The crowd model predicts hourly fixed-counter pedestrian flow, not area
  density. Route-edge interpretation now exists in a limited, pragmatic form
  (`route-planner.py` samples predictions at points along a candidate path to
  score a "quieter" route), not a proper effective-dated sensor-to-edge model.
- The implemented traffic bundle predicts one-hour-ahead fixed SCATS
  intersection and Transport Activity countline volumes on separate scales;
  route congestion/travel-time interpretation remains future work.
- Shade is implemented as a real tree-canopy-density grid
  (`ml/routing/scripts/build_shade_grid.py`), not building geometry/solar
  position — a deliberate simplification, labelled "Shade"/"canopy" in the UI
  rather than claiming solar-angle precision.

### Emissions

- Show a transparent estimate of emissions avoided by walking rather than an
  equivalent car trip, accumulated across walking history (per-user in
  history, cross-user on the marketing page via `community_impact()`).
- Label it **estimated avoided emissions**. The factor is `distanceKm * 0.19`
  (`src/lib/actions/walks.ts`) — still an illustrative constant, not a
  documented per-trip calculation.

### V1 priorities

1. ~~Build a usable Melbourne pedestrian routing graph.~~ Done (`ml/routing/`).
2. Crowd, weather, and tree-canopy attributes are real; traffic/building/solar
   are not.
3. Personalised routes balancing time/shade/crowd exist; preference-tuning
   does not yet feed them.
4. Lightweight forecasting for future crowd/traffic/environment: not started.
5. Emissions-avoided estimate: done, still illustrative (see above).
6. Basic personalisation/history tracking: done; preference-weighted routing
   is not.

### Deferred

Cycling/multimodal routing, infrastructure-planning tools, advanced
accessibility, large-scale social features, and calendar linking (must stay
opt-in with manual routing preserved if ever added).

## How to extend

For UI/application work, load `software/frontend-shell`; load
`software/auth-persistence` for user data and `software/routing-boundary` for
geocoding/routing/ML-adapter integration. For crowd model work, load
`ml/crowd-training`/`ml/crowd-modeling`; for traffic, load
`ml/traffic-processing`/`ml/traffic-training`/`ml/traffic-modeling`.

Add focused chunks only when real source areas appear; update the owning
chunk and `verified` value whenever a listed source changes.

## Gotchas

- This file is the product brief and context root, but product intent is not
  evidence that a feature, API, dataset, algorithm, or dependency exists.
- Real routing and ML inference are now live in production (Python Vercel
  Functions under `/api`, see `software/routing-boundary`), but traffic
  remains unwired into any provider, and route-edge crowd scoring is a
  pragmatic point-sampling approximation, not a validated model.
- Model evaluation, safety constraints, privacy, prediction missing-data
  fallbacks, and the production data refresh policy remain open. Dataset
  licences and geographic coverage are recorded but still require per-source
  compliance during publication. (`ml/data/catalog.json`)
- Retain the archived City hourly snapshot whole, but use only 1 November 2022
  through 20 August 2024 when harmonising; overlapping publisher revisions are
  not identical. (`ml/README.md`, `ml/data/catalog.json`)
- Crowd/traffic processing, training, and promotion have Python tests; there is
  still no standalone TypeScript type-check command (`tsc` runs as part of
  `next build`).
