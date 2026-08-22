"""Contract tests for the crowd dataset harmoniser.

The builder is intentionally exercised as a subprocess because the ML
workstream does not yet expose a Python package API.  The expected command is::

    python ml/scripts/build_crowd_dataset.py \
        --historical-zip PATH \
        --wayback-csv PATH \
        --current-parquet PATH \
        --sensor-locations PATH \
        --output-dir PATH

The command should write one harmonised Parquet table below ``--output-dir``.
The tests accept a small set of equivalent canonical field names so that the
file name and minor naming choices can evolve without weakening the data
contract.  The semantic fields exercised here are an observation key, sensor,
local date/hour, total count, directional counts, a direction-validity flag,
coordinates, and a Melbourne daylight-saving flag.
"""

from __future__ import annotations

import csv
import io
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # pyarrow is also the native dependency needed to read the current input.
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised only in minimal environments.
    pa = None
    pq = None


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "ml" / "scripts" / "build_crowd_dataset.py"

HISTORICAL_COLUMNS = [
    "ID",
    "Date_Time",
    "Year",
    "Month",
    "Mdate",
    "Day",
    "Time",
    "Sensor_ID",
    "Sensor_Name",
    "Hourly_Counts",
]
WAYBACK_COLUMNS = [
    "id",
    "location_id",
    "sensing_date",
    "hourday",
    "direction_1",
    "direction_2",
    "pedestriancount",
    "sensor_name",
    "location",
]

KEY_FIELDS = ("observation_key", "observation_id", "obs_key", "stable_key")
SENSOR_FIELDS = ("sensor_id", "location_id", "Sensor_ID", "sensor")
DATE_FIELDS = ("local_date", "sensing_date", "date", "observation_date")
HOUR_FIELDS = ("local_hour", "hour", "hourday", "hour_of_day")
TIMESTAMP_FIELDS = (
    "timestamp_local",
    "local_timestamp",
    "observed_at",
    "datetime_local",
    "date_time",
    "Date_Time",
)
COUNT_FIELDS = (
    "pedestrian_flow",
    "pedestrian_count",
    "pedestriancount",
    "count",
    "hourly_count",
    "hourly_counts",
    "count_total",
    "total_count",
)
DIRECTION_1_FIELDS = (
    "direction_1",
    "direction_1_count",
    "direction1",
)
DIRECTION_2_FIELDS = (
    "direction_2",
    "direction_2_count",
    "direction2",
)
DIRECTION_VALID_FIELDS = (
    "direction_counts_valid",
    "direction_valid",
    "directions_valid",
    "valid_directions",
    "direction_validation",
    "direction_status",
)
DST_FIELDS = ("is_dst", "dst", "dst_flag", "daylight_saving")
RECONSTRUCTED_FIELDS = ("hour_was_reconstructed", "hour_reconstructed")
LAT_FIELDS = ("latitude", "lat")
LON_FIELDS = ("longitude", "lon", "lng")
LOCATION_FIELDS = ("location", "geometry", "point")


def _wkb_point(latitude: float, longitude: float) -> bytes:
    """Return a little-endian WKB Point, matching the live Parquet export."""

    return b"\x01\x01\x00\x00\x00" + struct.pack("<dd", longitude, latitude)


def _historical_row(
    sensor_id: int,
    observed_at: datetime,
    count: int,
    *,
    row_id: int | str | None = None,
    sensor_name: str | None = None,
) -> dict[str, str]:
    """Build a row with the attachment's native historical CSV schema."""

    return {
        "ID": str(row_id if row_id is not None else f"h-{sensor_id}-{observed_at:%Y%m%d%H}"),
        "Date_Time": observed_at.strftime("%B %d, %Y %I:%M:%S %p"),
        "Year": str(observed_at.year),
        "Month": observed_at.strftime("%B"),
        "Mdate": str(observed_at.day),
        "Day": observed_at.strftime("%A"),
        "Time": str(observed_at.hour),
        "Sensor_ID": str(sensor_id),
        "Sensor_Name": sensor_name or f"Sensor {sensor_id}",
        "Hourly_Counts": str(count),
    }


