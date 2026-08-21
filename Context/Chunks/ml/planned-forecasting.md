---
id: ml/planned-forecasting
title: Planned ML and forecasting workstream
sources:
  - Context/Chunks/heatroute.md
  - ml/README.md
links: [heatroute, software/frontend-shell, ml/data-acquisition]
verified: initial
---

## What this is

Forecasting remains planned rather than implemented. The product brief proposes
lightweight models for future crowd density, vehicle traffic, and local
environmental conditions, using historical signals such as time, day of week,
weather, and location. The ML lane now has reproducible raw-source acquisition,
but no features or predictions. (`Context/Chunks/heatroute.md`, `ml/README.md`)

No model files, Python package, transformation pipeline, FastAPI service,
feature store, or evaluation workflow exists. Do not describe a prediction as
available until its interface and verification are added.

## Key files

- `Context/Chunks/heatroute.md` - ML intent, candidate inputs, graph concept, and V1/deferred scope.
- `ml/README.md` - acquisition boundary and licence caveats; detailed dataset
  ownership lives in `ml/data-acquisition`.

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

When modelling begins, add actual feature/model/evaluation package paths and
split this chunk when those concepts become independently load-bearing. Record
the software integration contract in both workstreams.

## Gotchas

- The final client format is still undecided in the product brief, so do not
  couple the model design to a mobile or web-only assumption.
  (`Context/Chunks/heatroute.md`)
- Source licences and coverage are catalogued, but schema harmonisation,
  temporal joins, retraining, and prediction failure fallbacks remain open.
- The V1 brief is broad and includes deferred ideas. Keep a first model narrow
  enough for a two-person project and document deliberate deferrals in `STATE.md`.
