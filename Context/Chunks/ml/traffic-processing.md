---
id: ml/traffic-processing
title: Canonical hourly vehicle-traffic target processing
sources:
  - ml/scripts/build_traffic_dataset.py
  - ml/scripts/finalize_traffic_recovery.py
  - ml/traffic/README.md
  - ml/traffic/config/transport_activity_countlines.csv
  - ml/traffic/config/expected_coverage.json
  - ml/tests/test_build_traffic_dataset.py
  - ml/tests/test_finalize_traffic_recovery.py
links: [heatroute, ml/data-acquisition, ml/traffic-training, ml/traffic-modeling, ml/planned-forecasting]
verified: initial
---

## What this is

The traffic cleaner reduces City Transport Activity road countlines and
Victorian SCATS detector-day archives into one canonical hourly vehicle target.
It is a cleaning boundary only: it does not acquire sources, join weather or
road features, create forecast horizons, train models, calibrate 0-1 scores, or
map observations onto route edges. (`ml/scripts/build_traffic_dataset.py`)

The generated artifact is one content-versioned Parquet with one row per source
dataset, observation unit, and UTC hour. The raw builder can publish a 500-row
stratified preview; the recovery finalizer instead validates completed Parquet
partitions and publishes a complete readiness manifest without reopening raw
ZIPs. Generated files live under ignored `ml/traffic/processed/`.
(`ml/traffic/README.md`)

The current training target intentionally excludes all 2023 data and reuses 23
immutable 2024–2026 recovery partitions. It contains 17,744,407 unique-key rows
(17,526,830 SCATS and 217,577 Transport Activity), with no null or negative
targets. (`ml/scripts/finalize_traffic_recovery.py`, `ml/traffic/README.md`)

## Key files

- `ml/scripts/build_traffic_dataset.py` - streaming ZIP discovery, source
  validation, hourly reduction, strict/partial coverage checks, deterministic
  ordering/preview, explicit Arrow schema, checksums, and atomic publication.
- `ml/scripts/finalize_traffic_recovery.py` - read-only recovery validation,
  explicit year exclusion, bounded DuckDB merge/sort, coverage reconciliation,
  checksums, and atomic complete-artifact publication.
- `ml/traffic/config/transport_activity_countlines.csv` - reviewed exact
  `(count_location_id, countline_name)` registry: 53 approved road channels and
  114 excluded or ambiguous channels across 167 observed IDs.
- `ml/traffic/config/expected_coverage.json` - V1 completeness contract: City
  archives for 2023-2026 and SCATS from 1 January 2023 through 31 July 2026.
- `ml/tests/test_build_traffic_dataset.py` - source-shaped TA/SCATS contract
  tests, including misleading-Z/DST behavior and deterministic output.
- `ml/tests/test_finalize_traffic_recovery.py` - synthetic recovery-selection,
  schema/key/coverage, zero-eligible-date, overwrite, and preservation tests.

## Invariants

- The canonical key is `source_dataset_id + observation_unit_id +
  hour_start_utc`; output keys must be unique, non-null, and exactly hourly.
- `vehicle_count` is a non-negative integer target and
  `log1p_vehicle_count` is its convenience transform. Unknown targets stay
  absent; they are never imputed to zero.
- Transport Activity admits only registry rows where
  `traffic_eligible=true` and `review_status=approved`. Runtime name heuristics
  are forbidden. Unknown publisher classes, unreviewed ID/name pairs, malformed
  counts, and non-DST reversed intervals fail closed.
- Motor classes are bus, car, emergency car/van, fire engine, minibus,
  motorbike, rigid, taxi, truck, and van. Cyclist, pedestrian, and e-scooter
  rows are non-motor evidence only. A zero is derived only when an approved
  countline/hour reports non-motor classes and no motor row; silent hours remain
  absent and the zero is quality flagged.
- City timestamps are Melbourne wall-clock labels despite their trailing `Z`.
  They are localized with `Australia/Melbourne`, raw labels and UTC lineage are
  retained, and the repeated fallback hour deterministically uses standard
  time. Annual `02:55 -> 02:00` rollback rows are accepted only on the verified
  DST transition and are flagged.
- SCATS is interpreted in its documented fixed AEST (UTC+10), not Melbourne
  daylight time. `V00..V95` are grouped into site/intersection hours across all
  detectors. `-1` is missing, `0` is real, and all-missing site-hours are
  omitted and counted in the manifest.
- SCATS coordinates come from the signal-site table and the configured bbox is
  explicit. Missing or unmatched coordinates are never invented.
- Strict raw-build mode refuses incomplete configured coverage. `--allow-partial`
  produces a visibly partial artifact. The recovery finalizer accepts only its
  explicit 2024–2026 contract and separates publisher gaps from a reviewed date
  with zero sites inside the spatial filter. The manifest is written last and
  every input and output has a SHA-256 hash.
- Recovery partitions are immutable provenance. Finalization must not rewrite
  or delete them, and it must record selected and excluded inputs separately.

## How to extend

Use `ml/traffic-training` for feature/horizon construction and
`ml/traffic-modeling` for fitting and promotion. If the date contract changes,
create a new content-versioned artifact; do not silently append another year to
this release or reinterpret an allowlisted publisher gap.

When adding a source schema or class, update the explicit contract, registry,
manifest diagnostics, and source-shaped tests together. Rebuilds are currently
CPU-heavy; optimize vectorization or external staging without changing the
canonical key, timezone, missingness, or quality semantics.

## Gotchas

- The old partial artifact remains local evidence, but it is not the training
  input. The complete content-versioned artifact covers 2024 through July 2026
  and explicitly excludes 2023.
- SCATS dates 2024-09-30, 2025-02-28, and 2025-12-31 are publisher gaps.
  The official 2025-05-27 file has observations but zero eligible City-bbox
  sites; do not misclassify it as a missing publisher file.
- Transport Activity publishes positive events only. Absence is not a zero.
- SCATS `CT_RECORDS` and alarm fields are retained as quality evidence; interval
  validity is derived from `V00..V95`, with `-1` handled before summing.
- A Transport Activity observation unit is a road countline; a SCATS unit is an
  intersection total. Their magnitudes are not interchangeable calibration
  scales even though they share `vehicle_count`.
