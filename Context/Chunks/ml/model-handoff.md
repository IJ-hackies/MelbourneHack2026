---
id: ml/model-handoff
title: Ready-model software handoff and compute envelope
sources:
  - .gitignore
  - .gitattributes
  - ml/requirements.txt
  - ml/crowd/SOFTWARE_HANDOFF.md
  - ml/crowd/models/all-history-v1/model.ubj
  - ml/crowd/models/all-history-v1/metadata.json
  - ml/crowd/models/all-history-v1/README.md
  - ml/traffic/SOFTWARE_HANDOFF.md
  - ml/traffic/models/source-stratified-v1/release_manifest.json
  - ml/traffic/models/source-stratified-v1/checksums.json
  - ml/traffic/models/source-stratified-v1/scats-intersection/model.ubj
  - ml/traffic/models/source-stratified-v1/scats-intersection/metadata.json
  - ml/traffic/models/source-stratified-v1/transport-activity-countline/model.ubj
  - ml/traffic/models/source-stratified-v1/transport-activity-countline/metadata.json
  - api/crowd-inference.py
  - api/traffic-inference.py
  - api/_shared/model_loader.py
  - api/_shared/feature_lookup.py
  - api/_shared/crowd_model.py
links: [heatroute, ml/crowd-training, ml/crowd-modeling, ml/traffic-training, ml/traffic-modeling, ml/planned-forecasting, software/routing-boundary]
verified: 7cdd997
---

## Implementation status (update)

Both contracts are now implemented as root-level Python Vercel Functions —
`api/crowd-inference.py` and `api/traffic-inference.py` — sharing
`api/_shared/model_loader.py` (checksum verification + cached booster
loading) and `api/_shared/feature_lookup.py` (live request-time feature
assembly, distinct from the offline `ml/` training pipeline). Crowd model
load/predict was further extracted into `api/_shared/crowd_model.py` so
`api/route-planner.py` can score candidate routes' real crowd levels without
a self HTTP call — see `software/routing-boundary`. Crowd is wired into
`conditionProvider` and route scoring; traffic is implemented but not called
from any provider yet.

`_fetch_weather`/`_fetch_sensor_locations`/`_fetch_sensor_history` in
`feature_lookup.py` are cached (per-hour, 1h, 5min) — without this,
`route-planner.py` sampling multiple points per candidate hit Open-Meteo/
Opendatasoft once per sample with no reuse, a real ~47s→~10s latency bug.