def _wayback_row(
    sensor_id: int,
    observed_date: date | str,
    hour: int,
    direction_1: int | str,
    direction_2: int | str,
    count: int | str | None = None,
    *,
    row_id: str | None = None,
    sensor_name: str | None = None,
    location: str | None = None,
) -> dict[str, str]:
    """Build a semicolon-delimited archived City CSV row."""

    date_text = observed_date.isoformat() if isinstance(observed_date, date) else observed_date
    if count is None:
        count = int(direction_1) + int(direction_2)
    return {
        "id": row_id or f"w-{sensor_id}-{date_text.replace('-', '')}-{hour:02d}",
        "location_id": str(sensor_id),
        "sensing_date": date_text,
        "hourday": str(hour),
        "direction_1": str(direction_1),
        "direction_2": str(direction_2),
        "pedestriancount": str(count),
        "sensor_name": sensor_name or f"Sensor {sensor_id}",
        # The native export orders this text as latitude, longitude.
        "location": location or "-37.80000000, 144.96000000",
    }


def _current_row(
    sensor_id: int,
    observed_date: date | str,
    hour: int,
    direction_1: int,
    direction_2: int,
    *,
    count: int | None = None,
    row_id: int | None = None,
    sensor_name: str | None = None,
    location: bytes | None = None,
) -> dict[str, Any]:
    """Build a row with the current portal Parquet schema."""

    if isinstance(observed_date, str):
        observed_date = date.fromisoformat(observed_date)
    if count is None:
        count = direction_1 + direction_2
    return {
        "id": row_id if row_id is not None else sensor_id * 100000000 + int(observed_date.strftime("%Y%m%d")) * 24 + hour,
        "location_id": sensor_id,
        "sensing_date": observed_date,
        "hourday": hour,
        "direction_1": direction_1,
        "direction_2": direction_2,
        "pedestriancount": count,
        "sensor_name": sensor_name or f"Sensor {sensor_id}",
        "location": location or _wkb_point(-37.8, 144.96),
    }


