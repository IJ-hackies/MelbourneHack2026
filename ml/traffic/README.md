# Traffic forecasting workspace

This workspace owns immutable traffic snapshots, canonical hourly labels,
leakage-safe feature tables, offline evaluation, and promoted traffic models.
Generated `processed/`, `training/`, and recovery artifacts remain local and
Git-ignored; versioned winners under `models/` are the portable release layer.

For the software-side inference boundary, model routing, feature encoding,
freshness, checksums, and demo-safe normalization, read the
[traffic software handoff](SOFTWARE_HANDOFF.md) before integrating the bundle.

## Canonical target

The canonical output has one row per source dataset, observation unit, and UTC
hour. `vehicle_count` is a non-negative integer; missing target hours remain
absent rather than becoming zero. SCATS totals represent intersections while
Transport Activity values represent reviewed road countlines, so their count
scales remain separate throughout training and inference.

Transport Activity timestamps are Melbourne wall-clock labels despite their
trailing `Z`. SCATS timestamps are fixed AEST (UTC+10). The cleaner retains the
raw timestamp and quality lineage for both sources. Only explicitly approved
road channels in `config/transport_activity_countlines.csv` enter the traffic
target.

The finalized training target deliberately excludes the complete 2023 archive.
It reuses, without modifying, the 24 Parquet partitions in
`processed/.traffic-recovery-20260822/`; 23 partitions from 2024–2026 are
selected and the 2023 Transport Activity partition is retained but excluded.
No raw SCATS ZIP is reopened by the finalizer.

```bash
python ml/scripts/finalize_traffic_recovery.py \
  --allow-zero-eligible-scats-date 2025-05-27 \
  --memory-limit 6GB \
  --threads 6
```

The complete artifact contains 17,744,407 unique natural-key rows: 17,526,830
SCATS rows and 217,577 Transport Activity rows. It has no 2023, null, negative,
or duplicate-key target rows. Three missing SCATS dates are publisher gaps
(2024-09-30, 2025-02-28, and 2025-12-31); the official 2025-05-27 source has
no sites inside the configured City bounding box. Unexpected missing dates are
zero. The manifest beside the content-versioned Parquet is the readiness marker.

## Feature tables

The feature builder preserves all 17,744,407 target rows in both candidates:

- `traffic_training_base.parquet` contains identity, source/scope, calendar,
  holiday, coordinate, and quality features available before prediction.
- `traffic_training_lag_enhanced.parquet` adds exact 1/24/168-hour count lags
  and strictly past-only 24/168-hour rolling statistics.

The prediction horizon is one hour and `feature_asof` is the target hour minus
one hour. The chronological split is train=2024 (6,813,016 rows),
validation=2025 (6,826,496), and test=2026 through 31 July (4,104,895). The
candidate keys and labels are identical.

```bash
python ml/scripts/build_traffic_training_datasets.py \
  --memory-limit 12GiB \
  --threads 4 \
  --overwrite
```

DuckDB performs the ordered, bounded-memory Parquet build. Increasing worker
count is useful only while the configured memory limit can sustain each worker;
the proven full run uses four threads and 12 GiB. GPU/VRAM does not accelerate
this SQL/window feature stage.

## CUDA model training and release

The trainer fits XGBoost Poisson models independently for the SCATS intersection
and Transport Activity countline groups. It evaluates base and lag-enhanced
candidates using chronological validation for model selection, then reports the
untouched 2026 test split. `--device cuda` is CUDA-only and never silently falls
back to CPU. Full mode streams Parquet batches through XGBoost external memory.

```bash
python ml/scripts/train_traffic_models.py \
  --device cuda \
  --n-jobs 1 \
  --overwrite

python ml/scripts/promote_traffic_models.py --overwrite
```

The 22 August 2026 full CUDA run used all rows and selected lag-enhanced for both
source groups. Held-out test metrics were:

| Source model | Test rows | MAE | RMSE | Poisson deviance |
| --- | ---: | ---: | ---: | ---: |
| SCATS intersection | 3,988,344 | 81.4622 | 150.7749 | 13.7267 |
| Transport Activity countline | 116,551 | 48.9760 | 97.0135 | 19.5501 |

The promoted release is a two-model bundle under
`models/source-stratified-v1/`, not a single pooled model. Its manifest records
validation-based selection, held-out test metrics, source hashes, CUDA evidence,
feature/encoder metadata, and artifact checksums. Loading a UBJSON model alone
does not perform feature engineering; serving must reproduce the corresponding
metadata contract.

The currently promoted `source-stratified-v1` bundle is the first CUDA release
and predates the leakage-safe feature correction. Its target-hour diagnostic
fields are a known limitation—materially more important for Transport Activity
than SCATS—so SCATS is the preferred hackathon demo path. The handoff documents
the limitation and the safe software boundary.

## Verification

```bash
python -m unittest \
  ml.tests.test_build_traffic_dataset \
  ml.tests.test_finalize_traffic_recovery \
  ml.tests.test_build_traffic_training_datasets \
  ml.tests.test_train_traffic_models \
  ml.tests.test_promote_traffic_models
```

Recovery partitions and raw publisher snapshots are provenance inputs. Never
delete or rewrite them when rebuilding features, retraining, or promoting a new
version. Promoted `*.ubj` files use Git LFS.
