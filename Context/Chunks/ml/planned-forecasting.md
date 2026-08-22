---
id: ml/planned-forecasting
title: Planned ML and forecasting workstream
sources:
  - Context/Chunks/heatroute.md
  - ml/README.md
  - ml/crowd/README.md
  - ml/crowd/SOFTWARE_HANDOFF.md
  - ml/traffic/README.md
  - ml/traffic/SOFTWARE_HANDOFF.md
links: [heatroute, software/frontend-shell, ml/data-acquisition, ml/crowd-processing, ml/crowd-training, ml/crowd-modeling, ml/model-handoff, ml/traffic-processing, ml/traffic-training, ml/traffic-modeling]
verified: initial
---

## What this is

Crowd and traffic target processing, feature construction, CUDA model training,
offline evaluation, and versioned model promotion are implemented. Production
inference, environmental forecasting, uncertainty, freshness, effective-dated
route-edge mapping, and application integration remain planned. Traffic uses a
two-model source-stratified release because SCATS intersection and Transport
Activity countline counts have incompatible scales.
(`Context/Chunks/heatroute.md`, `ml/README.md`, `ml/crowd/README.md`)

Each domain has tested target, feature, fit/evaluate, and promotion boundaries.
Full reports and non-winning models remain ignored; promoted UBJSON is tracked
with Git LFS. Crowd and traffic now have documented `crowd-inference/v1` and
`traffic-inference/v1` fixed-site JSON contracts, metadata-driven encoding, and
freshness warnings; no FastAPI service, adapter implementation, route-edge
feature store, online prediction command, or calibrated uncertainty exists. Do
not describe fixed-site flow predictions as route-level predictions.

## Key files

- `Context/Chunks/heatroute.md` - ML intent, candidate inputs, graph concept, and V1/deferred scope.
- `ml/README.md` - acquisition boundary and licence caveats; detailed dataset
  ownership lives in `ml/data-acquisition`.
- `ml/crowd/README.md`, `ml/traffic/README.md` - domain dataset ownership and
  the single-copy rule for multimodal Transport Activity snapshots.
- `ml/crowd-processing` - implemented target schema, source precedence,
  missingness, repair, and validation contract.
- `ml/crowd-training` - implemented two-table feature schema, temporal joins,
  leakage controls, previews, and chronological comparison contract.
- `ml/crowd-modeling` - implemented XGBoost fit/evaluate command, CUDA/CPU
  behavior, models, metrics, matched ablation, and current result.
- `ml/traffic-processing` - implemented traffic target, reviewed countline
  registry, SCATS reduction, missingness/timezone rules, and completeness gate.
- `ml/traffic-training` - implemented one-hour-ahead base/lag features,
  chronological 2024/2025/2026 split, bounded DuckDB build, and readiness manifest.
- `ml/traffic-modeling` - implemented source-stratified CUDA XGBoost training,
  validation-only selection, held-out metrics, integrity checks, and promotion.
- `ml/model-handoff` - release inventory, software contracts, exact tree
  capacity, measured CPU/RAM smoke envelope, training compute, and publication.
- `ml/crowd/SOFTWARE_HANDOFF.md` - crowd request/response, metadata-driven
  features, unseen sensors, weather/history freshness, and quality states.
- `ml/traffic/SOFTWARE_HANDOFF.md` - software-facing model paths, metadata-driven
  encoding, request/response, quality, lag freshness, and normalization contract.

## Invariants

- ML owns forecasting and data-science implementation; software owns the web
  experience and integration. Keep the two-person boundary visible.
- Software may consume crowd/traffic only through their documented fixed-site
  contracts; route segments still need a separate identity/calibration contract.
- Candidate crowd models must use the shared test keys through 11 May 2026 and
  report a matched recent-window ablation; a random adjacent-row split is not a
  valid comparison. (`ml/crowd/README.md`)
- Geometry-based shade (buildings, trees, sun position) may be computed without
  ML according to the brief; do not force every environmental feature into a
  model. (`Context/Chunks/heatroute.md`)
- Estimated avoided emissions must remain transparent and labelled as an
  estimate; it is not proof that a user would otherwise have driven.
  (`Context/Chunks/heatroute.md`)

## How to extend

Use the promoted domain models only through explicit feature/encoder metadata.
Implement each adapter exactly from its handoff, then separately define
retraining, uncertainty, and route-segment calibration.

## Gotchas

- The final client format is still undecided in the product brief, so do not
  couple the model design to a mobile or web-only assumption.
  (`Context/Chunks/heatroute.md`)
- The first crowd model and its contract exist, but the adapter, tuning,
  retraining, spatial route joins, uncertainty, and fallbacks remain open.
- Traffic has a complete 2024–2026 offline model bundle, but no app-facing
  adapter or route calibration. Its v1 diagnostics limitation is documented;
  the full 2023 archive is deliberately excluded.
- The V1 brief is broad and includes deferred ideas. Keep a first model narrow
  enough for a two-person project and document deliberate deferrals in `STATE.md`.
