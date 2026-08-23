#!/usr/bin/env python3
"""Converts the Vicmap Vegetation Tree Urban ArcGIS export
(ml/data/raw/vicmap_tree_urban_com_bbox/, fetched via
`python ml/scripts/fetch_datasets.py --dataset vicmap_tree_urban_com_bbox
--bbox <wider bbox>`) into the same {"points": [[lon, lat], ...]}
shape build_shade_grid.py already reads from fetch_tree_canopy.py's
City-of-Melbourne-only centroid export.

Point features are used as-is; Polygon/MultiPolygon features (if the layer
ever returns canopy footprints instead of points) fall back to an average-
vertex centroid — the same "coarse proxy, not precise polygon-area
geometry" positioning fetch_tree_canopy.py already documents, not a new
precision claim.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "ml" / "data" / "raw" / "vicmap_tree_urban_com_bbox" / "tree_urban_com_bbox.geojson"
DEFAULT_OUTPUT = ROOT / "ml" / "routing" / "datasets" / "tree_canopy_points_extended.json"


def _centroid(coords: list) -> tuple[float, float] | None:
    # Exterior ring only (coords[0]) — holes don't matter for a coarse
    # density proxy, and this matches fetch_tree_canopy.py's own
    # "centroid, not precise polygon geometry" scope.
    ring = coords[0] if coords and isinstance(coords[0], list) and coords[0] and isinstance(coords[0][0], list) else coords
    if not ring:
        return None
    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]
    return sum(lons) / len(lons), sum(lats) / len(lats)


def convert(input_path: Path) -> list[list[float]]:
    with input_path.open() as f:
        data = json.load(f)

    points: list[list[float]] = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if not coords:
            continue
        if gtype == "Point":
            points.append([coords[0], coords[1]])
        elif gtype in ("Polygon", "MultiPolygon"):
            rings = coords[0] if gtype == "MultiPolygon" else coords
            centroid = _centroid(rings)
            if centroid:
                points.append([centroid[0], centroid[1]])
    return points


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    points = convert(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump({"points": points}, f)
    print(f"Wrote {args.output} ({len(points):,} canopy points)")
