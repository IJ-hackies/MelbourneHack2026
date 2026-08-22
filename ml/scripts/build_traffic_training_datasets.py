#!/usr/bin/env python3
"""Build leakage-safe traffic forecasting feature tables.

The canonical traffic Parquet is the only target boundary.  This builder keeps
every canonical observation row, adds an explicit one-hour prediction
contract, and publishes two views from the same target:

* ``traffic_training_base.parquet`` contains the canonical row plus static
  identity/source semantics, calendar, holiday, and coordinate predictors;
  target-hour quality and lineage evidence remains in the row for diagnostics
  but is never advertised as a model feature;
* ``traffic_training_lag_enhanced.parquet`` adds exact natural-unit target
  lags and strictly past-only rolling statistics.

DuckDB performs the joins, window calculations, ordering, and Parquet writes so
the same path remains practical for tens of millions of canonical rows.  Raw
inputs are never overwritten and the final manifest is published last.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import duckdb
import pyarrow.parquet as pq

try:
    import holidays
except ImportError:  # pragma: no cover - dependency error is reported by main.
    holidays = None


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR = ROOT / "ml" / "traffic" / "processed"
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "traffic" / "training"
MELBOURNE_TIMEZONE = "Australia/Melbourne"

RELEASE_START = dt.date(2024, 1, 1)
TRAIN_END = dt.date(2024, 12, 31)
VALIDATION_END = dt.date(2025, 12, 31)
TEST_START = dt.date(2026, 1, 1)
TEST_END = dt.date(2026, 7, 31)

TARGET_REQUIRED_COLUMNS = frozenset(
    {
        "source_dataset_id",
        "observation_unit_id",
        "hour_start_utc",
        "vehicle_count",
        "measurement_scope",
        "label_source",
    }
)

CONTRACT_COLUMNS = frozenset(
    {
        "feature_asof",
        "prediction_horizon_hours",
        "split",
        "hour_sin",
        "hour_cos",
        "day_of_week_sin",
        "day_of_week_cos",
        "month_sin",
        "month_cos",
        "day_of_year_sin",
        "day_of_year_cos",
        "is_public_holiday",
        "public_holiday_name",
    }
)

LAG_COLUMNS = (
    "vehicle_count_lag_1h",
    "vehicle_count_lag_24h",
    "vehicle_count_lag_168h",
    "vehicle_count_rolling_past_24h_mean",
    "vehicle_count_rolling_past_24h_stddev",
    "vehicle_count_rolling_past_24h_count",
    "vehicle_count_rolling_past_168h_mean",
    "vehicle_count_rolling_past_168h_stddev",
    "vehicle_count_rolling_past_168h_count",
)

# The traffic trainer fits separate models by label_source/measurement_scope.
# Those fields remain in the identity contract, but the trainer itself filters
# on them and must not receive them as model columns.
TRAINER_SEMANTIC_EXCLUSIONS = frozenset(
    {
        "source_dataset_id",
        "label_source",
        "measurement_scope",
    }
)

# These are intentionally explicit.  They are the source fields that can be
# used as model inputs when present in the current canonical schema.  Other
# canonical columns are retained for audit/lineage but are not silently made
# model features.
IDENTITY_CATEGORICAL_COLUMNS = (
    "source_dataset_id",
    "observation_unit_id",
    "measurement_scope",
    "label_source",
    "channel_type",
    "count_location_id",
    "countline_name",
    "scats_site",
    "physical_site_id",
    "review_status",
    "timezone_name",
    "source_timezone_name",
)

IDENTITY_NUMERIC_COLUMNS = (
    "year",
    "month",
    "day",
    "local_hour",
    "day_of_week",
    "is_weekend",
    "latitude",
    "longitude",
)

# These are retained in the Parquet rows and reported as diagnostics, but they
# describe the observed target hour or its source record.  They are therefore
# not available at feature_asof=target-1h and must not enter any predictor list.
# Keep this list explicit: a new canonical quality/lineage field must be
# classified here before it can be considered for modelling.
SAME_HOUR_DIAGNOSTIC_COLUMNS = (
    "label_quality",
    "quality_flag",
    "quality_partial_flag",
    "quality_alarm_flag",
    "quality_missing_interval_count",
    "source_timestamp_utc",
    "source_timestamp_first_utc",
    "source_timestamp_last_utc",
    "source_timestamp_lineage",
    "source_timestamp_count",
    "source_timestamp_semantics",
    "source_archive_member",
    "source_date_local",
    "source_row_count",
    "source_record_count",
    "traffic_eligible",
    "coordinate_valid",
    "coordinate_missing",
    "coordinate_drift_flag",
    "ta_motor_class_rows",
    "ta_non_motor_class_rows",
    "ta_reported_class_rows",
    "ta_derived_zero",
    "ta_dst_ambiguous_flag",
    "ta_dst_fallback_wrap_flag",
    "scats_detector_count",
    "scats_detector_row_count",
    "scats_ct_records_min",
    "scats_ct_records_max",
    "scats_qt_volume_24hour_sum",
    "scats_alarm_24hour_count",
    "scats_source_date_local",
    # These are calendar-derived, but expose target-hour DST state/evidence.
    "is_dst",
    "source_timezone_offset_minutes",
    "local_utc_offset_minutes",
)

# Same-row labels, direct label transforms/equivalents, and target-hour
# diagnostics are retained in the output for auditability but can never appear
# in a feature list.
LEAKAGE_EXCLUDED_COLUMNS = (
    "vehicle_count",
    "intersection_total",
    "log1p_vehicle_count",
    "observation_id",
    *SAME_HOUR_DIAGNOSTIC_COLUMNS,
)


class BuildError(RuntimeError):
    """Actionable failure at the traffic training-data boundary."""


_MEMORY_LIMIT_PATTERN = re.compile(
    r"^(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)?$",
    re.IGNORECASE,
)
_MEMORY_LIMIT_UNITS = {
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}
_MIN_MEMORY_LIMIT_BYTES = 256 * 1024**2
_MAX_MEMORY_LIMIT_BYTES = 1 << 50


def _memory_limit_details(value: Any) -> tuple[str, int]:
    """Validate a DuckDB memory budget and return its stable text/byte forms."""

    text = str(value).strip()
    match = _MEMORY_LIMIT_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(
            "memory limit must be a positive number with an optional unit "
            "(for example 8GiB, 8000MB, or 8589934592B)"
        )
    try:
        amount = Decimal(match.group("amount"))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - regex guards this.
        raise ValueError("memory limit amount is invalid") from exc
    unit = (match.group("unit") or "B").upper()
    multiplier = _MEMORY_LIMIT_UNITS[unit]
    byte_count = int(amount * multiplier)
    if byte_count <= 0:
        raise ValueError("memory limit must be positive")
    if byte_count < _MIN_MEMORY_LIMIT_BYTES:
        raise ValueError("memory limit must be at least 256MiB")
    if byte_count > _MAX_MEMORY_LIMIT_BYTES:
        raise ValueError("memory limit must not exceed 1TiB")

    amount_text = format(amount, "f")
    if "." in amount_text:
        amount_text = amount_text.rstrip("0").rstrip(".")
    amount_text = amount_text or "0"
    canonical_unit = {
        "B": "B",
        "KB": "KB",
        "MB": "MB",
        "GB": "GB",
        "TB": "TB",
        "KIB": "KiB",
        "MIB": "MiB",
        "GIB": "GiB",
        "TIB": "TiB",
    }[unit]
    return f"{amount_text}{canonical_unit}", byte_count


def _memory_limit_arg(value: str) -> str:
    try:
        normalized, _ = _memory_limit_details(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return normalized


def _sql_path(path: Path) -> str:
    """Return a DuckDB-safe absolute path literal body."""

    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _schema_descriptor(path: Path) -> list[dict[str, Any]]:
    schema = pq.read_schema(path)
    return [
        # Keep the descriptor compatible with the existing traffic trainer's
        # schema verifier, which hashes the stable name/type contract.  Arrow
        # nullability remains enforced by the row-level target/key checks.
        {"name": field.name, "type": str(field.type)}
        for field in schema
    ]


def _schema_hash(descriptor: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(_stable_json(list(descriptor)).encode("utf-8")).hexdigest()


def _manifest_path_basename(value: Any) -> str | None:
    if not value:
        return None
    return str(value).replace("\\", "/").rstrip("/").split("/")[-1] or None


def _manifest_output_path(payload: Mapping[str, Any]) -> str | None:
    outputs = payload.get("outputs")
    if not isinstance(outputs, Mapping):
        return None
    parquet_output = outputs.get("parquet")
    if not isinstance(parquet_output, Mapping):
        return None
    return _manifest_path_basename(parquet_output.get("path"))


def _manifest_is_partial(payload: Mapping[str, Any]) -> bool:
    """Recognise the canonical builder's partial markers conservatively."""

    for key in ("artifact_status", "status", "readiness_status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "partial",
            "incomplete",
            "failed",
            "unready",
        }:
            return True
    if payload.get("partial") is True or payload.get("complete") is False:
        return True
    coverage = payload.get("coverage")
    if isinstance(coverage, Mapping):
        if coverage.get("partial") is True:
            return True
        for key in (
            "unexpected_missing_scats_date_count",
            "missing_date_count",
            "missing_source_count",
            "missing_scats_year_count",
        ):
            try:
                if int(coverage.get(key, 0) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        for key in ("missing_ta_sources", "missing_sources", "missing_scats_years"):
            value = coverage.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
                return True
    return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"unable to read canonical manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BuildError(f"canonical manifest must contain a JSON object: {path}")
    return payload


def _target_from_manifest(manifest_path: Path, payload: Mapping[str, Any]) -> Path | None:
    basename = _manifest_output_path(payload)
    if basename:
        candidate = manifest_path.parent / basename
        if candidate.is_file():
            return candidate.resolve()
    outputs = payload.get("outputs")
    if isinstance(outputs, Mapping):
        parquet_output = outputs.get("parquet")
        if isinstance(parquet_output, Mapping):
            raw_path = parquet_output.get("path")
            if raw_path:
                raw = Path(str(raw_path))
                if raw.is_file():
                    return raw.resolve()
    return None


def _resolve_canonical_inputs(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any]]:
    target = args.target.expanduser().resolve() if args.target else None
    manifest_path = (
        args.canonical_manifest.expanduser().resolve()
        if args.canonical_manifest
        else None
    )

    if manifest_path is None and target is not None:
        likely = target.with_name(f"{target.stem}_manifest.json")
        if likely.is_file():
            manifest_path = likely
        else:
            matching = []
            for candidate in sorted(target.parent.glob("*_manifest.json")):
                payload = _read_json(candidate)
                if _manifest_output_path(payload) == target.name:
                    matching.append(candidate)
            if len(matching) == 1:
                manifest_path = matching[0]

    if manifest_path is None:
        candidates = sorted(
            path
            for path in DEFAULT_PROCESSED_DIR.glob("*_manifest.json")
            if path.name != "training_manifest.json"
        )
        resolved: list[tuple[Path, Path]] = []
        for candidate in candidates:
            payload = _read_json(candidate)
            candidate_target = _target_from_manifest(candidate, payload)
            if candidate_target is not None:
                resolved.append((candidate, candidate_target))
        if len(resolved) == 1:
            manifest_path, inferred_target = resolved[0]
            target = target or inferred_target
        elif not resolved:
            raise BuildError(
                "no canonical traffic manifest/output pair found; pass --target and --canonical-manifest"
            )
        else:
            raise BuildError(
                "multiple canonical traffic manifests found; pass --target and --canonical-manifest explicitly"
            )

    if not manifest_path.is_file():
        raise BuildError(f"canonical manifest not found: {manifest_path}")
    payload = _read_json(manifest_path)
    if _manifest_is_partial(payload):
        raise BuildError(
            f"refusing traffic feature construction from partial canonical manifest: {manifest_path}"
        )

    if target is None:
        target = _target_from_manifest(manifest_path, payload)
    if target is None:
        raise BuildError(
            f"canonical manifest does not identify an existing Parquet target: {manifest_path}"
        )
    target = target.expanduser().resolve()
    if not target.is_file():
        raise BuildError(f"canonical traffic target not found: {target}")

    manifest_target_name = _manifest_output_path(payload)
    if manifest_target_name and manifest_target_name != target.name:
        raise BuildError(
            "canonical manifest points at a different target Parquet: "
            f"{manifest_target_name} != {target.name}"
        )

    parquet_output = payload.get("outputs", {}).get("parquet", {})
    if isinstance(parquet_output, Mapping):
        expected_bytes = parquet_output.get("bytes")
        if expected_bytes is not None and int(expected_bytes) != target.stat().st_size:
            raise BuildError(
                f"canonical target byte count disagrees with manifest: {target}"
            )
        expected_hash = parquet_output.get("sha256")
        if expected_hash:
            actual_hash = _sha256(target)
            if str(expected_hash).lower() != actual_hash:
                raise BuildError(
                    f"canonical target SHA-256 disagrees with manifest: {target}"
                )
    return target, manifest_path, payload


