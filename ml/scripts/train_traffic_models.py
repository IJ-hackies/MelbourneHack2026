#!/usr/bin/env python3
"""Train and evaluate source-stratified hourly traffic count models.

The traffic feature builder owns the feature tables and their manifest.  This
module is intentionally a separate boundary: it validates the manifest and
the feature-table semantics, fits one XGBoost Poisson model for each candidate
(``base`` and ``lag_enhanced``) and traffic measurement group, and writes an
offline release bundle.  SCATS intersection totals and Transport Activity
countline volumes are not pooled because they are different measurement
scales.

The manifest parser accepts either a mapping or a list under ``datasets`` and
accepts the common ``feature_columns``/``features``/``feature_list`` spellings.
The semantic contract remains strict: both candidates must describe the same
one-hour test keys and labels, hashes and declared schemas must match the
files, splits must be chronological, and same-hour target-derived columns are
not valid model features.

``--device auto`` attempts CUDA first and falls back to CPU only when that
CUDA attempt fails.  ``--device cuda`` is an explicit CUDA-only request and
never silently falls back.  All fitted artifacts are local/offline outputs;
the final traffic release is a bundle of the winning source-stratified models,
not one model trained across incompatible count scales.  Uncapped full mode
uses pushdown Parquet selections, bounded replay batches, and XGBoost external
memory; its CUDA iterator requires a CUDA-matched CuPy installation.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gc
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

try:  # Keep ``--help`` usable before the ML environment is installed.
    import pyarrow as pa
    import pyarrow.dataset as pads
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised in dependency-error paths.
    pa = None
    pads = None
    pq = None

try:  # XGBoost is declared in ml/requirements.txt and loaded lazily in run().
    import xgboost as xgb
except ImportError:  # pragma: no cover - exercised in dependency-error paths.
    xgb = None

try:  # CuPy is optional; full CUDA external-memory mode needs it.
    import cupy as cp
except ImportError:  # pragma: no cover - exercised in CPU-only environments.
    cp = None


SCRIPT_DIR = Path(__file__).resolve().parent
ML_DIR = SCRIPT_DIR.parent
REPO_DIR = ML_DIR.parent
DEFAULT_MANIFEST = ML_DIR / "traffic" / "training" / "training_manifest.json"
DEFAULT_OUTPUT_DIR = ML_DIR / "traffic" / "training" / "evaluation"

DATASET_NAMES = ("base", "lag_enhanced")
TARGET_COLUMN = "vehicle_count"
SPLITS = ("train", "validation", "test")
OPTIONAL_SPLITS = (*SPLITS, "post_test")
GROUP_SEPARATOR = "|"
DEFAULT_SMALL_ROWS = 2_000
DEFAULT_SMALL_ESTIMATORS = 16
DEFAULT_BATCH_SIZE_ROWS = 65_536
DEFAULT_INSPECTION_BATCH_SIZE_ROWS = 65_536
DEFAULT_MAX_BIN = 256

# The traffic release is intentionally tied to the completed 2024--2026
# feature build.  These are inclusive local-date bounds; post-test rows may be
# retained for provenance but must start after the July 2026 test window.
TRAFFIC_TRAIN_START = dt.date(2024, 1, 1)
TRAFFIC_TRAIN_END = dt.date(2024, 12, 31)
TRAFFIC_VALIDATION_START = dt.date(2025, 1, 1)
TRAFFIC_VALIDATION_END = dt.date(2025, 12, 31)
TRAFFIC_TEST_START = dt.date(2026, 1, 1)
TRAFFIC_TEST_END = dt.date(2026, 7, 31)
TRAFFIC_POST_TEST_START = dt.date(2026, 8, 1)

TRAFFIC_SPLIT_DATE_RANGES: dict[str, tuple[dt.date, dt.date | None]] = {
    "train": (TRAFFIC_TRAIN_START, TRAFFIC_TRAIN_END),
    "validation": (TRAFFIC_VALIDATION_START, TRAFFIC_VALIDATION_END),
    "test": (TRAFFIC_TEST_START, TRAFFIC_TEST_END),
    "post_test": (TRAFFIC_POST_TEST_START, None),
}

# These columns are emitted as same-hour audit/quality lineage by the traffic
# cleaner.  They are useful for diagnosis, but are not available at the
# one-hour-ahead feature_asof and must never become predictors.  Keep the
# explicit names here even though the matcher below also covers their common
# naming patterns, so a future schema rename fails closed.
TARGET_HOUR_QUALITY_FEATURES = frozenset(
    {
        "label_quality",
        "quality_flag",
        "quality_partial_flag",
        "quality_alarm_flag",
        "quality_missing_interval_count",
        "source_row_count",
        "source_record_count",
        "source_timestamp_count",
        "source_interval_count",
        "ta_motor_class_rows",
        "ta_non_motor_class_rows",
        "ta_reported_class_rows",
        "scats_detector_count",
        "scats_detector_row_count",
        "scats_ct_records_min",
        "scats_ct_records_max",
        "scats_alarm_24hour_count",
        "ta_derived_zero",
        "ta_dst_ambiguous_flag",
        "ta_dst_fallback_wrap_flag",
    }
)

BASELINE_ALIASES: dict[str, tuple[str, ...]] = {
    "lag_1h": (
        "vehicle_count_lag_1h",
        "vehicle_count_lag_1",
        "traffic_lag_1h",
        "traffic_lag_1",
        "count_lag_1h",
        "count_lag_1",
        "vehicle_lag_1h",
        "lag_1h",
    ),
    "lag_24h": (
        "vehicle_count_lag_24h",
        "vehicle_count_lag_24",
        "traffic_lag_24h",
        "traffic_lag_24",
        "count_lag_24h",
        "count_lag_24",
        "vehicle_lag_24h",
        "lag_24h",
    ),
    "lag_168h": (
        "vehicle_count_lag_168h",
        "vehicle_count_lag_168",
        "traffic_lag_168h",
        "traffic_lag_168",
        "count_lag_168h",
        "count_lag_168",
        "vehicle_lag_168h",
        "lag_168h",
        "lag_7d",
    ),
}


class TrainingError(RuntimeError):
    """An actionable input, validation, or training failure."""


@dataclass(frozen=True)
class DatasetContract:
    """The manifest contract for one candidate feature table."""

    name: str
    path: Path
    sha256: str
    feature_columns: tuple[str, ...]
    columns: Mapping[str, str]
    horizon_hours: float | None
    time_column: str | None
    declared_schema: Any
    schema_hash: str | None
    baseline_columns: Mapping[str, str]


@dataclass
class TrafficDataset:
    """A validated feature table with normalized audit columns."""

    contract: DatasetContract
    # ``frame`` is populated only by the bounded, one-candidate/source loader
    # during fitting.  The dataset inspection object itself never retains a
    # full candidate Parquet in pandas.
    frame: pd.DataFrame | None
    available_columns: tuple[str, ...]
    horizon_column: str | None
    asof_column: str | None
    resolved_time_column: str
    schema_descriptor: list[dict[str, str]]
    row_count: int
    split_counts: Mapping[str, int]
    split_dates: Mapping[str, Mapping[str, str]]
    group_ids: tuple[str, ...]
    test_index: Mapping[str, tuple[str, str, str, float]]


@dataclass
class _BatchTelemetry:
    """Bounded-read telemetry retained in the final evaluation manifest."""

    requested_batch_rows: int
    batches: int = 0
    rows: int = 0
    max_observed_rows: int = 0
    scanner_calls: int = 0
    cache_reads: int = 0

    def observe(self, rows: int) -> None:
        self.batches += 1
        self.rows += int(rows)
        self.max_observed_rows = max(self.max_observed_rows, int(rows))


@dataclass
class _MetricAccumulator:
    """Sufficient statistics for exact bounded-batch metric aggregation."""

    n: int = 0
    target_sum: float = 0.0
    absolute_error_sum: float = 0.0
    squared_error_sum: float = 0.0
    poisson_deviance_sum: float = 0.0

    def update(self, actual: Sequence[float], predicted: Sequence[float]) -> None:
        y_true = np.asarray(actual, dtype="float64").reshape(-1)
        y_pred = np.asarray(predicted, dtype="float64").reshape(-1)
        if len(y_true) != len(y_pred):
            raise TrainingError("metric accumulator received mismatched actual/prediction lengths")
        valid = np.isfinite(y_true) & np.isfinite(y_pred)
        y_true = y_true[valid]
        y_pred = np.clip(y_pred[valid], 0.0, None)
        if not len(y_true):
            return
        safe_pred = np.maximum(y_pred, 1e-12)
        positive = y_true > 0
        terms = np.zeros_like(y_true)
        terms[positive] = y_true[positive] * (
            np.log(y_true[positive]) - np.log(safe_pred[positive])
        ) - (y_true[positive] - safe_pred[positive])
        terms[~positive] = safe_pred[~positive]
        errors = y_true - y_pred
        self.n += int(len(y_true))
        self.target_sum += float(np.sum(y_true, dtype="float64"))
        self.absolute_error_sum += float(np.sum(np.abs(errors), dtype="float64"))
        self.squared_error_sum += float(np.sum(errors * errors, dtype="float64"))
        self.poisson_deviance_sum += float(np.sum(2.0 * terms, dtype="float64"))

    def bundle(self) -> dict[str, Any]:
        if self.n == 0:
            return {
                "n": 0,
                "target_mean": None,
                "mae": None,
                "rmse": None,
                "poisson_deviance": None,
            }
        return {
            "n": self.n,
            "target_mean": self.target_sum / self.n,
            "mae": self.absolute_error_sum / self.n,
            "rmse": math.sqrt(self.squared_error_sum / self.n),
            "poisson_deviance": self.poisson_deviance_sum / self.n,
        }


@dataclass
class _StreamingEvaluation:
    """Metric state for one candidate/source/split without retaining rows."""

    dataset: TrafficDataset
    candidate: str
    group_id: str
    split: str
    overall: _MetricAccumulator
    baselines: dict[str, _MetricAccumulator]
    baseline_available: dict[str, int]
    baseline_missing: dict[str, int]
    units: dict[str, tuple[str, bool, _MetricAccumulator]]
    seen: dict[str, _MetricAccumulator]
    quality: dict[tuple[str, str], _MetricAccumulator]
    missingness: dict[str, _MetricAccumulator]
    feature_missingness: dict[tuple[str, str], _MetricAccumulator]
    rows: int = 0

    @classmethod
    def create(
        cls,
        dataset: TrafficDataset,
        *,
        candidate: str,
        group_id: str,
        split: str,
    ) -> "_StreamingEvaluation":
        return cls(
            dataset=dataset,
            candidate=candidate,
            group_id=group_id,
            split=split,
            overall=_MetricAccumulator(),
            baselines={lag: _MetricAccumulator() for lag in BASELINE_ALIASES},
            baseline_available={lag: 0 for lag in BASELINE_ALIASES},
            baseline_missing={lag: 0 for lag in BASELINE_ALIASES},
            units={},
            seen={"seen": _MetricAccumulator(), "unseen": _MetricAccumulator()},
            quality={},
            missingness={
                "complete": _MetricAccumulator(),
                "missing": _MetricAccumulator(),
            },
            feature_missingness={
                (feature, state): _MetricAccumulator()
                for feature in dataset.contract.feature_columns
                for state in ("present", "missing")
            },
        )

    def update(self, frame: pd.DataFrame, predictions: np.ndarray, *, train_unit_tokens: set[str]) -> None:
        actual = frame["__target"].to_numpy(dtype="float64")
        self.overall.update(actual, predictions)
        self.rows += len(frame)

        for lag, column in _baseline_columns(self.dataset).items():
            if column is None:
                self.baseline_missing[lag] += len(frame)
                continue
            values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
            valid = np.isfinite(values) & (values >= 0)
            if valid.any():
                self.baselines[lag].update(actual[valid], values[valid])
            self.baseline_available[lag] += int(valid.sum())
            self.baseline_missing[lag] += int((~valid).sum())

        tokens = frame["__unit"].map(_stable_token).to_numpy(dtype=object)
        seen_mask = np.asarray([token in train_unit_tokens for token in tokens], dtype=bool)
        self.seen["seen"].update(actual[seen_mask], predictions[seen_mask])
        self.seen["unseen"].update(actual[~seen_mask], predictions[~seen_mask])
        for token in sorted(set(tokens.tolist())):
            positions = np.flatnonzero(tokens == token)
            if len(positions) == 0:
                continue
            raw_value = str(frame.iloc[int(positions[0])]["__unit"])
            self.units.setdefault(
                str(token),
                (raw_value, bool(token in train_unit_tokens), _MetricAccumulator()),
            )[2].update(actual[positions], predictions[positions])

        for column in (
            "label_quality",
            "quality_flag",
            "quality_partial_flag",
            "quality_alarm_flag",
        ):
            if column not in frame.columns:
                continue
            values = frame[column].map(
                lambda value: "<MISSING>" if _is_missing(value) else str(value)
            ).to_numpy(dtype=object)
            for stratum in sorted(set(values.tolist())):
                mask = values == stratum
                self.quality.setdefault((column, str(stratum)), _MetricAccumulator()).update(
                    actual[mask], predictions[mask]
                )

        missing_count = frame[list(self.dataset.contract.feature_columns)].isna().sum(axis=1)
        missing_group = np.where(missing_count.to_numpy() > 0, "missing", "complete")
        for stratum in ("complete", "missing"):
            mask = missing_group == stratum
            self.missingness[stratum].update(actual[mask], predictions[mask])
        for feature in self.dataset.contract.feature_columns:
            mask = frame[feature].isna().to_numpy(dtype=bool)
            self.feature_missingness[(feature, "present")].update(
                actual[~mask], predictions[~mask]
            )
            self.feature_missingness[(feature, "missing")].update(
                actual[mask], predictions[mask]
            )

    def metric_rows(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        metric = self.overall.bundle()
        baseline_rows: list[dict[str, Any]] = []
        for lag in BASELINE_ALIASES:
            available = self.baseline_available[lag]
            missing = self.baseline_missing[lag]
            baseline_rows.append(
                _metric_row(
                    candidate=self.candidate,
                    dataset_name=self.dataset.contract.name,
                    group_id=self.group_id,
                    split=self.split,
                    scope="baseline",
                    baseline=lag,
                    metric=self.baselines[lag].bundle(),
                    coverage=float(available / (available + missing))
                    if available + missing
                    else 0.0,
                    available_rows=available,
                    missing_rows=missing,
                )
            )
        per_unit_rows: list[dict[str, Any]] = []
        for token in sorted(self.units):
            raw_value, unit_seen, accumulator = self.units[token]
            per_unit_rows.append(
                _metric_row(
                    candidate=self.candidate,
                    dataset_name=self.dataset.contract.name,
                    group_id=self.group_id,
                    split=self.split,
                    scope="per_unit",
                    metric=accumulator.bundle(),
                    unit_id=raw_value,
                    unit_seen_in_train=unit_seen,
                )
            )
        seen_rows = [
            _metric_row(
                candidate=self.candidate,
                dataset_name=self.dataset.contract.name,
                group_id=self.group_id,
                split=self.split,
                scope="unit_seen_group",
                metric=self.seen[name].bundle(),
                unit_id=name,
                unit_seen_in_train=name == "seen",
                stratum_type="unit_group",
                stratum=name,
            )
            for name in ("seen", "unseen")
        ]
        quality_rows: list[dict[str, Any]] = []
        for (column, stratum), accumulator in sorted(self.quality.items()):
            quality_rows.append(
                _metric_row(
                    candidate=self.candidate,
                    dataset_name=self.dataset.contract.name,
                    group_id=self.group_id,
                    split=self.split,
                    scope="quality_stratum",
                    metric=accumulator.bundle(),
                    stratum_type=column,
                    stratum=stratum,
                )
            )
        for stratum in ("complete", "missing"):
            quality_rows.append(
                _metric_row(
                    candidate=self.candidate,
                    dataset_name=self.dataset.contract.name,
                    group_id=self.group_id,
                    split=self.split,
                    scope="missingness_stratum",
                    metric=self.missingness[stratum].bundle(),
                    stratum_type="feature_missingness",
                    stratum=stratum,
                )
            )
        for feature in self.dataset.contract.feature_columns:
            total = self.feature_missingness[(feature, "present")].n + self.feature_missingness[
                (feature, "missing")
            ].n
            for state in ("present", "missing"):
                available = self.feature_missingness[(feature, state)].n
                quality_rows.append(
                    _metric_row(
                        candidate=self.candidate,
                        dataset_name=self.dataset.contract.name,
                        group_id=self.group_id,
                        split=self.split,
                        scope="feature_missingness",
                        metric=self.feature_missingness[(feature, state)].bundle(),
                        stratum_type="feature",
                        stratum=state,
                        feature=feature,
                        coverage=float(available / total) if total else 0.0,
                        available_rows=available,
                    )
                )
        return metric, baseline_rows, per_unit_rows, seen_rows + quality_rows


@dataclass
class FeatureEncoder:
    """Train-only native categorical encoding with an unseen-unit branch."""

    feature_columns: list[str]
    categorical_columns: list[str]
    categories: dict[str, list[str]]
    train_unit_tokens: set[str]
    unit_column: str

    @classmethod
    def fit(
        cls,
        frame: pd.DataFrame,
        feature_columns: Sequence[str],
        *,
        unit_column: str,
    ) -> "FeatureEncoder":
        categorical_columns: list[str] = []
        categories: dict[str, list[str]] = {}
        for column in feature_columns:
            series = frame[column]
            force_categorical = column == unit_column or not (
                pd.api.types.is_numeric_dtype(series)
                or pd.api.types.is_bool_dtype(series)
            )
            if force_categorical:
                categorical_columns.append(column)
                tokens = {
                    _stable_token(value)
                    for value in series.tolist()
                    if not _is_missing(value)
                }
                categories[column] = sorted(tokens)

        train_unit_tokens = {
            _stable_token(value)
            for value in frame[unit_column].tolist()
            if not _is_missing(value)
        }
        return cls(
            feature_columns=list(feature_columns),
            categorical_columns=categorical_columns,
            categories=categories,
            train_unit_tokens=train_unit_tokens,
            unit_column=unit_column,
        )

    @classmethod
    def fit_stream(
        cls,
        frames: Iterable[pd.DataFrame],
        feature_columns: Sequence[str],
        *,
        unit_column: str,
    ) -> "FeatureEncoder":
        """Fit category vocabularies from bounded frames only.

        Traffic unit vocabularies are small compared with the row count.  The
        rows themselves are deliberately not retained; the source iterator
        can be replayed from Parquet for model construction.
        """

        categorical_columns: list[str] = []
        category_sets: dict[str, set[str]] = {}
        train_unit_tokens: set[str] = set()
        saw_rows = False
        for frame in frames:
            if frame.empty:
                continue
            saw_rows = True
            if not categorical_columns:
                for column in feature_columns:
                    series = frame[column]
                    force_categorical = column == unit_column or not (
                        pd.api.types.is_numeric_dtype(series)
                        or pd.api.types.is_bool_dtype(series)
                    )
                    if force_categorical:
                        categorical_columns.append(column)
                        category_sets[column] = set()
            for column in categorical_columns:
                category_sets[column].update(
                    _stable_token(value)
                    for value in frame[column].tolist()
                    if not _is_missing(value)
                )
            train_unit_tokens.update(
                _stable_token(value)
                for value in frame[unit_column].tolist()
                if not _is_missing(value)
            )
        if not saw_rows:
            raise TrainingError("cannot fit a traffic feature encoder on an empty stream")
        return cls(
            feature_columns=list(feature_columns),
            categorical_columns=categorical_columns,
            categories={column: sorted(values) for column, values in category_sets.items()},
            train_unit_tokens=train_unit_tokens,
            unit_column=unit_column,
        )

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return stable XGBoost-compatible numeric/categorical features."""

        transformed: dict[str, pd.Series] = {}
        for column in self.feature_columns:
            series = frame[column]
            if column in self.categorical_columns:
                tokens = series.map(
                    lambda value: None if _is_missing(value) else _stable_token(value)
                )
                transformed[column] = pd.Series(
                    pd.Categorical(tokens, categories=self.categories[column]),
                    index=frame.index,
                    name=column,
                )
                if column == self.unit_column:
                    is_missing = series.map(_is_missing).astype(bool)
                    is_unseen = (~is_missing) & (~tokens.isin(self.train_unit_tokens))
                    transformed[f"{self.unit_column}__unseen"] = is_unseen.astype(
                        "float32"
                    )
            else:
                transformed[column] = pd.to_numeric(
                    series, errors="coerce"
                ).astype("float32")
        return pd.DataFrame(transformed, index=frame.index)

    def model_feature_columns(self) -> list[str]:
        columns = list(self.feature_columns)
        if self.unit_column in self.categorical_columns:
            index = columns.index(self.unit_column) + 1
            columns.insert(index, f"{self.unit_column}__unseen")
        return columns

    def model_feature_types(self) -> list[str]:
        """Return deterministic XGBoost feature types for array iterators."""

        feature_types: list[str] = []
        for column in self.feature_columns:
            feature_types.append("c" if column in self.categorical_columns else "float")
            if column == self.unit_column and column in self.categorical_columns:
                feature_types.append("float")
        return feature_types

    def transform_numeric(self, frame: pd.DataFrame) -> np.ndarray:
        """Encode one bounded frame as float32/native-categorical columns.

        Category codes are fit from training rows and marked as categorical in
        the XGBoost DMatrix.  Missing and unseen categories use NaN plus the
        existing explicit unseen-unit indicator, so this is deterministic and
        preserves native categorical split semantics without pandas object
        frames or device-sized host copies.
        """

        columns: list[np.ndarray] = []
        for column in self.feature_columns:
            series = frame[column]
            if column in self.categorical_columns:
                tokens = series.map(
                    lambda value: None if _is_missing(value) else _stable_token(value)
                )
                codes = pd.Categorical(tokens, categories=self.categories[column]).codes
                encoded = codes.astype("float32", copy=False)
                encoded[codes < 0] = np.nan
                columns.append(encoded)
                if column == self.unit_column:
                    is_missing = series.map(_is_missing).to_numpy(dtype=bool)
                    is_unseen = np.asarray(
                        [
                            (not missing) and token not in self.train_unit_tokens
                            for token, missing in zip(tokens.tolist(), is_missing)
                        ],
                        dtype="float32",
                    )
                    columns.append(is_unseen)
            else:
                columns.append(
                    pd.to_numeric(series, errors="coerce").to_numpy(
                        dtype="float32", na_value=np.nan
                    )
                )
        if not columns:
            return np.empty((len(frame), 0), dtype="float32")
        return np.column_stack(columns).astype("float32", copy=False)

    def metadata(self) -> dict[str, Any]:
        return {
            "encoding": "xgboost_native_categorical",
            "feature_columns": list(self.feature_columns),
            "model_feature_columns": self.model_feature_columns(),
            "categorical_columns": list(self.categorical_columns),
            "categories": self.categories,
            "model_feature_types": self.model_feature_types(),
            "unit_column": self.unit_column,
            "train_unit_count": len(self.train_unit_tokens),
            "train_unit_tokens": sorted(self.train_unit_tokens),
            "unseen_unit_policy": (
                "map unknown unit categories to the native missing branch and add "
                f"{self.unit_column}__unseen=1"
            ),
        }


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if np.isscalar(result) else False
    except (TypeError, ValueError):
        return False


