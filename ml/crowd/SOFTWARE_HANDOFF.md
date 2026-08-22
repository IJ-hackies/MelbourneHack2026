# Crowd model software handoff

This is the software boundary for the promoted crowd release. It describes a
small Python inference adapter: the Next.js application may call that adapter
over an API, but the browser must never load or parse the UBJSON model.

The adapter described here is not implemented yet. This document defines the
contract the implementation must follow; it is not evidence that an API,
backend, route mapper, or live feature pipeline exists.

## Release and target

The release is `all-history-v1` at
`ml/crowd/models/all-history-v1/model.ubj`. It predicts the next-hour
`pedestrian_flow`: people passing one fixed City of Melbourne counter during an
hour. It does not predict simultaneous area occupancy, street-segment density,
or a route-level crowd score.

The promoted artifact is an XGBoost 3.4.1 `count:poisson` model trained with
CUDA. It has 2,500 boosted rounds, with best iteration 2,499, and 29 model
columns: 28 declared source features plus the derived `sensor_id__unseen`
flag. The model file is 68,989,147 bytes (65.8 MiB) and its expected SHA-256
is:

```text
34918786e699c2fab26c4a34d6eceecf844fd3819a4dce1a682c1e294ed9c1dd
```

`metadata.json` is the source of truth for the release path, artifact bytes,
hash, feature order, native categorical state, unseen-sensor policy, training
parameters, dataset hash, and XGBoost build information. There is no separate
crowd release checksum manifest; the adapter must validate the promoted hash
recorded in `metadata.json` before loading the model.

## Versioned JSON contract

The adapter should accept `crowd-inference/v1` requests. `features` contains
the 28 declared `metadata.json.encoder.feature_columns`; it is not a raw
publisher sensor row. The adapter derives `sensor_id__unseen` and constructs
the final 29-column model frame in metadata order.

```json
{
  "contract_version": "crowd-inference/v1",
  "sensor_id": 123,
  "target_hour": "2026-08-23T10:00:00+10:00",
  "feature_asof": "2026-08-23T09:00:00+10:00",
  "features": {
    "sensor_id": 123,
    "local_hour": 10,
    "prediction_horizon_hours": 1,
    "hour_sin": 0.5,
    "hour_cos": -0.8660254,
    "flow_lag_1h": 121.0,
    "flow_lag_24h": 118.0,
    "flow_lag_168h": 125.0,
    "flow_rolling_past_24h_mean": 120.4,
    "flow_rolling_past_168h_std": 85.2,
    "flow_rolling_past_168h_count": 164.0,
    "nasa_temperature_c": 18.2,
    "nasa_weather_observation_count": 1
  }
}
```

The example is abbreviated. A valid request must provide or allow the adapter
to build every declared feature. `target_hour` and `feature_asof` use
`Australia/Melbourne` semantics; `feature_asof` is the one-hour prediction
boundary before the target hour. An offset must be supplied when serializing
an instant. The adapter must preserve the model's `is_dst` value and should
reject or mark a request degraded/unavailable when a daylight-saving ambiguous
or nonexistent local time cannot be represented safely.

The response shape is:

```json
{
  "contract_version": "crowd-inference/v1",
  "prediction": {
    "pedestrian_flow_per_hour": 123.4
  },
  "model": {
    "release": "all-history-v1",
    "variant": "all_history",
    "sensor_id": 123
  },
  "quality": {
    "status": "ok",
    "feature_coverage": "complete",
    "warnings": []
  }
}
```

`quality.status` should be `ok`, `degraded`, or `unavailable`. A degraded
prediction may be shown with its warnings. An unavailable response must not
pretend that zero people is a valid prediction. Keep the raw
`pedestrian_flow_per_hour` value in the API response; any sensor-specific or
route presentation normalization is future work. This model has no calibrated
uncertainty, so the contract must not expose a made-up confidence score.

## Loading and feature assembly

At startup, the adapter should:

1. Materialize the model with Git LFS and verify its SHA-256 against
   `metadata.json.promoted_artifact.sha256` and its expected byte count. Refuse
   startup on a mismatch.
2. Load `metadata.json` and treat `encoder.feature_columns`,
   `encoder.model_feature_columns`, `encoder.categorical_columns`,
   `encoder.categories`, and `encoder.unseen_sensor_policy` as authoritative.
3. Reproduce the feature construction performed by
   `ml/scripts/train_crowd_models.py`: calendar/cyclical fields, exact sensor
   lags, past-only rolling fields, and the target-hour weather fields.
4. Create a pandas frame with the 29 `model_feature_columns` in exactly the
   stored order. Preserve native XGBoost categorical dtypes for `sensor_id`
   and `is_dst`; do not alphabetize, infer, or hand-copy the order.
5. Load the UBJSON with XGBoost and keep the model warm in the Python backend.
   Loading the file alone does not perform feature engineering.

The model was trained on 111 sensor categories. An unknown sensor must follow
the recorded policy: map it to the native categorical missing branch and set
`sensor_id__unseen=1`; include `unseen_sensor` in `quality.warnings`. The
seven unseen sensors in the held-out test were separately reported and perform
worse than seen sensors. Do not invent a sensor category or backfill historical
coordinates from the current metadata snapshot.

Missing numeric values may remain `NaN` so XGBoost can use its native missing
branch. Never convert a missing count, weather reading, or lag to zero. A
non-finite or negative prediction must be rejected; the training evaluator
clips predictions to the non-negative domain.

## Freshness and missing history

For target hour `T`, every historical feature must come from data available at
or before `feature_asof`:

- `flow_lag_1h` is the exact immediately preceding sensor hour;
- `flow_lag_24h` and `flow_lag_168h` are exact prior sensor-hour values;
- rolling 24-hour and 168-hour fields use past-only windows and exclude `T`.

The source pipeline must not shift the target, forward-fill a gap, or invent a
zero for a missing sensor hour. If the latest history is late or incomplete,
return `degraded` with warnings such as `stale_lag_history` or
`missing_lag_history`; if a valid feature row cannot be formed, return
`unavailable` rather than a fabricated prediction.

The promoted model uses target-hour regional NASA POWER weather features. A
production adapter therefore needs an observation or forecast-equivalent value
for the target hour and should warn/degrade when it is unavailable. NASA POWER
is regional/grid-scale weather, not a street-local measurement. The rejected
recent-enhanced candidate's citywide microclimate and Transport Activity
features are not inputs to this promoted all-history model and must not be sent
as substitutes.

## Retrieval and integrity checks

After cloning, materialize the LFS object before loading it:

```bash
git lfs install
git lfs pull --include="ml/crowd/models/all-history-v1/model.ubj"
sha256sum ml/crowd/models/all-history-v1/model.ubj
```

The final hash must equal the value above and the file must be 68,989,147
bytes. A basic load check is:

```python
from pathlib import Path
import xgboost as xgb

model = xgb.XGBRegressor()
model.load_model(Path("ml/crowd/models/all-history-v1/model.ubj"))
```

The promoted model is immutable and byte-identical to the winning generated
artifact. A replacement must use a new versioned directory, pass the same-key
chronological evaluation, and ship updated metadata, checksum information,
and handoff documentation together.

## What remains unimplemented

There is currently no Python adapter, API route, online prediction command,
freshness monitor, retraining policy, calibrated uncertainty, fallback
implementation, effective-dated sensor relocation history, sensor-to-route-edge
mapping, or route-level crowd calibration. A fixed-counter prediction must not
be used directly as a pedestrian-graph edge cost. Route integration needs a
separate spatial contract, coverage policy, and validation before software
turns this signal into a personalized route recommendation.
