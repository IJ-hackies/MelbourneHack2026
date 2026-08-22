# Crowd flow model: all-history v1

## What this is

This is the first promoted HeatRoute crowd model and the current offline
evaluation winner. It is an XGBoost 3.4.1 Poisson model that predicts
`pedestrian_flow`: the number of people expected to pass a fixed City of
Melbourne pedestrian counter during the next hour.

It does **not** predict the number of people simultaneously occupying an area,
street-segment crowd density, or a route-level crowd score. Those mappings and
the production inference boundary are not implemented yet.

## Artifacts

| File | Purpose |
| --- | --- |
| `model.ubj` | XGBoost UBJSON model, stored through Git LFS |
| `metadata.json` | Feature order, categorical encoder state, parameters, run provenance, and release checksum |
| `README.md` | Model card and operating constraints |

- Release: `all-history-v1`
- Model size: 68,989,147 bytes (65.8 MiB)
- SHA-256: `34918786e699c2fab26c4a34d6eceecf844fd3819a4dce1a682c1e294ed9c1dd`
- Objective: `count:poisson`
- Training runtime: CUDA
- XGBoost: 3.4.1
- Seed: 42
- Boosted rounds: 2,500; best iteration: 2,499

The promoted file is byte-identical to the winning generated artifact at
`ml/crowd/training/evaluation/models/all_history.ubj`.

## Training and evaluation

The model used 5,961,171 training rows from 1 May 2009 through 31 December
2024. Validation used 815,447 rows from calendar year 2025. The untouched test
set used 296,087 rows from 1 January through 11 May 2026.

| Test metric | Result |
| --- | ---: |
| MAE | 47.2055 pedestrians/hour |
| RMSE | 110.0230 pedestrians/hour |
| Poisson deviance | 14.8839 |

All-history beat the matched recent-window ablation and the recent-enhanced
candidate on the same test keys. The enhanced features are therefore not part
of this release.

Manual held-out sanity checks also showed the expected Melbourne pattern. At
weekday 13:00, Town Hall, Flinders Lane/Swanston, Melbourne Central, QV, and
RMIT ranked among the busiest counters. The same Swanston corridor fell from a
sensor-median prediction of about 2,582 pedestrians/hour at 13:00 to about 26
at 03:00. Retail/civic counters rose on weekend afternoons while RMIT counters
fell, matching the observed direction.

## Input contract

The model has 29 model columns: 28 declared source features plus the derived
`sensor_id__unseen` flag. They include sensor identity, cyclical calendar
features, weekend/holiday/DST flags, lagged and trailing pedestrian flows, and
regional NASA POWER weather. Use `metadata.json` as the source of truth for:

- exact `encoder.feature_columns` and `encoder.model_feature_columns` order;
- native categorical tokens for `sensor_id` and `is_dst`;
- the unseen-sensor transformation;
- model parameters and source hashes.

Do not pass raw sensor rows directly to the model. The same feature-building
and train-only categorical transformation used by
`ml/scripts/train_crowd_models.py` must be reproduced by the future inference
adapter. Missing optional numeric features may remain `NaN`; feature order and
categorical treatment may not change.

## Getting and checking the model

Git LFS is required to materialize the UBJSON after cloning:

```powershell
git lfs install
git lfs pull --include="ml/crowd/models/all-history-v1/model.ubj"
```

Verify the promoted bytes with:

```powershell
(Get-FileHash -Algorithm SHA256 `
  ml/crowd/models/all-history-v1/model.ubj).Hash.ToLower()
```

The expected value is the SHA-256 listed above. A basic load check is:

```python
from pathlib import Path
import xgboost as xgb

model = xgb.XGBRegressor()
model.load_model(Path("ml/crowd/models/all-history-v1/model.ubj"))
```

Loading is not the complete inference path; inputs still require the exact
transformation described by `metadata.json`.

## Limitations

- Seven test sensors were absent from training. They use the documented
  missing-category branch plus `sensor_id__unseen=1` and perform worse than
  seen sensors.
- The model depends strongly on 1-hour, 24-hour, and 168-hour flow lags. A
  serving system needs a freshness and missing-history policy.
- Target-hour NASA POWER observations require a forecast-equivalent weather
  source in production.
- Current sensor metadata is not an effective-dated relocation history.
- The model has no calibrated uncertainty, retraining policy, inference API,
  route-edge mapping, or fallback contract yet.
- All history reached the configured 2,500-round ceiling, so this is a
  promoted baseline rather than a claim of final optimality.