def _date_expression(alias: str, columns: set[str]) -> str:
    if "local_date" in columns:
        return f"CAST({alias}.{_quote_identifier('local_date')} AS DATE)"
    if "local_timestamp" in columns:
        return f"CAST({alias}.{_quote_identifier('local_timestamp')} AS DATE)"
    return (
        f"CAST({alias}.{_quote_identifier('hour_start_utc')} AT TIME ZONE "
        f"'{MELBOURNE_TIMEZONE}' AS DATE)"
    )


def _hour_expression(alias: str, columns: set[str], date_expression: str) -> str:
    if "local_hour" in columns:
        return f"CAST({alias}.{_quote_identifier('local_hour')} AS INTEGER)"
    if "local_timestamp" in columns:
        return f"CAST(EXTRACT(HOUR FROM {alias}.{_quote_identifier('local_timestamp')}) AS INTEGER)"
    return f"CAST(EXTRACT(HOUR FROM {date_expression}) AS INTEGER)"


def _derived_observation_key_expression(alias: str) -> str:
    """Build a collision-resistant key from the canonical natural key only."""

    source = f"CAST({alias}.{_quote_identifier('source_dataset_id')} AS VARCHAR)"
    unit = f"CAST({alias}.{_quote_identifier('observation_unit_id')} AS VARCHAR)"
    epoch_us = (
        f"CAST(epoch_us(CAST({alias}.{_quote_identifier('hour_start_utc')} "
        "AS TIMESTAMPTZ)) AS VARCHAR)"
    )
    # Length-prefix the free-text fields so delimiters in publisher IDs cannot
    # create an ambiguous key.  The timestamp is represented as UTC epoch
    # microseconds; the canonical cleaner has already enforced hourly values.
    return (
        f"'v1:' || CAST(LENGTH({source}) AS VARCHAR) || ':' || {source} || ':' || "
        f"CAST(LENGTH({unit}) AS VARCHAR) || ':' || {unit} || ':' || {epoch_us}"
    )


