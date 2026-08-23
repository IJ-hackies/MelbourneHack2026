# HeatRoute ML data

This directory owns HeatRoute's data-science inputs. The first acquisition is
scoped to the City of Melbourne demo and covers the product's named signals:
pedestrian activity, traffic, weather/microclimate, tree and building shade,
solar context, a pedestrian graph, and transparent emissions constants.

## Fetch data

Python 3.10+ is sufficient; the downloader uses only the standard library.

```powershell
python ml/scripts/fetch_datasets.py --list
python ml/scripts/fetch_datasets.py --profile core
```

Install the transformation dependencies before building processed crowd data:

```powershell
python -m pip install -r ml/requirements.txt
python ml/scripts/build_crowd_dataset.py
python ml/scripts/fetch_nasa_power_weather.py --end-date 2026-08-20
python ml/scripts/build_crowd_training_datasets.py `
  --weather ml/data/raw/nasa_power_melbourne_hourly.csv `
  --microclimate ml/data/raw/com_microclimate_readings/microclimate_readings.parquet `
  --transport ml/traffic/datasets
python ml/scripts/train_crowd_models.py --device cuda --overwrite
```

The builder streams the historical ZIP, the archived gap CSV, and the current
Parquet export into `ml/crowd/processed/`. Generated outputs remain local and
Git-ignored. See `ml/crowd/README.md` for the source precedence, schema, repair,
training-table schemas, previews, and comparison contract.

The NASA POWER helper makes validated yearly requests for the all-history
regional weather features, converts provider fill values to null, and writes a
provenance sidecar. It is a reproducible no-key fallback, not street-level
microclimate. The training builder produces two Parquet tables plus 250-row CSV
previews under `ml/crowd/training/`. The model trainer consumes their manifest,
fits CUDA-capable XGBoost count models, and writes ignored models, predictions,
and evaluation reports under `ml/crowd/training/evaluation/`. The winning
all-history artifact is separately promoted with Git LFS at
`ml/crowd/models/all-history-v1/`; its model card and portable metadata travel
with it. These outputs forecast fixed-counter pedestrian flow, not area crowd
density.

Traffic now has the same target → features → evaluation → promoted-release
shape. The finalized target reuses the immutable
`ml/traffic/processed/.traffic-recovery-20260822/` partitions, excludes all
2023 inputs, and contains 17,744,407 rows from 2024 through July 2026. Build and
train it with:

```bash
python ml/scripts/finalize_traffic_recovery.py \
  --allow-zero-eligible-scats-date 2025-05-27 \
  --memory-limit 6GB --threads 6
python ml/scripts/build_traffic_training_datasets.py \
  --memory-limit 12GiB --threads 4 --overwrite
python ml/scripts/train_traffic_models.py \
  --device cuda --n-jobs 1 --overwrite
python ml/scripts/promote_traffic_models.py --overwrite
```

The chronological split is 2024 train, 2025 validation, and 2026 test. Models
are selected only on validation. The full CUDA run selected lag-enhanced
XGBoost Poisson models for both SCATS intersections and Transport Activity
countlines; they are promoted together under
`ml/traffic/models/source-stratified-v1/`. See `ml/traffic/README.md` for row
counts, metrics, source gaps, resource settings, and release limitations. The
software-side adapter must follow `ml/traffic/SOFTWARE_HANDOFF.md`; for the
hackathon, prefer SCATS and treat Transport Activity as degraded/optional.

Downloads are written atomically to the catalogued storage root. Crowd sources
land under `ml/crowd/datasets/<dataset-id>/`, traffic sources under
`ml/traffic/datasets/<dataset-id>/`, and shared environmental, routing, shade,
and emissions sources remain under `ml/data/raw/<dataset-id>/`. Interrupted
direct downloads leave a `.part` file and resume when the publisher supports
HTTP ranges. Each attempt writes retrieval time, response metadata, byte count,
SHA-256, domains, storage root, and licence context to `ml/data/provenance/`.

The City Transport Activity archives include multiple travel modes. They are
stored once under `ml/traffic/datasets/` and tagged for both `traffic` and
`crowd`; this avoids duplicating the publisher snapshots. `--raw-dir` can still
override all catalogued roots for an isolated download or test.

The archived 12 January 2025 City pedestrian export fills the live portal's
missing interval. Keep the archive immutable and use only 1 November 2022 to
20 August 2024 when building a harmonised hourly table. Its earlier and later
records overlap other publisher snapshots and can contain different revisions.

Useful targeted commands:

```powershell
python ml/scripts/fetch_datasets.py --dataset com_pedestrian_counts_current
python ml/scripts/fetch_datasets.py --dataset com_pedestrian_counts_wayback_20250112
python ml/scripts/fetch_datasets.py --dataset vicmap_tree_urban_com_bbox
python ml/scripts/fetch_datasets.py --profile extended
python ml/scripts/fetch_datasets.py --profile manual --list
```

`--force` refreshes changing snapshots. `--bbox min_lon,min_lat,max_lon,max_lat`
overrides the catalog rectangle for paginated ArcGIS layers.

## Profiles and boundaries

- `core` is the local hackathon mirror. Some 2026 archives are year-to-date or
  partial-month snapshots and are labelled accordingly.
- `extended` contains broader routing coverage, traffic validation, historic
  heat, regional solar and optional air-quality data.
- `manual` records sources that are multi-gigabyte, key-gated, broken or have
  unclear redistribution rights. BOM's public pages also reject this automated
  client with HTTP 403, so their exact endpoints are catalogued but not bypassed.
  The 0.1 m City surface model is about 12.98 GB and is deliberately not part of
  the automatic install.

The promoted pedestrian graph (`melbourne-metro-v1`, see `ml/routing/README.md`)
now merges City of Melbourne's own data with an OSM extract
(`osm_inner_metro_overpass`) for inner+middle-ring coverage — still not full
Greater Melbourne. Building and tree shade are geometry/solar calculations;
they should not be forced into the forecasting model.

## Licence cautions

Most City of Melbourne and Victorian Government sources are CC BY 4.0 and need
publisher attribution. OpenStreetMap is ODbL 1.0. The live City pedestrian
hourly dataset currently has no explicit licence value in its API metadata, so
keep it internal until redistribution terms are confirmed. Anonymous BOM web
products are retained only as local/internal snapshots; obtain appropriate BOM
access before publication or commercial use. Raw files and generated
provenance are ignored by Git.
