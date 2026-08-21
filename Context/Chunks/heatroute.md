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
  - ml/data/catalog.json
  - ml/scripts/fetch_datasets.py
links: [software/frontend-shell, software/tooling, ml/planned-forecasting, ml/data-acquisition]
verified: initial
---

## What this is

HeatRoute is a personalised Melbourne walking-route planner. Instead of always
choosing the shortest route, it aims to balance travel time with the heat, sun,
crowds, traffic, and urban environment a pedestrian will experience. V1 is
walking-only; cycling and other modes come after the walking experience works
well. The final client may be web, mobile, or another interface and is not yet
decided.

The application remains an intentionally empty Next.js frontend scaffold:
`src/app/page.tsx` renders `Hello world!`. The ML lane now includes a versioned
dataset catalog/downloader and an ignored local raw-data mirror, but no map,
application routing graph, API, persistence, feature pipeline, or ML model is
implemented. (`ml/README.md`, `ml/data/catalog.json`)

This is a two-person project. The `software` workstream owns the application,
routing system, and integration surface. The `ml` workstream owns forecasting
and data-science work. Their interface must be explicit before integration.

## Key files

- `src/app/layout.tsx`, `src/app/page.tsx`, `src/app/globals.css` - current App Router shell.
- `package.json` - Next.js 16, React 19, TypeScript, Tailwind CSS 4, and project commands.
- `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`, `postcss.config.mjs` - build and quality configuration.
- `scripts/context-drift.mjs` - validates chunk structure and source freshness.
- `ml/README.md`, `ml/data/catalog.json`, `ml/scripts/fetch_datasets.py` - ML
  source acquisition workflow, profiles, licences, known access restrictions,
  official source URLs, and the executable fetch boundary.
- `software/INDEX.md` - current application context; `ml/INDEX.md` - planned forecasting context.
- Primary commands: `npm run dev`, `npm run lint`, `npm run build`, and
  `npm run context:drift`; `npm run start` serves a production build.

Planned V1 stack beyond the installed frontend is MapLibre GL JS for mapping
and Python with FastAPI and Pydantic for the backend API. These dependencies and
services do not exist yet. (`package.json`)

## Invariants

### Route model

- Represent walkable streets as a pedestrian graph and convert environmental
  conditions into personalised costs for each segment.
- Balance distance/walking time against crowd density, vehicle traffic,
  temperature, humidity, wind, tree canopy, building shade, and sun exposure.
- Shade must vary with journey time by combining date/time and solar position
  with building geometry and tree canopy.
- Recommendations may trade a small delay for comfort—for example, an
  illustrative 17-minute route instead of 14 minutes when it is substantially
  shadier, quieter, and less crowded.

### Personalisation and history

- Support walking speed, speed-versus-comfort balance, heat/sun sensitivity,
  preference for quieter or less crowded routes, and lower-traffic preference.
- Personalisation is optional: new users must receive useful routes immediately
  through sensible defaults.
- Lightweight history may track distance, walking time, or journey count. A
  possible GitHub-style daily contribution graph can visualise activity.

### Current and future conditions

- Current sensors and weather describe conditions now; forecasts estimate what
  conditions will be when the user leaves later.
- Candidate lightweight models predict crowd density from historical sensors,
  time, weekday, weather, and location; vehicle traffic from historical traffic
  and temporal patterns; and local conditions from forecasts plus street traits.
- Building and tree shade is primarily a geometry/solar calculation, not
  necessarily ML. Do not force every environmental feature into a model.
- The result is a time-dependent pedestrian graph whose segment costs change
  through the day, eventually supporting route and departure-time suggestions.

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
- Optional calendar linking that can read an upcoming event's time and location,
  suggest the destination and departure window, and avoid manual trip entry.
  It must be opt-in, request minimal permissions, let users confirm or edit the
  inferred trip, fall back to manual entry when an event has no usable time or
  location, and preserve fully manual routing without calendar access.

## How to extend

For UI/application work, load `software/frontend-shell`. For forecasting work,
load `ml/planned-forecasting`. Define a versioned software/ML contract for
route-segment identity, prediction time, value/unit, confidence, freshness, and
missing-data behavior before either lane consumes the other's output.

Add focused backend, data, routing, or mapping chunks only when real source
areas appear. Update the owning chunk and `verified` value whenever a listed
source changes.

## Gotchas

- This file is the product brief and context root, but product intent is not
  evidence that a feature, API, dataset, algorithm, or dependency exists.
- The current web scaffold does not settle the final client or deployment model.
- Model evaluation, safety constraints, privacy, prediction missing-data
  fallbacks, and the production data refresh policy remain open. Dataset
  licences and geographic coverage are recorded but still require per-source
  compliance during publication. (`ml/data/catalog.json`)
- There are no tests, standalone type-check, backend, mapping, feature,
  training, evaluation, or model-serving commands.
- This chunk remains `verified: initial` while its new ML sources are untracked;
  commit the acquisition work, then run `/reupdate` to establish a comparison
  baseline.
