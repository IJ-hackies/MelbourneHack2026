#!/usr/bin/env python3
"""Builds a routable pedestrian graph from the City of Melbourne Pedestrian
Network GeoJSON (fetch_sources.py), optionally merged with a second, lower-
priority source (fetch_osm_extract.py's inner-metro OSM extract) covering
the surrounding councils the City dataset doesn't reach.

Each GeoJSON LineString feature is a footpath segment; consecutive
coordinate pairs within a segment become graph edges. Within a single
source, coordinates rounded to ~1cm precision and used as a dict key
correctly identify shared nodes (that source's own ways coincide exactly at
real intersections — standard practice for maintained network GIS data, and
true of OSM ways sharing real OSM node IDs too). *Across* two independently-
digitized sources this does NOT hold: confirmed by an actual merge attempt
that the City of Melbourne dataset (dedicated footpath/sidewalk geometry)
and OSM (mostly road-centreline geometry, separately surveyed) essentially
never share literal coordinates even where they trace the same physical
street a few metres apart — an exact-match-only merge produced two fully
disconnected 90%/10% components, not one graph. A proximity-based snap
(SNAP_TOLERANCE_M, grid-bucket indexed so it stays roughly linear over ~1M
nodes) bridges the seam between sources by reusing an existing node instead
of creating a near-duplicate one, without needing full geometric conflation.
Edge weight is the real haversine distance between endpoints in metres, not
the City dataset's own `COST` field (whose derivation isn't documented —
computing our own weight from real coordinates keeps the cost semantics we
can actually explain).

No osmnx/geopandas/shapely dependency: this file only uses the stdlib —
fetch_osm_extract.py already reshapes its Overpass response into the same
GeoJSON LineString shape the City dataset comes in, so both sources are
graph-shaped by the time they reach this file.
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
DEFAULT_EXTRA_INPUT = (
    ROOT / "ml" / "routing" / "datasets" / "osm_inner_metro" / "osm_extract.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "routing" / "processed"

# Round to 7 decimal places (~1.1cm at this latitude) so genuinely distinct
# points never collide, while floating-point noise in shared endpoints does.
COORD_PRECISION = 7

# How close two points from *different* sources need to be to be treated as
# the same real-world intersection/point — see the module docstring for why
# this is needed at all. 20m comfortably covers the offset between a City
# footpath line and the OSM road centreline it runs alongside, without being
# so large it would merge two genuinely distinct nearby intersections.
SNAP_TOLERANCE_M = 20.0
METRES_PER_DEGREE_LAT = 111_320.0
# Only used to size the snap grid's longitude cells in degrees — the ~15km
# bbox this graph covers has negligible latitude-dependent cos() variation,
# so one reference latitude (central Melbourne) is accurate enough for that.
SNAP_GRID_REFERENCE_LAT_DEG = -37.81


def node_key(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon, COORD_PRECISION), round(lat, COORD_PRECISION))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_graph(
    feature_sources: list[list[dict[str, Any]]], snap_tolerance_m: float = SNAP_TOLERANCE_M
) -> dict[str, Any]:
    """Builds one merged graph from one or more ordered feature sources.

    Earlier sources are authoritative. A new point first tries an exact
    coordinate match (cheap, and correct within a single well-digitized
    source); failing that, it snaps onto an existing node from *any* source
    if one is within snap_tolerance_m, via a grid-bucket spatial index sized
    to the tolerance so this stays close to linear over ~1M nodes rather
    than an O(n*m) brute-force distance check. Edge dedup then happens on
    the *resolved* node-id pair (post-snap), not raw coordinates — so a
    geometrically-duplicate edge introduced by two sources tracing the same
    street a few metres apart is correctly caught even though their raw
    coordinates never matched exactly."""
    node_ids: dict[tuple[float, float], int] = {}
    node_coords: list[tuple[float, float]] = []  # index -> (lon, lat)
    adjacency: list[list[tuple[int, float]]] = []  # node id -> [(neighbor id, weight_m), ...]
    seen_edges: set[tuple[int, int]] = set()

    cell_deg_lat = snap_tolerance_m / METRES_PER_DEGREE_LAT
    cell_deg_lon = snap_tolerance_m / (
        METRES_PER_DEGREE_LAT * math.cos(math.radians(SNAP_GRID_REFERENCE_LAT_DEG))
    )
    grid: dict[tuple[int, int], list[int]] = {}

    def grid_cell(lon: float, lat: float) -> tuple[int, int]:
        return (int(lat // cell_deg_lat), int(lon // cell_deg_lon))

    def find_snap_target(lon: float, lat: float) -> int | None:
        row, col = grid_cell(lon, lat)
        best_id: int | None = None
        best_dist = snap_tolerance_m
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for candidate_id in grid.get((row + dr, col + dc), ()):
                    c_lon, c_lat = node_coords[candidate_id]
                    dist = haversine_m(lon, lat, c_lon, c_lat)
                    if dist < best_dist:
                        best_dist = dist
                        best_id = candidate_id
        return best_id

    snapped_count = 0

    def get_or_create_node(lon: float, lat: float) -> int:
        nonlocal snapped_count
        key = node_key(lon, lat)
        node_id = node_ids.get(key)
        if node_id is not None:
            return node_id

        snap_target = find_snap_target(lon, lat)
        if snap_target is not None:
            node_ids[key] = snap_target
            snapped_count += 1
            return snap_target

        node_id = len(node_coords)
        node_ids[key] = node_id
        node_coords.append((lon, lat))
        adjacency.append([])
        grid.setdefault(grid_cell(lon, lat), []).append(node_id)
        return node_id

    edge_count = 0
    skipped_zero_length = 0
    skipped_duplicate = 0
    for features in feature_sources:
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
                edge_key = (a, b) if a < b else (b, a)
                if edge_key in seen_edges:
                    skipped_duplicate += 1
                    continue
                seen_edges.add(edge_key)

                # Computed from the *resolved* node positions, not the raw
                # (lon1, lat1)/(lon2, lat2) this edge's own feature geometry
                # used -- either endpoint may have snapped onto an existing
                # node up to snap_tolerance_m away rather than sitting at its
                # own raw coordinate (see get_or_create_node). Using the raw
                # coordinates here systematically understated a snapped
                # edge's real length by however far its snap moved it, and
                # across the many seam edges a typical route crosses, that
                # compounded into serving a total route distance/time
                # substantially shorter than the real, rendered path -- a
                # live 0.56km/7min figure for what was actually a 1.6km/20min
                # walk was traced back to exactly this.
                a_lon, a_lat = node_coords[a]
                b_lon, b_lat = node_coords[b]
                weight = haversine_m(a_lon, a_lat, b_lon, b_lat)
                # Pedestrians walk both directions.
                adjacency[a].append((b, weight))
                adjacency[b].append((a, weight))
                edge_count += 1

    return {
        "node_coords": node_coords,
        "adjacency": adjacency,
        "edge_count": edge_count,
        "skipped_zero_length": skipped_zero_length,
        "skipped_duplicate": skipped_duplicate,
        "snapped_count": snapped_count,
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
    parser.add_argument(
        "--extra-input", type=Path, default=DEFAULT_EXTRA_INPUT,
        help="Second, lower-priority feature source merged in after --input "
        "(e.g. an OSM extract covering the surrounding area) — pass a "
        "nonexistent path or --no-extra-input to build City-of-Melbourne-only, "
        "matching the original single-source behaviour.",
    )
    parser.add_argument("--no-extra-input", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    with args.input.open() as f:
        primary = json.load(f)["features"]

    feature_sources = [primary]
    if not args.no_extra_input and args.extra_input.exists():
        with args.extra_input.open() as f:
            feature_sources.append(json.load(f)["features"])
        print(f"Merging extra source: {args.extra_input}")
    else:
        print("No extra source merged (City of Melbourne data only).")

    graph = build_graph(feature_sources)
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

    print(f"Nodes: {len(graph['node_coords']):,} ({graph['snapped_count']:,} snapped onto an existing node)")
    print(
        f"Edges: {graph['edge_count']:,} "
        f"(skipped {graph['skipped_zero_length']:,} zero-length, "
        f"{graph['skipped_duplicate']:,} duplicate-at-seam)"
    )
    print(f"Largest connected component: {largest_size:,} nodes ({ratio:.4%} of all nodes)")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
