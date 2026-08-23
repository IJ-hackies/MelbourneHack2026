"""Real pedestrian routing over the promoted City of Melbourne network graph.

POST body: { "origin": {"lat": number, "lon": number},
             "destination": {"lat": number, "lon": number} }
Response:  { "routes": [
               { "id": "fastest" | "shaded",
                 "path": [{"lat", "lon"}, ...] | null,
                 "distance_km": number | null,
                 "minutes": number | null,
                 "canopy_density_avg": number | null,
                 "pedestrian_flow_avg_per_hour": number | null,
                 "tags": [string, ...],
                 "quality": {"status": "ok"|"unavailable", "warnings": [...]} },
               ... ] }

Never fabricates a path, a canopy score, or a crowd figure: any point that
can't be computed from real data is null with a warning, and a candidate
route is only tagged with a superlative ("most shaded"/"least crowded") when
both candidates actually have that real metric AND they differ — callers
(route-provider.ts) are responsible for their own straight-line fallback
when quality.status is "unavailable" for every candidate.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import crowd_model, feature_lookup, graph_loader, router

_load_error: str | None = None

# Sampling a candidate's real path every ~250m and averaging each sample's
# nearest-sensor prediction is the "collection of points on the way" scoring
# approach — real per-point model output, not a fabricated route-level
# figure. Capped so a long route doesn't fire dozens of model calls.
CROWD_SAMPLE_SPACING_M = 250.0
CROWD_SAMPLE_MAX_POINTS = 12


def _ensure_loaded() -> str | None:
    global _load_error
    if _load_error is not None:
        return _load_error
    try:
        graph_loader.load_graph()
    except Exception as exc:  # refuse to serve an unverified/missing graph
        _load_error = str(exc)
    return _load_error


def _sample_points(node_coords: list, node_ids: list[int], distance_m: float) -> list[tuple[float, float]]:
    """Evenly-spaced (lat, lon) samples along the resolved node path,
    including both endpoints, capped at CROWD_SAMPLE_MAX_POINTS."""
    if len(node_ids) <= 2 or distance_m <= 0:
        return [(node_coords[n][1], node_coords[n][0]) for n in (node_ids[0], node_ids[-1])]

    step_count = min(CROWD_SAMPLE_MAX_POINTS - 1, max(1, round(distance_m / CROWD_SAMPLE_SPACING_M)))
    stride = max(1, len(node_ids) // step_count)
    indices = list(range(0, len(node_ids), stride))
    if indices[-1] != len(node_ids) - 1:
        indices.append(len(node_ids) - 1)
    return [(node_coords[node_ids[i]][1], node_coords[node_ids[i]][0]) for i in indices]


def _crowd_avg_for_path(node_coords: list, node_ids: list[int], distance_m: float) -> tuple[float | None, list[str]]:
    warnings: list[str] = []
    samples = _sample_points(node_coords, node_ids, distance_m)
    now = datetime.now(tz=UTC)

    # Nearby samples commonly resolve to the same nearest sensor — dedupe
    # before predicting so a 12-point path doesn't fire 12x the model calls
    # and history fetches for what's really 2-3 distinct sensors.
    sensor_ids: set[str] = set()
    for lat, lon in samples:
        sensor_id, _dist_km, sensor_warnings = feature_lookup.resolve_nearest_crowd_sensor(lat, lon)
        if sensor_id is None:
            if "live_sensor_locations_feed_unreachable" in sensor_warnings:
                warnings.append("crowd_sensor_feed_unreachable")
            continue
        sensor_ids.add(sensor_id)

    readings: list[float] = []
    for sensor_id in sensor_ids:
        result = crowd_model.predict(sensor_id, now)
        if result["prediction"] is not None:
            readings.append(result["prediction"]["pedestrian_flow_per_hour"])

    if not readings:
        return None, warnings
    return sum(readings) / len(readings), warnings


def _build_candidate(
    candidate_id: str,
    node_coords: list,
    adjacency: list,
    start_id: int,
    end_id: int,
    shade_bias: float,
) -> dict:
    result = router.shortest_path(adjacency, start_id, end_id, shade_bias=shade_bias)
    if result is None:
        return {
            "id": candidate_id,
            "path": None,
            "distance_km": None,
            "minutes": None,
            "canopy_density_avg": None,
            "pedestrian_flow_avg_per_hour": None,
            "tags": [],
            "quality": {"status": "unavailable", "warnings": ["no_connected_path"]},
        }

    node_ids, total_m = result
    path = [{"lon": node_coords[n][0], "lat": node_coords[n][1]} for n in node_ids]
    shade_avg = router.path_avg_shade(adjacency, node_ids)
    crowd_avg, crowd_warnings = _crowd_avg_for_path(node_coords, node_ids, total_m)

    return {
        "id": candidate_id,
        "path": path,
        "distance_km": round(total_m / 1000, 3),
        "minutes": round(total_m / router.WALKING_SPEED_M_PER_MIN, 1),
        "canopy_density_avg": round(shade_avg, 3) if shade_avg is not None else None,
        "pedestrian_flow_avg_per_hour": round(crowd_avg, 1) if crowd_avg is not None else None,
        "tags": [],
        "quality": {"status": "ok", "warnings": crowd_warnings},
    }


def _apply_tags(candidates: list[dict]) -> None:
    ok = [c for c in candidates if c["quality"]["status"] == "ok"]
    if len(ok) >= 2:
        by_shade = [c for c in ok if c["canopy_density_avg"] is not None]
        if len(by_shade) >= 2 and len({c["canopy_density_avg"] for c in by_shade}) > 1:
            max(by_shade, key=lambda c: c["canopy_density_avg"])["tags"].append("most_shaded")

        by_crowd = [c for c in ok if c["pedestrian_flow_avg_per_hour"] is not None]
        if len(by_crowd) >= 2 and len({c["pedestrian_flow_avg_per_hour"] for c in by_crowd}) > 1:
            min(by_crowd, key=lambda c: c["pedestrian_flow_avg_per_hour"])["tags"].append("least_crowded")

    fastest = min((c for c in ok), key=lambda c: c["distance_km"], default=None)
    if fastest is not None:
        fastest["tags"].insert(0, "fastest")


def plan_route(origin: dict, destination: dict) -> dict:
    load_error = _ensure_loaded()
    if load_error:
        unavailable = {
            "id": "fastest",
            "path": None,
            "distance_km": None,
            "minutes": None,
            "canopy_density_avg": None,
            "pedestrian_flow_avg_per_hour": None,
            "tags": [],
            "quality": {"status": "unavailable", "warnings": [load_error]},
        }
        return {"routes": [unavailable]}

    graph = graph_loader.load_graph()
    node_coords = graph["node_coords"]
    adjacency = graph["adjacency"]

    start_id, start_snap_m = router.snap_to_nearest_node(node_coords, origin["lon"], origin["lat"])
    end_id, end_snap_m = router.snap_to_nearest_node(node_coords, destination["lon"], destination["lat"])

    if start_id is None or end_id is None:
        warnings = []
        if start_id is None:
            warnings.append("origin_outside_graph_coverage")
        if end_id is None:
            warnings.append("destination_outside_graph_coverage")
        unavailable = {
            "id": "fastest",
            "path": None,
            "distance_km": None,
            "minutes": None,
            "canopy_density_avg": None,
            "pedestrian_flow_avg_per_hour": None,
            "tags": [],
            "quality": {"status": "unavailable", "warnings": warnings},
        }
        return {"routes": [unavailable]}

    snap_warnings = []
    if start_snap_m and start_snap_m > 50:
        snap_warnings.append(f"origin_snap_distance_m_{round(start_snap_m)}")
    if end_snap_m and end_snap_m > 50:
        snap_warnings.append(f"destination_snap_distance_m_{round(end_snap_m)}")

    fastest = _build_candidate("fastest", node_coords, adjacency, start_id, end_id, shade_bias=0.0)
    shaded = _build_candidate("shaded", node_coords, adjacency, start_id, end_id, shade_bias=router.SHADE_BIAS)

    candidates = [fastest]
    # Only surface "shaded" as a distinct option when it's actually a
    # different path — an identical geometry (no shadier alternative
    # exists between these two points) would be a fake-looking duplicate
    # card for zero real differentiation.
    if shaded["quality"]["status"] == "ok" and shaded["path"] != fastest["path"]:
        candidates.append(shaded)

    for c in candidates:
        c["quality"]["warnings"] = [*c["quality"]["warnings"], *snap_warnings]

    _apply_tags(candidates)
    return {"routes": candidates}


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
