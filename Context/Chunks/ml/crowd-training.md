---
id: ml/crowd-training
title: Crowd training feature tables and comparison contract
sources:
  - ml/scripts/build_crowd_training_datasets.py
  - ml/scripts/fetch_nasa_power_weather.py
  - ml/tests/test_build_crowd_training_datasets.py
  - ml/crowd/README.md
  - ml/requirements.txt
links: [heatroute, ml/data-acquisition, ml/crowd-processing, ml/crowd-modeling, ml/planned-forecasting]
verified: initial
---

## What this is

The ML lane has two reproducible, leakage-aware crowd training feature tables
with the same label: `pedestrian_flow`, measured as people passing a fixed City
counter during one hour. The tables are training inputs, not trained models,
crowd-density estimates, predictions, or route-edge features.
(`ml/scripts/build_crowd_training_datasets.py`, `ml/crowd/README.md`)

The all-history table retains all 7,295,962 canonical target rows from May 2009
and adds calendar, Victorian public holidays, exact target lags, past-only
rolling statistics, and regional NASA POWER weather. The recent-enhanced table
retains 2,642,497 rows from January 2023 and adds one-hour-lagged citywide
microclimate and Transport Activity aggregates. Both Parquet outputs, their
250-row CSV previews, and a manifest are local and Git-ignored under
`ml/crowd/training/`. (`ml/crowd/README.md`)

The tables now feed the implemented trainer and shared-test evaluation described
by `ml/crowd-modeling`; keep transformation and model contracts separate.

## Key files

- `ml/scripts/fetch_nasa_power_weather.py` - yearly NASA POWER requests from
  May 2009, UTC-normalized CSV, provider-fill normalization, validation,
  checksums, retries, atomic output, and a provenance sidecar.
- `ml/scripts/build_crowd_training_datasets.py` - external-source aggregation,
  temporal joins, features, shared splits, Parquet/preview output, and manifest.
- `ml/tests/test_build_crowd_training_datasets.py` - synthetic contract tests
  for target identity, exact lags, null preservation, leakage exclusions,
  chronological splits, previews, and manifest output.
- `ml/crowd/training/training_manifest.json` - ignored local evidence containing
  output schemas, feature lists, counts, split contract, and source paths.

## Invariants

- The label remains `pedestrian_flow`; do not describe it as area density.
- Direction 1/2 counts and same-hour target-derived values are excluded. Exact
  1/24/168-hour lags and rolling windows use only earlier rows for that sensor.
- The comparison horizon is one hour. Microclimate and Transport Activity
  aggregates are shifted one hour; absent source intervals remain null.
- NASA timestamps are UTC and converted to Melbourne local hours. Fill values
  such as `-999` become null. Repeated DST hours are collapsed with an
  observation count rather than multiplying target rows.
- Microclimate `received_at` is UTC. Transport Activity's apparent `Z` suffix
  is misleading: its interval jumps follow Melbourne wall time, so the builder
  strips the suffix and preserves local/DST semantics.
- The shared split is train through 2024, validation in 2025, test from
  1 January through 11 May 2026, and `post_test` afterward. Compare identical
  test `observation_key` values; use a recent-window baseline ablation to
  separate feature uplift from extra historical coverage.

## How to extend

Use `ml/crowd-modeling` for fitting, evaluation, and model artifacts. Before
routing integration, map sensors/features to effective-dated route edges and
define the software contract for timestamp, segment id, value/unit,
uncertainty, freshness, and missing-data fallback.

## Gotchas

- Regional NASA POWER weather and citywide microclimate/traffic aggregates are
  not street-level features. Spatial, effective-dated joins remain future work.
- Transport Activity has no explicit zero readings and ends 11 May 2026; sparse
  modes/hours are unknown, not zero. NASA solar is missing after 31 May 2026.
- Target DST ambiguity cannot be resolved to a unique UTC instant from the
  publisher rows. Retain the flags and stratify or exclude those rows in model
  evaluation.
- Generated feature tables are ignored local evidence. Rebuild them when raw
  snapshots, acquisition code, or feature logic changes.
