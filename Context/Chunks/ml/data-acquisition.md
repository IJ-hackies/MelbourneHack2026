---
id: ml/data-acquisition
title: ML dataset acquisition and raw source boundary
sources:
  - ml/README.md
  - ml/data/catalog.json
  - ml/scripts/fetch_datasets.py
  - ml/scripts/fetch_nasa_power_weather.py
  - ml/crowd/README.md
  - ml/traffic/README.md
  - .gitignore
links: [heatroute, ml/planned-forecasting, ml/crowd-processing, ml/crowd-training, ml/traffic-processing]
verified: initial
---

## What this is

The ML lane owns a reproducible catalog and standard-library Python downloader
for official HeatRoute inputs. Catalog-controlled storage routes crowd snapshots
to `ml/crowd/datasets/`, traffic snapshots to `ml/traffic/datasets/`, and shared
environmental/routing inputs to `ml/data/raw/`. It covers pedestrian activity,
vehicle traffic, weather/microclimate, pedestrian routing, tree/building shade
geometry, solar context, and emissions constants. (`ml/data/catalog.json`)

Crowd acquisition includes a 12 January 2025 Internet Archive capture of the
official City hourly CSV. The full capture is retained immutably, while its
1 November 2022 to 20 August 2024 slice fills the 659-day gap between the
historical attachment and current portal export. (`ml/data/catalog.json`,
`ml/crowd/README.md`)

The catalog separates `core`, `extended`, and `manual` sources. Core is the City
of Melbourne hackathon mirror; extended adds broader coverage and validation;
manual records large, key-gated, blocked, or licence-unclear sources without
silently downloading them. (`ml/README.md`, `ml/data/catalog.json`)

NASA POWER regional hourly weather is acquired through a dedicated yearly API
helper because the dynamic point service needs validation and normalization.
The helper writes one ignored CSV plus provenance, and converts provider fill
sentinels to null. (`ml/scripts/fetch_nasa_power_weather.py`)

## Key files

- `ml/data/catalog.json` - source URLs, publishers, signal families, domains,
  storage roots, profiles, outputs, licences, caveats, and the default City
  bounding rectangle.
- `ml/scripts/fetch_datasets.py` - selection, atomic/resumable direct downloads,
  catalog-directed storage, paginated ArcGIS clipping, checksums, and per-source
  provenance. `--raw-dir` explicitly overrides catalog storage roots.
- `ml/scripts/fetch_nasa_power_weather.py` - no-key NASA POWER hourly point
  acquisition, yearly retries, normalization, validation, and provenance.
- `ml/crowd/datasets/`, `ml/traffic/datasets/`, and `ml/data/raw/` - ignored
  local publisher snapshots; not transformed feature stores and not committed.
- `ml/data/provenance/` - ignored retrieval records including byte counts,
  response metadata, status, checksum, timestamp, source and licence context.

## Invariants

- Raw files are immutable publisher snapshots. Crowd harmonisation writes to
  ignored `ml/crowd/processed/`; feature building writes to ignored
  `ml/crowd/training/`; traffic cleaning writes to ignored
  `ml/traffic/processed/`. None may overwrite raw paths.
- A snapshot has one physical storage location even when its catalog `domains`
  include both crowd and traffic. Multimodal Transport Activity archives live
  under traffic and are shared by catalog reference rather than duplication.
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
- Git publication is code-and-release only. Raw downloads, provenance, canonical
  targets, feature tables, previews, recovery, and evaluation runs stay ignored.
  Scripts, tests, requirements, catalog and small pipeline configs stay visible;
  only the exact `all-history-v1` crowd files and complete
  `source-stratified-v1` traffic bundle are unignored model artifacts.

## How to extend

Add a catalog entry with a unique id and the narrowest suitable profile. Use
`direct` for stable files, `arcgis_geojson` for paginated FeatureServer layers,
and `manual` for sources that must not be acquired automatically. Run `--list`,
fetch the target id, inspect its provenance, and verify its native container or
schema before building transformations. (`ml/scripts/fetch_datasets.py`)

Use the processing/training/modeling domain chunks for derived artifacts and
`ml/model-handoff` for the only publishable releases, compute, and software
boundary. Define stable route-segment and timestamp contracts before fixed-site
outputs cross into routing software.

## Gotchas

- The default bbox is a hackathon rectangle around the municipality, not its
  legal boundary. The City pedestrian graph does not cover Greater Melbourne.
- The historical attachment's filename claims 14 December 2022, but its actual
  records end 31 October 2022. The archived snapshot fills every missing day,
  but overlaps both neighbouring sources and overlapping revisions differ; use
  only the catalogued gap dates during harmonisation.
- Sensor ids can move; joins must respect sensor location history and notes.
- Transport 2026 and SCATS August 2026 are incomplete time periods.
- BOM endpoints returned HTTP 403 to the automated client and remain manual.
- The 0.1 m surface model is about 12.98 GB and intentionally manual; building
  extrusions and clipped urban-tree heights are the initial shade inputs.
- AirWatch is sparse station data, not route-local air-quality evidence.
