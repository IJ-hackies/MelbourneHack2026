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
links: [heatroute, ml/crowd-training, ml/crowd-modeling, ml/traffic-training, ml/traffic-modeling, ml/planned-forecasting, software/routing-boundary]
verified: edcfab5
---

## Implementation status (update)

Both contracts are now implemented as root-level Python Vercel Functions —
`api/crowd-inference.py` and `api/traffic-inference.py` — sharing
`api/_shared/model_loader.py` (checksum verification + cached booster
loading) and `api/_shared/feature_lookup.py` (live request-time feature
assembly, distinct from the offline `ml/` training pipeline). Crowd is wired
into `conditionProvider` (`software/routing-boundary`) via `ml-client.ts`;
traffic is implemented but not called from any provider yet.

Crowd's feature lookup genuinely queries live data: the City's pedestrian-counts
dataset exposes an Opendatasoft Explore v2.1 `/records` endpoint (filterable by
`location_id`), not the bulk `/exports` the offline pipeline uses, plus
Open-Meteo for target-hour temperature. The crowd endpoint also accepts
`{lat, lon}` directly (a software-adapter extension beyond the strict v1
contract) and resolves the nearest sensor itself via
`feature_lookup.resolve_nearest_crowd_sensor` — a live query against the
sensor-locations dataset (same pattern), not a committed snapshot, since
relocations aren't an effective-dated history; a sensor farther than 0.5km
adds a quality warning rather than being silently presented as the
destination. Traffic's lookup always returns `quality.status: "unavailable"`
— SCATS/Transport Activity are batch-archive-only (`ml/data/catalog.json`)
with no live query endpoint; the checksum/load path is still exercised, but a
real signal needs a scheduled ingestion job.

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

These are boosted-tree ensembles, not neural networks; each model's max
observed tree depth is 8. Artifact bytes, rounds, features, and hashes live
in metadata.

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

For each request, metadata is authoritative for feature order, feature types,
native categorical categories, and unseen-unit behavior. Preserve NaN for
missing numeric inputs. Unknown crowd sensors/traffic units use the native
missing-category branch plus the recorded `__unseen` flag and warning; other
unknown categories fail/degrade rather than receiving a made-up code.

Historical lags must be exact and no newer than `feature_asof`; past-only
rolling windows exclude the target hour. Crowd additionally needs a
forecast-equivalent target-hour weather row. SCATS uses fixed AEST semantics;
Transport Activity uses Melbourne wall time despite its misleading `Z`; crowd
uses `Australia/Melbourne` and must preserve DST ambiguity flags.

## Serving compute

A 23 August 2026 Linux CPU smoke audit (XGBoost 3.4.1, one thread, synthetic
rows, excludes feature lookup/HTTP) gives capacity evidence, not an SLA:
crowd loads in 399ms/315MiB RSS (0.76ms/13.63ms median for 1/100 rows);
SCATS 322ms/187MiB (0.26ms/1.18ms); countline 280ms/129MiB (0.21ms/0.88ms).

CPU inference is sufficient; a serving GPU is unnecessary. Allow roughly 1 GiB
for a crowd-only worker and 2 GiB for one holding all three boosters, then
load-test the real feature/API path — don't treat smoke timings as latency
guarantees across hosts/versions, and batch units per target hour rather than
issuing thousands of one-row requests.

## Training and build compute

- Crowd fitting used CUDA, XGBoost `hist`, eight CPU threads, and full pandas
  tables (330 MiB + 153 MiB compressed Parquet inputs). No elapsed time,
  peak RAM/VRAM, energy, or GPU model was retained. Plan on a CUDA workstation
  and at least 16 GiB host RAM, but treat that as an estimate pending telemetry.
- Traffic feature building is CPU/DuckDB work, proven with four threads and a
  12 GiB memory limit; fitting used CUDA external memory, 65,536-row disk-backed
  batches, one XGBoost CPU thread, no row cap, and no whole-table pandas load.
  Elapsed time, peak VRAM, energy, and GPU identity were not recorded — GPU
  acceleration does not reduce the 12 GiB DuckDB envelope; measure the next
  run rather than publishing an invented number.

## Publication and route integration

`.gitignore` exposes exactly the crowd release files and complete traffic bundle
while ignoring every other model plus all raw, processed, recovery, training,
evaluation, preview, and provenance artifacts. `.gitattributes` sends exactly
the three promoted UBJSON paths to Git LFS.

Before route scoring (not just inference), software still needs effective-dated
sensor/unit-to-edge mapping, spatial coverage/distance rules, source-specific
normalization, and route-level validation. Raw counts aren't comparable across
counters, and SCATS/countline scales are never comparable to each other. Keep
raw predictions and warnings in the API even if the UI gets a versioned score.

## Gotchas

- Traffic v1 predates the corrected feature allow-list: unavailable target-hour
  diagnostics explain about 0.37% of SCATS gain and about 28% of countline gain.
- Seven crowd test sensors were unseen (MAE 59.43 vs 46.29 for seen sensors).
  Transport Activity has only five trained units, 107,259/116,551 test rows
  unseen — not a robust rollout claim. Missing branches are defined behavior,
  not proof of accuracy.
- Crowd reached its 2,500-round ceiling; both traffic winners reached 300.
  Retraining must publish a new immutable release and rerun the chronological
  same-key evaluation before promotion.