def _stable_token(value: Any) -> str:
    """Represent a category deterministically across processes."""

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


def _key_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _normalise(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


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


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    data = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=_json_default,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_bytes(path, data)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        frame.to_csv(temporary, index=False, na_rep="")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    if pa is None or pq is None:
        raise TrainingError("pyarrow is required to write traffic predictions.parquet")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, temporary, compression="none")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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
    raw = str(value).strip()
    candidates: list[Path] = [Path(raw).expanduser()]
    if "\\" in raw:
        candidates.append(Path(raw.replace("\\", os.sep)).expanduser())
    if "MelbourneHack2026" in raw:
        suffix = raw.split("MelbourneHack2026", 1)[1].lstrip("\\/")
        candidates.append(REPO_DIR / suffix.replace("\\", os.sep))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    for candidate in candidates:
        if candidate.is_absolute():
            continue
        for base in (manifest_path.parent, REPO_DIR, Path.cwd()):
            resolved = (base / candidate).resolve()
            if resolved.exists():
                return resolved
    return candidates[0].resolve()


def _mapping_value(container: Any, keys: Sequence[str]) -> Any:
    if not isinstance(container, Mapping):
        return None
    for key in keys:
        if key in container:
            value = container[key]
            if isinstance(value, Mapping):
                for nested in ("column", "name", "value", "hours"):
                    if nested in value:
                        return value[nested]
            return value
    return None


