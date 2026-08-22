---
id: ml/traffic-modeling
title: CUDA traffic model training, evaluation, and release
sources:
  - .gitignore
  - .gitattributes
  - ml/scripts/train_traffic_models.py
  - ml/scripts/promote_traffic_models.py
  - ml/tests/test_train_traffic_models.py
  - ml/tests/test_promote_traffic_models.py
  - ml/traffic/README.md
  - ml/traffic/SOFTWARE_HANDOFF.md
  - ml/traffic/models/source-stratified-v1/release_manifest.json
  - ml/traffic/models/source-stratified-v1/checksums.json
  - ml/traffic/models/source-stratified-v1/README.md
  - ml/traffic/models/source-stratified-v1/scats-intersection/model.ubj
  - ml/traffic/models/source-stratified-v1/scats-intersection/metadata.json
  - ml/traffic/models/source-stratified-v1/transport-activity-countline/model.ubj
  - ml/traffic/models/source-stratified-v1/transport-activity-countline/metadata.json
  - ml/requirements.txt
links: [heatroute, ml/traffic-processing, ml/traffic-training, ml/model-handoff, ml/planned-forecasting]
verified: initial
---

## What this is

The traffic trainer fits XGBoost Poisson count models separately for SCATS
intersection totals and Transport Activity countline volumes. It compares base
and lag-enhanced candidates on chronological validation and reports the held-out
2026 test split. The release is therefore a source-stratified two-model bundle,
not one pooled traffic model. (`ml/scripts/train_traffic_models.py`)

The 22 August 2026 uncapped run trained all four candidates with CUDA and
external-memory Parquet batches. Validation selected lag-enhanced for both
groups. Held-out test metrics are 81.4622 MAE, 150.7749 RMSE, and 13.7267
Poisson deviance over 3,988,344 SCATS rows; Transport Activity scores 48.9760,
97.0135, and 19.5501 over 116,551 rows. (`ml/traffic/README.md`)

That promoted `source-stratified-v1` bundle predates the corrected leakage-safe
predictor contract. An audit attributed about 0.37% of feature gain to
target-hour diagnostics for SCATS and about 28% for Transport Activity. It is a
hackathon release: use SCATS as the primary demo signal and expose Transport
Activity only as optional/degraded. (`ml/traffic/SOFTWARE_HANDOFF.md`)

## Key files

- `ml/scripts/train_traffic_models.py` - manifest/table validation, train-only
  categorical encoding, CPU/CUDA execution, external-memory training, metrics,
  serialization, checksums, and atomic evaluation publication.
- `ml/scripts/promote_traffic_models.py` - validation-only winner selection,
  integrity/load checks, and atomic versioned release publication.
- `ml/tests/test_train_traffic_models.py` - synthetic full/sampled contracts,
  split discipline, CUDA fallback behavior, alignment, metrics, and artifacts.
- `ml/tests/test_promote_traffic_models.py` - validation-vs-test selection,
  integrity rejection, overwrite protection, and release-layout tests.
- `ml/traffic/training/evaluation/` - ignored full predictions, reports, four
  candidate model/metadata pairs, and checksums.
- `ml/traffic/models/source-stratified-v1/` - promoted portable two-model release;
  its release manifest, checksums, paired metadata, and model card travel together.
- `ml/traffic/SOFTWARE_HANDOFF.md` - versioned fixed-site request/response,
  model routing, encoding, freshness, LFS, checksum, and demo-normalization rules.

## Model and training detail

- Both winners are XGBoost 3.4.1 gradient-boosted `count:poisson` trees with
  `poisson-nloglik`, `hist`, native categoricals, CUDA, seed 42, depth 8,
  learning rate 0.05, full row/column sampling, minimum child weight 1, L2 1,
  L1 0, max delta step 0.7, max bin 256, one CPU thread, 300 estimators, and
  30-round early stopping. Both reached best iteration 299.
