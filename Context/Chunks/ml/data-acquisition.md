---
id: ml/data-acquisition
title: ML dataset acquisition and raw source boundary
sources:
  - ml/README.md
  - ml/data/catalog.json
  - ml/scripts/fetch_datasets.py
  - .gitignore
links: [heatroute, ml/planned-forecasting]
verified: initial
---

## What this is

The ML lane owns a reproducible catalog and standard-library Python downloader
for official HeatRoute inputs. It covers pedestrian activity, vehicle traffic,
weather/microclimate, pedestrian routing, tree/building shade geometry, solar
context, and emissions constants. (`ml/data/catalog.json`)

The catalog separates `core`, `extended`, and `manual` sources. Core is the City
of Melbourne hackathon mirror; extended adds broader coverage and validation;
manual records large, key-gated, blocked, or licence-unclear sources without
silently downloading them. (`ml/README.md`, `ml/data/catalog.json`)

## Key files

- `ml/data/catalog.json` - source URLs, publishers, signal families, profiles,
  outputs, licences, caveats, and the default City bounding rectangle.
- `ml/scripts/fetch_datasets.py` - selection, atomic/resumable direct downloads,
  paginated ArcGIS clipping, checksums, and per-source provenance.
- `ml/data/raw/` - ignored local publisher snapshots; not a transformed feature
  store and not committed to Git.
- `ml/data/provenance/` - ignored retrieval records including byte counts,
  response metadata, status, checksum, timestamp, source and licence context.

## Invariants

- Raw files are immutable publisher snapshots. Transformations and harmonised
  features must be written to a separate future path.
- Every automated source has a stable catalog id, explicit output name, source
  page, publisher, licence state, and a provenance record.
- Direct downloads use `.part` files and atomic replacement. Existing complete
  files are checksummed and skipped unless `--force` is supplied.
- ArcGIS layers are clipped to the catalog bbox and paginated; do not request
  the statewide 11.3-million-tree service as one unbounded export.
- CC BY sources require attribution. OSM-derived databases retain ODbL duties.
  The City hourly pedestrian data and BOM products have additional licence or
  access caveats recorded in the catalog.
- Geometry sources support shade and routing calculations; their presence does
  not imply a forecasting model or application integration exists.

## How to extend

Add a catalog entry with a unique id and the narrowest suitable profile. Use
`direct` for stable files, `arcgis_geojson` for paginated FeatureServer layers,
and `manual` for sources that must not be acquired automatically. Run `--list`,
fetch the target id, inspect its provenance, and verify its native container or
schema before building transformations. (`ml/scripts/fetch_datasets.py`)

Create separate chunks when feature engineering, training/evaluation, and
serving become real source areas. Define stable route-segment and timestamp
contracts before those outputs cross into software.

## Gotchas

- The default bbox is a hackathon rectangle around the municipality, not its
  legal boundary. The City pedestrian graph does not cover Greater Melbourne.
- Current City pedestrian hourly exports and the 2009-2022 attachment are
  separate raw sources and require deduplication/gap analysis.
- Sensor ids can move; joins must respect sensor location history and notes.
- Transport 2026 and SCATS August 2026 are incomplete time periods.
- BOM endpoints returned HTTP 403 to the automated client and remain manual.
- The 0.1 m surface model is about 12.98 GB and intentionally manual; building
  extrusions and clipped urban-tree heights are the initial shade inputs.
- AirWatch is sparse station data, not route-local air-quality evidence.