def _semantic_column(
    semantic: str,
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> str:
    aliases = {
        "observation_key": ("observation_key", "key_column", "observation_id"),
        "split": ("split", "split_column", "dataset_split", "partition"),
        "observation_unit_id": (
            "observation_unit_id",
            "unit_column",
            "observation_unit",
            "unit_id",
        ),
        "label_source": ("label_source", "source_column", "source"),
        "measurement_scope": (
            "measurement_scope",
            "scope_column",
            "measurement_unit",
        ),
    }
    containers: list[Any] = [entry.get("columns")]
    containers.append(entry)
    containers.extend((manifest.get("columns"), manifest))
    value = None
    for container in containers:
        value = _mapping_value(container, aliases[semantic])
        if value is not None:
            break
    if value is None:
        value = semantic
    if not isinstance(value, str) or not value.strip():
        raise TrainingError(f"manifest semantic column {semantic!r} must be a non-empty string")
    return value.strip()


def _extract_hash(entry: Mapping[str, Any]) -> str:
    raw = None
    for key in (
        "sha256",
        "hash",
        "checksum",
        "dataset_sha256",
        "file_sha256",
    ):
        if key in entry:
            raw = entry[key]
            break
    if isinstance(raw, Mapping):
        raw = _mapping_value(raw, ("sha256", "hash", "value"))
    if not isinstance(raw, str):
        raise TrainingError("each traffic dataset must declare a file SHA-256 hash")
    value = raw.strip().lower()
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise TrainingError(f"invalid dataset SHA-256 hash: {raw!r}")
    return value


def _extract_feature_list(
    name: str,
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = None
    for key in ("feature_columns", "features", "feature_list", "predictors", "used_features"):
        if key in entry:
            raw = entry[key]
            break
    if raw is None:
        feature_map = manifest.get("feature_lists") or manifest.get("features")
        if isinstance(feature_map, Mapping):
            raw = feature_map.get(name)
    if isinstance(raw, Mapping):
        raw = _mapping_value(raw, ("columns", "feature_columns", "values"))
    if not isinstance(raw, list) or not raw:
        raise TrainingError(f"datasets.{name} must declare a non-empty feature list")
    values = tuple(str(value).strip() for value in raw)
    if any(not value for value in values):
        raise TrainingError(f"datasets.{name} feature list contains an empty name")
    if len(set(values)) != len(values):
        raise TrainingError(f"datasets.{name} feature list contains duplicate names")
    return values


def _extract_horizon(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> float | None:
    target_nodes = [entry.get("target"), entry, manifest.get("target"), manifest]
    for node in target_nodes:
        value = _mapping_value(
            node,
            (
                "prediction_horizon_hours",
                "horizon_hours",
                "prediction_horizon",
                "horizon",
            ),
        )
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise TrainingError(f"one-hour traffic horizon is not numeric: {value!r}") from exc
        if not math.isfinite(numeric):
            raise TrainingError("traffic prediction horizon must be finite")
        return numeric
    return None


def _extract_schema(entry: Mapping[str, Any], manifest: Mapping[str, Any], name: str) -> Any:
    if "schema" in entry:
        return entry["schema"]
    raw = manifest.get("schema")
    if isinstance(raw, Mapping) and name in raw:
        return raw[name]
    return raw


def _extract_schema_hash(entry: Mapping[str, Any], manifest: Mapping[str, Any]) -> str | None:
    for node in (entry, manifest):
        raw = _mapping_value(node, ("schema_hash", "schema_sha256"))
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise TrainingError("schema_hash must be a SHA-256 string")
        value = raw.lower().removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise TrainingError(f"invalid schema hash: {raw!r}")
        return value
    return None


def _extract_baseline_columns(entry: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, str]:
    raw = entry.get("baseline_columns")
    if raw is None:
        raw = manifest.get("baseline_columns")
        if isinstance(raw, Mapping) and any(name in raw for name in DATASET_NAMES):
            raw = raw.get(str(entry.get("name", "")))
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for lag in BASELINE_ALIASES:
        value = raw.get(lag)
        if isinstance(value, str) and value.strip():
            result[lag] = value.strip()
    return result


def _dataset_entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = manifest.get("datasets")
    if raw is None:
        raw = manifest.get("candidates")
    if raw is None:
        raw = {name: manifest.get(name) for name in DATASET_NAMES}

    entries: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                continue
            entry = dict(value)
            entry.setdefault("name", str(key))
            entries[str(entry["name"])] = entry
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, Mapping):
                continue
            entry = dict(value)
            name = entry.get("name") or entry.get("dataset") or entry.get("candidate")
            if name is not None:
                entries[str(name)] = entry
    else:
        raise TrainingError("traffic training manifest datasets must be a mapping or list")
    missing = [name for name in DATASET_NAMES if name not in entries]
    if missing:
        raise TrainingError(f"traffic training manifest is missing datasets: {missing}")
    return entries


def _parse_manifest(path: Path) -> tuple[dict[str, Any], dict[str, DatasetContract]]:
    if not path.exists():
        raise TrainingError(f"traffic training manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"could not read traffic training manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise TrainingError("traffic training manifest must be a JSON object")

    target_nodes = [payload.get("target"), payload]
    target_column = None
    for node in target_nodes:
        value = _mapping_value(node, ("target_column", "column"))
        if value is not None:
            target_column = str(value)
            break
    if target_column != TARGET_COLUMN:
        raise TrainingError(
            f"traffic target_column must be {TARGET_COLUMN!r}, got {target_column!r}"
        )

    contracts: dict[str, DatasetContract] = {}
    for name, entry in _dataset_entries(payload).items():
        entry_target = _mapping_value(entry, ("target_column",))
        if entry_target is not None and str(entry_target) != TARGET_COLUMN:
            raise TrainingError(
                f"datasets.{name}.target_column must be {TARGET_COLUMN!r}, got {entry_target!r}"
            )
        path_value = entry.get("path") or entry.get("dataset_path") or entry.get("file")
        if not isinstance(path_value, str) or not path_value.strip():
            raise TrainingError(f"datasets.{name}.path is empty")
        feature_columns = _extract_feature_list(name, entry, payload)
        _validate_feature_columns(name, feature_columns)
        columns = {
            semantic: _semantic_column(semantic, entry, payload)
            for semantic in (
                "observation_key",
                "split",
                "observation_unit_id",
                "label_source",
                "measurement_scope",
            )
        }
        if len(set(columns.values())) != len(columns):
            raise TrainingError(f"datasets.{name} semantic columns must be distinct")
        time_column = entry.get("time_column") or entry.get("timestamp_column")
        if time_column is not None and not isinstance(time_column, str):
            raise TrainingError(f"datasets.{name}.time_column must be a string")
        contracts[name] = DatasetContract(
            name=name,
            path=_resolve_path(path_value, manifest_path=path),
            sha256=_extract_hash(entry),
            feature_columns=feature_columns,
            columns=columns,
            horizon_hours=_extract_horizon(entry, payload),
            time_column=time_column.strip() if isinstance(time_column, str) else None,
            declared_schema=_extract_schema(entry, payload, name),
            schema_hash=_extract_schema_hash(entry, payload),
            baseline_columns=_extract_baseline_columns(entry, payload),
        )
    return dict(payload), contracts


def _validate_feature_columns(name: str, columns: Sequence[str]) -> None:
    forbidden_exact = {
        "observation_key",
        "split",
        "local_date",
        "observed_at_local",
        "local_timestamp",
        "hour_start_utc",
        "feature_asof_utc",
        "feature_asof_local",
        "label_source",
        "measurement_scope",
        "source_dataset_id",
        "source_record_id",
        TARGET_COLUMN,
        "log1p_vehicle_count",
        "intersection_total",
    }
    forbidden: list[str] = []
    for column in columns:
        normalized = _normalise(column)
        if normalized in {
            _normalise(value)
            for value in (*forbidden_exact, *TARGET_HOUR_QUALITY_FEATURES)
        }:
            forbidden.append(column)
            continue
        if _looks_like_same_hour_target_feature(normalized):
            forbidden.append(column)
    if forbidden:
        raise TrainingError(
            f"{name} feature list contains target-derived/audit columns: {sorted(set(forbidden))}"
        )


def _looks_like_same_hour_quality_feature(normalized: str) -> bool:
    """Reject same-hour source/quality summaries while allowing static identity."""

    if normalized in {_normalise(value) for value in TARGET_HOUR_QUALITY_FEATURES}:
        return True
    tokens = set(normalized.split("_"))
    summary_tokens = {
        "count",
        "counts",
        "row",
        "rows",
        "record",
        "records",
        "flag",
        "min",
        "max",
        "mean",
        "total",
        "sum",
    }
    if "quality" in tokens:
        return True
    if {"derived", "zero"}.issubset(tokens):
        return True
    if "dst" in tokens and tokens.intersection(
        {"ambiguous", "fallback", "wrap", "quality", "flag"}
    ):
        return True
    if "alarm" in tokens and tokens.intersection(summary_tokens):
        return True
    if "detector" in tokens and tokens.intersection(summary_tokens):
        return True
    if "class" in tokens and tokens.intersection(summary_tokens):
        return True
    if "source" in tokens and tokens.intersection(
        {"row", "rows", "record", "records", "timestamp", "interval"}
    ) and tokens.intersection(summary_tokens):
        return True
    return False


def _looks_like_same_hour_target_feature(normalized: str) -> bool:
    """Reject current-hour target proxies while permitting past-only lags."""

    if _looks_like_same_hour_quality_feature(normalized):
        return True

    tokens = set(normalized.split("_"))
    has_target_term = any(
        term in normalized
        for term in ("vehicle_count", "traffic_count", "vehicle_volume", "traffic_volume", "intersection_total")
    ) or "target" in tokens
    if not has_target_term:
        return False
    prior_markers = {
        "lag",
        "past",
        "previous",
        "prior",
        "history",
        "shift",
        "tminus",
        "asof",
        "delayed",
    }
    # A target-named feature is accepted only when its name explicitly says
    # that it comes from a prior observation.  In particular, a bare
    # ``vehicle_count_*`` suffix must not become an accidental same-hour
    # predictor just because it does not match one of the known proxy names.
    if "rolling" in tokens and not tokens.intersection(
        {"past", "previous", "prior", "history"}
    ):
        return True
    if not tokens.intersection(prior_markers):
        return True
    if tokens.intersection({"current", "same", "observed", "now", "future", "next"}):
        return True
    return False


def _schema_descriptor(schema: Any) -> list[dict[str, str]]:
    return [{"name": field.name, "type": str(field.type)} for field in schema]


def _schema_hash(descriptor: Sequence[Mapping[str, str]]) -> str:
    encoded = json.dumps(
        list(descriptor), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _declared_schema_names(declared: Any) -> list[str]:
    if declared is None:
        return []
    if isinstance(declared, Mapping):
        if "fields" in declared:
            return _declared_schema_names(declared["fields"])
        if "columns" in declared and isinstance(declared["columns"], (Mapping, list)):
            return _declared_schema_names(declared["columns"])
        return [str(key) for key in declared if str(key) not in {"schema_version", "hash"}]
    if isinstance(declared, list):
        names: list[str] = []
        for item in declared:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("column")
                if name is not None:
                    names.append(str(name))
            elif isinstance(item, str):
                names.append(item)
        return names
    return []


def _validate_declared_schema(
    contract: DatasetContract,
    available: Sequence[str],
    descriptor: Sequence[Mapping[str, str]],
) -> None:
    available_set = set(available)
    declared_names = _declared_schema_names(contract.declared_schema)
    missing = sorted(set(declared_names) - available_set)
    if missing:
        raise TrainingError(
            f"{contract.name} Parquet schema is missing manifest-declared columns: {missing}"
        )
    if contract.schema_hash is not None:
        actual = _schema_hash(descriptor)
        if actual != contract.schema_hash:
            raise TrainingError(
                f"{contract.name} schema hash mismatch: expected {contract.schema_hash}, got {actual}"
            )


def _parse_date(value: Any, *, label: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        parsed = pd.to_datetime(value, errors="raise")
    except (TypeError, ValueError) as exc:
        raise TrainingError(f"{label} is not an ISO date/datetime: {value!r}") from exc
    if isinstance(parsed, pd.Timestamp):
        return parsed.date()
    raise TrainingError(f"{label} is not an ISO date/datetime: {value!r}")


def _date_series(frame: pd.DataFrame, *, time_column: str) -> pd.Series:
    if "local_date" in frame.columns and frame["local_date"].notna().all():
        values = pd.to_datetime(frame["local_date"], errors="coerce")
    else:
        values = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
    if values.isna().any():
        raise TrainingError(f"time column {time_column!r} contains unparseable values")
    return values.dt.date


def _resolve_time_column(contract: DatasetContract, available: Sequence[str]) -> str:
    available_set = set(available)
    if contract.time_column:
        if contract.time_column not in available_set:
            raise TrainingError(
                f"{contract.name} time_column {contract.time_column!r} is absent from Parquet schema"
            )
        return contract.time_column
    for candidate in (
        "local_date",
        "observed_at_local",
        "local_timestamp",
        "hour_start_utc",
        "timestamp",
        "observed_at",
        "date",
    ):
        if candidate in available_set:
            return candidate
    if {"year", "month", "day"}.issubset(available_set):
        return "__ymd__"
    raise TrainingError(
        f"{contract.name} has no date/timestamp column for chronological split validation"
    )


def _resolve_asof_column(available: Sequence[str]) -> str | None:
    for candidate in (
        "feature_asof_utc",
        "feature_asof_local",
        "feature_asof",
        "asof_timestamp",
        "as_of",
    ):
        if candidate in available:
            return candidate
    return None


def _resolve_observed_time_column(
    available: Sequence[str], time_column: str
) -> str | None:
    for candidate in (
        "hour_start_utc",
        "observed_at_local",
        "local_timestamp",
        "observed_at",
        "timestamp",
    ):
        if candidate in available:
            return candidate
    return None if time_column in {"local_date", "__ymd__"} else time_column


def _dataset_read_columns(
    contract: DatasetContract,
    available: Sequence[str],
    *,
    time_column: str,
    horizon_column: str | None,
    asof_column: str | None,
    observed_time_column: str | None,
    inspection: bool,
) -> list[str]:
    """Return a narrow, stable column projection for batch reads."""

    required = {TARGET_COLUMN, *contract.columns.values()}
    if not inspection:
        required.update(contract.feature_columns)
    if time_column != "__ymd__":
        required.add(time_column)
    if "local_date" in available:
        # Preserve Melbourne calendar semantics when a separate UTC timestamp
        # is also present.
        required.add("local_date")
    if time_column == "__ymd__":
        required.update(("year", "month", "day"))
    if horizon_column:
        required.add(horizon_column)
    if asof_column:
        required.add(asof_column)
    if observed_time_column:
        required.add(observed_time_column)
    if not inspection:
        required.update(
            {
                "label_quality",
                "quality_flag",
                "quality_partial_flag",
                "quality_alarm_flag",
                "quality_missing_interval_count",
                "source_dataset_id",
            }
        )
        required.update(
            column
            for aliases in BASELINE_ALIASES.values()
            for column in aliases
        )
        required.update(contract.baseline_columns.values())
    return [column for column in available if column in required]


def _normalise_batch(
    batch: pd.DataFrame,
    contract: DatasetContract,
    *,
    time_column: str,
    group_id: str | None = None,
) -> pd.DataFrame:
    """Normalize one Arrow batch without retaining the source batch."""

    frame = batch.copy()
    key_column = contract.columns["observation_key"]
    split_column = contract.columns["split"]
    unit_column = contract.columns["observation_unit_id"]
    source_column = contract.columns["label_source"]
    scope_column = contract.columns["measurement_scope"]
    frame["__key"] = frame[key_column].map(_key_text)
    frame["__split"] = frame[split_column].map(
        lambda value: "" if _is_missing(value) else str(value).strip().lower()
    )
    frame["__unit"] = frame[unit_column].map(_key_text)
    frame["__label_source"] = frame[source_column].map(
        lambda value: "" if _is_missing(value) else str(value).strip()
    )
    frame["__measurement_scope"] = frame[scope_column].map(
        lambda value: "" if _is_missing(value) else str(value).strip()
    )
    frame["__group"] = [
        _group_id(source, scope)
        for source, scope in zip(frame["__label_source"], frame["__measurement_scope"])
    ]
    target = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce")
    frame["__target"] = target.astype("float64")
    if time_column == "__ymd__":
        frame["__local_date"] = pd.to_datetime(
            frame[["year", "month", "day"]], errors="coerce"
        ).dt.date
    else:
        frame["__local_date"] = _date_series(frame, time_column=time_column)
    if group_id is not None:
        frame = frame.loc[frame["__group"] == group_id]
    return frame


def _validate_batch(
    frame: pd.DataFrame,
    contract: DatasetContract,
    *,
    horizon_column: str | None,
    asof_column: str | None,
    observed_time_column: str | None,
) -> None:
    """Validate row-level semantics on a bounded Arrow batch."""

    canonical_columns = (
        (contract.columns["split"], "__split", "lower-case with no surrounding whitespace"),
        (contract.columns["label_source"], "__label_source", "with no surrounding whitespace"),
        (
            contract.columns["measurement_scope"],
            "__measurement_scope",
            "with no surrounding whitespace",
        ),
    )
    for raw_column, normalized_column, requirement in canonical_columns:
        raw_values = frame[raw_column]
        normalized_values = frame[normalized_column]
        mismatched = [
            (raw, normalized)
            for raw, normalized in zip(raw_values.tolist(), normalized_values.tolist())
            if not isinstance(raw, str) or raw != normalized
        ]
        if mismatched:
            raw, normalized = mismatched[0]
            raise TrainingError(
                f"{contract.name} {raw_column!r} must use canonical raw values "
                f"{requirement} for exact Parquet pushdown; got {raw!r}, "
                f"canonical value {normalized!r}"
            )

    if frame["__key"].eq("").any():
        raise TrainingError(f"{contract.name} contains a missing observation_key")
    invalid_splits = sorted(set(frame["__split"]) - set(OPTIONAL_SPLITS))
    if invalid_splits:
        raise TrainingError(f"{contract.name} has unknown split labels: {invalid_splits}")
    target = frame["__target"].to_numpy(dtype="float64")
    if (~np.isfinite(target)).any():
        raise TrainingError(f"{contract.name} target {TARGET_COLUMN!r} contains null/non-finite values")
    if (target < 0).any():
        raise TrainingError(f"{contract.name} target {TARGET_COLUMN!r} contains negative counts")
    for semantic, column in (
        ("observation_unit_id", "__unit"),
        ("label_source", "__label_source"),
        ("measurement_scope", "__measurement_scope"),
    ):
        if frame[column].eq("").any():
            raise TrainingError(f"{contract.name} {semantic} contains null/empty values")
    if frame["__local_date"].isna().any():
        raise TrainingError(f"{contract.name} has null chronological dates")
    if horizon_column:
        horizon = pd.to_numeric(frame[horizon_column], errors="coerce").to_numpy(dtype="float64")
        if (~np.isfinite(horizon)).any() or not np.all(horizon == 1):
            raise TrainingError(
                f"{contract.name} {horizon_column!r} must contain only the one-hour horizon value 1"
            )
    if asof_column and observed_time_column:
        observed = pd.to_datetime(frame[observed_time_column], errors="coerce", utc=True)
        asof = pd.to_datetime(frame[asof_column], errors="coerce", utc=True)
        delta_hours = (observed - asof).dt.total_seconds() / 3600.0
        if delta_hours.isna().any() or not np.allclose(
            delta_hours.to_numpy(dtype="float64"), 1.0, rtol=0.0, atol=1e-9
        ):
            raise TrainingError(
                f"{contract.name} feature as-of timestamps must be exactly one hour before the target hour"
            )


class _DiskInspectionStore:
    """SQLite-backed key/alignment state for uncapped inspection.

    The trainer must validate duplicate observation keys and exact candidate
    test alignment, but retaining tens of millions of Python strings is not a
    viable contract.  This store keeps that state on disk with a batch-sized
    Python working set.  It is created only for uncapped full mode and is
    removed before model fitting begins.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=120.0)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute("PRAGMA mmap_size=0")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS all_keys (
                dataset TEXT NOT NULL,
                observation_key TEXT NOT NULL,
                PRIMARY KEY (dataset, observation_key)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS test_rows (
                dataset TEXT NOT NULL,
                observation_key TEXT NOT NULL,
                label_source TEXT NOT NULL,
                measurement_scope TEXT NOT NULL,
                observation_unit_id TEXT NOT NULL,
                vehicle_count REAL NOT NULL,
                PRIMARY KEY (dataset, observation_key)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS test_rows_key_idx
                ON test_rows (observation_key, dataset);
            """
        )
        self.connection.commit()

    def add_batch(self, dataset_name: str, frame: pd.DataFrame) -> None:
        keys = ((dataset_name, str(value)) for value in frame["__key"].tolist())
        try:
            self.connection.executemany(
                "INSERT INTO all_keys(dataset, observation_key) VALUES (?, ?)", keys
            )
        except sqlite3.IntegrityError as exc:
            raise TrainingError(
                f"{dataset_name} observation_key is not unique (disk-backed validation)"
            ) from exc
        test_rows = frame.loc[
            frame["__split"] == "test",
            ["__key", "__label_source", "__measurement_scope", "__unit", "__target"],
        ]
        try:
            self.connection.executemany(
                """
                INSERT INTO test_rows(
                    dataset, observation_key, label_source, measurement_scope,
                    observation_unit_id, vehicle_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        dataset_name,
                        str(key),
                        str(source),
                        str(scope),
                        str(unit),
                        float(target),
                    )
                    for key, source, scope, unit, target in test_rows.itertuples(
                        index=False, name=None
                    )
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TrainingError(
                f"{dataset_name} test observation_key is not unique (disk-backed validation)"
            ) from exc
        self.connection.commit()

    def groups(self, dataset_name: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT label_source, measurement_scope
            FROM test_rows
            WHERE dataset = ?
            ORDER BY label_source, measurement_scope
            """,
            (dataset_name,),
        )
        return [_group_id(source, scope) for source, scope in rows]

    def validate_alignment(self) -> dict[str, Any]:
        connection = self.connection
        base_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM test_rows WHERE dataset = 'base'"
            ).fetchone()[0]
        )
        lag_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM test_rows WHERE dataset = 'lag_enhanced'"
            ).fetchone()[0]
        )
        shared_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM test_rows AS base
                INNER JOIN test_rows AS lag
                  ON lag.observation_key = base.observation_key
                WHERE base.dataset = 'base' AND lag.dataset = 'lag_enhanced'
                """
            ).fetchone()[0]
        )
        base_only = base_count - shared_count
        lag_only = lag_count - shared_count
        if base_only or lag_only:
            raise TrainingError(
                "base and lag_enhanced test observation_key sets differ: "
                f"base_only={base_only}, lag_enhanced_only={lag_only}"
            )
        if not shared_count:
            raise TrainingError("candidate datasets have no shared test observation_key values")
        mismatch = connection.execute(
            """
            SELECT base.observation_key
            FROM test_rows AS base
            INNER JOIN test_rows AS lag
              ON lag.observation_key = base.observation_key
            WHERE base.dataset = 'base' AND lag.dataset = 'lag_enhanced'
              AND (
                base.label_source <> lag.label_source
                OR base.measurement_scope <> lag.measurement_scope
                OR base.observation_unit_id <> lag.observation_unit_id
                OR base.vehicle_count <> lag.vehicle_count
              )
            LIMIT 3
            """
        ).fetchall()
        if mismatch:
            raise TrainingError(
                "candidate test keys have different labels or source assignments: "
                f"{[row[0] for row in mismatch]}"
            )
        base_groups = set(self.groups("base"))
        lag_groups = set(self.groups("lag_enhanced"))
        if base_groups != lag_groups:
            raise TrainingError("candidate test keys do not expose the same source/measurement groups")
        return {
            "base_test_keys": base_count,
            "lag_enhanced_test_keys": lag_count,
            "shared_test_keys": shared_count,
            "exact_match": True,
            "label_match": True,
            "group_match": True,
            "intersection_policy": "strict_exact",
            "alignment_storage": "sqlite_disk_backed",
        }

    def close(self) -> None:
        self.connection.close()


def _inspect_dataset(
    contract: DatasetContract,
    *,
    inspection_store: _DiskInspectionStore | None = None,
    batch_size: int = DEFAULT_INSPECTION_BATCH_SIZE_ROWS,
) -> TrafficDataset:
    """Validate a candidate by streaming Arrow batches, not a full pandas read."""

    if pa is None or pq is None:
        raise TrainingError("pyarrow is required to load traffic Parquet feature tables")
    if not contract.path.exists():
        raise TrainingError(f"{contract.name} dataset not found: {contract.path}")
    actual_hash = _sha256(contract.path)
    if actual_hash != contract.sha256:
        raise TrainingError(
            f"{contract.name} dataset hash mismatch: expected {contract.sha256}, got {actual_hash}"
        )
    try:
        parquet = pq.ParquetFile(contract.path)
        schema = parquet.schema_arrow
    except (OSError, ValueError, ImportError) as exc:
        raise TrainingError(f"could not read {contract.name} Parquet schema: {exc}") from exc
    available = tuple(schema.names)
    descriptor = _schema_descriptor(schema)
    _validate_declared_schema(contract, available, descriptor)
    time_column = _resolve_time_column(contract, available)
    asof_column = _resolve_asof_column(available)
    observed_time_column = _resolve_observed_time_column(available, time_column)
    horizon_column = next(
        (
            candidate
            for candidate in (
                "prediction_horizon_hours",
                "horizon_hours",
                "prediction_horizon",
            )
            if candidate in available
        ),
        None,
    )
    required = set(contract.feature_columns) | set(contract.columns.values()) | {TARGET_COLUMN}
    if time_column != "__ymd__":
        required.add(time_column)
    if horizon_column:
        required.add(horizon_column)
    if asof_column:
        required.add(asof_column)
    if time_column == "__ymd__":
        required.update(("year", "month", "day"))
    if "local_date" in available:
        required.add("local_date")
    missing = sorted(required - set(available))
    if missing:
        raise TrainingError(f"{contract.name} dataset is missing required columns: {missing}")
    if contract.horizon_hours is not None and contract.horizon_hours != 1:
        raise TrainingError(
            f"{contract.name} declares prediction horizon {contract.horizon_hours}; traffic requires exactly 1 hour"
        )

    read_columns = _dataset_read_columns(
        contract,
        available,
        time_column=time_column,
        horizon_column=horizon_column,
        asof_column=asof_column,
        observed_time_column=observed_time_column,
        inspection=True,
    )
    seen_keys: set[str] | None = set() if inspection_store is None else None
    test_index: dict[str, tuple[str, str, str, float]] = {}
    group_ids: set[str] = set()
    split_counts = {split: 0 for split in OPTIONAL_SPLITS}
    split_min: dict[str, dt.date] = {}
    split_max: dict[str, dt.date] = {}
    row_count = 0
    try:
        batches = parquet.iter_batches(columns=read_columns, batch_size=batch_size)
        for arrow_batch in batches:
            frame = _normalise_batch(
                arrow_batch.to_pandas(), contract, time_column=time_column
            )
            _validate_batch(
                frame,
                contract,
                horizon_column=horizon_column,
                asof_column=asof_column,
                observed_time_column=observed_time_column,
            )
            if inspection_store is not None:
                inspection_store.add_batch(contract.name, frame)
            else:
                assert seen_keys is not None
                keys = frame["__key"].tolist()
                duplicate = set(keys).intersection(seen_keys)
                if duplicate:
                    raise TrainingError(
                        f"{contract.name} observation_key is not unique: {sorted(duplicate)[:3]}"
                    )
                seen_keys.update(keys)
            row_count += len(frame)
            for split, values in frame.groupby("__split", sort=False):
                split_counts[split] += len(values)
                split_min[split] = min(split_min.get(split, values["__local_date"].min()), values["__local_date"].min())
                split_max[split] = max(split_max.get(split, values["__local_date"].max()), values["__local_date"].max())
            group_ids.update(frame["__group"].unique().tolist())
            if inspection_store is None:
                test_rows = frame.loc[
                    frame["__split"] == "test",
                    ["__key", "__label_source", "__measurement_scope", "__unit", "__target"],
                ]
                for row in test_rows.itertuples(index=False, name=None):
                    key, source, scope, unit, target = row
                    test_index[str(key)] = (str(source), str(scope), str(unit), float(target))
    except (OSError, ValueError, ImportError) as exc:
        raise TrainingError(f"could not stream {contract.name} dataset {contract.path}: {exc}") from exc
    if row_count == 0:
        raise TrainingError(f"{contract.name} dataset is empty: {contract.path}")
    missing_splits = [split for split in SPLITS if split_counts[split] == 0]
    if missing_splits:
        raise TrainingError(f"{contract.name} is missing required split(s): {missing_splits}")
    observed_dates = {
        split: {"min_date": str(split_min[split]), "max_date": str(split_max[split])}
        for split in OPTIONAL_SPLITS
        if split_counts[split]
    }
    return TrafficDataset(
        contract=contract,
        frame=None,
        available_columns=available,
        horizon_column=horizon_column,
        asof_column=asof_column,
        resolved_time_column=time_column,
        schema_descriptor=descriptor,
        row_count=row_count,
        split_counts=split_counts,
        split_dates=observed_dates,
        group_ids=tuple(sorted(group_ids)),
        test_index=test_index,
    )


def _load_dataset(
    contract: DatasetContract,
    *,
    inspection_store: _DiskInspectionStore | None = None,
    batch_size: int = DEFAULT_INSPECTION_BATCH_SIZE_ROWS,
) -> TrafficDataset:
    """Backward-compatible name for the streaming inspection boundary."""

    return _inspect_dataset(
        contract,
        inspection_store=inspection_store,
        batch_size=batch_size,
    )


def _stable_sample_rank(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def _downcast_frame(frame: pd.DataFrame, contract: DatasetContract) -> pd.DataFrame:
    """Reduce resident feature memory without changing category semantics."""

    for column in contract.feature_columns:
        if column not in frame.columns:
            continue
        if pd.api.types.is_float_dtype(frame[column]):
            frame[column] = frame[column].astype("float32")
        elif pd.api.types.is_integer_dtype(frame[column]):
            frame[column] = pd.to_numeric(frame[column], downcast="integer")
    for column in _baseline_columns_from_columns(frame.columns, contract):
        if column in frame.columns and pd.api.types.is_float_dtype(frame[column]):
            frame[column] = frame[column].astype("float32")
    return frame


def _baseline_columns_from_columns(
    available: Iterable[str], contract: DatasetContract
) -> set[str]:
    names = set(available)
    result = set(contract.baseline_columns.values())
    for aliases in BASELINE_ALIASES.values():
        result.update(alias for alias in aliases if alias in names)
    return result


def _load_group_split_frame(
    dataset: TrafficDataset,
    group_id: str,
    split: str,
    *,
    keys: Sequence[str] | None,
    sample_limit: int | None,
    seed: int,
) -> pd.DataFrame:
    """Read one source/candidate/split with bounded Arrow batches.

    A full run may retain a large train split for the single model currently
    fitting, but never retains another candidate or measurement source.  In
    small-data mode the deterministic hash-ranked reservoir keeps only the
    configured cap while scanning the file.
    """

    if pq is None:
        raise TrainingError("pyarrow is required to stream traffic feature tables")
    read_columns = _dataset_read_columns(
        dataset.contract,
        dataset.available_columns,
        time_column=dataset.resolved_time_column,
        horizon_column=dataset.horizon_column,
        asof_column=dataset.asof_column,
        observed_time_column=_resolve_observed_time_column(
            dataset.available_columns, dataset.resolved_time_column
        ),
        inspection=False,
    )
    key_set = set(keys) if keys is not None else None
    parts: list[pd.DataFrame] = []
    parquet = pq.ParquetFile(dataset.contract.path)
    for arrow_batch in parquet.iter_batches(columns=read_columns, batch_size=250_000):
        frame = _normalise_batch(
            arrow_batch.to_pandas(),
            dataset.contract,
            time_column=dataset.resolved_time_column,
            group_id=group_id,
        )
        frame = frame.loc[frame["__split"] == split]
        if key_set is not None:
            frame = frame.loc[frame["__key"].isin(key_set)]
        if frame.empty:
            continue
        frame = _downcast_frame(frame, dataset.contract)
        if sample_limit is not None:
            frame["__sample_rank"] = frame["__key"].map(_stable_sample_rank)
            parts.append(frame)
            combined = pd.concat(parts, ignore_index=True, copy=False)
            combined = combined.nsmallest(sample_limit, "__sample_rank", keep="first")
            parts = [combined]
        else:
            parts.append(frame)
    if not parts:
        raise TrainingError(f"{dataset.contract.name}/{group_id} has no rows for split={split!r}")
    frame = pd.concat(parts, ignore_index=True, copy=False)
    if "__sample_rank" in frame.columns:
        frame = frame.drop(columns=["__sample_rank"])
    return frame.sort_values("__key", kind="mergesort").reset_index(drop=True)


@dataclass(frozen=True)
class _MaterializedSelection:
    """A narrow, exact candidate/group/split Parquet selection on disk."""

    path: Path
    read_columns: tuple[str, ...]
    rows: int


def _materialize_group_split(
    dataset: TrafficDataset,
    group_id: str,
    split: str,
    *,
    path: Path,
    batch_size: int,
    telemetry: _BatchTelemetry,
) -> _MaterializedSelection:
    """Push down a selection once, then replay it from disk-backed Parquet."""

    if pads is None or pa is None or pq is None:
        raise TrainingError("pyarrow dataset/parquet support is required for full streaming mode")
    read_columns = tuple(
        _dataset_read_columns(
            dataset.contract,
            dataset.available_columns,
            time_column=dataset.resolved_time_column,
            horizon_column=dataset.horizon_column,
            asof_column=dataset.asof_column,
            observed_time_column=_resolve_observed_time_column(
                dataset.available_columns, dataset.resolved_time_column
            ),
            inspection=False,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    parquet_dataset = pads.dataset(str(dataset.contract.path), format="parquet")
    scanner = parquet_dataset.scanner(
        columns=list(read_columns),
        filter=_scanner_filter(dataset, group_id, split),
        batch_size=batch_size,
        batch_readahead=1,
        fragment_readahead=1,
        use_threads=False,
        cache_metadata=True,
    )
    telemetry.scanner_calls += 1
    writer: pq.ParquetWriter | None = None
    rows = 0
    try:
        for arrow_batch in scanner.to_batches():
            if arrow_batch.num_rows == 0:
                continue
            if writer is None:
                writer = pq.ParquetWriter(
                    temporary,
                    arrow_batch.schema,
                    compression="zstd",
                    use_dictionary=True,
                )
            writer.write_table(pa.Table.from_batches([arrow_batch]))
            rows += arrow_batch.num_rows
            telemetry.observe(arrow_batch.num_rows)
        if writer is None or rows == 0:
            raise TrainingError(f"{dataset.contract.name}/{group_id} has no rows for split={split!r}")
        writer.close()
        writer = None
        os.replace(temporary, path)
    finally:
        if writer is not None:
            writer.close()
        if temporary.exists():
            temporary.unlink()
    return _MaterializedSelection(path=path, read_columns=read_columns, rows=rows)


def _iter_materialized_selection(
    selection: _MaterializedSelection,
    dataset: TrafficDataset,
    *,
    group_id: str,
    split: str,
    batch_size: int,
    telemetry: _BatchTelemetry,
) -> Iterable[pd.DataFrame]:
    if pq is None:
        raise TrainingError("pyarrow is required to replay materialized traffic selections")
    parquet = pq.ParquetFile(selection.path)
    telemetry.cache_reads += 1
    for arrow_batch in parquet.iter_batches(
        columns=list(selection.read_columns), batch_size=batch_size
    ):
        frame = _normalise_batch(
            arrow_batch.to_pandas(),
            dataset.contract,
            time_column=dataset.resolved_time_column,
            group_id=group_id,
        )
        frame = frame.loc[frame["__split"] == split]
        if frame.empty:
            continue
        frame = _downcast_frame(frame, dataset.contract)
        telemetry.observe(len(frame))
        yield frame


def _scanner_filter(
    dataset: TrafficDataset,
    group_id: str,
    split: str,
    *,
    keys: Sequence[str] | None = None,
) -> Any:
    if pads is None:
        raise TrainingError("pyarrow.dataset is required for full streaming traffic training")
    source, scope = _group_parts(group_id)
    columns = dataset.contract.columns
    expression = (
        pads.field(columns["split"]) == split
    ) & (
        pads.field(columns["label_source"]) == source
    ) & (
        pads.field(columns["measurement_scope"]) == scope
    )
    if keys is not None:
        if not keys:
            return expression & pads.field(columns["observation_key"]).isin(["__no_key__"])
        expression = expression & pads.field(columns["observation_key"]).isin(
            [str(key) for key in keys]
        )
    return expression


def _iter_group_split_frames(
    dataset: TrafficDataset,
    group_id: str,
    split: str,
    *,
    keys: Sequence[str] | None,
    batch_size: int,
    telemetry: _BatchTelemetry | None = None,
) -> Iterable[pd.DataFrame]:
    """Yield only one pushdown-filtered Arrow batch at a time."""

    if pads is None:
        raise TrainingError("pyarrow.dataset is required for full streaming traffic training")
    read_columns = _dataset_read_columns(
        dataset.contract,
        dataset.available_columns,
        time_column=dataset.resolved_time_column,
        horizon_column=dataset.horizon_column,
        asof_column=dataset.asof_column,
        observed_time_column=_resolve_observed_time_column(
            dataset.available_columns, dataset.resolved_time_column
        ),
        inspection=False,
    )
    parquet_dataset = pads.dataset(str(dataset.contract.path), format="parquet")
    scanner = parquet_dataset.scanner(
        columns=read_columns,
        filter=_scanner_filter(dataset, group_id, split, keys=keys),
        batch_size=batch_size,
        batch_readahead=1,
        fragment_readahead=1,
        use_threads=False,
        cache_metadata=True,
    )
    if telemetry is not None:
        telemetry.scanner_calls += 1
    for arrow_batch in scanner.to_batches():
        frame = _normalise_batch(
            arrow_batch.to_pandas(),
            dataset.contract,
            time_column=dataset.resolved_time_column,
            group_id=group_id,
        )
        frame = frame.loc[frame["__split"] == split]
        if keys is not None:
            frame = frame.loc[frame["__key"].isin(set(keys))]
        if frame.empty:
            continue
        frame = _downcast_frame(frame, dataset.contract)
        if telemetry is not None:
            telemetry.observe(len(frame))
        yield frame


def _count_group_split_rows(dataset: TrafficDataset, group_id: str, split: str) -> int:
    """Count a pushed-down selection without converting it to pandas."""

    if pads is None:
        raise TrainingError("pyarrow.dataset is required for full streaming traffic training")
    parquet_dataset = pads.dataset(str(dataset.contract.path), format="parquet")
    try:
        return int(parquet_dataset.count_rows(filter=_scanner_filter(dataset, group_id, split)))
    except (OSError, ValueError, TypeError) as exc:
        raise TrainingError(
            f"could not count pushed-down {dataset.contract.name}/{group_id}/{split} rows: {exc}"
        ) from exc


class _StreamingDataIter(xgb.DataIter if xgb is not None else object):
    """XGBoost DataIter backed by filtered Arrow batches and disk cache."""

    def __init__(
        self,
        frame_factory: Any,
        encoder: FeatureEncoder,
        *,
        device: str,
        cache_prefix: Path,
        cache_on_host: bool,
        min_cache_page_bytes: int | None,
    ) -> None:
        if xgb is None or not hasattr(xgb, "DataIter"):
            raise TrainingError("full streaming mode requires XGBoost DataIter support")
        self._frame_factory = frame_factory
        self._encoder = encoder
        self._device = device
        self._frames: Iterable[pd.DataFrame] | None = None
        self._min_cache_page_bytes = min_cache_page_bytes
        self.rows_seen = 0
        self.batch_count = 0
        self.max_batch_rows = 0
        xgb.DataIter.__init__(
            self,
            cache_prefix=str(cache_prefix),
            release_data=True,
            on_host=cache_on_host,
            min_cache_page_bytes=min_cache_page_bytes,
        )

    def reset(self) -> None:
        self._frames = iter(self._frame_factory())

    def next(self, input_data: Any) -> bool:
        if self._frames is None:
            self.reset()
        assert self._frames is not None
        try:
            frame = next(self._frames)
        except StopIteration:
            return False
        numeric = self._encoder.transform_numeric(frame)
        target = frame["__target"].to_numpy(dtype="float32")
        self.rows_seen += len(frame)
        self.batch_count += 1
        self.max_batch_rows = max(self.max_batch_rows, len(frame))
        if self._device == "cuda":
            if cp is None:
                raise TrainingError(
                    "full CUDA external-memory training requires CuPy; "
                    "install a CUDA-matched cupy package or use --device cpu"
                )
            data = cp.asarray(numeric)
            label = cp.asarray(target)
        else:
            data = numeric
            label = target
        input_data(
            data=data,
            label=label,
            feature_types=self._encoder.model_feature_types(),
        )
        del label, data, numeric, target, frame
        return True


@dataclass
class _StreamingModel:
    """Small adapter giving a streamed Booster the old model interface."""

    booster: Any
    device: str
    feature_types: list[str]
    n_jobs: int
    cuda_async_pool: bool

    def predict(self, features: np.ndarray) -> np.ndarray:
        if xgb is None:
            raise TrainingError("xgboost is required for prediction")
        data: Any = features
        if self.device == "cuda":
            if cp is None:
                raise TrainingError("CuPy is required for CUDA prediction")
            data = cp.asarray(features)
        cuda_config = (
            xgb.config_context(use_cuda_async_pool=True)
            if self.device == "cuda" and self.cuda_async_pool
            else contextlib.nullcontext()
        )
        matrix: Any = None
        try:
            with cuda_config:
                matrix = xgb.DMatrix(
                    data,
                    enable_categorical=True,
                    feature_types=self.feature_types,
                    nthread=self.n_jobs,
                )
                predict_kwargs: dict[str, Any] = {}
                best_iteration = getattr(self.booster, "best_iteration", None)
                if best_iteration is not None and int(best_iteration) >= 0:
                    predict_kwargs["iteration_range"] = (0, int(best_iteration) + 1)
                # XGBoost 3.x does not accept iteration_range=None.  Omitting
                # it selects all boosted rounds for models without early
                # stopping, which is the intended default.
                values = self.booster.predict(matrix, **predict_kwargs)
                if cp is not None and isinstance(values, cp.ndarray):
                    values = cp.asnumpy(values)
                if self.device == "cuda" and cp is not None:
                    cp.cuda.runtime.deviceSynchronize()
                return np.asarray(values, dtype="float64")
        finally:
            del matrix, data

    def save_model(self, path: str) -> None:
        self.booster.save_model(path)

    def get_booster(self) -> Any:
        return self.booster

    @property
    def best_iteration(self) -> Any:
        return getattr(self.booster, "best_iteration", None)

    @property
    def best_score(self) -> Any:
        return getattr(self.booster, "best_score", None)


def _remove_external_cache(prefix: Path) -> None:
    for path in prefix.parent.glob(f"{prefix.name}*"):
        if path.is_file():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _fit_streaming_model(
    dataset: TrafficDataset,
    group_id: str,
    *,
    encoder: FeatureEncoder,
    train_factory: Any,
    validation_factory: Any,
    train_rows: int,
    validation_rows: int,
    args: argparse.Namespace,
    cache_dir: Path,
    cache_key: str,
) -> tuple[_StreamingModel, str, list[str], bool]:
    """Fit one uncapped model using XGBoost's disk-backed external memory."""

    if xgb is None:
        raise TrainingError("xgboost is required; install ml/requirements.txt")
    if not hasattr(xgb, "ExtMemQuantileDMatrix"):
        raise TrainingError(
            "uncapped full mode requires XGBoost 3.x ExtMemQuantileDMatrix support"
        )
    devices = ["cpu"] if args.device == "cpu" else ["cuda"]
    if args.device == "auto":
        devices = ["cuda", "cpu"]
    failures: list[str] = []
    cuda_attempted = False
    cache_dir.mkdir(parents=True, exist_ok=True)
    for device in devices:
        if device == "cuda":
            cuda_attempted = True
            if cp is None:
                failures.append("cuda: CuPy is not installed for GPU external-memory input")
                if args.device != "auto":
                    break
                continue
        prefix = cache_dir / f"{_slug(cache_key)}__{device}"
        train_iter: _StreamingDataIter | None = None
        validation_iter: _StreamingDataIter | None = None
        train_matrix: Any = None
        validation_matrix: Any = None
        try:
            train_iter = _StreamingDataIter(
                train_factory,
                encoder,
                device=device,
                cache_prefix=prefix.with_name(prefix.name + "__train"),
                cache_on_host=args.cache_on_host,
                min_cache_page_bytes=args.min_cache_page_bytes,
            )
            validation_iter = _StreamingDataIter(
                validation_factory,
                encoder,
                device=device,
                cache_prefix=prefix.with_name(prefix.name + "__validation"),
                cache_on_host=args.cache_on_host,
                min_cache_page_bytes=args.min_cache_page_bytes,
            )
            dmatrix_kwargs: dict[str, Any] = {
                "enable_categorical": True,
                "max_bin": args.max_bin,
            }
            if args.cache_host_ratio is not None:
                dmatrix_kwargs["cache_host_ratio"] = args.cache_host_ratio
            cuda_config = (
                xgb.config_context(use_cuda_async_pool=True)
                if device == "cuda" and args.cuda_async_pool
                else contextlib.nullcontext()
            )
            with cuda_config:
                train_matrix = xgb.ExtMemQuantileDMatrix(train_iter, **dmatrix_kwargs)
                validation_matrix = xgb.ExtMemQuantileDMatrix(
                    validation_iter,
                    ref=train_matrix,
                    **dmatrix_kwargs,
                )
                parameters = _model_parameters(args, device)
                parameters.pop("n_estimators", None)
                parameters.pop("early_stopping_rounds", None)
                parameters["max_bin"] = args.max_bin
                evals_result: dict[str, Any] = {}
                booster = xgb.train(
                    parameters,
                    train_matrix,
                    num_boost_round=args.n_estimators,
                    evals=[(validation_matrix, "validation")],
                    early_stopping_rounds=(
                        args.early_stopping_rounds
                        if args.early_stopping_rounds > 0
                        else None
                    ),
                    evals_result=evals_result,
                    verbose_eval=False,
                )
            model = _StreamingModel(
                booster=booster,
                device=device,
                feature_types=encoder.model_feature_types(),
                n_jobs=args.n_jobs,
                cuda_async_pool=bool(device == "cuda" and args.cuda_async_pool),
            )
            del validation_matrix, train_matrix, validation_iter, train_iter
            gc.collect()
            return model, device, failures, cuda_attempted
        except Exception as exc:  # CUDA failures vary across XGBoost/CuPy versions.
            failures.append(f"{device}: {type(exc).__name__}: {exc}")
            del validation_matrix, train_matrix, validation_iter, train_iter
            gc.collect()
            _remove_external_cache(prefix)
            if device != "cuda" or args.device != "auto":
                break
    joined = " | ".join(failures)
    fallback_note = "; auto permits CPU fallback" if args.device == "auto" else "; no CPU fallback permitted"
    raise TrainingError(f"XGBoost external-memory training failed ({joined}){fallback_note}")


