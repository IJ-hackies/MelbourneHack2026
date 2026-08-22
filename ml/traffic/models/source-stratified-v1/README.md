# Traffic model bundle: source-stratified-v1

## What this is

This is HeatRoute's first promoted traffic release. It contains one XGBoost
Poisson model for SCATS intersection totals and another for reviewed Transport
Activity countline volumes. The scales are intentionally not pooled.

Software integration instructions are in [the traffic software handoff](../../SOFTWARE_HANDOFF.md).

Both models predict the next hour at fixed observation units. They do not predict
travel time, route-edge congestion, or a pedestrian exposure score; those mappings
and the production inference boundary are not implemented.

## Artifacts and selection

Each source directory contains `model.ubj` and its paired `metadata.json`.
`release_manifest.json` records selection, provenance, CUDA evidence, and scores;
`checksums.json` authenticates every release artifact.

Winners used validation-only selection on the chronological validation split,
using Poisson deviance,
then MAE, then RMSE, with base before lag_enhanced as the deterministic final
tie-break. Held-out test metrics were used only for final reporting.

| Source group | Winner | Model bytes | SHA-256 |
| --- | --- | ---: | --- |
| scats\|intersection | lag_enhanced | 33,284,016 | `aefbbcdbf4dc7a6bbcb4ef966b58a494c4519cc35ae78245f7a0d3686a3b7f56` |
| transport_activity\|countline | lag_enhanced | 4,531,207 | `e9419d9628ff5c179d5beb8f4dc69a62770ce4af276a9bbd86df0c88e7145b9f` |

## Training and evaluation

The source evaluation uses chronological train, validation, and held-out test
splits. CUDA/run evidence and exact split provenance are recorded in
`release_manifest.json`; the selected winner for each group is shown below.

| Source group | Split | Rows | MAE | RMSE | Poisson deviance |
| --- | --- | ---: | ---: | ---: | ---: |
| scats\|intersection | Validation | 6,768,092 | 82.2650 | 156.9917 | 14.1533 |
| scats\|intersection | Held-out test | 3,988,344 | 81.4622 | 150.7749 | 13.7267 |
| transport_activity\|countline | Validation | 58,404 | 23.5756 | 58.1811 | 7.6022 |
| transport_activity\|countline | Held-out test | 116,551 | 48.9760 | 97.0135 | 19.5501 |

## Input contract

Use each `metadata.json` as the source of truth for feature order, train-only
categorical encoder state, missing/unseen category behavior, parameters, best
iteration, and source hashes. Do not pass raw SCATS or countline rows directly
to a model. Serving must reproduce the lag-enhanced one-hour feature boundary.
Missing lag values may remain missing; they must not be converted to zero.

## Getting and checking the models

Git LFS is required to materialize both UBJSON files after cloning:

```bash
git lfs install
git lfs pull --include="ml/traffic/models/source-stratified-v1/**/*.ubj"
sha256sum ml/traffic/models/source-stratified-v1/*/model.ubj
```

A basic load check is:

```python
from pathlib import Path
import xgboost as xgb

for path in Path("ml/traffic/models/source-stratified-v1").glob("*/model.ubj"):
    model = xgb.Booster()
    model.load_model(path)
```

Loading a model is not the complete inference path; apply the exact paired
metadata transformation and choose the model matching the source group.

## Limitations

- These are fixed-site count forecasts, not route-edge traffic or travel time.
- Lag features require a freshness and missing-history policy in serving.
- SCATS and countline outputs are not interchangeable calibration scales.
- There is no calibrated uncertainty, retraining policy, adapter implementation,
  effective-dated route mapping, or production fallback implementation yet.
- The canonical training release deliberately excludes all 2023 data.
- Evaluation and recovery directories remain immutable provenance inputs.
