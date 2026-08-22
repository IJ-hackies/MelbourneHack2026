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


def snap_to_nearest_node(
    node_coords: list[tuple[float, float]], lon: float, lat: float
) -> tuple[int | None, float | None]:
    """Returns (node_id, distance_m) for the nearest node, or (None, None) if
    every node is farther than MAX_SNAP_DISTANCE_M. Plain linear scan — the
    graph is bbox-limited to ~68k nodes, cheap enough per-request without a
    spatial index; revisit if the graph's coverage area grows substantially."""
    best_id: int | None = None
    best_dist: float | None = None
    for node_id, (node_lon, node_lat) in enumerate(node_coords):
        dist = haversine_m(lon, lat, node_lon, node_lat)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = node_id
    if best_dist is None or best_dist > MAX_SNAP_DISTANCE_M:
        return None, None
    return best_id, best_dist


def shortest_path(
    adjacency: list[list[list[float]]], start: int, end: int
) -> tuple[list[int], float] | None:
    """Dijkstra over the adjacency list. Returns (node id path, total metres),
    or None if start/end aren't connected."""
    if start == end:
        return [start], 0.0

    distances = {start: 0.0}
    previous: dict[int, int] = {}
    visited: set[int] = set()
    queue: list[tuple[float, int]] = [(0.0, start)]

    while queue:
        dist, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == end:
            break
        for neighbor, weight in adjacency[node]:
            neighbor = int(neighbor)
            new_dist = dist + weight
            if new_dist < distances.get(neighbor, math.inf):
                distances[neighbor] = new_dist
                previous[neighbor] = node
                heapq.heappush(queue, (new_dist, neighbor))

    if end not in distances:
        return None

    path = [end]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return path, distances[end]
