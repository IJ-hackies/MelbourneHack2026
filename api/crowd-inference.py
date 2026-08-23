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

from _shared import crowd_model, feature_lookup, rate_limit

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


def _bad_request(handler: "handler", message: str) -> None:
    handler.send_response(400)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps({"error": message}).encode())


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
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

        target_hour = body.get("target_hour")
        if not target_hour:
            _bad_request(self, "target_hour is required")
            return

        lat, lon = body.get("lat"), body.get("lon")
        if (lat is not None and not isinstance(lat, (int, float))) or (
            lon is not None and not isinstance(lon, (int, float))
        ):
            _bad_request(self, "lat and lon must be numbers")
            return

        try:
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
        except ValueError:
            _bad_request(self, "target_hour must be a valid ISO-8601 timestamp")
            return
        except Exception as exc:  # never leak a raw traceback to the client
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "prediction failed", "detail": str(exc)}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
