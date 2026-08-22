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

from _shared import feature_lookup, model_loader

METADATA_PATH = "ml/crowd/models/all-history-v1/metadata.json"
RELEASE = "all-history-v1"

_metadata = None
_booster = None
_load_error: str | None = None


def _ensure_loaded():
    global _metadata, _booster, _load_error
    if _booster is not None or _load_error is not None:
        return
    try:
        _metadata = model_loader.load_metadata(METADATA_PATH)
        artifact = _metadata["promoted_artifact"]
        _booster = model_loader.load_model(
            artifact["path"], artifact["bytes"], artifact["sha256"]
        )
    except Exception as exc:  # refuse to serve an unverified/missing model
        _load_error = str(exc)


def _build_dmatrix(features: dict, metadata: dict):
    import xgboost as xgb

    encoder = metadata["encoder"]
    columns = encoder["model_feature_columns"]
    categorical_columns = set(encoder["categorical_columns"])
    known_sensors = set(encoder["categories"]["sensor_id"])

    sensor_token = f"int:{features['sensor_id']}"
    unseen = sensor_token not in known_sensors

    row = []
    for col in columns:
        if col == "sensor_id__unseen":
            row.append(1 if unseen else 0)
        elif col == "sensor_id":
            row.append(float("nan") if unseen else features["sensor_id"])
        else:
            value = features.get(col)
            row.append(value if value is not None else float("nan"))

    frame_dtypes = {
        col: ("category" if col in categorical_columns else "float64") for col in columns
    }
    import pandas as pd

    df = pd.DataFrame([row], columns=columns)
    for col, dtype in frame_dtypes.items():
        if dtype == "category":
            df[col] = df[col].astype("category")
        else:
            df[col] = df[col].astype("float64")

    return xgb.DMatrix(df, enable_categorical=True), unseen


def predict(sensor_id: str, target_hour_str: str) -> dict:
    _ensure_loaded()
    if _load_error:
        return {
            "prediction": None,
            "model": {"release": RELEASE, "variant": "all-history", "sensor_id": sensor_id},
            "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": [_load_error]},
        }

    target_hour = datetime.fromisoformat(target_hour_str)
    features, status, warnings = feature_lookup.build_crowd_features(sensor_id, target_hour)

    if status == "unavailable":
        return {
            "prediction": None,
            "model": {"release": RELEASE, "variant": "all-history", "sensor_id": sensor_id},
            "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": warnings},
        }

    dmatrix, unseen = _build_dmatrix(features, _metadata)
    if unseen:
        warnings = [*warnings, "unseen_sensor"]

    raw_pred = _booster.predict(dmatrix)[0]
    if raw_pred < 0 or not (raw_pred == raw_pred):  # negative or NaN
        return {
            "prediction": None,
            "model": {"release": RELEASE, "variant": "all-history", "sensor_id": sensor_id},
            "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": [*warnings, "invalid_model_output"]},
        }

    covered = sum(1 for k in features if features[k] is not None)
    coverage = covered / len(features) if features else 0.0

    return {
        "prediction": {"pedestrian_flow_per_hour": float(raw_pred)},
        "model": {"release": RELEASE, "variant": "all-history", "sensor_id": sensor_id},
        "quality": {"status": status, "feature_coverage": coverage, "warnings": warnings},
    }


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
