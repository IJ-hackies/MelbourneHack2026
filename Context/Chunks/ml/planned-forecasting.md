---
id: ml/planned-forecasting
title: Planned ML and forecasting workstream
sources:
  - Context/Chunks/heatroute.md
links: [heatroute, software/frontend-shell]
verified: initial
---

## What this is

The ML workstream is planned, not implemented. The product brief proposes
lightweight models for future crowd density, vehicle traffic, and local
environmental conditions, using historical signals such as time, day of week,
weather, and location. It also describes a time-dependent pedestrian graph and
possible departure-time optimisation. (`Context/Chunks/heatroute.md`)

No model files, Python package, data pipeline, FastAPI service, feature store,
or evaluation workflow exists in the current repository. Do not describe a
prediction as available until its source, interface, and verification are added.

## Key files

- `Context/Chunks/heatroute.md` - ML intent, candidate inputs, graph concept, and V1/deferred scope.
- No ML source path exists yet; this is an intentional coverage marker for the planned workstream.

## Invariants

- ML owns forecasting and data-science implementation; software owns the web
  experience and integration. Keep the two-person boundary visible.
- Future ML output must have an explicit contract for route segments, time,
  confidence/quality, and missing-data behavior before software consumes it.
- Geometry-based shade (buildings, trees, sun position) may be computed without
  ML according to the brief; do not force every environmental feature into a
  model. (`Context/Chunks/heatroute.md`)
- Estimated avoided emissions must remain transparent and labelled as an
  estimate; it is not proof that a user would otherwise have driven.
  (`Context/Chunks/heatroute.md`)

## How to extend

When ML work begins, add the actual package and data paths, then split this
chunk into focused chunks for ingestion, features, models, evaluation, and
serving if those concepts are independently load-bearing. Record the software
integration contract in both workstreams and add sources to make drift checks
meaningful.

## Gotchas

- The final client format is still undecided in the product brief, so do not
  couple the model design to a mobile or web-only assumption.
  (`Context/Chunks/heatroute.md`)
- Historical sensor/weather data, licences, coverage, retraining, and failure
  fallbacks are open design questions; they are not silently solved by this
  chunk.
- The V1 brief is broad and includes deferred ideas. Keep a first model narrow
  enough for a two-person project and document deliberate deferrals in `STATE.md`.
