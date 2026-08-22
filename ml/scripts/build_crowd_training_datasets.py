#!/usr/bin/env python3
"""Build the two leakage-safe crowd forecasting training tables.

The all-history table combines the canonical hourly pedestrian-flow target
with calendar, exact past-flow, rolling-history, and regional NASA POWER
weather features.  The recent-enhanced table is a recent subset of the same
contract and adds one-hour-lagged City microclimate and Transport Activity
features.  Missing source readings remain null; no sensor-hours are invented.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    import holidays
except ImportError:  # pragma: no cover - dependency error is reported by main.
    holidays = None


SCRIPT_DIR = Path(__file__).resolve().parent
ML_DIR = SCRIPT_DIR.parent
DEFAULT_TARGET = ML_DIR / "crowd" / "processed" / "pedestrian_flow_hourly.parquet"
DEFAULT_OUTPUT_DIR = ML_DIR / "crowd" / "training"
DEFAULT_RECENT_START = dt.date(2023, 1, 1)
COMMON_TRAIN_END = dt.date(2024, 12, 31)
COMMON_VALIDATION_END = dt.date(2025, 12, 31)
COMMON_TEST_END = dt.date(2026, 5, 11)
TIMEZONE = "Australia/Melbourne"
TARGET_REQUIRED = {
    "observation_key",
    "source_dataset_id",
    "sensor_id",
    "local_date",
    "local_hour",
    "observed_at_local",
    "pedestrian_flow",
}
TRANSPORT_MODES = ("pedestrian", "cyclist", "vehicle", "other")


class BuildError(RuntimeError):
    """Actionable training-table build failure."""


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''").replace("\\", "/")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, path, compression="zstd")


def _local_hour(series: pd.Series, *, assume_utc: bool) -> pd.Series:
    if assume_utc:
        parsed = pd.to_datetime(series, errors="coerce", utc=True)
        return parsed.dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.floor("h")
    parsed = pd.to_datetime(series, errors="coerce")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert(TIMEZONE).dt.tz_localize(None)
    return parsed.dt.floor("h")


def _prepare_weather(source: Path | None, destination: Path) -> list[str]:
    columns = [
        "observed_at_local",
        "nasa_temperature_c",
        "nasa_dewpoint_c",
        "nasa_relative_humidity_pct",
        "nasa_wind_speed_10m_m_s",
        "nasa_wind_direction_sin",
        "nasa_wind_direction_cos",
        "nasa_surface_pressure_kpa",
        "nasa_precipitation_corrected_mm_h",
        "nasa_surface_solar_radiation_w_m2",
        "nasa_weather_observation_count",
    ]
    if source is None:
        empty = {name: pd.Series(dtype="float64") for name in columns[1:]}
        empty["observed_at_local"] = pd.Series(dtype="datetime64[ns]")
        _write_frame(destination, pd.DataFrame(empty)[columns])
        return columns[1:]
    if not source.exists():
        raise BuildError(f"weather input not found: {source}")
    frame = pd.read_csv(source, na_values=[-999, -999.0, "-999", "-999.0"])
    required = {
        "timestamp_utc", "T2M", "T2MDEW", "RH2M", "WS10M", "WD10M", "PS",
        "PRECTOTCORR", "ALLSKY_SFC_SW_DWN",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BuildError(f"weather input is missing columns: {', '.join(missing)}")
    frame["observed_at_local"] = _local_hour(frame["timestamp_utc"], assume_utc=True)
    for name in required - {"timestamp_utc"}:
        frame[name] = pd.to_numeric(frame[name], errors="coerce").mask(lambda value: value <= -900)
    radians = frame["WD10M"] * math.pi / 180.0
    frame["_wind_sin"] = radians.map(math.sin, na_action="ignore")
    frame["_wind_cos"] = radians.map(math.cos, na_action="ignore")
    grouped = frame.groupby("observed_at_local", dropna=True).agg(
        nasa_temperature_c=("T2M", "mean"),
        nasa_dewpoint_c=("T2MDEW", "mean"),
        nasa_relative_humidity_pct=("RH2M", "mean"),
        nasa_wind_speed_10m_m_s=("WS10M", "mean"),
        nasa_wind_direction_sin=("_wind_sin", "mean"),
        nasa_wind_direction_cos=("_wind_cos", "mean"),
        nasa_surface_pressure_kpa=("PS", "mean"),
        nasa_precipitation_corrected_mm_h=("PRECTOTCORR", "mean"),
        nasa_surface_solar_radiation_w_m2=("ALLSKY_SFC_SW_DWN", "mean"),
        nasa_weather_observation_count=("timestamp_utc", "size"),
    ).reset_index()
    _write_frame(destination, grouped[columns])
    return columns[1:]


def _prepare_microclimate(source: Path | None, destination: Path) -> list[str]:
    feature_columns = [
        "micro_temperature_c_lag_1h",
        "micro_relative_humidity_pct_lag_1h",
        "micro_pressure_hpa_lag_1h",
        "micro_wind_speed_m_s_lag_1h",
        "micro_gust_speed_m_s_lag_1h",
        "micro_pm25_ug_m3_lag_1h",
        "micro_pm10_ug_m3_lag_1h",
        "micro_noise_db_lag_1h",
        "micro_active_device_count_lag_1h",
        "micro_reading_count_lag_1h",
    ]
    if source is None:
        _write_frame(destination, pd.DataFrame({"observed_at_local": pd.Series(dtype="datetime64[ns]"), **{name: pd.Series(dtype="float64") for name in feature_columns}}))
        return feature_columns
    if not source.exists():
        raise BuildError(f"microclimate input not found: {source}")
    frame = pd.read_parquet(source)
    timestamp_name = "timestamp_local" if "timestamp_local" in frame.columns else "received_at"
    if timestamp_name not in frame.columns:
        raise BuildError("microclimate input needs received_at or timestamp_local")
    frame["observed_at_local"] = _local_hour(
        frame[timestamp_name], assume_utc=timestamp_name == "received_at"
    )
    numeric = {
        "airtemperature": "micro_temperature_c_lag_1h",
        "relativehumidity": "micro_relative_humidity_pct_lag_1h",
        "atmosphericpressure": "micro_pressure_hpa_lag_1h",
        "averagewindspeed": "micro_wind_speed_m_s_lag_1h",
        "gustwindspeed": "micro_gust_speed_m_s_lag_1h",
        "pm25": "micro_pm25_ug_m3_lag_1h",
        "pm10": "micro_pm10_ug_m3_lag_1h",
        "noise": "micro_noise_db_lag_1h",
    }
    for source_name in numeric:
        if source_name not in frame.columns:
            frame[source_name] = math.nan
        frame[source_name] = pd.to_numeric(frame[source_name], errors="coerce")
    if "device_id" not in frame.columns:
        frame["device_id"] = None
    aggregations: dict[str, tuple[str, str]] = {
        output_name: (source_name, "median") for source_name, output_name in numeric.items()
    }
    aggregations["micro_active_device_count_lag_1h"] = ("device_id", "nunique")
    aggregations["micro_reading_count_lag_1h"] = ("observed_at_local", "size")
    grouped = frame.groupby("observed_at_local", dropna=True).agg(**aggregations).reset_index()
    # A one-hour shift makes every microclimate feature known before its label.
    grouped["observed_at_local"] += pd.Timedelta(hours=1)
    _write_frame(destination, grouped[["observed_at_local", *feature_columns]])
    return feature_columns


def _transport_sources(source: Path) -> tuple[list[Path], list[Path]]:
    if source.is_file():
        return ([source] if source.suffix.lower() == ".parquet" else [], [source] if source.suffix.lower() == ".zip" else [])
    parquet = sorted(
        path for path in source.rglob("*.parquet")
        if "transport_activity" in str(path).lower()
    )
    archives = sorted(
        path for path in source.rglob("*.zip")
        if "transport_activity" in str(path).lower()
    )
    return parquet, archives


def _classify_transport(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.lower().fillna("")
    result = pd.Series("other", index=text.index, dtype="string")
    result[text.str.contains(r"pedestrian|person|walker", regex=True)] = "pedestrian"
    result[text.str.contains(r"cycl|bicycle|bike|escooter", regex=True)] = "cyclist"
    result[text.str.contains(r"car|taxi|van|truck|bus|motor|vehicle|ute|rigid|fire_engine", regex=True)] = "vehicle"
    return result


def _transport_chunk(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timestamp_name = "timestamp_local" if "timestamp_local" in frame.columns else "from"
    if timestamp_name not in frame.columns or "class" not in frame.columns or "count" not in frame.columns:
        raise BuildError("Transport Activity input needs from/timestamp_local, class, and count")
    frame = frame.copy()
    timestamp_values = frame[timestamp_name]
    if timestamp_name == "from":
        # The publisher appends ``Z`` but the spring/fall interval jumps follow
        # Melbourne wall time. Treating it as UTC would shift every feature by
        # 10-11 hours.
        timestamp_values = timestamp_values.astype("string").str.replace(
            r"Z$", "", regex=True
        )
    frame["observed_at_local"] = _local_hour(timestamp_values, assume_utc=False)
    frame["transport_mode"] = _classify_transport(frame["class"])
    frame["count"] = pd.to_numeric(frame["count"], errors="coerce")
    frame.loc[frame["count"] < 0, "count"] = math.nan
    valid = frame.dropna(subset=["observed_at_local", "count"])
    totals = valid.groupby(["observed_at_local", "transport_mode"], observed=True)["count"].sum(min_count=1).rename("count").reset_index()
    location_name = "countLocationId" if "countLocationId" in valid.columns else None
    if location_name:
        locations = valid[["observed_at_local", location_name]].dropna().drop_duplicates()
        locations = locations.rename(columns={location_name: "location_id"})
    else:
        locations = valid[["observed_at_local"]].drop_duplicates().assign(location_id="unknown")
    observations = valid.groupby("observed_at_local").size().rename("observation_count").reset_index()
    return totals, locations, observations


def _prepare_transport(source: Path | None, destination: Path) -> list[str]:
    count_columns = [f"transport_{mode}_count_lag_1h" for mode in TRANSPORT_MODES]
    feature_columns = [
        *count_columns,
        "transport_active_location_count_lag_1h",
        "transport_observation_count_lag_1h",
    ]
    if source is None:
        _write_frame(destination, pd.DataFrame({"observed_at_local": pd.Series(dtype="datetime64[ns]"), **{name: pd.Series(dtype="float64") for name in feature_columns}}))
        return feature_columns
    if not source.exists():
        raise BuildError(f"transport input not found: {source}")
    parquet_sources, archives = _transport_sources(source)
    if not parquet_sources and not archives:
        raise BuildError(f"no Transport Activity Parquet or ZIP inputs under {source}")
    totals_parts: list[pd.DataFrame] = []
    location_parts: list[pd.DataFrame] = []
    observation_parts: list[pd.DataFrame] = []
    for path in parquet_sources:
        totals, locations, observations = _transport_chunk(pd.read_parquet(path))
        totals_parts.append(totals)
        location_parts.append(locations)
        observation_parts.append(observations)
    for archive in archives:
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.namelist():
                if not member.lower().endswith(".csv"):
                    continue
                with zipped.open(member) as handle:
                    for chunk in pd.read_csv(
                        handle,
                        usecols=lambda name: name in {"countLocationId", "from", "class", "count"},
                        chunksize=500_000,
                        low_memory=False,
                    ):
                        totals, locations, observations = _transport_chunk(chunk)
                        totals_parts.append(totals)
                        location_parts.append(locations)
                        observation_parts.append(observations)
    if not totals_parts:
        raise BuildError(f"Transport Activity inputs contained no readable rows: {source}")
    totals = pd.concat(totals_parts, ignore_index=True).groupby(
        ["observed_at_local", "transport_mode"], observed=True
    )["count"].sum(min_count=1).reset_index()
    wide = totals.pivot(index="observed_at_local", columns="transport_mode", values="count")
    for mode in TRANSPORT_MODES:
        if mode not in wide.columns:
            wide[mode] = math.nan
    wide = wide[list(TRANSPORT_MODES)].rename(
        columns={mode: f"transport_{mode}_count_lag_1h" for mode in TRANSPORT_MODES}
    ).reset_index()
    locations = pd.concat(location_parts, ignore_index=True).drop_duplicates()
    active = locations.groupby("observed_at_local")["location_id"].nunique().rename(
        "transport_active_location_count_lag_1h"
    ).reset_index()
    grouped = wide.merge(active, on="observed_at_local", how="left")
    observation_counts = pd.concat(observation_parts, ignore_index=True).groupby(
        "observed_at_local"
    )["observation_count"].sum().rename("transport_observation_count_lag_1h").reset_index()
    grouped = grouped.merge(observation_counts, on="observed_at_local", how="left")
    grouped["observed_at_local"] += pd.Timedelta(hours=1)
    _write_frame(destination, grouped[["observed_at_local", *feature_columns]])
    return feature_columns


def _prepare_holidays(min_date: dt.date, max_date: dt.date, destination: Path) -> None:
    if holidays is None:
        raise BuildError("the holidays package is required; install ml/requirements.txt")
    victoria = holidays.Australia(subdiv="VIC", years=range(min_date.year, max_date.year + 1))
    dates = pd.date_range(min_date, max_date, freq="D")
    frame = pd.DataFrame({"local_date": dates.date})
    frame["public_holiday_name"] = frame["local_date"].map(victoria.get)
    frame["is_public_holiday"] = frame["public_holiday_name"].notna()
    _write_frame(destination, frame)


def _split_expression(min_ts: dt.datetime, max_ts: dt.datetime) -> tuple[str, dict[str, str]]:
    span = max_ts - min_ts
    if span >= dt.timedelta(days=730) and max_ts.date() >= dt.date(2026, 1, 1):
        expression = f"""CASE
            WHEN t.local_date <= DATE '{COMMON_TRAIN_END}' THEN 'train'
            WHEN t.local_date <= DATE '{COMMON_VALIDATION_END}' THEN 'validation'
            WHEN t.local_date <= DATE '{COMMON_TEST_END}' THEN 'test'
            ELSE 'post_test'
        END"""
        return expression, {
            "train_end": str(COMMON_TRAIN_END),
            "validation_end": str(COMMON_VALIDATION_END),
            "test_end": str(COMMON_TEST_END),
            "post_test_policy": "retained but excluded from the common comparison",
        }
    validation_start = min_ts + span * 0.70
    test_start = min_ts + span * 0.85
    expression = f"""CASE
        WHEN t.observed_at_local < TIMESTAMP '{validation_start.isoformat(sep=' ')}' THEN 'train'
        WHEN t.observed_at_local < TIMESTAMP '{test_start.isoformat(sep=' ')}' THEN 'validation'
        ELSE 'test'
    END"""
    return expression, {
        "strategy": "70/15/15 chronological fallback for short fixtures",
        "validation_start": validation_start.isoformat(),
        "test_start": test_start.isoformat(),
    }


def _copy_query(connection: duckdb.DuckDBPyConnection, query: str, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    connection.execute(
        f"COPY ({query}) TO '{_sql_path(temporary)}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)"
    )
    os.replace(temporary, destination)


def _write_preview(connection: duckdb.DuckDBPyConnection, source: Path, destination: Path, rows: int) -> None:
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    source_sql = _sql_path(source)
    query = f"SELECT * FROM read_parquet('{source_sql}') USING SAMPLE reservoir({rows} ROWS) REPEATABLE (42) ORDER BY observed_at_local, sensor_id"
    connection.execute(f"COPY ({query}) TO '{_sql_path(temporary)}' (HEADER, DELIMITER ',')")
    os.replace(temporary, destination)


def _counts(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, Any]:
    source = _sql_path(path)
    rows, min_time, max_time = connection.execute(
        f"SELECT count(*), min(observed_at_local), max(observed_at_local) FROM read_parquet('{source}')"
    ).fetchone()
    split_rows = dict(connection.execute(
        f"SELECT split, count(*) FROM read_parquet('{source}') GROUP BY split ORDER BY split"
    ).fetchall())
    return {
        "rows": int(rows),
        "min_observed_at_local": min_time.isoformat() if min_time else None,
        "max_observed_at_local": max_time.isoformat() if max_time else None,
        "split_rows": {str(key): int(value) for key, value in split_rows.items()},
        "bytes": path.stat().st_size,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    target = args.target.resolve()
    if not target.exists():
        raise BuildError(f"canonical target not found: {target}")
    schema = pq.read_schema(target)
    missing = sorted(TARGET_REQUIRED - set(schema.names))
    if missing:
        raise BuildError(f"canonical target is missing columns: {', '.join(missing)}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    all_output = output_dir / "crowd_training_all_history.parquet"
    recent_output = output_dir / "crowd_training_recent_enhanced.parquet"
    all_preview = output_dir / "crowd_training_all_history_preview.csv"
    recent_preview = output_dir / "crowd_training_recent_enhanced_preview.csv"
    manifest_path = output_dir / "training_manifest.json"
    destinations = [all_output, recent_output, all_preview, recent_preview, manifest_path]
    existing = [path for path in destinations if path.exists()]
    if existing and not args.overwrite:
        raise BuildError("outputs already exist; pass --overwrite: " + ", ".join(str(path) for path in existing))

    with tempfile.TemporaryDirectory(prefix="heatroute-training-") as temporary_name:
        temporary = Path(temporary_name)
        weather_path = temporary / "weather.parquet"
        micro_path = temporary / "micro.parquet"
        transport_path = temporary / "transport.parquet"
        holiday_path = temporary / "holidays.parquet"
        weather_features = _prepare_weather(args.weather, weather_path)
        micro_features = _prepare_microclimate(args.microclimate, micro_path)
        transport_features = _prepare_transport(args.transport, transport_path)

        connection = duckdb.connect(str(temporary / "features.duckdb"))
        connection.execute("SET threads TO 4")
        connection.execute(f"SET temp_directory='{_sql_path(temporary / 'duckdb-temp')}'")
        target_sql = _sql_path(target)
        min_ts, max_ts, rows, unique_keys = connection.execute(
            f"SELECT min(observed_at_local), max(observed_at_local), count(*), count(DISTINCT observation_key) FROM read_parquet('{target_sql}')"
        ).fetchone()
        if rows != unique_keys:
            raise BuildError(f"canonical target observation_key is not unique ({rows} rows, {unique_keys} keys)")
        _prepare_holidays(min_ts.date(), max_ts.date(), holiday_path)
        split_sql, split_contract = _split_expression(min_ts, max_ts)

        weather_select = ",\n            ".join(f"w.{name}" for name in weather_features)
        base_query = f"""
        WITH target AS (
            SELECT observation_key, source_dataset_id AS target_source_dataset_id,
                   sensor_id, local_date, local_hour, observed_at_local,
                   pedestrian_flow, is_dst, dst_ambiguous_local_time,
                   dst_nonexistent_local_time, hour_was_reconstructed
            FROM read_parquet('{target_sql}')
        ), rolling AS (
            SELECT *,
                avg(pedestrian_flow) OVER (
                    PARTITION BY sensor_id ORDER BY observed_at_local
                    RANGE BETWEEN INTERVAL 24 HOURS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING
                ) AS flow_rolling_past_24h_mean,
                stddev_samp(pedestrian_flow) OVER (
                    PARTITION BY sensor_id ORDER BY observed_at_local
                    RANGE BETWEEN INTERVAL 168 HOURS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING
                ) AS flow_rolling_past_168h_std,
                count(pedestrian_flow) OVER (
                    PARTITION BY sensor_id ORDER BY observed_at_local
                    RANGE BETWEEN INTERVAL 168 HOURS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING
                ) AS flow_rolling_past_168h_count
            FROM target
        )
        SELECT
            t.observation_key, t.target_source_dataset_id, t.sensor_id,
            t.local_date, t.local_hour, t.observed_at_local,
            t.observed_at_local - INTERVAL 1 HOUR AS feature_asof_local,
            1::UTINYINT AS prediction_horizon_hours,
            t.pedestrian_flow,
            {split_sql} AS split,
            sin(2 * pi() * t.local_hour / 24.0) AS hour_sin,
            cos(2 * pi() * t.local_hour / 24.0) AS hour_cos,
            sin(2 * pi() * dayofweek(t.local_date) / 7.0) AS day_of_week_sin,
            cos(2 * pi() * dayofweek(t.local_date) / 7.0) AS day_of_week_cos,
            sin(2 * pi() * dayofyear(t.local_date) / 365.25) AS day_of_year_sin,
            cos(2 * pi() * dayofyear(t.local_date) / 365.25) AS day_of_year_cos,
            (dayofweek(t.local_date) IN (0, 6)) AS is_weekend,
            h.is_public_holiday, h.public_holiday_name,
            t.is_dst, t.dst_ambiguous_local_time, t.dst_nonexistent_local_time,
            t.hour_was_reconstructed,
            lag1.pedestrian_flow AS flow_lag_1h,
            lag24.pedestrian_flow AS flow_lag_24h,
            lag168.pedestrian_flow AS flow_lag_168h,
            t.flow_rolling_past_24h_mean,
            t.flow_rolling_past_168h_std,
            t.flow_rolling_past_168h_count,
            {weather_select}
        FROM rolling t
        LEFT JOIN target lag1 ON lag1.sensor_id=t.sensor_id AND lag1.observed_at_local=t.observed_at_local-INTERVAL 1 HOUR
        LEFT JOIN target lag24 ON lag24.sensor_id=t.sensor_id AND lag24.observed_at_local=t.observed_at_local-INTERVAL 24 HOURS
        LEFT JOIN target lag168 ON lag168.sensor_id=t.sensor_id AND lag168.observed_at_local=t.observed_at_local-INTERVAL 168 HOURS
        LEFT JOIN read_parquet('{_sql_path(holiday_path)}') h ON h.local_date=t.local_date
        LEFT JOIN read_parquet('{_sql_path(weather_path)}') w ON w.observed_at_local=t.observed_at_local
        """
        _copy_query(connection, base_query, all_output)

        enhanced_select = ",\n            ".join(
            [*(f"m.{name}" for name in micro_features), *(f"v.{name}" for name in transport_features)]
        )
        recent_query = f"""
        SELECT b.*, {enhanced_select}
        FROM read_parquet('{_sql_path(all_output)}') b
        LEFT JOIN read_parquet('{_sql_path(micro_path)}') m USING (observed_at_local)
        LEFT JOIN read_parquet('{_sql_path(transport_path)}') v USING (observed_at_local)
        WHERE b.local_date >= DATE '{args.recent_start.isoformat()}'
        """
        _copy_query(connection, recent_query, recent_output)
        _write_preview(connection, all_output, all_preview, args.preview_rows)
        _write_preview(connection, recent_output, recent_preview, args.preview_rows)

        all_columns = pq.read_schema(all_output).names
        recent_columns = pq.read_schema(recent_output).names
        audit_columns = {
            "observation_key", "target_source_dataset_id", "local_date", "observed_at_local",
            "feature_asof_local", "pedestrian_flow", "split", "public_holiday_name",
            "dst_ambiguous_local_time", "dst_nonexistent_local_time", "hour_was_reconstructed",
        }
        feature_columns_all = [name for name in all_columns if name not in audit_columns]
        feature_columns_recent = [name for name in recent_columns if name not in audit_columns]
        manifest = {
            "schema_version": 1,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "target": {
                "column": "pedestrian_flow",
                "unit": "people per sensor-hour",
                "meaning": "pedestrian flow past a fixed counter; not area crowd density",
            },
            "prediction_contract": {
                "horizon_hours": 1,
                "timezone": TIMEZONE,
                "external_features": "weather at the target hour requires an observation or forecast equivalent",
                "target_history": "exact timestamps and past-only windows; missing hours remain null",
                "microclimate": "citywide hourly aggregates lagged one hour",
                "transport": "citywide class aggregates lagged one hour; no same-hour values",
            },
            "split_contract": split_contract,
            "fair_comparison": {
                "test_period": f"2026-01-01 through {COMMON_TEST_END}",
                "matched_recent_ablation": f"filter all_history to local_date >= {args.recent_start}",
                "note": "Score both models on identical observation_key values in the common test period.",
            },
            "inputs": {
                "target": {"path": str(target), "bytes": target.stat().st_size, "sha256": _sha256(target)},
                "weather": str(args.weather.resolve()) if args.weather else None,
                "microclimate": str(args.microclimate.resolve()) if args.microclimate else None,
                "transport": str(args.transport.resolve()) if args.transport else None,
            },
            "datasets": {
                "all_history": {**_counts(connection, all_output), "path": str(all_output), "columns": all_columns, "feature_columns": feature_columns_all, "preview": str(all_preview)},
                "recent_enhanced": {**_counts(connection, recent_output), "path": str(recent_output), "columns": recent_columns, "feature_columns": feature_columns_recent, "preview": str(recent_preview), "recent_start": str(args.recent_start)},
            },
            "excluded_leakage": [
                "direction_1_count", "direction_2_count", "same-hour target-derived counts",
                "same-hour Transport Activity counts",
            ],
            "missingness_policy": "Optional joins remain null; absent source intervals are never converted to zero.",
        }
        _atomic_json(manifest_path, manifest)
        connection.close()
    return manifest


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="canonical pedestrian_flow_hourly Parquet")
    parser.add_argument("--weather", type=Path, help="normalized NASA POWER hourly CSV")
    parser.add_argument("--microclimate", type=Path, help="City microclimate readings Parquet")
    parser.add_argument("--transport", type=Path, help="Transport Activity Parquet, ZIP, or dataset directory")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recent-start", type=dt.date.fromisoformat, default=DEFAULT_RECENT_START)
    parser.add_argument("--preview-rows", type=int, default=250)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.preview_rows < 1 or args.preview_rows > 10_000:
        parser.error("--preview-rows must be between 1 and 10000")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        manifest = build(args)
    except (BuildError, OSError, ValueError, duckdb.Error, pa.ArrowException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name, details in manifest["datasets"].items():
        print(f"[ok] {name}: {details['rows']:,} rows -> {details['path']}")
        print(f"     preview -> {details['preview']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
