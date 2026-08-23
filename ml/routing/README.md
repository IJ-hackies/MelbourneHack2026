# Routing: pedestrian graph build pipeline

Builds the real, checksum-verified pedestrian routing graph served by
`api/route-planner.py`. Mirrors `ml/crowd/`'s offline-build → promoted-artifact
pattern.

## Source data

- `com_pedestrian_network` (City of Melbourne Pedestrian Network export, see
  `ml/data/catalog.json`) — a GeoJSON `FeatureCollection` of footpath
  `LineString` segments. Covers the City of Melbourne municipality bbox and
  stays authoritative there — the original, single-source `melbourne-inner-v1`
  release used only this.
- `osm_inner_metro_overpass` (`ml/routing/scripts/fetch_osm_extract.py`) — a
  single ~15km-radius bbox query against the public Overpass API, filtered
  to standard foot-routing `highway` tags. Covers the surrounding
  inner+middle-ring councils the City dataset doesn't reach (Port Phillip,
  Yarra, Stonnington, inner Boroondara, Moreland/Merri-bek, Maribyrnong,
  inner Moonee Valley, inner Glen Eira). Chosen over the catalogued
  full-state `osm_victoria_pbf` Geofabrik export specifically to avoid a new
  heavy geospatial dependency (osmium/pyrosm/osmnx) just to parse a PBF for
  a single bbox extract — `osm_victoria_pbf` stays catalogued for a possible
  future full-metro pass.

Queries outside the current (metro) bbox correctly report
`quality.status: "unavailable"` rather than silently guessing.

## Pipeline

```
python ml/routing/scripts/fetch_sources.py       # downloads pedestrian_network.zip (City of Melbourne)
python ml/routing/scripts/fetch_osm_extract.py   # downloads the OSM inner+middle-ring extract
python ml/routing/scripts/build_graph.py         # merges both sources -> node/adjacency graph
python ml/routing/scripts/validate_topology.py   # fails the build if connectivity < threshold
python ml/routing/scripts/fetch_tree_canopy.py   # downloads City tree canopy centroid points
python ml/routing/scripts/build_shade_grid.py    # buckets centroids into a density grid
python ml/routing/scripts/serialize_runtime_graph.py  # promotes graph.json + shade_grid.json + metadata.json
```

### Merging two independently-digitized sources needs proximity snapping, not just exact matching

`build_graph.py` treats coordinate pairs (rounded to ~1cm) as shared graph
nodes *within* a single source — the City dataset's own segment endpoints,
and OSM's own way endpoints, each coincide exactly at real intersections, so
no fuzzy matching is needed there. **Across the two sources this does not
hold**: an actual merge attempt using only exact-coordinate matching produced
two fully disconnected components (the entire City network as one island,
the entire OSM network as another) — City of Melbourne traces dedicated
footpath/sidewalk geometry, OSM mostly traces road centrelines, and two
independently-surveyed datasets essentially never share literal coordinates
even where they run a few metres apart along the same physical street.

`build_graph.py` therefore also does a **proximity snap**: a new point first
tries an exact match (cheap, correct within one source); failing that, it
searches a grid-bucket spatial index (cell size == `SNAP_TOLERANCE_M`, 20m)
for an existing node from *either* source within tolerance, and reuses it
instead of creating a near-duplicate. Edge dedup then happens on the
resolved node-id pair, not raw coordinates, so a geometrically-duplicate
edge introduced by the two sources tracing the same street is still caught
even though their raw coordinates never matched. See the module docstring
for the full reasoning.

`fetch_tree_canopy.py`/`build_shade_grid.py` add a real, if approximate,
canopy-density score (`com_tree_canopy_2021`, see `ml/data/catalog.json`) —
a count of tree canopy centroids per ~40m grid cell, normalised 0..1 against
the 95th-percentile cell. `serialize_runtime_graph.py` embeds each edge's
score (average of its endpoints' cells) as a third element on every
adjacency entry, and promotes the grid itself for point queries
(`api/_shared/shade_lookup.py`, used by `api/shade.py`). This is a
tree-density proxy, not a solar-shade calculation — no polygon area, height,
or sun-angle data exists in the source, so it's labelled "canopy density"
everywhere it's surfaced, never "shade %". **Canopy coverage is still
City-of-Melbourne-only** as of `melbourne-metro-v1` — a wider Vicmap re-clip
(`convert_vicmap_tree_points.py`) was attempted but hit an ArcGIS pagination
limit on the larger bbox and wasn't promoted; every edge outside the
original municipality currently scores `canopy_density: 0.0` (not `null`) —
a real, flagged follow-up, not a silent gap. See the release's own
`metadata.json` `coverage_note`.

Edge weight is always the real haversine distance between endpoints, not
either source's own cost/length field (undocumented derivation).

## Promoted artifact (`models/melbourne-metro-v1/`)

- `graph.json` — plain `{node_coords: [[lon, lat], ...], adjacency: [[[neighbor_id, weight_m, canopy_density], ...], ...]}`.
  Deliberately not a `networkx` pickle: the runtime function
  (`api/route-planner.py`, `api/_shared/router.py`) needs zero geospatial
  dependencies, just a hand-rolled Dijkstra over stdlib `heapq`.
- `shade_grid.json` — the promoted canopy-density grid, for point queries
  outside the context of a route (`api/shade.py`).
- `metadata.json` — sha256/byte count (verified by `api/_shared/graph_loader.py`
  before load, same trust contract as the ML models) for both `graph.json`
  and `shade_grid.json`, node/edge counts, bbox, source attribution/license,
  and the coverage caveats above.

Current build: 197,534 nodes, 300,318 edges, 97.38% single-component
connectivity (the remaining ~2.6% is small islands clustered at the bbox
edge — Overpass truncating ways that cross the query boundary — discarded
by `serialize_runtime_graph.py`'s largest-component trim same as it always
has been, not a merge bug), covering inner+middle-ring Melbourne
(`[144.779, -37.956, 145.150, -37.665]`). The predecessor `melbourne-inner-v1`
release (67,974 nodes, City of Melbourne municipality only) remains on disk
as an easy rollback.

`api/_shared/router.py`'s `snap_to_nearest_node` is also grid-bucket indexed
(not a linear scan) as of this release — measured at ~117ms/call over as a
linear scan against ~197k nodes (two calls per route-planner request), down
to ~0.9ms/call indexed. The original 68k-node graph didn't need this; this
one clearly does.

## Attribution

- City of Melbourne Pedestrian Network and Tree Canopies 2021, CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/).
- © OpenStreetMap contributors, ODbL 1.0
  (https://www.openstreetmap.org/copyright).