- SCATS trained on 6,770,394 rows/779 units, validated on 6,768,092 rows, and
  tested on 3,988,344. Its 33,284,016-byte model has 300 trees, 69,350 decision
  nodes, 69,650 leaves, 139,000 total nodes, 19,550 categorical splits, and
  maximum observed depth 8.
- Transport Activity trained on 42,622 rows/five countlines, validated on 58,404,
  and tested on 116,551. Its 4,531,207-byte model has 300 trees, 61,131 decision
  nodes, 61,431 leaves, 122,562 total nodes, 4,907 categorical splits, and
  maximum observed depth 8.
- Each promoted metadata file declares 58 source features and 59 model columns
  after `observation_unit_id__unseen`: identity/quality/time/location fields,
  cyclic calendar fields, and exact/past-only lag statistics. This v1 schema is
  22 fields wider than the corrected 36-feature lag allow-list because it still
  includes unavailable same-hour diagnostics. Do not fabricate those inputs.
- On test, SCATS lag-enhanced MAE 81.4622 beat its base model's 202.1190 and
  168-hour lag baseline's 136.0849. Countline MAE 48.9760 beat base 108.9593,
  but only 9,292 test rows were from seen units; 107,259 were unseen and scored
  MAE 51.0715. The target has 53 countlines but training saw only five (versus
  779 of 786 SCATS units). This plus leakage makes countline degraded.

## Software use and compute

`traffic-inference/v1` routes exact `source_group` values to two warm Python
boosters, verifies release checksums, assembles metadata-ordered native-category
features, and returns raw `vehicle_count_per_hour` plus quality warnings. The
browser never loads UBJSON. Preserve missing lags as NaN; do not pool source
scales or turn either raw count directly into route congestion/travel time.

The feature build is CPU/DuckDB work proven at four threads and a 12 GiB memory
limit. Full fitting used CUDA external memory, 65,536-row disk-backed batches,
no row cap, 2,174 observed batches/125,535,676 row visits, and no whole-table
pandas load. Elapsed time, peak VRAM, energy, original GPU, and disk-page peak
were not retained. `ml/model-handoff` records measured CPU serving smoke figures
and a 2 GiB all-model worker planning estimate; neither is an application SLA.

## Invariants

- Candidate selection uses only validation Poisson deviance, then validation
  MAE, validation RMSE, and deterministic base-before-lag tie-breaks. Test data
  is untouched until final reporting.
- Both candidates score the same 4,104,895 test keys and labels. Post-test rows
  are excluded from claimed metrics.
- Models are trained and served per `label_source|measurement_scope`; never pool
  SCATS and countline scales into one estimator.
- `--device cuda` is CUDA-only and cannot silently fall back. The full run records
  CUDA for all four fitted candidates and uses no row cap.
- UBJSON and metadata move together with feature order, encoders, parameters,
  best iteration, device, input hashes, and release checksums.
- Exactly the three current promoted UBJSON paths are Git-LFS tracked; explicit
  release-file allow-lists keep raw, processed, recovery, training, other model,
  and non-promoted evaluation artifacts ignored.
- Promotion validates source checksums and loads selected UBJSON before an
  atomic directory replacement. A versioned release is immutable afterward.

## How to extend

Repeat the same chronological validation/test contract for tuning or new
features. Publish a replacement under a new versioned models directory only
after integrity checks and a documented same-key comparison. A corrected
leakage-safe retrain is the next model priority. Implement the documented Python
adapter before app integration; route-edge mapping remains separate.

## Gotchas

- Lag-enhanced wins strongly, but this is an offline fixed-site forecast—not a
  route-edge congestion score or travel-time model.
- The published v1 scores include unavailable same-hour diagnostic predictors.
  Do not describe them as leakage-safe or production performance.
- Loading UBJSON does not engineer features. CUDA fitting does not accelerate
  DuckDB feature windows; WSL still needs bounded host memory and disk cache.