def _field(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    raise AssertionError(
        f"output is missing a field for {tuple(names)!r}; columns={sorted(row)}"
    )


def _optional_field(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text and len(text) > 10:
        text = text.split(" ", 1)[0]
    return date.fromisoformat(text[:10])


def _date_hour(row: Mapping[str, Any]) -> tuple[date, int]:
    date_value = _optional_field(row, DATE_FIELDS)
    hour_value = _optional_field(row, HOUR_FIELDS)
    if date_value is not None and hour_value is not None:
        return _as_date(date_value), int(hour_value)

    timestamp = _field(row, TIMESTAMP_FIELDS)
    if isinstance(timestamp, datetime):
        return timestamp.date(), timestamp.hour
    text = str(timestamp).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%B %d, %Y %I:%M:%S %p"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:  # pragma: no cover - makes a schema mismatch actionable.
            raise AssertionError(f"cannot parse harmonised timestamp {timestamp!r}")
    return parsed.date(), parsed.hour


def _natural_key(row: Mapping[str, Any]) -> tuple[int, date, int]:
    return int(_field(row, SENSOR_FIELDS)), *_date_hour(row)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "valid", "ok", "dst", "aedt", "daylight"}:
        return True
    if text in {"0", "false", "no", "n", "invalid", "aest", "standard", "none"}:
        return False
    raise AssertionError(f"cannot interpret boolean output value {value!r}")


def _is_missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _coordinates(row: Mapping[str, Any]) -> tuple[float, float]:
    latitude = _optional_field(row, LAT_FIELDS)
    longitude = _optional_field(row, LON_FIELDS)
    if latitude is not None and longitude is not None:
        return float(latitude), float(longitude)

    location = _field(row, LOCATION_FIELDS)
    if isinstance(location, (bytes, bytearray)) and len(location) >= 21:
        # WKB is x=longitude followed by y=latitude.
        x, y = struct.unpack("<dd", bytes(location)[5:21])
        return y, x
    text = str(location).strip()
    if "," in text:
        latitude_text, longitude_text = text.split(",", 1)
        return float(latitude_text), float(longitude_text)
    raise AssertionError(f"cannot decode harmonised location {location!r}")


class BuildCrowdDatasetTests(unittest.TestCase):
    """Small, source-shaped fixtures for the forthcoming CLI."""

    def setUp(self) -> None:
        if pa is None or pq is None:
            self.skipTest("pyarrow is required for Parquet fixtures")
        self.tempdir = tempfile.TemporaryDirectory(prefix="crowd-builder-")
        self.work = Path(self.tempdir.name)

    def tearDown(self) -> None:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def _write_inputs(
        self,
        *,
        historical: Iterable[Mapping[str, Any]] = (),
        wayback: Iterable[Mapping[str, Any]] = (),
        current: Iterable[Mapping[str, Any]] = (),
        reverse_rows: bool = False,
    ) -> dict[str, Path]:
        historical_rows = [dict(row) for row in historical]
        wayback_rows = [dict(row) for row in wayback]
        current_rows = [dict(row) for row in current]
        if reverse_rows:
            historical_rows.reverse()
            wayback_rows.reverse()
            current_rows.reverse()

        historical_zip = self.work / "historical.zip"
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=HISTORICAL_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(historical_rows)
        with zipfile.ZipFile(historical_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("historical.csv", csv_buffer.getvalue().encode("utf-8"))

        wayback_csv = self.work / "wayback.csv"
        with wayback_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=WAYBACK_COLUMNS, delimiter=";", lineterminator="\n")
            writer.writeheader()
            writer.writerows(wayback_rows)

        current_parquet = self.work / "current.parquet"
        current_table = pa.table(
            {
                "id": pa.array([row["id"] for row in current_rows], type=pa.int64()),
                "location_id": pa.array([row["location_id"] for row in current_rows], type=pa.int64()),
                "sensing_date": pa.array(
                    [row["sensing_date"] for row in current_rows], type=pa.date32()
                ),
                "hourday": pa.array([row["hourday"] for row in current_rows], type=pa.int64()),
                "direction_1": pa.array(
                    [row["direction_1"] for row in current_rows], type=pa.int64()
                ),
                "direction_2": pa.array(
                    [row["direction_2"] for row in current_rows], type=pa.int64()
                ),
                "pedestriancount": pa.array(
                    [row["pedestriancount"] for row in current_rows], type=pa.int64()
                ),
                "sensor_name": pa.array(
                    [row["sensor_name"] for row in current_rows], type=pa.string()
                ),
                "location": pa.array(
                    [row["location"] for row in current_rows], type=pa.binary()
                ),
            }
        )
        pq.write_table(current_table, current_parquet)

        sensor_ids = {
            int(row["Sensor_ID"]) for row in historical_rows
        } | {int(row["location_id"]) for row in wayback_rows} | {
            int(row["location_id"]) for row in current_rows
        }
        sensor_parquet = self.work / "sensor_locations.parquet"
        sensor_table = pa.table(
            {
                "location_id": pa.array(sorted(sensor_ids), type=pa.int64()),
                "sensor_description": pa.array(
                    [f"Fixture sensor {sensor_id}" for sensor_id in sorted(sensor_ids)],
                    type=pa.string(),
                ),
                "sensor_name": pa.array(
                    [f"Sensor {sensor_id}" for sensor_id in sorted(sensor_ids)],
                    type=pa.string(),
                ),
                "installation_date": pa.array(
                    [date(2009, 1, 1) for _ in sensor_ids], type=pa.date32()
                ),
                "note": pa.array([None for _ in sensor_ids], type=pa.string()),
                "location_type": pa.array(
                    ["Outdoor" for _ in sensor_ids], type=pa.string()
                ),
                "status": pa.array(["A" for _ in sensor_ids], type=pa.string()),
                "direction_1": pa.array(
                    ["North" for _ in sensor_ids], type=pa.string()
                ),
                "direction_2": pa.array(
                    ["South" for _ in sensor_ids], type=pa.string()
                ),
                "latitude": pa.array(
                    [-37.8 for _ in sensor_ids], type=pa.float64()
                ),
                "longitude": pa.array(
                    [144.96 for _ in sensor_ids], type=pa.float64()
                ),
                "location": pa.array(
                    [_wkb_point(-37.8, 144.96) for _ in sensor_ids], type=pa.binary()
                ),
            }
        )
        pq.write_table(sensor_table, sensor_parquet)
        return {
            "historical": historical_zip,
            "wayback": wayback_csv,
            "current": current_parquet,
            "sensors": sensor_parquet,
        }

    def _run_builder(self, inputs: Mapping[str, Path], label: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        output_dir = self.work / f"output-{label}"
        output_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--historical-zip",
                str(inputs["historical"]),
                "--wayback-csv",
                str(inputs["wayback"]),
                "--current-parquet",
                str(inputs["current"]),
                "--sensor-locations",
                str(inputs["sensors"]),
                "--output-dir",
                str(output_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed, output_dir

    def _read_output(self, output_dir: Path) -> tuple[list[dict[str, Any]], set[str]]:
        parquet_files = sorted(output_dir.rglob("*.parquet"))
        self.assertTrue(
            parquet_files,
            f"builder did not write a Parquet output below {output_dir}; "
            f"stdout/stderr should identify the output contract",
        )
        table = pq.read_table(parquet_files[0])
        return table.to_pylist(), set(table.column_names)

    def _assert_success(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            completed.returncode,
            0,
            f"builder failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def _assert_failure(self, completed: subprocess.CompletedProcess[str], pattern: str) -> None:
        self.assertNotEqual(
            completed.returncode,
            0,
            f"builder unexpectedly accepted invalid input\nstdout:\n{completed.stdout}",
        )
        self.assertRegex(
            f"{completed.stdout}\n{completed.stderr}",
            pattern,
            f"failure should explain {pattern!r}",
        )

    def test_schema_harmonizes_historical_wayback_and_current_rows(self) -> None:
        inputs = self._write_inputs(
            historical=[
                _historical_row(1, datetime(2022, 10, 31, 23), 7),
            ],
            wayback=[
                _wayback_row(1, "2022-11-01", 0, 2, 3),
            ],
            current=[
                _current_row(1, "2024-08-21", 0, 4, 5),
            ],
        )
        completed, output_dir = self._run_builder(inputs, "schema")
        self._assert_success(completed)
        rows, columns = self._read_output(output_dir)

        self.assertEqual(len(rows), 3)
        self.assertTrue(any(name in columns for name in KEY_FIELDS))
        self.assertTrue(any(name in columns for name in SENSOR_FIELDS))
        self.assertTrue(any(name in columns for name in DATE_FIELDS + TIMESTAMP_FIELDS))
        self.assertTrue(any(name in columns for name in COUNT_FIELDS))
        self.assertTrue(any(name in columns for name in DIRECTION_VALID_FIELDS))

        by_key = {_natural_key(row): row for row in rows}
        self.assertEqual(
            set(by_key),
            {
                (1, date(2022, 10, 31), 23),
                (1, date(2022, 11, 1), 0),
                (1, date(2024, 8, 21), 0),
            },
        )
        self.assertEqual(int(_field(by_key[(1, date(2022, 10, 31), 23)], COUNT_FIELDS)), 7)
        self.assertEqual(int(_field(by_key[(1, date(2022, 11, 1), 0)], COUNT_FIELDS)), 5)
        self.assertEqual(int(_field(by_key[(1, date(2024, 8, 21), 0)], COUNT_FIELDS)), 9)

        # Historical rows do not carry directional fields: unknown is not zero.
        historical = by_key[(1, date(2022, 10, 31), 23)]
        self.assertTrue(_is_missing(_field(historical, DIRECTION_1_FIELDS)))
        self.assertTrue(_is_missing(_field(historical, DIRECTION_2_FIELDS)))

        for key, row in by_key.items():
            if key[1] <= date(2022, 10, 31):
                self.assertTrue(_is_missing(_optional_field(row, LAT_FIELDS)))
                self.assertTrue(_is_missing(_optional_field(row, LON_FIELDS)))
                continue
            latitude, longitude = _coordinates(row)
            self.assertAlmostEqual(latitude, -37.8, places=6)
            self.assertAlmostEqual(longitude, 144.96, places=6)

    def test_date_windows_apply_source_precedence_at_boundaries(self) -> None:
        """Historical <= 2022-10-31, Wayback gap through 2024-08-20, current after it."""

        inputs = self._write_inputs(
            historical=[
                _historical_row(1, datetime(2022, 10, 31, 23), 10),
                # This conflicting copy must lose to the archived gap slice.
                _historical_row(1, datetime(2022, 11, 1, 0), 11),
            ],
            wayback=[
                _wayback_row(1, "2022-10-31", 23, 9, 11, count=20),
                _wayback_row(1, "2022-11-01", 0, 14, 16, count=30),
                _wayback_row(1, "2024-08-20", 23, 19, 21, count=40),
                # This conflicting copy must lose to the current portal slice.
                _wayback_row(1, "2024-08-21", 0, 24, 26, count=41),
            ],
            current=[
                _current_row(1, "2024-08-20", 23, 30, 12, count=42),
                _current_row(1, "2024-08-21", 0, 24, 26, count=50),
            ],
        )
        completed, output_dir = self._run_builder(inputs, "precedence")
        self._assert_success(completed)
        rows, _ = self._read_output(output_dir)
        by_key = {_natural_key(row): row for row in rows}

        expected = {
            (1, date(2022, 10, 31), 23): 10,
            (1, date(2022, 11, 1), 0): 30,
            (1, date(2024, 8, 20), 23): 40,
            (1, date(2024, 8, 21), 0): 50,
        }
        self.assertEqual(set(by_key), set(expected))
        for key, count in expected.items():
            self.assertEqual(int(_field(by_key[key], COUNT_FIELDS)), count)

    def test_duplicate_natural_key_is_rejected(self) -> None:
        inputs = self._write_inputs(
            wayback=[
                _wayback_row(1, "2023-01-02", 4, 1, 2, row_id="different-id-a"),
                _wayback_row(1, "2023-01-02", 4, 3, 4, row_id="different-id-b"),
            ],
        )
        completed, _ = self._run_builder(inputs, "duplicate")
        self._assert_failure(completed, r"(?i)(duplicate|natural.?key|unique)")

    def test_missing_directional_counts_are_not_zero_imputed(self) -> None:
        inputs = self._write_inputs(
            historical=[_historical_row(1, datetime(2022, 10, 31, 12), 123)],
            wayback=[_wayback_row(1, "2022-11-01", 12, 7, 8)],
            current=[_current_row(1, "2024-08-21", 12, 9, 10)],
        )
        completed, output_dir = self._run_builder(inputs, "missing-directions")
        self._assert_success(completed)
        rows, _ = self._read_output(output_dir)
        historical = next(
            row for row in rows if _natural_key(row) == (1, date(2022, 10, 31), 12)
        )
        self.assertEqual(int(_field(historical, COUNT_FIELDS)), 123)
        self.assertTrue(_is_missing(_field(historical, DIRECTION_1_FIELDS)))
        self.assertTrue(_is_missing(_field(historical, DIRECTION_2_FIELDS)))

    def test_direction_counts_are_validated_and_flagged_without_dropping_rows(self) -> None:
        inputs = self._write_inputs(
            wayback=[
                # Sum is correct but a directional count is negative.
                _wayback_row(1, "2023-01-03", 1, -1, 2, count=1),
                # Counts are non-negative but the total does not match their sum.
                _wayback_row(1, "2023-01-03", 2, 2, 2, count=5),
                _wayback_row(1, "2023-01-03", 3, 2, 3, count=5),
            ],
        )
        completed, output_dir = self._run_builder(inputs, "direction-validation")
        self._assert_success(completed)
        rows, _ = self._read_output(output_dir)
        by_key = {_natural_key(row): row for row in rows}
        self.assertEqual(len(by_key), 3)
        self.assertFalse(
            _as_bool(_field(by_key[(1, date(2023, 1, 3), 1)], DIRECTION_VALID_FIELDS))
        )
        self.assertFalse(
            _as_bool(_field(by_key[(1, date(2023, 1, 3), 2)], DIRECTION_VALID_FIELDS))
        )
        self.assertTrue(
            _as_bool(_field(by_key[(1, date(2023, 1, 3), 3)], DIRECTION_VALID_FIELDS))
        )

    def test_observation_keys_are_unique_and_stable_under_input_order(self) -> None:
        historical = [
            _historical_row(1, datetime(2022, 10, 31, 10), 10),
            _historical_row(1, datetime(2022, 10, 31, 11), 11),
        ]
        wayback = [
            _wayback_row(1, "2022-11-01", 10, 2, 3),
            _wayback_row(1, "2022-11-01", 11, 4, 5),
        ]
        current = [
            _current_row(1, "2024-08-21", 10, 6, 7),
            _current_row(1, "2024-08-21", 11, 8, 9),
        ]
        inputs_a = self._write_inputs(historical=historical, wayback=wayback, current=current)
        completed_a, output_a = self._run_builder(inputs_a, "stable-a")
        self._assert_success(completed_a)
        rows_a, _ = self._read_output(output_a)

        inputs_b = self._write_inputs(
            historical=historical,
            wayback=wayback,
            current=current,
            reverse_rows=True,
        )
        completed_b, output_b = self._run_builder(inputs_b, "stable-b")
        self._assert_success(completed_b)
        rows_b, _ = self._read_output(output_b)

        keys_a = {_natural_key(row): str(_field(row, KEY_FIELDS)) for row in rows_a}
        keys_b = {_natural_key(row): str(_field(row, KEY_FIELDS)) for row in rows_b}
        self.assertEqual(len(keys_a), len(rows_a))
        self.assertEqual(len(set(keys_a.values())), len(keys_a))
        self.assertEqual(keys_a, keys_b)

    def test_melbourne_daylight_saving_flag_uses_local_date_and_hour(self) -> None:
        inputs = self._write_inputs(
            wayback=[
                # AEDT in January, AEST in July; both dates are in the gap slice.
                _wayback_row(1, "2023-01-15", 12, 1, 2),
                _wayback_row(1, "2023-07-15", 12, 3, 4),
            ],
        )
        completed, output_dir = self._run_builder(inputs, "dst")
        self._assert_success(completed)
        rows, _ = self._read_output(output_dir)
        by_key = {_natural_key(row): row for row in rows}
        self.assertTrue(
            _as_bool(_field(by_key[(1, date(2023, 1, 15), 12)], DST_FIELDS))
        )
        self.assertFalse(
            _as_bool(_field(by_key[(1, date(2023, 7, 15), 12)], DST_FIELDS))
        )

    def test_verified_september_2010_hour_labels_are_reconstructed(self) -> None:
        historical = []
        raw_id = 1
        for day in range(1, 31):
            for actual_hour in range(24):
                for sensor_id in range(1, 18):
                    row = _historical_row(
                        sensor_id,
                        datetime(2010, 9, day, 0),
                        actual_hour * 100 + sensor_id,
                        row_id=raw_id,
                    )
                    historical.append(row)
                    raw_id += 1
        inputs = self._write_inputs(historical=historical)
        completed, output_dir = self._run_builder(inputs, "september-2010-repair")
        self._assert_success(completed)
        rows, _ = self._read_output(output_dir)

        self.assertEqual(len(rows), 12_240)
        sensor_one_day_one = [
            row for row in rows
            if _natural_key(row)[:2] == (1, date(2010, 9, 1))
        ]
        self.assertEqual(sorted(_date_hour(row)[1] for row in sensor_one_day_one), list(range(24)))
        self.assertTrue(all(_as_bool(_field(row, RECONSTRUCTED_FIELDS)) for row in rows))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