def _group_id(label_source: Any, measurement_scope: Any) -> str:
    return f"{str(label_source).strip()}{GROUP_SEPARATOR}{str(measurement_scope).strip()}"


def _group_parts(group_id: str) -> tuple[str, str]:
    source, separator, scope = group_id.partition(GROUP_SEPARATOR)
    if not separator or not source or not scope:
        raise TrainingError(f"invalid traffic measurement group: {group_id!r}")
    return source, scope


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return result or "unknown"


def _validate_split_contract(
    dataset: TrafficDataset,
    split_contract: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    if not isinstance(split_contract, Mapping):
        raise TrainingError("traffic training manifest has no split_contract mapping")
    strategy = str(split_contract.get("strategy", "chronological")).lower()
    if not any(token in strategy for token in ("chronolog", "time", "as_of", "as-of")):
        raise TrainingError("traffic split_contract must declare a chronological/as-of strategy")
    aliases = {
        "train_end": ("train_end", "train_end_date", "train_until"),
        "validation_end": (
            "validation_end",
            "validation_end_date",
            "validation_until",
            "val_end",
        ),
        "test_end": ("test_end", "test_end_date", "test_until"),
    }
    bounds: dict[str, dt.date] = {}
    for name, keys in aliases.items():
        value = _mapping_value(split_contract, keys)
        if value is None:
            raise TrainingError(f"split_contract is missing {name}")
        bounds[name] = _parse_date(value, label=f"split_contract.{name}")

    expected_manifest_ends = {
        "train_end": TRAFFIC_TRAIN_END,
        "validation_end": TRAFFIC_VALIDATION_END,
        "test_end": TRAFFIC_TEST_END,
    }
    for name, expected in expected_manifest_ends.items():
        if bounds[name] != expected:
            raise TrainingError(
                f"traffic split date contract requires {name}={expected.isoformat()}, "
                f"got {bounds[name].isoformat()}"
            )

    # Some manifests also repeat the bounds in per-split records.  If those
    # optional values are present, validate them too; omitted train.start is
    # allowed because the table-level lower bound below is authoritative.
    nested_aliases = {
        "start": ("start", "start_date", "from", "lower"),
        "end": ("end", "end_date", "until", "upper"),
    }
    for split, (expected_start, expected_end) in TRAFFIC_SPLIT_DATE_RANGES.items():
        section = split_contract.get(split)
        if not isinstance(section, Mapping):
            continue
        for edge, aliases_for_edge in nested_aliases.items():
            value = next(
                (section[key] for key in aliases_for_edge if key in section),
                None,
            )
            if value is None:
                continue
            expected = expected_start if edge == "start" else expected_end
            if expected is None:
                raise TrainingError(
                    f"traffic split date contract does not allow a {split}.{edge} upper bound"
                )
            actual = _parse_date(value, label=f"split_contract.{split}.{edge}")
            if actual != expected:
                raise TrainingError(
                    f"traffic split date contract requires {split}.{edge}={expected.isoformat()}, "
                    f"got {actual.isoformat()}"
                )

    optional_start_aliases = {
        "train_start": ("train_start", "train_start_date", "train_from"),
        "validation_start": (
            "validation_start",
            "validation_start_date",
            "validation_from",
            "val_start",
        ),
        "test_start": ("test_start", "test_start_date", "test_from"),
        "post_test_start": ("post_test_start", "post_test_start_date", "post_test_from"),
    }
    expected_starts = {
        "train_start": TRAFFIC_TRAIN_START,
        "validation_start": TRAFFIC_VALIDATION_START,
        "test_start": TRAFFIC_TEST_START,
        "post_test_start": TRAFFIC_POST_TEST_START,
    }
    for name, keys in optional_start_aliases.items():
        value = _mapping_value(split_contract, keys)
        if value is None:
            continue
        actual = _parse_date(value, label=f"split_contract.{name}")
        expected = expected_starts[name]
        if actual != expected:
            raise TrainingError(
                f"traffic split date contract requires {name}={expected.isoformat()}, "
                f"got {actual.isoformat()}"
            )

    observed: dict[str, dict[str, str]] = {}
    for split, (inclusive_start, inclusive_end) in TRAFFIC_SPLIT_DATE_RANGES.items():
        date_record = dataset.split_dates.get(split)
        if date_record is None:
            if split == "post_test":
                continue
            raise TrainingError(f"{dataset.contract.name} has no rows for split {split!r}")
        minimum = dt.date.fromisoformat(str(date_record["min_date"]))
        maximum = dt.date.fromisoformat(str(date_record["max_date"]))
        if minimum < inclusive_start:
            raise TrainingError(
                f"{dataset.contract.name} {split} starts {minimum}, before the required "
                f"lower date bound {inclusive_start}"
            )
        if inclusive_end is not None and maximum > inclusive_end:
            raise TrainingError(
                f"{dataset.contract.name} {split} ends {maximum}, after the required "
                f"upper date bound {inclusive_end}"
            )
        observed[split] = {"min_date": str(minimum), "max_date": str(maximum)}
    # A split label must itself impose a strict temporal ordering, even if a
    # future builder emits a boundary contract with broad date ranges.
    maxima = [
        dt.date.fromisoformat(str(dataset.split_dates[split]["max_date"]))
        for split in SPLITS
    ]
    minima = [
        dt.date.fromisoformat(str(dataset.split_dates[split]["min_date"]))
        for split in SPLITS
    ]
    if not (maxima[0] < minima[1] and maxima[1] < minima[2]):
        raise TrainingError(f"{dataset.contract.name} split labels are not strictly chronological")
    return observed


def _validate_candidate_alignment(
    base: TrafficDataset,
    lag_enhanced: TrafficDataset,
) -> dict[str, Any]:
    # ``test_index`` contains only the strict comparison columns.  The full
    # feature tables remain on disk and are never loaded together in pandas.
    base_test = base.test_index
    lag_test = lag_enhanced.test_index
    base_keys = set(base_test)
    lag_keys = set(lag_test)
    if base_keys != lag_keys:
        raise TrainingError(
            "base and lag_enhanced test observation_key sets differ: "
            f"base_only={len(base_keys - lag_keys)}, lag_enhanced_only={len(lag_keys - base_keys)}"
        )
    if not base_keys:
        raise TrainingError("candidate datasets have no shared test observation_key values")
    for key in sorted(base_keys):
        left = base_test[key]
        right = lag_test[key]
        if not math.isclose(left[3], right[3], rel_tol=0.0, abs_tol=0.0):
            raise TrainingError(
                f"candidate test key {key!r} has different {TARGET_COLUMN} labels"
            )
        if left[:3] != right[:3]:
            raise TrainingError(
                f"candidate test key {key!r} has different unit/source assignments"
            )
    base_groups = {_group_id(values[0], values[1]) for values in base_test.values()}
    lag_groups = {_group_id(values[0], values[1]) for values in lag_test.values()}
    if base_groups != lag_groups:
        raise TrainingError("candidate test keys do not expose the same label_source/measurement_scope groups")
    return {
        "base_test_keys": len(base_keys),
        "lag_enhanced_test_keys": len(lag_keys),
        "shared_test_keys": len(base_keys),
        "exact_match": True,
        "label_match": True,
        "group_match": True,
        "intersection_policy": "strict_exact",
    }


def _sample_keys(keys: Sequence[str], limit: int | None, seed: int) -> list[str]:
    ordered = sorted(keys)
    if limit is None or len(ordered) <= limit:
        return ordered
    generator = np.random.default_rng(seed)
    selected = generator.choice(len(ordered), size=limit, replace=False)
    return sorted(ordered[int(index)] for index in selected)


def _select_group_frame(
    dataset: TrafficDataset,
    group_id: str,
    split: str,
    *,
    keys: Sequence[str] | None,
    sample_limit: int | None,
    seed: int,
) -> pd.DataFrame:
    return _load_group_split_frame(
        dataset,
        group_id,
        split,
        keys=keys,
        sample_limit=sample_limit,
        seed=seed,
    )


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
    safe_pred = np.maximum(y_pred, 1e-12)
    positive = y_true > 0
    terms = np.zeros_like(y_true)
    terms[positive] = y_true[positive] * (
        np.log(y_true[positive]) - np.log(safe_pred[positive])
    ) - (y_true[positive] - safe_pred[positive])
    terms[~positive] = safe_pred[~positive]
    return {
        "n": int(len(y_true)),
        "target_mean": float(np.mean(y_true)),
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "poisson_deviance": float(2.0 * np.mean(terms)),
    }


def _prediction_array(model: Any, features: Any) -> np.ndarray:
    try:
        values = np.asarray(model.predict(features), dtype="float64").reshape(-1)
    except Exception as exc:  # pragma: no cover - exact XGBoost error varies.
        raise TrainingError(f"model prediction failed: {exc}") from exc
    if len(values) != len(features):
        raise TrainingError("model returned a different number of predictions than input rows")
    if (~np.isfinite(values)).any():
        raise TrainingError("model returned non-finite traffic predictions")
    values = np.clip(values, 0.0, None)
    if (~np.isfinite(values)).any() or (values < 0).any():
        raise TrainingError("traffic predictions must be finite and non-negative")
    return values


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
        "max_delta_step": args.max_delta_step,
        "max_bin": args.max_bin,
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
) -> tuple[Any, str, list[str], bool]:
    if xgb is None:
        raise TrainingError("xgboost is required; install ml/requirements.txt")
    if args.device == "cpu":
        devices = ["cpu"]
    elif args.device == "cuda":
        devices = ["cuda"]
    else:
        devices = ["cuda", "cpu"]
    failures: list[str] = []
    cuda_attempted = False
    for device in devices:
        if device == "cuda":
            cuda_attempted = True
        parameters = _model_parameters(args, device)
        try:
            model = xgb.XGBRegressor(**parameters)
            model.fit(
                train_features,
                train_target,
                eval_set=[(validation_features, validation_target)],
                verbose=False,
            )
            return model, device, failures, cuda_attempted
        except Exception as exc:  # CUDA failures differ across XGBoost versions.
            failures.append(f"{device}: {type(exc).__name__}: {exc}")
            if device != "cuda" or args.device != "auto":
                break
    joined = " | ".join(failures)
    fallback_note = "; auto permits CPU fallback" if args.device == "auto" else "; no CPU fallback permitted"
    raise TrainingError(f"XGBoost training failed ({joined}){fallback_note}")