def _existing_observation_key_is_valid(
    connection: duckdb.DuckDBPyConnection,
    target: Path,
    source_columns: set[str],
) -> bool:
    """Return whether an existing key column is complete and globally unique."""

    if "observation_key" not in source_columns:
        return False
    relation = f"read_parquet('{_sql_path(target)}')"
    row_count, invalid_count, distinct_count = connection.execute(
        "SELECT COUNT(*), "
        "COUNT(*) FILTER (WHERE observation_key IS NULL "
        "OR TRIM(CAST(observation_key AS VARCHAR)) = ''), "
        "COUNT(DISTINCT CAST(observation_key AS VARCHAR)) "
        f"FROM {relation}"
    ).fetchone()
    return (
        int(row_count) > 0
        and int(invalid_count or 0) == 0
        and int(distinct_count) == int(row_count)
    )


def _create_canonical_source_view(
    connection: duckdb.DuckDBPyConnection,
    target: Path,
    source_columns: Sequence[str],
) -> tuple[str, list[str]]:
    """Expose a normalized canonical relation without rewriting the input."""

    use_existing = _existing_observation_key_is_valid(
        connection, target, set(source_columns)
    )
    source_alias = "s"
    key_expression = (
        f"CAST({source_alias}.{_quote_identifier('observation_key')} AS VARCHAR)"
        if use_existing
        else _derived_observation_key_expression(source_alias)
    )
    normalized_columns = [
        "observation_key",
        *[name for name in source_columns if name != "observation_key"],
    ]
    projection = [f"{key_expression} AS {_quote_identifier('observation_key')}"]
    projection.extend(
        f"{source_alias}.{_quote_identifier(name)}"
        for name in source_columns
        if name != "observation_key"
    )
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW canonical_source AS SELECT "
        + ", ".join(projection)
        + f" FROM read_parquet('{_sql_path(target)}') AS {source_alias}"
    )
    return ("existing_valid" if use_existing else "derived_natural_key"), normalized_columns


def _day_of_week_expression(alias: str, columns: set[str], date_expression: str) -> str:
    if "day_of_week" in columns:
        return f"CAST({alias}.{_quote_identifier('day_of_week')} AS INTEGER)"
    return f"CAST(DAYOFWEEK({date_expression}) AS INTEGER)"


def _split_expression(date_column: str) -> str:
    return f"CASE WHEN {date_column} BETWEEN DATE '{RELEASE_START.isoformat()}' AND DATE '{TRAIN_END.isoformat()}' THEN 'train' " \
        f"WHEN {date_column} BETWEEN DATE '{(TRAIN_END + dt.timedelta(days=1)).isoformat()}' AND DATE '{VALIDATION_END.isoformat()}' THEN 'validation' " \
        f"WHEN {date_column} BETWEEN DATE '{TEST_START.isoformat()}' AND DATE '{TEST_END.isoformat()}' THEN 'test' " \
        "ELSE 'invalid_release_date' END"


