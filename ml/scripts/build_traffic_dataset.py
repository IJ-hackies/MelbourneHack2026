#!/usr/bin/env python3
"""Build HeatRoute's canonical hourly vehicle-traffic target.

This module deliberately stops at source cleaning.  It does not acquire data,
join weather/roads/holidays, or train a model.  City Transport Activity ZIPs
are reduced to reviewed motor-vehicle countline hours and Victorian SCATS ZIPs
are reduced to intersection hours.  Both source families are written to one
content-addressed Parquet table with a deterministic CSV preview and manifest.

The raw inputs are treated as immutable publisher snapshots.  ZIP members are
read in chunks and all validation happens before an output is published.
The Transport Activity publisher appends ``Z`` to Melbourne wall-clock labels;
this builder intentionally interprets those labels in ``Australia/Melbourne``
and chooses standard time for ambiguous daylight-saving fallback values.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor, Future, as_completed
from contextlib import contextmanager
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS = ROOT / "ml" / "traffic" / "datasets"
DEFAULT_REGISTRY = ROOT / "ml" / "traffic" / "config" / "transport_activity_countlines.csv"
DEFAULT_SIGNALS = DEFAULT_DATASETS / "vic_traffic_signals" / "victorian_traffic_signals.csv"
DEFAULT_OUTPUT = ROOT / "ml" / "traffic" / "processed"
DEFAULT_EXPECTED_CONFIG = ROOT / "ml" / "traffic" / "config" / "expected_coverage.json"

MELBOURNE_TIMEZONE = "Australia/Melbourne"
SCATS_SOURCE_TIMEZONE = "UTC+10"
SCATS_SOURCE_OFFSET_MINUTES = 600
CITY_BBOX = (144.89, -37.86, 145.01, -37.76)

MOTOR_CLASSES = frozenset(
    {
        "car",
        "taxi",
        "van",
        "bus",
        "minibus",
        "motorbike",
        "rigid",
        "truck",
        "emergency_car",
        "emergency_van",
        "fire_engine",
    }
)
KNOWN_CLASSES = MOTOR_CLASSES | frozenset({"cyclist", "pedestrian", "escooter"})

TA_REQUIRED_COLUMNS = {
    "countLocationId",
    "countlineName",
    "countlineDirection",
    "CountLocationLat",
    "CountLocationLong",
    "from",
    "to",
    "class",
    "count",
    "year",
    "quarter",
}
REGISTRY_REQUIRED_COLUMNS = {
    "count_location_id",
    "countline_name",
    "channel_type",
    "traffic_eligible",
    "review_status",
    "notes",
}
SIGNAL_REQUIRED_COLUMNS = {"SITE_NO", "SITE_NAME", "TYPE", "MUNICIPALITY", "LATITUDE", "LONGITUDE"}
SCATS_BASE_COLUMNS = {
    "NB_SCATS_SITE",
    "QT_INTERVAL_COUNT",
    "NB_DETECTOR",
    "NM_REGION",
    "CT_RECORDS",
    "QT_VOLUME_24HOUR",
    "CT_ALARM_24HOUR",
}
SCATS_INTERVAL_COLUMNS = tuple(f"V{i:02d}" for i in range(96))
SCATS_REQUIRED_COLUMNS = SCATS_BASE_COLUMNS | set(SCATS_INTERVAL_COLUMNS)


def _output_schema() -> pa.Schema:
    """Return the explicit public schema used in the canonical artifact."""

    return pa.schema(
        [
            ("source_dataset_id", pa.string()),
            ("observation_unit_id", pa.string()),
            ("observation_id", pa.string()),
            ("hour_start_utc", pa.timestamp("us", tz="UTC")),
            ("local_timestamp", pa.timestamp("us", tz=MELBOURNE_TIMEZONE)),
            ("local_date", pa.date32()),
            ("local_hour", pa.int8()),
            ("year", pa.int16()),
            ("month", pa.int8()),
            ("day", pa.int8()),
            ("day_of_week", pa.int8()),
            ("is_weekend", pa.bool_()),
            ("timezone_name", pa.string()),
            ("source_timezone_name", pa.string()),
            ("source_timezone_offset_minutes", pa.int32()),
            ("local_utc_offset_minutes", pa.int32()),
            ("source_timestamp_utc", pa.timestamp("us", tz="UTC")),
            ("source_timestamp_first_utc", pa.timestamp("us", tz="UTC")),
            ("source_timestamp_last_utc", pa.timestamp("us", tz="UTC")),
            ("source_timestamp_lineage", pa.string()),
            ("source_timestamp_count", pa.int64()),
            ("source_timestamp_semantics", pa.string()),
            ("source_archive_member", pa.string()),
            ("source_date_local", pa.date32()),
            ("source_row_count", pa.int64()),
            ("source_record_count", pa.int64()),
            ("count_location_id", pa.string()),
            ("countline_name", pa.string()),
            ("channel_type", pa.string()),
            ("traffic_eligible", pa.bool_()),
            ("review_status", pa.string()),
            ("scats_site", pa.string()),
            ("physical_site_id", pa.string()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("coordinate_valid", pa.bool_()),
            ("coordinate_missing", pa.bool_()),
            ("coordinate_drift_flag", pa.bool_()),
            ("vehicle_count", pa.int64()),
            ("intersection_total", pa.int64()),
            ("log1p_vehicle_count", pa.float64()),
            ("label_quality", pa.string()),
            ("quality_flag", pa.string()),
            ("quality_partial_flag", pa.bool_()),
            ("quality_alarm_flag", pa.bool_()),
            ("quality_missing_interval_count", pa.int64()),
            ("measurement_scope", pa.string()),
            ("label_source", pa.string()),
            ("ta_motor_class_rows", pa.int64()),
            ("ta_non_motor_class_rows", pa.int64()),
            ("ta_reported_class_rows", pa.int64()),
            ("ta_derived_zero", pa.bool_()),
            ("ta_dst_ambiguous_flag", pa.bool_()),
            ("ta_dst_fallback_wrap_flag", pa.bool_()),
            ("scats_detector_count", pa.int64()),
            ("scats_detector_row_count", pa.int64()),
            ("scats_ct_records_min", pa.int64()),
            ("scats_ct_records_max", pa.int64()),
            ("scats_qt_volume_24hour_sum", pa.float64()),
            ("scats_alarm_24hour_count", pa.int64()),
            ("scats_source_date_local", pa.date32()),
        ]
    )


OUTPUT_SCHEMA = _output_schema()
OUTPUT_COLUMNS = [field.name for field in OUTPUT_SCHEMA]
NATURAL_KEY = ["source_dataset_id", "observation_unit_id", "hour_start_utc"]
CONTENT_HASH_EXECUTION_ONLY_OPTIONS = frozenset(
    {
        "workers",
        "requested_workers",
        "effective_workers",
        "worker_strategy",
        "phase_worker_strategies",
    }
)


@dataclass(frozen=True)
class ArchiveInfo:
    path: Path
    source_dataset_id: str
    kind: str


RegistryLookup = Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class LogicalWorkUnit:
    """One independently stageable source unit.

    Transport Activity archives remain one unit.  A SCATS archive with nested
    monthly ZIP members is fanned out by the parent into one unit per nested
    member; a top-level-CSV SCATS archive remains one unit containing all of
    its top-level CSV members.
    """

    archive_info: ArchiveInfo
    nested_member: str | None = None
    top_level_members: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.nested_member is not None:
            return f"{self.archive_info.source_dataset_id}::{self.nested_member}"
        return self.archive_info.source_dataset_id


@dataclass(frozen=True)
class StageTask:
    """Spawn-picklable task payload for the shared staging pool."""

    index: int
    unit: LogicalWorkUnit
    chunk_size: int
    stage_path: Path
    registry: RegistryLookup | None = None
    eligible_site_ids: set[str] | None = None
    coordinates: Mapping[str, Mapping[str, Any]] | None = None
    bbox: tuple[float, float, float, float] | None = None
    include_missing_coordinates: bool = False


SCATS_METRIC_SUM_KEYS = frozenset(
    {
        "input_rows",
        "selected_rows",
        "raw_rows",
        "bbox_excluded_rows",
        "omitted_null_target_site_hours",
        "staged_rows",
        "scats_negative_interval_count",
        "scats_standard_minus_one_count",
        "scats_nonstandard_negative_count",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_snapshot(path: Path) -> dict[str, Any]:
    """Return a content snapshot for an input that is expected to be immutable.

    The second stat guards against hashing a file while it is being replaced.
    ``mtime_ns`` is deliberately an internal stability check only; it is not
    part of the content-addressed manifest.
    """

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {
            "path": str(resolved),
            "exists": False,
            "bytes": None,
            "sha256": None,
        }
    if not resolved.is_file():
        raise ValueError(f"immutable input is not a file: {resolved}")
    before = resolved.stat()
    digest = sha256_file(resolved)
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"immutable input changed while hashing: {resolved}")
    return {
        "path": str(resolved),
        "exists": True,
        "bytes": after.st_size,
        "sha256": digest,
    }


def _revalidate_input_snapshots(
    snapshots: Sequence[Mapping[str, Any]],
    phase: str,
) -> None:
    """Fail closed when an immutable input changed during a build phase."""

    for expected in snapshots:
        path = Path(str(expected["path"]))
        observed = _file_snapshot(path)
        expected_signature = (
            bool(expected.get("exists", True)),
            expected.get("bytes"),
            expected.get("sha256"),
        )
        observed_signature = (
            bool(observed.get("exists", False)),
            observed.get("bytes"),
            observed.get("sha256"),
        )
        if expected_signature != observed_signature:
            label = " ".join(
                part
                for part in (
                    str(expected.get("kind", "input")),
                    str(expected.get("source_dataset_id", "")),
                )
                if part and part != "None"
            )
            raise ValueError(
                f"immutable input changed during {phase}: {label} {path} "
                f"(expected={expected_signature}, observed={observed_signature})"
            )


@contextmanager
def _exclusive_publish_lock(path: Path) -> Iterator[None]:
    """Serialize publication for one content-versioned output stem.

    A lock directory is created with mkdir, which is an exclusive filesystem
    operation.  An existing lock is treated as an active concurrent publisher
    (or a stale lock after an interrupted process) and therefore fails closed.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError as error:
        raise FileExistsError(f"traffic output publication is already in progress: {path}") from error
    try:
        yield
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _publish_staged_file(source: Path, destination: Path, *, overwrite: bool) -> None:
    """Publish a complete staged file without replacing an unauthorized output."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        os.replace(source, destination)
        return
    try:
        # Source and destination are in the same output directory.  A hard
        # link makes the complete staged inode visible atomically and fails
        # with EEXIST instead of silently replacing a concurrent output.
        os.link(source, destination)
    except FileExistsError as error:
        raise FileExistsError(
            f"content-versioned output was published concurrently: {destination}"
        ) from error
    except OSError as error:
        raise RuntimeError(
            f"cannot publish output exclusively without overwrite: {destination}"
        ) from error
    source.unlink()


def require_columns(actual: Iterable[str], expected: set[str], label: str) -> None:
    missing = expected - set(actual)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def _canonical_id(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    # CSV readers commonly turn integer identifiers into values such as 44864.0.
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def _canonical_id_series(series: pd.Series, label: str) -> pd.Series:
    result = series.map(_canonical_id).astype("string")
    if result.isna().any():
        raise ValueError(f"{label} contains null/blank identifiers")
    return result


def _normalise_bool_series(series: pd.Series, label: str) -> pd.Series:
    values: list[bool | pd._libs.missing.NAType] = []
    for value in series.tolist():
        if value is None or (isinstance(value, float) and math.isnan(value)):
            values.append(pd.NA)
            continue
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "t"}:
            values.append(True)
        elif text in {"false", "0", "no", "n", "f"}:
            values.append(False)
        else:
            raise ValueError(f"{label} contains an unrecognised boolean value: {value!r}")
    result = pd.Series(values, index=series.index, dtype="boolean")
    if result.isna().any():
        raise ValueError(f"{label} contains null boolean values")
    return result


def _strict_numeric(series: pd.Series, label: str, *, integer: bool = False) -> pd.Series:
    text = series.astype("string")
    nonblank = text.notna() & text.str.strip().ne("")
    numeric = pd.to_numeric(series, errors="coerce")
    invalid = nonblank & numeric.isna()
    if invalid.any():
        sample = series.loc[invalid].head(3).tolist()
        raise ValueError(f"{label} contains non-numeric values: {sample}")
    if integer:
        non_integer = numeric.notna() & ((numeric % 1) != 0)
        if non_integer.any():
            sample = numeric.loc[non_integer].head(3).tolist()
            raise ValueError(f"{label} contains non-integer values: {sample}")
    return numeric


def _localize_melbourne_standard_fold(value: pd.Timestamp) -> tuple[pd.Timestamp | None, int | None, bool, bool]:
    """Localize a Melbourne wall time, choosing standard time for a fold.

    ``zoneinfo`` exposes both sides of a fallback through ``fold``.  A
    round-trip check distinguishes valid, ambiguous, and nonexistent wall
    times.  For an ambiguous value we deliberately choose ``fold=1`` (AEST,
    UTC+10) so a publisher label has one deterministic UTC interpretation.
    """

    naive = value.to_pydatetime().replace(tzinfo=None)
    zone = ZoneInfo(MELBOURNE_TIMEZONE)
    valid: list[datetime] = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        round_trip = aware.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive:
            valid.append(aware)
    if not valid:
        return None, None, False, True
    offsets = {item.utcoffset() for item in valid}
    ambiguous = len(offsets) > 1
    chosen = next((item for item in valid if item.fold == 1), valid[0]) if ambiguous else valid[0]
    offset = chosen.utcoffset()
    offset_minutes = int(offset.total_seconds() // 60) if offset is not None else None
    return pd.Timestamp(chosen), offset_minutes, ambiguous, False


def _parse_ta_wall_z(series: pd.Series, label: str) -> pd.DataFrame:
    """Parse TA's misleading ``Z`` labels as Melbourne wall-clock values.

    The raw suffix is retained by the caller for lineage.  Removing only the
    suffix before parsing is intentional: the publisher's date/time fields
    represent local Melbourne wall time, not UTC instants.
    """

    raw = series.astype("string").str.strip()
    if raw.isna().any() or raw.eq("").any():
        raise ValueError(f"{label} contains null/blank timestamps")
    if (~raw.str.endswith("Z")).any():
        sample = raw.loc[~raw.str.endswith("Z")].head(3).tolist()
        raise ValueError(f"{label} must use source timestamps ending in Z; sample={sample}")
    wall_text = raw.str.slice(stop=-1)
    naive = pd.to_datetime(wall_text, errors="coerce", format="ISO8601")
    if naive.isna().any():
        sample = raw.loc[naive.isna()].head(3).tolist()
        raise ValueError(f"{label} contains invalid Melbourne wall timestamps: {sample}")
    cache: dict[pd.Timestamp, tuple[pd.Timestamp | None, int | None, bool, bool]] = {}
    rows: list[dict[str, Any]] = []
    for raw_value, naive_value in zip(raw.tolist(), naive.tolist()):
        key = pd.Timestamp(naive_value)
        localized = cache.get(key)
        if localized is None:
            localized = _localize_melbourne_standard_fold(key)
            cache[key] = localized
        aware, offset_minutes, ambiguous, nonexistent = localized
        rows.append(
            {
                "raw": str(raw_value),
                "wall": key,
                "local": aware,
                "utc": aware.tz_convert("UTC") if aware is not None else pd.NaT,
                "offset_minutes": offset_minutes,
                "ambiguous": ambiguous,
                "nonexistent": nonexistent,
            }
        )
    result = pd.DataFrame(rows, index=series.index)
    if result["nonexistent"].any():
        sample = result.loc[result["nonexistent"], "raw"].head(3).tolist()
        raise ValueError(f"{label} contains nonexistent Melbourne wall times: {sample}")
    result["utc"] = pd.to_datetime(result["utc"], errors="coerce", utc=True)
    return result


def _validate_ta_interval_contract(selected: pd.DataFrame, member: str) -> None:
    """Enforce the publisher's five-minute wall-clock interval contract.

    The source labels are Melbourne wall time.  Therefore duration is checked
    in ``*_wall`` rather than UTC: the official rollback fixture includes
    ``01:55 -> 02:00`` and the publisher's verified ``02:55 -> 02:00``
    representation.  The latter is explicitly accepted when both endpoints
    are ambiguous and the wall clock reverses; all other reversed intervals
    have already failed closed above.

    Different overlapping spans are invalid.  Same-span rows with different
    counts are retained as publisher revisions because the official archives
    contain those rows and the existing cleaner intentionally aggregates them.
    """

    from_wall = selected["_from_wall"]
    to_wall = selected["_to_wall"]
    from_aligned = (
        (from_wall.dt.minute % 5 == 0)
        & from_wall.dt.second.eq(0)
        & from_wall.dt.microsecond.eq(0)
        & from_wall.dt.nanosecond.eq(0)
    )
    to_aligned = (
        (to_wall.dt.minute % 5 == 0)
        & to_wall.dt.second.eq(0)
        & to_wall.dt.microsecond.eq(0)
        & to_wall.dt.nanosecond.eq(0)
    )
    fallback_wrap = selected["_dst_fallback_wrap"].fillna(False).astype(bool)

    # Check for a genuinely different overlapping span before the shape error
    # below so the failure identifies both contracts when a malformed source
    # interval also overlaps a neighbouring row.  Fallback rows are excluded
    # because their verified wall-clock reversal is intentionally exceptional.
    normal = selected.loc[~fallback_wrap].copy()
    stream_columns = ["_count_location_id", "_countline_name", "countlineDirection", "_class"]
    for _stream, group in normal.groupby(stream_columns, dropna=False, sort=False):
        if len(group) < 2:
            continue
        ordered = group.sort_values(["_from_wall", "_to_wall"], kind="mergesort")
        previous_from = ordered["_from_wall"].shift()
        previous_to = ordered["_to_wall"].shift()
        current_from = ordered["_from_wall"]
        current_to = ordered["_to_wall"]
        different_span = (current_from != previous_from) | (current_to != previous_to)
        invalid_overlap = (current_from < previous_to) & different_span
        if invalid_overlap.any():
            sample = ordered.loc[invalid_overlap, ["from", "to"]].head(3).to_dict("records")
            raise ValueError(
                f"{member} contains overlapping Transport Activity intervals "
                f"for one source stream: {sample}"
            )

    wall_duration = to_wall - from_wall
    invalid_shape = (~from_aligned) | (~to_aligned) | (
        ~fallback_wrap & ~wall_duration.eq(pd.Timedelta(5, unit="min"))
    )
    if invalid_shape.any():
        sample = selected.loc[invalid_shape, ["from", "to"]].head(3).to_dict("records")
        raise ValueError(
            f"{member} contains Transport Activity intervals that are not "
            f"five-minute aligned/duration rows: {sample}"
        )

    # Once the shape contract holds, non-fallback rows can only overlap when
    # they share a start.  Allow exact-span publisher revisions, but reject a
    # different end point that overlaps an earlier interval in this chunk.


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _truthy(value: Any) -> bool:
    """Treat pandas' nullable missing values as false for quality flags."""

    if value is None or value is pd.NA:
        return False
    try:
        if bool(pd.isna(value)):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _coord_key(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 7), round(lon, 7)


