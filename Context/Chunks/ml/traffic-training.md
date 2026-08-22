---
id: ml/traffic-training
title: Traffic training feature tables and chronological split
sources:
  - ml/scripts/build_traffic_training_datasets.py
  - ml/tests/test_build_traffic_training_datasets.py
  - ml/traffic/README.md
  - ml/requirements.txt
links: [heatroute, ml/traffic-processing, ml/traffic-modeling, ml/planned-forecasting]
verified: initial
---

## What this is

The traffic feature builder turns the complete canonical hourly target into two
leakage-safe, one-hour-ahead training tables. Both preserve all 17,744,407
canonical rows and the exact same observation keys and labels. Generated
Parquet, previews, and the readiness manifest stay ignored under
`ml/traffic/training/`. (`ml/scripts/build_traffic_training_datasets.py`)

The current trainer-safe manifest exposes 27 base predictors: identity/scope,
calendar, holiday, and coordinate fields. Lag-enhanced exposes 36 predictors by
adding exact 1/24/168-hour count lags and strictly past-only 24/168-hour rolling
statistics. Same-hour quality, detector/class, alarm, DST, and source-lineage
fields remain in the Parquet tables only as diagnostics. (`ml/traffic/README.md`)

## Key files

- `ml/scripts/build_traffic_training_datasets.py` - manifest validation,
  DuckDB feature SQL, chronological splits, bounded-memory Parquet output,
  previews, checksums, and atomic readiness-manifest publication.
- `ml/tests/test_build_traffic_training_datasets.py` - synthetic schema,
  leakage, exact-gap lag, rolling-window, split, determinism, and overwrite tests.
- `ml/traffic/training/training_manifest.json` - ignored local evidence with
  schemas, predictor lists, row/split counts, hashes, quality, and resource use.

## Invariants

- The target is `vehicle_count` at `hour_start_utc`; `feature_asof` is exactly
  one hour earlier and the prediction horizon is one hour.
- Both candidates retain 17,744,407 rows and identical keys/labels. No missing
  source hour is converted to zero.
- `training_contract.feature_columns`, not every non-label Parquet column, is
  the trainer/serving predictor allow-list.
- The chronological local-date split is train=2024 (6,813,016 rows),
  validation=2025 (6,826,496), and test=2026 through 31 July (4,104,895).
- Exact lags are gap-aware: a 24-hour lag is null unless that exact natural-unit
  observation exists. Rolling windows include only prior rows for the same
  observation unit.
- The builder accepts only a canonical manifest marked complete and validates
  declared schemas, counts, hashes, uniqueness, and source group semantics.
- DuckDB owns the external sort/window work. The proven full build uses four
  threads and a 12 GiB limit; VRAM is not used by this stage.

## How to extend

Keep new predictors reproducible at `feature_asof`, update the manifest feature
lists and leakage exclusions, and add exact synthetic tests. Use
`ml/traffic-modeling` for fitting and evaluation; do not mix model selection
into the feature builder.

## Gotchas

- SCATS intersections and Transport Activity countlines share a target name but
  not a calibration scale. Preserve `label_source` and `measurement_scope`.
- Increasing DuckDB threads can increase peak memory and trigger WSL OOM. Four
  threads/12 GiB is the verified full-run setting; six workers suit the earlier
  independent recovery-build stage, not this global window query.
- Lag missingness is signal. XGBoost handles null predictors; do not fill them
  with zero.
- The currently promoted `source-stratified-v1` model predates this corrected
  allow-list. A leakage-safe retrain must use a new release version.
