# Traffic model software handoff

This is the software boundary for the promoted traffic release. It is a small
Python inference-adapter contract: the Next.js app should call that adapter over
an API and should never load an UBJSON model in the browser.

## Read this first: release caveat

source-stratified-v1 is the first promoted CUDA traffic release. It was trained
before the leakage-safe feature correction and its metadata includes target-hour
diagnostic/quality fields such as source-row counts, detector/class counts,
ta_derived_zero, and other same-hour lineage flags. Those values are not known
at the one-hour-ahead feature_asof boundary. An audit estimated a small impact
for SCATS (about 0.37% of feature gain) and a material impact for Transport
Activity (about 28% of feature gain), so the Transport Activity score is less
trustworthy.

For the hackathon, use SCATS as the primary traffic demo signal. Treat Transport
Activity as optional and always surface a warning. Do not manufacture future
diagnostic values to improve either score. If a required v1 field is unavailable,
use the documented missing-value path and mark the response degraded; a future
leakage-safe retrain must receive a new versioned release.

## Routing and model paths

There are two separate models. Route by the exact source_group string and never
pool their raw counts or train one estimator across both scales.

| source_group | Fixed unit | Model | Metadata |
| --- | --- | --- | --- |
| scats\|intersection | SCATS intersection total | ml/traffic/models/source-stratified-v1/scats-intersection/model.ubj | ml/traffic/models/source-stratified-v1/scats-intersection/metadata.json |
| transport_activity\|countline | reviewed road countline volume | ml/traffic/models/source-stratified-v1/transport-activity-countline/model.ubj | ml/traffic/models/source-stratified-v1/transport-activity-countline/metadata.json |

The release root is ml/traffic/models/source-stratified-v1/.
release_manifest.json records the winner, release version, split provenance, CUDA
evidence, and source-specific metrics. checksums.json authenticates the files
in the release directory.

## Versioned JSON contract

The adapter should accept traffic-inference/v1 requests. features is an object
of model inputs, not a raw SCATS/Transport Activity row. The example is
illustrative and abbreviated; the adapter must supply every key listed by the
paired metadata.

~~~json
{
  "contract_version": "traffic-inference/v1",
  "source_group": "scats|intersection",
  "observation_unit_id": "1234",
  "target_hour": "2026-08-23T10:00:00+10:00",
  "feature_asof": "2026-08-23T09:00:00+10:00",
  "features": {
    "observation_unit_id": "1234",
    "channel_type": "SCATS",
    "hour_sin": 0.5,
    "hour_cos": -0.8660254,
    "vehicle_count_lag_1h": 1210.0,
    "vehicle_count_lag_24h": 1188.0,
    "vehicle_count_lag_168h": 1254.0,
    "vehicle_count_rolling_past_24h_mean": 1201.4
  }
}
~~~

Required request fields are contract_version, source_group,
observation_unit_id, target_hour, feature_asof, and features.
feature_asof must be exactly one hour before target_hour for this release.
Use the source's local-hour semantics: SCATS is fixed AEST (UTC+10), while
Transport Activity timestamps are Melbourne wall-clock labels despite a trailing
Z; do not reinterpret those labels as UTC.

The response shape is:

~~~json
{
  "contract_version": "traffic-inference/v1",
  "prediction": {
    "vehicle_count_per_hour": 1234.5
  },
  "model": {
    "release": "source-stratified-v1",
    "candidate": "lag_enhanced",
    "source_group": "scats|intersection"
  },
  "quality": {
    "status": "degraded",
    "feature_coverage": "partial",
    "warnings": ["target_hour_diagnostic_unavailable"]
  }
}
~~~

quality.status should be ok, degraded, or unavailable. A degraded prediction
may be shown with its warnings; an unavailable response must not pretend that a
zero count is a valid prediction. Keep the raw vehicle_count_per_hour in the
API response even when the UI also receives a normalized presentation score.

## Python adapter rules

Load one bundle per source group in a Python backend process and keep the
xgboost.Booster objects warm. The browser and Next.js client should send the JSON
contract to that backend; they must not receive or directly parse model.ubj.

