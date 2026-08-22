#!/usr/bin/env python3
"""Train and evaluate the two crowd-flow feature tables.

This command is deliberately a small, file-first training boundary.  It reads
the feature lists and split contract from ``training_manifest.json``, fits an
XGBoost count model to both candidate tables, and writes model/evaluation
artifacts below an ignored evaluation directory.  The all-history table is
also fitted on the same post-2023 window as the enhanced table so that the
effect of the additional features can be separated from the effect of extra
history.

The target is a non-negative count (people passing a fixed sensor in one
hour), so the model objective is ``count:poisson``.  ``--device auto`` first
tries XGBoost's CUDA histogram implementation and falls back to CPU when CUDA
is not available.  No random row split is performed: the feature builder's
chronological ``train``/``validation``/``test`` labels are required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

try:  # Keep ``--help`` usable before the ML environment is installed.
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised in dependency-error paths.
    pq = None

try:  # XGBoost is a declared ML dependency but loaded lazily for diagnostics.
    import xgboost as xgb
except ImportError:  # pragma: no cover - exercised in dependency-error paths.
    xgb = None


SCRIPT_DIR = Path(__file__).resolve().parent
ML_DIR = SCRIPT_DIR.parent
REPO_DIR = ML_DIR.parent
DEFAULT_MANIFEST = ML_DIR / "crowd" / "training" / "training_manifest.json"
# ``training/`` is already ignored by the repository's data-artifact rule.
DEFAULT_OUTPUT_DIR = ML_DIR / "crowd" / "training" / "evaluation"
TARGET_COLUMN = "pedestrian_flow"
KEY_COLUMN = "observation_key"
SENSOR_COLUMN = "sensor_id"
SPLITS = ("train", "validation", "test")
BASELINES = {
    "lag_1h": "flow_lag_1h",
    "lag_24h": "flow_lag_24h",
    "lag_168h": "flow_lag_168h",
}
DEFAULT_RECENT_START = dt.date(2023, 1, 1)
COMMON_TEST_END = dt.date(2026, 5, 11)


class TrainingError(RuntimeError):
    """An actionable input, validation, or training failure."""


@dataclass
class Dataset:
    """A manifest-described feature table and its normalized audit columns."""

    name: str
    path: Path
    feature_columns: list[str]
    frame: pd.DataFrame


@dataclass
class FeatureEncoder:
    """Train-only categorical encoding for XGBoost.

    Sensor IDs are intentionally categorical rather than ordinal numbers.  A
    category absent from the training split becomes ``NaN`` and receives an
    explicit ``sensor_id__unseen`` indicator.  This avoids assigning an
    artificial ordering to counters while still giving the model a stable
    global branch for a sensor first seen after the training cutoff.
    """

    feature_columns: list[str]
    categorical_columns: list[str]
    categories: dict[str, list[str]]
    train_sensor_tokens: set[str]

    @classmethod
    def fit(cls, frame: pd.DataFrame, feature_columns: Sequence[str]) -> "FeatureEncoder":
        categorical: list[str] = []
        categories: dict[str, list[str]] = {}
        for column in feature_columns:
            series = frame[column]
            force_categorical = column == SENSOR_COLUMN or not (
                pd.api.types.is_numeric_dtype(series)
                or pd.api.types.is_bool_dtype(series)
            )
            if force_categorical:
                categorical.append(column)
                tokens = {
                    _stable_token(value)
                    for value in series.tolist()
                    if not _is_missing(value)
                }
                categories[column] = sorted(tokens)

        train_sensor_tokens = {
            _stable_token(value)
            for value in frame[SENSOR_COLUMN].tolist()
            if not _is_missing(value)
        } if SENSOR_COLUMN in frame.columns else set()
        return cls(
            feature_columns=list(feature_columns),
            categorical_columns=categorical,
            categories=categories,
            train_sensor_tokens=train_sensor_tokens,
        )

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return a pandas frame with native XGBoost categorical columns."""

        transformed: dict[str, pd.Series] = {}
        for column in self.feature_columns:
            series = frame[column]
            if column in self.categorical_columns:
                tokens = series.map(_stable_token)
                transformed[column] = pd.Series(
                    pd.Categorical(tokens, categories=self.categories[column]),
                    index=frame.index,
                    name=column,
                )
                if column == SENSOR_COLUMN:
                    is_missing = series.map(_is_missing).astype(bool)
                    is_unseen = (~is_missing) & (~tokens.isin(self.train_sensor_tokens))
                    transformed["sensor_id__unseen"] = is_unseen.astype("float32")
            else:
                # XGBoost handles NaN values natively.  Float32 keeps the
                # all-history table tractable without changing feature order.
                numeric = pd.to_numeric(series, errors="coerce")
                transformed[column] = numeric.astype("float32")
        return pd.DataFrame(transformed, index=frame.index)

    def metadata(self) -> dict[str, Any]:
        model_feature_columns: list[str] = []
        for column in self.feature_columns:
            model_feature_columns.append(column)
            if column == SENSOR_COLUMN and column in self.categorical_columns:
                model_feature_columns.append("sensor_id__unseen")
        return {
            "encoding": "xgboost_native_categorical",
            "feature_columns": self.feature_columns,
            "model_feature_columns": model_feature_columns,
            "categorical_columns": self.categorical_columns,
            "categories": self.categories,
            "train_sensor_count": len(self.train_sensor_tokens),
            "train_sensor_tokens": sorted(self.train_sensor_tokens),
            "unseen_sensor_policy": "map to missing category plus sensor_id__unseen=1",
        }