Crowd's feature lookup genuinely queries live data: the City's pedestrian-counts
dataset exposes an Opendatasoft Explore v2.1 `/records` endpoint (filterable by
`location_id`, paginated at its 100-row-per-request cap — an earlier `limit=200`
silently 400'd and made crowd inference always fail), plus Open-Meteo for
target-hour temperature. The crowd endpoint also accepts `{lat, lon}` directly
and resolves the nearest sensor via `feature_lookup.resolve_nearest_crowd_sensor`
— a live query, not a committed snapshot; a sensor farther than 0.5km adds a
quality warning. Traffic's lookup always returns `quality.status:
"unavailable"` — SCATS/Transport Activity are batch-archive-only
(`ml/data/catalog.json`) with no live endpoint; a real signal needs a
scheduled ingestion job.

## What is ready

There are two logical promoted prediction releases and three UBJSON binaries:
`all-history-v1` is one crowd estimator; vehicle prediction is the inseparable
`source-stratified-v1` bundle containing separate SCATS-intersection and
Transport-Activity-countline estimators. All predict a fixed observation unit
one hour ahead. None predicts route-edge density, congestion, speed, or travel
time. (`ml/crowd/SOFTWARE_HANDOFF.md`, `ml/traffic/SOFTWARE_HANDOFF.md`)

| Release/model | Output | Size | Features | Trees |
| --- | --- | ---: | ---: | --- |
| crowd `all-history-v1` | pedestrians/counter/hour | 65.8 MiB | 29 | 2,500 |
| traffic SCATS | vehicles/intersection/hour | 31.7 MiB | 59 | 300 |
| traffic countline | vehicles/countline/hour | 4.3 MiB | 59 | 300 |

These are boosted-tree ensembles (max depth 8), not neural networks. Artifact
bytes, rounds, features, and hashes live in metadata.

## Software architecture and contracts

The browser/Next.js client must call a Python backend adapter, never fetch or
parse model binaries directly. Materialize Git LFS, verify byte count/SHA-256,
load each `Booster` once at startup, keep it warm, and refuse startup on any
mismatch — see "Implementation status" above for the now-implemented adapters.

- Crowd uses `crowd-inference/v1`: `sensor_id`, offset-aware `target_hour`,
  `feature_asof = target_hour - 1h`, 28 input features. Returns raw
  `pedestrian_flow_per_hour`, release identity, quality warnings.
- Traffic uses `traffic-inference/v1`: routes by exact `source_group`, never
  pools scales, returns raw `vehicle_count_per_hour`. SCATS is the primary
  hackathon signal; Transport Activity is always optional/degraded for v1.
- Both contracts use `quality.status = ok|degraded|unavailable`. Never convert
  missing history to zero, fabricate future diagnostics, or return zero as a
  stand-in for an unavailable prediction. Neither release has calibrated
  uncertainty, so software must not invent a confidence value.

For each request, metadata is authoritative for feature order/types/native
categorical categories/unseen-unit behavior. Preserve NaN for missing numeric
inputs; unknown crowd sensors/traffic units use the native missing-category
branch plus the recorded `__unseen` flag rather than a made-up code.

Historical lags must be exact and no newer than `feature_asof`; past-only
rolling windows exclude the target hour. Crowd needs a forecast-equivalent
target-hour weather row. SCATS uses fixed AEST; Transport Activity uses
Melbourne wall time despite its misleading `Z`; crowd uses
`Australia/Melbourne` and must preserve DST ambiguity flags.

## Serving and build compute

A 23 August 2026 Linux CPU smoke audit (XGBoost 3.4.1, one thread, synthetic
rows) gives capacity evidence, not an SLA: crowd loads in 399ms/315MiB RSS
(0.76ms/13.63ms median for 1/100 rows); SCATS 322ms/187MiB; countline
280ms/129MiB. CPU inference is sufficient, no serving GPU needed — but
`route-planner.py` samples multiple crowd predictions per candidate route
(see Implementation status), so batch/cache rather than issuing many one-row
requests; don't treat smoke timings as latency guarantees across hosts.

Crowd fitting used CUDA, XGBoost `hist`, eight CPU threads, full pandas
tables; plan on a CUDA workstation and 16 GiB+ host RAM as an estimate.
Traffic feature building is CPU/DuckDB (four threads, 12 GiB limit); fitting
used CUDA external memory with disk-backed batches. Elapsed time/peak
RAM/VRAM/energy were not retained for either — measure the next run rather
than publishing an invented number.

## Publication and route integration

`.gitignore` exposes exactly the crowd release files and complete traffic bundle
while ignoring every other model plus all raw, processed, recovery, training,
evaluation, preview, and provenance artifacts. `.gitattributes` sends exactly
the three promoted UBJSON paths to Git LFS.

Crowd route scoring now exists (`route-planner.py`'s corridor-sampling
"quieter" candidate, `software/routing-boundary`) but as pragmatic real-time
point sampling, not a proper effective-dated sensor-to-edge mapping — traffic
still has neither. SCATS/countline scales remain never comparable to each
other; keep raw predictions and warnings in the API even as the UI gets a
versioned score.

## Gotchas

- `requirements.txt` must pin `xgboost-cpu` to the **exact** training version,
  not just the same major version — a 3.0.5-vs-3.4.1 mismatch silently
  produced predictions ~1000x too low under a plausible-looking `degraded`
  status. Verify any bump with a real prediction, not just a clean load.
- Traffic v1 predates the corrected feature allow-list: unavailable target-hour
  diagnostics explain about 0.37% of SCATS gain and about 28% of countline gain.
- Seven crowd test sensors were unseen (MAE 59.43 vs 46.29 for seen sensors).
  Transport Activity has only five trained units, 107,259/116,551 test rows
  unseen — not a robust rollout claim. Missing branches are defined behavior,
  not proof of accuracy.
- Crowd reached its 2,500-round ceiling; both traffic winners reached 300.
  Retraining must publish a new immutable release and rerun the chronological
  same-key evaluation before promotion.
