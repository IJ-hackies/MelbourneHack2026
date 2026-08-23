"""Real pedestrian routing over the promoted City of Melbourne network graph.

POST body: { "origin": {"lat": number, "lon": number},
             "destination": {"lat": number, "lon": number} }
Response:  { "routes": [
               { "id": "fastest" | "shaded" | "quieter",
                 "path": [{"lat", "lon"}, ...] | null,
                 "distance_km": number | null,
                 "minutes": number | null,
                 "canopy_density_avg": number | null,
                 "pedestrian_flow_avg_per_hour": number | null,
                 "tags": [string, ...],
                 "quality": {"status": "ok"|"unavailable", "warnings": [...]} },
               ... ],
             "heat_context": { "temperature_c": number|null, "advisory": bool,
                                "extreme": bool, "shade_bias_multiplier": number } | null }

heat_context is the real current temperature at the Melbourne CBD point
(same source as /api/weather) and how much harder "shaded" routing is
biasing toward canopy right now — the app's actual heat-adaptation
behaviour, not just a UI label. null only when the load itself failed
before any weather lookup happened.

Never fabricates a path, a canopy score, or a crowd figure: any point that
can't be computed from real data is null with a warning, and a candidate
route is only tagged with a superlative ("most shaded"/"least crowded") when
both candidates actually have that real metric AND they differ — callers
(route-provider.ts) are responsible for their own straight-line fallback
when quality.status is "unavailable" for every candidate.
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import crowd_model, feature_lookup, graph_loader, rate_limit, router

_load_error: str | None = None

# Sampling a candidate's real path every ~250m and averaging each sample's
# nearest-sensor prediction is the "collection of points on the way" scoring
# approach — real per-point model output, not a fabricated route-level
# figure. Capped so a long route doesn't fire dozens of model calls.
CROWD_SAMPLE_SPACING_M = 250.0
CROWD_SAMPLE_MAX_POINTS = 12

# A shaded/quieter candidate needs its headline metric (the entire reason it
# gets its own card) to move by at least this much versus every
# already-accepted candidate, regardless of how much extra distance/time it
# costs — see _meaningfully_different's docstring for why gating on distance
# too let through routes that cost real minutes for ~0% actual benefit.
MIN_SHADE_DIFFERENCE = 0.04
MIN_CROWD_DIFFERENCE_FRACTION = 0.08

# Real-time crowd-aware routing: only sensors within this padded corridor
# around the straight-line origin-destination bbox are considered, and only
# graph nodes within CROWD_INFLUENCE_RADIUS_M of one of those sensors get a
# nonzero penalty — bounds the cost to a query-local subset of the ~68k-node
# graph rather than scoring the whole city on every request.
CROWD_QUERY_PADDING_M = 500.0
CROWD_INFLUENCE_RADIUS_M = 180.0
CROWD_PENALTY_WEIGHT = 0.6
MAX_CROWD_SENSORS_CONSIDERED = 12

# Heat-adaptive shade routing: on a hot day, "shaded" should mean it more
# strongly, not use the same fixed bias as a mild afternoon — this is the
# app's actual climate-adaptation behaviour (routing you to cope with heat
# that's already happening), not just marketing copy. Thresholds follow the
# same bands as the plan page's "Feels hot" condition label
# (condition-provider.ts's feelsLabel) so the advisory and the routing
# behaviour agree with each other.
HEAT_ADVISORY_TEMP_C = 28.0
HEAT_EXTREME_TEMP_C = 33.0


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


def _predict_crowd_cached(cache: dict[str, dict], sensor_id: str, now: datetime) -> dict:
    """crowd_model.predict, memoized per plan_route() call. fastest/shaded/
    quieter candidates (each calling _crowd_avg_for_path) and the "quieter"
    crowd-penalty corridor lookup commonly resolve to overlapping sensors
    for the same request — without this, each one independently re-fetched
    that sensor's history and re-ran the model, repeating real network I/O
    and the feature-scan work for a result that can't have changed within
    the same request."""
    if sensor_id not in cache:
        cache[sensor_id] = crowd_model.predict(sensor_id, now)
    return cache[sensor_id]


def _crowd_avg_for_path(
    node_coords: list, node_ids: list[int], distance_m: float, now: datetime, crowd_cache: dict[str, dict]
) -> tuple[float | None, list[str]]:
    warnings: list[str] = []
    samples = _sample_points(node_coords, node_ids, distance_m)

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
        result = _predict_crowd_cached(crowd_cache, sensor_id, now)
        if result["prediction"] is not None:
            readings.append(result["prediction"]["pedestrian_flow_per_hour"])

    if not readings:
        return None, warnings
    return sum(readings) / len(readings), warnings


def _build_crowd_penalty(
    node_coords: list, origin: dict, destination: dict, now: datetime, crowd_cache: dict[str, dict]
) -> dict[int, float] | None:
    """Real, live per-node crowd penalty for a "quieter" routing bias:
    finds live pedestrian sensors near the origin-destination corridor,
    predicts each one's current flow, and penalises graph nodes within
    CROWD_INFLUENCE_RADIUS_M of a sensor by that real predicted value.
    Returns None (never a fabricated empty-but-nonzero penalty) when no
    sensors are found nearby or none produce a usable prediction."""
    min_lat = min(origin["lat"], destination["lat"])
    max_lat = max(origin["lat"], destination["lat"])
    min_lon = min(origin["lon"], destination["lon"])
    max_lon = max(origin["lon"], destination["lon"])
    mid_lat = (min_lat + max_lat) / 2
    pad_deg_lat = CROWD_QUERY_PADDING_M / 111_320
    pad_deg_lon = pad_deg_lat / max(0.2, math.cos(math.radians(mid_lat)))
    min_lat, max_lat = min_lat - pad_deg_lat, max_lat + pad_deg_lat
    min_lon, max_lon = min_lon - pad_deg_lon, max_lon + pad_deg_lon

    sensors = feature_lookup.list_sensor_locations()
    corridor_sensors = []
    for s in sensors:
        lat, lon, location_id = s.get("latitude"), s.get("longitude"), s.get("location_id")
        if lat is None or lon is None or location_id is None:
            continue
        lat, lon = float(lat), float(lon)
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            corridor_sensors.append((str(location_id), lat, lon))
    if not corridor_sensors:
        return None

    mid_lon = (min_lon + max_lon) / 2
    corridor_sensors.sort(key=lambda s: router.haversine_m(mid_lon, mid_lat, s[2], s[1]))
    corridor_sensors = corridor_sensors[:MAX_CROWD_SENSORS_CONSIDERED]

    sensor_flows: list[tuple[float, float, float]] = []  # (lat, lon, flow)
    for sensor_id, lat, lon in corridor_sensors:
        result = _predict_crowd_cached(crowd_cache, sensor_id, now)
        if result["prediction"] is not None:
            sensor_flows.append((lat, lon, result["prediction"]["pedestrian_flow_per_hour"]))
    if not sensor_flows:
        return None

    penalty: dict[int, float] = {}
    for node_id, (lon, lat) in enumerate(node_coords):
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        nearest_flow = None
        nearest_dist = CROWD_INFLUENCE_RADIUS_M
        for s_lat, s_lon, flow in sensor_flows:
            dist = router.haversine_m(lon, lat, s_lon, s_lat)
            if dist <= nearest_dist:
                nearest_dist = dist
                nearest_flow = flow
        if nearest_flow is not None:
            penalty[node_id] = nearest_flow
    return penalty or None


def _build_candidate(
    candidate_id: str,
    node_coords: list,
    adjacency: list,
    start_id: int,
    end_id: int,
    now: datetime,
    crowd_cache: dict[str, dict],
    shade_bias: float = 0.0,
    node_penalty: dict[int, float] | None = None,
    penalty_weight: float = 0.0,
) -> dict:
    result = router.shortest_path(
        adjacency, start_id, end_id,
        shade_bias=shade_bias, node_penalty=node_penalty, penalty_weight=penalty_weight,
    )
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
    crowd_avg, crowd_warnings = _crowd_avg_for_path(node_coords, node_ids, total_m, now, crowd_cache)

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

    # Gates on minutes, not distance — currently always equivalent (minutes
    # is distance / a fixed walking speed) but "fastest" should track time
    # directly so it can't silently mislabel a route if per-node pace,
    # elevation, or traffic ever makes the two diverge.
    fastest = min((c for c in ok), key=lambda c: c["minutes"], default=None)
    if fastest is not None:
        fastest["tags"].insert(0, "fastest")


def _meaningfully_different(candidate: dict, accepted: list[dict], metric_key: str, relative_metric: bool) -> bool:
    """A candidate only earns its own card if it delivers a real, noticeably
    better result on the metric that's the entire point of that candidate
    type — "quieter" must actually be quieter, "shaded" must actually be
    shadier — regardless of how much extra distance/time it costs to get
    there. Gating on distance instead (or as an alternative pass condition)
    let a route through purely for costing meaningfully more time even when
    its crowd/shade improvement rounded to ~0%, which is a real trade-off no
    user would choose and not what the card claims to offer."""
    if candidate["quality"]["status"] != "ok":
        return False
    min_metric_diff = MIN_CROWD_DIFFERENCE_FRACTION if relative_metric else MIN_SHADE_DIFFERENCE
    for other in accepted:
        metric_a, metric_b = candidate.get(metric_key), other.get(metric_key)
        if metric_a is None or metric_b is None:
            return False
        if relative_metric:
            metric_diff = abs(metric_a - metric_b) / max(abs(metric_b), 0.01)
        else:
            metric_diff = abs(metric_a - metric_b)
        if metric_diff < min_metric_diff:
            return False
    return True


def _heat_context() -> dict:
    """Real current temperature (Melbourne CBD point, same source as
    /api/weather) and the shade-bias multiplier it implies. Never fabricates
    a temperature — advisory/extreme are simply False when the live weather
    feed is unavailable, and shade routing falls back to the flat baseline
    bias rather than guessing."""
    temp_c = feature_lookup.current_temperature_c(datetime.now(tz=UTC))
    if temp_c is None:
        return {"temperature_c": None, "advisory": False, "extreme": False, "shade_bias_multiplier": 1.0}
    if temp_c >= HEAT_EXTREME_TEMP_C:
        return {"temperature_c": temp_c, "advisory": True, "extreme": True, "shade_bias_multiplier": 2.2}
    if temp_c >= HEAT_ADVISORY_TEMP_C:
        return {"temperature_c": temp_c, "advisory": True, "extreme": False, "shade_bias_multiplier": 1.6}
    return {"temperature_c": temp_c, "advisory": False, "extreme": False, "shade_bias_multiplier": 1.0}


def plan_route(origin: dict, destination: dict) -> dict:
    """Always generates the same set of candidates for a given origin/
    destination/time — which of fastest/shaded/quieter exist, and how
    different they are, depends only on the real graph, live heat, and live
    crowd data, never on saved preferences. Preferences instead decide which
    of these already-generated candidates gets recommended to a signed-in
    user (see route-provider.ts's chooseRecommendedId) — a preference set to
    a maximum or minimum must never change how many real route options a
    search returns, only which one is suggested first."""
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
        return {"routes": [unavailable], "heat_context": None}

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
        return {"routes": [unavailable], "heat_context": None}

    snap_warnings = []
    if start_snap_m and start_snap_m > 50:
        snap_warnings.append(f"origin_snap_distance_m_{round(start_snap_m)}")
    if end_snap_m and end_snap_m > 50:
        snap_warnings.append(f"destination_snap_distance_m_{round(end_snap_m)}")

    heat_context = _heat_context()
    # One shared timestamp and one shared per-sensor prediction cache for the
    # whole request — fastest/shaded/quieter and the crowd-penalty corridor
    # lookup all query the same live sensors around the same origin/
    # destination, so without this each one redundantly re-fetched history
    # and re-ran the model for sensors the others had already resolved.
    now = datetime.now(tz=UTC)
    crowd_cache: dict[str, dict] = {}

    fastest = _build_candidate("fastest", node_coords, adjacency, start_id, end_id, now, crowd_cache)
    candidates = [fastest]

    shaded = _build_candidate(
        "shaded", node_coords, adjacency, start_id, end_id, now, crowd_cache,
        shade_bias=router.SHADE_BIAS * heat_context["shade_bias_multiplier"],
    )
    # Only surface "shaded" as a distinct option when it's a real, noticeably
    # different trade-off — a path that's technically different by a few
    # metres but rounds to the same displayed time/percentage is a
    # fake-looking duplicate card for zero real difference to the user.
    if _meaningfully_different(shaded, candidates, "canopy_density_avg", relative_metric=False):
        candidates.append(shaded)

    crowd_penalty = _build_crowd_penalty(node_coords, origin, destination, now, crowd_cache)
    if crowd_penalty:
        quieter = _build_candidate(
            "quieter", node_coords, adjacency, start_id, end_id, now, crowd_cache,
            node_penalty=crowd_penalty, penalty_weight=CROWD_PENALTY_WEIGHT,
        )
        if _meaningfully_different(quieter, candidates, "pedestrian_flow_avg_per_hour", relative_metric=True):
            candidates.append(quieter)

    for c in candidates:
        c["quality"]["warnings"] = [*c["quality"]["warnings"], *snap_warnings]

    _apply_tags(candidates)
    return {"routes": candidates, "heat_context": heat_context}


def _bad_request(handler: "handler", message: str) -> None:
    handler.send_response(400)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps({"error": message}).encode())


def _coords(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    lat, lon = value.get("lat"), value.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    return {"lat": float(lat), "lon": float(lon)}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        start = time.monotonic()

        if not rate_limit.check(self.headers):
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "60")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "too many requests"}).encode())
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw_body or b"{}")
        except (ValueError, json.JSONDecodeError):
            _bad_request(self, "request body must be valid JSON")
            return
        if not isinstance(body, dict):
            _bad_request(self, "request body must be a JSON object")
            return

        origin = _coords(body.get("origin"))
        destination = _coords(body.get("destination"))
        if origin is None or destination is None:
            _bad_request(self, "origin and destination each require numeric lat/lon")
            return

        try:
            result = plan_route(origin, destination)
        except Exception as exc:
            # Logged server-side (Vercel function logs) for real debugging,
            # never returned to the client -- str(exc) can carry internal
            # file paths/library details even though it isn't a full
            # traceback, which contradicts the point of catching this at all.
            print(f"route-planner: unhandled exception in plan_route: {exc!r}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "route planning failed"}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
        route_count = len(result.get("routes", []))
        print(json.dumps({
            "endpoint": "route-planner",
            "status": 200,
            "route_count": route_count,
            "duration_ms": round((time.monotonic() - start) * 1000, 1),
        }))