def _safe_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return str(value)


def _metric_row(
    *,
    candidate: str,
    dataset_name: str,
    group_id: str,
    split: str,
    scope: str,
    metric: Mapping[str, Any],
    baseline: str | None = None,
    unit_id: str | None = None,
    unit_seen_in_train: bool | None = None,
    stratum_type: str | None = None,
    stratum: str | None = None,
    feature: str | None = None,
    coverage: float | None = None,
    available_rows: int | None = None,
    missing_rows: int | None = None,
) -> dict[str, Any]:
    source, measurement_scope = _group_parts(group_id)
    row: dict[str, Any] = {
        "candidate": candidate,
        "dataset": dataset_name,
        "group_id": group_id,
        "label_source": source,
        "measurement_scope": measurement_scope,
        "split": split,
        "scope": scope,
        "baseline": baseline,
        "observation_unit_id": unit_id,
        "unit_id": unit_id,
        "unit_seen_in_train": unit_seen_in_train,
        "stratum_type": stratum_type,
        "stratum": stratum,
        "feature": feature,
        "coverage": coverage,
        "available_rows": available_rows,
        "missing_rows": missing_rows,
    }
    row.update(metric)
    return row


def _baseline_columns(dataset: TrafficDataset) -> dict[str, str | None]:
    columns: dict[str, str | None] = {}
    normalized_available = {_normalise(column): column for column in dataset.available_columns}
    for lag, aliases in BASELINE_ALIASES.items():
        explicit = dataset.contract.baseline_columns.get(lag)
        if explicit:
            if explicit not in dataset.available_columns:
                raise TrainingError(
                    f"{dataset.contract.name} baseline {lag} column {explicit!r} is absent"
                )
            columns[lag] = explicit
            continue
        columns[lag] = next(
            (normalized_available[_normalise(alias)] for alias in aliases if _normalise(alias) in normalized_available),
            None,
        )
    return columns


def _baseline_metric_rows(
    frame: pd.DataFrame,
    dataset: TrafficDataset,
    *,
    candidate: str,
    group_id: str,
    split: str,
) -> list[dict[str, Any]]:
    actual = frame["__target"].to_numpy(dtype="float64")
    rows: list[dict[str, Any]] = []
    for baseline, column in _baseline_columns(dataset).items():
        if column is None:
            valid = np.zeros(len(frame), dtype=bool)
            values = np.zeros(len(frame), dtype="float64")
        else:
            values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
            valid = np.isfinite(values) & (values >= 0)
        metric = _metric_bundle(actual[valid], values[valid])
        rows.append(
            _metric_row(
                candidate=candidate,
                dataset_name=dataset.contract.name,
                group_id=group_id,
                split=split,
                scope="baseline",
                baseline=baseline,
                metric=metric,
                coverage=float(valid.mean()) if len(valid) else 0.0,
                available_rows=int(valid.sum()),
                missing_rows=int((~valid).sum()),
            )
        )
    return rows


