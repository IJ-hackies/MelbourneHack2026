"""Contract tests for api/_shared/router.py's Dijkstra/snap logic, run
against a small synthetic graph fixture — no real promoted graph.json or
HTTP handler needed.

Run with: python -m unittest ml.routing.tests.test_router -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "api"))
from _shared import router  # noqa: E402


# A small square "street grid": four corners connected in a loop, plus a
# diagonal shortcut between nodes 0 and 2 that's longer than going via 1.
#   0 --- 1
#   |     |
#   3 --- 2
# Coordinates are arbitrary but real-world-shaped (Melbourne-ish).
SQUARE_NODE_COORDS = [
    (144.9600, -37.8100),  # 0
    (144.9610, -37.8100),  # 1
    (144.9610, -37.8110),  # 2
    (144.9600, -37.8110),  # 3
]


def _edge(coords, a, b):
    return router.haversine_m(*coords[a], *coords[b])


def build_square_adjacency():
    c = SQUARE_NODE_COORDS
    adjacency = [[] for _ in c]

    def connect(a, b):
        w = _edge(c, a, b)
        adjacency[a].append([b, w])
        adjacency[b].append([a, w])

    connect(0, 1)
    connect(1, 2)
    connect(2, 3)
    connect(3, 0)
    return adjacency


class ShortestPathTests(unittest.TestCase):
    def test_direct_neighbor(self):
        adjacency = build_square_adjacency()
        path, total_m = router.shortest_path(adjacency, 0, 1)
        self.assertEqual(path, [0, 1])
        self.assertAlmostEqual(total_m, _edge(SQUARE_NODE_COORDS, 0, 1), places=3)

    def test_picks_shorter_of_two_routes_around_loop(self):
        adjacency = build_square_adjacency()
        path, total_m = router.shortest_path(adjacency, 0, 2)
        # Both 0-1-2 and 0-3-2 are valid; with a square grid they're equal
        # length, so just confirm a valid connected path of the right cost.
        self.assertIn(path, ([0, 1, 2], [0, 3, 2]))
        expected = _edge(SQUARE_NODE_COORDS, 0, 1) + _edge(SQUARE_NODE_COORDS, 1, 2)
        self.assertAlmostEqual(total_m, expected, places=1)

    def test_same_start_and_end(self):
        adjacency = build_square_adjacency()
        path, total_m = router.shortest_path(adjacency, 2, 2)
        self.assertEqual(path, [2])
        self.assertEqual(total_m, 0.0)

    def test_disconnected_returns_none(self):
        adjacency = build_square_adjacency()
        adjacency.append([])  # an isolated 5th node with no edges
        result = router.shortest_path(adjacency, 0, 4)
        self.assertIsNone(result)


class SnapToNearestNodeTests(unittest.TestCase):
    def test_snaps_to_closest_node(self):
        node_id, distance_m = router.snap_to_nearest_node(SQUARE_NODE_COORDS, 144.9601, -37.8101)
        self.assertEqual(node_id, 0)
        self.assertLess(distance_m, 50)

    def test_returns_none_when_too_far(self):
        # Sydney — nowhere near the synthetic Melbourne-ish square.
        node_id, distance_m = router.snap_to_nearest_node(SQUARE_NODE_COORDS, 151.2093, -33.8688)
        self.assertIsNone(node_id)
        self.assertIsNone(distance_m)


class HaversineTests(unittest.TestCase):
    def test_zero_distance_for_identical_points(self):
        self.assertEqual(router.haversine_m(144.96, -37.81, 144.96, -37.81), 0.0)

    def test_known_short_distance_is_reasonable(self):
        # ~111m per 0.001 degree of latitude near Melbourne's latitude.
        d = router.haversine_m(144.96, -37.81, 144.96, -37.809)
        self.assertGreater(d, 90)
        self.assertLess(d, 130)


if __name__ == "__main__":
    unittest.main()