def _feature_lists(source_columns: Sequence[str], *, enhanced: bool) -> dict[str, list[str]]:
    available = set(source_columns)
    all_categorical: list[str] = []
    all_numeric: list[str] = []

    def add_unique(destination: list[str], name: str) -> None:
        if name not in destination and name not in LEAKAGE_EXCLUDED_COLUMNS:
            destination.append(name)

    # Identity/source semantics are safe only when they describe the fixed
    # observation unit or publisher contract.  Same-hour quality/lineage
    # fields are deliberately not iterated here even though they remain in the
    # output Parquet.
    for name in IDENTITY_CATEGORICAL_COLUMNS:
        if name in available:
            add_unique(all_categorical, name)
    for name in (
        *IDENTITY_NUMERIC_COLUMNS,
        "hour_sin",
        "hour_cos",
        "day_of_week_sin",
        "day_of_week_cos",
        "month_sin",
        "month_cos",
        "day_of_year_sin",
        "day_of_year_cos",
        "is_public_holiday",
    ):
        if name in available or name in CONTRACT_COLUMNS:
            add_unique(all_numeric, name)
    if "public_holiday_name" in CONTRACT_COLUMNS:
        add_unique(all_categorical, "public_holiday_name")
    if enhanced:
        for name in LAG_COLUMNS:
            add_unique(all_numeric, name)

    overlap = set(all_categorical) & set(all_numeric)
    if overlap:
        raise BuildError(f"feature lists overlap: {sorted(overlap)}")
    all_features = [*all_categorical, *all_numeric]
    if any(name in LEAKAGE_EXCLUDED_COLUMNS for name in all_features):
        raise BuildError("a leakage-excluded column entered the feature list")
    model_categorical = [
        name for name in all_categorical if name not in TRAINER_SEMANTIC_EXCLUSIONS
    ]
    model_numeric = [
        name for name in all_numeric if name not in TRAINER_SEMANTIC_EXCLUSIONS
    ]
    return {
        "feature_columns": [*model_categorical, *model_numeric],
        "categorical_features": model_categorical,
        "numeric_features": model_numeric,
        # This is an audit inventory, not a predictor list.  It includes
        # diagnostic columns so downstream tooling can see which target-hour
        # evidence was available in the row without accidentally training on
        # it.  The model-facing lists above remain strictly leakage-safe.
        "train_available_features": [
            *all_features,
            *[
                name
                for name in SAME_HOUR_DIAGNOSTIC_COLUMNS
                if name in available and name not in all_features
            ],
        ],
        "identity_features": [
            name for name in (*IDENTITY_CATEGORICAL_COLUMNS, *IDENTITY_NUMERIC_COLUMNS)
            if name in available
        ],
        "quality_features": [
            name
            for name in SAME_HOUR_DIAGNOSTIC_COLUMNS
            if name in available
        ],
    }


def _create_holiday_table(
    connection: duckdb.DuckDBPyConnection,
    min_date: dt.date,
    max_date: dt.date,
) -> None:
    if holidays is None:
        raise BuildError("the holidays package is required; install ml/requirements.txt")
    if max_date < min_date:
        raise BuildError("canonical target has an invalid local-date range")
    calendar = holidays.Australia(
        subdiv="VIC", years=range(min_date.year, max_date.year + 1)
    )
    rows: list[tuple[dt.date, bool, str | None]] = []
    current = min_date
    while current <= max_date:
        name = calendar.get(current)
        rows.append((current, name is not None, str(name) if name is not None else None))
        current += dt.timedelta(days=1)
    connection.execute(
        "CREATE TEMP TABLE holiday_calendar(" \
        "local_date DATE, is_public_holiday BOOLEAN, public_holiday_name VARCHAR)"
    )
    connection.executemany("INSERT INTO holiday_calendar VALUES (?, ?, ?)", rows)


def _copy_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    destination: Path,
) -> None:
    destination.unlink(missing_ok=True)
    connection.execute(
        f"COPY ({query}) TO '{_sql_path(destination)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)"
    )


def _write_preview(
    connection: duckdb.DuckDBPyConnection,
    source: Path,
    destination: Path,
    rows: int,
) -> int:
    destination.unlink(missing_ok=True)
    query = (
        f"SELECT * FROM read_parquet('{_sql_path(source)}') "
        f"ORDER BY {_quote_identifier('observation_key')} LIMIT {int(rows)}"
    )
    connection.execute(
        f"COPY ({query}) TO '{_sql_path(destination)}' "
        "(FORMAT CSV, HEADER, DELIMITER ',')"
    )
    result = connection.execute(
        f"SELECT COUNT(*) FROM read_parquet('{_sql_path(source)}')"
    ).fetchone()
    return min(int(result[0]), rows)


