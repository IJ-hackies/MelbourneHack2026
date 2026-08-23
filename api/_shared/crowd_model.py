"""Shared crowd-model loading and prediction, used by both
api/crowd-inference.py (the public endpoint) and api/route-planner.py
(sampling predicted flow along a candidate route to score "least crowded").
Kept in one place so both call sites share the same model/feature contract
instead of route-planner re-implementing prediction via a self HTTP call.
"""

from __future__ import annotations

from datetime import datetime

from . import feature_lookup, model_loader

METADATA_PATH = "ml/crowd/models/all-history-v1/metadata.json"
RELEASE = "all-history-v1"

_metadata = None
_booster = None
_load_error: str | None = None


def ensure_loaded() -> str | None:
    global _metadata, _booster, _load_error
    if _booster is not None or _load_error is not None:
        return _load_error
    try:
        _metadata = model_loader.load_metadata(METADATA_PATH)
        artifact = _metadata["promoted_artifact"]
        _booster = model_loader.load_model(artifact["path"], artifact["bytes"], artifact["sha256"])
    except Exception as exc:  # refuse to serve an unverified/missing model
        _load_error = str(exc)
    return _load_error


def _build_dmatrix(features: dict, metadata: dict):
    # See api/crowd-inference.py's original docstring for why this is
    # built directly from numpy rather than pandas.Categorical.
    import numpy as np
    import xgboost as xgb

    encoder = metadata["encoder"]
    columns = encoder["model_feature_columns"]
    categorical_columns = set(encoder["categorical_columns"])
    category_lists = encoder["categories"]
    known_sensors = category_lists["sensor_id"]

    sensor_token = f"int:{features['sensor_id']}"
    unseen = sensor_token not in known_sensors

    row: list[float] = []
    feature_types: list[str] = []
    for col in columns:
        feature_types.append("c" if col in categorical_columns else "q")
        if col == "sensor_id__unseen":
            row.append(1.0 if unseen else 0.0)
        elif col == "sensor_id":
            row.append(float("nan") if unseen else float(known_sensors.index(sensor_token)))
        elif col == "is_dst":
            token = f"bool:{features.get('is_dst')}"
            categories = category_lists.get(col, [])
            row.append(float(categories.index(token)) if token in categories else float("nan"))
        else:
            value = features.get(col)
            row.append(float(value) if value is not None else float("nan"))

    array = np.array([row], dtype=np.float64)
    return (
        xgb.DMatrix(array, feature_names=columns, feature_types=feature_types, enable_categorical=True),
        unseen,
    )


def predict(sensor_id: str, target_hour: datetime) -> dict:
    """Returns the same shape as crowd-inference.py's response body:
    {"prediction": {...} | None, "model": {...}, "quality": {...}}."""
    load_error = ensure_loaded()
    if load_error:
        return {
            "prediction": None,
            "model": {"release": RELEASE, "variant": "all-history", "sensor_id": sensor_id},
            "quality": {"status": "unavailable", "feature_coverage": 0.0, "warnings": [load_error]},
        }

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
        # This sensor's own real rolling-168h average/sample-count at the
        # feature_asof cutoff — not a model input, just real recent history
        # to describe the prediction against (e.g. "busier than usual"),
        # rather than showing a bare count with nothing to compare it to.
        "context": {
            "typical_flow_per_hour": features.get("flow_rolling_past_168h_mean"),
            "typical_flow_sample_hours": features.get("flow_rolling_past_168h_count"),
        },
    }