def _stable_token(value: Any) -> str:
    """Represent a scalar category/key deterministically across processes."""

    if _is_missing(value):
        return "<MISSING>"
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return f"{type(value).__name__}:{value.isoformat()}"
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return f"int:{int(value)}"
    if isinstance(value, (np.floating, float)):
        return f"float:{float(value):.17g}"
    if isinstance(value, (np.bool_, bool)):
        return f"bool:{bool(value)}"
    return f"str:{str(value)}"


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if np.isscalar(result) else False
    except (TypeError, ValueError):
        return False


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return value.isoformat()
    if value is pd.NA:
        return None
    raise TypeError(f"cannot JSON-encode {type(value).__name__}")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=_json_default)
        handle.write("\n")
    os.replace(temporary, path)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, na_rep="")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _artifact_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative_path = str(resolved.relative_to(REPO_DIR))
    except ValueError:
        relative_path = None
    return {
        "path": str(resolved),
        "relative_path": relative_path,
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _resolve_path(value: str | os.PathLike[str], *, manifest_path: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.exists():
        return candidate.resolve()
    if not candidate.is_absolute():
        for base in (manifest_path.parent, REPO_DIR, Path.cwd()):
            resolved = (base / candidate).resolve()
            if resolved.exists():
                return resolved
    return candidate.resolve()


def _parse_date_series(series: pd.Series, *, column: str) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.isna().any():
        raise TrainingError(f"{column} contains unparseable local dates")
    return parsed.dt.date


def _validate_feature_columns(name: str, columns: Sequence[str]) -> None:
    if not columns:
        raise TrainingError(f"{name} has no manifest feature_columns")
    if len(set(columns)) != len(columns):
        raise TrainingError(f"{name} feature_columns contain duplicates")
    forbidden_exact = {KEY_COLUMN, TARGET_COLUMN, "local_date", "observed_at_local"}
    forbidden = sorted(forbidden_exact.intersection(columns))
    if forbidden:
        raise TrainingError(f"{name} feature list includes audit/target columns: {forbidden}")
    leakage = []
    for column in columns:
        lowered = column.lower()
        if "direction" in lowered and "count" in lowered:
            leakage.append(column)
        if lowered in {"pedestrian_flow", "target", "target_count"}:
            leakage.append(column)
        if "same_hour" in lowered and ("flow" in lowered or "transport" in lowered):
            leakage.append(column)
    if leakage:
        raise TrainingError(f"{name} feature list contains likely target leakage: {sorted(set(leakage))}")


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise TrainingError(f"training manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"could not read training manifest {path}: {exc}") from exc
    datasets = payload.get("datasets")
    if not isinstance(datasets, Mapping):
        raise TrainingError("training manifest has no datasets mapping")
    for required in ("all_history", "recent_enhanced"):
        details = datasets.get(required)
        if not isinstance(details, Mapping):
            raise TrainingError(f"training manifest is missing datasets.{required}")
        feature_columns = details.get("feature_columns")
        if not isinstance(feature_columns, list):
            raise TrainingError(f"datasets.{required}.feature_columns must be a list")
        _validate_feature_columns(required, [str(column) for column in feature_columns])
        if not details.get("path"):
            raise TrainingError(f"datasets.{required}.path is empty")
    return payload


def _load_dataset(name: str, details: Mapping[str, Any], manifest_path: Path) -> Dataset:
    path = _resolve_path(str(details["path"]), manifest_path=manifest_path)
    if not path.exists():
        raise TrainingError(f"{name} dataset not found: {path}")
    feature_columns = [str(column) for column in details["feature_columns"]]
    required = [
        KEY_COLUMN,
        TARGET_COLUMN,
        SENSOR_COLUMN,
        "local_date",
        "observed_at_local",
        "split",
        *feature_columns,
    ]
    columns = list(dict.fromkeys(required))
    try:
        frame = pd.read_parquet(path, columns=columns)
    except (OSError, ValueError, ImportError) as exc:
        raise TrainingError(f"could not read {name} dataset {path}: {exc}") from exc
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise TrainingError(f"{name} dataset is missing columns: {', '.join(missing)}")
    if frame.empty:
        raise TrainingError(f"{name} dataset is empty: {path}")

    frame = frame.copy()
    frame["__key"] = frame[KEY_COLUMN].map(_key_text)
    if frame["__key"].eq("").any():
        raise TrainingError(f"{name} contains a missing observation_key")
    duplicates = frame["__key"].duplicated(keep=False)
    if duplicates.any():
        example = frame.loc[duplicates, "__key"].head(3).tolist()
        raise TrainingError(f"{name} observation_key is not unique (examples: {example})")

    frame["__split"] = frame["split"].astype("string").str.lower()
    observed_splits = set(frame["__split"].dropna().tolist())
    missing_splits = sorted(set(SPLITS) - observed_splits)
    if missing_splits:
        raise TrainingError(f"{name} is missing required split(s): {missing_splits}")
    invalid_splits = sorted(observed_splits - {"train", "validation", "test", "post_test"})
    if invalid_splits:
        raise TrainingError(f"{name} has unknown split labels: {invalid_splits}")

    target = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce")
    if target.isna().any() or (~np.isfinite(target.to_numpy(dtype="float64"))).any():
        raise TrainingError(f"{name} target {TARGET_COLUMN!r} contains null/non-finite values")
    if (target < 0).any():
        raise TrainingError(f"{name} target {TARGET_COLUMN!r} contains negative counts")
    frame[TARGET_COLUMN] = target.astype("float32")
    frame["__local_date"] = _parse_date_series(frame["local_date"], column=f"{name}.local_date")
    # Stable order makes sampling and output byte-for-byte reproducible.
    frame = frame.sort_values("__key", kind="mergesort").reset_index(drop=True)
    return Dataset(name=name, path=path, feature_columns=feature_columns, frame=frame)


def _key_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value)


def _validate_chronological_split_contract(
    dataset: Dataset,
    split_contract: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    """Assert that split labels agree with the manifest's actual date bounds."""

    try:
        train_end = dt.date.fromisoformat(str(split_contract["train_end"]))
        validation_end = dt.date.fromisoformat(str(split_contract["validation_end"]))
        test_end = dt.date.fromisoformat(str(split_contract["test_end"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise TrainingError(
            "split_contract must define ISO train_end, validation_end, and test_end dates"
        ) from exc
    if not train_end < validation_end < test_end:
        raise TrainingError("split_contract boundaries must be strictly chronological")

    expected = {
        "train": (None, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, test_end),
        "post_test": (test_end, None),
    }
    observed: dict[str, dict[str, str]] = {}
    for split, (exclusive_start, inclusive_end) in expected.items():
        rows = dataset.frame.loc[dataset.frame["__split"] == split, "__local_date"]
        if rows.empty:
            if split == "post_test":
                continue
            raise TrainingError(f"{dataset.name} has no rows for required split {split!r}")
        minimum = min(rows)
        maximum = max(rows)
        if exclusive_start is not None and minimum <= exclusive_start:
            raise TrainingError(
                f"{dataset.name} {split} starts {minimum}, not after {exclusive_start}"
            )
        if inclusive_end is not None and maximum > inclusive_end:
            raise TrainingError(
                f"{dataset.name} {split} ends {maximum}, after {inclusive_end}"
            )
        observed[split] = {"min_date": str(minimum), "max_date": str(maximum)}
    return observed


def _validate_shared_test_keys(
    all_history: Dataset,
    recent_enhanced: Dataset,
    *,
    allow_intersection: bool,
) -> tuple[list[str], dict[str, Any]]:
    all_keys = set(
        all_history.frame.loc[all_history.frame["__split"] == "test", "__key"]
    )
    recent_keys = set(
        recent_enhanced.frame.loc[recent_enhanced.frame["__split"] == "test", "__key"]
    )
    shared = all_keys.intersection(recent_keys)
    only_all = sorted(all_keys - recent_keys)
    only_recent = sorted(recent_keys - all_keys)
    if not shared:
        raise TrainingError("all_history and recent_enhanced have no shared test observation_key values")
    if (only_all or only_recent) and not allow_intersection:
        raise TrainingError(
            "test observation_key sets differ; pass --allow-test-key-intersection only "
            f"for an explicit intersection comparison (all_only={len(only_all)}, "
            f"recent_only={len(only_recent)})"
        )

    all_test = all_history.frame.set_index("__key").loc[sorted(shared)]
    recent_test = recent_enhanced.frame.set_index("__key").loc[sorted(shared)]
    all_target = all_test[TARGET_COLUMN].to_numpy(dtype="float64")
    recent_target = recent_test[TARGET_COLUMN].to_numpy(dtype="float64")
    if not np.allclose(all_target, recent_target, rtol=0.0, atol=0.0):
        raise TrainingError("shared test keys have different pedestrian_flow labels between datasets")
    all_sensor = all_test[SENSOR_COLUMN].map(_stable_token).to_numpy()
    recent_sensor = recent_test[SENSOR_COLUMN].map(_stable_token).to_numpy()
    if not np.array_equal(all_sensor, recent_sensor):
        raise TrainingError("shared test keys have different sensor_id values between datasets")
    details = {
        "all_history_test_keys": len(all_keys),
        "recent_enhanced_test_keys": len(recent_keys),
        "shared_test_keys": len(shared),
        "all_history_only_test_keys": len(only_all),
        "recent_enhanced_only_test_keys": len(only_recent),
        "exact_match": not only_all and not only_recent,
        "intersection_policy": "strict" if not allow_intersection else "shared_intersection",
    }
    return sorted(shared), details


def _sample_keys(keys: Sequence[str], limit: int | None, seed: int) -> list[str]:
    ordered = sorted(keys)
    if limit is None or len(ordered) <= limit:
        return ordered
    generator = np.random.default_rng(seed)
    selected = generator.choice(len(ordered), size=limit, replace=False)
    return sorted(ordered[int(index)] for index in selected)


def _select_split(
    dataset: Dataset,
    split: str,
    *,
    recent_start: dt.date | None,
    test_keys: Sequence[str] | None,
    sample_limit: int | None,
    seed: int,
) -> pd.DataFrame:
    frame = dataset.frame.loc[dataset.frame["__split"] == split]
    if recent_start is not None:
        frame = frame.loc[frame["__local_date"] >= recent_start]
    if test_keys is not None:
        frame = frame.loc[frame["__key"].isin(set(test_keys))]
    if frame.empty:
        raise TrainingError(
            f"{dataset.name} has no rows for split={split!r} after ablation/key filtering"
        )
    frame = frame.sort_values("__key", kind="mergesort")
    if sample_limit is not None and len(frame) > sample_limit:
        generator = np.random.default_rng(seed)
        selected = generator.choice(len(frame), size=sample_limit, replace=False)
        frame = frame.iloc[np.sort(selected)]
    return frame.reset_index(drop=True)


def _metric_bundle(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    y_true = np.asarray(actual, dtype="float64")
    y_pred = np.asarray(predicted, dtype="float64")
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true = y_true[valid]
    y_pred = y_pred[valid]
    if len(y_true) == 0:
        return {
            "n": 0,
            "target_mean": None,
            "mae": None,
            "rmse": None,
            "poisson_deviance": None,
        }
    y_pred = np.clip(y_pred, 0.0, None)
    # A practical epsilon avoids overflow in ``y / mu`` for predictions that
    # underflow near zero while preserving the limiting Poisson penalty.
    safe_pred = np.maximum(y_pred, 1e-12)
    positive = y_true > 0
    deviance_terms = np.zeros_like(y_true)
    deviance_terms[positive] = y_true[positive] * (
        np.log(y_true[positive]) - np.log(safe_pred[positive])
    ) - (y_true[positive] - safe_pred[positive])
    deviance_terms[~positive] = safe_pred[~positive]
    return {
        "n": int(len(y_true)),
        "target_mean": float(np.mean(y_true)),
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "poisson_deviance": float(2.0 * np.mean(deviance_terms)),
    }


def _prediction_array(model: Any, features: pd.DataFrame) -> np.ndarray:
    try:
        predictions = np.asarray(model.predict(features), dtype="float64")
    except Exception as exc:  # pragma: no cover - exact exception is XGBoost-version-specific.
        raise TrainingError(f"model prediction failed: {exc}") from exc
    if predictions.ndim != 1:
        predictions = predictions.reshape(-1)
    if len(predictions) != len(features):
        raise TrainingError("model returned a prediction count different from its input rows")
    if (~np.isfinite(predictions)).any():
        raise TrainingError("model returned non-finite predictions")
    return np.clip(predictions, 0.0, None)


def _model_parameters(args: argparse.Namespace, device: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "objective": "count:poisson",
        "eval_metric": "poisson-nloglik",
        "n_estimators": args.n_estimators,
        "max_depth": args.max_depth,
        "learning_rate": args.learning_rate,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "min_child_weight": args.min_child_weight,
        "reg_lambda": args.reg_lambda,
        "reg_alpha": args.reg_alpha,
        # A bounded Newton step is recommended for count:poisson models.
        "max_delta_step": args.max_delta_step,
        "random_state": args.seed,
        "n_jobs": args.n_jobs,
        "tree_method": "hist",
        "device": device,
        "enable_categorical": True,
        "verbosity": 0,
    }
    if args.early_stopping_rounds > 0:
        parameters["early_stopping_rounds"] = args.early_stopping_rounds
    return parameters


def _fit_model(
    train_features: pd.DataFrame,
    train_target: np.ndarray,
    validation_features: pd.DataFrame,
    validation_target: np.ndarray,
    args: argparse.Namespace,
) -> tuple[Any, str, list[str]]:
    if xgb is None:
        raise TrainingError(
            "xgboost is required to train crowd models; install ml/requirements.txt"
        )
    if args.device == "cpu":
        devices = ["cpu"]
    else:
        devices = ["cuda", "cpu"]
    failures: list[str] = []
    for device in devices:
        parameters = _model_parameters(args, device)
        try:
            model = xgb.XGBRegressor(**parameters)
            model.fit(
                train_features,
                train_target,
                eval_set=[(validation_features, validation_target)],
                verbose=False,
            )
            return model, device, failures
        except Exception as exc:  # CUDA errors differ across XGBoost versions.
            failures.append(f"{device}: {type(exc).__name__}: {exc}")
            if device == "cpu":
                break
    joined = " | ".join(failures)
    raise TrainingError(f"XGBoost training failed on requested devices ({joined})")


def _safe_sensor_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value)


def _rows_for_metrics(
    *,
    variant: str,
    dataset_name: str,
    split: str,
    scope: str,
    metric: Mapping[str, Any],
    sensor_id: Any = None,
    sensor_seen_in_train: bool | None = None,
    feature: str | None = None,
    baseline: str | None = None,
    missing: bool | None = None,
    missing_rate: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "variant": variant,
        "dataset": dataset_name,
        "split": split,
        "scope": scope,
        "sensor_id": None if sensor_id is None else _safe_sensor_text(sensor_id),
        "sensor_seen_in_train": sensor_seen_in_train,
        "feature": feature,
        "baseline": baseline,
        "missing": missing,
        "missing_rate": missing_rate,
    }
    row.update(metric)
    return row


def _baseline_metrics(
    frame: pd.DataFrame,
    *,
    variant: str,
    dataset_name: str,
    split: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    actual = frame[TARGET_COLUMN].to_numpy(dtype="float64")
    for baseline_name, column in BASELINES.items():
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
        valid = np.isfinite(values)
        metric = _metric_bundle(actual[valid], values[valid])
        metric["available_rows"] = int(valid.sum())
        metric["coverage"] = float(valid.mean()) if len(valid) else 0.0
        metric["missing_rows"] = int((~valid).sum())
        rows.append(
            _rows_for_metrics(
                variant=variant,
                dataset_name=dataset_name,
                split=split,
                scope="baseline",
                metric=metric,
                baseline=baseline_name,
            )
        )
    return rows


def _sensor_metrics(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    *,
    variant: str,
    dataset_name: str,
    split: str,
    train_sensor_tokens: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actual = frame[TARGET_COLUMN].to_numpy(dtype="float64")
    sensor_tokens = frame[SENSOR_COLUMN].map(_stable_token)
    seen = sensor_tokens.isin(train_sensor_tokens)
    per_sensor: list[dict[str, Any]] = []
    for token, indexes in sensor_tokens.groupby(sensor_tokens, sort=True).groups.items():
        positions = np.asarray(list(indexes), dtype="int64")
        metric = _metric_bundle(actual[positions], predictions[positions])
        per_sensor.append(
            _rows_for_metrics(
                variant=variant,
                dataset_name=dataset_name,
                split=split,
                scope="per_sensor",
                metric=metric,
                sensor_id=token,
                sensor_seen_in_train=bool(seen.iloc[positions[0]]),
            )
        )
    groups: list[dict[str, Any]] = []
    for group_name, mask in (
        ("seen", seen.to_numpy(dtype=bool)),
        ("unseen", (~seen).to_numpy(dtype=bool)),
    ):
        metric = _metric_bundle(actual[mask], predictions[mask])
        metric["sensor_group"] = group_name
        groups.append(
            _rows_for_metrics(
                variant=variant,
                dataset_name=dataset_name,
                split=split,
                scope="sensor_seen_group",
                metric=metric,
                sensor_id=group_name,
                sensor_seen_in_train=group_name == "seen",
            )
        )
    return per_sensor, groups


def _missing_feature_metrics(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    *,
    variant: str,
    dataset_name: str,
    split: str,
    feature_columns: Sequence[str],
) -> list[dict[str, Any]]:
    actual = frame[TARGET_COLUMN].to_numpy(dtype="float64")
    rows: list[dict[str, Any]] = []
    for feature in feature_columns:
        missing = frame[feature].isna().to_numpy(dtype=bool)
        rate = float(missing.mean()) if len(missing) else 0.0
        for is_missing in (True, False):
            metric = _metric_bundle(actual[missing == is_missing], predictions[missing == is_missing])
            metric["rows"] = int((missing == is_missing).sum())
            rows.append(
                _rows_for_metrics(
                    variant=variant,
                    dataset_name=dataset_name,
                    split=split,
                    scope="missing_feature",
                    metric=metric,
                    feature=feature,
                    missing=is_missing,
                    missing_rate=rate,
                )
            )
    return rows


def _missingness_group_metrics(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    *,
    variant: str,
    dataset_name: str,
    split: str,
    feature_columns: Sequence[str],
) -> list[dict[str, Any]]:
    """Score complete rows separately from rows with any missing feature."""

    actual = frame[TARGET_COLUMN].to_numpy(dtype="float64")
    incomplete = frame[list(feature_columns)].isna().any(axis=1).to_numpy(dtype=bool)
    rows: list[dict[str, Any]] = []
    for group_name, mask in (("complete", ~incomplete), ("missing", incomplete)):
        metric = _metric_bundle(actual[mask], predictions[mask])
        metric["missingness_group"] = group_name
        rows.append(
            _rows_for_metrics(
                variant=variant,
                dataset_name=dataset_name,
                split=split,
                scope="missingness_group",
                metric=metric,
                feature=group_name,
            )
        )
    return rows


def _prediction_frame(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    *,
    variant: str,
    dataset_name: str,
    train_sensor_tokens: set[str],
    feature_columns: Sequence[str],
) -> pd.DataFrame:
    sensor_tokens = frame[SENSOR_COLUMN].map(_stable_token)
    output: dict[str, Any] = {
        "variant": variant,
        "candidate": variant,
        "dataset": dataset_name,
        "observation_key": frame[KEY_COLUMN].map(_key_text).to_numpy(),
        "sensor_id": frame[SENSOR_COLUMN].to_numpy(),
        "split": frame["__split"].to_numpy(),
        "sensor_seen_in_train": sensor_tokens.isin(train_sensor_tokens).to_numpy(dtype=bool),
        "local_date": frame["local_date"].to_numpy(),
        "observed_at_local": frame["observed_at_local"].to_numpy(),
        "actual": frame[TARGET_COLUMN].to_numpy(dtype="float64"),
        "prediction": predictions,
        "missing_feature_count": frame[list(feature_columns)].isna().sum(axis=1).to_numpy(dtype="int64"),
    }
    for baseline_name, column in BASELINES.items():
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
        output[f"baseline_{baseline_name}"] = np.where(np.isfinite(values), np.clip(values, 0, None), np.nan)
    return pd.DataFrame(output)


def _model_output_paths(output_dir: Path, variant: str) -> tuple[Path, Path]:
    model_dir = output_dir / "models"
    return model_dir / f"{variant}.ubj", model_dir / f"{variant}.metadata.json"


def _ensure_output_is_writable(output_dir: Path, *, overwrite: bool) -> None:
    known = [
        output_dir / "predictions.csv",
        output_dir / "metrics.json",
        output_dir / "metrics.csv",
        output_dir / "per_sensor_metrics.csv",
        output_dir / "missing_feature_breakdown.csv",
        output_dir / "evaluation_manifest.json",
    ]
    model_dir = output_dir / "models"
    if model_dir.exists():
        known.extend(
            path for path in model_dir.glob("*")
            if path.suffix.lower() in {".json", ".ubj"}
        )
    existing = [path for path in known if path.exists()]
    if existing and not overwrite:
        joined = ", ".join(str(path) for path in existing[:8])
        suffix = " ..." if len(existing) > 8 else ""
        raise TrainingError(f"outputs already exist; pass --overwrite: {joined}{suffix}")
    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.sample is not None and args.sample < 1:
        raise TrainingError("--sample must be at least 1")
    if args.smoke:
        sample_limit = args.sample or 2_000
        n_estimators = min(args.n_estimators, 12)
    else:
        sample_limit = args.sample
        n_estimators = args.n_estimators
    args.n_estimators = n_estimators

    manifest_path = args.manifest.resolve()
    manifest = _load_manifest(manifest_path)
    datasets = manifest["datasets"]
    for argument, dataset_name in (
        (args.all_history, "all_history"),
        (args.recent_enhanced, "recent_enhanced"),
    ):
        if argument is not None:
            datasets[dataset_name] = dict(datasets[dataset_name])
            datasets[dataset_name]["path"] = str(argument)
    if args.recent_start is None:
        manifest_recent_start = datasets["recent_enhanced"].get("recent_start")
        args.recent_start = (
            dt.date.fromisoformat(str(manifest_recent_start))
            if manifest_recent_start
            else DEFAULT_RECENT_START
        )
    all_history = _load_dataset("all_history", datasets["all_history"], manifest_path)
    recent_enhanced = _load_dataset("recent_enhanced", datasets["recent_enhanced"], manifest_path)
    split_contract = manifest.get("split_contract")
    if not isinstance(split_contract, Mapping):
        raise TrainingError("training manifest has no split_contract mapping")
    observed_split_dates = {
        "all_history": _validate_chronological_split_contract(all_history, split_contract),
        "recent_enhanced": _validate_chronological_split_contract(
            recent_enhanced, split_contract
        ),
    }
    shared_keys, shared_details = _validate_shared_test_keys(
        all_history,
        recent_enhanced,
        allow_intersection=args.allow_test_key_intersection,
    )
    selected_test_keys = _sample_keys(shared_keys, sample_limit, args.seed + 1)

    output_dir = args.output_dir.resolve()
    _ensure_output_is_writable(output_dir, overwrite=args.overwrite)

    variant_specs = [
        ("all_history", all_history, None),
        ("recent_enhanced", recent_enhanced, None),
        ("matched_recent_ablation", all_history, args.recent_start),
    ]
    all_metric_rows: list[dict[str, Any]] = []
    per_sensor_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    model_records: list[dict[str, Any]] = []
    model_results: dict[str, dict[str, Any]] = {}
    actual_devices: dict[str, str] = {}

    for variant, dataset, date_filter in variant_specs:
        train = _select_split(
            dataset,
            "train",
            recent_start=date_filter,
            test_keys=None,
            sample_limit=sample_limit,
            seed=args.seed + 11,
        )
        validation = _select_split(
            dataset,
            "validation",
            recent_start=date_filter,
            test_keys=None,
            sample_limit=sample_limit,
            seed=args.seed + 17,
        )
        test = _select_split(
            dataset,
            "test",
            recent_start=date_filter,
            test_keys=selected_test_keys,
            sample_limit=None,
            seed=args.seed + 23,
        )
        expected_keys = set(selected_test_keys)
        actual_keys = set(test["__key"])
        if actual_keys != expected_keys:
            raise TrainingError(
                f"{variant} test keys changed after filtering (expected {len(expected_keys)}, "
                f"got {len(actual_keys)})"
            )

        encoder = FeatureEncoder.fit(train, dataset.feature_columns)
        train_features = encoder.transform(train)
        validation_features = encoder.transform(validation)
        test_features = encoder.transform(test)
        train_target = train[TARGET_COLUMN].to_numpy(dtype="float64")
        validation_target = validation[TARGET_COLUMN].to_numpy(dtype="float64")
        test_target = test[TARGET_COLUMN].to_numpy(dtype="float64")
        model, device, device_failures = _fit_model(
            train_features,
            train_target,
            validation_features,
            validation_target,
            args,
        )
        actual_devices[variant] = device
        validation_predictions = _prediction_array(model, validation_features)
        test_predictions = _prediction_array(model, test_features)
        if sample_limit is not None:
            train_predictions = _prediction_array(model, train_features)
            prediction_frames.extend(
                [
                    _prediction_frame(
                        train,
                        train_predictions,
                        variant=variant,
                        dataset_name=dataset.name,
                        train_sensor_tokens=encoder.train_sensor_tokens,
                        feature_columns=dataset.feature_columns,
                    ),
                    _prediction_frame(
                        validation,
                        validation_predictions,
                        variant=variant,
                        dataset_name=dataset.name,
                        train_sensor_tokens=encoder.train_sensor_tokens,
                        feature_columns=dataset.feature_columns,
                    ),
                ]
            )

        validation_metric = _metric_bundle(validation_target, validation_predictions)
        validation_metric["rows_available"] = len(validation)
        test_metric = _metric_bundle(test_target, test_predictions)
        test_metric["rows_available"] = len(test)
        all_metric_rows.extend(
            [
                _rows_for_metrics(
                    variant=variant,
                    dataset_name=dataset.name,
                    split="validation",
                    scope="overall",
                    metric=validation_metric,
                ),
                _rows_for_metrics(
                    variant=variant,
                    dataset_name=dataset.name,
                    split="test",
                    scope="overall",
                    metric=test_metric,
                ),
            ]
        )
        all_metric_rows.extend(
            _baseline_metrics(
                validation,
                variant=variant,
                dataset_name=dataset.name,
                split="validation",
            )
        )
        all_metric_rows.extend(
            _baseline_metrics(
                test,
                variant=variant,
                dataset_name=dataset.name,
                split="test",
            )
        )
        sensors, groups = _sensor_metrics(
            test,
            test_predictions,
            variant=variant,
            dataset_name=dataset.name,
            split="test",
            train_sensor_tokens=encoder.train_sensor_tokens,
        )
        per_sensor_rows.extend(sensors)
        all_metric_rows.extend(groups)
        variant_missing_rows = _missing_feature_metrics(
            test,
            test_predictions,
            variant=variant,
            dataset_name=dataset.name,
            split="test",
            feature_columns=dataset.feature_columns,
        )
        missing_rows.extend(variant_missing_rows)
        missingness_groups = _missingness_group_metrics(
            test,
            test_predictions,
            variant=variant,
            dataset_name=dataset.name,
            split="test",
            feature_columns=dataset.feature_columns,
        )
        all_metric_rows.extend(missingness_groups)
        prediction_frames.append(
            _prediction_frame(
                test,
                test_predictions,
                variant=variant,
                dataset_name=dataset.name,
                train_sensor_tokens=encoder.train_sensor_tokens,
                feature_columns=dataset.feature_columns,
            )
        )

        model_path, metadata_path = _model_output_paths(output_dir, variant)
        try:
            model.save_model(str(model_path))
        except Exception as exc:  # pragma: no cover - exact exception is XGBoost-version-specific.
            raise TrainingError(f"could not save {variant} model to {model_path}: {exc}") from exc
        booster = model.get_booster()
        feature_gain = sorted(
            booster.get_score(importance_type="gain").items(),
            key=lambda item: (-item[1], item[0]),
        )
        best_iteration = getattr(model, "best_iteration", None)
        best_score = getattr(model, "best_score", None)
        metadata = {
            "schema_version": 1,
            "variant": variant,
            "dataset": dataset.name,
            "dataset_path": str(dataset.path),
            "dataset_sha256": _sha256(dataset.path),
            "objective": "count:poisson",
            "eval_metric": "poisson-nloglik",
            "device": device,
            "device_failures_before_success": device_failures,
            "seed": args.seed,
            "train_rows": len(train),
            "validation_rows": len(validation),
            "test_rows": len(test),
            "ablation_recent_start": str(args.recent_start) if date_filter else None,
            "encoder": encoder.metadata(),
            "xgboost_version": getattr(xgb, "__version__", None),
            "xgboost_parameters": _model_parameters(args, device),
            "boosted_rounds": booster.num_boosted_rounds(),
            "best_iteration": best_iteration,
            "best_score": best_score,
            "feature_gain": [
                {"feature": feature, "gain": gain}
                for feature, gain in feature_gain
            ],
            "training_script_sha256": _sha256(Path(__file__).resolve()),
            "git_head": _git_head(),
            "xgboost_build_info": xgb.build_info(),
        }
        _atomic_json(metadata_path, metadata)
        model_record = {
            "variant": variant,
            "dataset": dataset.name,
            "model_path": str(model_path),
            "metadata_path": str(metadata_path),
            "device": device,
            "train_rows": len(train),
            "validation_rows": len(validation),
            "test_rows": len(test),
            "feature_count": len(encoder.metadata()["model_feature_columns"]),
            "train_sensor_count": len(encoder.train_sensor_tokens),
            "device_failures_before_success": device_failures,
            "boosted_rounds": booster.num_boosted_rounds(),
            "best_iteration": best_iteration,
            "best_score": best_score,
            "model_bytes": model_path.stat().st_size,
            "model_sha256": _sha256(model_path),
            "top_feature_gain": metadata["feature_gain"][:20],
        }
        model_records.append(model_record)
        model_results[variant] = {
            **model_record,
            "feature_columns": list(dataset.feature_columns),
            "test_metrics": test_metric,
            "validation_metrics": validation_metric,
            "per_sensor_metrics": sensors,
            "seen_unseen_sensor_metrics": groups,
            "missingness_metrics": variant_missing_rows,
            "missingness_group_metrics": missingness_groups,
        }

    predictions = pd.concat(prediction_frames, ignore_index=True)
    predictions = predictions.sort_values(["variant", "observation_key"], kind="mergesort")
    predictions_path = output_dir / "predictions.csv"
    _atomic_csv(predictions_path, predictions)
    for result in model_results.values():
        result["predictions_csv"] = str(predictions_path)

    metrics_frame = pd.DataFrame(all_metric_rows)
    metrics_frame = metrics_frame.sort_values(
        ["variant", "split", "scope", "sensor_id", "feature", "baseline"],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    metrics_path = output_dir / "metrics.csv"
    _atomic_csv(metrics_path, metrics_frame)
    per_sensor_frame = pd.DataFrame(per_sensor_rows)
    per_sensor_frame = per_sensor_frame.sort_values(
        ["variant", "sensor_id"], kind="mergesort"
    ).reset_index(drop=True)
    per_sensor_path = output_dir / "per_sensor_metrics.csv"
    _atomic_csv(per_sensor_path, per_sensor_frame)
    missing_frame = pd.DataFrame(missing_rows)
    missing_frame = missing_frame.sort_values(
        ["variant", "feature", "missing"], kind="mergesort"
    ).reset_index(drop=True)
    missing_path = output_dir / "missing_feature_breakdown.csv"
    _atomic_csv(missing_path, missing_frame)

    run_manifest = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "git_head": _git_head(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "seed": args.seed,
        "objective": "count:poisson",
        "run_mode": "small_data" if sample_limit is not None else "full",
        "requested_device": args.device,
        "actual_devices": actual_devices,
        "reproducibility": {
            "seed": args.seed,
            "sample_rows_per_split": sample_limit,
            "variant_xgboost_parameters": {
                variant: _model_parameters(args, device)
                for variant, device in actual_devices.items()
            },
            "xgboost_build_info": xgb.build_info(),
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": _sha256(manifest_path),
        },
        "split_contract": {
            "strategy": "chronological/as-of; post_test is excluded from claimed scores",
            **dict(split_contract),
            "observed_dates": observed_split_dates,
        },
        "shared_test_keys": {
            **shared_details,
            "selected_test_keys": len(selected_test_keys),
            "sampled": len(selected_test_keys) != len(shared_keys),
            "test_end_contract": str(COMMON_TEST_END),
        },
        "datasets": {
            name: {
                "path": str(dataset.path),
                "sha256": _sha256(dataset.path),
                "rows_loaded": len(dataset.frame),
                "feature_columns": dataset.feature_columns,
            }
            for name, dataset in (
                ("all_history", all_history),
                ("recent_enhanced", recent_enhanced),
            )
        },
        "models": model_results,
        "model_records": model_records,
        "outputs": {
            "predictions_csv": str(predictions_path),
            "metrics_csv": str(metrics_path),
            "per_sensor_metrics_csv": str(per_sensor_path),
            "missing_feature_breakdown_csv": str(missing_path),
        },
        "notes": [
            "Test metrics use identical shared observation_key values for both candidate tables.",
            "matched_recent_ablation filters all_history to local_date >= recent_start before fitting.",
            "Sensor IDs are categorical and fit on training sensors only; unseen sensors are scored separately.",
            "Optional feature nulls remain null for XGBoost and are not converted to zero.",
        ],
        "scores": all_metric_rows,
        "per_sensor_scores": per_sensor_rows,
        "missing_feature_scores": missing_rows,
    }
    metrics_json_path = output_dir / "metrics.json"
    _atomic_json(metrics_json_path, run_manifest)
    evaluation_manifest_path = output_dir / "evaluation_manifest.json"
    artifact_paths = [
        predictions_path,
        metrics_path,
        metrics_json_path,
        per_sensor_path,
        missing_path,
        *(
            Path(record[key])
            for record in model_records
            for key in ("model_path", "metadata_path")
        ),
    ]
    _atomic_json(
        evaluation_manifest_path,
        {
            "schema_version": 1,
            "metrics_json": str(metrics_json_path),
            "predictions_csv": str(predictions_path),
            "metrics_csv": str(metrics_path),
            "per_sensor_metrics_csv": str(per_sensor_path),
            "missing_feature_breakdown_csv": str(missing_path),
            "model_paths": [record["model_path"] for record in model_records],
            "shared_test_keys": shared_details,
            "artifacts": [_artifact_record(path) for path in artifact_paths],
        },
    )
    return run_manifest


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--all-history",
        type=Path,
        help="override datasets.all_history.path from the training manifest",
    )
    parser.add_argument(
        "--recent-enhanced",
        type=Path,
        help="override datasets.recent_enhanced.path from the training manifest",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="XGBoost device; auto/cuda attempts CUDA then falls back to CPU",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="force CPU training (equivalent to --device cpu)",
    )
    parser.add_argument(
        "--sample",
        "--sample-rows",
        dest="sample",
        type=int,
        nargs="?",
        const=5_000,
        help="deterministically cap rows per split (bare flag uses 5000)",
    )
    parser.add_argument(
        "--smoke",
        "--small-data",
        "--ci",
        action="store_true",
        help="small deterministic run (up to 2000 rows per split and 12 trees)",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-test-key-intersection",
        action="store_true",
        help="permit fair scoring on the intersection when candidate test keys differ",
    )
    parser.add_argument(
        "--recent-start",
        type=dt.date.fromisoformat,
        default=None,
        help="matched-ablation start date (defaults to the enhanced manifest recent_start)",
    )
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--subsample", type=float, default=1.0)
    parser.add_argument("--colsample-bytree", type=float, default=1.0)
    parser.add_argument("--min-child-weight", type=float, default=1.0)
    parser.add_argument("--reg-lambda", type=float, default=1.0)
    parser.add_argument("--reg-alpha", type=float, default=0.0)
    parser.add_argument("--max-delta-step", type=float, default=0.7)
    parser.add_argument("--early-stopping-rounds", type=int, default=30)
    parser.add_argument("--n-jobs", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    args = parser.parse_args(argv)
    if args.cpu:
        args.device = "cpu"
    if args.seed < 0:
        parser.error("--seed must be non-negative")
    if args.n_estimators < 1:
        parser.error("--n-estimators must be at least 1")
    if args.max_depth < 1:
        parser.error("--max-depth must be at least 1")
    if args.learning_rate <= 0:
        parser.error("--learning-rate must be positive")
    for name in ("subsample", "colsample_bytree"):
        value = getattr(args, name)
        if not 0 < value <= 1:
            parser.error(f"--{name.replace('_', '-')} must be in (0, 1]")
    if args.min_child_weight < 0 or args.reg_lambda < 0 or args.reg_alpha < 0:
        parser.error("regularization/min-child parameters must be non-negative")
    if args.max_delta_step < 0:
        parser.error("--max-delta-step must be non-negative")
    if args.early_stopping_rounds < 0:
        parser.error("--early-stopping-rounds must be non-negative")
    if args.n_jobs < 1:
        parser.error("--n-jobs must be at least 1")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        manifest = run(args)
    except (TrainingError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"[ok] trained {len(manifest['models'])} model variants; "
        f"shared test keys={manifest['shared_test_keys']['selected_test_keys']:,}"
    )
    for record in manifest["model_records"]:
        print(
            f"     {record['variant']}: device={record['device']}, "
            f"train={record['train_rows']:,}, model={record['model_path']}"
        )
    print(f"     metrics={manifest['outputs']['metrics_csv']}")
    print(f"     predictions={manifest['outputs']['predictions_csv']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