def _publish_staged_artifacts(
    staged: Mapping[str, Path],
    destinations: Mapping[str, Path],
    temporary: Path,
) -> None:
    """Publish the feature artifact set with per-file rollback.

    A directory swap is deliberately avoided because ``output_dir`` also
    contains the separate training evaluation.  The five feature artifacts are
    first moved to same-filesystem sibling paths, then replaced one at a time.
    If any replacement fails, newly published files are removed and every
    previous file is restored before the original exception is re-raised.
    """

    order = ("base", "lag_enhanced", "base_preview", "lag_enhanced_preview", "manifest")
    backup_dir = temporary / "publication-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups: dict[Path, Path] = {}
    published: list[Path] = []

    try:
        for key in order:
            staged_path = staged[key]
            if not staged_path.is_file():
                raise BuildError(f"staged traffic training artifact is missing: {staged_path}")

        for key in order:
            destination = destinations[key]
            if destination.exists():
                backup = backup_dir / destination.name
                os.replace(destination, backup)
                backups[destination] = backup
            os.replace(staged[key], destination)
            published.append(destination)
    except Exception as exc:
        rollback_errors: list[str] = []

        for destination in reversed(published):
            try:
                destination.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(
                    f"remove {destination}: {rollback_error}"
                )

        for destination, backup in reversed(tuple(backups.items())):
            if not backup.exists():
                continue
            try:
                os.replace(backup, destination)
            except OSError as rollback_error:
                rollback_errors.append(
                    f"restore {destination}: {rollback_error}"
                )

        if rollback_errors:
            raise BuildError(
                "traffic training publication failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        raise


def _validate_target(
    connection: duckdb.DuckDBPyConnection,
    target_relation: str,
    source_columns: set[str],
    manifest_payload: Mapping[str, Any],
) -> tuple[int, dt.date, dt.date]:
    missing = sorted(TARGET_REQUIRED_COLUMNS - source_columns)
    if missing:
        raise BuildError(
            "canonical traffic target is missing required columns: " + ", ".join(missing)
        )
    relation = target_relation
    row_count, null_keys, invalid_targets = connection.execute(
        f"SELECT COUNT(*), "
        f"COUNT(*) FILTER (WHERE observation_key IS NULL OR source_dataset_id IS NULL "
        f"OR observation_unit_id IS NULL OR label_source IS NULL "
        f"OR measurement_scope IS NULL OR hour_start_utc IS NULL), "
        f"COUNT(*) FILTER (WHERE vehicle_count IS NULL OR vehicle_count < 0 "
        f"OR vehicle_count != FLOOR(vehicle_count)) "
        f"FROM {relation}"
    ).fetchone()
    if not row_count:
        raise BuildError("canonical traffic target is empty")
    if null_keys:
        raise BuildError(f"canonical traffic target has {null_keys} null natural-key fields")
    if invalid_targets:
        raise BuildError(
            f"canonical traffic target has {invalid_targets} null/negative/non-integer targets"
        )
    duplicate_key = connection.execute(
        f"SELECT 1 FROM {relation} GROUP BY observation_key HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicate_key:
        raise BuildError("canonical traffic observation_key is not unique")
    duplicate_natural_key = connection.execute(
        f"SELECT 1 FROM {relation} "
        "GROUP BY source_dataset_id, observation_unit_id, hour_start_utc "
        "HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicate_natural_key:
        raise BuildError("canonical traffic natural unit/hour key is not unique")

    date_expression = _date_expression("t", source_columns)
    min_date, max_date = connection.execute(
        f"SELECT MIN({date_expression}), MAX({date_expression}) FROM {relation} t"
    ).fetchone()
    if min_date is None or max_date is None:
        raise BuildError("canonical traffic target has no usable local dates")
    if not isinstance(min_date, dt.date):
        min_date = dt.date.fromisoformat(str(min_date)[:10])
    if not isinstance(max_date, dt.date):
        max_date = dt.date.fromisoformat(str(max_date)[:10])

    null_date_rows, before_release_rows, after_release_rows = connection.execute(
        f"SELECT "
        f"COUNT(*) FILTER (WHERE ({date_expression}) IS NULL), "
        f"COUNT(*) FILTER (WHERE ({date_expression}) < DATE '{RELEASE_START.isoformat()}'), "
        f"COUNT(*) FILTER (WHERE ({date_expression}) > DATE '{TEST_END.isoformat()}') "
        f"FROM {relation} t"
    ).fetchone()
    if null_date_rows:
        raise BuildError(
            f"canonical traffic target has {null_date_rows} row(s) without a usable local date"
        )
    if before_release_rows or after_release_rows:
        raise BuildError(
            "canonical traffic target violates the exact release date contract "
            f"({RELEASE_START.isoformat()} through {TEST_END.isoformat()}): "
            f"before_release={before_release_rows}, after_test_end={after_release_rows}"
        )

    manifest_rows = manifest_payload.get("outputs", {}).get("parquet", {}).get("rows")
    if manifest_rows is not None and int(manifest_rows) != int(row_count):
        raise BuildError(
            f"canonical target row count disagrees with manifest: {row_count} != {manifest_rows}"
        )
    return int(row_count), min_date, max_date


def _assert_candidate_keys(
    connection: duckdb.DuckDBPyConnection,
    target_relation: str,
    base: Path,
    enhanced: Path,
    expected_rows: int,
) -> None:
    target_sql = target_relation
    base_sql = _sql_path(base)
    enhanced_sql = _sql_path(enhanced)
    for label, path in (("base", base), ("lag_enhanced", enhanced)):
        sql = _sql_path(path)
        rows, keys = connection.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT observation_key) FROM read_parquet('{sql}')"
        ).fetchone()
        if int(rows) != expected_rows or int(keys) != expected_rows:
            raise BuildError(
                f"{label} candidate changed target rows or keys: rows={rows}, keys={keys}, expected={expected_rows}"
            )
    mismatch = connection.execute(
        f"SELECT COUNT(*) FROM ("
        f"SELECT observation_key FROM read_parquet('{base_sql}') "
        f"EXCEPT SELECT observation_key FROM read_parquet('{enhanced_sql}')"
        f")"
    ).fetchone()[0]
    reverse_mismatch = connection.execute(
        f"SELECT COUNT(*) FROM ("
        f"SELECT observation_key FROM read_parquet('{enhanced_sql}') "
        f"EXCEPT SELECT observation_key FROM read_parquet('{base_sql}')"
        f")"
    ).fetchone()[0]
    if mismatch or reverse_mismatch:
        raise BuildError(
            f"base and lag_enhanced key sets differ: base_only={mismatch}, enhanced_only={reverse_mismatch}"
        )
    target_mismatch = connection.execute(
        f"SELECT COUNT(*) FROM ("
        f"SELECT observation_key FROM {target_sql} "
        f"EXCEPT SELECT observation_key FROM read_parquet('{base_sql}')"
        f")"
    ).fetchone()[0]
    if target_mismatch:
        raise BuildError(f"base candidate is missing {target_mismatch} canonical target keys")


def _split_counts(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT split, COUNT(*) FROM read_parquet('{_sql_path(path)}') "
        "GROUP BY split ORDER BY split"
    ).fetchall()
    return {str(name): int(count) for name, count in rows}


def _quality_counts(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    columns: set[str],
) -> dict[str, int]:
    expressions: list[str] = []
    for name in (
        "quality_partial_flag",
        "quality_alarm_flag",
        "coordinate_missing",
        "coordinate_drift_flag",
        "ta_derived_zero",
    ):
        if name in columns:
            expressions.append(
                f"COUNT(*) FILTER (WHERE {_quote_identifier(name)}) AS {_quote_identifier(name)}"
            )
    if not expressions:
        return {}
    values = connection.execute(
        f"SELECT {', '.join(expressions)} FROM read_parquet('{_sql_path(path)}')"
    ).fetchone()
    return {name: int(value or 0) for name, value in zip(
        (name for name in (
            "quality_partial_flag",
            "quality_alarm_flag",
            "coordinate_missing",
            "coordinate_drift_flag",
            "ta_derived_zero",
        ) if name in columns),
        values,
    )}


def _dataset_manifest(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    preview: Path,
    preview_rows: int,
    feature_lists: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    descriptor = _schema_descriptor(path)
    rows = int(
        connection.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_sql_path(path)}')"
        ).fetchone()[0]
    )
    return {
        "path": path.name,
        "preview": preview.name,
        "rows": rows,
        "row_count": rows,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "preview_rows": int(preview_rows),
        "preview_bytes": preview.stat().st_size,
        "preview_sha256": _sha256(preview),
        "schema": descriptor,
        "schema_hash": _schema_hash(descriptor),
        "target_column": "vehicle_count",
        "categorical_features": list(feature_lists["categorical_features"]),
        "numeric_features": list(feature_lists["numeric_features"]),
        "feature_columns": list(feature_lists["feature_columns"]),
        "train_available_features": list(feature_lists["train_available_features"]),
        "identity_features": list(feature_lists["identity_features"]),
        "quality_features": list(feature_lists["quality_features"]),
        "split_counts": _split_counts(connection, path),
    }


