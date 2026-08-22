# Crowd forecasting workspace

`datasets/` contains immutable publisher snapshots used for crowd forecasting:
pedestrian sensor locations plus historical, archived-gap, and current hourly
counts. The 12 January 2025 Internet Archive snapshot is retained in full for
provenance, but only its 1 November 2022 to 20 August 2024 rows should fill the
gap between the historical attachment and the current portal slice.

The multimodal City Transport Activity archives are stored once under
`ml/traffic/datasets/` and tagged for both `traffic` and `crowd` in the central
catalog. Future cleaned features, model artifacts, and evaluation outputs must
not overwrite files in `datasets/`.

## Build the canonical hourly target

Install the transformation dependencies and run the reproducible builder:

```powershell
python -m pip install -r ml/requirements.txt
python ml/scripts/build_crowd_dataset.py
```

The builder applies these inclusive, non-overlapping windows:

1. Historical attachment through 31 October 2022.
2. Internet Archive capture from 1 November 2022 through 20 August 2024.
3. Current portal Parquet from 21 August 2024 onward.

It writes three ignored local artifacts under `processed/`:

- `pedestrian_flow_hourly.parquet` — one observed row per sensor, local date,
  and local hour.
- `sensor_coverage.parquet` — source coverage, observed names, coordinate
  availability, and current sensor-metadata fields by sensor id.
- `quality_manifest.json` — input hashes, row/date counts, repair counts,
  missingness, validation results, and training caveats.

Pass explicit source paths for isolated or fixture builds, and `--overwrite`
to replace an existing generated result:

```powershell
python ml/scripts/build_crowd_dataset.py `
  --historical-zip path/to/historical.zip `
  --wayback-csv path/to/archive.csv `
  --current-parquet path/to/current.parquet `
  --sensor-locations path/to/sensors.parquet `
  --output-dir path/to/output
```

Run the contract tests with:

```powershell
python -m unittest ml.tests.test_build_crowd_dataset -v
```

## Build the two training tables

Fetch the regional all-history weather series, then build both tables from the
same canonical `pedestrian_flow` target:

```powershell
python ml/scripts/fetch_nasa_power_weather.py --end-date 2026-08-20
python ml/scripts/build_crowd_training_datasets.py `
  --weather ml/data/raw/nasa_power_melbourne_hourly.csv `
  --microclimate ml/data/raw/com_microclimate_readings/microclimate_readings.parquet `
  --transport ml/traffic/datasets `
  --recent-start 2023-01-01 `
  --preview-rows 250 `
  --overwrite
```

Generated artifacts remain local and ignored under `training/`:

- `crowd_training_all_history.parquet` — all 7,295,962 target rows from 2009,
  with calendar, Victorian public-holiday, exact target-lag, past-only rolling,
  and NASA POWER regional weather features.
- `crowd_training_recent_enhanced.parquet` — 2,642,497 rows from 2023, adding
  City microclimate and multimodal Transport Activity aggregates. These inputs
  are shifted one hour so same-hour measurements cannot leak into the label.
- `crowd_training_all_history_preview.csv` and
  `crowd_training_recent_enhanced_preview.csv` — deterministic 250-row samples
  intended for spreadsheet/text inspection.
- `training_manifest.json` — input identity, row counts, columns, feature lists,
  split counts, intended horizon, leakage exclusions, and comparison rules.

The comparison split is chronological and shared: train through 2024,
validation during 2025, and final test from 1 January through 11 May 2026. The
May cutoff is the last common Transport Activity coverage. Later 2026 target
rows remain available as `post_test`, but must not be included in the claimed
head-to-head score. For a feature-only ablation, filter the all-history table to
the same `local_date >= 2023-01-01` keys before training.

Missing lag, weather, microclimate, or transport readings remain null. The
builder never manufactures zeros. Directional counts are excluded because they
sum directly to the target. NASA weather at the target hour is an exogenous
feature: production inference must supply the corresponding observation or
forecast. Microclimate and traffic features are citywide hourly summaries, not
street-specific values; adding effective-dated spatial joins is a later
accuracy improvement.

Run both target and training-table contracts with:

```powershell
python -m unittest ml.tests.test_build_crowd_dataset ml.tests.test_build_crowd_training_datasets ml.tests.test_train_crowd_models -v
```

## Train and compare the crowd models

The trainer reads the manifest feature lists, validates the shared test keys,
and fits the all-history and recent-enhanced candidates plus the required
matched recent-window ablation. It uses an XGBoost Poisson count objective,
native categorical sensor IDs, validation early stopping, and CUDA with an
explicit CPU fallback.

The current full run is reproduced with:

```powershell
python ml/scripts/train_crowd_models.py `
  --device cuda `
  --overwrite `
  --n-estimators 2500 `
  --max-depth 8 `
  --learning-rate 0.05 `
  --subsample 0.8 `
  --colsample-bytree 0.8 `
  --min-child-weight 20 `
  --reg-lambda 10 `
  --early-stopping-rounds 100 `
  --seed 42
```

