---
id: ml/crowd-modeling
title: Crowd model training and evaluation
sources:
  - ml/scripts/train_crowd_models.py
  - ml/tests/test_train_crowd_models.py
  - ml/crowd/README.md
  - ml/crowd/SOFTWARE_HANDOFF.md
  - ml/crowd/models/all-history-v1/model.ubj
  - ml/crowd/models/all-history-v1/metadata.json
  - ml/crowd/models/all-history-v1/README.md
  - ml/requirements.txt
links: [heatroute, ml/crowd-processing, ml/crowd-training, ml/model-handoff, ml/planned-forecasting]
verified: initial
---

## What this is

The ML lane has a reproducible XGBoost fit/evaluate command for one-hour-ahead
fixed-counter `pedestrian_flow`. It trains the all-history and recent-enhanced
candidates plus a matched recent-window all-history ablation. The command uses
the feature lists and chronological splits in the training manifest; it does
not create a random split or treat the target as area crowd density.
(`ml/scripts/train_crowd_models.py`)

The 22 August 2026 CUDA run scored 296,087 identical test keys. All history led
with MAE 47.2055, RMSE 110.0230, and Poisson deviance 14.8839. The matched
recent ablation scored 49.6585/118.0417/16.4785; recent enhanced scored
56.1154/127.0281/20.2527. Full generated runs remain ignored under
`ml/crowd/training/evaluation/`; the byte-identical winner is promoted as the
Git-LFS-backed `ml/crowd/models/all-history-v1/model.ubj`.
(`ml/crowd/README.md`, `ml/crowd/models/all-history-v1/README.md`)

## Key files

- `ml/scripts/train_crowd_models.py` - input validation, train-only categorical
  encoding, CUDA/CPU selection, Poisson XGBoost fitting, shared-test evaluation,
  baselines, ablation, artifact hashing, and CLI.
- `ml/tests/test_train_crowd_models.py` - synthetic CPU-sized end-to-end contract
  for leakage exclusions, split discipline, shared keys, metrics, missingness,
  unseen sensors, serialization, and overwrite protection.
- `ml/crowd/models/all-history-v1/model.ubj` - tracked 65.8 MiB promoted winner;
  SHA-256 `34918786e699c2fab26c4a34d6eceecf844fd3819a4dce1a682c1e294ed9c1dd`.
- `ml/crowd/models/all-history-v1/metadata.json` - portable feature order,
  encoder categories, parameters, source hashes, and artifact identity.
- `ml/crowd/models/all-history-v1/README.md` - model card, evaluation scope,
  manual sanity checks, retrieval/loading instructions, and limitations.
- `ml/crowd/training/evaluation/metrics.json` - ignored run manifest and scores;
  model UBJSON, metadata, predictions, and CSV reports sit beside it.
- `ml/requirements.txt` - transformation dependencies plus XGBoost 3.x.

## Model and training detail

- The promoted model is XGBoost 3.4.1 gradient-boosted trees with
  `count:poisson`, `poisson-nloglik`, `hist`, native categoricals, seed 42, and
  CUDA. It used depth 8, learning rate 0.05, 0.8 row/column sampling, minimum
  child weight 20, L2 10, L1 0, max delta step 0.7, eight CPU threads, 2,500
  estimators, and 100-round early stopping. Best iteration was the 2,499 ceiling.
- The all-history split was 5,961,171 training rows (May 2009–2024), 815,447
  validation rows (2025), and 296,087 test rows (1 January–11 May 2026), with
  111 training sensor categories. Target mean on test was 408.05 people/hour.
- Metadata declares 28 inputs and 29 model columns after `sensor_id__unseen`:
  sensor identity, calendar/cyclic/holiday/DST fields, exact 1/24/168-hour lags,
  past-only 24/168-hour statistics, and regional NASA weather. The three lag
  fields dominate recorded gain; target-hour weather needs forecast equivalents.
- This is not a neural network, so a scalar neural-style parameter count is not
  meaningful. An XGBoost 3.4.1 UBJSON inspection found exactly 2,500 trees,
  596,440 decision nodes, 598,940 learned leaves (1,195,380 total nodes),
  120,275 categorical split nodes, and depth 8 in every tree. Only 27 of 29
  columns appear in splits; constant `prediction_horizon_hours` and the unseen
  flag are unused, though unseen identity still takes the categorical branch.
- The winner's test MAE was 47.2055 versus 98.5980 for a 168-hour lag baseline
  on available rows. Seen-sensor MAE was 46.2887 over 275,430 rows; seven unseen
  sensors used the missing branch and scored 59.4297 over 20,657 rows.

## Software use and compute

`ml/crowd/SOFTWARE_HANDOFF.md` defines `crowd-inference/v1`. A Python service
must verify the hash, load and keep the booster warm, assemble metadata-ordered
native-categorical features, and return raw `pedestrian_flow_per_hour` plus
`ok|degraded|unavailable` warnings. The browser must not load UBJSON. Do not
zero-impute history, invent confidence, compare raw sensors without calibration,
or turn a fixed-counter value directly into a route-edge cost.

Training used CUDA and full pandas tables; inputs are 330 MiB and 153 MiB
compressed Parquet, and an audit measured about 5.1 GiB RSS merely loading both
selected-column frames. Elapsed training time, peak RAM/VRAM, energy, and GPU
model were not retained; 8–16 GiB host RAM is a planning range. A local
100,000-row audit took about 1.29 s on CPU and 0.102 s on an RTX 5060 Ti,
excluding feature assembly/transfer. Lower-volume serving smoke and caveats
live in `ml/model-handoff`; none of these measurements is an application SLA.

## Invariants

- Both candidates score the exact same test `observation_key` set through
  11 May 2026; `post_test` is never part of the claimed comparison.
- The all-history table is also retrained from 1 January 2023 for the matched
  ablation. Compare this model with recent enhanced to isolate feature uplift
  from additional historical coverage.
- `sensor_id` is categorical. Sensors absent from the training split map to the
  model's missing-category branch and are reported separately; seven real test
  sensors are currently unseen during training.
- Predictions are nonnegative. Report overall MAE, RMSE, Poisson deviance,
  lag baselines, per-sensor scores, seen/unseen scores, and missingness strata.
- Optional source gaps remain null. Direction counts and same-hour target or
  Transport Activity counts remain excluded as leakage.
- UBJSON models and metadata are paired with SHA-256, feature order, encoder
  categories, parameters, best iteration, device, and dataset hashes.
- A promoted release directory is immutable. Its model, metadata, checksum, and
  model card move together; replacements use a new versioned directory only
  after winning the same-key evaluation.
- Git LFS owns promoted `*.ubj` files. A clone must materialize the binary with
  `git lfs pull`; an LFS pointer is not a loadable XGBoost model.

## How to extend

Treat all history as the current winner. Implement its documented adapter before
software consumption. Before promoting a replacement, repeat the identical-key
and matched-window comparison. Investigate why enhanced citywide features
degrade MAE; publish a new immutable version with metadata, checksum/model card,
handoff, resource telemetry, and a same-key win.

## Gotchas

- Recent enhanced is 13.0% worse in MAE than its matched recent ablation; the
  presence of extra features is not evidence of uplift.
- All history reached the configured 2,500-round ceiling, so further tuning may
  improve it; the current run is a verified first model, not a final optimum.
- Target-hour NASA weather requires an equivalent forecast at production time.
- Loading UBJSON does not engineer features. The model predicts City-counter
  flow; its adapter, route mapping, uncertainty, and fallbacks are unimplemented.
