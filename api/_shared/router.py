"""Pure-Python shortest-path routing over the pedestrian graph.

Deliberately has zero geospatial dependencies (no networkx/osmnx/shapely) —
the graph is already a plain node/adjacency structure
(api/_shared/graph_loader.py), so Dijkstra over stdlib `heapq` is all that's
needed. Kept separate from api/route-planner.py so it's unit-testable
against a small synthetic graph fixture without needing the real ~7MB
promoted artifact or an HTTP handler.
"""

from __future__ import annotations

import heapq
import math


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Beyond this, the query point is likely outside the graph's coverage
# entirely (see metadata.json's coverage_note) — snapping it to the nearest
# node anyway would silently misrepresent an out-of-area query as routable.
MAX_SNAP_DISTANCE_M = 400.0

# Average adult walking speed, used to convert real path length into an
# estimated time — the same order of magnitude as the stub fixtures this
# replaces, but now derived from a real distance instead of being hardcoded.
WALKING_SPEED_M_PER_MIN = 80.0

METRES_PER_DEGREE_LAT = 111_320.0
# Same reference-latitude simplification build_graph.py's snap-merge index
# uses — this graph's ~15km-radius bbox has negligible cos() variation
# across it, so one constant is accurate enough for sizing grid cells.
SNAP_GRID_REFERENCE_LAT_DEG = -37.81

# Keyed by id(node_coords) so a warm serverless instance builds this once
# per graph load (same lifetime as graph_loader.py's own module-level graph
# cache) and reuses it across every request, rather than rebuilding it per
# call.
_snap_index_cache: dict[int, dict[tuple[int, int], list[int]]] = {}


def _build_snap_index(node_coords: list[tuple[float, float]]) -> dict[tuple[int, int], list[int]]:
    cell_deg_lat = MAX_SNAP_DISTANCE_M / METRES_PER_DEGREE_LAT
    cell_deg_lon = MAX_SNAP_DISTANCE_M / (
        METRES_PER_DEGREE_LAT * math.cos(math.radians(SNAP_GRID_REFERENCE_LAT_DEG))
    )
    index: dict[tuple[int, int], list[int]] = {}
    for node_id, (lon, lat) in enumerate(node_coords):
        cell = (int(lat // cell_deg_lat), int(lon // cell_deg_lon))
        index.setdefault(cell, []).append(node_id)
    return index


def snap_to_nearest_node(
    node_coords: list[tuple[float, float]], lon: float, lat: float
) -> tuple[int | None, float | None]:
    """Returns (node_id, distance_m) for the nearest node, or (None, None) if
    every node is farther than MAX_SNAP_DISTANCE_M.

    Grid-bucket indexed (cell size == MAX_SNAP_DISTANCE_M, so a 3x3
    neighbourhood always covers every node within tolerance of the query
    point) rather than a linear scan — measured at ~117ms/call over this
    graph's ~197k nodes, which real-request route-planner.py calls twice
    (origin + destination) per request; a 68k-node graph didn't need this,
    a ~200k-node one clearly does. Falls back to a full scan only if the
    index has genuinely no candidate nearby, which happens only right at
    MAX_SNAP_DISTANCE_M's edge or for a query truly outside coverage."""
    cell_deg_lat = MAX_SNAP_DISTANCE_M / METRES_PER_DEGREE_LAT
    cell_deg_lon = MAX_SNAP_DISTANCE_M / (
        METRES_PER_DEGREE_LAT * math.cos(math.radians(SNAP_GRID_REFERENCE_LAT_DEG))
    )
    index = _snap_index_cache.get(id(node_coords))
    if index is None:
        index = _build_snap_index(node_coords)
        _snap_index_cache[id(node_coords)] = index

    row, col = int(lat // cell_deg_lat), int(lon // cell_deg_lon)
    best_id: int | None = None
    best_dist: float | None = None
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            for node_id in index.get((row + dr, col + dc), ()):
                node_lon, node_lat = node_coords[node_id]
                dist = haversine_m(lon, lat, node_lon, node_lat)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_id = node_id

    if best_dist is None or best_dist > MAX_SNAP_DISTANCE_M:
        return None, None
    return best_id, best_dist


# Each adjacency edge is [neighbor_id, weight_m, canopy_density_0_1]. A
# shade-biased search stays real routing over real distances — it never
# invents a shorter path — it just prefers well-shaded edges when two are
# otherwise close in length, via a multiplicative penalty on low-canopy
# edges. At SHADE_BIAS=0 this degenerates to plain shortest_path.
SHADE_BIAS = 1.4


def shortest_path(
    adjacency: list[list[list[float]]],
    start: int,
    end: int,
    shade_bias: float = 0.0,
    node_penalty: dict[int, float] | None = None,
    penalty_weight: float = 0.0,
) -> tuple[list[int], float] | None:
    """Dijkstra over the adjacency list. Returns (node id path, total metres
    of real walking distance — NOT the shade-weighted/penalised cost used
    internally when shade_bias/penalty_weight > 0), or None if start/end
    aren't connected.

    node_penalty is an optional real, per-query cost (e.g. nearby live
    crowd flow) added for arriving at a given node — see
    api/route-planner.py's crowd-aware "quieter" candidate. Absent from a
    node means zero penalty, not an unknown value."""
    if start == end:
        return [start], 0.0

    # cost is what the priority queue optimises; distance tracks the real
    # metres walked so the reported distance/minutes are never inflated by
    # the shade bias or crowd penalty.
    costs = {start: 0.0}
    distances = {start: 0.0}
    previous: dict[int, int] = {}
    visited: set[int] = set()
    queue: list[tuple[float, int]] = [(0.0, start)]

    while queue:
        cost, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == end:
            break
        for edge in adjacency[node]:
            neighbor = int(edge[0])
            weight = edge[1]
            shade = edge[2] if len(edge) > 2 else 0.0
            edge_cost = weight * (1.0 + shade_bias * (1.0 - shade)) if shade_bias else weight
            if node_penalty and penalty_weight:
                edge_cost += penalty_weight * node_penalty.get(neighbor, 0.0)
            new_cost = cost + edge_cost
            if new_cost < costs.get(neighbor, math.inf):
                costs[neighbor] = new_cost
                distances[neighbor] = distances[node] + weight
                previous[neighbor] = node
                heapq.heappush(queue, (new_cost, neighbor))

    if end not in distances:
        return None

    path = [end]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return path, distances[end]


def path_avg_shade(adjacency: list[list[list[float]]], node_ids: list[int]) -> float | None:
    """Length-weighted average canopy density along a resolved node path.
    None if the graph predates edge shade data (2-element adjacency tuples)."""
    total_weight = 0.0
    total_shade_weight = 0.0
    for a, b in zip(node_ids, node_ids[1:]):
        edge = next((e for e in adjacency[a] if int(e[0]) == b), None)
        if edge is None or len(edge) <= 2:
            return None
        weight = edge[1]
        total_weight += weight
        total_shade_weight += weight * edge[2]
    if total_weight == 0:
        return None
    return total_shade_weight / total_weight
