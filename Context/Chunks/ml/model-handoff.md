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
links: [heatroute, ml/crowd-training, ml/crowd-modeling, ml/traffic-training, ml/traffic-modeling, ml/planned-forecasting]
verified: initial
---

## What is ready

There are two logical promoted prediction releases and three UBJSON binaries:
`all-history-v1` is one crowd estimator; vehicle prediction is the inseparable
`source-stratified-v1` bundle containing separate SCATS-intersection and
Transport-Activity-countline estimators. All predict a fixed observation unit
one hour ahead. None predicts route-edge density, congestion, speed, or travel
time. (`ml/crowd/SOFTWARE_HANDOFF.md`, `ml/traffic/SOFTWARE_HANDOFF.md`)

| Release/model | Output | Size | Features | Trees / learned structure |
| --- | --- | ---: | ---: | --- |
| crowd `all-history-v1` | pedestrians/counter/hour | 65.8 MiB | 29 | 2,500 trees; 596,440 decisions; 598,940 leaves |
| traffic SCATS | vehicles/intersection/hour | 31.7 MiB | 59 | 300 trees; 69,350 decisions; 69,650 leaves |
| traffic countline | vehicles/countline/hour | 4.3 MiB | 59 | 300 trees; 61,131 decisions; 61,431 leaves |

These are boosted-tree ensembles, not neural networks, so a neural-style scalar
parameter count is not meaningful. The table reports exact serialized model
capacity from an XGBoost 3.4.1 UBJSON inspection: each model's maximum observed
tree depth is 8. Artifact bytes, rounds, features, and hashes live in metadata.

## Software architecture and contracts

The browser/Next.js client must call a Python backend adapter; it must not fetch
or parse model binaries. Materialize Git LFS, verify byte count and SHA-256, load
each `xgboost.Booster` once at process startup, and keep it warm. Refuse startup
on any model/metadata/checksum mismatch. The adapters and API routes are
documented but not implemented.

- Crowd uses `crowd-inference/v1`: `sensor_id`, offset-aware `target_hour`,
  `feature_asof = target_hour - 1h`, and the 28 declared input features. Return
  raw `pedestrian_flow_per_hour`, release identity, and quality warnings.
- Traffic uses `traffic-inference/v1`: route by the exact `source_group`, never
  pool source scales, and return raw `vehicle_count_per_hour`, selected model
  identity, and quality warnings. SCATS is the primary hackathon signal;
  Transport Activity is always optional/degraded for v1.
- Both contracts use `quality.status = ok|degraded|unavailable`. Never convert
  missing history to zero, fabricate future diagnostics, or return zero as a
  stand-in for an unavailable prediction. Neither release has calibrated
  uncertainty, so software must not invent a confidence value.

For each request, metadata is authoritative for feature order, model feature
types, native categorical categories, and unseen-unit behavior. Preserve NaN
for missing numeric inputs. Unknown crowd sensors and traffic units use the
native missing-category branch plus the recorded `__unseen` flag and warning;
other unknown categories fail or degrade rather than receiving a made-up code.

Historical lags must be exact and no newer than `feature_asof`; past-only
rolling windows exclude the target hour. Crowd additionally needs a
forecast-equivalent target-hour weather row. SCATS uses fixed AEST semantics;
Transport Activity uses Melbourne wall time despite its misleading `Z`; crowd
uses `Australia/Melbourne` and must preserve DST ambiguity flags.

## Serving compute

A reproducible 23 August 2026 Linux CPU smoke audit used XGBoost 3.4.1, one
thread, a new native-categorical DMatrix per call, and schema-shaped synthetic
rows. It excludes feature-store lookup, pandas feature engineering, JSON, HTTP,
and route mapping, so it is capacity evidence rather than an application SLA.

| Model | Load time | Incremental peak RSS | 1-row median / p95 | 100-row median |
| --- | ---: | ---: | ---: | ---: |
| crowd | 399 ms | 315 MiB | 0.76 / 1.39 ms | 13.63 ms |
| SCATS | 322 ms | 187 MiB | 0.26 / 0.34 ms | 1.18 ms |
| countline | 280 ms | 129 MiB | 0.21 / 0.26 ms | 0.88 ms |

CPU inference is sufficient; a serving GPU is unnecessary. As a planning
estimate, allow roughly 1 GiB for a crowd-only Python worker and 2 GiB for one
worker holding all three boosters, then load-test the actual feature/API path.
Batch units for a target hour instead of issuing thousands of one-row requests.
Do not use smoke timings as latency guarantees across hosts or XGBoost versions.

## Training and build compute

- Crowd fitting used CUDA, XGBoost `hist`, eight CPU threads, and full pandas
  tables (330 MiB + 153 MiB compressed Parquet inputs). No elapsed time,
  peak RAM/VRAM, energy, or GPU model was retained. Plan on a CUDA workstation
  and at least 16 GiB host RAM, but treat that as an estimate pending telemetry.
- Traffic feature building is CPU/DuckDB work, proven with four threads and a
  12 GiB memory limit. Fitting used CUDA external memory, 65,536-row disk-backed
  batches, one XGBoost CPU thread, no row cap, 2,174 observed batches and
  125,535,676 streamed row visits; it made no whole-table pandas load.
- Traffic elapsed time, peak VRAM, energy, and original GPU identity were not
  recorded. GPU acceleration does not reduce the 12 GiB DuckDB feature-build
  envelope; allow extra local disk for external-memory pages and measure it on
  the next run rather than publishing an invented number.

## Publication and route integration

`.gitignore` exposes exactly the crowd release files and complete traffic bundle
while ignoring every other model plus all raw, processed, recovery, training,
evaluation, preview, and provenance artifacts. `.gitattributes` sends exactly
the three promoted UBJSON paths to Git LFS. Dataset/model-building scripts,
tests, requirements, catalog, docs, and small pipeline configs remain publishable.

Before routing, software still needs effective-dated sensor/unit-to-edge mapping,
spatial coverage and distance rules, source/sensor-specific normalization,
freshness/fallback behavior, and route-level validation. Raw crowd counts are not
comparable across counters without calibration; SCATS and countline raw counts
are never comparable to one another. Keep raw predictions and warnings in the
API even if the UI also receives a versioned percentile/exposure score.

## Gotchas

- Traffic v1 predates the corrected feature allow-list: unavailable target-hour
  diagnostics explain about 0.37% of SCATS gain and about 28% of countline gain.
- Seven crowd test sensors were unseen; their MAE was 59.43 versus 46.29 for
  seen sensors. Transport Activity has only five trained units and 107,259 of
  116,551 test rows belong to unseen units; its result is not a robust rollout
  claim. XGBoost missing branches are defined behavior, not proof of accuracy.
- Crowd reached its 2,500-round ceiling; both traffic winners reached their
  300-round ceiling. Retraining/tuning must publish a new immutable release and
  rerun the chronological same-key evaluation before promotion.
