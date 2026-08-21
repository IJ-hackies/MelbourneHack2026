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

Downloads are written atomically under `ml/data/raw/<dataset-id>/`. Interrupted
direct downloads leave a `.part` file and resume when the publisher supports
HTTP ranges. Each attempt writes retrieval time, response metadata, byte count,
SHA-256 and licence context to `ml/data/provenance/`.

Useful targeted commands:

```powershell
python ml/scripts/fetch_datasets.py --dataset com_pedestrian_counts_current
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

The City pedestrian graph is municipal, not metropolitan. Use the OSM Victoria
extract (ODbL) for broader coverage. Building and tree shade are geometry/solar
calculations; they should not be forced into the forecasting model.

## Licence cautions

Most City of Melbourne and Victorian Government sources are CC BY 4.0 and need
publisher attribution. OpenStreetMap is ODbL 1.0. The live City pedestrian
hourly dataset currently has no explicit licence value in its API metadata, so
keep it internal until redistribution terms are confirmed. Anonymous BOM web
products are retained only as local/internal snapshots; obtain appropriate BOM
access before publication or commercial use. Raw files and generated
provenance are ignored by Git.
