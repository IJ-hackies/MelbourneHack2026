"""Real pedestrian routing over the promoted City of Melbourne network graph.

POST body: { "origin": {"lat": number, "lon": number},
             "destination": {"lat": number, "lon": number} }
Response:  { "path": [{"lat", "lon"}, ...] | null,
             "distance_km": number | null,
             "minutes": number | null,
             "quality": {"status": "ok"|"unavailable", "warnings": [...]} }

Never fabricates a path: if either point falls outside the graph's coverage
(see ml/routing/models/melbourne-inner-v1/metadata.json's coverage_note) or
the two points aren't connected, this returns quality.status="unavailable"
with path=null rather than a straight line or an invented distance — callers
(route-provider.ts) are responsible for their own straight-line fallback.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import graph_loader, router

_load_error: str | None = None


def _ensure_loaded() -> str | None:
    global _load_error
    if _load_error is not None:
        return _load_error
    try:
        graph_loader.load_graph()
    except Exception as exc:  # refuse to serve an unverified/missing graph
        _load_error = str(exc)
    return _load_error


def plan_route(origin: dict, destination: dict) -> dict:
    load_error = _ensure_loaded()
    if load_error:
        return {
            "path": None,
            "distance_km": None,
            "minutes": None,
            "quality": {"status": "unavailable", "warnings": [load_error]},
        }

    graph = graph_loader.load_graph()
    node_coords = graph["node_coords"]
    adjacency = graph["adjacency"]

    start_id, start_snap_m = router.snap_to_nearest_node(
        node_coords, origin["lon"], origin["lat"]
    )
    end_id, end_snap_m = router.snap_to_nearest_node(
        node_coords, destination["lon"], destination["lat"]
    )

    if start_id is None or end_id is None:
        warnings = []
        if start_id is None:
            warnings.append("origin_outside_graph_coverage")
        if end_id is None:
            warnings.append("destination_outside_graph_coverage")
        return {
            "path": None,
            "distance_km": None,
            "minutes": None,
            "quality": {"status": "unavailable", "warnings": warnings},
        }

    result = router.shortest_path(adjacency, start_id, end_id)
    if result is None:
        return {
            "path": None,
            "distance_km": None,
            "minutes": None,
            "quality": {"status": "unavailable", "warnings": ["no_connected_path"]},
        }

    node_ids, total_m = result
    path = [{"lon": node_coords[n][0], "lat": node_coords[n][1]} for n in node_ids]

    warnings = []
    # Snap distance is how far the requested point was from the nearest
    # graph node — large values mean the real route starts/ends further
    # from the actual coordinates than the map will visually suggest.
    if start_snap_m and start_snap_m > 50:
        warnings.append(f"origin_snap_distance_m_{round(start_snap_m)}")
    if end_snap_m and end_snap_m > 50:
        warnings.append(f"destination_snap_distance_m_{round(end_snap_m)}")

    return {
        "path": path,
        "distance_km": round(total_m / 1000, 3),
        "minutes": round(total_m / router.WALKING_SPEED_M_PER_MIN, 1),
        "quality": {"status": "ok", "warnings": warnings},
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        origin = body.get("origin")
        destination = body.get("destination")
        if not origin or not destination:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": "origin and destination are required"}).encode()
            )
            return

        result = plan_route(origin, destination)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
