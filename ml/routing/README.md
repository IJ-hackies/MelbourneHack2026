# Routing: pedestrian graph build pipeline

Builds the real, checksum-verified pedestrian routing graph served by
`api/route-planner.py`. Mirrors `ml/crowd/`'s offline-build → promoted-artifact
pattern.

## Source data

- `com_pedestrian_network` (City of Melbourne Pedestrian Network export, see
  `ml/data/catalog.json`) — a GeoJSON `FeatureCollection` of footpath
  `LineString` segments. This is the **only** input for `melbourne-inner-v1`;
  it already covers the full City of Melbourne municipality bbox, so no
  OSM/Vicmap merge was needed to produce a real, well-connected graph for V1.

An OSM Victoria extract is catalogued for coverage **outside** the
municipality (`osm_victoria_pbf` in `ml/data/catalog.json`) but is not fetched
or merged yet — a documented fast-follow, not implemented here. Queries
outside the current bbox correctly report `quality.status: "unavailable"`
rather than silently guessing.

## Pipeline

```
python ml/routing/scripts/fetch_sources.py       # downloads pedestrian_network.zip
python ml/routing/scripts/build_graph.py         # parses GeoJSON -> node/adjacency graph
python ml/routing/scripts/validate_topology.py   # fails the build if connectivity < 99%
python ml/routing/scripts/fetch_tree_canopy.py   # downloads tree canopy centroid points
python ml/routing/scripts/build_shade_grid.py    # buckets centroids into a density grid
python ml/routing/scripts/serialize_runtime_graph.py  # promotes graph.json + shade_grid.json + metadata.json
```

`fetch_tree_canopy.py`/`build_shade_grid.py` add a real, if approximate,
canopy-density score (`com_tree_canopy_2021`, see `ml/data/catalog.json`) —
a count of tree canopy centroids per ~40m grid cell, normalised 0..1 against
the 95th-percentile cell. `serialize_runtime_graph.py` embeds each edge's
score (average of its endpoints' cells) as a third element on every
adjacency entry, and promotes the grid itself for point queries
(`api/_shared/shade_lookup.py`, used by `api/shade.py`). This is a
tree-density proxy, not a solar-shade calculation — no polygon area, height,
or sun-angle data exists in the source, so it's labelled "canopy density"
everywhere it's surfaced, never "shade %".

`build_graph.py` treats coordinate pairs (rounded to ~1cm) as shared graph
nodes — this dataset's segment endpoints coincide exactly at real
intersections, so no fuzzy spatial-join tolerance search was needed. Edge
weight is the real haversine distance between endpoints, not the dataset's
own `COST` field (undocumented derivation).

## Promoted artifact (`models/melbourne-inner-v1/`)

- `graph.json` — plain `{node_coords: [[lon, lat], ...], adjacency: [[[neighbor_id, weight_m, canopy_density], ...], ...]}`.
  Deliberately not a `networkx` pickle: the runtime function
  (`api/route-planner.py`, `api/_shared/router.py`) needs zero geospatial
  dependencies, just a hand-rolled Dijkstra over stdlib `heapq`.
- `shade_grid.json` — the promoted canopy-density grid, for point queries
  outside the context of a route (`api/shade.py`).
- `metadata.json` — sha256/byte count (verified by `api/_shared/graph_loader.py`
  before load, same trust contract as the ML models) for both `graph.json`
  and `shade_grid.json`, node/edge counts, bbox, source attribution/license.

Current build: 67,974 nodes, 77,650 edges, 99.99% single-component
connectivity, covering the City of Melbourne municipality bbox
(`[144.899, -37.851, 144.992, -37.775]`).

## Attribution

City of Melbourne Pedestrian Network, CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/).
