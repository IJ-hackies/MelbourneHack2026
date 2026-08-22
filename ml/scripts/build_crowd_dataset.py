#!/usr/bin/env python3
"""Build HeatRoute's canonical hourly pedestrian-flow target table.

The three publisher snapshots overlap but are not identical revisions.  This
builder therefore applies fixed, non-overlapping source windows, preserves
publisher provenance, and never creates rows for unobserved hours.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "ml" / "crowd" / "datasets"
DEFAULT_HISTORICAL = DATASETS / "com_pedestrian_counts_2009_2022" / "hourly_counts_2009_2022.zip"
DEFAULT_WAYBACK = DATASETS / "com_pedestrian_counts_wayback_20250112" / "hourly_counts_wayback_20250112.csv"
DEFAULT_CURRENT = DATASETS / "com_pedestrian_counts_current" / "hourly_counts_current.parquet"
DEFAULT_SENSORS = DATASETS / "com_pedestrian_sensor_locations" / "sensor_locations.parquet"
DEFAULT_OUTPUT = ROOT / "ml" / "crowd" / "processed"

HISTORICAL_ID = "com_pedestrian_counts_2009_2022"
WAYBACK_ID = "com_pedestrian_counts_wayback_20250112"
CURRENT_ID = "com_pedestrian_counts_current"
TIMEZONE = "Australia/Melbourne"
HISTORICAL_END = pd.Timestamp("2022-10-31")
GAP_START = pd.Timestamp("2022-11-01")
GAP_END = pd.Timestamp("2024-08-20")
CURRENT_START = pd.Timestamp("2024-08-21")

MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

HISTORICAL_COLUMNS = {
    "ID", "Date_Time", "Year", "Month", "Mdate", "Day", "Time",
    "Sensor_ID", "Sensor_Name", "Hourly_Counts",
}
MODERN_COLUMNS = {
    "id", "location_id", "sensing_date", "hourday", "direction_1",
    "direction_2", "pedestriancount", "sensor_name", "location",
}

OUTPUT_SCHEMA = pa.schema(
    [
        ("observation_key", pa.string()),
        ("source_dataset_id", pa.string()),
        ("source_record_id", pa.string()),
        ("source_row_number", pa.int64()),
        ("sensor_id", pa.int32()),
        ("sensor_name", pa.string()),
        ("local_date", pa.date32()),
        ("local_hour", pa.int8()),
        ("observed_at_local", pa.timestamp("s")),
        ("timezone_name", pa.string()),
        ("utc_offset_known", pa.bool_()),
        ("is_dst", pa.bool_()),
        ("dst_ambiguous_local_time", pa.bool_()),
        ("dst_nonexistent_local_time", pa.bool_()),
        ("year", pa.int16()),
        ("month", pa.int8()),
        ("day_of_month", pa.int8()),
        ("day_of_week", pa.int8()),
        ("is_weekend", pa.bool_()),
        ("pedestrian_flow", pa.int64()),
        ("direction_1_count", pa.int64()),
        ("direction_2_count", pa.int64()),
        ("direction_counts_valid", pa.bool_()),
        ("direction_semantics", pa.string()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("coordinate_valid", pa.bool_()),
        ("sensor_in_current_metadata", pa.bool_()),
        ("sensor_name_missing", pa.bool_()),
        ("coordinate_missing", pa.bool_()),
        ("hour_was_reconstructed", pa.bool_()),
    ]
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_columns(actual: Iterable[str], expected: set[str], label: str) -> None:
    missing = expected - set(actual)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def historical_member(archive: zipfile.ZipFile) -> str:
    members = [
        name for name in archive.namelist()
        if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
    ]
    if len(members) != 1:
        raise ValueError(f"historical ZIP must contain exactly one data CSV; found {members}")
    return members[0]


def historical_reader(path: Path, chunk_size: int, usecols: list[str] | None = None):
    archive = zipfile.ZipFile(path)
    member = historical_member(archive)
    handle = archive.open(member)
    reader = pd.read_csv(handle, chunksize=chunk_size, usecols=usecols, dtype={"ID": "string"})
    try:
        yield from reader
    finally:
        handle.close()
        archive.close()


def inspect_september_2010_repair(path: Path, chunk_size: int) -> tuple[dict[str, int], dict[str, int]]:
    """Return raw-ID -> recovered hour for the publisher's known malformed block."""

    pieces: list[pd.DataFrame] = []
    columns = ["ID", "Year", "Month", "Mdate", "Time", "Sensor_ID"]
    for chunk in historical_reader(path, chunk_size, columns):
        month = chunk["Month"].astype("string").str.strip()
        mask = (pd.to_numeric(chunk["Year"], errors="coerce") == 2010) & (month == "September")
        if mask.any():
            pieces.append(chunk.loc[mask, columns].copy())
    if not pieces:
        return {}, {"rows": 0, "groups": 0}

    block = pd.concat(pieces, ignore_index=True)
    block["Time"] = pd.to_numeric(block["Time"], errors="raise")
    block["Mdate"] = pd.to_numeric(block["Mdate"], errors="raise")
    block["Sensor_ID"] = pd.to_numeric(block["Sensor_ID"], errors="raise")
    group_sizes = block.groupby(["Mdate", "Sensor_ID"], sort=False).size()
    has_duplicate_keys = bool((group_sizes > 1).any())
    if not has_duplicate_keys:
        return {}, {"rows": 0, "groups": 0}

    expected_rows, expected_groups = 12_240, 510
    if len(block) != expected_rows or len(group_sizes) != expected_groups:
        raise ValueError(
            "September 2010 has duplicate sensor/date/hour keys but does not match "
            f"the verified repair shape ({expected_rows} rows, {expected_groups} groups)"
        )
    if not (block["Time"] == 0).all() or not (group_sizes == 24).all():
        raise ValueError("September 2010 repair requires 24 hour-0 rows per sensor/day")
    if block["ID"].duplicated().any():
        raise ValueError("September 2010 repair requires unique publisher row IDs")

    recovered: dict[str, int] = {}
    for day, day_rows in block.groupby("Mdate", sort=False):
        sensors = day_rows["Sensor_ID"].tolist()
        first = sensors[:17]
        if len(day_rows) != 24 * 17 or len(set(first)) != 17:
            raise ValueError(f"September 2010 day {day} does not have 24 x 17 rows")
        if any(sensors[offset:offset + 17] != first for offset in range(0, len(sensors), 17)):
            raise ValueError(f"September 2010 day {day} sensor block order is not stable")
        for position, raw_id in enumerate(day_rows["ID"].astype("string")):
            recovered[str(raw_id)] = position // 17
    return recovered, {"rows": len(block), "groups": len(group_sizes)}


