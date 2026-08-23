"""Implements traffic-inference/v1 (ml/traffic/SOFTWARE_HANDOFF.md).

POST body: { "source_group": "scats"|"intersection"|"transport_activity"|"countline",
             "observation_unit_id": string, "target_hour": ISO-8601 string }
Response:  { "prediction": {"vehicle_count_per_hour": number} | null,
             "model": {"release", "variant", "source_group"},
             "quality": {"status", "feature_coverage", "warnings"} }

Routes strictly by source_group to the matching model — SCATS and Transport
Activity are never pooled, per the handoff docs. SCATS timestamps are fixed
AEST as-is; Transport Activity's trailing-`Z` timestamps are Melbourne
wall-clock, never reinterpreted as UTC (no conversion happens here on
purpose — do not "fix" this later).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import feature_lookup, model_loader, rate_limit

RELEASE = "source-stratified-v1"

SOURCE_GROUP_TO_VARIANT = {
    "scats": "scats-intersection",
    "intersection": "scats-intersection",
    "transport_activity": "transport-activity-countline",
    "countline": "transport-activity-countline",
}

# Transport Activity's v1 release predates the corrected feature allow-list:
# about 28% of its feature gain is leakage-affected, per
# ml/traffic/SOFTWARE_HANDOFF.md. Always surface this — never silently drop it.
TRANSPORT_ACTIVITY_WARNING = "transport_activity_v1_leakage_affected_feature_gain"

_checksums_cache: dict | None = None
_load_errors: dict[str, str] = {}


def _load_checksums() -> dict:
    global _checksums_cache
    if _checksums_cache is None:
        _checksums_cache = model_loader.load_metadata(
            "ml/traffic/models/source-stratified-v1/checksums.json"
        )
    return _checksums_cache


def _artifact_checksum(variant: str, filename: str) -> dict:
    checksums = _load_checksums()
    relative_path = f"{variant}/{filename}"
    for artifact in checksums["artifacts"]:
        if artifact["relative_path"] == relative_path:
            return artifact
    raise RuntimeError(f"no checksum entry for {relative_path}")


def _ensure_model_loaded(variant: str) -> str | None:
    """Verifies the checksum and loads the model.ubj for `variant`, even
    though prediction is currently always skipped (no live traffic feed) —
    this proves the checksum-verification path works now, so only
    feature_lookup needs to change once a live source exists."""
    if variant in _load_errors:
        return _load_errors[variant]
    try:
        artifact = _artifact_checksum(variant, "model.ubj")
        model_loader.load_model(
            f"ml/traffic/models/source-stratified-v1/{variant}/model.ubj",
            artifact["bytes"],
            artifact["sha256"],
        )
    except Exception as exc:
        _load_errors[variant] = str(exc)
        return _load_errors[variant]
    return None


def predict(source_group: str, observation_unit_id: str, target_hour_str: str) -> dict:
    variant = SOURCE_GROUP_TO_VARIANT.get(source_group)
    if variant is None:
        return {
            "prediction": None,
            "model": {"release": RELEASE, "variant": None, "source_group": source_group},
            "quality": {
                "status": "unavailable",
                "feature_coverage": 0.0,
                "warnings": [f"unknown_source_group:{source_group}"],
            },
        }

    warnings: list[str] = []
    if variant == "transport-activity-countline":
        warnings.append(TRANSPORT_ACTIVITY_WARNING)

    load_error = _ensure_model_loaded(variant)
    if load_error:
        warnings.append(load_error)

    target_hour = datetime.fromisoformat(target_hour_str)
    features, status, lookup_warnings = feature_lookup.build_traffic_features(
        source_group, observation_unit_id, target_hour
    )
    warnings.extend(lookup_warnings)

    # No live traffic feed exists yet (see feature_lookup module docstring),
    # so this always reports unavailable rather than predicting on a
    # fabricated feature vector, even once the model itself has loaded.
    return {
        "prediction": None,
        "model": {"release": RELEASE, "variant": variant, "source_group": source_group},
        "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": warnings},
    }


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

        source_group = body.get("source_group")
        observation_unit_id = body.get("observation_unit_id")
        target_hour = body.get("target_hour")
        if not source_group or not observation_unit_id or not target_hour:
            _bad_request(self, "source_group, observation_unit_id and target_hour are required")
            return

        try:
            result = predict(str(source_group), str(observation_unit_id), target_hour)
        except ValueError:
            _bad_request(self, "target_hour must be a valid ISO-8601 timestamp")
            return
        except Exception as exc:
            # Logged server-side only -- str(exc) can carry internal
            # details even though it isn't a full traceback.
            print(f"traffic-inference: unhandled exception: {exc!r}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "prediction failed"}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