def physical_site_id(latitude: float | None, longitude: float | None) -> str | None:
    """Return a stable coordinate-derived site identifier."""

    key = _coord_key(latitude, longitude)
    if key is None:
        return None
    return f"coord:{key[0]:.7f}:{key[1]:.7f}"


def _source_id_for_path(path: Path, prefix: str) -> str:
    for parent in (path.parent, *path.parents):
        if parent.name.startswith(prefix):
            return parent.name
    return path.stem


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    result: dict[str, Path] = {}
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved.is_file() and resolved.suffix.lower() == ".zip":
            result[str(resolved).lower()] = resolved
    return sorted(result.values(), key=lambda item: str(item).lower())


def _expand_zip_paths(paths: Iterable[Path]) -> list[Path]:
    expanded: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if path.is_dir():
            expanded.extend(path.rglob("*.zip"))
        elif path.is_file():
            expanded.append(path)
        else:
            raise FileNotFoundError(f"archive path does not exist: {raw}")
    return _unique_paths(expanded)


def discover_transport_archives(
    datasets_dir: Path = DEFAULT_DATASETS,
    paths: Sequence[Path] | None = None,
) -> list[ArchiveInfo]:
    """Discover all Transport Activity ZIPs without opening their data rows."""

    candidates = _expand_zip_paths(paths) if paths else _expand_zip_paths(
        [path for path in datasets_dir.rglob("*.zip") if _source_id_for_path(path, "com_transport_activity_").startswith("com_transport_activity_")]
    )
    archives = [
        ArchiveInfo(
            path,
            _source_id_for_path(path, "com_transport_activity_")
            if _source_id_for_path(path, "com_transport_activity_").startswith("com_transport_activity_")
            else path.stem,
            "transport_activity",
        )
        for path in candidates
        if paths or _source_id_for_path(path, "com_transport_activity_").startswith("com_transport_activity_")
    ]
    return archives


def _member_header(archive: zipfile.ZipFile, member: str) -> list[str]:
    with archive.open(member) as handle:
        return _header_from_handle(handle, member=member)


def _header_from_handle(handle: Any, *, member: str = "<unknown>") -> list[str]:
    sample = handle.read(128 * 1024)
    try:
        handle.seek(0)
    except (AttributeError, OSError):
        pass
    line = sample.splitlines()[0] if sample.splitlines() else b""
    try:
        return next(csv.reader([line.decode("utf-8-sig")]))
    except (UnicodeDecodeError, StopIteration, csv.Error) as error:
        raise ValueError(
            f"cannot read CSV header from ZIP member {member!r}: {error}"
        ) from error


def _data_members(archive: zipfile.ZipFile) -> list[str]:
    return [
        name
        for name in archive.namelist()
        if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
    ]


def _nested_zip_members(archive: zipfile.ZipFile) -> list[str]:
    return [
        name
        for name in archive.namelist()
        if name.lower().endswith(".zip") and not name.startswith("__MACOSX/")
    ]


