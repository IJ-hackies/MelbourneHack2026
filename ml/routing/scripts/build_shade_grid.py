#!/usr/bin/env python3
"""Builds a coarse canopy-density grid from tree canopy centroids.

This is a real, data-derived proxy for "how leafy is this area" — a count
of tree canopy centroids per grid cell, normalised to 0..1 — not a precise
solar-shade calculation (that would need canopy polygon area, height, and
sun angle, none of which are in the source dataset). It is deliberately
labelled "canopy density" throughout the app rather than "shade %" for that
reason; see ml/routing/README.md.

Runtime (api/_shared/shade_lookup.py) needs to answer two kinds of query
cheaply: a single point (for the "near destination" condition tile) and an
edge midpoint (for per-edge route weighting, embedded directly into
graph.json by serialize_runtime_graph.py). A grid keyed by (row, col) index
serves both without shipping all 58k raw points to the runtime function.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POINTS = ROOT / "ml" / "routing" / "datasets" / "tree_canopy_points.json"
DEFAULT_GRAPH_METADATA = ROOT / "ml" / "routing" / "models" / "melbourne-inner-v1" / "metadata.json"
DEFAULT_OUTPUT = ROOT / "ml" / "routing" / "processed" / "shade_grid.json"

# ~40m cells: fine enough to distinguish a leafy side street from a bare
# arterial road, coarse enough that a single missing/relocated tree doesn't
# swing a cell's score.
CELL_SIZE_M = 40.0
METRES_PER_DEGREE_LAT = 111_320.0


def build(points_path: Path, bbox: list[float], cell_size_m: float) -> dict:
    with points_path.open() as f:
        points = json.load(f)["points"]

    min_lon, min_lat, max_lon, max_lat = bbox
    # Small buffer so canopy just outside the routing graph's bbox still
    # contributes to edges near the boundary.
    buffer_deg = 0.005
    min_lon, min_lat = min_lon - buffer_deg, min_lat - buffer_deg
    max_lon, max_lat = max_lon + buffer_deg, max_lat + buffer_deg

    mid_lat_rad = math.radians((min_lat + max_lat) / 2)
    metres_per_degree_lon = METRES_PER_DEGREE_LAT * math.cos(mid_lat_rad)
    cell_deg_lat = cell_size_m / METRES_PER_DEGREE_LAT
    cell_deg_lon = cell_size_m / metres_per_degree_lon

    cols = max(1, math.ceil((max_lon - min_lon) / cell_deg_lon))
    rows = max(1, math.ceil((max_lat - min_lat) / cell_deg_lat))

    counts = [[0] * cols for _ in range(rows)]
    for lon, lat in points:
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        col = min(cols - 1, int((lon - min_lon) / cell_deg_lon))
        row = min(rows - 1, int((lat - min_lat) / cell_deg_lat))
        counts[row][col] += 1

    # Normalise by the 95th percentile cell count rather than the max, so a
    # single unusually dense park (e.g. the Botanic Gardens) doesn't flatten
    # every ordinary street's score toward zero.
    flat_counts = sorted(c for row in counts for c in row if c > 0)
    if flat_counts:
        idx = min(len(flat_counts) - 1, int(0.95 * len(flat_counts)))
        p95 = max(1, flat_counts[idx])
    else:
        p95 = 1

    density = [[round(min(1.0, c / p95), 4) for c in row] for row in counts]

    return {
        "cell_size_m": cell_size_m,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "cell_deg_lon": cell_deg_lon,
        "cell_deg_lat": cell_deg_lat,
        "cols": cols,
        "rows": rows,
        "normalization_p95_count": p95,
        "source_point_count": len(points),
        "density": density,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=Path, default=DEFAULT_POINTS)
    parser.add_argument("--graph-metadata", type=Path, default=DEFAULT_GRAPH_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cell-size-m", type=float, default=CELL_SIZE_M)
    args = parser.parse_args()

    with args.graph_metadata.open() as f:
        bbox = json.load(f)["bbox"]

    grid = build(args.points, bbox, args.cell_size_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(grid, f)

    nonzero = sum(1 for row in grid["density"] for v in row if v > 0)
    total = grid["rows"] * grid["cols"]
    print(f"Wrote {args.output} ({grid['rows']}x{grid['cols']} cells, "
          f"{nonzero:,}/{total:,} nonzero, p95_count={grid['normalization_p95_count']})")
