"""Implements crowd-inference/v1 (ml/crowd/SOFTWARE_HANDOFF.md).

POST body: either { "sensor_id": string, "target_hour": ISO-8601 string }
or the software-adapter convenience extension { "lat": number, "lon": number,
"target_hour": ISO-8601 string } — the latter resolves the nearest live
pedestrian-counting sensor via feature_lookup.resolve_nearest_crowd_sensor
before predicting, since callers (e.g. a destination lat/lon from the app)
rarely know a raw sensor_id. This does not change the v1 response contract.

Response: { "prediction": {"pedestrian_flow_per_hour": number} | null,
            "model": {"release", "variant", "sensor_id"},
            "quality": {"status", "feature_coverage", "warnings"} }

Never fabricates a confidence value (the release has none) and never
zero-fills a missing feature — matches the handoff docs' explicit rules. A
resolved-but-distant nearest sensor is surfaced as a quality warning rather
than silently treated as being "at" the destination.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import crowd_model, feature_lookup

RELEASE = crowd_model.RELEASE


def predict(sensor_id: str, target_hour_str: str) -> dict:
    return crowd_model.predict(sensor_id, datetime.fromisoformat(target_hour_str))


def _resolve_sensor_id(body: dict) -> tuple[str | None, dict]:
    """Returns (sensor_id, extra) where `extra` carries a pre-built
    unavailable response when resolution fails, or nearest-sensor warnings
    to merge into a successful prediction."""
    if body.get("sensor_id"):
        return str(body["sensor_id"]), {"warnings": []}

    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None:
        return None, {
            "error_response": {
                "prediction": None,
                "model": {"release": RELEASE, "variant": "all-history", "sensor_id": None},
                "quality": {
                    "status": "unavailable",
                    "feature_coverage": 0.0,
                    "warnings": ["sensor_id_or_lat_lon_required"],
                },
            }
        }

    sensor_id, distance_km, warnings = feature_lookup.resolve_nearest_crowd_sensor(
        float(lat), float(lon)
    )
    if sensor_id is None:
        return None, {
            "error_response": {
                "prediction": None,
                "model": {"release": RELEASE, "variant": "all-history", "sensor_id": None},
                "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": warnings},
            }
        }
    return sensor_id, {"warnings": warnings, "nearest_sensor_distance_km": distance_km}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        target_hour = body.get("target_hour")
        if not target_hour:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "target_hour is required"}).encode())
            return

        sensor_id, resolution = _resolve_sensor_id(body)
        if sensor_id is None:
            result = resolution["error_response"]
        else:
            result = predict(sensor_id, target_hour)
            if result["prediction"] is not None and resolution["warnings"]:
                result["quality"]["warnings"] = [
                    *result["quality"]["warnings"],
                    *resolution["warnings"],
                ]

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