def _base_query(source_relation: str, source_columns: Sequence[str]) -> str:
    columns = set(source_columns)
    date_expression = _date_expression("s", columns)
    hour_expression = _hour_expression("d", columns, "d.__local_date")
    dow_expression = _day_of_week_expression("d", columns, "d.__local_date")
    if "is_weekend" in columns:
        weekend_expression = f"CAST(d.{_quote_identifier('is_weekend')} AS BOOLEAN)"
    else:
        weekend_expression = "(d.__day_of_week IN (0, 6))"
    if "is_dst" in columns:
        dst_expression = f"CAST(d.{_quote_identifier('is_dst')} AS BOOLEAN)"
    else:
        dst_expression = "NULL::BOOLEAN"
    if "year" in columns:
        year_expression = f"CAST(d.{_quote_identifier('year')} AS INTEGER)"
    else:
        year_expression = "YEAR(d.__local_date)"
    if "month" in columns:
        month_expression = f"CAST(d.{_quote_identifier('month')} AS INTEGER)"
    else:
        month_expression = "MONTH(d.__local_date)"
    split_expression = _split_expression("e.__local_date")
    return f"""
        WITH source AS (
            SELECT * FROM {source_relation}
        ), dated AS (
            SELECT
                s.*,
                {date_expression} AS __local_date
            FROM source s
        ), enriched AS (
            SELECT
                d.*,
                {hour_expression} AS __local_hour,
                {dow_expression} AS __day_of_week,
                {year_expression} AS __year,
                {month_expression} AS __month,
                {weekend_expression} AS __is_weekend,
                {dst_expression} AS __is_dst
            FROM dated d
        )
        SELECT
            {', '.join(f'e.{_quote_identifier(name)}' for name in source_columns)},
            e.{_quote_identifier('hour_start_utc')} - INTERVAL 1 HOUR AS feature_asof,
            CAST(1 AS SMALLINT) AS prediction_horizon_hours,
            {split_expression} AS split,
            SIN(2 * PI() * e.__local_hour / 24.0) AS hour_sin,
            COS(2 * PI() * e.__local_hour / 24.0) AS hour_cos,
            SIN(2 * PI() * e.__day_of_week / 7.0) AS day_of_week_sin,
            COS(2 * PI() * e.__day_of_week / 7.0) AS day_of_week_cos,
            SIN(2 * PI() * e.__month / 12.0) AS month_sin,
            COS(2 * PI() * e.__month / 12.0) AS month_cos,
            SIN(2 * PI() * DAYOFYEAR(e.__local_date) / 365.25) AS day_of_year_sin,
            COS(2 * PI() * DAYOFYEAR(e.__local_date) / 365.25) AS day_of_year_cos,
            h.is_public_holiday,
            h.public_holiday_name
        FROM enriched e
        LEFT JOIN holiday_calendar h ON h.local_date = e.__local_date
        ORDER BY e.{_quote_identifier('observation_key')}
    """


def _enhanced_query(base: Path) -> str:
    base_sql = _sql_path(base)
    past_window = "RANGE BETWEEN INTERVAL {hours} HOURS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING"
    exact_window = "RANGE BETWEEN INTERVAL {hours} HOURS PRECEDING AND INTERVAL {hours} HOURS PRECEDING"
    partition_and_order = """
        PARTITION BY b.label_source, b.measurement_scope, b.observation_unit_id
        ORDER BY b.hour_start_utc
    """
    return f"""
        WITH base AS (
            SELECT * FROM read_parquet('{base_sql}')
        ), enhanced AS (
            SELECT
                b.*,
                MAX(b.vehicle_count) OVER (
                    {partition_and_order}
                    {exact_window.format(hours=1)}
                ) AS vehicle_count_lag_1h,
                MAX(b.vehicle_count) OVER (
                    {partition_and_order}
                    {exact_window.format(hours=24)}
                ) AS vehicle_count_lag_24h,
                MAX(b.vehicle_count) OVER (
                    {partition_and_order}
                    {exact_window.format(hours=168)}
                ) AS vehicle_count_lag_168h,
                AVG(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=24)}
                ) AS vehicle_count_rolling_past_24h_mean,
                STDDEV_SAMP(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=24)}
                ) AS vehicle_count_rolling_past_24h_stddev,
                NULLIF(COUNT(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=24)}
                ), 0) AS vehicle_count_rolling_past_24h_count,
                AVG(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=168)}
                ) AS vehicle_count_rolling_past_168h_mean,
                STDDEV_SAMP(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=168)}
                ) AS vehicle_count_rolling_past_168h_stddev,
                NULLIF(COUNT(b.vehicle_count) OVER (
                    {partition_and_order}
                    {past_window.format(hours=168)}
                ), 0) AS vehicle_count_rolling_past_168h_count
            FROM base b
        )
        SELECT * FROM enhanced
        ORDER BY observation_key
    """