At startup, the adapter should:

1. Select the path from the exact source_group allow-list above.
2. Read the paired metadata.json and treat it as the source of truth for
   feature_columns, encoder.feature_columns,
   encoder.model_feature_columns, encoder.model_feature_types,
   encoder.categorical_columns, encoder.categories, and
   encoder.unseen_unit_policy.
3. Verify the model SHA-256 against metadata.model_sha256 and the release entry
   in checksums.json, then load the UBJSON. Refuse startup on mismatch.
4. Build each prediction row in metadata order. Do not alphabetize, hand-copy, or
   infer feature order from the JSON request. The current release uses
   xgboost_native_categorical; preserve categorical dtypes and the model
   feature columns before calling Booster.predict.

The current unseen-unit policy maps an unknown observation_unit_id to the
native categorical missing branch and sets observation_unit_id__unseen=1.
Return unseen_observation_unit in quality.warnings. For other categorical
values, follow the paired metadata categories and fail or degrade rather than
inventing an encoder. Numeric null values should remain missing/NaN so XGBoost
can use its native missing branch; never turn a missing count into zero.

A minimal load check, after git lfs pull, is:

~~~python
from pathlib import Path
import xgboost as xgb

release = Path("ml/traffic/models/source-stratified-v1")
for model_path in release.glob("*/model.ubj"):
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    print(model_path, booster.num_features())
~~~

The adapter's prediction step should create a one-row pandas frame with the
metadata's model_feature_columns and native categorical types, then pass it to
an XGBoost DMatrix(..., enable_categorical=True) or equivalent supported
prediction path. Loading the UBJSON alone does not perform feature engineering.

## Lag freshness and missing history

For target hour T, all features must be built from observations available at
or before T - 1 hour:

- vehicle_count_lag_1h is the exact immediately preceding complete source hour;
- vehicle_count_lag_24h and vehicle_count_lag_168h are exact prior-hour values;
- rolling 24/168-hour values use past-only windows and exclude T.

The source pipeline must have a complete observation through feature_asof before
returning ok. If publication is late, the adapter should return degraded with
stale_lag_history or missing_lag_history; preserve nulls and do not shift the
target hour, forward-fill, or zero-impute. No value with a timestamp after
feature_asof may enter the row. For Transport Activity, aggregate completed
five-minute wall-clock intervals before building the hourly lags.

## Git LFS and checksums

After cloning the repository, materialize the promoted binaries and verify both
model files before starting the backend:

~~~bash
git lfs install
git lfs pull --include="ml/crowd/models/**/*.ubj,ml/traffic/models/**/*.ubj"
sha256sum ml/traffic/models/source-stratified-v1/scats-intersection/model.ubj
sha256sum ml/traffic/models/source-stratified-v1/transport-activity-countline/model.ubj
~~~

Compare those hashes with the matching metadata.json model_sha256 values and
the checksums.json entries. The existing .gitattributes keeps .ubj in Git LFS;
processed/, training/, raw snapshots, and recovery inputs remain local and
ignored.

## Demo-safe normalization

Raw counts from the two models are not comparable: a SCATS intersection total
and a Transport Activity countline volume have different units and coverage.
Keep the raw prediction for debugging, but show the UI a source-specific
percentile or 0–1 score. Build one fixed reference distribution per source group
from an explicitly chosen historical/validation prediction set and version it
with the demo. For example:

~~~text
percentile_g(x) = empirical_CDF(reference_predictions_g, x)
score_0_1 = clip(percentile_g(x), 0, 1)
~~~

Higher means “higher than usual for this source group,” not “longer route
travel time.” Never compute a shared percentile over SCATS and countline rows,
and do not label the score as a travel-time or route-congestion estimate.

## What remains software work

These models forecast fixed-site vehicle_count per hour. They are not route
travel-time models, and a fixed-site count is not an edge speed or segment
congestion value. Mapping observation units to route edges, handling spatial
coverage, calibrating route-level meaning, and defining production fallbacks
remain software work. The first integration should therefore expose the
source-specific fixed-site signal with the quality warnings above.

