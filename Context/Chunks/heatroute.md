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
verified: 8c14561
---

## What this is

HeatRoute is a personalised Melbourne walking-route planner. It aims to balance
travel time with the heat, sun, crowds, traffic, and urban environment a
pedestrian will experience. V1 is walking-only; the final client is undecided.

The Next.js application now provides a public LeafRoute marketing surface,
Supabase authentication/onboarding, destination search, personalised planning
screens, saved places, preferences, account controls, and walk history. Route
and condition cards remain fixtures behind provider interfaces: there is no
map, routing graph, route prediction, or ML serving adapter. The ML lane has a
versioned data mirror, tested crowd/traffic targets, CUDA evaluations, and
Git-LFS-backed promoted model releases. (`src/app/page.tsx`,
`src/lib/providers/route-provider.ts`, `ml/README.md`)

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
- `ml/crowd/README.md` - target, feature-table, training, and evaluation contracts.
- `ml/model-handoff` - ready releases, integration contracts, compute, and publication.
- `ml/traffic/README.md`, `ml/traffic-processing`, `ml/traffic-training`, and
  `ml/traffic-modeling` - target, feature, CUDA evaluation, release, and software
  handoff contracts.
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
- Personalisation is optional: new users must receive useful routes immediately
  through sensible defaults.
- Lightweight history may track distance, walking time, or journey count.

### Current and future conditions

- Current sensors and weather describe conditions now; forecasts estimate what
  conditions will be when the user leaves later.
- The current UI accepts personalisation and route queries, but its provider
  fixtures ignore destination coordinates, departure time, and preferences.
- The implemented crowd model predicts hourly fixed-counter pedestrian flow,
  not area density; route-edge interpretation remains future integration work.
- The implemented traffic bundle predicts one-hour-ahead fixed SCATS
  intersection and Transport Activity countline volumes on separate scales;
  route congestion/travel-time interpretation remains future work.
- Building and tree shade is primarily a geometry/solar calculation, not
  necessarily ML. Do not force every environmental feature into a model.

### Emissions

- Show a transparent estimate of emissions avoided by walking rather than an
  equivalent car trip, optionally accumulated across walking history.
- Label it **estimated avoided emissions**; it does not prove the user would
  otherwise have driven. The illustrative `1.1 kg CO2e` example is not a fixed
  factor and requires documented calculation assumptions before use.

### V1 priorities

1. Build a usable Melbourne pedestrian routing graph.
2. Add crowd, traffic, weather, tree, building, and solar attributes.
3. Produce personalised routes that balance travel time and exposure.
4. Add lightweight forecasting for future crowd, traffic, and environment.
5. Estimate emissions avoided against an equivalent car journey.
6. Add basic personalisation and walking-history tracking.

Keep routing and data systems independent of the final frontend where practical.

### Deferred until the walking foundation is reliable

- Cycling routes and public-transport/multimodal routing.
- City infrastructure-planning tools and urban-intervention simulations.
- Advanced accessibility modes.
- Large-scale social or community features.
- Optional calendar linking must be opt-in, request minimal permissions, let
  users confirm inferred trips, and preserve fully manual routing.

## How to extend

For UI/application work, load `software/frontend-shell`; load
`software/auth-persistence` for user data and `software/routing-boundary` for
geocoding/provider integration. For crowd features,
load `ml/crowd-training`; for model work, load `ml/crowd-modeling` and the
cross-domain boundary in `ml/planned-forecasting`. For traffic work, load
`ml/traffic-processing`, `ml/traffic-training`, and `ml/traffic-modeling`.
Define a versioned software/ML contract for route-segment identity, prediction
time, value/unit, confidence, freshness, and missing-data behavior. The
fixed-site adapters should implement their versioned contracts as documented.

Add focused backend, data, routing, or mapping chunks only when real source
areas appear. Update the owning chunk and `verified` value whenever a listed
source changes.

## Gotchas

- This file is the product brief and context root, but product intent is not
  evidence that a feature, API, dataset, algorithm, or dependency exists.
- The current client is a Next.js application with a Vercel workflow, but the
  production architecture for real routing and ML inference is still open.
- Model evaluation, safety constraints, privacy, prediction missing-data
  fallbacks, and the production data refresh policy remain open. Dataset
  licences and geographic coverage are recorded but still require per-source
  compliance during publication. (`ml/data/catalog.json`)
- Retain the archived City hourly snapshot whole, but use only 1 November 2022
  through 20 August 2024 when harmonising; overlapping publisher revisions are
  not identical. (`ml/README.md`, `ml/data/catalog.json`)
- Crowd/traffic processing, training, and promotion have Python tests; there is
  no TypeScript check, backend, route mapping, inference API, or serving command.