def build(args: argparse.Namespace) -> dict[str, Any]:
    target, canonical_manifest, canonical_manifest_payload = _resolve_canonical_inputs(args)
    source_schema = pq.read_schema(target)
    source_columns = list(source_schema.names)
    source_column_set = set(source_columns)
    collisions = sorted(source_column_set & CONTRACT_COLUMNS)
    if collisions:
        raise BuildError(
            "canonical target already contains training-contract columns: "
            + ", ".join(collisions)
        )

    output_dir = args.output_dir.expanduser().resolve()
    destinations = {
        "base": output_dir / "traffic_training_base.parquet",
        "lag_enhanced": output_dir / "traffic_training_lag_enhanced.parquet",
        "base_preview": output_dir / "traffic_training_base_preview.csv",
        "lag_enhanced_preview": output_dir / "traffic_training_lag_enhanced_preview.csv",
        "manifest": output_dir / "training_manifest.json",
    }
    existing = [path for path in destinations.values() if path.exists()]
    if existing and not args.overwrite:
        raise BuildError(
            "traffic training outputs already exist; pass --overwrite: "
            + ", ".join(str(path) for path in existing)
        )
    if args.preview_rows < 1 or args.preview_rows > 100_000:
        raise BuildError("--preview-rows must be between 1 and 100000")
    if args.threads < 1:
        raise BuildError("--threads must be positive")
    memory_limit, memory_limit_bytes = _memory_limit_details(args.memory_limit)

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".traffic-training-", dir=output_dir) as temporary_name:
        temporary = Path(temporary_name)
        staged = {
            key: temporary / path.name for key, path in destinations.items()
        }
        connection = duckdb.connect(str(temporary / "build.duckdb"))
        try:
            connection.execute(f"SET memory_limit='{memory_limit}'")
            connection.execute("SET preserve_insertion_order=false")
            connection.execute(f"SET threads TO {int(args.threads)}")
            connection.execute(
                f"SET temp_directory='{_sql_path(temporary / 'duckdb-temp')}'"
            )
            observation_key_mode, normalized_source_columns = _create_canonical_source_view(
                connection, target, source_columns
            )
            expected_rows, min_date, max_date = _validate_target(
                connection,
                "canonical_source",
                set(normalized_source_columns),
                canonical_manifest_payload,
            )
            _create_holiday_table(connection, min_date, max_date)

            _copy_query(
                connection,
                _base_query("canonical_source", normalized_source_columns),
                staged["base"],
            )
            _copy_query(connection, _enhanced_query(staged["base"]), staged["lag_enhanced"])
            base_preview_rows = _write_preview(
                connection,
                staged["base"],
                staged["base_preview"],
                args.preview_rows,
            )
            enhanced_preview_rows = _write_preview(
                connection,
                staged["lag_enhanced"],
                staged["lag_enhanced_preview"],
                args.preview_rows,
            )
            _assert_candidate_keys(
                connection,
                "canonical_source",
                staged["base"],
                staged["lag_enhanced"],
                expected_rows,
            )

            base_columns = pq.read_schema(staged["base"]).names
            enhanced_columns = pq.read_schema(staged["lag_enhanced"]).names
            base_features = _feature_lists(base_columns, enhanced=False)
            enhanced_features = _feature_lists(enhanced_columns, enhanced=True)

            base_details = _dataset_manifest(
                connection,
                staged["base"],
                staged["base_preview"],
                base_preview_rows,
                base_features,
            )
            enhanced_details = _dataset_manifest(
                connection,
                staged["lag_enhanced"],
                staged["lag_enhanced_preview"],
                enhanced_preview_rows,
                enhanced_features,
            )

            manifest: dict[str, Any] = {
                "schema_version": 1,
                "builder": "build_traffic_training_datasets.py",
                "artifact_status": "complete",
                "target": {
                    "column": "vehicle_count",
                    "unit": "vehicles per canonical observation hour",
                    "meaning": "canonical Transport Activity countline or SCATS intersection volume",
                    "natural_unit": [
                        "label_source",
                        "measurement_scope",
                        "observation_unit_id",
                    ],
                    "prediction_horizon_hours": 1,
                    "measurement_identity": [
                        "label_source",
                        "measurement_scope",
                        "observation_unit_id",
                    ],
                    "canonical_row_identity": [
                        "source_dataset_id",
                        "observation_unit_id",
                        "hour_start_utc",
                    ],
                    "timestamp": "hour_start_utc",
                },
                "observation_key": {
                    "column": "observation_key",
                    "mode": observation_key_mode,
                    "natural_key": [
                        "source_dataset_id",
                        "observation_unit_id",
                        "hour_start_utc",
                    ],
                    "derived_format": (
                        "v1:<source_length>:<source_dataset_id>:<unit_length>:"
                        "<observation_unit_id>:<hour_start_utc_epoch_microseconds>"
                    ),
                    "observation_id_used": False,
                },
                "prediction_contract": {
                    "prediction_horizon_hours": 1,
                    "feature_asof_column": "feature_asof",
                    "feature_asof_definition": "hour_start_utc minus one hour",
                    "timezone_for_split": MELBOURNE_TIMEZONE,
                    "target_rows_retained": True,
                },
                "split_contract": {
                    "strategy": "chronological local_date split",
                    "train_end": TRAIN_END.isoformat(),
                    "validation_end": VALIDATION_END.isoformat(),
                    "test_end": TEST_END.isoformat(),
                    "split_by": "local_date",
                    "train": {
                        "start": RELEASE_START.isoformat(),
                        "end": TRAIN_END.isoformat(),
                    },
                    "validation": {
                        "start": dt.date(2025, 1, 1).isoformat(),
                        "end": VALIDATION_END.isoformat(),
                    },
                    "test": {
                        "start": TEST_START.isoformat(),
                        "end": TEST_END.isoformat(),
                    },
                },
                "inputs": {
                    "canonical_target": {
                        "path": target.name,
                        "bytes": target.stat().st_size,
                        "sha256": _sha256(target),
                    },
                    "canonical_manifest": {
                        "path": canonical_manifest.name,
                        "bytes": canonical_manifest.stat().st_size,
                        "sha256": _sha256(canonical_manifest),
                        "artifact_status": canonical_manifest_payload.get(
                            "artifact_status", "complete"
                        ),
                    },
                },
                "datasets": {
                    "base": {
                        **base_details,
                        "path": destinations["base"].name,
                        "preview": destinations["base_preview"].name,
                        "target_column": "vehicle_count",
                    },
                    "lag_enhanced": {
                        **enhanced_details,
                        "path": destinations["lag_enhanced"].name,
                        "preview": destinations["lag_enhanced_preview"].name,
                        "target_column": "vehicle_count",
                    },
                },
                "outputs": {
                    "base": {
                        "parquet": destinations["base"].name,
                        "preview": destinations["base_preview"].name,
                        "sha256": base_details["sha256"],
                        "preview_sha256": base_details["preview_sha256"],
                    },
                    "lag_enhanced": {
                        "parquet": destinations["lag_enhanced"].name,
                        "preview": destinations["lag_enhanced_preview"].name,
                        "sha256": enhanced_details["sha256"],
                        "preview_sha256": enhanced_details["preview_sha256"],
                    },
                },
                "training_contract": {
                    "target_column": "vehicle_count",
                    "categorical_features": {
                        "base": base_features["categorical_features"],
                        "lag_enhanced": enhanced_features["categorical_features"],
                    },
                    "numeric_features": {
                        "base": base_features["numeric_features"],
                        "lag_enhanced": enhanced_features["numeric_features"],
                    },
                    "feature_columns": {
                        "base": base_features["feature_columns"],
                        "lag_enhanced": enhanced_features["feature_columns"],
                    },
                    "train_available_features": {
                        "base": base_features["train_available_features"],
                        "lag_enhanced": enhanced_features["train_available_features"],
                    },
                    "identity_features": {
                        "base": base_features["identity_features"],
                        "lag_enhanced": enhanced_features["identity_features"],
                    },
                    "quality_features": {
                        "base": base_features["quality_features"],
                        "lag_enhanced": enhanced_features["quality_features"],
                    },
                    "feature_policy": (
                        "train_available_features records identity/source/scope and quality evidence; "
                        "categorical_features, numeric_features, and feature_columns are the current "
                        "trainer-safe predictor lists"
                    ),
                },
                "leakage_exclusions": {
                    "columns": list(LEAKAGE_EXCLUDED_COLUMNS),
                    "same_hour_observation_fields": list(SAME_HOUR_DIAGNOSTIC_COLUMNS),
                    "diagnostic_only_fields": list(SAME_HOUR_DIAGNOSTIC_COLUMNS),
                    "rules": [
                        "same-hour vehicle_count and direct target transforms are labels, never features",
                        "same-hour quality, source/record, detector/class, alarm, and DST evidence is retained only for diagnostics",
                        "SCATS intersection_total and 24-hour volume evidence are excluded from model inputs",
                        "static identity/source semantics, coordinates, and calendar/holiday fields are available when present",
                        "feature_asof is target hour_start_utc minus one hour; no target-hour observation field is a predictor",
                        "exact target lags use label_source + measurement_scope + observation_unit_id + hour_start_utc joins; source_dataset_id changes across publisher archives",
                        "rolling statistics use a preceding-hour upper bound and never include the target row",
                        "missing source hours remain null; no target or lag value is zero-imputed",
                        "base and lag_enhanced are built from the same canonical target key set",
                    ],
                },
                "quality": {
                    "canonical_rows": expected_rows,
                    "base": _quality_counts(connection, staged["base"], set(base_columns)),
                    "lag_enhanced": _quality_counts(
                        connection, staged["lag_enhanced"], set(enhanced_columns)
                    ),
                },
                "determinism": {
                    "row_order": "observation_key ascending",
                    "preview_order": "observation_key ascending, first preview_rows rows",
                    "parquet_compression": "zstd",
                    "manifest_timestamps_omitted": True,
                    "duckdb_threads": int(args.threads),
                    "duckdb_memory_limit": memory_limit,
                    "duckdb_memory_limit_bytes": memory_limit_bytes,
                    "duckdb_preserve_insertion_order": False,
                },
                "resource_budget": {
                    "duckdb_memory_limit": memory_limit,
                    "duckdb_memory_limit_bytes": memory_limit_bytes,
                    "duckdb_threads": int(args.threads),
                },
                "assertions": {
                    "canonical_manifest_partial_rejected": True,
                    "release_date_start": RELEASE_START.isoformat(),
                    "release_test_end": TEST_END.isoformat(),
                    "out_of_release_dates_rejected": True,
                    "canonical_target_complete": True,
                    "target_rows_preserved": True,
                    "candidate_keys_identical": True,
                    "prediction_horizon_hours": 1,
                    "feature_asof_strictly_before_target": True,
                    "missing_source_hours_zero_imputed": False,
                },
            }
            manifest_bytes = (
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n"
            ).encode("utf-8")
            _atomic_bytes(staged["manifest"], manifest_bytes)

            # Publish all staged artifacts only after the complete set has been
            # validated and the readiness manifest has been assembled.  Each
            # replace is atomic, and the sibling backups make the whole set
            # recoverable if a later replace fails.  Evaluation lives beside
            # these files, so the transaction is intentionally limited to the
            # five feature artifacts.
            _publish_staged_artifacts(staged, destinations, temporary)
        finally:
            connection.close()

    return {
        "datasets": destinations,
        "rows": expected_rows,
        "manifest": destinations["manifest"],
    }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        "--target-parquet",
        "--input-target",
        type=Path,
        help="complete canonical traffic Parquet; inferred from the processed manifest when omitted",
    )
    parser.add_argument(
        "--canonical-manifest",
        "--target-manifest",
        "--manifest",
        dest="canonical_manifest",
        type=Path,
        help="canonical traffic manifest; it must declare a complete artifact",
    )
    parser.add_argument("--output-dir", "--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--preview-rows", type=int, default=250)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--memory-limit",
        type=_memory_limit_arg,
        default="8GiB",
        help="DuckDB memory budget (256MiB to 1TiB; default: 8GiB)",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        result = build(args)
    except (BuildError, OSError, ValueError, duckdb.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {result['rows']:,} rows to {result['datasets']['base']}")
    print(f"Wrote {result['rows']:,} rows to {result['datasets']['lag_enhanced']}")
    print(f"Manifest: {result['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