def _unit_metric_rows(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    *,
    candidate: str,
    dataset_name: str,
    group_id: str,
    split: str,
    train_unit_tokens: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actual = frame["__target"].to_numpy(dtype="float64")
    unit_column = "__unit"
    tokens = frame[unit_column].map(_stable_token)
    seen = tokens.isin(train_unit_tokens)
    per_unit: list[dict[str, Any]] = []
    for token, indexes in tokens.groupby(tokens, sort=True).groups.items():
        positions = np.asarray(list(indexes), dtype="int64")
        metric = _metric_bundle(actual[positions], predictions[positions])
        raw_value = frame.iloc[int(positions[0])][dataset_name if False else "__unit"]
        per_unit.append(
            _metric_row(
                candidate=candidate,
                dataset_name=dataset_name,
                group_id=group_id,
                split=split,
                scope="per_unit",
                metric=metric,
                unit_id=str(raw_value),
                unit_seen_in_train=bool(seen.iloc[positions[0]]),
            )
        )
    seen_groups: list[dict[str, Any]] = []
    for group_name, mask in (("seen", seen.to_numpy(dtype=bool)), ("unseen", (~seen).to_numpy(dtype=bool))):
        metric = _metric_bundle(actual[mask], predictions[mask])
        seen_groups.append(
            _metric_row(
                candidate=candidate,
                dataset_name=dataset_name,
                group_id=group_id,
                split=split,
                scope="unit_seen_group",
                metric=metric,
                unit_id=group_name,
                unit_seen_in_train=group_name == "seen",
                stratum_type="unit_group",
                stratum=group_name,
            )
        )
    return per_unit, seen_groups


def _quality_metric_rows(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    dataset: TrafficDataset,
    *,
    candidate: str,
    group_id: str,
    split: str,
) -> list[dict[str, Any]]:
    actual = frame["__target"].to_numpy(dtype="float64")
    quality_columns = [
        column
        for column in (
            "label_quality",
            "quality_flag",
            "quality_partial_flag",
            "quality_alarm_flag",
        )
        if column in frame.columns
    ]
    rows: list[dict[str, Any]] = []
    for column in quality_columns:
        values = frame[column].map(lambda value: "<MISSING>" if _is_missing(value) else str(value))
        for stratum in sorted(set(values)):
            mask = (values == stratum).to_numpy(dtype=bool)
            rows.append(
                _metric_row(
                    candidate=candidate,
                    dataset_name=dataset.contract.name,
                    group_id=group_id,
                    split=split,
                    scope="quality_stratum",
                    metric=_metric_bundle(actual[mask], predictions[mask]),
                    stratum_type=column,
                    stratum=stratum,
                )
            )

    missing_count = frame[list(dataset.contract.feature_columns)].isna().sum(axis=1)
    missing_group = np.where(missing_count.to_numpy() > 0, "missing", "complete")
    for stratum in ("complete", "missing"):
        mask = missing_group == stratum
        rows.append(
            _metric_row(
                candidate=candidate,
                dataset_name=dataset.contract.name,
                group_id=group_id,
                split=split,
                scope="missingness_stratum",
                metric=_metric_bundle(actual[mask], predictions[mask]),
                stratum_type="feature_missingness",
                stratum=stratum,
            )
        )
    for feature in dataset.contract.feature_columns:
        missing = frame[feature].isna().to_numpy(dtype=bool)
        rate = float(missing.mean()) if len(missing) else 0.0
        for is_missing in (False, True):
            mask = missing == is_missing
            rows.append(
                _metric_row(
                    candidate=candidate,
                    dataset_name=dataset.contract.name,
                    group_id=group_id,
                    split=split,
                    scope="feature_missingness",
                    metric=_metric_bundle(actual[mask], predictions[mask]),
                    stratum_type="feature",
                    stratum="missing" if is_missing else "present",
                    feature=feature,
                    coverage=1.0 - rate if not is_missing else rate,
                    available_rows=int(mask.sum()),
                )
            )
    return rows


def _prediction_frame(
    frame: pd.DataFrame,
    predictions: np.ndarray,
    dataset: TrafficDataset,
    *,
    candidate: str,
    group_id: str,
    train_unit_tokens: set[str],
) -> pd.DataFrame:
    unit_tokens = frame["__unit"].map(_stable_token)
    missing_count = frame[list(dataset.contract.feature_columns)].isna().sum(axis=1)
    output: dict[str, Any] = {
        "candidate": candidate,
        "dataset": dataset.contract.name,
        "group_id": group_id,
        "label_source": frame["__label_source"].to_numpy(),
        "measurement_scope": frame["__measurement_scope"].to_numpy(),
        "observation_key": frame["__key"].to_numpy(),
        "observation_unit_id": frame["__unit"].to_numpy(),
        "split": frame["__split"].to_numpy(),
        "local_date": frame["__local_date"].to_numpy(),
        "actual": frame["__target"].to_numpy(dtype="float64"),
        "prediction": predictions,
        "unit_seen_in_train": unit_tokens.isin(train_unit_tokens).to_numpy(dtype=bool),
        "missing_feature_count": missing_count.to_numpy(dtype="int64"),
        "missingness_group": np.where(missing_count.to_numpy() > 0, "missing", "complete"),
    }
    for column in (
        "label_quality",
        "quality_flag",
        "quality_partial_flag",
        "quality_alarm_flag",
        "quality_missing_interval_count",
    ):
        if column in frame.columns:
            output[column] = frame[column].to_numpy()
        else:
            output[column] = np.full(len(frame), np.nan)
    for baseline, column in _baseline_columns(dataset).items():
        if column is None:
            output[f"baseline_{baseline}"] = np.full(len(frame), np.nan)
        else:
            values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
            values[~np.isfinite(values) | (values < 0)] = np.nan
            output[f"baseline_{baseline}"] = values
    return pd.DataFrame(output)


def _ensure_output_is_writable(output_dir: Path, *, overwrite: bool) -> None:
    if output_dir.exists() and not overwrite:
        existing = [path for path in output_dir.rglob("*") if path.is_file()]
        if existing:
            preview = ", ".join(str(path) for path in existing[:8])
            suffix = " ..." if len(existing) > 8 else ""
            raise TrainingError(f"outputs already exist; pass --overwrite: {preview}{suffix}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "models").mkdir(parents=True, exist_ok=True)


def _validate_output_target(output_dir: Path, *, overwrite: bool) -> None:
    """Validate a publication target without modifying an existing release."""

    if output_dir.exists() and not output_dir.is_dir():
        raise TrainingError(f"output path exists and is not a directory: {output_dir}")
    if output_dir.exists() and not overwrite:
        existing = [path for path in output_dir.rglob("*") if path.is_file()]
        if existing:
            preview = ", ".join(str(path) for path in existing[:8])
            suffix = " ..." if len(existing) > 8 else ""
            raise TrainingError(f"outputs already exist; pass --overwrite: {preview}{suffix}")


def _retarget_json_value(
    value: Any,
    *,
    staged_prefix: str,
    published_prefix: str,
    value_replacements: Mapping[str, str] | None = None,
) -> Any:
    """Retarget staged absolute paths and known content hashes recursively."""

    if isinstance(value, dict):
        return {
            key: _retarget_json_value(
                child,
                staged_prefix=staged_prefix,
                published_prefix=published_prefix,
                value_replacements=value_replacements,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _retarget_json_value(
                child,
                staged_prefix=staged_prefix,
                published_prefix=published_prefix,
                value_replacements=value_replacements,
            )
            for child in value
        ]
    if isinstance(value, str):
        result = value.replace(staged_prefix, published_prefix)
        if value_replacements is not None:
            result = value_replacements.get(result, result)
        return result
    return value


def _refresh_staged_artifacts(
    payload: dict[str, Any],
    *,
    staging_dir: Path,
    published_dir: Path,
) -> None:
    """Refresh hashes while keeping artifact paths pointed at publication."""

    for record in payload.get("artifacts", []):
        published_path = Path(record["path"])
        try:
            relative = published_path.relative_to(published_dir)
        except ValueError as exc:
            raise TrainingError(
                f"staged artifact is outside the evaluation directory: {published_path}"
            ) from exc
        staged_path = staging_dir / relative
        record["bytes"] = staged_path.stat().st_size
        record["sha256"] = _sha256(staged_path)
        try:
            record["relative_path"] = str(published_path.relative_to(REPO_DIR))
        except ValueError:
            record["relative_path"] = None


def _retarget_staged_publication(staging_dir: Path, published_dir: Path) -> None:
    """Make staged manifests describe their final paths before publication."""

    staged_prefix = str(staging_dir.resolve())
    published_prefix = str(published_dir.resolve())
    hash_replacements: dict[str, str] = {}

    for metadata_path in sorted((staging_dir / "models").glob("*.metadata.json")):
        old_hash = _sha256(metadata_path)
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload = _retarget_json_value(
            payload,
            staged_prefix=staged_prefix,
            published_prefix=published_prefix,
        )
        _atomic_json(metadata_path, payload)
        hash_replacements[old_hash] = _sha256(metadata_path)

    metrics_path = staging_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics = _retarget_json_value(
        metrics,
        staged_prefix=staged_prefix,
        published_prefix=published_prefix,
        value_replacements=hash_replacements,
    )
    _atomic_json(metrics_path, metrics)

    checksums_path = staging_dir / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums = _retarget_json_value(
        checksums,
        staged_prefix=staged_prefix,
        published_prefix=published_prefix,
        value_replacements=hash_replacements,
    )
    _refresh_staged_artifacts(
        checksums,
        staging_dir=staging_dir,
        published_dir=published_dir,
    )
    _atomic_json(checksums_path, checksums)

    evaluation_path = staging_dir / "evaluation_manifest.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation = _retarget_json_value(
        evaluation,
        staged_prefix=staged_prefix,
        published_prefix=published_prefix,
        value_replacements=hash_replacements,
    )
    _refresh_staged_artifacts(
        evaluation,
        staging_dir=staging_dir,
        published_dir=published_dir,
    )
    _atomic_json(evaluation_path, evaluation)


def _publish_staged_directory(staging_dir: Path, published_dir: Path) -> None:
    """Replace an evaluation directory only after every artifact is complete."""

    backup_dir: Path | None = None
    if published_dir.exists():
        backup_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{published_dir.name}.previous-",
                dir=published_dir.parent,
            )
        )
        backup_dir.rmdir()
        os.replace(published_dir, backup_dir)
    try:
        os.replace(staging_dir, published_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not published_dir.exists():
            os.replace(backup_dir, published_dir)
        raise
    if backup_dir is not None:
        shutil.rmtree(backup_dir)


def _winner_key(result: Mapping[str, Any], candidate_order: Mapping[str, int]) -> tuple[float, float, float, int]:
    # Model selection must only use the chronological validation split.  The
    # test split is retained for one final, unbiased performance report.
    metrics = result.get("validation_metrics", {})
    return tuple(
        [
            float(metrics.get("poisson_deviance")) if metrics.get("poisson_deviance") is not None else math.inf,
            float(metrics.get("mae")) if metrics.get("mae") is not None else math.inf,
            float(metrics.get("rmse")) if metrics.get("rmse") is not None else math.inf,
            candidate_order.get(str(result.get("candidate")), 999),
        ]
    )


class _PredictionCsvWriter:
    """Stream prediction rows to disk so full traffic runs stay bounded."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.temporary = path.with_name(f".{path.name}.tmp")
        self.handle = self.temporary.open("w", encoding="utf-8", newline="")
        self.wrote_header = False

    def write(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        frame.to_csv(
            self.handle,
            index=False,
            header=not self.wrote_header,
            na_rep="",
        )
        self.wrote_header = True
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()
        os.replace(self.temporary, self.path)

    def abort(self) -> None:
        try:
            self.handle.close()
        finally:
            if self.temporary.exists():
                self.temporary.unlink()


def _finalize_training_outputs(
    *,
    args: argparse.Namespace,
    sample_limit: int | None,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    datasets: Mapping[str, TrafficDataset],
    split_contract: Mapping[str, Any],
    observed_splits: Mapping[str, Any],
    alignment: Mapping[str, Any],
    output_dir: Path,
    base_groups: Sequence[str],
    predictions_csv: Path,
    all_metric_rows: list[dict[str, Any]],
    per_unit_rows: list[dict[str, Any]],
    model_records: list[dict[str, Any]],
    model_results: Mapping[str, Mapping[str, Mapping[str, Any]]],
    actual_devices: Mapping[str, str],
    cuda_attempts: Mapping[str, bool],
    memory_policy: Mapping[str, Any],
    cache_policy: Mapping[str, Any],
) -> dict[str, Any]:
    metrics_frame = pd.DataFrame(all_metric_rows)
    metric_sort = [
        column
        for column in (
            "candidate",
            "group_id",
            "split",
            "scope",
            "baseline",
            "observation_unit_id",
            "stratum_type",
            "stratum",
            "feature",
        )
        if column in metrics_frame.columns
    ]
    metrics_frame = metrics_frame.sort_values(
        metric_sort, na_position="last", kind="mergesort"
    ).reset_index(drop=True)
    metrics_csv = output_dir / "metrics.csv"
    _atomic_csv(metrics_csv, metrics_frame)
    per_unit_frame = pd.DataFrame(per_unit_rows)
    per_unit_frame = per_unit_frame.sort_values(
        ["candidate", "group_id", "split", "observation_unit_id"],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    per_unit_csv = output_dir / "per_unit_metrics.csv"
    _atomic_csv(per_unit_csv, per_unit_frame)
    quality_frame = metrics_frame.loc[
        metrics_frame["scope"].isin(
            ["quality_stratum", "missingness_stratum", "feature_missingness"]
        )
    ].reset_index(drop=True)
    quality_csv = output_dir / "quality_metrics.csv"
    _atomic_csv(quality_csv, quality_frame)

    candidate_order = {"base": 0, "lag_enhanced": 1}
    winners: dict[str, dict[str, Any]] = {}
    for group_id in base_groups:
        candidates = [model_results[candidate][group_id] for candidate in DATASET_NAMES]
        winner = min(candidates, key=lambda result: _winner_key(result, candidate_order))
        winners[group_id] = {
            "source_group": group_id,
            "label_source": _group_parts(group_id)[0],
            "measurement_scope": _group_parts(group_id)[1],
            "candidate": winner["candidate"],
            "model_path": winner["model_path"],
            "metadata_path": winner["metadata_path"],
            "selection_split": "validation",
            "validation_metrics": winner["validation_metrics"],
            "test_metrics": winner["test_metrics"],
            "selection_key": list(_winner_key(winner, candidate_order)),
            "tie_break": "validation poisson_deviance, then validation MAE, then validation RMSE, then base before lag_enhanced",
        }

    metrics_json = output_dir / "metrics.json"
    checksums_path = output_dir / "checksums.json"
    evaluation_manifest_path = output_dir / "evaluation_manifest.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "git_head": _git_head(),
        "python_version": platform.python_version(),
        "seed": args.seed,
        "objective": "count:poisson",
        "tree_method": "hist",
        "run_mode": "small_data" if sample_limit is not None else "full",
        "requested_device": args.device,
        "actual_devices": dict(actual_devices),
        "cuda_attempts": dict(cuda_attempts),
        "cuda_used": any(device == "cuda" for device in actual_devices.values()),
        "memory_policy": dict(memory_policy),
        "cache_policy": dict(cache_policy),
        "reproducibility": {
            "seed": args.seed,
            "sample_rows_per_split_and_group": sample_limit,
            "candidate_order": list(DATASET_NAMES),
            "selection_split": "validation",
            "tie_break": "validation poisson_deviance, then validation MAE, then validation RMSE, then base before lag_enhanced",
            "variant_xgboost_parameters": {
                model_key: _model_parameters(args, device)
                for model_key, device in actual_devices.items()
            },
        },
        "manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path)},
        "split_contract": {
            "strategy": "chronological/as-of; post_test is excluded from claimed scores",
            **dict(split_contract),
            "observed_dates": dict(observed_splits),
        },
        "candidate_test_alignment": dict(alignment),
        "datasets": {
            name: {
                "path": str(dataset.contract.path),
                "sha256": dataset.contract.sha256,
                "rows_inspected": dataset.row_count,
                "split_counts": dict(dataset.split_counts),
                "feature_columns": list(dataset.contract.feature_columns),
                "target_column": TARGET_COLUMN,
                "observation_key": dataset.contract.columns["observation_key"],
                "split": dataset.contract.columns["split"],
                "observation_unit_id": dataset.contract.columns["observation_unit_id"],
                "label_source": dataset.contract.columns["label_source"],
                "measurement_scope": dataset.contract.columns["measurement_scope"],
                "prediction_horizon_hours": 1,
            }
            for name, dataset in datasets.items()
        },
        "source_groups": list(base_groups),
        "models": model_results,
        "model_records": model_records,
        "release": {
            "type": "bundle_of_source_stratified_models",
            "winner_by_source_group": winners,
        },
        "outputs": {
            "predictions_csv": str(predictions_csv),
            "metrics_csv": str(metrics_csv),
            "per_unit_metrics_csv": str(per_unit_csv),
            "quality_metrics_csv": str(quality_csv),
            "checksums_json": str(checksums_path),
            "evaluation_manifest": str(evaluation_manifest_path),
        },
        "scores": all_metric_rows,
        "per_unit_scores": per_unit_rows,
        "notes": [
            "Models are trained separately per label_source and measurement_scope.",
            "Candidate test keys and vehicle_count labels are identical by strict validation.",
            "Post-test rows are retained in feature tables but are excluded from predictions and claimed scores.",
            "Categorical unit identities are fit on training rows only; unseen units map to the missing branch and are reported separately.",
            "Lag baselines report valid-row coverage and never convert missing values to zero.",
            "Full mode uses all rows unless an explicit --sample cap is supplied; any cap is recorded in every model and evaluation manifest.",
        ],
    }
    _atomic_json(metrics_json, report)

    artifact_paths: list[Path] = [
        predictions_csv,
        metrics_csv,
        metrics_json,
        per_unit_csv,
        quality_csv,
        *(Path(record[key]) for record in model_records for key in ("model_path", "metadata_path")),
    ]
    checksum_payload = {
        "schema_version": 1,
        "artifacts": [_artifact_record(path) for path in artifact_paths],
        "model_metadata_pairs": [
            {
                "model_path": record["model_path"],
                "model_sha256": record["model_sha256"],
                "metadata_path": record["metadata_path"],
                "metadata_sha256": record["metadata_sha256"],
            }
            for record in model_records
        ],
    }
    _atomic_json(checksums_path, checksum_payload)
    evaluation_payload = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "metrics_json": str(metrics_json),
        "predictions_csv": str(predictions_csv),
        "metrics_csv": str(metrics_csv),
        "per_unit_metrics_csv": str(per_unit_csv),
        "quality_metrics_csv": str(quality_csv),
        "checksums": str(checksums_path),
        "requested_device": args.device,
        "actual_devices": dict(actual_devices),
        "cuda_attempts": dict(cuda_attempts),
        "cuda_used": any(device == "cuda" for device in actual_devices.values()),
        "candidate_test_alignment": dict(alignment),
        "memory_policy": report["memory_policy"],
        "cache_policy": report["cache_policy"],
        "release": report["release"],
        "model_paths": [record["model_path"] for record in model_records],
        "artifacts": [_artifact_record(path) for path in [*artifact_paths, checksums_path]],
    }
    _atomic_json(evaluation_manifest_path, evaluation_payload)
    return report


def _run_full_streaming(
    *,
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    datasets: Mapping[str, TrafficDataset],
    split_contract: Mapping[str, Any],
    observed_splits: Mapping[str, Any],
    alignment: Mapping[str, Any],
    output_dir: Path,
    base_groups: Sequence[str],
) -> dict[str, Any]:
    """Run uncapped source-stratified training with bounded resident memory."""

    if args.device == "cuda" and cp is None:
        raise TrainingError(
            "full CUDA external-memory training requires CuPy; install a CUDA-matched "
            "cupy package or use --device cpu"
        )

    requested_cache_dir = (
        args.cache_dir.resolve()
        if args.cache_dir is not None
        else (output_dir / ".xgb-cache").resolve()
    )
    requested_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_run_dir = Path(
        tempfile.mkdtemp(prefix="traffic-training-", dir=str(requested_cache_dir))
    )
    telemetry = _BatchTelemetry(requested_batch_rows=args.batch_size)
    all_metric_rows: list[dict[str, Any]] = []
    per_unit_rows: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    model_results: dict[str, dict[str, dict[str, Any]]] = {
        name: {} for name in DATASET_NAMES
    }
    actual_devices: dict[str, str] = {}
    cuda_attempts: dict[str, bool] = {}
    predictions_csv = output_dir / "predictions.csv"
    prediction_writer = _PredictionCsvWriter(predictions_csv)
    writer_closed = False
    try:
        for candidate_index, candidate in enumerate(DATASET_NAMES):
            dataset = datasets[candidate]
            for group_index, group_id in enumerate(base_groups):
                selection_key = f"{candidate}__{group_index}__{_slug(group_id)}"
                selection_dir = cache_run_dir / "selections"
                train_selection = _materialize_group_split(
                    dataset,
                    group_id,
                    "train",
                    path=selection_dir / f"{selection_key}__train.parquet",
                    batch_size=args.batch_size,
                    telemetry=telemetry,
                )
                validation_selection = _materialize_group_split(
                    dataset,
                    group_id,
                    "validation",
                    path=selection_dir / f"{selection_key}__validation.parquet",
                    batch_size=args.batch_size,
                    telemetry=telemetry,
                )
                test_selection = _materialize_group_split(
                    dataset,
                    group_id,
                    "test",
                    path=selection_dir / f"{selection_key}__test.parquet",
                    batch_size=args.batch_size,
                    telemetry=telemetry,
                )
                train_rows = train_selection.rows
                validation_rows = validation_selection.rows
                test_rows = test_selection.rows

                def train_factory(
                    selection: _MaterializedSelection = train_selection,
                    dataset: TrafficDataset = dataset,
                    group_id: str = group_id,
                ) -> Iterable[pd.DataFrame]:
                    return _iter_materialized_selection(
                        selection,
                        dataset,
                        group_id=group_id,
                        split="train",
                        batch_size=args.batch_size,
                        telemetry=telemetry,
                    )

                def validation_factory(
                    selection: _MaterializedSelection = validation_selection,
                    dataset: TrafficDataset = dataset,
                    group_id: str = group_id,
                ) -> Iterable[pd.DataFrame]:
                    return _iter_materialized_selection(
                        selection,
                        dataset,
                        group_id=group_id,
                        split="validation",
                        batch_size=args.batch_size,
                        telemetry=telemetry,
                    )

                def test_factory(
                    selection: _MaterializedSelection = test_selection,
                    dataset: TrafficDataset = dataset,
                    group_id: str = group_id,
                ) -> Iterable[pd.DataFrame]:
                    return _iter_materialized_selection(
                        selection,
                        dataset,
                        group_id=group_id,
                        split="test",
                        batch_size=args.batch_size,
                        telemetry=telemetry,
                    )

                encoder = FeatureEncoder.fit_stream(
                    train_factory(),
                    dataset.contract.feature_columns,
                    unit_column=dataset.contract.columns["observation_unit_id"],
                )
                model_key = f"{candidate}::{group_id}"
                model, device, device_failures, cuda_attempted = _fit_streaming_model(
                    dataset,
                    group_id,
                    encoder=encoder,
                    train_factory=train_factory,
                    validation_factory=validation_factory,
                    train_rows=train_rows,
                    validation_rows=validation_rows,
                    args=args,
                    cache_dir=cache_run_dir,
                    cache_key=f"{candidate}__{group_index}__{_slug(group_id)}",
                )
                actual_devices[model_key] = device
                cuda_attempts[model_key] = cuda_attempted

                validation_eval = _StreamingEvaluation.create(
                    dataset,
                    candidate=candidate,
                    group_id=group_id,
                    split="validation",
                )
                for frame in validation_factory():
                    features = encoder.transform_numeric(frame)
                    predictions = _prediction_array(model, features)
                    validation_eval.update(
                        frame, predictions, train_unit_tokens=encoder.train_unit_tokens
                    )
                    prediction_writer.write(
                        _prediction_frame(
                            frame,
                            predictions,
                            dataset,
                            candidate=candidate,
                            group_id=group_id,
                            train_unit_tokens=encoder.train_unit_tokens,
                        )
                    )
                    del predictions, features, frame
                if validation_eval.rows != validation_rows:
                    raise TrainingError(
                        f"{candidate}/{group_id} validation row count changed during streaming: "
                        f"expected {validation_rows}, got {validation_eval.rows}"
                    )
                validation_metric, baseline_validation, per_unit_validation, extra_validation = (
                    validation_eval.metric_rows()
                )
                all_metric_rows.extend(
                    [
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="validation",
                            scope="overall",
                            metric=validation_metric,
                        ),
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="validation",
                            scope="per_source",
                            metric=validation_metric,
                        ),
                    ]
                )
                all_metric_rows.extend(baseline_validation + extra_validation)
                per_unit_rows.extend(per_unit_validation)

                test_eval = _StreamingEvaluation.create(
                    dataset,
                    candidate=candidate,
                    group_id=group_id,
                    split="test",
                )
                for frame in test_factory():
                    features = encoder.transform_numeric(frame)
                    predictions = _prediction_array(model, features)
                    test_eval.update(
                        frame, predictions, train_unit_tokens=encoder.train_unit_tokens
                    )
                    prediction_writer.write(
                        _prediction_frame(
                            frame,
                            predictions,
                            dataset,
                            candidate=candidate,
                            group_id=group_id,
                            train_unit_tokens=encoder.train_unit_tokens,
                        )
                    )
                    del predictions, features, frame
                if test_eval.rows != test_rows:
                    raise TrainingError(
                        f"{candidate}/{group_id} test row count changed during streaming: "
                        f"expected {test_rows}, got {test_eval.rows}"
                    )
                test_metric, baseline_test, per_unit_test, extra_test = test_eval.metric_rows()
                all_metric_rows.extend(
                    [
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="test",
                            scope="overall",
                            metric=test_metric,
                        ),
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="test",
                            scope="per_source",
                            metric=test_metric,
                        ),
                    ]
                )
                all_metric_rows.extend(baseline_test + extra_test)
                per_unit_rows.extend(per_unit_test)

                group_slug = f"{_slug(_group_parts(group_id)[0])}__{_slug(_group_parts(group_id)[1])}"
                model_path = output_dir / "models" / f"{candidate}__{group_slug}.ubj"
                metadata_path = output_dir / "models" / f"{candidate}__{group_slug}.metadata.json"
                try:
                    model.save_model(str(model_path))
                except Exception as exc:  # pragma: no cover - exact XGBoost error varies.
                    raise TrainingError(f"could not save {model_key} to {model_path}: {exc}") from exc
                booster = model.get_booster()
                try:
                    gains = booster.get_score(importance_type="gain")
                except Exception:
                    gains = {}
                feature_gain = sorted(
                    ((str(feature), float(gain)) for feature, gain in gains.items()),
                    key=lambda item: (-item[1], item[0]),
                )
                best_iteration = model.best_iteration
                best_score = model.best_score
                cache_policy = {
                    "backend": "xgboost_extmem_quantile_dmatrix",
                    "requested_cache_dir": str(requested_cache_dir),
                    "cache_run_dir": str(cache_run_dir),
                    "cache_on_host": bool(args.cache_on_host),
                    "cache_host_ratio": args.cache_host_ratio,
                    "min_cache_page_bytes": args.min_cache_page_bytes,
                    "max_bin": args.max_bin,
                    "cuda_async_pool": bool(device == "cuda" and args.cuda_async_pool),
                    "cache_retained_after_run": bool(args.keep_cache),
                    "cache_cleanup": "retained only with --keep-cache",
                }
                metadata: dict[str, Any] = {
                    "schema_version": 1,
                    "candidate": candidate,
                    "dataset": dataset.contract.name,
                    "source_group": group_id,
                    "label_source": _group_parts(group_id)[0],
                    "measurement_scope": _group_parts(group_id)[1],
                    "dataset_path": str(dataset.contract.path),
                    "dataset_sha256": dataset.contract.sha256,
                    "target_column": TARGET_COLUMN,
                    "prediction_horizon_hours": 1,
                    "feature_columns": list(dataset.contract.feature_columns),
                    "model_feature_types": encoder.model_feature_types(),
                    "objective": "count:poisson",
                    "eval_metric": "poisson-nloglik",
                    "tree_method": "hist",
                    "device": device,
                    "cuda_attempted": cuda_attempted,
                    "cuda_used": device == "cuda",
                    "device_failures_before_success": device_failures,
                    "seed": args.seed,
                    "run_mode": "full",
                    "configured_row_cap": None,
                    "train_rows": train_rows,
                    "validation_rows": validation_rows,
                    "test_rows": test_rows,
                    "dataset_rows_inspected": dataset.row_count,
                    "encoder": encoder.metadata(),
                    "input_backend": "xgboost_external_memory",
                    "batch_size_rows": args.batch_size,
                    "cache_policy": cache_policy,
                    "xgboost_version": getattr(xgb, "__version__", None),
                    "xgboost_parameters": _model_parameters(args, device),
                    "boosted_rounds": booster.num_boosted_rounds(),
                    "best_iteration": best_iteration,
                    "best_score": best_score,
                    "feature_gain": [
                        {"feature": feature, "gain": gain} for feature, gain in feature_gain
                    ],
                    "training_script_sha256": _sha256(Path(__file__).resolve()),
                    "git_head": _git_head(),
                    "model_sha256": _sha256(model_path),
                    "checksums_file": str(output_dir / "checksums.json"),
                }
                _atomic_json(metadata_path, metadata)
                model_record = {
                    "model_key": model_key,
                    "candidate": candidate,
                    "dataset": dataset.contract.name,
                    "source_group": group_id,
                    "label_source": _group_parts(group_id)[0],
                    "measurement_scope": _group_parts(group_id)[1],
                    "model_path": str(model_path),
                    "metadata_path": str(metadata_path),
                    "device": device,
                    "cuda_attempted": cuda_attempted,
                    "cuda_used": device == "cuda",
                    "run_mode": "full",
                    "configured_row_cap": None,
                    "train_rows": train_rows,
                    "validation_rows": validation_rows,
                    "test_rows": test_rows,
                    "feature_columns": list(dataset.contract.feature_columns),
                    "model_feature_columns": encoder.model_feature_columns(),
                    "model_feature_types": encoder.model_feature_types(),
                    "input_backend": "xgboost_external_memory",
                    "batch_size_rows": args.batch_size,
                    "cache_policy": cache_policy,
                    "train_unit_count": len(encoder.train_unit_tokens),
                    "boosted_rounds": booster.num_boosted_rounds(),
                    "best_iteration": best_iteration,
                    "best_score": best_score,
                    "model_sha256": _sha256(model_path),
                    "metadata_sha256": _sha256(metadata_path),
                    "top_feature_gain": [
                        {"feature": feature, "gain": gain} for feature, gain in feature_gain[:20]
                    ],
                }
                model_records.append(model_record)
                model_results[candidate][group_id] = {
                    **model_record,
                    "test_metrics": test_metric,
                    "validation_metrics": validation_metric,
                    "baseline_validation": baseline_validation,
                    "baseline_test": baseline_test,
                    "per_unit_validation": per_unit_validation,
                    "per_unit_test": per_unit_test,
                    "seen_unseen_validation": [
                        row for row in extra_validation if row["scope"] == "unit_seen_group"
                    ],
                    "seen_unseen_test": [
                        row for row in extra_test if row["scope"] == "unit_seen_group"
                    ],
                    "quality_validation": [
                        row for row in extra_validation if row["scope"] != "unit_seen_group"
                    ],
                    "quality_test": [
                        row for row in extra_test if row["scope"] != "unit_seen_group"
                    ],
                }
                del model, encoder, booster
                gc.collect()
        prediction_writer.close()
        writer_closed = True
        cache_policy = {
            "backend": "xgboost_extmem_quantile_dmatrix",
            "requested_cache_dir": str(requested_cache_dir),
            "cache_run_dir": str(cache_run_dir),
            "cache_on_host": bool(args.cache_on_host),
            "cache_host_ratio": args.cache_host_ratio,
            "min_cache_page_bytes": args.min_cache_page_bytes,
            "max_bin": args.max_bin,
            "cuda_async_pool": bool(
                args.cuda_async_pool and any(device == "cuda" for device in actual_devices.values())
            ),
            "cache_retained_after_run": bool(args.keep_cache),
            "cache_cleanup": "retained only with --keep-cache",
        }
        memory_policy = {
            "backend": "xgboost_external_memory",
            "batch_size_rows": args.batch_size,
            "inspection_batch_size_rows": args.inspection_batch_size,
            "host_memory_bound": "one Arrow/Pandas batch plus bounded encoder/metric state; XGBoost pages on disk by default",
            "resident_pandas_scope": "one pushdown-filtered Arrow batch per iterator",
            "test_alignment": "SQLite disk-backed exact key/label index",
            "selection_pushdown": "candidate label_source + measurement_scope + split",
            "candidate_group_split_pushdown": True,
            "selection_materialization": "narrow per-candidate/group/split Parquet cache",
            "source_scan_reuse": "replay materialized selections; no repeated source-table pandas scans",
            "whole_table_pandas_loads": 0,
            "whole_table_pandas_retention": False,
            "repeated_full_table_pandas_retention": False,
            "prediction_output": "streamed CSV; full mode omits train predictions",
            "numeric_downcast": "float features to float32; categories to deterministic native codes",
            "observed_batches": telemetry.batches,
            "observed_rows": telemetry.rows,
            "observed_max_batch_rows": telemetry.max_observed_rows,
            "scanner_calls": telemetry.scanner_calls,
            "materialized_cache_reads": telemetry.cache_reads,
            "configured_row_cap": None,
            "full_run_uses_all_rows": True,
        }
        return _finalize_training_outputs(
            args=args,
            sample_limit=None,
            manifest_path=manifest_path,
            manifest=manifest,
            datasets=datasets,
            split_contract=split_contract,
            observed_splits=observed_splits,
            alignment=alignment,
            output_dir=output_dir,
            base_groups=base_groups,
            predictions_csv=predictions_csv,
            all_metric_rows=all_metric_rows,
            per_unit_rows=per_unit_rows,
            model_records=model_records,
            model_results=model_results,
            actual_devices=actual_devices,
            cuda_attempts=cuda_attempts,
            memory_policy=memory_policy,
            cache_policy=cache_policy,
        )
    except Exception:
        if not writer_closed:
            prediction_writer.abort()
        raise
    finally:
        if not args.keep_cache:
            shutil.rmtree(cache_run_dir, ignore_errors=True)


def _run_to_output(args: argparse.Namespace) -> dict[str, Any]:
    if args.sample is not None and args.sample < 1:
        raise TrainingError("--sample must be at least 1")
    if args.small_data:
        sample_limit = args.sample or DEFAULT_SMALL_ROWS
        args.n_estimators = min(args.n_estimators, DEFAULT_SMALL_ESTIMATORS)
        args.early_stopping_rounds = min(args.early_stopping_rounds, 6)
    else:
        sample_limit = args.sample

    manifest_path = args.manifest.resolve()
    manifest, contracts = _parse_manifest(manifest_path)
    if args.base is not None:
        contracts["base"] = replace(contracts["base"], path=args.base.resolve())
    if args.lag_enhanced is not None:
        contracts["lag_enhanced"] = replace(
            contracts["lag_enhanced"], path=args.lag_enhanced.resolve()
        )

    # Inspection streams both files in Arrow batches.  Uncapped mode moves
    # duplicate-key and candidate-alignment state into a temporary SQLite
    # database instead of retaining millions of Python strings.
    inspection_store: _DiskInspectionStore | None = None
    inspection_temp: tempfile.TemporaryDirectory[str] | None = None
    if sample_limit is None:
        inspection_temp = tempfile.TemporaryDirectory(prefix="traffic-inspection-")
        inspection_store = _DiskInspectionStore(Path(inspection_temp.name) / "alignment.sqlite")
    datasets = {
        name: _load_dataset(
            contracts[name],
            inspection_store=inspection_store,
            batch_size=args.inspection_batch_size,
        )
        for name in DATASET_NAMES
    }
    split_contract = manifest.get("split_contract")
    observed_splits = {
        name: _validate_split_contract(dataset, split_contract)
        for name, dataset in datasets.items()
    }
    if inspection_store is not None:
        alignment = inspection_store.validate_alignment()
    else:
        alignment = _validate_candidate_alignment(datasets["base"], datasets["lag_enhanced"])
    output_dir = args.output_dir.resolve()
    _ensure_output_is_writable(output_dir, overwrite=args.overwrite)

    if inspection_store is not None:
        base_groups = inspection_store.groups("base")
    else:
        base_groups = sorted(
            {
                _group_id(values[0], values[1])
                for values in datasets["base"].test_index.values()
            }
        )
    if not base_groups:
        raise TrainingError("traffic candidate test set has no source/measurement groups")
    if not set(base_groups).issubset(set(datasets["base"].group_ids)):
        raise TrainingError("traffic test groups are absent from the inspected candidate groups")
    if not set(base_groups).issubset(set(datasets["lag_enhanced"].group_ids)):
        raise TrainingError("traffic test groups are absent from lag_enhanced candidate groups")

    if inspection_store is not None:
        inspection_store.close()
        inspection_store = None
        assert inspection_temp is not None
        inspection_temp.cleanup()
        inspection_temp = None

    if sample_limit is None:
        return _run_full_streaming(
            args=args,
            manifest_path=manifest_path,
            manifest=manifest,
            datasets=datasets,
            split_contract=split_contract,
            observed_splits=observed_splits,
            alignment=alignment,
            output_dir=output_dir,
            base_groups=base_groups,
        )

    selected_test_keys: dict[str, list[str]] = {}
    for group_index, group_id in enumerate(base_groups):
        group_keys = [
            key
            for key, values in datasets["base"].test_index.items()
            if _group_id(values[0], values[1]) == group_id
        ]
        selected_test_keys[group_id] = _sample_keys(
            group_keys,
            sample_limit,
            args.seed + 1_001 + group_index,
        )

    all_metric_rows: list[dict[str, Any]] = []
    per_unit_rows: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    model_results: dict[str, dict[str, dict[str, Any]]] = {
        name: {} for name in DATASET_NAMES
    }
    actual_devices: dict[str, str] = {}
    cuda_attempts: dict[str, bool] = {}
    candidate_order = {"base": 0, "lag_enhanced": 1}
    predictions_csv = output_dir / "predictions.csv"
    prediction_writer = _PredictionCsvWriter(predictions_csv)
    prediction_splits = ("train", "validation", "test") if sample_limit is not None else ("validation", "test")

    try:
        for candidate_index, candidate in enumerate(DATASET_NAMES):
            dataset = datasets[candidate]
            for group_index, group_id in enumerate(base_groups):
                # Only this candidate/source group is loaded.  The train and
                # validation frames are released before the test frame is
                # loaded, keeping the largest resident pandas object bounded.
                train = _select_group_frame(
                    dataset,
                    group_id,
                    "train",
                    keys=None,
                    sample_limit=sample_limit,
                    seed=args.seed + 10_000 + candidate_index * 100 + group_index,
                )
                validation = _select_group_frame(
                    dataset,
                    group_id,
                    "validation",
                    keys=None,
                    sample_limit=sample_limit,
                    seed=args.seed + 11_000 + candidate_index * 100 + group_index,
                )
                encoder = FeatureEncoder.fit(
                    train,
                    dataset.contract.feature_columns,
                    unit_column=dataset.contract.columns["observation_unit_id"],
                )
                train_features = encoder.transform(train)
                validation_features = encoder.transform(validation)
                model, device, device_failures, cuda_attempted = _fit_model(
                    train_features,
                    train["__target"].to_numpy(dtype="float64"),
                    validation_features,
                    validation["__target"].to_numpy(dtype="float64"),
                    args,
                )
                model_key = f"{candidate}::{group_id}"
                actual_devices[model_key] = device
                cuda_attempts[model_key] = cuda_attempted
                train_rows_used = len(train)
                validation_rows_used = len(validation)
                validation_predictions = _prediction_array(model, validation_features)
                validation_metric = _metric_bundle(
                    validation["__target"].to_numpy(dtype="float64"),
                    validation_predictions,
                )
                for split, metric in (("validation", validation_metric),):
                    all_metric_rows.extend(
                        [
                            _metric_row(
                                candidate=candidate,
                                dataset_name=dataset.contract.name,
                                group_id=group_id,
                                split=split,
                                scope="overall",
                                metric=metric,
                            ),
                            _metric_row(
                                candidate=candidate,
                                dataset_name=dataset.contract.name,
                                group_id=group_id,
                                split=split,
                                scope="per_source",
                                metric=metric,
                            ),
                        ]
                    )
                all_metric_rows.extend(
                    _baseline_metric_rows(
                        validation,
                        dataset,
                        candidate=candidate,
                        group_id=group_id,
                        split="validation",
                    )
                )
                per_unit_validation, seen_validation = _unit_metric_rows(
                    validation,
                    validation_predictions,
                    candidate=candidate,
                    dataset_name=dataset.contract.name,
                    group_id=group_id,
                    split="validation",
                    train_unit_tokens=encoder.train_unit_tokens,
                )
                quality_validation = _quality_metric_rows(
                    validation,
                    validation_predictions,
                    dataset,
                    candidate=candidate,
                    group_id=group_id,
                    split="validation",
                )
                per_unit_rows.extend(per_unit_validation)
                all_metric_rows.extend(seen_validation + quality_validation)
                if "train" in prediction_splits:
                    train_predictions = _prediction_array(model, train_features)
                    prediction_writer.write(
                        _prediction_frame(
                            train,
                            train_predictions,
                            dataset,
                            candidate=candidate,
                            group_id=group_id,
                            train_unit_tokens=encoder.train_unit_tokens,
                        )
                    )
                prediction_writer.write(
                    _prediction_frame(
                        validation,
                        validation_predictions,
                        dataset,
                        candidate=candidate,
                        group_id=group_id,
                        train_unit_tokens=encoder.train_unit_tokens,
                    )
                )

                # Fit state and chronological training frames are no longer
                # needed after validation scoring; release before test read.
                del train_features, validation_features, train, validation
                gc.collect()

                test = _select_group_frame(
                    dataset,
                    group_id,
                    "test",
                    keys=selected_test_keys[group_id],
                    sample_limit=None,
                    seed=args.seed + 12_000 + candidate_index * 100 + group_index,
                )
                expected_keys = set(selected_test_keys[group_id])
                if set(test["__key"]) != expected_keys:
                    raise TrainingError(
                        f"{candidate}/{group_id} test keys changed during selection"
                    )
                test_features = encoder.transform(test)
                test_predictions = _prediction_array(model, test_features)
                test_metric = _metric_bundle(
                    test["__target"].to_numpy(dtype="float64"), test_predictions
                )
                all_metric_rows.extend(
                    [
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="test",
                            scope="overall",
                            metric=test_metric,
                        ),
                        _metric_row(
                            candidate=candidate,
                            dataset_name=dataset.contract.name,
                            group_id=group_id,
                            split="test",
                            scope="per_source",
                            metric=test_metric,
                        ),
                    ]
                )
                all_metric_rows.extend(
                    _baseline_metric_rows(
                        test,
                        dataset,
                        candidate=candidate,
                        group_id=group_id,
                        split="test",
                    )
                )
                per_unit_test, seen_test = _unit_metric_rows(
                    test,
                    test_predictions,
                    candidate=candidate,
                    dataset_name=dataset.contract.name,
                    group_id=group_id,
                    split="test",
                    train_unit_tokens=encoder.train_unit_tokens,
                )
                quality_test = _quality_metric_rows(
                    test,
                    test_predictions,
                    dataset,
                    candidate=candidate,
                    group_id=group_id,
                    split="test",
                )
                per_unit_rows.extend(per_unit_test)
                all_metric_rows.extend(seen_test + quality_test)
                prediction_writer.write(
                    _prediction_frame(
                        test,
                        test_predictions,
                        dataset,
                        candidate=candidate,
                        group_id=group_id,
                        train_unit_tokens=encoder.train_unit_tokens,
                    )
                )

                group_slug = f"{_slug(_group_parts(group_id)[0])}__{_slug(_group_parts(group_id)[1])}"
                model_path = output_dir / "models" / f"{candidate}__{group_slug}.ubj"
                metadata_path = output_dir / "models" / f"{candidate}__{group_slug}.metadata.json"
                try:
                    model.save_model(str(model_path))
                except Exception as exc:  # pragma: no cover - exact XGBoost error varies.
                    raise TrainingError(f"could not save {model_key} to {model_path}: {exc}") from exc
                booster = model.get_booster()
                try:
                    gains = booster.get_score(importance_type="gain")
                except Exception:
                    gains = {}
                feature_gain = sorted(
                    ((str(feature), float(gain)) for feature, gain in gains.items()),
                    key=lambda item: (-item[1], item[0]),
                )
                best_iteration = getattr(model, "best_iteration", None)
                best_score = getattr(model, "best_score", None)
                metadata: dict[str, Any] = {
                    "schema_version": 1,
                    "candidate": candidate,
                    "dataset": dataset.contract.name,
                    "source_group": group_id,
                    "label_source": _group_parts(group_id)[0],
                    "measurement_scope": _group_parts(group_id)[1],
                    "dataset_path": str(dataset.contract.path),
                    "dataset_sha256": dataset.contract.sha256,
                    "target_column": TARGET_COLUMN,
                    "prediction_horizon_hours": 1,
                    "feature_columns": list(dataset.contract.feature_columns),
                    "objective": "count:poisson",
                    "eval_metric": "poisson-nloglik",
                    "tree_method": "hist",
                    "device": device,
                    "cuda_attempted": cuda_attempted,
                    "cuda_used": device == "cuda",
                    "device_failures_before_success": device_failures,
                    "seed": args.seed,
                    "run_mode": "small_data" if sample_limit is not None else "full",
                    "configured_row_cap": sample_limit,
                    "train_rows": train_rows_used,
                    "validation_rows": validation_rows_used,
                    "test_rows": len(test),
                    "dataset_rows_inspected": dataset.row_count,
                    "encoder": encoder.metadata(),
                    "xgboost_version": getattr(xgb, "__version__", None),
                    "xgboost_parameters": _model_parameters(args, device),
                    "boosted_rounds": booster.num_boosted_rounds(),
                    "best_iteration": best_iteration,
                    "best_score": best_score,
                    "feature_gain": [
                        {"feature": feature, "gain": gain} for feature, gain in feature_gain
                    ],
                    "training_script_sha256": _sha256(Path(__file__).resolve()),
                    "git_head": _git_head(),
                    "model_sha256": _sha256(model_path),
                    "checksums_file": str(output_dir / "checksums.json"),
                }
                _atomic_json(metadata_path, metadata)
                model_record = {
                    "model_key": model_key,
                    "candidate": candidate,
                    "dataset": dataset.contract.name,
                    "source_group": group_id,
                    "label_source": _group_parts(group_id)[0],
                    "measurement_scope": _group_parts(group_id)[1],
                    "model_path": str(model_path),
                    "metadata_path": str(metadata_path),
                    "device": device,
                    "cuda_attempted": cuda_attempted,
                    "cuda_used": device == "cuda",
                    "run_mode": "small_data" if sample_limit is not None else "full",
                    "configured_row_cap": sample_limit,
                    "train_rows": train_rows_used,
                    "validation_rows": validation_rows_used,
                    "test_rows": len(test),
                    "feature_columns": list(dataset.contract.feature_columns),
                    "model_feature_columns": encoder.model_feature_columns(),
                    "train_unit_count": len(encoder.train_unit_tokens),
                    "boosted_rounds": booster.num_boosted_rounds(),
                    "best_iteration": best_iteration,
                    "best_score": best_score,
                    "model_sha256": _sha256(model_path),
                    "metadata_sha256": _sha256(metadata_path),
                    "top_feature_gain": [
                        {"feature": feature, "gain": gain} for feature, gain in feature_gain[:20]
                    ],
                }
                model_records.append(model_record)
                model_results[candidate][group_id] = {
                    **model_record,
                    "test_metrics": test_metric,
                    "validation_metrics": validation_metric,
                    "baseline_validation": [
                        row
                        for row in all_metric_rows
                        if row["candidate"] == candidate
                        and row["group_id"] == group_id
                        and row["split"] == "validation"
                        and row["scope"] == "baseline"
                    ],
                    "baseline_test": [
                        row
                        for row in all_metric_rows
                        if row["candidate"] == candidate
                        and row["group_id"] == group_id
                        and row["split"] == "test"
                        and row["scope"] == "baseline"
                    ],
                    "per_unit_validation": per_unit_validation,
                    "per_unit_test": per_unit_test,
                    "seen_unseen_validation": seen_validation,
                    "seen_unseen_test": seen_test,
                    "quality_validation": quality_validation,
                    "quality_test": quality_test,
                }
                del test_features, test, model, encoder
                gc.collect()
        prediction_writer.close()
    except Exception:
        prediction_writer.abort()
        raise

    metrics_frame = pd.DataFrame(all_metric_rows)
    metric_sort = [
        column
        for column in (
            "candidate",
            "group_id",
            "split",
            "scope",
            "baseline",
            "observation_unit_id",
            "stratum_type",
            "stratum",
            "feature",
        )
        if column in metrics_frame.columns
    ]
    metrics_frame = metrics_frame.sort_values(
        metric_sort, na_position="last", kind="mergesort"
    ).reset_index(drop=True)
    metrics_csv = output_dir / "metrics.csv"
    _atomic_csv(metrics_csv, metrics_frame)
    per_unit_frame = pd.DataFrame(per_unit_rows)
    per_unit_frame = per_unit_frame.sort_values(
        ["candidate", "group_id", "split", "observation_unit_id"],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    per_unit_csv = output_dir / "per_unit_metrics.csv"
    _atomic_csv(per_unit_csv, per_unit_frame)
    quality_frame = metrics_frame.loc[
        metrics_frame["scope"].isin(
            ["quality_stratum", "missingness_stratum", "feature_missingness"]
        )
    ].reset_index(drop=True)
    quality_csv = output_dir / "quality_metrics.csv"
    _atomic_csv(quality_csv, quality_frame)

    winners: dict[str, dict[str, Any]] = {}
    for group_id in base_groups:
        candidates = [model_results[candidate][group_id] for candidate in DATASET_NAMES]
        winner = min(candidates, key=lambda result: _winner_key(result, candidate_order))
        winners[group_id] = {
            "source_group": group_id,
            "label_source": _group_parts(group_id)[0],
            "measurement_scope": _group_parts(group_id)[1],
            "candidate": winner["candidate"],
            "model_path": winner["model_path"],
            "metadata_path": winner["metadata_path"],
            "selection_split": "validation",
            "validation_metrics": winner["validation_metrics"],
            "test_metrics": winner["test_metrics"],
            "selection_key": list(_winner_key(winner, candidate_order)),
            "tie_break": "validation poisson_deviance, then validation MAE, then validation RMSE, then base before lag_enhanced",
        }

    metrics_json = output_dir / "metrics.json"
    checksums_path = output_dir / "checksums.json"
    evaluation_manifest_path = output_dir / "evaluation_manifest.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "git_head": _git_head(),
        "python_version": platform.python_version(),
        "seed": args.seed,
        "objective": "count:poisson",
        "tree_method": "hist",
        "run_mode": "small_data" if sample_limit is not None else "full",
        "requested_device": args.device,
        "actual_devices": actual_devices,
        "cuda_attempts": cuda_attempts,
        "cuda_used": any(device == "cuda" for device in actual_devices.values()),
        "memory_policy": {
            "batch_size_rows": 250_000,
            "resident_pandas_scope": "one candidate and one label_source/measurement_scope at a time",
            "test_alignment": "compact key/label index only",
            "prediction_output": "streamed CSV; full mode omits train predictions",
            "numeric_downcast": "float features to float32 and integer features downcast",
            "configured_row_cap": sample_limit,
            "full_run_uses_all_rows": sample_limit is None,
        },
        "reproducibility": {
            "seed": args.seed,
            "sample_rows_per_split_and_group": sample_limit,
            "candidate_order": list(DATASET_NAMES),
            "selection_split": "validation",
            "tie_break": "validation poisson_deviance, then validation MAE, then validation RMSE, then base before lag_enhanced",
            "variant_xgboost_parameters": {
                model_key: _model_parameters(args, device)
                for model_key, device in actual_devices.items()
            },
        },
        "manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path)},
        "split_contract": {
            "strategy": "chronological/as-of; post_test is excluded from claimed scores",
            **dict(split_contract),
            "observed_dates": observed_splits,
        },
        "candidate_test_alignment": alignment,
        "datasets": {
            name: {
                "path": str(dataset.contract.path),
                "sha256": dataset.contract.sha256,
                "rows_inspected": dataset.row_count,
                "split_counts": dict(dataset.split_counts),
                "feature_columns": list(dataset.contract.feature_columns),
                "target_column": TARGET_COLUMN,
                "observation_key": dataset.contract.columns["observation_key"],
                "split": dataset.contract.columns["split"],
                "observation_unit_id": dataset.contract.columns["observation_unit_id"],
                "label_source": dataset.contract.columns["label_source"],
                "measurement_scope": dataset.contract.columns["measurement_scope"],
                "prediction_horizon_hours": 1,
            }
            for name, dataset in datasets.items()
        },
        "source_groups": base_groups,
        "models": model_results,
        "model_records": model_records,
        "release": {
            "type": "bundle_of_source_stratified_models",
            "winner_by_source_group": winners,
        },
        "outputs": {
            "predictions_csv": str(predictions_csv),
            "metrics_csv": str(metrics_csv),
            "per_unit_metrics_csv": str(per_unit_csv),
            "quality_metrics_csv": str(quality_csv),
            "checksums_json": str(checksums_path),
            "evaluation_manifest": str(evaluation_manifest_path),
        },
        "scores": all_metric_rows,
        "per_unit_scores": per_unit_rows,
        "notes": [
            "Models are trained separately per label_source and measurement_scope.",
            "Candidate test keys and vehicle_count labels are identical by strict validation.",
            "Post-test rows are retained in feature tables but are excluded from predictions and claimed scores.",
            "Categorical unit identities are fit on training rows only; unseen units map to the missing branch and are reported separately.",
            "Lag baselines report valid-row coverage and never convert missing values to zero.",
            "Full mode uses all rows unless an explicit --sample cap is supplied; any cap is recorded in every model and evaluation manifest.",
        ],
    }
    _atomic_json(metrics_json, report)

    artifact_paths: list[Path] = [
        predictions_csv,
        metrics_csv,
        metrics_json,
        per_unit_csv,
        quality_csv,
        *(Path(record[key]) for record in model_records for key in ("model_path", "metadata_path")),
    ]
    checksum_payload = {
        "schema_version": 1,
        "artifacts": [_artifact_record(path) for path in artifact_paths],
        "model_metadata_pairs": [
            {
                "model_path": record["model_path"],
                "model_sha256": record["model_sha256"],
                "metadata_path": record["metadata_path"],
                "metadata_sha256": record["metadata_sha256"],
            }
            for record in model_records
        ],
    }
    _atomic_json(checksums_path, checksum_payload)

    evaluation_payload = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "metrics_json": str(metrics_json),
        "predictions_csv": str(predictions_csv),
        "metrics_csv": str(metrics_csv),
        "per_unit_metrics_csv": str(per_unit_csv),
        "quality_metrics_csv": str(quality_csv),
        "checksums": str(checksums_path),
        "requested_device": args.device,
        "actual_devices": actual_devices,
        "cuda_attempts": cuda_attempts,
        "cuda_used": any(device == "cuda" for device in actual_devices.values()),
        "candidate_test_alignment": alignment,
        "memory_policy": report["memory_policy"],
        "release": report["release"],
        "model_paths": [record["model_path"] for record in model_records],
        "artifacts": [_artifact_record(path) for path in [*artifact_paths, checksums_path]],
    }
    _atomic_json(evaluation_manifest_path, evaluation_payload)
    return report


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Build a complete evaluation in staging, then atomically publish it."""

    published_dir = args.output_dir.resolve()
    _validate_output_target(published_dir, overwrite=args.overwrite)
    published_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{published_dir.name}.staging-",
            dir=published_dir.parent,
        )
    )
    staged_args = argparse.Namespace(**vars(args))
    staged_args.output_dir = staging_dir
    staged_args.overwrite = True
    try:
        _run_to_output(staged_args)
        _retarget_staged_publication(staging_dir, published_dir)
        _publish_staged_directory(staging_dir, published_dir)
        return json.loads((published_dir / "metrics.json").read_text(encoding="utf-8"))
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--base",
        "--base-dataset",
        "--base-path",
        dest="base",
        type=Path,
        help="override datasets.base.path while retaining its manifest hash",
    )
    parser.add_argument(
        "--lag-enhanced",
        "--lag-enhanced-dataset",
        "--lag-enhanced-path",
        dest="lag_enhanced",
        type=Path,
        help="override datasets.lag_enhanced.path while retaining its manifest hash",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="auto attempts CUDA then CPU; cuda is CUDA-only; cpu is CPU-only",
    )
    parser.add_argument("--cpu", action="store_true", help="alias for --device cpu")
    parser.add_argument("--seed", "--random-state", type=int, default=42)
    parser.add_argument(
        "--sample",
        "--sample-rows",
        "--max-rows",
        "--max-train-rows",
        dest="sample",
        type=int,
        nargs="?",
        const=DEFAULT_SMALL_ROWS,
        help="deterministically cap rows per split and source group",
    )
    parser.add_argument(
        "--small-data",
        "--smoke",
        "--ci",
        action="store_true",
        help=f"small deterministic run (up to {DEFAULT_SMALL_ROWS} rows and {DEFAULT_SMALL_ESTIMATORS} trees)",
    )
    parser.add_argument("--overwrite", action="store_true")
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
    parser.add_argument("--n-jobs", "--threads", type=int, default=1)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE_ROWS,
        help="full-mode Arrow/XGBoost iterator batch size; not a row cap",
    )
    parser.add_argument(
        "--inspection-batch-size",
        type=int,
        default=DEFAULT_INSPECTION_BATCH_SIZE_ROWS,
        help="uncapped validation inspection batch size; not a row cap",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="parent directory for full-mode XGBoost external-memory cache",
    )
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="retain full-mode external-memory cache pages for diagnosis",
    )
    parser.add_argument(
        "--cache-on-host",
        action="store_true",
        help="allow XGBoost to keep external-memory pages in host RAM (default: disk)",
    )
    parser.add_argument(
        "--cache-host-ratio",
        type=float,
        default=None,
        help="GPU external-memory host/(host+device) cache ratio",
    )
    parser.add_argument(
        "--min-cache-page-bytes",
        type=int,
        default=0,
        help="minimum external-memory page size; 0 disables page concatenation",
    )
    parser.add_argument(
        "--max-bin",
        type=int,
        default=DEFAULT_MAX_BIN,
        help="histogram bins; lower values reduce XGBoost cache/VRAM pressure",
    )
    parser.add_argument(
        "--cuda-async-pool",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "use XGBoost's CUDA asynchronous memory pool for full external-memory "
            "training (default: enabled; disable with --no-cuda-async-pool)"
        ),
    )
    args = parser.parse_args(argv)
    if args.cpu:
        args.device = "cpu"
    if args.seed < 0:
        parser.error("--seed must be non-negative")
    if args.sample is not None and args.sample < 1:
        parser.error("--sample must be at least 1")
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
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.inspection_batch_size < 1:
        parser.error("--inspection-batch-size must be at least 1")
    if args.max_bin < 2:
        parser.error("--max-bin must be at least 2")
    if args.min_cache_page_bytes < 0:
        parser.error("--min-cache-page-bytes must be non-negative")
    if args.cache_host_ratio is not None and not 0 <= args.cache_host_ratio <= 1:
        parser.error("--cache-host-ratio must be in [0, 1]")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = run(args)
    except (TrainingError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"[ok] trained {len(report['model_records'])} source-stratified traffic models; "
        f"shared test keys={report['candidate_test_alignment']['shared_test_keys']:,}"
    )
    for record in report["model_records"]:
        print(
            f"     {record['candidate']}/{record['source_group']}: "
            f"device={record['device']}, test={record['test_rows']:,}, "
            f"model={record['model_path']}"
        )
    print(f"     metrics={report['outputs']['metrics_csv']}")
    print(f"     evaluation={report['outputs']['evaluation_manifest']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
