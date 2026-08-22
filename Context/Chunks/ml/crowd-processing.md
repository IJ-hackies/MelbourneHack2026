---
id: ml/crowd-processing
title: Canonical crowd target processing
sources:
  - ml/scripts/build_crowd_dataset.py
  - ml/tests/test_build_crowd_dataset.py
  - ml/crowd/README.md
  - ml/requirements.txt
links: [heatroute, ml/data-acquisition, ml/crowd-training, ml/crowd-modeling, ml/planned-forecasting]
verified: initial
---

## What this is

The ML lane has a reproducible transformation from three immutable City of
Melbourne pedestrian-count snapshots into one canonical hourly target. It
streams the historical ZIP, the archived gap CSV, and the current Parquet
export; applies non-overlapping source windows; and writes ignored processed
Parquet tables plus a JSON quality manifest. (`ml/scripts/build_crowd_dataset.py`)

The output represents hourly pedestrian flow past fixed counters, not crowd
density over a street or area. It is a cleaned target layer, not a feature
store, training package, prediction, or route-edge mapping.
(`ml/crowd/README.md`)

## Key files

- `ml/scripts/build_crowd_dataset.py` - CLI, schema harmonisation, strict source
  precedence, September 2010 repair, DST/missingness flags, validation, sensor
  coverage aggregation, and manifest generation.
- `ml/tests/test_build_crowd_dataset.py` - source-shaped ZIP/CSV/Parquet contract
  fixtures covering precedence, keys, missingness, directions, coordinates,
  DST, and the historical hour-label repair.
- `ml/crowd/README.md` - operator commands, data contract, and training caveats.
- `ml/requirements.txt` - shared pandas, PyArrow, DuckDB, and holidays
  transformation dependencies.
- Generated local outputs: `ml/crowd/processed/pedestrian_flow_hourly.parquet`,
  `sensor_coverage.parquet`, and `quality_manifest.json`; they are Git-ignored.

## Invariants

- Source precedence is historical through 31 October 2022, Wayback from
  1 November 2022 through 20 August 2024, and current from 21 August 2024.
- The natural key is `(sensor_id, local_date, local_hour)`. Publisher ids are
  lineage only because the current export reuses ids.
- Missing sensor-hours remain absent and are never imputed as zero.
- Times remain naive `Australia/Melbourne` wall-clock values. DST ambiguity and
  nonexistent local times are flagged; ambiguous rows do not receive invented
  UTC instants.
- Historical direction counts and observation coordinates remain null. Current
  metadata coordinates are not backfilled across possible sensor relocations.
- Modern direction counts are validated against the total but must not be used
  to predict that same total because they are direct target leakage.
- September 2010 hour labels are reconstructed only when the verified complete
  30-day, 24-block, 17-sensor ordering matches. All 12,240 repaired rows retain
  `hour_was_reconstructed`; changed structure fails rather than guesses.

## How to extend

Keep the canonical target stable. Existing time/weather/lag/microclimate/
transport features belong to `ml/crowd-training`, and fitted models belong to
`ml/crowd-modeling`; event, COVID, effective-dated location, and route-edge
features remain separate future work.
Use chronological or as-of splits, optionally with held-out sensors; do not
randomly split adjacent-hour rows. (`ml/crowd/README.md`)

Run `python -m unittest ml.tests.test_build_crowd_dataset -v` after changing
the contract, then rebuild with `python ml/scripts/build_crowd_dataset.py
--overwrite` and inspect the generated quality manifest.

## Gotchas

- The current sensor-location snapshot is not an effective-dated geometry
  history; relocation and upgrade notes require explicit modelling.
- `direction_1_count + direction_2_count` equals `pedestrian_flow` in modern
  rows and is target leakage for a same-hour total model.
- Licences for the City hourly exports remain unspecified in publisher API
  metadata. Generated results stay internal until redistribution is confirmed.
- Generated Parquet and manifest files are local evidence, not committed source.
  Rebuild them when raw snapshots or processing code changes.