def parse_dates_from_historical(chunk: pd.DataFrame) -> pd.Series:
    month = chunk["Month"].astype("string").str.strip().map(MONTHS)
    if month.isna().any():
        bad = chunk.loc[month.isna(), "Month"].drop_duplicates().tolist()[:5]
        raise ValueError(f"historical data contains unknown month names: {bad}")
    return pd.to_datetime(
        {
            "year": pd.to_numeric(chunk["Year"], errors="raise"),
            "month": month.astype("int16"),
            "day": pd.to_numeric(chunk["Mdate"], errors="raise"),
        },
        errors="raise",
    )


def parse_wkb_point(value: Any) -> tuple[float | None, float | None]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, None
    raw = bytes(value)
    if len(raw) < 21:
        return None, None
    byte_order = "<" if raw[0] == 1 else ">"
    geometry_type = struct.unpack(f"{byte_order}I", raw[1:5])[0]
    if geometry_type != 1:
        return None, None
    longitude, latitude = struct.unpack(f"{byte_order}dd", raw[5:21])
    return latitude, longitude


def parse_text_points(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    extracted = series.astype("string").str.extract(
        r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
    )
    return pd.to_numeric(extracted[0], errors="coerce"), pd.to_numeric(extracted[1], errors="coerce")


def wall_clock_status(value: datetime, cache: dict[datetime, tuple[bool | None, bool, bool]]) -> tuple[bool | None, bool, bool]:
    cached = cache.get(value)
    if cached is not None:
        return cached
    zone = ZoneInfo(TIMEZONE)
    valid: list[datetime] = []
    for fold in (0, 1):
        aware = value.replace(tzinfo=zone, fold=fold)
        round_trip = aware.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == value:
            valid.append(aware)
    offsets = {item.utcoffset() for item in valid}
    nonexistent = not valid
    ambiguous = len(offsets) > 1
    is_dst = None if nonexistent or ambiguous else bool(valid[0].dst())
    result = (is_dst, ambiguous, nonexistent)
    cache[value] = result
    return result


def finish_common(
    frame: pd.DataFrame,
    metadata_ids: set[int],
    dst_cache: dict[datetime, tuple[bool | None, bool, bool]],
) -> pd.DataFrame:
    frame["sensor_id"] = pd.to_numeric(frame["sensor_id"], errors="raise").astype("int32")
    frame["local_hour"] = pd.to_numeric(frame["local_hour"], errors="raise").astype("int8")
    if not frame["local_hour"].between(0, 23).all():
        raise ValueError("local hour must be between 0 and 23")
    frame["pedestrian_flow"] = pd.to_numeric(frame["pedestrian_flow"], errors="raise").astype("int64")
    if (frame["pedestrian_flow"] < 0).any():
        raise ValueError("pedestrian_flow contains negative totals")

    frame["local_date"] = pd.to_datetime(frame["local_date"], errors="raise").dt.normalize()
    frame["observed_at_local"] = frame["local_date"] + pd.to_timedelta(frame["local_hour"], unit="h")
    frame["observation_key"] = (
        frame["sensor_id"].astype("string") + ":" +
        frame["local_date"].dt.strftime("%Y%m%d") + ":" +
        frame["local_hour"].astype("int16").astype("string").str.zfill(2)
    )
    unique_times = frame["observed_at_local"].drop_duplicates().tolist()
    statuses = {value: wall_clock_status(value.to_pydatetime(), dst_cache) for value in unique_times}
    values = frame["observed_at_local"].map(statuses)
    frame["is_dst"] = pd.array([item[0] for item in values], dtype="boolean")
    frame["dst_ambiguous_local_time"] = [item[1] for item in values]
    frame["dst_nonexistent_local_time"] = [item[2] for item in values]
    frame["utc_offset_known"] = ~(frame["dst_ambiguous_local_time"] | frame["dst_nonexistent_local_time"])
    frame["timezone_name"] = TIMEZONE
    frame["year"] = frame["local_date"].dt.year.astype("int16")
    frame["month"] = frame["local_date"].dt.month.astype("int8")
    frame["day_of_month"] = frame["local_date"].dt.day.astype("int8")
    frame["day_of_week"] = frame["local_date"].dt.dayofweek.astype("int8")
    frame["is_weekend"] = frame["day_of_week"] >= 5
    frame["sensor_in_current_metadata"] = frame["sensor_id"].isin(metadata_ids)
    frame["sensor_name"] = frame["sensor_name"].astype("string").str.strip().replace("", pd.NA)
    frame["sensor_name_missing"] = frame["sensor_name"].isna()
    frame["coordinate_missing"] = frame["latitude"].isna() | frame["longitude"].isna()
    coordinate_valid = (
        frame["latitude"].between(-90, 90, inclusive="both") &
        frame["longitude"].between(-180, 180, inclusive="both")
    )
    frame["coordinate_valid"] = pd.array(coordinate_valid.where(~frame["coordinate_missing"]), dtype="boolean")
    return frame[[field.name for field in OUTPUT_SCHEMA]]


def normalize_historical(
    chunk: pd.DataFrame,
    row_offset: int,
    repaired_hours: dict[str, int],
    metadata_ids: set[int],
    dst_cache: dict[datetime, tuple[bool | None, bool, bool]],
) -> pd.DataFrame:
    require_columns(chunk.columns, HISTORICAL_COLUMNS, "historical CSV")
    local_date = parse_dates_from_historical(chunk)
    hours = pd.to_numeric(chunk["Time"], errors="raise").astype("int16")
    raw_ids = chunk["ID"].astype("string")
    reconstructed = raw_ids.isin(repaired_hours)
    if reconstructed.any():
        hours.loc[reconstructed] = raw_ids.loc[reconstructed].map(repaired_hours).astype("int16")
    frame = pd.DataFrame(
        {
            "source_dataset_id": HISTORICAL_ID,
            "source_record_id": raw_ids,
            "source_row_number": range(row_offset + 1, row_offset + 1 + len(chunk)),
            "sensor_id": chunk["Sensor_ID"],
            "sensor_name": chunk["Sensor_Name"],
            "local_date": local_date,
            "local_hour": hours,
            "pedestrian_flow": chunk["Hourly_Counts"],
            "direction_1_count": pd.array([pd.NA] * len(chunk), dtype="Int64"),
            "direction_2_count": pd.array([pd.NA] * len(chunk), dtype="Int64"),
            "direction_counts_valid": pd.array([pd.NA] * len(chunk), dtype="boolean"),
            "direction_semantics": "total_only",
            "latitude": float("nan"),
            "longitude": float("nan"),
            "hour_was_reconstructed": reconstructed,
        }
    )
    frame = frame.loc[frame["local_date"] <= HISTORICAL_END].copy()
    return finish_common(frame, metadata_ids, dst_cache)


def normalize_modern(
    chunk: pd.DataFrame,
    row_offset: int,
    source_id: str,
    metadata_ids: set[int],
    dst_cache: dict[datetime, tuple[bool | None, bool, bool]],
) -> pd.DataFrame:
    require_columns(chunk.columns, MODERN_COLUMNS, source_id)
    local_date = pd.to_datetime(chunk["sensing_date"], errors="raise").dt.normalize()
    if source_id == WAYBACK_ID:
        selected = local_date.between(GAP_START, GAP_END, inclusive="both")
        latitude, longitude = parse_text_points(chunk["location"])
    else:
        selected = local_date >= CURRENT_START
        points = [parse_wkb_point(value) for value in chunk["location"]]
        latitude = pd.Series((point[0] for point in points), index=chunk.index, dtype="float64")
        longitude = pd.Series((point[1] for point in points), index=chunk.index, dtype="float64")

    direction_1 = pd.to_numeric(chunk["direction_1"], errors="coerce").astype("Int64")
    direction_2 = pd.to_numeric(chunk["direction_2"], errors="coerce").astype("Int64")
    total = pd.to_numeric(chunk["pedestriancount"], errors="raise").astype("int64")
    directions_valid = (
        direction_1.notna() & direction_2.notna() &
        (direction_1 >= 0) & (direction_2 >= 0) &
        ((direction_1 + direction_2) == total)
    )
    frame = pd.DataFrame(
        {
            "source_dataset_id": source_id,
            "source_record_id": chunk["id"].astype("string"),
            "source_row_number": range(row_offset + 1, row_offset + 1 + len(chunk)),
            "sensor_id": chunk["location_id"],
            "sensor_name": chunk["sensor_name"],
            "local_date": local_date,
            "local_hour": chunk["hourday"],
            "pedestrian_flow": total,
            "direction_1_count": direction_1,
            "direction_2_count": direction_2,
            "direction_counts_valid": pd.array(directions_valid, dtype="boolean"),
            "direction_semantics": "bidirectional_components",
            "latitude": latitude,
            "longitude": longitude,
            "hour_was_reconstructed": False,
        }
    )
    frame = frame.loc[selected].copy()
    return finish_common(frame, metadata_ids, dst_cache)


def update_metrics(
    frame: pd.DataFrame,
    quality: Counter[str],
    source_metrics: dict[str, dict[str, Any]],
    sensor_metrics: dict[int, dict[str, Any]],
) -> None:
    if frame.empty:
        return
    source = str(frame["source_dataset_id"].iloc[0])
    metric = source_metrics[source]
    metric["selected_rows"] += len(frame)
    minimum, maximum = frame["local_date"].min(), frame["local_date"].max()
    metric["min_date"] = minimum if metric["min_date"] is None else min(metric["min_date"], minimum)
    metric["max_date"] = maximum if metric["max_date"] is None else max(metric["max_date"], maximum)
    metric["dates"].update(frame["local_date"].dt.date.unique())

    quality["rows"] += len(frame)
    quality["zero_flow_rows"] += int((frame["pedestrian_flow"] == 0).sum())
    quality["invalid_direction_rows"] += int((frame["direction_counts_valid"] == False).sum())  # noqa: E712
    quality["missing_sensor_name_rows"] += int(frame["sensor_name_missing"].sum())
    quality["missing_coordinate_rows"] += int(frame["coordinate_missing"].sum())
    quality["invalid_coordinate_rows"] += int((frame["coordinate_valid"] == False).sum())  # noqa: E712
    quality["missing_current_metadata_rows"] += int((~frame["sensor_in_current_metadata"]).sum())
    quality["dst_ambiguous_rows"] += int(frame["dst_ambiguous_local_time"].sum())
    quality["dst_nonexistent_rows"] += int(frame["dst_nonexistent_local_time"].sum())
    quality["reconstructed_hour_rows"] += int(frame["hour_was_reconstructed"].sum())

    for sensor_id, rows in frame.groupby("sensor_id", sort=False):
        current = sensor_metrics[int(sensor_id)]
        current["row_count"] += len(rows)
        current[f"{source}_rows"] += len(rows)
        current["coordinate_rows"] += int((~rows["coordinate_missing"]).sum())
        current["reconstructed_hour_rows"] += int(rows["hour_was_reconstructed"].sum())
        current["names"].update(rows["sensor_name"].dropna().astype(str).unique())
        low, high = rows["local_date"].min(), rows["local_date"].max()
        current["first_date"] = low if current["first_date"] is None else min(current["first_date"], low)
        current["last_date"] = high if current["last_date"] is None else max(current["last_date"], high)


def table_from_frame(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(frame, schema=OUTPUT_SCHEMA, preserve_index=False, safe=True)


def validate_unique_natural_keys(path: Path) -> None:
    keys = pq.read_table(path, columns=["sensor_id", "observed_at_local"]).to_pandas()
    duplicated = keys.duplicated(["sensor_id", "observed_at_local"], keep=False)
    if duplicated.any():
        sample = keys.loc[duplicated].head(5).to_dict(orient="records")
        raise ValueError(
            f"duplicate natural key (sensor_id, local date/hour) detected; sample={sample}"
        )


def sensor_coverage_frame(sensor_metrics: dict[int, dict[str, Any]], metadata: pd.DataFrame) -> pd.DataFrame:
    metadata = metadata.copy()
    metadata["location_id"] = pd.to_numeric(metadata["location_id"], errors="raise").astype("int32")
    metadata = metadata.set_index("location_id", drop=False)
    rows: list[dict[str, Any]] = []
    for sensor_id in sorted(sensor_metrics):
        metric = sensor_metrics[sensor_id]
        row: dict[str, Any] = {
            "sensor_id": sensor_id,
            "row_count": metric["row_count"],
            "first_local_date": metric["first_date"],
            "last_local_date": metric["last_date"],
            "historical_rows": metric[f"{HISTORICAL_ID}_rows"],
            "wayback_gap_rows": metric[f"{WAYBACK_ID}_rows"],
            "current_rows": metric[f"{CURRENT_ID}_rows"],
            "observed_coordinate_rows": metric["coordinate_rows"],
            "reconstructed_hour_rows": metric["reconstructed_hour_rows"],
            "distinct_source_names": len(metric["names"]),
            "source_names": " | ".join(sorted(metric["names"])),
            "current_metadata_present": sensor_id in metadata.index,
        }
        if sensor_id in metadata.index:
            item = metadata.loc[sensor_id]
            for column in [
                "sensor_description", "sensor_name", "installation_date", "note",
                "location_type", "status", "direction_1", "direction_2", "latitude", "longitude",
            ]:
                row[f"metadata_{column}"] = item.get(column)
        rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-zip", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--wayback-csv", type=Path, default=DEFAULT_WAYBACK)
    parser.add_argument("--current-parquet", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--sensor-locations", type=Path, default=DEFAULT_SENSORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=250_000)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be positive")
    input_paths = {
        "historical": args.historical_zip.resolve(),
        "wayback": args.wayback_csv.resolve(),
        "current": args.current_parquet.resolve(),
        "sensor_locations": args.sensor_locations.resolve(),
    }
    for label, path in input_paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} input does not exist: {path}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    hourly_path = output_dir / "pedestrian_flow_hourly.parquet"
    coverage_path = output_dir / "sensor_coverage.parquet"
    manifest_path = output_dir / "quality_manifest.json"
    temporary_hourly = output_dir / ".pedestrian_flow_hourly.tmp.parquet"
    for final in (hourly_path, coverage_path, manifest_path):
        if final.exists() and not args.overwrite:
            raise FileExistsError(f"output already exists (pass --overwrite): {final}")
    if temporary_hourly.exists():
        temporary_hourly.unlink()

    metadata = pd.read_parquet(input_paths["sensor_locations"])
    require_columns(metadata.columns, {"location_id"}, "sensor locations Parquet")
    if metadata["location_id"].duplicated().any():
        raise ValueError("sensor locations metadata contains duplicate location_id values")
    metadata_ids = set(pd.to_numeric(metadata["location_id"], errors="raise").astype(int))
    repaired_hours, repair_metrics = inspect_september_2010_repair(
        input_paths["historical"], args.chunk_size
    )

    quality: Counter[str] = Counter()
    source_metrics = defaultdict(lambda: {"input_rows": 0, "selected_rows": 0, "min_date": None, "max_date": None, "dates": set()})
    sensor_metrics = defaultdict(lambda: {
        "row_count": 0, "coordinate_rows": 0, "reconstructed_hour_rows": 0,
        "names": set(), "first_date": None, "last_date": None,
        f"{HISTORICAL_ID}_rows": 0, f"{WAYBACK_ID}_rows": 0, f"{CURRENT_ID}_rows": 0,
    })
    dst_cache: dict[datetime, tuple[bool | None, bool, bool]] = {}
    writer: pq.ParquetWriter | None = None

    def write(frame: pd.DataFrame) -> None:
        nonlocal writer
        if frame.empty:
            return
        update_metrics(frame, quality, source_metrics, sensor_metrics)
        table = table_from_frame(frame)
        if writer is None:
            writer = pq.ParquetWriter(temporary_hourly, OUTPUT_SCHEMA, compression="zstd")
        writer.write_table(table, row_group_size=min(len(frame), 250_000))

    try:
        offset = 0
        for chunk in historical_reader(input_paths["historical"], args.chunk_size):
            source_metrics[HISTORICAL_ID]["input_rows"] += len(chunk)
            write(normalize_historical(chunk, offset, repaired_hours, metadata_ids, dst_cache))
            offset += len(chunk)

        offset = 0
        for chunk in pd.read_csv(
            input_paths["wayback"], sep=";", chunksize=args.chunk_size,
            dtype={"id": "string", "location": "string"},
        ):
            source_metrics[WAYBACK_ID]["input_rows"] += len(chunk)
            write(normalize_modern(chunk, offset, WAYBACK_ID, metadata_ids, dst_cache))
            offset += len(chunk)

        parquet = pq.ParquetFile(input_paths["current"])
        offset = 0
        for batch in parquet.iter_batches(batch_size=args.chunk_size):
            chunk = batch.to_pandas()
            source_metrics[CURRENT_ID]["input_rows"] += len(chunk)
            write(normalize_modern(chunk, offset, CURRENT_ID, metadata_ids, dst_cache))
            offset += len(chunk)

        current_ids = pq.read_table(input_paths["current"], columns=["id"])["id"]
        current_distinct_ids = int(pc.count_distinct(current_ids).as_py())
        quality["current_source_record_id_distinct"] = current_distinct_ids
        quality["current_source_record_id_excess_rows"] = len(current_ids) - current_distinct_ids

        if writer is None:
            pq.write_table(pa.Table.from_pylist([], schema=OUTPUT_SCHEMA), temporary_hourly)
        else:
            writer.close()
            writer = None

        validate_unique_natural_keys(temporary_hourly)
        if quality["reconstructed_hour_rows"] != repair_metrics["rows"]:
            raise ValueError("not all verified September 2010 hours were reconstructed")

        coverage = sensor_coverage_frame(sensor_metrics, metadata)
        temporary_coverage = coverage_path.with_suffix(".tmp.parquet")
        coverage.to_parquet(temporary_coverage, index=False, compression="zstd")
        os.replace(temporary_hourly, hourly_path)
        os.replace(temporary_coverage, coverage_path)

        sources_json: dict[str, Any] = {}
        for source_id in (HISTORICAL_ID, WAYBACK_ID, CURRENT_ID):
            metric = source_metrics[source_id]
            sources_json[source_id] = {
                "input_rows": metric["input_rows"],
                "selected_rows": metric["selected_rows"],
                "min_date": metric["min_date"].date().isoformat() if metric["min_date"] is not None else None,
                "max_date": metric["max_date"].date().isoformat() if metric["max_date"] is not None else None,
                "distinct_dates": len(metric["dates"]),
            }
        manifest = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "measure": "hourly pedestrian flow past a fixed counting location; not crowd density",
            "natural_key": ["sensor_id", "local_date", "local_hour"],
            "timezone": TIMEZONE,
            "source_precedence": [
                {"source": HISTORICAL_ID, "window": "through 2022-10-31"},
                {"source": WAYBACK_ID, "window": "2022-11-01 through 2024-08-20"},
                {"source": CURRENT_ID, "window": "from 2024-08-21"},
            ],
            "inputs": {
                label: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for label, path in input_paths.items()
            },
            "sources": sources_json,
            "outputs": {
                "hourly": {"path": str(hourly_path), "rows": quality["rows"], "bytes": hourly_path.stat().st_size},
                "sensor_coverage": {"path": str(coverage_path), "rows": len(coverage), "bytes": coverage_path.stat().st_size},
            },
            "quality": {
                **dict(sorted(quality.items())),
                "duplicate_natural_keys": 0,
                "september_2010_repair_groups": repair_metrics["groups"],
                "gap_expected_days": 659,
                "gap_observed_days": sources_json[WAYBACK_ID]["distinct_dates"],
            },
            "assertions": {
                "source_windows_are_non_overlapping": True,
                "natural_key_is_unique": True,
                "missing_hours_were_zero_imputed": False,
                "historical_coordinates_backfilled_from_current_metadata": False,
                "utc_instants_invented_for_naive_source_times": False,
                "publisher_source_record_id_used_as_natural_key": False,
            },
            "training_notes": [
                "Do not use direction_1_count or direction_2_count to predict pedestrian_flow for the same row; they sum to the target.",
                "Use chronological/as-of evaluation rather than random row splits.",
                "Sensor names and current metadata are not effective-dated location history; review relocation notes before spatial joins.",
                "DST ambiguous wall-clock rows cannot be assigned a unique UTC instant from these sources.",
                "Weather, events, COVID regimes, and route-edge mappings remain separate, time-aware feature work.",
            ],
        }
        temporary_manifest = manifest_path.with_suffix(".tmp.json")
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_manifest, manifest_path)
    except Exception:
        if writer is not None:
            writer.close()
        if temporary_hourly.exists():
            temporary_hourly.unlink()
        raise

    print(f"Wrote {quality['rows']:,} rows to {hourly_path}")
    print(f"Wrote {len(coverage):,} sensor summaries to {coverage_path}")
    print(f"Quality manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
