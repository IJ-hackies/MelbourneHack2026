#!/usr/bin/env python3
"""Builds a routable pedestrian graph from the City of Melbourne Pedestrian
Network GeoJSON (fetch_sources.py).

Each GeoJSON LineString feature is a footpath segment; consecutive
coordinate pairs within a segment become graph edges. Coordinates are
rounded to ~1cm precision and used as a dict key to identify shared nodes —
this dataset's endpoints coincide exactly at real intersections (standard
practice for maintained network GIS data), so no fuzzy spatial-join/snapping
tolerance search is needed. Edge weight is the real haversine distance
between endpoints in metres, not the dataset's own `COST` field (whose
derivation isn't documented — computing our own weight from real coordinates
keeps the cost semantics we can actually explain).

No osmnx/geopandas/shapely dependency: this file only uses the stdlib, kept
deliberately simple given a single, already-graph-shaped GeoJSON input.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT / "ml" / "routing" / "datasets" / "com_pedestrian_network" / "Pedestrian_network.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "routing" / "processed"

# Round to 7 decimal places (~1.1cm at this latitude) so genuinely distinct
# points never collide, while floating-point noise in shared endpoints does.
COORD_PRECISION = 7


def node_key(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon, COORD_PRECISION), round(lat, COORD_PRECISION))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_graph(features: list[dict[str, Any]]) -> dict[str, Any]:
    node_ids: dict[tuple[float, float], int] = {}
    node_coords: list[tuple[float, float]] = []  # index -> (lon, lat)
    adjacency: list[list[tuple[int, float]]] = []  # node id -> [(neighbor id, weight_m), ...]

    def get_or_create_node(lon: float, lat: float) -> int:
        key = node_key(lon, lat)
        node_id = node_ids.get(key)
        if node_id is None:
            node_id = len(node_coords)
            node_ids[key] = node_id
            node_coords.append((lon, lat))
            adjacency.append([])
        return node_id

    edge_count = 0
    skipped_zero_length = 0
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coords = geometry.get("coordinates") or []
        for i in range(len(coords) - 1):
            lon1, lat1 = coords[i][0], coords[i][1]
            lon2, lat2 = coords[i + 1][0], coords[i + 1][1]
            a = get_or_create_node(lon1, lat1)
            b = get_or_create_node(lon2, lat2)
            if a == b:
                skipped_zero_length += 1
                continue
            weight = haversine_m(lon1, lat1, lon2, lat2)
            # Pedestrians walk both directions.
            adjacency[a].append((b, weight))
            adjacency[b].append((a, weight))
            edge_count += 1

    return {
        "node_coords": node_coords,
        "adjacency": adjacency,
        "edge_count": edge_count,
        "skipped_zero_length": skipped_zero_length,
    }


def largest_component_ratio(adjacency: list[list[tuple[int, float]]]) -> tuple[float, int]:
    """Returns (fraction of nodes in the largest connected component, its size)."""
    n = len(adjacency)
    if n == 0:
        return 0.0, 0
    visited = [False] * n
    largest = 0
    for start in range(n):
        if visited[start]:
            continue
        stack = [start]
        visited[start] = True
        size = 0
        while stack:
            node = stack.pop()
            size += 1
            for neighbor, _ in adjacency[node]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)
        largest = max(largest, size)
    return largest / n, largest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    with args.input.open() as f:
        data = json.load(f)

    graph = build_graph(data["features"])
    ratio, largest_size = largest_component_ratio(graph["adjacency"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "graph_raw.json"
    with out_path.open("w") as f:
        json.dump(
            {
                "node_coords": graph["node_coords"],
                "adjacency": graph["adjacency"],
            },
            f,
        )

    print(f"Nodes: {len(graph['node_coords']):,}")
    print(f"Edges: {graph['edge_count']:,} (skipped {graph['skipped_zero_length']} zero-length)")
    print(f"Largest connected component: {largest_size:,} nodes ({ratio:.4%} of all nodes)")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
