#!/usr/bin/env python3
"""Promotes a validated graph_raw.json into a versioned runtime artifact.

Trims to only the largest connected component (unreachable nodes can never
be a valid route endpoint or waypoint, so shipping them just wastes bundle
size) and writes metadata.json with a SHA-256/byte-count checksum, following
the same trust contract as the promoted ML models
(ml/crowd/models/all-history-v1/metadata.json): api/_shared/graph_loader.py
verifies this before loading at runtime and refuses to load on mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "ml" / "routing" / "processed" / "graph_raw.json"
DEFAULT_SHADE_GRID = ROOT / "ml" / "routing" / "processed" / "shade_grid.json"
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "routing" / "models" / "melbourne-inner-v1"
RELEASE = "melbourne-inner-v1"
SOURCE_DATASET_ID = "com_pedestrian_network"
SOURCE_LICENSE = "CC BY 4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_ATTRIBUTION = "City of Melbourne — Pedestrian Network"


def largest_component_node_ids(adjacency: list[list[tuple[int, float]]]) -> set[int]:
    n = len(adjacency)
    visited = [False] * n
    best: set[int] = set()
    for start in range(n):
        if visited[start]:
            continue
        stack = [start]
        visited[start] = True
        component = {start}
        while stack:
            node = stack.pop()
            for neighbor, _ in adjacency[node]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    component.add(neighbor)
                    stack.append(neighbor)
        if len(component) > len(best):
            best = component
    return best


def cell_density(grid: dict, lon: float, lat: float) -> float:
    col = int((lon - grid["min_lon"]) / grid["cell_deg_lon"])
    row = int((lat - grid["min_lat"]) / grid["cell_deg_lat"])
    if not (0 <= row < grid["rows"] and 0 <= col < grid["cols"]):
        return 0.0
    return grid["density"][row][col]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--shade-grid", type=Path, default=DEFAULT_SHADE_GRID)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    with args.input.open() as f:
        graph = json.load(f)
    with args.shade_grid.open() as f:
        shade_grid = json.load(f)

    node_coords: list[tuple[float, float]] = graph["node_coords"]
    adjacency: list[list[tuple[int, float]]] = graph["adjacency"]

    keep_ids = largest_component_node_ids(adjacency)
    remap = {old_id: new_id for new_id, old_id in enumerate(sorted(keep_ids))}

    new_node_coords = [node_coords[old_id] for old_id in sorted(keep_ids)]

    # Each edge's canopy-density score is the average of its two endpoint
    # cells (cheap, and endpoints already share the coordinate lookups the
    # edge needs anyway) rather than a separate midpoint sample.
    node_density = [cell_density(shade_grid, lon, lat) for lon, lat in new_node_coords]

    new_adjacency: list[list[list[float]]] = [[] for _ in new_node_coords]
    for old_id in sorted(keep_ids):
        new_id = remap[old_id]
        for neighbor, weight in adjacency[old_id]:
            if neighbor in remap:
                new_neighbor = remap[neighbor]
                edge_shade = round((node_density[new_id] + node_density[new_neighbor]) / 2, 4)
                new_adjacency[new_id].append([new_neighbor, weight, edge_shade])

    lons = [c[0] for c in new_node_coords]
    lats = [c[1] for c in new_node_coords]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    graph_path = args.output_dir / "graph.json"
    with graph_path.open("w") as f:
        json.dump({"node_coords": new_node_coords, "adjacency": new_adjacency}, f)

    graph_bytes = graph_path.read_bytes()
    graph_sha256 = hashlib.sha256(graph_bytes).hexdigest()

    shade_grid_path = args.output_dir / "shade_grid.json"
    with shade_grid_path.open("w") as f:
        json.dump(shade_grid, f)
    shade_grid_bytes = shade_grid_path.read_bytes()
    shade_grid_sha256 = hashlib.sha256(shade_grid_bytes).hexdigest()

    metadata = {
        "schema_version": 2,
        "release": RELEASE,
        "graph_file": "graph.json",
        "graph_bytes": len(graph_bytes),
        "graph_sha256": graph_sha256,
        # adjacency entries are [neighbor_id, weight_m, canopy_density_0_1] —
        # a real, if approximate, canopy-density proxy (see
        # scripts/build_shade_grid.py), not a precise solar-shade figure.
        "edge_shade_field": "canopy_density",
        "shade_grid_file": "shade_grid.json",
        "shade_grid_bytes": len(shade_grid_bytes),
        "shade_grid_sha256": shade_grid_sha256,
        "shade_source_dataset_id": "com_tree_canopy_2021",
        "node_count": len(new_node_coords),
        "edge_count": sum(len(edges) for edges in new_adjacency) // 2,
        "bbox": bbox,
        "source_dataset_id": SOURCE_DATASET_ID,
        "source_license": SOURCE_LICENSE,
        "source_license_url": SOURCE_LICENSE_URL,
        "source_attribution": SOURCE_ATTRIBUTION,
        "coverage_note": (
            "City of Melbourne municipality only, per ml/data/catalog.json's "
            "com_pedestrian_network notes ('Primary hackathon graph; static "
            "and limited to the municipality'). Queries outside this bbox "
            "will fail to snap to a graph node — a wider OSM-based merge "
            "remains a documented fast-follow, not implemented here."
        ),
        "built_at_utc": datetime.now(UTC).isoformat(),
    }
    metadata_path = args.output_dir / "metadata.json"
    with metadata_path.open("w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote {graph_path} ({len(graph_bytes):,} bytes, sha256={graph_sha256[:12]}...)")
    print(f"Wrote {metadata_path}")
    print(f"Nodes: {metadata['node_count']:,}, edges: {metadata['edge_count']:,}")
    print(f"Bbox: {bbox}")


if __name__ == "__main__":
    main()