Ignored artifacts are written under `training/evaluation/`: UBJSON models and
metadata, shared-test predictions, overall/baseline/per-sensor/missingness
metrics, and an evaluation manifest. The 22 August 2026 run scored all three
variants on the same 296,087 test rows:

| Candidate | MAE | RMSE | Poisson deviance |
| --- | ---: | ---: | ---: |
| All history | 47.2055 | 110.0230 | 14.8839 |
| Matched recent ablation | 49.6585 | 118.0417 | 16.4785 |
| Recent enhanced | 56.1154 | 127.0281 | 20.2527 |

The all-history model is the current winner. The enhanced candidate is 13.0%
worse in MAE than the matched recent ablation, so its citywide microclimate and
Transport Activity features are not accepted as an improvement. Seven test
sensors are unseen during training and remain separately reported. Use
`--small-data --cpu` for the synthetic/CI-sized path and never score `post_test`
rows as part of the head-to-head result.

### Promoted model artifact

The winning all-history model is promoted separately from the ignored run
directory at `models/all-history-v1/model.ubj`. It is stored with Git LFS so
the 65.8 MiB binary can be pushed without adding it directly to normal Git
history. Its portable `metadata.json` preserves feature order, categorical
encoder state, training parameters, provenance, and the expected SHA-256.
`models/all-history-v1/README.md` is the authoritative model card.

After cloning, run `git lfs install` and `git lfs pull` before loading the
model. The promoted artifact is byte-identical to the generated winner; do not
edit it in place. A replacement must receive a new versioned directory after
winning the identical-key evaluation, and its checksum and documentation must
be updated together.

## Data contract and deliberate missingness

The target is named `pedestrian_flow`: it measures people passing a fixed
counter during an hour, not crowd density over a street or area. Its natural
key is `(sensor_id, local_date, local_hour)`. Publisher record ids are retained
for lineage but are not keys because the current export reuses them.

Times remain naive `Australia/Melbourne` wall-clock labels. The table records
whether a time is daylight saving, ambiguous, or nonexistent, but does not
invent a UTC instant where the source cannot distinguish one. Missing sensor
hours remain absent; they are never generated as zero counts. Historical rows
have null directional counts and null observation coordinates because the old
attachment did not provide them. Current metadata coordinates are not copied
backward across possible sensor moves.

The historical publisher file has one verified format defect: all 12,240 rows
for 17 sensors during September 2010 are labelled hour 0. Their file order is
exactly 24 stable sensor blocks per day and follows the expected diurnal count
shape. The builder reconstructs hours 0–23 only when the complete
`30 days × 24 hours × 17 sensors` structure and stable block order match, marks
every repaired row with `hour_was_reconstructed`, and fails rather than guessing
if those invariants change.

`direction_1_count` and `direction_2_count` sum directly to
`pedestrian_flow` in the modern sources. They are useful for validation or a
separate directional target, but using them to predict the same row's total is
target leakage. Model evaluation should use chronological/as-of splits rather
than random rows. Events, COVID regimes, effective-dated sensor locations, and
sensor-to-route-edge mappings remain separate feature work.