def _logical_csv_members(
    archive: zipfile.ZipFile,
    *,
    nested_member: str | None = None,
    top_level_members: Sequence[str] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield the CSV members belonging to one logical SCATS unit.

    With no selector this retains the discovery behavior used to validate an
    archive: top-level CSVs followed by all nested ZIP CSVs.  The staging
    parent passes either a fixed top-level member list or one nested ZIP
    member, so a nested annual archive can be processed by independent
    workers without reopening unrelated months in every worker.
    """

    if nested_member is not None:
        if nested_member not in set(_nested_zip_members(archive)):
            raise ValueError(f"SCATS nested ZIP member is not present: {nested_member}")
        try:
            with archive.open(nested_member) as source:
                with tempfile.TemporaryFile(prefix="traffic-inner-scats-") as temporary:
                    shutil.copyfileobj(source, temporary, length=1024 * 1024)
                    temporary.seek(0)
                    with zipfile.ZipFile(temporary) as nested:
                        members = sorted(_data_members(nested))
                        if not members:
                            raise ValueError(f"nested SCATS ZIP has no CSV members: {nested_member}")
                        for member in members:
                            with nested.open(member) as handle:
                                yield f"{nested_member}::{member}", handle
        except zipfile.BadZipFile as error:
            raise ValueError(f"invalid nested SCATS ZIP member: {nested_member}") from error
        return

    members = sorted(top_level_members if top_level_members is not None else _data_members(archive))
    for member in members:
        with archive.open(member) as handle:
            yield member, handle
    # An explicit top-level member list is a complete unit selection.  The
    # default path keeps the old archive-wide discovery behavior.
    if top_level_members is not None:
        return
    for nested_member in sorted(_nested_zip_members(archive)):
        yield from _logical_csv_members(archive, nested_member=nested_member)


def _archive_has_csv_schema(path: Path, required: set[str]) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            for member, handle in _logical_csv_members(archive):
                if required.issubset(set(_header_from_handle(handle, member=member))):
                    return True
    except zipfile.BadZipFile as error:
        raise ValueError(f"invalid ZIP archive: {path}") from error
    return False


def _is_scats_archive(path: Path) -> bool:
    return _archive_has_csv_schema(path, SCATS_REQUIRED_COLUMNS)


def _normalise_source_dataset_values(value: Any, label: str) -> list[str] | None:
    """Resolve a configured source-dataset list without accepting blanks."""

    if value is None:
        return None
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise ValueError(f"{label} must be a list of source dataset ids")

    resolved: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("source_dataset_id", item.get("id"))
        text = str(item).strip() if item is not None else ""
        if not text:
            raise ValueError(f"{label} contains a blank source dataset id")
        resolved.append(text)
    return sorted(set(resolved))


def discover_scats_archives(
    datasets_dir: Path = DEFAULT_DATASETS,
    paths: Sequence[Path] | None = None,
    *,
    source_dataset_allowlist: Sequence[str] | None = None,
) -> list[ArchiveInfo]:
    """Discover SCATS ZIPs recursively, ignoring other traffic ZIP families."""

    explicit = paths is not None
    allowed_sources = (
        set(_normalise_source_dataset_values(source_dataset_allowlist, "SCATS source dataset allowlist") or [])
        if source_dataset_allowlist is not None
        else set()
    )
    if not explicit and not allowed_sources:
        raise ValueError(
            "implicit SCATS discovery requires a non-empty resolved SCATS source dataset allowlist "
            "in expected coverage config (scats.source_datasets)"
        )
    candidates = _expand_zip_paths(paths) if explicit else _expand_zip_paths([datasets_dir])
    archives: list[ArchiveInfo] = []
    for path in candidates:
        if not _is_scats_archive(path):
            if explicit:
                raise ValueError(f"explicit SCATS archive does not have a SCATS schema: {path}")
            continue
        source_dataset_id = _source_id_for_path(path, "vic_scats_") or path.stem
        if not explicit and source_dataset_id not in allowed_sources:
            continue
        archives.append(ArchiveInfo(path, source_dataset_id, "scats"))
    if not explicit:
        observed_sources = {archive.source_dataset_id for archive in archives}
        missing_sources = sorted(allowed_sources - observed_sources)
        if missing_sources:
            raise FileNotFoundError(
                "configured SCATS source dataset archives were not discovered: "
                f"{missing_sources}; searched {datasets_dir.resolve()}"
            )
    return sorted(archives, key=lambda item: (item.source_dataset_id, str(item.path).lower()))


def enumerate_scats_logical_units(archives: Sequence[ArchiveInfo]) -> list[LogicalWorkUnit]:
    """Enumerate SCATS work units before any process pool is created."""

    units: list[LogicalWorkUnit] = []
    for archive_info in sorted(archives, key=lambda item: (item.source_dataset_id, str(item.path).lower())):
        try:
            with zipfile.ZipFile(archive_info.path) as archive:
                top_level_members = tuple(sorted(_data_members(archive)))
                nested_members = tuple(sorted(_nested_zip_members(archive)))
        except zipfile.BadZipFile as error:
            raise ValueError(f"invalid SCATS ZIP: {archive_info.path}") from error
        if top_level_members:
            units.append(
                LogicalWorkUnit(
                    archive_info=archive_info,
                    top_level_members=top_level_members,
                )
            )
        for nested_member in nested_members:
            units.append(
                LogicalWorkUnit(
                    archive_info=archive_info,
                    nested_member=nested_member,
                )
            )
        if not top_level_members and not nested_members:
            raise ValueError(f"SCATS ZIP has no CSV or nested ZIP members: {archive_info.path}")
    return units


def load_transport_registry(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Transport Activity review registry does not exist: {path}")
    registry = pd.read_csv(path, dtype="string")
    require_columns(registry.columns, REGISTRY_REQUIRED_COLUMNS, "Transport Activity review registry")
    registry = registry.copy()
    registry["count_location_id"] = _canonical_id_series(registry["count_location_id"], "registry count_location_id")
    registry["countline_name"] = registry["countline_name"].astype("string").fillna("").str.strip()
    registry["traffic_eligible"] = _normalise_bool_series(registry["traffic_eligible"], "registry traffic_eligible")
    registry["review_status"] = registry["review_status"].astype("string").str.strip().str.lower()
    if registry["review_status"].isna().any() or registry["review_status"].eq("").any():
        raise ValueError("Transport Activity review registry contains blank review_status")
    if registry["channel_type"].isna().any():
        raise ValueError("Transport Activity review registry contains null channel_type")
    blank_approved = registry["countline_name"].eq("") & registry["traffic_eligible"] & registry["review_status"].eq("approved")
    if blank_approved.any():
        sample = registry.loc[blank_approved, "count_location_id"].head(3).tolist()
        raise ValueError(f"Transport Activity review registry has blank approved countline_name: {sample}")
    registry["_registry_key"] = registry["count_location_id"] + "\x1f" + registry["countline_name"]
    if registry["_registry_key"].duplicated().any():
        duplicate = registry.loc[registry["_registry_key"].duplicated(keep=False), "_registry_key"].head(3).tolist()
        raise ValueError(f"Transport Activity review registry has duplicate labels: {duplicate}")
    return registry


def load_signal_coordinates(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"SCATS traffic-signal coordinate CSV does not exist: {path}")
    signals = pd.read_csv(path, dtype="string")
    require_columns(signals.columns, SIGNAL_REQUIRED_COLUMNS, "SCATS traffic-signal coordinates")
    signals = signals.copy()
    signals["_site"] = _canonical_id_series(signals["SITE_NO"], "signal SITE_NO")
    if signals["_site"].duplicated().any():
        duplicate = signals.loc[signals["_site"].duplicated(keep=False), "_site"].head(3).tolist()
        raise ValueError(f"SCATS traffic-signal coordinates have duplicate SITE_NO values: {duplicate}")
    latitude = pd.to_numeric(signals["LATITUDE"], errors="coerce")
    longitude = pd.to_numeric(signals["LONGITUDE"], errors="coerce")
    result: dict[str, dict[str, Any]] = {}
    for row, lat, lon in zip(signals.to_dict("records"), latitude, longitude):
        coord = _coord_key(lat, lon)
        result[str(row["_site"])] = {
            "latitude": coord[0] if coord else None,
            "longitude": coord[1] if coord else None,
            "coordinate_valid": coord is not None,
            "site_name": row.get("SITE_NAME"),
        }
    return result


def scats_bbox_eligible_site_ids(
    coordinates: Mapping[str, Mapping[str, Any]],
    bbox: tuple[float, float, float, float] | None,
) -> set[str] | None:
    """Return SCATS sites whose reviewed coordinates can enter ``bbox``.

    ``None`` means that no early site filter is safe (for example, when the
    caller disabled the bbox).  A site with missing or invalid coordinates is
    never eligible for the normal bbox path; callers that explicitly request
    missing-coordinate inclusion must retain the old post-aggregation path.
    """

    if bbox is None:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    eligible: set[str] = set()
    for site, metadata in coordinates.items():
        latitude = metadata.get("latitude")
        longitude = metadata.get("longitude")
        if (
            latitude is not None
            and longitude is not None
            and min_lat <= float(latitude) <= max_lat
            and min_lon <= float(longitude) <= max_lon
        ):
            eligible.add(str(site))
    return eligible


def _registry_lookup(registry: pd.DataFrame | RegistryLookup) -> dict[str, dict[str, Any]]:
    """Return the compact registry payload needed by archive workers.

    The parent loads and validates the registry once, then drops its pandas
    frame before staging starts.  Workers receive only these three scalar
    fields per reviewed channel rather than a dataframe or unrelated notes.
    """

    if isinstance(registry, Mapping):
        return {
            str(key): {
                "traffic_eligible": bool(value["traffic_eligible"]),
                "review_status": str(value["review_status"]),
                "channel_type": str(value["channel_type"]),
            }
            for key, value in registry.items()
        }
    return {
        str(row["_registry_key"]): {
            "traffic_eligible": bool(row["traffic_eligible"]),
            "review_status": str(row["review_status"]),
            "channel_type": str(row["channel_type"]),
        }
        for row in registry.to_dict("records")
    }


class _ParquetStageWriter:
    """Append bounded canonical batches to one temporary Parquet file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._writer: pq.ParquetWriter | None = None
        self.rows = 0
        self._closed = False

    def write(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        validate_canonical_frame(frame)
        table = _frame_to_arrow(frame)
        if self._writer is None:
            self._writer = pq.ParquetWriter(
                self.path,
                OUTPUT_SCHEMA,
                compression="zstd",
                use_dictionary=True,
            )
        self._writer.write_table(table)
        self.rows += len(frame)

    def close(self) -> None:
        if self._closed:
            return
        if self._writer is None:
            empty = pa.Table.from_arrays(
                [pa.array([], type=field.type) for field in OUTPUT_SCHEMA],
                schema=OUTPUT_SCHEMA,
            )
            pq.write_table(empty, self.path, compression="zstd")
        else:
            self._writer.close()
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._closed = True

    def __enter__(self) -> "_ParquetStageWriter":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def _new_ta_state(source_id: str, site_id: str, name: str, hour: pd.Timestamp) -> dict[str, Any]:
    return {
        "source_dataset_id": source_id,
        "count_location_id": site_id,
        "countline_name": name,
        "hour_start_utc": hour,
        "motor_count": 0,
        "motor_class_rows": 0,
        "non_motor_class_rows": 0,
        "reported_class_rows": 0,
        "classes": set(),
        "channel_types": set(),
        "review_statuses": set(),
        "source_timestamps": set(),
        "source_end_timestamps": set(),
        "source_raw_timestamps": set(),
        "source_raw_end_timestamps": set(),
        "source_offsets": set(),
        "dst_ambiguous_rows": 0,
        "dst_fallback_wrap_rows": 0,
        "source_members": set(),
        "source_row_count": 0,
        "coordinate_counts": Counter(),
        "missing_coordinate_rows": 0,
        "invalid_coordinate_rows": 0,
    }


def _update_ta_state(state: dict[str, Any], group: pd.DataFrame, member: str) -> None:
    state["motor_count"] += int(group.loc[group["_is_motor"], "_count_value"].sum())
    state["motor_class_rows"] += int(group["_is_motor"].sum())
    state["non_motor_class_rows"] += int((~group["_is_motor"]).sum())
    state["reported_class_rows"] += len(group)
    state["classes"].update(group["_class"].dropna().astype(str).tolist())
    state["channel_types"].update(group["channel_type"].dropna().astype(str).tolist())
    state["review_statuses"].update(group["review_status"].dropna().astype(str).tolist())
    state["source_timestamps"].update(
        item.isoformat()
        for item in group["_from_utc"].drop_duplicates().sort_values().tolist()
    )
    state["source_end_timestamps"].update(
        item.isoformat()
        for item in group["_to_utc"].drop_duplicates().sort_values().tolist()
    )
    state["source_raw_timestamps"].update(group["from"].astype("string").dropna().tolist())
    state["source_raw_end_timestamps"].update(group["to"].astype("string").dropna().tolist())
    state["source_offsets"].update(
        int(item)
        for item in pd.concat([group["_from_offset_minutes"], group["_to_offset_minutes"]]).dropna().unique()
    )
    state["dst_ambiguous_rows"] += int(group["_from_ambiguous"].sum()) + int(group["_to_ambiguous"].sum())
    state["dst_fallback_wrap_rows"] += int(group["_dst_fallback_wrap"].sum())
    state["source_members"].add(member)
    state["source_row_count"] += len(group)
    for lat, lon in zip(group["_latitude"], group["_longitude"]):
        key = _coord_key(lat, lon)
        if key is None:
            raw_lat = pd.isna(lat)
            raw_lon = pd.isna(lon)
            if raw_lat or raw_lon:
                state["missing_coordinate_rows"] += 1
            else:
                state["invalid_coordinate_rows"] += 1
        else:
            state["coordinate_counts"][key] += 1


def _finish_ta_states(states: Mapping[tuple[str, str, pd.Timestamp], dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states.values():
        coordinates = state["coordinate_counts"]
        selected_coord = max(coordinates.items(), key=lambda item: (item[1], item[0]))[0] if coordinates else None
        source_timestamps = sorted(state["source_timestamps"])
        source_end_timestamps = sorted(state["source_end_timestamps"])
        source_raw_timestamps = sorted(state["source_raw_timestamps"])
        source_raw_end_timestamps = sorted(state["source_raw_end_timestamps"])
        source_offsets = sorted(state["source_offsets"])
        derived_zero = state["motor_class_rows"] == 0 and state["non_motor_class_rows"] > 0
        vehicle_count = 0 if derived_zero else state["motor_count"]
        rows.append(
            {
                "source_dataset_id": state["source_dataset_id"],
                "observation_unit_id": f"ta:{state['count_location_id']}",
                "observation_id": f"ta:{state['count_location_id']}",
                "hour_start_utc": state["hour_start_utc"],
                "source_timezone_name": MELBOURNE_TIMEZONE,
                "source_timezone_offset_minutes": source_offsets[0] if len(source_offsets) == 1 else None,
                "source_timestamp_first_utc": pd.Timestamp(source_timestamps[0], tz="UTC") if source_timestamps else pd.NaT,
                "source_timestamp_last_utc": pd.Timestamp(source_end_timestamps[-1], tz="UTC") if source_end_timestamps else pd.NaT,
                "source_timestamp_lineage": _json_dumps(
                    {
                        "from_raw": source_raw_timestamps,
                        "to_raw": source_raw_end_timestamps,
                        "from_utc": source_timestamps,
                        "to_utc": source_end_timestamps,
                        "source_timezone": MELBOURNE_TIMEZONE,
                        "z_suffix_interpreted_as": "wall_time",
                    }
                ),
                "source_timestamp_count": len(source_timestamps),
                "source_timestamp_semantics": "melbourne_wall_time_despite_z",
                "source_archive_member": "|".join(sorted(state["source_members"])),
                "source_date_local": None,
                "source_row_count": state["source_row_count"],
                "source_record_count": state["source_row_count"],
                "count_location_id": state["count_location_id"],
                "countline_name": state["countline_name"],
                "channel_type": "|".join(sorted(state["channel_types"])),
                "traffic_eligible": True,
                "review_status": "approved",
                "scats_site": None,
                "latitude": selected_coord[0] if selected_coord else None,
                "longitude": selected_coord[1] if selected_coord else None,
                "coordinate_valid": selected_coord is not None,
                "coordinate_missing": selected_coord is None,
                "coordinate_drift_flag": len(coordinates) > 1,
                "vehicle_count": vehicle_count,
                "intersection_total": vehicle_count,
                "label_quality": "derived_zero" if derived_zero else "observed",
                "quality_partial_flag": False,
                "quality_alarm_flag": False,
                "quality_missing_interval_count": 0,
                "measurement_scope": "countline",
                "label_source": "transport_activity",
                "ta_motor_class_rows": state["motor_class_rows"],
                "ta_non_motor_class_rows": state["non_motor_class_rows"],
                "ta_reported_class_rows": state["reported_class_rows"],
                "ta_derived_zero": derived_zero,
                "ta_dst_ambiguous_flag": state["dst_ambiguous_rows"] > 0,
                "ta_dst_fallback_wrap_flag": state["dst_fallback_wrap_rows"] > 0,
            }
        )
    return pd.DataFrame(rows)


def process_transport_archive(
    archive_info: ArchiveInfo,
    registry: pd.DataFrame | RegistryLookup,
    chunk_size: int = 250_000,
    *,
    stage_path: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Stream and aggregate one Transport Activity ZIP."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    lookup = _registry_lookup(registry)
    metrics: dict[str, Any] = {
        "kind": "transport_activity",
        "path": str(archive_info.path),
        "source_dataset_id": archive_info.source_dataset_id,
        "input_rows": 0,
        "selected_rows": 0,
        "excluded_ineligible_rows": 0,
        "excluded_unapproved_rows": 0,
        "missing_registry_rows": 0,
        "members": [],
        "dates": set(),
        "min_hour": None,
        "max_hour": None,
        "dst_ambiguous_rows": 0,
        "dst_fallback_wrap_rows": 0,
    }
    try:
        archive = zipfile.ZipFile(archive_info.path)
    except zipfile.BadZipFile as error:
        raise ValueError(f"invalid Transport Activity ZIP: {archive_info.path}") from error
    member_frames: list[pd.DataFrame] = []
    stage_writer = _ParquetStageWriter(stage_path) if stage_path is not None else None
    try:
        with archive:
            members = _data_members(archive)
            if not members:
                raise ValueError(f"Transport Activity ZIP has no CSV members: {archive_info.path}")
            for member in sorted(members):
                header = set(_member_header(archive, member))
                require_columns(header, TA_REQUIRED_COLUMNS, f"Transport Activity member {member}")
                metrics["members"].append(member)
                # Publisher members are quarter-sized snapshots.  Keep state at
                # member scope so a multi-year archive cannot grow an unbounded
                # countline-hour dictionary.
                states: dict[tuple[str, str, pd.Timestamp], dict[str, Any]] = {}
                with archive.open(member) as handle:
                    reader = pd.read_csv(handle, chunksize=chunk_size, dtype={"countLocationId": "string", "countlineName": "string", "class": "string"})
                    for chunk in reader:
                        metrics["input_rows"] += len(chunk)
                        require_columns(chunk.columns, TA_REQUIRED_COLUMNS, f"Transport Activity member {member}")
                        chunk = chunk.copy()
                        chunk["_count_location_id"] = _canonical_id_series(chunk["countLocationId"], f"{member} countLocationId")
                        chunk["_countline_name"] = chunk["countlineName"].astype("string").fillna("").str.strip()
                        chunk["_class"] = chunk["class"].astype("string").fillna("").str.strip().str.lower()
                        if chunk["_class"].eq("").any():
                            raise ValueError(f"{member} contains blank class labels")
                        unknown_classes = ~chunk["_class"].isin(KNOWN_CLASSES)
                        if unknown_classes.any():
                            sample = chunk.loc[unknown_classes, "class"].head(5).tolist()
                            raise ValueError(f"{member} contains unknown Transport Activity classes: {sample}")
                        years = _strict_numeric(chunk["year"], f"{member} year", integer=True)
                        quarters = _strict_numeric(chunk["quarter"], f"{member} quarter", integer=True)
                        if years.isna().any() or quarters.isna().any() or (~quarters.between(1, 4)).any():
                            raise ValueError(f"{member} contains invalid year/quarter values")
                        chunk["_registry_key"] = chunk["_count_location_id"] + "\x1f" + chunk["_countline_name"]
                        missing_registry = ~chunk["_registry_key"].isin(lookup)
                        if missing_registry.any():
                            metrics["missing_registry_rows"] += int(missing_registry.sum())
                            sample = chunk.loc[missing_registry, ["countLocationId", "countlineName"]].head(3).to_dict("records")
                            raise ValueError(f"Transport Activity row has no reviewed registry label: {sample}")
                        metadata = chunk["_registry_key"].map(lookup)
                        chunk["traffic_eligible"] = metadata.map(lambda item: item["traffic_eligible"]).astype("boolean")
                        chunk["review_status"] = metadata.map(lambda item: item["review_status"]).astype("string")
                        chunk["channel_type"] = metadata.map(lambda item: item["channel_type"]).astype("string")
                        approved = chunk["traffic_eligible"] & chunk["review_status"].eq("approved")
                        metrics["excluded_ineligible_rows"] += int((~chunk["traffic_eligible"]).sum())
                        unapproved_traffic = chunk["traffic_eligible"] & ~chunk["review_status"].eq("approved")
                        if unapproved_traffic.any():
                            metrics["excluded_unapproved_rows"] += int(unapproved_traffic.sum())
                            sample = chunk.loc[unapproved_traffic, ["countLocationId", "countlineName", "review_status"]].head(3).to_dict("records")
                            raise ValueError(f"unapproved Transport Activity traffic label row: {sample}")
                        if not approved.any():
                            continue
                        selected = chunk.loc[approved].copy()
                        blank_approved_name = selected["_countline_name"].eq("")
                        if blank_approved_name.any():
                            sample = selected.loc[blank_approved_name, "countLocationId"].head(3).tolist()
                            raise ValueError(f"{member} contains an approved row with blank countlineName: {sample}")
                        from_info = _parse_ta_wall_z(selected["from"], f"{member} from")
                        to_info = _parse_ta_wall_z(selected["to"], f"{member} to")
                        for prefix, info in (("from", from_info), ("to", to_info)):
                            selected[f"_{prefix}_wall"] = pd.to_datetime(info["wall"], errors="coerce")
                            selected[f"_{prefix}_utc"] = pd.to_datetime(info["utc"], errors="coerce", utc=True)
                            selected[f"_{prefix}_offset_minutes"] = pd.to_numeric(info["offset_minutes"], errors="coerce").astype("Int64")
                            selected[f"_{prefix}_ambiguous"] = info["ambiguous"].astype("boolean")
                        local_reverse = selected["_to_wall"] < selected["_from_wall"]
                        same_wall_date = selected["_to_wall"].dt.date == selected["_from_wall"].dt.date
                        fallback_wrap = (
                            local_reverse
                            & same_wall_date
                            & selected["_from_ambiguous"]
                            & selected["_to_ambiguous"]
                        )
                        selected["_dst_fallback_wrap"] = fallback_wrap.astype("boolean")
                        invalid_wall_reverse = local_reverse & ~fallback_wrap
                        invalid_utc_reverse = selected["_to_utc"] < selected["_from_utc"]
                        if (invalid_wall_reverse | (invalid_utc_reverse & ~fallback_wrap)).any():
                            raise ValueError(f"{member} contains a non-fallback timestamp interval ending before it starts")
                        _validate_ta_interval_contract(selected, member)
                        selected["_hour_start_utc"] = selected["_from_utc"].dt.floor("h")
                        selected["_count_value"] = _strict_numeric(selected["count"], f"{member} count", integer=True)
                        if selected["_count_value"].isna().any():
                            raise ValueError(f"{member} contains null counts in reviewed traffic rows")
                        if (selected["_count_value"] < 0).any():
                            raise ValueError(f"{member} contains negative counts in reviewed traffic rows")
                        selected["_count_value"] = selected["_count_value"].astype("int64")
                        selected["_is_motor"] = selected["_class"].isin(MOTOR_CLASSES)
                        selected["_latitude"] = pd.to_numeric(selected["CountLocationLat"], errors="coerce")
                        selected["_longitude"] = pd.to_numeric(selected["CountLocationLong"], errors="coerce")
                        metrics["selected_rows"] += len(selected)
                        metrics["dst_ambiguous_rows"] += int(selected["_from_ambiguous"].sum()) + int(selected["_to_ambiguous"].sum())
                        metrics["dst_fallback_wrap_rows"] += int(selected["_dst_fallback_wrap"].sum())
                        metrics["dates"].update(selected["_hour_start_utc"].dt.date.tolist())
                        low, high = selected["_hour_start_utc"].min(), selected["_hour_start_utc"].max()
                        metrics["min_hour"] = low if metrics["min_hour"] is None else min(metrics["min_hour"], low)
                        metrics["max_hour"] = high if metrics["max_hour"] is None else max(metrics["max_hour"], high)
                        grouped = selected.groupby(["_count_location_id", "_countline_name", "_hour_start_utc"], sort=False, dropna=False)
                        for (site_id, name, hour), group in grouped:
                            key = (str(site_id), str(name), pd.Timestamp(hour))
                            state = states.get(key)
                            if state is None:
                                state = _new_ta_state(archive_info.source_dataset_id, str(site_id), str(name), pd.Timestamp(hour))
                                states[key] = state
                            _update_ta_state(state, group, member)
                member_frame = _finish_ta_states(states)
                if not member_frame.empty:
                    member_frame["source_timestamp_utc"] = member_frame["source_timestamp_first_utc"]
                    if stage_writer is not None:
                        stage_writer.write(_finish_common_frame(member_frame))
                    else:
                        member_frames.append(member_frame)
        if stage_writer is not None:
            stage_writer.close()
            metrics["staged_rows"] = stage_writer.rows
            frame = pd.DataFrame()
        else:
            frame = pd.concat(member_frames, ignore_index=True) if member_frames else pd.DataFrame()
    except Exception:
        if stage_writer is not None:
            stage_writer.abort()
        raise
    if not frame.empty:
        frame["source_timestamp_utc"] = frame["source_timestamp_first_utc"]
    return frame, metrics


def _new_scats_state(source_id: str, site: str, local_date: date, local_hour: int) -> dict[str, Any]:
    return {
        "source_dataset_id": source_id,
        "site": site,
        "source_date_local": local_date,
        "local_hour": local_hour,
        "vehicle_count": None,
        "missing_interval_count": 0,
        "observed_interval_count": 0,
        "expected_interval_count": 0,
        "detectors": set(),
        "detector_row_count": 0,
        "records": [],
        "volumes": [],
        "alarm_count": 0,
        "alarm_unknown": False,
        "source_members": set(),
        "source_rows": 0,
        "duplicate_detector_rows": 0,
    }


def _update_scats_state(state: dict[str, Any], row: Mapping[str, Any], member: str) -> None:
    piece = row["vehicle_piece"]
    if pd.notna(piece):
        state["vehicle_count"] = int(piece) if state["vehicle_count"] is None else state["vehicle_count"] + int(piece)
    state["missing_interval_count"] += int(row["missing_intervals"])
    state["observed_interval_count"] += int(row["observed_intervals"])
    state["expected_interval_count"] += int(row["expected_intervals"])
    state["detectors"].add(str(row["detector"]))
    state["detector_row_count"] += 1
    if pd.notna(row["records"]):
        state["records"].append(int(row["records"]))
    if pd.notna(row["volume"]):
        state["volumes"].append(float(row["volume"]))
    if pd.isna(row["alarm"]):
        state["alarm_unknown"] = True
    elif bool(row["alarm"]):
        state["alarm_count"] += 1
    state["source_members"].add(member)
    state["source_rows"] += 1


def _fixed_scats_hour_timestamp(local_date: date, local_hour: int) -> pd.Timestamp:
    # The SCATS dictionary specifies UTC+10, not Australia/Melbourne.  This
    # intentionally does not apply daylight saving to source interpretation.
    local = datetime.combine(local_date, datetime.min.time()) + timedelta(hours=int(local_hour))
    return pd.Timestamp(local, tz="Etc/GMT-10").tz_convert("UTC")


def _finish_scats_states(
    states: Mapping[tuple[str, date, int], dict[str, Any]],
    metrics: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states.values():
        hour_start = _fixed_scats_hour_timestamp(state["source_date_local"], state["local_hour"])
        source_first = hour_start
        # The explicit unit form avoids pandas/NumPy's deprecated generic
        # timedelta constructor while preserving the last SCATS interval time.
        source_last = hour_start + pd.Timedelta(45, unit="min")
        partial = (
            state["missing_interval_count"] > 0
            or state["expected_interval_count"] == 0
            or any(value != 96 for value in state["records"])
        )
        if state["vehicle_count"] is None:
            # A site-hour where every detector interval is unavailable cannot
            # form a target.  Omit it rather than impute zero, and retain an
            # explicit manifest metric for the omission.
            if metrics is not None:
                metrics["omitted_null_target_site_hours"] = metrics.get("omitted_null_target_site_hours", 0) + 1
            continue
        vehicle_count = int(state["vehicle_count"])
        rows.append(
            {
                "source_dataset_id": state["source_dataset_id"],
                "observation_unit_id": f"scats:{state['site']}",
                "observation_id": f"scats:{state['site']}",
                "hour_start_utc": hour_start,
                "source_timezone_name": SCATS_SOURCE_TIMEZONE,
                "source_timezone_offset_minutes": SCATS_SOURCE_OFFSET_MINUTES,
                "source_timestamp_utc": source_first,
                "source_timestamp_first_utc": source_first,
                "source_timestamp_last_utc": source_last,
                "source_timestamp_lineage": _json_dumps(
                    {
                        "source_date_local": state["source_date_local"].isoformat(),
                        "local_hour": state["local_hour"],
                        "interval_indices": list(range(state["local_hour"] * 4, state["local_hour"] * 4 + 4)),
                    }
                ),
                "source_timestamp_count": 4,
                "source_timestamp_semantics": "scats_fixed_utc_plus_10",
                "source_archive_member": "|".join(sorted(state["source_members"])),
                "source_date_local": state["source_date_local"],
                "source_row_count": state["source_rows"],
                "source_record_count": state["source_rows"],
                "count_location_id": None,
                "countline_name": None,
                "channel_type": None,
                "traffic_eligible": None,
                "review_status": None,
                "scats_site": state["site"],
                "latitude": None,
                "longitude": None,
                "coordinate_valid": None,
                "coordinate_missing": True,
                "coordinate_drift_flag": False,
                "vehicle_count": vehicle_count,
                "intersection_total": vehicle_count,
                "label_quality": "partial" if partial else "observed",
                "quality_partial_flag": partial,
                "quality_alarm_flag": True if state["alarm_count"] > 0 else (None if state["alarm_unknown"] else False),
                "quality_missing_interval_count": state["missing_interval_count"],
                "measurement_scope": "intersection",
                "label_source": "scats",
                "scats_detector_count": len(state["detectors"]),
                "scats_detector_row_count": state["detector_row_count"],
                "scats_ct_records_min": min(state["records"]) if state["records"] else None,
                "scats_ct_records_max": max(state["records"]) if state["records"] else None,
                "scats_qt_volume_24hour_sum": sum(state["volumes"]) if state["volumes"] else None,
                "scats_alarm_24hour_count": state["alarm_count"],
                "scats_source_date_local": state["source_date_local"],
            }
        )
    return pd.DataFrame(rows)


def _normalise_scats_member(
    chunk: pd.DataFrame,
    member: str,
    archive_info: ArchiveInfo,
    seen_detector_keys: set[tuple[str, date, str]],
    states: dict[tuple[str, date, int], dict[str, Any]],
    metrics: dict[str, Any],
    eligible_site_ids: set[str] | None = None,
) -> tuple[int, set[date]]:
    require_columns(chunk.columns, SCATS_REQUIRED_COLUMNS, f"SCATS member {member}")
    chunk = chunk.copy()
    chunk["_site"] = _canonical_id_series(chunk["NB_SCATS_SITE"], f"{member} NB_SCATS_SITE")
    raw_dates = chunk["QT_INTERVAL_COUNT"].astype("string").str.strip()
    if raw_dates.isna().any() or raw_dates.eq("").any():
        raise ValueError(f"SCATS member {member} contains blank QT_INTERVAL_COUNT dates")
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce")
    if parsed_dates.isna().any():
        sample = raw_dates.loc[parsed_dates.isna()].head(3).tolist()
        raise ValueError(f"SCATS member {member} contains invalid QT_INTERVAL_COUNT dates: {sample}")
    chunk["_date"] = parsed_dates.dt.date
    observed_dates = set(chunk["_date"].tolist())

    # Every interval value is evidence, including rows that are later removed
    # by the configured spatial filter.  Negative values are invalid/missing
    # evidence: -1 is the standard publisher sentinel, while values below -1
    # are retained in the audit map as nonstandard negative sentinels.  None
    # of them can contribute to a vehicle total, and zero remains observed.
    interval_values: dict[str, pd.Series] = {}
    for column in SCATS_INTERVAL_COLUMNS:
        values = _strict_numeric(chunk[column], f"{member} {column}", integer=True)
        negative = values.notna() & values.lt(0)
        if negative.any():
            negative_values = values.loc[negative]
            metrics["scats_negative_interval_count"] += int(negative.sum())
            metrics["scats_standard_minus_one_count"] += int(negative_values.eq(-1).sum())
            metrics["scats_nonstandard_negative_count"] += int((negative_values < -1).sum())
            frequency = metrics["scats_negative_value_frequency"]
            for value, count in negative_values.value_counts(sort=False).items():
                frequency[str(int(value))] = frequency.get(str(int(value)), 0) + int(count)
        interval_values[column] = values.mask(negative)

    # Filter after the interval audit, but before detector-state expansion. In
    # particular, do not materialize 24 hourly projections for statewide sites
    # that cannot enter the configured bbox.
    if eligible_site_ids is not None:
        chunk = chunk.loc[chunk["_site"].isin(eligible_site_ids)].copy()
        if chunk.empty:
            return 0, observed_dates
        interval_values = {
            column: values.loc[chunk.index]
            for column, values in interval_values.items()
        }
    chunk["_detector"] = _canonical_id_series(chunk["NB_DETECTOR"], f"{member} NB_DETECTOR")
    duplicate_keys = list(zip(chunk["_site"], chunk["_date"], chunk["_detector"]))
    duplicate_mask = pd.Series(duplicate_keys).duplicated(keep=False).to_numpy()
    if duplicate_mask.any() or any(key in seen_detector_keys for key in duplicate_keys):
        raise ValueError(f"SCATS duplicate site/date/detector rows detected in or across members: {member}")
    seen_detector_keys.update(duplicate_keys)
    chunk["_records"] = _strict_numeric(chunk["CT_RECORDS"], f"{member} CT_RECORDS", integer=True)
    chunk["_volume"] = _strict_numeric(chunk["QT_VOLUME_24HOUR"], f"{member} QT_VOLUME_24HOUR")
    chunk["_alarm"] = _strict_numeric(chunk["CT_ALARM_24HOUR"], f"{member} CT_ALARM_24HOUR")
    for local_hour in range(24):
        columns = list(SCATS_INTERVAL_COLUMNS[local_hour * 4 : local_hour * 4 + 4])
        values = pd.concat([interval_values[column] for column in columns], axis=1)
        values.columns = columns
        pieces = values.sum(axis=1, min_count=1)
        missing = values.isna().sum(axis=1)
        observed = values.notna().sum(axis=1)
        work = pd.DataFrame(
            {
                "site": chunk["_site"],
                "date": chunk["_date"],
                "detector": chunk["_detector"],
                "vehicle_piece": pieces,
                "missing_intervals": missing,
                "observed_intervals": observed,
                "expected_intervals": 4,
                "records": chunk["_records"],
                "volume": chunk["_volume"],
                "alarm": chunk["_alarm"].gt(0),
            }
        )
        grouped = work.groupby(["site", "date"], sort=False, dropna=False)
        for (site, day), group in grouped:
            key = (str(site), day, local_hour)
            state = states.get(key)
            if state is None:
                state = _new_scats_state(archive_info.source_dataset_id, str(site), day, local_hour)
                states[key] = state
            for row in group.to_dict("records"):
                _update_scats_state(state, row, member)
    # Return raw detector rows, not the 24 hourly projections emitted from
    # each detector row.  The latter would inflate source coverage metrics.
    return len(chunk), observed_dates


def process_scats_archive(
    archive_info: ArchiveInfo,
    chunk_size: int = 100_000,
    *,
    eligible_site_ids: set[str] | None = None,
    coordinates: Mapping[str, Mapping[str, Any]] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    include_missing_coordinates: bool = False,
    stage_path: Path | None = None,
    nested_member: str | None = None,
    top_level_members: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Stream and aggregate one SCATS logical unit.

    When no member selector is supplied the function retains archive-wide
    behavior for direct callers.  Shared-pool workers pass either one nested
    monthly ZIP member or an explicit top-level CSV member list.
    """

    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    metrics: dict[str, Any] = {
        "kind": "scats",
        "path": str(archive_info.path),
        "source_dataset_id": archive_info.source_dataset_id,
        "input_rows": 0,
        "selected_rows": 0,
        "raw_rows": 0,
        "bbox_excluded_rows": 0,
        "bbox_filter_applied": eligible_site_ids is not None,
        "bbox_eligible_site_count": len(eligible_site_ids) if eligible_site_ids is not None else None,
        "raw_site_ids": set(),
        "selected_site_ids": set(),
        "members": [],
        "dates": set(),
        "min_date": None,
        "max_date": None,
        "omitted_null_target_site_hours": 0,
        "scats_negative_interval_count": 0,
        "scats_standard_minus_one_count": 0,
        "scats_nonstandard_negative_count": 0,
        "scats_negative_value_frequency": {},
        "work_unit_nested_member": nested_member,
    }
    try:
        archive = zipfile.ZipFile(archive_info.path)
    except zipfile.BadZipFile as error:
        raise ValueError(f"invalid SCATS ZIP: {archive_info.path}") from error
    member_frames: list[pd.DataFrame] = []
    stage_writer = _ParquetStageWriter(stage_path) if stage_path is not None else None
    coordinate_metrics: Counter[str] = Counter()
    found_member = False
    try:
        with archive:
            for member, handle in _logical_csv_members(
                archive,
                nested_member=nested_member,
                top_level_members=top_level_members,
            ):
                found_member = True
                header = set(_header_from_handle(handle, member=member))
                require_columns(header, SCATS_REQUIRED_COLUMNS, f"SCATS member {member}")
                metrics["members"].append(member)
                # SCATS publisher ZIPs are normally one daily CSV per member;
                # annual files may wrap those daily CSVs in monthly ZIPs.  In
                # both cases state is bounded to one logical member.
                states: dict[tuple[str, date, int], dict[str, Any]] = {}
                seen_detector_keys: set[tuple[str, date, str]] = set()
                reader = pd.read_csv(handle, chunksize=chunk_size, dtype={"NB_SCATS_SITE": "string", "NB_DETECTOR": "string", "QT_INTERVAL_COUNT": "string"})
                for chunk in reader:
                    raw_rows = len(chunk)
                    metrics["input_rows"] += raw_rows
                    metrics["raw_rows"] += raw_rows
                    raw_sites = chunk["NB_SCATS_SITE"].map(_canonical_id).dropna().astype(str)
                    metrics["raw_site_ids"].update(raw_sites.tolist())
                    rows, dates = _normalise_scats_member(
                        chunk,
                        member,
                        archive_info,
                        seen_detector_keys,
                        states,
                        metrics,
                        eligible_site_ids,
                    )
                    metrics["selected_rows"] += rows
                    metrics["bbox_excluded_rows"] += raw_rows - rows if eligible_site_ids is not None else 0
                    metrics["dates"].update(dates)
                    if eligible_site_ids is not None:
                        metrics["selected_site_ids"].update(
                            set(raw_sites).intersection(eligible_site_ids)
                        )
                    else:
                        metrics["selected_site_ids"].update(raw_sites.tolist())
                member_frame = _finish_scats_states(states, metrics)
                if not member_frame.empty and coordinates is not None:
                    if eligible_site_ids is not None:
                        member_frame, current_coordinates = apply_scats_coordinates(
                            member_frame,
                            coordinates,
                            bbox=None,
                        )
                    else:
                        member_frame, current_coordinates = apply_scats_coordinates(
                            member_frame,
                            coordinates,
                            bbox=bbox,
                            include_missing_coordinates=include_missing_coordinates,
                        )
                    for key, value in current_coordinates.items():
                        coordinate_metrics[key] += int(value)
                if not member_frame.empty:
                    if stage_writer is not None:
                        stage_writer.write(_finish_common_frame(member_frame))
                    else:
                        member_frames.append(member_frame)
        if not found_member:
            raise ValueError(f"SCATS ZIP has no CSV members: {archive_info.path}")
        if stage_writer is not None:
            stage_writer.close()
            metrics["staged_rows"] = stage_writer.rows
            frame = pd.DataFrame()
        else:
            frame = pd.concat(member_frames, ignore_index=True) if member_frames else pd.DataFrame()
    except Exception:
        if stage_writer is not None:
            stage_writer.abort()
        raise
    if metrics["dates"]:
        metrics["min_date"] = min(metrics["dates"])
        metrics["max_date"] = max(metrics["dates"])
    metrics["coordinate_metrics"] = dict(coordinate_metrics)
    metrics["bbox_output_rows"] = int(coordinate_metrics.get("rows_after_bbox", metrics.get("staged_rows", len(frame))))
    return frame, metrics


def _stage_result(
    index: int,
    unit: LogicalWorkUnit,
    stage_path: Path,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the small picklable result exchanged between worker and parent."""

    resolved_metrics = dict(metrics)
    resolved_metrics["work_unit_label"] = unit.label
    resolved_metrics["work_unit_nested_member"] = unit.nested_member
    return {
        "index": int(index),
        "kind": unit.archive_info.kind,
        "source_dataset_id": unit.archive_info.source_dataset_id,
        "work_unit_label": unit.label,
        "nested_member": unit.nested_member,
        "path": str(stage_path),
        "metrics": resolved_metrics,
    }


def _stage_work_unit_worker(task: StageTask) -> dict[str, Any]:
    """Spawn-safe dispatcher for the shared TA/SCATS pool."""

    archive_info = task.unit.archive_info
    if archive_info.kind == "transport_activity":
        if task.registry is None:
            raise ValueError(f"missing Transport Activity registry for {task.unit.label}")
        _frame, metrics = process_transport_archive(
            archive_info,
            task.registry,
            task.chunk_size,
            stage_path=task.stage_path,
        )
    elif archive_info.kind == "scats":
        _frame, metrics = process_scats_archive(
            archive_info,
            task.chunk_size,
            eligible_site_ids=task.eligible_site_ids,
            coordinates=task.coordinates,
            bbox=task.bbox,
            include_missing_coordinates=task.include_missing_coordinates,
            stage_path=task.stage_path,
            nested_member=task.unit.nested_member,
            top_level_members=task.unit.top_level_members or None,
        )
    else:
        raise ValueError(f"unsupported traffic staging work-unit kind: {archive_info.kind}")
    return _stage_result(task.index, task.unit, task.stage_path, metrics)


def _stage_transport_archive_worker(
    task: tuple[int, ArchiveInfo, RegistryLookup, int, Path],
) -> dict[str, Any]:
    """Backward-compatible entry point for one TA archive."""

    index, archive_info, registry, chunk_size, stage_path = task
    return _stage_work_unit_worker(
        StageTask(
            index=index,
            unit=LogicalWorkUnit(archive_info),
            registry=registry,
            chunk_size=chunk_size,
            stage_path=stage_path,
        )
    )


def _stage_scats_archive_worker(
    task: tuple[
        int,
        ArchiveInfo,
        int,
        set[str] | None,
        Mapping[str, Mapping[str, Any]],
        tuple[float, float, float, float] | None,
        bool,
        Path,
    ],
) -> dict[str, Any]:
    """Backward-compatible entry point for one SCATS archive."""

    (
        index,
        archive_info,
        chunk_size,
        eligible_site_ids,
        coordinates,
        bbox,
        include_missing_coordinates,
        stage_path,
    ) = task
    return _stage_work_unit_worker(
        StageTask(
            index=index,
            unit=LogicalWorkUnit(archive_info),
            chunk_size=chunk_size,
            stage_path=stage_path,
            eligible_site_ids=eligible_site_ids,
            coordinates=coordinates,
            bbox=bbox,
            include_missing_coordinates=include_missing_coordinates,
        )
    )


def _stage_task_label(task: Any) -> str:
    if isinstance(task, StageTask):
        return task.unit.label
    if isinstance(task, LogicalWorkUnit):
        return task.label
    if isinstance(task, Mapping):
        return str(task.get("work_unit_label", task.get("source_dataset_id", "unknown-work-unit")))
    try:
        candidate = task[1]
        if isinstance(candidate, LogicalWorkUnit):
            return candidate.label
        if isinstance(candidate, ArchiveInfo):
            return candidate.source_dataset_id
        return str(candidate)
    except (IndexError, AttributeError, TypeError):
        return "unknown-work-unit"


def _report_stage_completion(result: Mapping[str, Any]) -> None:
    metrics = result.get("metrics", {})
    rows = metrics.get("staged_rows", 0)
    label = str(result.get("work_unit_label", result.get("source_dataset_id", "unknown-work-unit")))
    print(
        f"staged {result.get('kind', 'archive')} "
        f"{label}: {int(rows):,} rows",
        flush=True,
    )


def _remove_stage_paths(tasks: Sequence[Any]) -> None:
    for task in tasks:
        path = getattr(task, "stage_path", None)
        if path is None:
            continue
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def _run_stage_tasks(
    tasks: Sequence[Any],
    *,
    requested_workers: int,
    worker_entry: Callable[[Any], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int, str]:
    """Stage all TA archives and SCATS logical units through one shared pool."""

    if requested_workers < 1:
        raise ValueError("--workers must be at least 1")
    if not tasks:
        return [], 0, "none"

    worker = worker_entry or _stage_work_unit_worker
    effective_workers = min(int(requested_workers), len(tasks))
    strategy = "serial" if effective_workers == 1 else "shared_spawn_process_pool"
    ordered: list[dict[str, Any] | None] = [None] * len(tasks)

    def consume(result: Mapping[str, Any]) -> None:
        try:
            index = int(result["index"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("archive worker returned an invalid stage index") from error
        if index < 0 or index >= len(tasks) or ordered[index] is not None:
            raise ValueError(f"archive worker returned duplicate or out-of-range stage index: {index}")
        ordered[index] = dict(result)
        _report_stage_completion(result)

    if effective_workers == 1:
        try:
            for task in tasks:
                consume(worker(task))
        except Exception as error:
            _remove_stage_paths(tasks)
            raise ValueError(
                f"staging failed for {_stage_task_label(task)}: {error}"
            ) from error
    else:
        context = mp.get_context("spawn")
        executor = ProcessPoolExecutor(
            max_workers=effective_workers,
            mp_context=context,
        )
        futures: dict[Future[dict[str, Any]], Any] = {}
        failed = False
        try:
            for task in tasks:
                futures[executor.submit(worker, task)] = task
            try:
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        consume(future.result())
                    except Exception as error:
                        failed = True
                        for pending in futures:
                            pending.cancel()
                        raise ValueError(
                            f"staging failed for {_stage_task_label(task)}: {error}"
                        ) from error
            except BaseException:
                failed = True
                for pending in futures:
                    pending.cancel()
                raise
        except BaseException:
            # Submission can fail before the completion loop starts (for
            # example, if a task is not spawn-picklable).  Treat that exactly
            # like a worker failure so pending work is cancelled and staging
            # is removed after the pool has stopped.
            failed = True
            for pending in futures:
                pending.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=failed)
            if failed:
                _remove_stage_paths(tasks)

    if any(result is None for result in ordered):
        _remove_stage_paths(tasks)
        raise ValueError("archive staging completed without one result per archive")
    return [result for result in ordered if result is not None], effective_workers, strategy


def _run_archive_stage_tasks(
    tasks: Sequence[Any],
    worker_entry: Callable[[Any], dict[str, Any]],
    *,
    requested_workers: int,
) -> tuple[list[dict[str, Any]], int, str]:
    """Compatibility wrapper for callers that still stage one task family."""

    return _run_stage_tasks(
        tasks,
        requested_workers=requested_workers,
        worker_entry=worker_entry,
    )


def _aggregate_source_metrics(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce unit metrics to one deterministic metric per source archive."""

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[(str(result.get("kind", "")), str(result["source_dataset_id"]))].append(result)

    numeric_sum_keys = SCATS_METRIC_SUM_KEYS | frozenset(
        {
            "excluded_ineligible_rows",
            "excluded_unapproved_rows",
            "missing_registry_rows",
            "dst_ambiguous_rows",
            "dst_fallback_wrap_rows",
            "bbox_output_rows",
        }
    )
    date_set_keys = {"dates"}
    value_set_keys = {"raw_site_ids", "selected_site_ids"}
    list_union_keys = {"members"}
    min_keys = {"min_hour", "min_date"}
    max_keys = {"max_hour", "max_date"}
    aggregated: list[dict[str, Any]] = []
    for (kind, source_dataset_id), source_results in sorted(grouped.items()):
        ordered_results = sorted(
            source_results,
            key=lambda item: (
                int(item.get("index", 0)),
                str(item.get("work_unit_label", "")),
                str(item.get("path", "")),
            ),
        )
        first_metrics = dict(ordered_results[0].get("metrics", {}))
        merged: dict[str, Any] = {
            key: value
            for key, value in first_metrics.items()
            if key not in numeric_sum_keys
            and key not in date_set_keys
            and key not in value_set_keys
            and key not in list_union_keys
            and key not in min_keys
            and key not in max_keys
            and key not in {
                "coordinate_metrics",
                "scats_negative_value_frequency",
                "work_unit_label",
                "work_unit_nested_member",
            }
        }
        merged.update(
            {
                "kind": kind,
                "source_dataset_id": source_dataset_id,
                "input_rows": 0,
                "selected_rows": 0,
                "members": set(),
                "dates": set(),
                "work_unit_count": len(ordered_results),
                "work_unit_labels": set(),
                "nested_members": set(),
                "scats_negative_value_frequency": {},
            }
        )
        for key in numeric_sum_keys:
            merged[key] = 0
        for key in date_set_keys | value_set_keys:
            merged[key] = set()
        merged["coordinate_metrics"] = Counter()
        min_values: dict[str, Any] = {key: None for key in min_keys}
        max_values: dict[str, Any] = {key: None for key in max_keys}
        negative_frequency: Counter[str] = Counter()
        for result in ordered_results:
            metrics = result.get("metrics", {})
            merged["work_unit_labels"].add(str(result.get("work_unit_label", "")))
            nested_member = result.get("nested_member")
            if nested_member is not None:
                merged["nested_members"].add(str(nested_member))
            for key in numeric_sum_keys:
                value = metrics.get(key)
                if value is not None:
                    merged[key] += int(value)
            for key in date_set_keys | value_set_keys:
                merged[key].update(metrics.get(key, set()))
            merged["members"].update(metrics.get("members", []))
            for key in min_keys:
                value = metrics.get(key)
                if value is not None and (min_values[key] is None or value < min_values[key]):
                    min_values[key] = value
            for key in max_keys:
                value = metrics.get(key)
                if value is not None and (max_values[key] is None or value > max_values[key]):
                    max_values[key] = value
            for key, value in metrics.get("coordinate_metrics", {}).items():
                merged["coordinate_metrics"][str(key)] += int(value)
            for key, value in metrics.get("scats_negative_value_frequency", {}).items():
                negative_frequency[str(key)] += int(value)
        merged.update(min_values)
        merged.update(max_values)
        merged["members"] = sorted(merged["members"])
        merged["dates"] = set(merged["dates"])
        merged["raw_site_ids"] = set(merged.get("raw_site_ids", set()))
        merged["selected_site_ids"] = set(merged.get("selected_site_ids", set()))
        merged["work_unit_labels"] = sorted(merged["work_unit_labels"])
        merged["nested_members"] = sorted(merged["nested_members"])
        merged["scats_negative_value_frequency"] = dict(sorted(negative_frequency.items()))
        merged["coordinate_metrics"] = dict(sorted(merged["coordinate_metrics"].items()))
        aggregated.append(merged)
    return aggregated


def apply_scats_coordinates(
    frame: pd.DataFrame,
    coordinates: Mapping[str, Mapping[str, Any]],
    bbox: tuple[float, float, float, float] | None = CITY_BBOX,
    *,
    include_missing_coordinates: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join stable signal coordinates and apply the City demo bounding box."""

    metrics = {"rows_before_bbox": len(frame), "missing_coordinate_rows": 0, "outside_bbox_rows": 0, "rows_after_bbox": 0}
    if frame.empty:
        return frame, metrics
    frame = frame.copy()
    frame["latitude"] = frame["scats_site"].map(lambda site: coordinates.get(str(site), {}).get("latitude"))
    frame["longitude"] = frame["scats_site"].map(lambda site: coordinates.get(str(site), {}).get("longitude"))
    frame["coordinate_valid"] = frame["latitude"].notna() & frame["longitude"].notna()
    frame["coordinate_missing"] = frame["latitude"].isna() | frame["longitude"].isna()
    frame["physical_site_id"] = [physical_site_id(lat, lon) for lat, lon in zip(frame["latitude"], frame["longitude"])]
    metrics["missing_coordinate_rows"] = int(frame["coordinate_missing"].sum())
    if bbox is None:
        selected = pd.Series(True, index=frame.index)
    else:
        min_lon, min_lat, max_lon, max_lat = bbox
        selected = (
            frame["latitude"].between(min_lat, max_lat, inclusive="both")
            & frame["longitude"].between(min_lon, max_lon, inclusive="both")
        )
        if include_missing_coordinates:
            selected = selected | frame["coordinate_missing"]
    metrics["outside_bbox_rows"] = int((~selected).sum())
    result = frame.loc[selected].copy()
    metrics["rows_after_bbox"] = len(result)
    return result, metrics


def _add_temporal_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["hour_start_utc"] = pd.to_datetime(frame["hour_start_utc"], errors="coerce", utc=True)
    if frame["hour_start_utc"].isna().any():
        raise ValueError("canonical target contains invalid hour_start_utc timestamps")
    local = frame["hour_start_utc"].dt.tz_convert(MELBOURNE_TIMEZONE)
    frame["local_timestamp"] = local
    frame["local_date"] = local.dt.date
    frame["local_hour"] = local.dt.hour.astype("int8")
    frame["year"] = local.dt.year.astype("int16")
    frame["month"] = local.dt.month.astype("int8")
    frame["day"] = local.dt.day.astype("int8")
    frame["day_of_week"] = local.dt.dayofweek.astype("int8")
    frame["is_weekend"] = local.dt.dayofweek >= 5
    frame["timezone_name"] = MELBOURNE_TIMEZONE
    frame["local_utc_offset_minutes"] = local.map(lambda item: int(item.utcoffset().total_seconds() // 60)).astype("int32")
    return frame


def _finish_common_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = _add_temporal_columns(frame)
    frame["observation_id"] = frame["observation_id"].fillna(frame["observation_unit_id"])
    frame["intersection_total"] = frame["vehicle_count"]
    frame["log1p_vehicle_count"] = frame["vehicle_count"].map(lambda value: math.log1p(int(value)) if pd.notna(value) else math.nan)
    frame["quality_flag"] = frame.apply(
        lambda row: ";".join(
            sorted(
                set(
                    (["partial"] if _truthy(row.get("quality_partial_flag", False)) else [])
                    + (["alarm"] if _truthy(row.get("quality_alarm_flag", False)) else [])
                    + (["derived_zero"] if _truthy(row.get("ta_derived_zero", False)) else [])
                    + (["dst_ambiguous"] if _truthy(row.get("ta_dst_ambiguous_flag", False)) else [])
                    + (["dst_fallback_wrap"] if _truthy(row.get("ta_dst_fallback_wrap_flag", False)) else [])
                    + (["coordinate_missing"] if _truthy(row.get("coordinate_missing", False)) else [])
                    + (["coordinate_drift"] if _truthy(row.get("coordinate_drift_flag", False)) else [])
                )
            )
        ),
        axis=1,
    )
    for field in OUTPUT_COLUMNS:
        if field not in frame.columns:
            frame[field] = None
    frame = frame[OUTPUT_COLUMNS].copy()
    int_columns = [
        "local_hour",
        "year",
        "month",
        "day",
        "day_of_week",
        "source_timezone_offset_minutes",
        "local_utc_offset_minutes",
        "source_row_count",
        "source_record_count",
        "source_timestamp_count",
        "vehicle_count",
        "intersection_total",
        "quality_missing_interval_count",
        "ta_motor_class_rows",
        "ta_non_motor_class_rows",
        "ta_reported_class_rows",
        "scats_detector_count",
        "scats_detector_row_count",
        "scats_ct_records_min",
        "scats_ct_records_max",
        "scats_alarm_24hour_count",
    ]
    for column in int_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    bool_columns = [
        "is_weekend",
        "traffic_eligible",
        "coordinate_valid",
        "coordinate_missing",
        "coordinate_drift_flag",
        "quality_partial_flag",
        "quality_alarm_flag",
        "ta_derived_zero",
        "ta_dst_ambiguous_flag",
        "ta_dst_fallback_wrap_flag",
    ]
    for column in bool_columns:
        frame[column] = frame[column].astype("boolean")
    frame["scats_qt_volume_24hour_sum"] = pd.to_numeric(frame["scats_qt_volume_24hour_sum"], errors="coerce").astype("Float64")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce").astype("Float64")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce").astype("Float64")
    for column in ["hour_start_utc", "source_timestamp_utc", "source_timestamp_first_utc", "source_timestamp_last_utc"]:
        frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    frame["local_timestamp"] = pd.to_datetime(frame["local_timestamp"], errors="coerce", utc=True).dt.tz_convert(MELBOURNE_TIMEZONE)
    return frame


def validate_canonical_frame(frame: pd.DataFrame) -> None:
    """Fail closed on the invariants that make the target training-safe."""

    if frame.empty:
        raise ValueError("canonical traffic target is empty after filtering")
    missing = [field for field in NATURAL_KEY if field not in frame.columns]
    if missing:
        raise ValueError(f"canonical target is missing natural-key columns: {missing}")
    if frame[NATURAL_KEY].isna().any(axis=None):
        raise ValueError("canonical target contains null natural-key values")
    duplicate = frame.duplicated(NATURAL_KEY, keep=False)
    if duplicate.any():
        sample = frame.loc[duplicate, NATURAL_KEY].head(5).to_dict("records")
        raise ValueError(f"duplicate canonical natural keys detected: {sample}")
    timestamps = pd.to_datetime(frame["hour_start_utc"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("canonical target contains invalid timestamps")
    if not timestamps.dt.floor("h").eq(timestamps).all():
        raise ValueError("canonical target contains non-hourly hour_start_utc timestamps")
    target = pd.to_numeric(frame["vehicle_count"], errors="coerce")
    if target.isna().any():
        raise ValueError("canonical target contains null vehicle_count values")
    if (~target.map(math.isfinite)).any() or (target < 0).any():
        raise ValueError("canonical target contains non-finite or negative vehicle_count values")
    if not target.map(lambda value: float(value).is_integer()).all():
        raise ValueError("canonical target contains non-integer vehicle_count values")
    ta = frame[frame["label_source"].eq("transport_activity")]
    if not ta.empty:
        approved = ta["traffic_eligible"].fillna(False) & ta["review_status"].astype("string").str.lower().eq("approved")
        if not approved.all():
            raise ValueError("unapproved or ineligible Transport Activity label reached canonical output")
        derived = ta["ta_derived_zero"].fillna(False)
        evidence = (ta["ta_motor_class_rows"].fillna(0) == 0) & (ta["ta_non_motor_class_rows"].fillna(0) > 0)
        if not derived.eq(evidence).all():
            raise ValueError("Transport Activity derived-zero row lacks class evidence or has incorrect quality")


def _frame_to_arrow(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(frame, schema=OUTPUT_SCHEMA, preserve_index=False, safe=True)


def deterministic_stratified_preview(frame: pd.DataFrame, rows: int = 500) -> pd.DataFrame:
    """Select a stable, source/year-stratified preview without random state."""

    if rows < 1:
        raise ValueError("preview row count must be positive")
    if len(frame) <= rows:
        return frame.sort_values(NATURAL_KEY, kind="mergesort").reset_index(drop=True)
    work = frame.copy()
    work["_stratum"] = work["label_source"].astype("string") + "|" + work["year"].astype("string")
    work["_stable_key"] = work[NATURAL_KEY].astype("string").agg("|".join, axis=1)
    work["_stable_hash"] = work["_stable_key"].map(lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())
    counts = work["_stratum"].value_counts().sort_index()
    quotas = (counts / len(work) * rows).round().astype(int).clip(lower=1)
    while int(quotas.sum()) > rows:
        candidates = quotas[quotas > 1].sort_values(ascending=False)
        if candidates.empty:
            break
        quotas[candidates.index[0]] -= 1
    while int(quotas.sum()) < rows:
        remaining = (counts - quotas).sort_values(ascending=False)
        remaining = remaining[remaining > 0]
        if remaining.empty:
            break
        quotas[remaining.index[0]] += 1
    chosen: list[pd.DataFrame] = []
    for stratum, quota in quotas.items():
        subset = work[work["_stratum"].eq(stratum)].sort_values(["_stable_hash", "_stable_key"], kind="mergesort")
        chosen.append(subset.head(int(quota)))
    result = pd.concat(chosen, ignore_index=True).head(rows)
    return result.drop(columns=["_stratum", "_stable_key", "_stable_hash"], errors="ignore").sort_values(NATURAL_KEY, kind="mergesort").reset_index(drop=True)


def _serialise_metric(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(_serialise_metric(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key): _serialise_metric(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_serialise_metric(item) for item in value]
    if isinstance(value, tuple):
        return [_serialise_metric(item) for item in value]
    if isinstance(value, (pd._libs.missing.NAType,)):
        return None
    return value


def _schema_descriptor() -> list[dict[str, str]]:
    return [{"name": field.name, "type": str(field.type)} for field in OUTPUT_SCHEMA]


def load_expected_coverage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"expected coverage configuration is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"expected coverage configuration is invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError("expected coverage configuration must be a JSON object")
    return payload


def _config_value(payload: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def expected_coverage(
    payload: Mapping[str, Any],
    *,
    expected_ta_sources: Sequence[str] | None = None,
    expected_scats_years: Sequence[int] | None = None,
    scats_start_year: int | None = None,
    scats_end_year: int | None = None,
) -> dict[str, Any]:
    nested_scats = payload.get("scats", {}) if isinstance(payload.get("scats", {}), dict) else {}
    ta_default = _config_value(
        payload,
        [
            "transport_activity_sources",
            "expected_ta_sources",
            "expected_ta_source_ids",
            "expected_transport_activity_sources",
            "ta_sources",
        ],
        None,
    )
    if ta_default is None:
        ta_default = [f"com_transport_activity_{year}" for year in range(2023, 2027)]
    if expected_ta_sources:
        ta_values = list(expected_ta_sources)
    else:
        ta_values = [str(item.get("source_dataset_id", item)) if isinstance(item, dict) else str(item) for item in ta_default]
    configured_years = _config_value(payload, ["scats_expected_years", "expected_scats_years"], None)
    if configured_years is None:
        configured_years = nested_scats.get("expected_years")
    if expected_scats_years:
        scats_years = sorted({int(year) for year in expected_scats_years})
    elif configured_years is not None:
        scats_years = sorted({int(year) for year in configured_years})
    else:
        configured_start = _config_value(payload, ["scats_start_year", "expected_scats_start_year"], nested_scats.get("start_year", 2023))
        configured_end = _config_value(payload, ["scats_end_year", "expected_scats_end_year", "latest_complete_scats_year", "latest_complete_year"], nested_scats.get("end_year", 2026))
        configured_start = int(configured_start)
        configured_end = int(configured_end)
        scats_years = list(range(configured_start, configured_end + 1))
    if scats_start_year is not None or scats_end_year is not None:
        start = int(scats_start_year if scats_start_year is not None else min(scats_years))
        end = int(scats_end_year if scats_end_year is not None else max(scats_years))
        scats_years = list(range(start, end + 1))
    expected_start = _config_value(payload, ["scats_expected_start", "expected_scats_start"], nested_scats.get("expected_start"))
    expected_end = _config_value(payload, ["scats_expected_end", "expected_scats_end"], nested_scats.get("expected_end"))
    allowed_missing = _config_value(
        payload,
        ["allowed_missing_scats_dates", "scats_allowed_missing_dates"],
        nested_scats.get("allowed_missing_dates", []),
    )
    if allowed_missing is None:
        allowed_missing = []
    if isinstance(allowed_missing, str):
        allowed_missing = [allowed_missing]
    if not isinstance(allowed_missing, (list, tuple, set)):
        raise ValueError("allowed_missing_scats_dates must be a list of ISO dates")
    allowed_missing_dates: list[str] = []
    for value in allowed_missing:
        try:
            allowed_missing_dates.append(date.fromisoformat(str(value)[:10]).isoformat())
        except ValueError as error:
            raise ValueError(f"allowed_missing_scats_dates contains an invalid date: {value!r}") from error

    # The nested form is the contract.  Keep a small set of top-level aliases
    # for hand-authored/older configs, but never let an alias override the
    # explicitly nested value.
    if "source_datasets" in nested_scats:
        configured_source_datasets = nested_scats["source_datasets"]
        source_dataset_label = "scats.source_datasets"
    else:
        configured_source_datasets = _config_value(
            payload,
            [
                "scats_source_datasets",
                "expected_scats_source_datasets",
                "expected_scats_sources",
            ],
            None,
        )
        source_dataset_label = "SCATS source dataset allowlist"
    scats_source_datasets = _normalise_source_dataset_values(
        configured_source_datasets,
        source_dataset_label,
    )
    return {
        "transport_activity_sources": sorted({str(item) for item in ta_values}),
        "scats_expected_years": scats_years,
        "scats_expected_start": str(expected_start) if expected_start else None,
        "scats_expected_end": str(expected_end) if expected_end else None,
        "allowed_missing_scats_dates": sorted(set(allowed_missing_dates)),
        # This is the resolved field consumed by implicit archive discovery
        # and retained in the canonical manifest/content hash.
        "scats_source_datasets": scats_source_datasets,
    }


def check_coverage(
    ta_metrics: Sequence[Mapping[str, Any]],
    scats_metrics: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    *,
    allow_partial: bool,
) -> dict[str, Any]:
    ta_ids = {str(metric["source_dataset_id"]) for metric in ta_metrics}
    expected_ta = set(expected.get("transport_activity_sources", []))
    missing_ta = sorted(expected_ta - ta_ids)
    scats_dates: set[date] = set()
    for metric in scats_metrics:
        scats_dates.update(metric.get("dates", set()))
    scats_years = sorted({item.year for item in scats_dates})
    expected_scats_years = {int(item) for item in expected.get("scats_expected_years", [])}
    missing_scats_years = sorted(expected_scats_years - set(scats_years))
    missing_dates: list[str] = []
    start = expected.get("scats_expected_start")
    end = expected.get("scats_expected_end")
    if start and end:
        start_date = date.fromisoformat(str(start)[:10])
        end_date = date.fromisoformat(str(end)[:10])
        expected_dates = pd.date_range(start_date, end_date, freq="D").date
        missing_dates = [item.isoformat() for item in expected_dates if item not in scats_dates]
    configured_allowed = {
        date.fromisoformat(str(item)[:10])
        for item in expected.get("allowed_missing_scats_dates", [])
    }
    missing_date_values = {date.fromisoformat(item) for item in missing_dates}
    allowed_missing = sorted(configured_allowed.intersection(missing_date_values))
    unexpected_missing = sorted(missing_date_values - configured_allowed)
    # A whole expected year is only excused by the date allowlist when the
    # configured date interval proves that every date in that year is allowed.
    if start and end:
        expected_dates_by_year: dict[int, set[date]] = defaultdict(set)
        for value in pd.date_range(
            date.fromisoformat(str(start)[:10]),
            date.fromisoformat(str(end)[:10]),
            freq="D",
        ).date:
            expected_dates_by_year[value.year].add(value)
        missing_scats_years = [
            year
            for year in missing_scats_years
            if not expected_dates_by_year.get(year, set()).issubset(configured_allowed)
        ]
    partial = bool(missing_ta or missing_scats_years or unexpected_missing)
    coverage = {
        "expected_ta_sources": sorted(expected_ta),
        "observed_ta_sources": sorted(ta_ids),
        "missing_ta_sources": missing_ta,
        "expected_scats_years": sorted(expected_scats_years),
        "observed_scats_years": scats_years,
        "missing_scats_years": missing_scats_years,
        "expected_scats_start": start,
        "expected_scats_end": end,
        "missing_scats_dates": missing_dates[:1000],
        "missing_scats_date_count": len(missing_dates),
        "configured_allowed_missing_scats_dates": sorted(item.isoformat() for item in configured_allowed),
        "allowed_missing_scats_dates": [item.isoformat() for item in allowed_missing[:1000]],
        "allowed_missing_scats_date_count": len(allowed_missing),
        "unexpected_missing_scats_dates": [item.isoformat() for item in unexpected_missing[:1000]],
        "unexpected_missing_scats_date_count": len(unexpected_missing),
        "partial": partial,
    }
    if partial and not allow_partial:
        raise ValueError(
            "strict traffic build requires configured TA sources and complete SCATS coverage; "
            f"missing_ta={missing_ta}, missing_scats_years={missing_scats_years}, "
            f"missing_scats_dates={len(missing_dates)} "
            f"(allowed={len(allowed_missing)}, unexpected={len(unexpected_missing)}; "
            "pass --allow-partial for a partial artifact)"
        )
    return coverage


def _content_hash(
    inputs: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_config: Mapping[str, Any],
    options: Mapping[str, Any],
) -> str:
    def without_paths(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): without_paths(item) for key, item in value.items() if key != "path"}
        if isinstance(value, (list, tuple)):
            return [without_paths(item) for item in value]
        return value

    payload = {
        "inputs": sorted(
            (without_paths(item) for item in inputs),
            key=lambda item: (str(item.get("kind")), str(item.get("source_dataset_id", "")), str(item.get("sha256", ""))),
        ),
        "registry": without_paths(registry),
        "expected_coverage": without_paths(expected_config),
        # Process sizing is execution-only.  Keep this guard here as well as
        # omitting the setting at the call site so future tuning metadata
        # cannot fork the content-addressed artifact stem.
        "options": without_paths(
            {
                key: value
                for key, value in options.items()
                if key not in CONTENT_HASH_EXECUTION_ONLY_OPTIONS
            }
        ),
        "schema": _schema_descriptor(),
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _write_atomic_bytes(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _publish_staged_file(Path(temporary), path, overwrite=overwrite)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_atomic_parquet(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _parquet_relation(paths: Sequence[Path]) -> str:
    if not paths:
        raise ValueError("no Parquet staging files were produced")
    literals = ", ".join(_sql_string(path) for path in paths)
    return f"SELECT * FROM read_parquet([{literals}], union_by_name=false)"


def _duckdb_connection(temp_dir: Path) -> Any:
    try:
        import duckdb
    except ImportError as error:  # pragma: no cover - dependency is in ml/requirements.txt.
        raise RuntimeError("duckdb is required for bounded-memory traffic publication") from error
    connection = duckdb.connect(database=":memory:")
    connection.execute("PRAGMA memory_limit='2GB'")
    connection.execute("SET preserve_insertion_order=false")
    connection.execute("SET temp_directory = ?", [str(temp_dir)])
    return connection


def _validate_duckdb_relation(
    connection: Any,
    relation: str,
    label: str,
    *,
    allow_empty: bool = False,
) -> dict[str, int]:
    """Validate a staged/final relation without loading it into pandas."""

    stats = connection.execute(
        f"""
        SELECT
            COUNT(*) AS rows,
            COUNT(*) FILTER (
                WHERE source_dataset_id IS NULL
                   OR observation_unit_id IS NULL
                   OR hour_start_utc IS NULL
            ) AS null_key_rows,
            COUNT(*) FILTER (
                WHERE vehicle_count IS NULL
                   OR vehicle_count < 0
                   OR NOT isfinite(CAST(vehicle_count AS DOUBLE))
            ) AS invalid_target_rows,
            COUNT(*) FILTER (
                WHERE hour_start_utc IS NOT NULL
                  AND (
                      EXTRACT(MINUTE FROM hour_start_utc) <> 0
                      OR EXTRACT(SECOND FROM hour_start_utc) <> 0
                  )
            ) AS non_hourly_rows
        FROM ({relation}) AS canonical
        """
    ).fetchone()
    result = {
        "rows": int(stats[0]),
        "null_key_rows": int(stats[1]),
        "invalid_target_rows": int(stats[2]),
        "non_hourly_rows": int(stats[3]),
    }
    if result["rows"] == 0 and allow_empty:
        return result | {"duplicate_key_groups": 0}
    duplicate_groups = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT source_dataset_id, observation_unit_id, hour_start_utc
            FROM ({relation}) AS canonical
            GROUP BY source_dataset_id, observation_unit_id, hour_start_utc
            HAVING COUNT(*) > 1
        ) AS duplicates
        """
    ).fetchone()[0]
    result["duplicate_key_groups"] = int(duplicate_groups)
    failures = {
        key: value
        for key, value in result.items()
        if key != "rows" and value
    }
    if result["rows"] == 0 or failures:
        raise ValueError(f"{label} invariant validation failed: {result}")
    return result


def _coerce_output_table(table: pa.Table) -> pa.Table:
    if table.column_names != OUTPUT_COLUMNS:
        raise ValueError(
            "Parquet relation changed the public traffic schema: "
            f"expected {OUTPUT_COLUMNS}, got {table.column_names}"
        )
    columns: list[pa.ChunkedArray] = []
    for field in OUTPUT_SCHEMA:
        column = table[field.name]
        if not column.type.equals(field.type):
            try:
                column = column.cast(field.type, safe=False)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                # DuckDB may materialize a timezone-aware timestamp as UTC.
                # The instant is already correct; relabeling its physical
                # microseconds with the public timezone preserves the schema.
                if pa.types.is_timestamp(column.type) and pa.types.is_timestamp(field.type):
                    column = column.cast(pa.timestamp(field.type.unit), safe=False).cast(field.type, safe=False)
                else:
                    raise
        columns.append(column)
    return pa.Table.from_arrays(columns, schema=OUTPUT_SCHEMA)


def _write_atomic_parquet_batches(
    path: Path,
    batches: Iterable[pa.RecordBatch],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    writer: pq.ParquetWriter | None = None
    rows = 0
    try:
        for batch in batches:
            table = _coerce_output_table(pa.Table.from_batches([batch]))
            if table.num_rows == 0:
                continue
            if writer is None:
                writer = pq.ParquetWriter(
                    temporary,
                    OUTPUT_SCHEMA,
                    compression="zstd",
                    use_dictionary=True,
                )
            writer.write_table(table)
            rows += table.num_rows
        if writer is None:
            raise ValueError("cannot publish an empty canonical traffic target")
        writer.close()
        os.replace(temporary, path)
    except Exception:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return rows


def _sort_relation_to_parquet(connection: Any, relation: str, path: Path) -> None:
    order = ", ".join(f'"{column}" ASC' for column in NATURAL_KEY)
    connection.execute(
        f"COPY (SELECT * FROM ({relation}) AS canonical ORDER BY {order}) "
        f"TO {_sql_string(path)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )


def _validate_sorted_parquet(path: Path) -> None:
    previous: tuple[Any, ...] | None = None
    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(columns=NATURAL_KEY, batch_size=100_000):
        for row in pa.Table.from_batches([batch]).to_pylist():
            current = tuple(row[column] for column in NATURAL_KEY)
            if previous is not None and current <= previous:
                raise ValueError(f"final canonical target is not globally ordered by {NATURAL_KEY}")
            previous = current


def _stable_key_sql() -> str:
    return (
        "CAST(source_dataset_id AS VARCHAR) || '|' || "
        "CAST(observation_unit_id AS VARCHAR) || '|' || "
        "CAST(hour_start_utc AS VARCHAR)"
    )


def _preview_from_relation(connection: Any, relation: str, rows: int) -> pd.DataFrame:
    if rows < 1:
        raise ValueError("preview row count must be positive")
    total = int(connection.execute(f"SELECT COUNT(*) FROM ({relation}) AS canonical").fetchone()[0])
    if total == 0:
        raise ValueError("cannot create a preview from an empty canonical target")
    order = ", ".join(f'"{column}" ASC' for column in NATURAL_KEY)
    selected_tables: list[pa.Table] = []
    if total <= rows:
        selected_tables.append(
            _coerce_output_table(
                connection.execute(
                    f"SELECT * FROM ({relation}) AS canonical ORDER BY {order}"
                ).to_arrow_table()
            )
        )
    else:
        strata = connection.execute(
            f"""
            SELECT label_source, year, COUNT(*) AS row_count
            FROM ({relation}) AS canonical
            GROUP BY label_source, year
            ORDER BY label_source, year
            """
        ).fetchall()
        counts = {(str(source), int(year)): int(count) for source, year, count in strata}
        quotas = {
            key: max(1, int(round(count / total * rows)))
            for key, count in counts.items()
        }
        while sum(quotas.values()) > rows:
            candidates = sorted(key for key, quota in quotas.items() if quota > 1)
            if not candidates:
                break
            key = sorted(candidates, key=lambda item: (-quotas[item], item))[0]
            quotas[key] -= 1
        while sum(quotas.values()) < rows:
            candidates = [key for key in sorted(counts) if counts[key] > quotas[key]]
            if not candidates:
                break
            key = sorted(candidates, key=lambda item: (-(counts[item] - quotas[item]), item))[0]
            quotas[key] += 1
        stable_key = _stable_key_sql()
        for (source, year), quota in sorted(quotas.items()):
            selected_tables.append(
                _coerce_output_table(
                    connection.execute(
                        f"""
                        SELECT *
                        FROM ({relation}) AS canonical
                        WHERE label_source = ? AND year = ?
                        ORDER BY sha256({stable_key}), {stable_key}
                        LIMIT ?
                        """,
                        [source, year, int(quota)],
                    ).to_arrow_table()
                )
            )
    preview_table = pa.concat_tables(selected_tables, promote_options="default")
    preview = preview_table.to_pandas()
    return preview.sort_values(NATURAL_KEY, kind="mergesort").reset_index(drop=True).head(rows)


def build_dataset(args: argparse.Namespace) -> dict[str, Any]:
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be positive")
    if args.preview_rows < 1:
        raise ValueError("--preview-rows must be positive")
    datasets_dir = args.datasets_dir.resolve()
    transport_root = (args.transport_root or datasets_dir).resolve()
    scats_root = (args.scats_root or datasets_dir).resolve()
    transport_paths = [Path(item) for item in args.transport_zip] if args.transport_zip else (
        sorted(transport_root.rglob("*.zip")) if args.transport_root else None
    )
    scats_paths = [Path(item) for item in args.scats_zip] if args.scats_zip else None
    expected_path = args.expected_config.resolve()
    expected_snapshot = _file_snapshot(expected_path)
    expected_payload = load_expected_coverage(expected_path)
    _revalidate_input_snapshots(
        [{"kind": "expected_config", **expected_snapshot}],
        "expected coverage load",
    )
    expected_ta_cli = [
        part.strip()
        for item in args.expected_ta_source
        for part in str(item).split(",")
        if part.strip()
    ]
    expected_scats_cli = [
        int(part.strip())
        for item in args.expected_scats_year
        for part in str(item).split(",")
        if part.strip()
    ]
    expected = expected_coverage(
        expected_payload,
        expected_ta_sources=expected_ta_cli,
        expected_scats_years=expected_scats_cli,
        scats_start_year=args.scats_start_year,
        scats_end_year=args.scats_end_year,
    )
    transport_archives = discover_transport_archives(transport_root, transport_paths)
    scats_archives = discover_scats_archives(
        scats_root,
        scats_paths,
        source_dataset_allowlist=expected["scats_source_datasets"],
    )
    if not transport_archives:
        raise FileNotFoundError("no com_transport_activity_* ZIP archives were discovered")
    if not scats_archives and not args.allow_partial:
        raise FileNotFoundError("no SCATS ZIP archives were discovered")
    archive_snapshots = [
        {
            "kind": archive_info.kind,
            "source_dataset_id": archive_info.source_dataset_id,
            **_file_snapshot(archive_info.path),
        }
        for archive_info in [*transport_archives, *scats_archives]
    ]
    registry_path = args.registry.resolve()
    registry_snapshot = _file_snapshot(registry_path)
    signals_path = args.signals.resolve()
    signals_snapshot = _file_snapshot(signals_path)
    registry_frame = load_transport_registry(registry_path)
    # Keep only the compact scalar lookup needed by TA workers.  In
    # particular, do not retain the validated registry dataframe in the
    # parent while archive staging is in progress.
    registry = _registry_lookup(registry_frame)
    del registry_frame
    signals = load_signal_coordinates(signals_path) if scats_archives else {}
    input_snapshots: list[dict[str, Any]] = [
        *archive_snapshots,
        {"kind": "ta_registry", **registry_snapshot},
        {"kind": "scats_signals", **signals_snapshot},
        {"kind": "expected_config", **expected_snapshot},
    ]
    _revalidate_input_snapshots(input_snapshots, "source inspection")

    input_paths = archive_snapshots
    registry_info = dict(registry_snapshot)
    signals_info = dict(signals_snapshot)
    expected_info = {
        **expected_snapshot,
        "payload": expected_payload,
    }
    options = {
        "bbox": args.bbox,
        "include_missing_coordinates": args.include_missing_coordinates,
        "expected": expected,
        "allow_partial": args.allow_partial,
        "preview_rows": args.preview_rows,
        # Worker count is deliberately execution-only.  It must never enter
        # the content hash: serial and parallel runs share one artifact stem.
    }
    content_hash = _content_hash(
        [*input_paths, {"kind": "ta_registry", **registry_info}, {"kind": "scats_signals", **signals_info}],
        {"registry": registry_info},
        expected_info,
        options,
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_root = Path(tempfile.mkdtemp(prefix=".traffic-staging-", dir=output_dir))
    connection: Any = None
    try:
        eligible_site_ids = None
        if scats_archives and args.bbox is not None and not args.include_missing_coordinates:
            eligible_site_ids = scats_bbox_eligible_site_ids(signals, args.bbox)

        scats_units = enumerate_scats_logical_units(scats_archives)
        logical_units = [
            LogicalWorkUnit(archive_info=archive_info)
            for archive_info in transport_archives
        ] + scats_units
        stage_tasks: list[StageTask] = []
        for index, unit in enumerate(logical_units):
            if unit.archive_info.kind == "transport_activity":
                stage_tasks.append(
                    StageTask(
                        index=index,
                        unit=unit,
                        registry=registry,
                        chunk_size=args.chunk_size,
                        stage_path=staging_root / f"unit_{index:05d}.parquet",
                    )
                )
            else:
                stage_tasks.append(
                    StageTask(
                        index=index,
                        unit=unit,
                        chunk_size=args.chunk_size,
                        stage_path=staging_root / f"unit_{index:05d}.parquet",
                        eligible_site_ids=eligible_site_ids,
                        coordinates=signals,
                        bbox=args.bbox,
                        include_missing_coordinates=args.include_missing_coordinates,
                    )
                )

        stage_results, effective_workers, worker_strategy = _run_stage_tasks(
            stage_tasks,
            requested_workers=args.workers,
        )
        _revalidate_input_snapshots(input_snapshots, "archive staging")
        ta_results = [
            result for result in stage_results
            if result["kind"] == "transport_activity"
        ]
        scats_results = [
            result for result in stage_results
            if result["kind"] == "scats"
        ]
        ta_metrics = _aggregate_source_metrics(ta_results)
        scats_metrics = _aggregate_source_metrics(scats_results)

        # Results are returned in deterministic parent work-unit order even
        # though worker completion messages above may have arrived out of order.
        stage_records = [
            {
                "kind": result["kind"],
                "source_dataset_id": result["source_dataset_id"],
                "work_unit_label": result.get("work_unit_label"),
                "nested_member": result.get("nested_member"),
                "path": Path(result["path"]),
            }
            for result in stage_results
        ]

        coverage = check_coverage(ta_metrics, scats_metrics, expected, allow_partial=args.allow_partial)
        status = "partial" if coverage["partial"] else "complete"
        stem = f"traffic_training_v1_{'partial_' if status == 'partial' else ''}{content_hash}"
        parquet_path = output_dir / f"{stem}.parquet"
        preview_path = output_dir / f"{stem}_preview.csv"
        manifest_path = output_dir / f"{stem}_manifest.json"
        stage_paths = [record["path"] for record in stage_records]
        connection = _duckdb_connection(staging_root / "duckdb-tmp")
        stage_validation: list[dict[str, Any]] = []
        for record in stage_records:
            validation = _validate_duckdb_relation(
                connection,
                _parquet_relation([record["path"]]),
                f"{record['kind']} stage {record['source_dataset_id']}",
                allow_empty=True,
            )
            stage_validation.append(
                {
                    "kind": record["kind"],
                    "source_dataset_id": record["source_dataset_id"],
                    "work_unit_label": record["work_unit_label"],
                    "nested_member": record["nested_member"],
                    **validation,
                }
            )

        staged_relation = _parquet_relation(stage_paths)
        sorted_stage_path = staging_root / "globally_sorted.parquet"
        _sort_relation_to_parquet(connection, staged_relation, sorted_stage_path)
        sorted_relation = _parquet_relation([sorted_stage_path])
        sorted_validation = _validate_duckdb_relation(connection, sorted_relation, "globally sorted stage")

        final_stage_path = staging_root / "final.parquet"
        sorted_file = pq.ParquetFile(sorted_stage_path)
        final_rows = _write_atomic_parquet_batches(
            final_stage_path,
            sorted_file.iter_batches(batch_size=100_000),
        )
        final_relation = _parquet_relation([final_stage_path])
        final_validation = _validate_duckdb_relation(connection, final_relation, "final canonical target")
        if final_rows != final_validation["rows"]:
            raise ValueError(f"final row-count mismatch: writer={final_rows}, validator={final_validation['rows']}")
        _validate_sorted_parquet(final_stage_path)
        preview = _preview_from_relation(connection, final_relation, args.preview_rows)
        quality = connection.execute(
            f"""
            SELECT
                COUNT(*) AS rows,
                COUNT(*) FILTER (WHERE label_source = 'transport_activity') AS transport_activity_rows,
                COUNT(*) FILTER (WHERE label_source = 'scats') AS scats_rows,
                COUNT(*) FILTER (WHERE ta_dst_ambiguous_flag) AS ta_dst_ambiguous_output_rows,
                COUNT(*) FILTER (WHERE ta_dst_fallback_wrap_flag) AS ta_dst_fallback_wrap_output_rows,
                COUNT(*) FILTER (WHERE ta_derived_zero) AS derived_zero_rows,
                COUNT(*) FILTER (WHERE quality_partial_flag) AS partial_rows,
                COUNT(*) FILTER (WHERE quality_alarm_flag) AS alarm_rows,
                COUNT(*) FILTER (WHERE coordinate_missing) AS coordinate_missing_rows,
                COUNT(*) FILTER (WHERE coordinate_drift_flag) AS coordinate_drift_rows
            FROM ({final_relation}) AS canonical
            """
        ).fetchone()
        scats_negative_frequency: Counter[str] = Counter()
        for metric in scats_metrics:
            for value, count in metric.get("scats_negative_value_frequency", {}).items():
                scats_negative_frequency[str(value)] += int(count)
        quality_metrics = {
            "rows": int(quality[0]),
            "transport_activity_rows": int(quality[1]),
            "scats_rows": int(quality[2]),
            "ta_dst_ambiguous_rows": int(sum(metric.get("dst_ambiguous_rows", 0) for metric in ta_metrics)),
            "ta_dst_fallback_wrap_rows": int(sum(metric.get("dst_fallback_wrap_rows", 0) for metric in ta_metrics)),
            "ta_dst_ambiguous_output_rows": int(quality[3]),
            "ta_dst_fallback_wrap_output_rows": int(quality[4]),
            "scats_omitted_null_target_site_hours": int(sum(metric.get("omitted_null_target_site_hours", 0) for metric in scats_metrics)),
            "scats_negative_interval_count": int(sum(metric.get("scats_negative_interval_count", 0) for metric in scats_metrics)),
            "scats_standard_minus_one_count": int(sum(metric.get("scats_standard_minus_one_count", 0) for metric in scats_metrics)),
            "scats_nonstandard_negative_count": int(sum(metric.get("scats_nonstandard_negative_count", 0) for metric in scats_metrics)),
            "scats_negative_value_frequency": dict(sorted(scats_negative_frequency.items())),
            "derived_zero_rows": int(quality[5]),
            "partial_rows": int(quality[6]),
            "alarm_rows": int(quality[7]),
            "coordinate_missing_rows": int(quality[8]),
            "coordinate_drift_rows": int(quality[9]),
        }
        preview_csv = preview.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S%z").encode("utf-8")
        parquet_bytes = final_stage_path.stat().st_size
        parquet_sha256 = sha256_file(final_stage_path)
        manifest = {
            "schema_version": 1,
            "content_hash": content_hash,
            "artifact_status": status,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "measure": "hourly vehicle traffic target; Transport Activity countline or SCATS intersection",
            "natural_key": NATURAL_KEY,
            "target_columns": {"raw": "vehicle_count", "transformed": "log1p_vehicle_count"},
            "timezone": MELBOURNE_TIMEZONE,
            "scats_source_timezone": SCATS_SOURCE_TIMEZONE,
            "bbox": list(args.bbox) if args.bbox is not None else None,
            "inputs": [*input_paths, {"kind": "ta_registry", **registry_info}, {"kind": "scats_signals", **signals_info}],
            "expected_coverage": {"config": expected_info, "resolved": expected},
            "coverage": _serialise_metric(coverage),
            "sources": _serialise_metric([*ta_metrics, *scats_metrics]),
            "staging": {
                "strategy": "per_work_unit_parquet_then_duckdb_external_global_sort",
                "memory_limit": "2GB",
                "worker_strategy": worker_strategy,
                "requested_workers": int(args.workers),
                "effective_workers": int(effective_workers),
                "work_unit_count": len(stage_tasks),
                "work_unit_counts": {
                    "transport_activity": sum(
                        1 for task in stage_tasks if task.unit.archive_info.kind == "transport_activity"
                    ),
                    "scats": sum(
                        1 for task in stage_tasks if task.unit.archive_info.kind == "scats"
                    ),
                },
                "source_unit_fanout": [
                    {
                        "kind": metric["kind"],
                        "source_dataset_id": metric["source_dataset_id"],
                        "work_unit_count": int(metric.get("work_unit_count", 0)),
                        "work_unit_labels": metric.get("work_unit_labels", []),
                        "nested_member_count": len(metric.get("nested_members", [])),
                    }
                    for metric in [*ta_metrics, *scats_metrics]
                ],
                "work_units": [
                    {
                        "index": task.index,
                        "kind": task.unit.archive_info.kind,
                        "source_dataset_id": task.unit.archive_info.source_dataset_id,
                        "label": task.unit.label,
                        "nested_member": task.unit.nested_member,
                        "top_level_member_count": len(task.unit.top_level_members),
                    }
                    for task in stage_tasks
                ],
                "stage_count": len(stage_validation),
                "per_work_unit_validation": stage_validation,
                "sorted_stage_validation": sorted_validation,
                "final_validation": final_validation,
                "global_ordered_by": NATURAL_KEY,
            },
            "schema": _schema_descriptor(),
            "schema_hash": hashlib.sha256(_json_dumps(_schema_descriptor()).encode("utf-8")).hexdigest(),
            "outputs": {
                "parquet": {"path": str(parquet_path), "rows": final_validation["rows"], "bytes": parquet_bytes, "sha256": parquet_sha256},
                "preview_csv": {"path": str(preview_path), "rows": len(preview), "stratified": True},
                "manifest": {"path": str(manifest_path)},
            },
            "quality": quality_metrics,
            "assertions": {
                "natural_key_unique": True,
                "null_targets_allowed": False,
                "negative_targets_allowed": False,
                "hour_start_utc_hourly": True,
                "ta_registry_filter": "traffic_eligible=true and review_status=approved",
                "ta_z_suffix_interpreted_as_melbourne_wall_time": True,
                "ta_ambiguous_fallback_fold": "standard_time_fold_1",
                "ta_derived_zero_requires_non_motor_class_evidence": True,
                "scats_fixed_source_timezone": True,
                "all_sentinel_scats_site_hours_omitted": True,
                "all_negative_evidence_scats_site_hours_omitted": True,
                "scats_negative_interval_policy": "all V00-V95 values below zero are missing evidence; -1 is standard and values below -1 are nonstandard",
                "scats_negative_intervals_in_vehicle_totals": False,
                "scats_negative_intervals_become_zero": False,
                "scats_zero_values_are_real_observations": True,
                "scats_negative_value_frequency_is_deterministic": True,
                "scats_negative_sentinel_metrics": {
                    "negative_interval_count": quality_metrics["scats_negative_interval_count"],
                    "standard_minus_one_count": quality_metrics["scats_standard_minus_one_count"],
                    "nonstandard_negative_count": quality_metrics["scats_nonstandard_negative_count"],
                    "negative_value_frequency": quality_metrics["scats_negative_value_frequency"],
                },
                "missing_coordinates_imputed": False,
                "weather_holidays_roads_joined": False,
                "acquisition_performed": False,
            },
        }
        # Publish the final target only after all checks and preview generation
        # have succeeded.  The manifest remains the last readiness marker.
        publish_lock = output_dir / f".{stem}.publish-lock"
        with _exclusive_publish_lock(publish_lock):
            existing = [path for path in (parquet_path, preview_path, manifest_path) if path.exists()]
            if existing and not args.overwrite:
                raise FileExistsError(
                    f"content-versioned output already exists (pass --overwrite): {existing[0]}"
                )
            _revalidate_input_snapshots(input_snapshots, "before publication")
            published: list[tuple[Path, int]] = []
            try:
                _publish_staged_file(final_stage_path, parquet_path, overwrite=args.overwrite)
                published.append((parquet_path, parquet_path.stat().st_ino))
                _write_atomic_bytes(preview_path, preview_csv, overwrite=args.overwrite)
                published.append((preview_path, preview_path.stat().st_ino))
                manifest["outputs"]["preview_csv"].update(
                    {"bytes": preview_path.stat().st_size, "sha256": sha256_file(preview_path)}
                )
                manifest_bytes = (
                    json.dumps(manifest, indent=2, sort_keys=True, default=_serialise_metric) + "\n"
                ).encode("utf-8")
                _write_atomic_bytes(manifest_path, manifest_bytes, overwrite=args.overwrite)
                published.append((manifest_path, manifest_path.stat().st_ino))
            except Exception:
                # If overwrite was not authorized, remove only files whose
                # inode this publisher created.  Never remove a concurrent
                # replacement that appeared after a publication failure.
                if not args.overwrite:
                    for path, inode in reversed(published):
                        try:
                            if path.stat().st_ino == inode:
                                path.unlink()
                        except FileNotFoundError:
                            pass
                raise
        return {
            "parquet": parquet_path,
            "preview": preview_path,
            "manifest": manifest_path,
            "rows": final_validation["rows"],
            "preview_rows": len(preview),
            "status": status,
            "content_hash": content_hash,
            "coverage": coverage,
        }
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(staging_root, ignore_errors=True)


def _parse_bbox(value: str) -> tuple[float, float, float, float] | None:
    if value.strip().lower() in {"none", "null", "off"}:
        return None
    parts = [float(item.strip()) for item in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = parts
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError("--bbox bounds must be increasing")
    return min_lon, min_lat, max_lon, max_lat


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--transport-root", "--ta-root", type=Path, help="root to recursively discover Transport Activity ZIPs")
    parser.add_argument("--scats-root", type=Path, help="root to recursively discover SCATS ZIPs")
    parser.add_argument("--transport-zip", "--ta-zip", action="append", default=[], help="explicit Transport Activity ZIP; repeatable")
    parser.add_argument("--scats-zip", action="append", default=[], help="explicit SCATS ZIP; repeatable")
    parser.add_argument("--registry", "--transport-registry", "--review-registry", "--approved-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--signals", "--traffic-signals", "--signals-csv", "--scats-signal-locations", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--expected-config", "--expected-coverage", type=Path, default=DEFAULT_EXPECTED_CONFIG)
    parser.add_argument("--expected-ta-source", "--expected-ta-sources", action="append", default=[], help="expected TA source dataset id; repeatable")
    parser.add_argument("--expected-scats-year", "--expected-scats-years", action="append", default=[], help="expected SCATS year; repeatable")
    parser.add_argument("--scats-start-year", "--expected-scats-start-year", type=int)
    parser.add_argument("--scats-end-year", "--expected-scats-end-year", "--latest-complete-scats-year", type=int)
    parser.add_argument("--bbox", type=_parse_bbox, default=CITY_BBOX)
    parser.add_argument("--include-missing-coordinates", action="store_true")
    parser.add_argument("--output-dir", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview-rows", type=int, default=500)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="maximum shared TA/SCATS logical-work-unit staging workers (default: 1)",
    )
    parser.add_argument("--allow-partial", action="store_true", help="publish a partial artifact when configured coverage is absent")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_dataset(args)
    print(f"Wrote {result['rows']:,} rows to {result['parquet']}")
    print(f"Preview ({result['preview_rows']:,} rows): {result['preview']}")
    print(f"Manifest ({result['status']}): {result['manifest']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
