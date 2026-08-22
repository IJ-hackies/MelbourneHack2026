"""Contract tests for the traffic dataset builder.

The traffic workstream deliberately remains a script rather than a Python
package, so these tests exercise the public CLI in subprocesses.  Fixtures use
the native shapes of the two publisher archives:

* City Transport Activity CSVs in ZIP archives;
* Victorian SCATS ``VSDATA`` CSVs, where ``QT_INTERVAL_COUNT`` is the date and
  ``V00`` through ``V95`` are quarter-hour detector volumes.

The output field names are allowed a small, explicit set of aliases.  The
semantic contract is kept strict: source rows are filtered by the approved
registry, counts are non-negative and deterministic, timestamps are explicit,
SCATS is converted from fixed AEST, and provenance/quality artifacts are
emitted.
"""

from __future__ import annotations

import csv
import contextlib
import datetime as dt
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # The builder's canonical output is Parquet.
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - minimal environments can still run --help.
    pq = None


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "ml" / "scripts" / "build_traffic_dataset.py"

TA_COLUMNS = [
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
]

SCATS_COLUMNS = [
    "NB_SCATS_SITE",
    "QT_INTERVAL_COUNT",
    "NB_DETECTOR",
    *(f"V{index:02d}" for index in range(96)),
    "NM_REGION",
    "CT_RECORDS",
    "QT_VOLUME_24HOUR",
    "CT_ALARM_24HOUR",
]

REGISTRY_COLUMNS = [
    "count_location_id",
    "countline_name",
    "channel_type",
    "traffic_eligible",
    "review_status",
    "notes",
]

SIGNAL_COLUMNS = [
    "SITE_NO",
    "SITE_NAME",
    "TYPE",
    "MUNICIPALITY",
    "LATITUDE",
    "LONGITUDE",
]


# Output aliases are intentionally narrow.  They cover normal naming choices
# without accepting an unrelated field such as a source row number as a count.
TA_COUNT_FIELDS = (
    "motor_vehicle_count",
    "motor_count",
    "motorised_count",
    "motorized_count",
    "vehicle_count",
    "traffic_count",
    "hourly_vehicle_count",
    "hourly_traffic_count",
    "count_total",
    "total_count",
    "volume",
    "count",
)
SCATS_COUNT_FIELDS = (
    "scats_vehicle_count",
    "scats_count",
    "vehicle_count",
    "traffic_volume",
    "hourly_volume",
    "hourly_count",
    "count_total",
    "total_count",
    "volume",
    "count",
)
MOTOR_COUNT_FIELDS = (
    "motor_vehicle_count",
    "motor_count",
    "motorised_count",
    "motorized_count",
)
LOCAL_DATE_FIELDS = (
    "local_date",
    "melbourne_date",
    "date_local",
    "observed_date_local",
)
LOCAL_HOUR_FIELDS = (
    "local_hour",
    "melbourne_hour",
    "hour_local",
    "hour_of_day",
)
UTC_TIMESTAMP_FIELDS = (
    "hour_start_utc",
    "timestamp_utc",
    "observed_at_utc",
    "utc_timestamp",
    "timestamp",
    "observed_at",
    "interval_start_utc",
)
LOCAL_TIMESTAMP_FIELDS = (
    "timestamp_local",
    "observed_at_local",
    "local_timestamp",
)
ROAD_FIELDS = (
    "road_name",
    "road",
    "countline_name",
    "countline",
    "countlineName",
    "channel_name",
)
SITE_FIELDS = (
    "scats_site",
    "site_id",
    "site",
    "nb_scats_site",
    "site_no",
    "signal_site",
)
DETECTOR_FIELDS = (
    "detector_id",
    "detector",
    "nb_detector",
)
LAT_FIELDS = ("latitude", "lat", "signal_latitude")
LON_FIELDS = ("longitude", "lon", "lng", "signal_longitude")
MAPPED_FIELDS = (
    "mapped_status",
    "mapping_status",
    "coordinate_status",
    "mapped",
    "coordinates_mapped",
    "is_mapped",
)
DERIVED_ZERO_FIELDS = (
    "derived_zero",
    "zero_derived",
    "is_derived_zero",
    "count_derived",
    "derived_from_other_class",
)
SOURCE_FIELDS = ("source", "source_type", "dataset", "source_dataset", "family")


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _field(row: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    """Return a field using exact normalised aliases, then a conservative match."""

    by_normalised = {_normalise(name): value for name, value in row.items()}
    for name in names:
        wanted = _normalise(name)
        if wanted in by_normalised:
            return by_normalised[wanted]
    return default


def _field_name(row: Mapping[str, Any], names: Sequence[str]) -> str | None:
    by_normalised = {_normalise(name): name for name in row}
    for name in names:
        if _normalise(name) in by_normalised:
            return by_normalised[_normalise(name)]
    return None


def _is_missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _as_number(value: Any) -> float:
    if _is_missing(value):
        raise AssertionError(f"expected numeric output value, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"expected numeric output value, got {value!r}") from exc
    if not math.isfinite(number):
        raise AssertionError(f"expected finite output value, got {value!r}")
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "ok", "valid", "mapped", "dst"}:
        return True
    if text in {"0", "false", "no", "n", "invalid", "unmapped", "missing", "none"}:
        return False
    raise AssertionError(f"cannot interpret boolean output value {value!r}")


def _as_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed


def _date_hour(row: Mapping[str, Any], *, local: bool) -> tuple[dt.date, int]:
    if local:
        date_value = _field(row, LOCAL_DATE_FIELDS)
        hour_value = _field(row, LOCAL_HOUR_FIELDS)
        timestamp_fields = LOCAL_TIMESTAMP_FIELDS
    else:
        date_value = _field(row, ("utc_date", "date_utc"))
        hour_value = _field(row, ("utc_hour", "hour_utc"))
        timestamp_fields = UTC_TIMESTAMP_FIELDS
    if date_value is not None and hour_value is not None:
        parsed_date = _as_datetime(date_value).date()
        return parsed_date, int(float(hour_value))
    timestamp = _field(row, timestamp_fields)
    if timestamp is None:
        raise AssertionError(f"output has no {'local' if local else 'UTC'} timestamp: {sorted(row)}")
    parsed = _as_datetime(timestamp)
    return parsed.date(), parsed.hour


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _write_zip_csv(
    path: Path,
    member_name: str,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(columns),
            lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        archive.writestr(member_name, buffer.getvalue().encode("utf-8"))
    return path


def _write_zip_csv_members(
    path: Path,
    members: Sequence[tuple[str, Sequence[str], Iterable[Mapping[str, Any]]]],
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member_name, columns, rows in members:
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(
                buffer,
                fieldnames=list(columns),
                lineterminator="\r\n",
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(member_name, buffer.getvalue().encode("utf-8"))
    return path


def _ta_row(
    countline: str,
    observed_at: str,
    vehicle_class: str,
    count: Any,
    *,
    location_id: int | None = None,
    latitude: Any = -37.800000,
    longitude: Any = 144.960000,
    direction: str = "CLOCKWISE_AND_ANTICLOCKWISE",
) -> dict[str, Any]:
    if location_id is None:
        location_id = {
            "King_Street_bn003": 1001,
            "Queen_Street_bn003": 1002,
            "Null_Coordinates_bn003": 1003,
            "Silent_Street_bn003": 1004,
            "Unapproved_Street_bn003": 1005,
            "Ambiguous_Street_bn003": 1006,
        }.get(countline, 1001)
    start = _as_datetime(observed_at)
    end = start + dt.timedelta(minutes=5)
    return {
        "countLocationId": location_id,
        "countlineName": countline,
        "countlineDirection": direction,
        "CountLocationLat": latitude,
        "CountLocationLong": longitude,
        "from": observed_at,
        "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "class": vehicle_class,
        "count": count,
        "year": start.year,
        "quarter": (start.month - 1) // 3 + 1,
    }


def _scats_row(
    site: int,
    detector: int,
    *,
    date_value: str = "2026-01-15",
    values: Mapping[int, Any] | None = None,
    ct_records: Any = 96,
    alarm: Any = 0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "NB_SCATS_SITE": site,
        "QT_INTERVAL_COUNT": date_value,
        "NB_DETECTOR": detector,
        "NM_REGION": "WV1",
        "CT_RECORDS": ct_records,
        "QT_VOLUME_24HOUR": "0",
        "CT_ALARM_24HOUR": alarm,
    }
    values = values or {}
    # Fixture call sites express the first hour as minute offsets. Publisher
    # columns are quarter-hour ordinal slots, so 00/15/30/45 map to V00..V03.
    if values and set(values).issubset({0, 15, 30, 45}):
        values = {index // 15: value for index, value in values.items()}
    for index in range(96):
        row[f"V{index:02d}"] = values.get(index, 0)
    return row


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def _delayed_stage_task_worker(task: tuple[int, float, str]) -> dict[str, Any]:
    """Spawn-picklable test worker whose completion order is intentional."""

    index, delay, source_dataset_id = task
    time.sleep(delay)
    return {
        "index": index,
        "kind": "test",
        "source_dataset_id": source_dataset_id,
        "path": f"stage-{index}.parquet",
        "metrics": {"staged_rows": index + 1},
    }


def _timed_logical_unit_worker(task: Any) -> dict[str, Any]:
    """Spawn-picklable worker used to prove nested units overlap."""

    if hasattr(task, "unit"):
        index = int(task.index)
        label = task.unit.label
    else:
        index, _delay, label = task
    started = time.monotonic()
    time.sleep(0.8)
    finished = time.monotonic()
    return {
        "index": index,
        "kind": "scats",
        "source_dataset_id": "fixture-annual",
        "work_unit_label": label,
        "path": f"stage-{index}.parquet",
        "started": started,
        "finished": finished,
        "metrics": {"staged_rows": 1},
    }


def _registry_rows(*, ambiguous: bool = False) -> list[dict[str, Any]]:
    rows = [
        {
            "count_location_id": "1001",
            "countline_name": "King_Street_bn003",
            "channel_type": "road",
            "traffic_eligible": "true",
            "review_status": "approved",
            "notes": "fixture approved road",
        },
        {
            "count_location_id": "1002",
            "countline_name": "Queen_Street_bn003",
            "channel_type": "road",
            "traffic_eligible": "true",
            "review_status": "approved",
            "notes": "fixture approved road",
        },
        {
            "count_location_id": "1003",
            "countline_name": "Null_Coordinates_bn003",
            "channel_type": "road",
            "traffic_eligible": "true",
            "review_status": "approved",
            "notes": "fixture approved road",
        },
        {
            "count_location_id": "1004",
            "countline_name": "Silent_Street_bn003",
            "channel_type": "road",
            "traffic_eligible": "true",
            "review_status": "approved",
            "notes": "fixture approved road",
        },
        {
            "count_location_id": "1005",
            "countline_name": "Unapproved_Street_bn003",
            "channel_type": "other",
            "traffic_eligible": "false",
            "review_status": "excluded",
            "notes": "fixture excluded channel",
        },
    ]
    if ambiguous:
        rows.extend(
            [
                {
                    "count_location_id": "1006",
                    "countline_name": "Ambiguous_Street_bn003",
                    "channel_type": "road",
                    "traffic_eligible": "true",
                    "review_status": "approved",
                    "notes": "first conflicting registry row",
                },
                {
                    "count_location_id": "1006",
                    "countline_name": "Ambiguous_Street_bn003",
                    "channel_type": "road",
                    "traffic_eligible": "true",
                    "review_status": "approved",
                    "notes": "second conflicting registry row",
                },
            ]
        )
    return rows


def _signal_rows() -> list[dict[str, Any]]:
    return [
        {
            "SITE_NO": 1001,
            "SITE_NAME": "Fixture In-Bbox Signal",
            "TYPE": "INT",
            "MUNICIPALITY": "MEL",
            "LATITUDE": -37.800000,
            "LONGITUDE": 144.960000,
        },
        {
            "SITE_NO": 1002,
            "SITE_NAME": "Fixture Out-of-Bbox Signal",
            "TYPE": "INT",
            "MUNICIPALITY": "OUT",
            "LATITUDE": -37.700000,
            "LONGITUDE": 145.500000,
        },
    ]


class BuildTrafficDatasetTests(unittest.TestCase):
    """Small source-shaped fixtures for ``build_traffic_dataset.py``."""

    def setUp(self) -> None:
        if pq is None:
            self.skipTest("pyarrow is required to inspect Parquet output")
        self.assertTrue(BUILDER.exists(), f"expected traffic builder at {BUILDER}")
        self.tempdir = tempfile.TemporaryDirectory(prefix="traffic-builder-")
        self.work = Path(self.tempdir.name)

    def tearDown(self) -> None:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def _help(self) -> str:
        completed = subprocess.run(
            [sys.executable, str(BUILDER), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"traffic builder --help failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return f"{completed.stdout}\n{completed.stderr}"

    def _choose_option(
        self,
        help_text: str,
        options: Sequence[str],
        *,
        required: bool = True,
    ) -> str | None:
        for option in options:
            if re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", help_text):
                return option
        if required:
            self.fail(f"builder help does not expose any of {tuple(options)!r}\n{help_text}")
        return None

    def _write_fixture_inputs(
        self,
        *,
        ta_rows: Iterable[Mapping[str, Any]],
        scats_rows: Iterable[Mapping[str, Any]],
        registry_rows: Iterable[Mapping[str, Any]] | None = None,
        signal_rows: Iterable[Mapping[str, Any]] | None = None,
        label: str = "fixture",
    ) -> dict[str, Path]:
        ta_zip = self.work / f"{label}-transport_activity.zip"
        _write_zip_csv(ta_zip, "TransportActivityCount_2026-1.csv", TA_COLUMNS, ta_rows)
        scats_zip = self.work / f"{label}-scats.zip"
        _write_zip_csv(scats_zip, "VSDATA_20260115.csv", SCATS_COLUMNS, scats_rows)
        registry = _write_csv(
            self.work / f"{label}-approved_registry.csv",
            REGISTRY_COLUMNS,
            registry_rows if registry_rows is not None else _registry_rows(),
        )
        signals = _write_csv(
            self.work / f"{label}-scats_signal_locations.csv",
            SIGNAL_COLUMNS,
            signal_rows if signal_rows is not None else _signal_rows(),
        )
        return {
            "ta": ta_zip,
            "scats": scats_zip,
            "registry": registry,
            "signals": signals,
        }

    def _command(
        self,
        inputs: Mapping[str, Path],
        output_dir: Path,
        *,
        allow_partial: bool = False,
        preview_rows: int | None = None,
        bbox: str | None = "144.94,-37.83,144.98,-37.78",
        ta_archives: Sequence[Path] | None = None,
        scats_archives: Sequence[Path] | None = None,
        expected_config: Path | None = None,
        chunk_size: int | None = None,
        workers: int = 1,
    ) -> list[str]:
        help_text = self._help()
        command = [sys.executable, str(BUILDER)]

        ta_option = self._choose_option(
            help_text,
            ("--ta-zip", "--ta-zips", "--transport-activity-zip", "--transport-activity", "--ta"),
        )
        scats_option = self._choose_option(
            help_text,
            ("--scats-zip", "--scats-zips", "--scats", "--scats-archive"),
        )
        registry_option = self._choose_option(
            help_text,
            ("--approved-registry", "--registry", "--road-registry", "--channel-registry"),
        )
        signal_option = self._choose_option(
            help_text,
            (
                "--scats-signal-locations",
                "--signal-locations",
                "--scats-signals",
                "--signals",
            ),
        )
        output_option = self._choose_option(
            help_text,
            ("--output-dir", "--output", "--destination"),
        )
        ta_paths = list(ta_archives) if ta_archives is not None else [inputs["ta"]]
        scats_paths = list(scats_archives) if scats_archives is not None else [inputs["scats"]]
        for path in ta_paths:
            command.extend([ta_option, str(path)])
        for path in scats_paths:
            command.extend([scats_option, str(path)])
        command.extend([registry_option, str(inputs["registry"])])
        command.extend([signal_option, str(inputs["signals"])])
        command.extend([output_option, str(output_dir)])
        workers_option = self._choose_option(help_text, ("--workers",))
        command.extend([workers_option, str(workers)])
        command.extend(["--expected-config", str(expected_config or self.work / "fixture-no-coverage-config.json")])
        for path in ta_paths:
            command.extend(["--expected-ta-source", path.stem])
        command.extend(["--expected-scats-year", "2026"])

        if allow_partial:
            self.assertRegex(help_text, r"(?<![\w-])--allow-partial(?![\w-])")
            command.extend(["--expected-ta-source", "intentionally_missing_fixture_source"])
            command.append("--allow-partial")
        if preview_rows is not None:
            preview_option = self._choose_option(
                help_text,
                ("--preview-rows", "--preview"),
                required=False,
            )
            if preview_option is not None:
                command.extend([preview_option, str(preview_rows)])
        if bbox is not None:
            bbox_option = self._choose_option(help_text, ("--bbox", "--bounding-box"), required=False)
            if bbox_option is not None:
                command.extend([bbox_option, bbox])
        if chunk_size is not None:
            chunk_option = self._choose_option(help_text, ("--chunk-size", "--chunksize"), required=False)
            if chunk_option is not None:
                command.extend([chunk_option, str(chunk_size)])
        return command

    def _run(
        self,
        inputs: Mapping[str, Path],
        label: str,
        *,
        allow_partial: bool = False,
        preview_rows: int | None = None,
        bbox: str | None = "144.94,-37.83,144.98,-37.78",
        ta_archives: Sequence[Path] | None = None,
        scats_archives: Sequence[Path] | None = None,
        expected_config: Path | None = None,
        chunk_size: int | None = None,
        workers: int = 1,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        output_dir = self.work / f"output-{label}"
        output_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            self._command(
                inputs,
                output_dir,
                allow_partial=allow_partial,
                preview_rows=preview_rows,
                bbox=bbox,
                ta_archives=ta_archives,
                scats_archives=scats_archives,
                expected_config=expected_config,
                chunk_size=chunk_size,
                workers=workers,
            ),
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed, output_dir

    def _assert_success(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            completed.returncode,
            0,
            f"traffic builder failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def _assert_failure(self, completed: subprocess.CompletedProcess[str], pattern: str) -> None:
        self.assertNotEqual(
            completed.returncode,
            0,
            f"traffic builder unexpectedly accepted invalid input\nstdout:\n{completed.stdout}",
        )
        self.assertRegex(
            f"{completed.stdout}\n{completed.stderr}",
            pattern,
            f"failure should explain {pattern!r}",
        )

    def _artifacts(self, output_dir: Path) -> list[tuple[Path, list[dict[str, Any]], set[str]]]:
        artifacts: list[tuple[Path, list[dict[str, Any]], set[str]]] = []
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or "preview" in path.stem.lower():
                continue
            if path.suffix.lower() == ".parquet":
                table = pq.read_table(path)
                artifacts.append((path, table.to_pylist(), set(table.column_names)))
            elif path.suffix.lower() == ".csv":
                with path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    rows = [dict(row) for row in reader]
                    artifacts.append((path, rows, set(reader.fieldnames or ())))
        self.assertTrue(artifacts, f"builder wrote no readable table below {output_dir}")
        return artifacts

    def _all_rows(self, output_dir: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _path, artifact_rows, _columns in self._artifacts(output_dir):
            rows.extend(artifact_rows)
        return rows

    def _rows_with_text(self, rows: Iterable[Mapping[str, Any]], text: str) -> list[dict[str, Any]]:
        needle = text.casefold()
        return [
            dict(row)
            for row in rows
            if needle in _flatten_text(row).casefold()
        ]

    def _rows_with_any_text(
        self,
        rows: Iterable[Mapping[str, Any]],
        *texts: str,
    ) -> list[dict[str, Any]]:
        needles = tuple(text.casefold() for text in texts)
        return [
            dict(row)
            for row in rows
            if any(needle in _flatten_text(row).casefold() for needle in needles)
        ]

    def _primary_artifact(
        self,
        output_dir: Path,
        *,
        kind: str,
    ) -> tuple[Path, list[dict[str, Any]], set[str]]:
        artifacts = self._artifacts(output_dir)
        tokens = {
            "ta": ("transport", "activity", "ta", "motor", "countline", "road"),
            "scats": ("scats", "detector", "signal", "v00", "site"),
        }[kind]
        scored: list[tuple[int, int, tuple[Path, list[dict[str, Any]], set[str]]]] = []
        for artifact in artifacts:
            path, rows, columns = artifact
            text = " ".join([path.stem.lower(), *(_normalise(name) for name in columns)])
            score = sum(token in text for token in tokens)
            scored.append((score, len(rows), artifact))
        scored.sort(key=lambda item: (-item[0], -item[1], str(item[2][0])))
        return scored[0][2]

    def _manifest(self, output_dir: Path) -> tuple[Path, dict[str, Any]]:
        paths = sorted(
            path
            for path in output_dir.rglob("*.json")
            if "manifest" in path.stem.lower()
        )
        self.assertTrue(paths, f"builder wrote no manifest below {output_dir}")
        path = paths[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        return path, payload

    def _assert_manifest_sources(self, output_dir: Path, inputs: Mapping[str, Path]) -> None:
        _path, payload = self._manifest(output_dir)
        text = json.dumps(payload, sort_keys=True, default=str).lower()
        self.assertRegex(text, r"sha.?256|checksum")
        for source in inputs.values():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertIn(
                digest,
                text,
                f"manifest omitted checksum for {source.name}; payload={payload}",
            )

    def _assert_row_local_and_utc(
        self,
        row: Mapping[str, Any],
        *,
        utc_hour: tuple[dt.date, int],
        local_hour: tuple[dt.date, int],
    ) -> None:
        self.assertEqual(_date_hour(row, local=False), utc_hour)
        self.assertEqual(_date_hour(row, local=True), local_hour)

    def _row_count(self, row: Mapping[str, Any], fields: Sequence[str]) -> float:
        value = _field(row, fields)
        if value is None:
            self.fail(f"output row has no count field among {tuple(fields)!r}: {sorted(row)}")
        return _as_number(value)

    def _assert_not_mapped(self, row: Mapping[str, Any]) -> None:
        mapped_name = _field_name(row, MAPPED_FIELDS)
        if mapped_name is not None:
            mapped_value = _field(row, MAPPED_FIELDS)
            if isinstance(mapped_value, str) and mapped_value.casefold() in {
                "unmapped",
                "outside_bbox",
                "missing_coordinates",
                "coordinate_missing",
                "unmatched",
            }:
                return
            self.assertFalse(_as_bool(mapped_value), f"null/out-of-bbox row was mapped: {row}")
            return
        lat = _field(row, LAT_FIELDS)
        lon = _field(row, LON_FIELDS)
        self.assertTrue(
            _is_missing(lat) or _is_missing(lon),
            f"row without a mapping flag retained coordinates: {row}",
        )

    def test_malformed_non_utf8_header_reports_member_context(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        with self.assertRaisesRegex(ValueError, r"bad-header\.csv"):
            traffic_builder._header_from_handle(
                io.BytesIO(b"name,\xff\n"),
                member="bad-header.csv",
            )

    def test_transport_intervals_require_five_minute_alignment_and_duration(self) -> None:
        cases = [
            (
                "ten-minute",
                _ta_row("King_Street_bn003", "2026-06-01T13:00:00Z", "car", 1),
            ),
            (
                "misaligned",
                _ta_row("King_Street_bn003", "2026-06-01T13:02:00Z", "car", 1),
            ),
        ]
        cases[0][1]["to"] = "2026-06-01T13:10:00.000Z"
        for label, invalid_row in cases:
            with self.subTest(label=label):
                inputs = self._write_fixture_inputs(
                    ta_rows=[invalid_row],
                    scats_rows=[_scats_row(1001, 1, values={0: 1})],
                    label=f"invalid-interval-{label}",
                )
                completed, _output_dir = self._run(inputs, f"invalid-interval-{label}")
                self._assert_failure(completed, r"five-minute|aligned|duration|interval")

    def test_transport_overlapping_intervals_fail_closed(self) -> None:
        first = _ta_row("King_Street_bn003", "2026-06-01T13:00:00Z", "car", 1)
        second = _ta_row("King_Street_bn003", "2026-06-01T13:02:00Z", "car", 2)
        inputs = self._write_fixture_inputs(
            ta_rows=[first, second],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
            label="overlapping-ta-intervals",
        )
        completed, _output_dir = self._run(inputs, "overlapping-ta-intervals")
        self._assert_failure(completed, r"overlapping|five-minute|aligned|interval")

    def test_input_snapshot_revalidation_detects_same_size_mutation(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        source = self.work / "immutable-input.csv"
        source.write_bytes(b"abc")
        snapshot = traffic_builder._file_snapshot(source)
        source.write_bytes(b"xyz")
        with self.assertRaisesRegex(ValueError, r"immutable input changed.*fixture"):
            traffic_builder._revalidate_input_snapshots(
                [{"kind": "fixture", **snapshot}],
                "focused test",
            )

    def test_publication_without_overwrite_never_replaces_existing_output(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        destination = self.work / "published.parquet"
        staged = self.work / "staged.parquet"
        destination.write_bytes(b"old")
        staged.write_bytes(b"new")
        with self.assertRaisesRegex(FileExistsError, r"published concurrently"):
            traffic_builder._publish_staged_file(staged, destination, overwrite=False)
        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(staged.read_bytes(), b"new")

    def test_concurrent_publication_lock_fails_closed(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        lock = self.work / "publication.lock"
        with traffic_builder._exclusive_publish_lock(lock):
            with self.assertRaisesRegex(FileExistsError, r"already in progress"):
                with traffic_builder._exclusive_publish_lock(lock):
                    pass

    def test_transport_filter_motor_sum_derived_zero_and_utc_local_fields(self) -> None:
        """Approved roads sum motor classes; sparse zeroes need other-class evidence."""

        ta_rows = [
            _ta_row("King_Street_bn003", "2026-06-01T13:05:00Z", "car", 2),
            _ta_row("King_Street_bn003", "2026-06-01T13:55:00Z", "truck", 3),
            # No motor row for Queen, but a same-countline cyclist observation
            # proves that the interval was observed and supports derived zero.
            _ta_row("Queen_Street_bn003", "2026-06-01T13:15:00Z", "cyclist", 7),
            # This must not make a row for Silent Street: the countline is truly silent.
            _ta_row("Unapproved_Street_bn003", "2026-06-01T13:25:00Z", "car", 99),
        ]
        scats_rows = [
            _scats_row(1001, 1, values={0: 1, 15: 1, 30: 1, 45: 1}),
        ]
        inputs = self._write_fixture_inputs(ta_rows=ta_rows, scats_rows=scats_rows)
        completed, output_dir = self._run(inputs, "transport-contract")
        self._assert_success(completed)
        ta_path, ta_rows_out, _columns = self._primary_artifact(output_dir, kind="ta")
        self.assertTrue(ta_rows_out, f"no Transport Activity rows in {ta_path}")

        king_rows = self._rows_with_any_text(ta_rows_out, "King_Street_bn003", "King Street")
        if not king_rows:
            king_rows = self._rows_with_any_text(
                self._all_rows(output_dir), "King_Street_bn003", "King Street"
            )
        self.assertTrue(king_rows, f"approved King countline missing from {ta_path}")
        king = king_rows[0]
        self.assertEqual(self._row_count(king, TA_COUNT_FIELDS), 5)
        self._assert_row_local_and_utc(
            king,
            utc_hour=(dt.date(2026, 6, 1), 3),
            local_hour=(dt.date(2026, 6, 1), 13),
        )

        queen_rows = self._rows_with_any_text(ta_rows_out, "Queen_Street_bn003", "Queen Street")
        if not queen_rows:
            queen_rows = self._rows_with_any_text(
                self._all_rows(output_dir), "Queen_Street_bn003", "Queen Street"
            )
        self.assertTrue(queen_rows, "same-countline other-class evidence should produce Queen zero")
        queen = queen_rows[0]
        self.assertEqual(self._row_count(queen, TA_COUNT_FIELDS), 0)
        derived_name = _field_name(queen, DERIVED_ZERO_FIELDS)
        if derived_name is not None:
            self.assertTrue(_as_bool(_field(queen, DERIVED_ZERO_FIELDS)))
        else:
            quality_text = _flatten_text(queen).casefold()
            self.assertRegex(quality_text, r"derived.?zero|other.?class|inferred")

        self.assertFalse(
            self._rows_with_any_text(ta_rows_out, "Silent_Street_bn003", "Silent Street"),
            "a countline with no observations must not become an imputed zero row",
        )
        self.assertFalse(
            self._rows_with_any_text(
                ta_rows_out, "Unapproved_Street_bn003", "Unapproved Street"
            ),
            "unapproved registry channels must be excluded",
        )

    def test_scats_aest_hour_bins_quality_flags_signal_join_and_bbox(self) -> None:
        """SCATS bins use fixed AEST and aggregate all detectors at a site/hour."""

        scats_rows = [
            _scats_row(
                1001,
                1,
                values={0: 1, 15: 2, 30: 3, 45: 4},
                ct_records=95,
                alarm=1,
            ),
            _scats_row(
                1001,
                2,
                values={0: 10, 15: 20, 30: 30, 45: 40},
                ct_records=96,
                alarm=0,
            ),
            _scats_row(
                1002,
                1,
                values={0: 100, 15: 100, 30: 100, 45: 100},
            ),
        ]
        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=scats_rows,
        )
        completed, output_dir = self._run(inputs, "scats-contract")
        self._assert_success(completed)
        _path, scats_rows_out, _columns = self._primary_artifact(output_dir, kind="scats")
        all_rows = self._all_rows(output_dir)
        if not any("1001" in _flatten_text(row) for row in scats_rows_out):
            scats_rows_out = [row for row in all_rows if "1001" in _flatten_text(row)]
        site_rows = [row for row in scats_rows_out if "1001" in _flatten_text(row)]
        self.assertTrue(site_rows, "signal site 1001 was not retained")
        site = site_rows[0]
        self.assertEqual(self._row_count(site, SCATS_COUNT_FIELDS), 110)
        # January deliberately verifies that SCATS source time is fixed AEST
        # (+10): source midnight is 14:00 UTC and therefore 01:00 in Melbourne
        # civil daylight time (+11).
        self._assert_row_local_and_utc(
            site,
            utc_hour=(dt.date(2026, 1, 14), 14),
            local_hour=(dt.date(2026, 1, 15), 1),
        )
        detector_name = _field_name(site, DETECTOR_FIELDS)
        if detector_name is not None:
            # A site/hour aggregate may retain a detector count rather than an
            # individual detector id; either way both input detectors must count.
            detector_value = _field(site, DETECTOR_FIELDS)
            if str(detector_value) not in {"1", "2"}:
                self.assertEqual(_as_number(detector_value), 2)

        quality_text = _flatten_text(site).casefold()
        self.assertRegex(quality_text, r"alarm")
        self.assertRegex(quality_text, r"ct.?records|record.?quality")

        lat = _field(site, LAT_FIELDS)
        lon = _field(site, LON_FIELDS)
        self.assertAlmostEqual(_as_number(lat), -37.8, places=5)
        self.assertAlmostEqual(_as_number(lon), 144.96, places=5)

        outside_rows = [row for row in scats_rows_out if "1002" in _flatten_text(row)]
        if outside_rows:
            self._assert_not_mapped(outside_rows[0])

    def test_scats_bbox_filters_detector_chunks_but_raw_dates_drive_coverage(self) -> None:
        """Out-of-bbox detector rows are dropped early without hiding their dates."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, date_value="2026-01-15", values={0: 2})],
            label="bbox-early",
        )
        _write_zip_csv_members(
            inputs["scats"],
            [
                ("VSDATA_20260115.csv", SCATS_COLUMNS, [_scats_row(1001, 1, date_value="2026-01-15", values={0: 2})]),
                ("VSDATA_20260116.csv", SCATS_COLUMNS, [_scats_row(1002, 1, date_value="2026-01-16", values={0: 3})]),
            ],
        )
        expected_config = self.work / "bbox-coverage.json"
        expected_config.write_text(
            json.dumps(
                {
                    "scats_expected_start": "2026-01-15",
                    "scats_expected_end": "2026-01-16",
                }
            ),
            encoding="utf-8",
        )
        completed, output_dir = self._run(
            inputs,
            "bbox-early",
            expected_config=expected_config,
        )
        self._assert_success(completed)
        rows = self._all_rows(output_dir)
        self.assertTrue(rows)
        self.assertFalse(any("1002" in _flatten_text(row) for row in rows))
        _manifest_path, manifest = self._manifest(output_dir)
        source = next(item for item in manifest["sources"] if item["kind"] == "scats")
        self.assertEqual(source["raw_rows"], 2)
        self.assertEqual(source["selected_rows"], 1)
        self.assertEqual(source["bbox_excluded_rows"], 1)
        self.assertEqual(manifest["coverage"]["missing_scats_date_count"], 0)
        self.assertEqual(manifest["coverage"]["unexpected_missing_scats_date_count"], 0)
        self.assertFalse(list(output_dir.glob(".traffic-staging-*")))

    def test_nested_scats_zip_members_are_discovered_processed_and_covered(self) -> None:
        """Annual-style outer ZIPs stream nested monthly CSV members."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 2})],
            label="nested-scats",
        )
        inner_january = self.work / "inner-january.zip"
        inner_february = self.work / "inner-february.zip"
        _write_zip_csv(
            inner_january,
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-15", values={0: 2})],
        )
        _write_zip_csv(
            inner_february,
            "VSDATA_20260116.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-16", values={0: 3})],
        )
        with zipfile.ZipFile(inputs["scats"], "w", compression=zipfile.ZIP_DEFLATED) as outer:
            outer.writestr("2026-01.zip", inner_january.read_bytes())
            outer.writestr("2026-02.zip", inner_february.read_bytes())
        expected_config = self.work / "nested-coverage.json"
        expected_config.write_text(
            json.dumps(
                {
                    "scats_expected_start": "2026-01-15",
                    "scats_expected_end": "2026-01-16",
                }
            ),
            encoding="utf-8",
        )
        completed, output_dir = self._run(
            inputs,
            "nested-scats",
            expected_config=expected_config,
        )
        self._assert_success(completed)
        rows = self._all_rows(output_dir)
        scats_rows = [row for row in rows if "scats" in _flatten_text(row).casefold()]
        self.assertGreaterEqual(len(scats_rows), 2)
        self.assertTrue(any("2026-01.zip::VSDATA_20260115.csv" in _flatten_text(row) for row in scats_rows))
        self.assertTrue(any("2026-02.zip::VSDATA_20260116.csv" in _flatten_text(row) for row in scats_rows))
        _manifest_path, manifest = self._manifest(output_dir)
        source = next(item for item in manifest["sources"] if item["kind"] == "scats")
        self.assertEqual(source["raw_rows"], 2)
        self.assertEqual(source["min_date"], "2026-01-15")
        self.assertEqual(source["max_date"], "2026-01-16")
        staging = manifest["staging"]
        self.assertEqual(staging["work_unit_counts"], {"transport_activity": 1, "scats": 2})
        self.assertEqual(staging["work_unit_count"], 3)
        fanout = next(
            item
            for item in staging["source_unit_fanout"]
            if item["source_dataset_id"] == source["source_dataset_id"]
        )
        self.assertEqual(fanout["work_unit_count"], 2)
        self.assertEqual(fanout["nested_member_count"], 2)
        self.assertEqual(
            fanout["work_unit_labels"],
            [
                f"{source['source_dataset_id']}::2026-01.zip",
                f"{source['source_dataset_id']}::2026-02.zip",
            ],
        )
        self.assertEqual(manifest["coverage"]["missing_scats_date_count"], 0)
        self.assertFalse(list(output_dir.glob(".traffic-staging-*")))

    def test_nested_logical_units_overlap_in_shared_pool(self) -> None:
        """Independent annual-month units use the global worker budget together."""

        from ml.scripts import build_traffic_dataset as traffic_builder

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 2})],
            label="overlap-scats",
        )
        inner_january = self.work / "overlap-january.zip"
        inner_february = self.work / "overlap-february.zip"
        _write_zip_csv(
            inner_january,
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-15", values={0: 2})],
        )
        _write_zip_csv(
            inner_february,
            "VSDATA_20260116.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-16", values={0: 3})],
        )
        with zipfile.ZipFile(inputs["scats"], "w", compression=zipfile.ZIP_DEFLATED) as outer:
            outer.writestr("2026-01.zip", inner_january.read_bytes())
            outer.writestr("2026-02.zip", inner_february.read_bytes())

        archive_info = traffic_builder.ArchiveInfo(
            inputs["scats"].resolve(),
            inputs["scats"].stem,
            "scats",
        )
        units = traffic_builder.enumerate_scats_logical_units([archive_info])
        self.assertEqual(
            [unit.nested_member for unit in units],
            ["2026-01.zip", "2026-02.zip"],
        )
        tasks = [
            traffic_builder.StageTask(
                index=index,
                unit=unit,
                chunk_size=1,
                stage_path=self.work / f"unused-stage-{index}.parquet",
            )
            for index, unit in enumerate(units)
        ]
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            results, effective, strategy = traffic_builder._run_stage_tasks(
                tasks,
                requested_workers=2,
                worker_entry=_timed_logical_unit_worker,
            )
        elapsed = time.monotonic() - started
        self.assertEqual(effective, 2)
        self.assertEqual(strategy, "shared_spawn_process_pool")
        self.assertLess(
            max(results[0]["started"], results[1]["started"]),
            min(results[0]["finished"], results[1]["finished"]),
            f"nested work units did not overlap; elapsed={elapsed:.3f}s",
        )
        self.assertLess(elapsed, 1.8, f"two 0.8s logical units were serialized: {elapsed:.3f}s")

    def test_source_metric_aggregation_is_deterministic_out_of_order(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        first = {
            "index": 0,
            "kind": "scats",
            "source_dataset_id": "vic_scats_annual",
            "work_unit_label": "vic_scats_annual::2023-01.zip",
            "nested_member": "2023-01.zip",
            "path": "/tmp/unit-0.parquet",
            "metrics": {
                "input_rows": 10,
                "selected_rows": 9,
                "members": ["2023-01.zip::VSDATA_20230101.csv"],
                "dates": {dt.date(2023, 1, 1)},
                "min_date": dt.date(2023, 1, 1),
                "max_date": dt.date(2023, 1, 1),
                "scats_negative_interval_count": 2,
                "scats_standard_minus_one_count": 1,
                "scats_nonstandard_negative_count": 1,
                "scats_negative_value_frequency": {"-1": 1, "-5": 1},
            },
        }
        second = {
            "index": 1,
            "kind": "scats",
            "source_dataset_id": "vic_scats_annual",
            "work_unit_label": "vic_scats_annual::2023-02.zip",
            "nested_member": "2023-02.zip",
            "path": "/tmp/unit-1.parquet",
            "metrics": {
                "input_rows": 20,
                "selected_rows": 18,
                "members": ["2023-02.zip::VSDATA_20230201.csv"],
                "dates": {dt.date(2023, 2, 1)},
                "min_date": dt.date(2023, 2, 1),
                "max_date": dt.date(2023, 2, 1),
                "scats_negative_interval_count": 3,
                "scats_standard_minus_one_count": 2,
                "scats_nonstandard_negative_count": 1,
                "scats_negative_value_frequency": {"-1": 2, "-30": 1},
            },
        }
        in_order = traffic_builder._aggregate_source_metrics([first, second])
        out_of_order = traffic_builder._aggregate_source_metrics([second, first])
        self.assertEqual(
            json.dumps(traffic_builder._serialise_metric(in_order), sort_keys=True),
            json.dumps(traffic_builder._serialise_metric(out_of_order), sort_keys=True),
        )
        source = out_of_order[0]
        self.assertEqual(source["input_rows"], 30)
        self.assertEqual(source["selected_rows"], 27)
        self.assertEqual(source["work_unit_count"], 2)
        self.assertEqual(source["scats_negative_value_frequency"], {"-1": 3, "-30": 1, "-5": 1})
        self.assertEqual(source["min_date"], dt.date(2023, 1, 1))
        self.assertEqual(source["max_date"], dt.date(2023, 2, 1))

    def test_scats_negative_sentinels_are_missing_and_zero_is_real(self) -> None:
        """-1/-5/-30 never enter totals, while a reported zero is retained."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[
                _scats_row(
                    1001,
                    1,
                    date_value="2026-01-15",
                    values={0: -1, 1: -5, 2: -30, 3: 0},
                ),
                _scats_row(
                    1001,
                    1,
                    date_value="2026-01-16",
                    values={index: None for index in range(96)},
                ),
            ],
            label="negative-sentinels",
        )
        completed, output_dir = self._run(inputs, "negative-sentinels")
        self._assert_success(completed)
        rows = [
            row
            for row in self._all_rows(output_dir)
            if str(row.get("scats_site")) == "1001"
            and row.get("source_date_local") == dt.date(2026, 1, 15)
            and row.get("quality_missing_interval_count") == 3
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vehicle_count"], 0)
        self.assertEqual(rows[0]["quality_missing_interval_count"], 3)
        self.assertTrue(rows[0]["quality_partial_flag"])
        self.assertFalse(
            any(
                row.get("source_date_local") == dt.date(2026, 1, 16)
                for row in self._all_rows(output_dir)
                if str(row.get("scats_site")) == "1001"
            ),
            "all-null site-hours must remain omitted",
        )
        _manifest_path, manifest = self._manifest(output_dir)
        source = next(item for item in manifest["sources"] if item["kind"] == "scats")
        expected_frequency = {"-1": 1, "-30": 1, "-5": 1}
        for payload in (source, manifest["quality"]):
            self.assertEqual(payload["scats_negative_interval_count"], 3)
            self.assertEqual(payload["scats_standard_minus_one_count"], 1)
            self.assertEqual(payload["scats_nonstandard_negative_count"], 2)
            self.assertEqual(payload["scats_negative_value_frequency"], expected_frequency)
        self.assertEqual(source["omitted_null_target_site_hours"], 24)
        self.assertFalse(manifest["assertions"]["scats_negative_intervals_in_vehicle_totals"])
        self.assertFalse(manifest["assertions"]["scats_negative_intervals_become_zero"])
        self.assertTrue(manifest["assertions"]["scats_zero_values_are_real_observations"])
        self.assertEqual(
            manifest["assertions"]["scats_negative_sentinel_metrics"],
            {
                "negative_interval_count": 3,
                "standard_minus_one_count": 1,
                "nonstandard_negative_count": 2,
                "negative_value_frequency": expected_frequency,
            },
        )

    def test_scats_source_allowlist_prefers_nested_config_and_resolves_ids(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        resolved = traffic_builder.expected_coverage(
            {
                "scats": {
                    "source_datasets": [
                        "vic_scats_august_2026",
                        "vic_scats_2026_07",
                        "vic_scats_2026_07",
                    ]
                },
                "scats_source_datasets": ["top_level_must_not_override"],
            }
        )

        self.assertEqual(
            resolved["scats_source_datasets"],
            ["vic_scats_2026_07", "vic_scats_august_2026"],
        )

    def test_implicit_scats_discovery_filters_allowlist_and_reports_missing_sources(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        root = self.work / "implicit-scats"
        retained_dir = root / "vic_scats_retained"
        excluded_dir = root / "vic_scats_excluded"
        retained_dir.mkdir(parents=True)
        excluded_dir.mkdir(parents=True)
        retained = _write_zip_csv(
            retained_dir / "retained.zip",
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, values={0: 2})],
        )
        _write_zip_csv(
            excluded_dir / "excluded.zip",
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, values={0: 3})],
        )

        discovered = traffic_builder.discover_scats_archives(
            root,
            source_dataset_allowlist=["vic_scats_retained"],
        )
        self.assertEqual([item.source_dataset_id for item in discovered], ["vic_scats_retained"])
        self.assertEqual(discovered[0].path, retained.resolve())

        with self.assertRaisesRegex(
            FileNotFoundError,
            r"configured SCATS source dataset archives.*vic_scats_missing",
        ):
            traffic_builder.discover_scats_archives(
                root,
                source_dataset_allowlist=["vic_scats_missing"],
            )

    def test_explicit_repeated_scats_paths_are_not_filtered_by_allowlist(self) -> None:
        from ml.scripts import build_traffic_dataset as traffic_builder

        root = self.work / "explicit-scats"
        first_dir = root / "vic_scats_first"
        second_dir = root / "vic_scats_second"
        first_dir.mkdir(parents=True)
        second_dir.mkdir(parents=True)
        first = _write_zip_csv(
            first_dir / "first.zip",
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, values={0: 2})],
        )
        second = _write_zip_csv(
            second_dir / "second.zip",
            "VSDATA_20260116.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-16", values={0: 3})],
        )

        discovered = traffic_builder.discover_scats_archives(
            root,
            paths=[first, second],
            source_dataset_allowlist=["vic_scats_first"],
        )
        self.assertEqual(
            {item.source_dataset_id for item in discovered},
            {"vic_scats_first", "vic_scats_second"},
        )

    def test_manifest_resolves_scats_source_allowlist_and_hash_changes_with_it(self) -> None:
        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 2})],
            label="manifest-allowlist",
        )
        first_config = self.work / "manifest-allowlist-first.json"
        first_config.write_text(
            json.dumps({"scats": {"source_datasets": ["configured-first"]}}),
            encoding="utf-8",
        )
        first, first_output = self._run(
            inputs,
            "manifest-allowlist-first",
            expected_config=first_config,
        )
        self._assert_success(first)
        _first_manifest_path, first_manifest = self._manifest(first_output)
        self.assertEqual(
            first_manifest["expected_coverage"]["resolved"]["scats_source_datasets"],
            ["configured-first"],
        )

        second_config = self.work / "manifest-allowlist-second.json"
        second_config.write_text(
            json.dumps({"scats": {"source_datasets": ["configured-second"]}}),
            encoding="utf-8",
        )
        second, second_output = self._run(
            inputs,
            "manifest-allowlist-second",
            expected_config=second_config,
        )
        self._assert_success(second)
        _second_manifest_path, second_manifest = self._manifest(second_output)
        self.assertEqual(
            second_manifest["expected_coverage"]["resolved"]["scats_source_datasets"],
            ["configured-second"],
        )
        self.assertNotEqual(first_manifest["content_hash"], second_manifest["content_hash"])

    def test_allowlisted_scats_gaps_are_complete_but_unexpected_gaps_fail(self) -> None:
        """Allowlisted publisher omissions are visible and do not excuse others."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T00:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, date_value="2026-01-15", values={0: 2})],
            label="allowed-gap",
        )
        allowed_config = self.work / "allowed-gap.json"
        allowed_config.write_text(
            json.dumps(
                {
                    "scats_expected_start": "2026-01-15",
                    "scats_expected_end": "2026-01-16",
                    "allowed_missing_scats_dates": ["2026-01-16"],
                }
            ),
            encoding="utf-8",
        )
        completed, output_dir = self._run(
            inputs,
            "allowed-gap",
            expected_config=allowed_config,
        )
        self._assert_success(completed)
        _manifest_path, manifest = self._manifest(output_dir)
        coverage = manifest["coverage"]
        self.assertFalse(coverage["partial"])
        self.assertEqual(coverage["missing_scats_date_count"], 1)
        self.assertEqual(coverage["allowed_missing_scats_date_count"], 1)
        self.assertEqual(coverage["allowed_missing_scats_dates"], ["2026-01-16"])
        self.assertEqual(coverage["unexpected_missing_scats_date_count"], 0)

        unexpected_config = self.work / "unexpected-gap.json"
        unexpected_config.write_text(
            json.dumps(
                {
                    "scats_expected_start": "2026-01-15",
                    "scats_expected_end": "2026-01-17",
                    "allowed_missing_scats_dates": ["2026-01-16"],
                }
            ),
            encoding="utf-8",
        )
        failed, failed_output = self._run(
            inputs,
            "unexpected-gap",
            expected_config=unexpected_config,
        )
        self._assert_failure(failed, r"allowed=1.*unexpected=1|unexpected=1.*allowed=1")
        self.assertFalse(list(failed_output.glob(".traffic-staging-*")))

    def test_official_four_scats_publisher_gaps_are_all_allowlisted(self) -> None:
        """The four known publisher omissions are not confused with new gaps."""

        from ml.scripts import build_traffic_dataset as traffic_builder

        allowed = {
            dt.date(2023, 7, 31),
            dt.date(2024, 9, 30),
            dt.date(2025, 2, 28),
            dt.date(2025, 12, 31),
        }
        start = dt.date(2023, 1, 1)
        end = dt.date(2025, 12, 31)
        observed: set[dt.date] = set()
        current = start
        while current <= end:
            if current not in allowed:
                observed.add(current)
            current += dt.timedelta(days=1)
        expected = traffic_builder.expected_coverage(
            {
                "scats_expected_years": [2023, 2024, 2025],
                "scats_expected_start": start.isoformat(),
                "scats_expected_end": end.isoformat(),
                "allowed_missing_scats_dates": sorted(item.isoformat() for item in allowed),
            },
            expected_ta_sources=["fixture-ta"],
        )
        coverage = traffic_builder.check_coverage(
            [{"source_dataset_id": "fixture-ta"}],
            [{"source_dataset_id": "fixture-scats", "dates": observed}],
            expected,
            allow_partial=False,
        )
        self.assertFalse(coverage["partial"])
        self.assertEqual(coverage["missing_scats_date_count"], 4)
        self.assertEqual(coverage["allowed_missing_scats_date_count"], 4)
        self.assertEqual(coverage["unexpected_missing_scats_date_count"], 0)
        self.assertEqual(set(coverage["allowed_missing_scats_dates"]), {item.isoformat() for item in allowed})

        observed.remove(dt.date(2025, 12, 30))
        with self.assertRaisesRegex(ValueError, r"allowed=4, unexpected=1"):
            traffic_builder.check_coverage(
                [{"source_dataset_id": "fixture-ta"}],
                [{"source_dataset_id": "fixture-scats", "dates": observed}],
                expected,
                allow_partial=False,
            )

    def test_chunk_size_changes_do_not_change_canonical_output(self) -> None:
        """Per-chunk staging produces the same canonical rows and preview."""

        inputs = self._write_fixture_inputs(
            ta_rows=[
                _ta_row("King_Street_bn003", "2026-01-15T13:05:00Z", "car", 2),
                _ta_row("King_Street_bn003", "2026-01-15T13:55:00Z", "truck", 3),
                _ta_row("Queen_Street_bn003", "2026-01-15T13:15:00Z", "cyclist", 7),
            ],
            scats_rows=[
                _scats_row(1001, 1, values={0: 1, 15: 2, 30: 3, 45: 4}),
                _scats_row(1001, 2, values={0: 10, 15: 20, 30: 30, 45: 40}),
            ],
            label="chunk-equivalent",
        )
        first, first_output = self._run(inputs, "chunk-one", chunk_size=1)
        second, second_output = self._run(inputs, "chunk-large", chunk_size=100000)
        self._assert_success(first)
        self._assert_success(second)
        self.assertEqual(self._table_signature(first_output), self._table_signature(second_output))
        self.assertEqual(self._preview_signature(first_output), self._preview_signature(second_output))

    def test_cross_archive_global_order_and_uniqueness_are_deterministic(self) -> None:
        """DuckDB's final external sort is independent of archive argument order."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 2})],
            label="cross-archive-base",
        )
        ta_a = self.work / "archive-a.zip"
        ta_b = self.work / "archive-b.zip"
        _write_zip_csv(ta_a, "TransportActivityCount_2026-1.csv", TA_COLUMNS, [_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)])
        _write_zip_csv(ta_b, "TransportActivityCount_2026-1.csv", TA_COLUMNS, [_ta_row("Queen_Street_bn003", "2026-01-15T14:00:00Z", "car", 2)])
        first, first_output = self._run(
            inputs,
            "cross-archive-ab",
            ta_archives=[ta_a, ta_b],
        )
        second, second_output = self._run(
            inputs,
            "cross-archive-ba",
            ta_archives=[ta_b, ta_a],
        )
        self._assert_success(first)
        self._assert_success(second)
        self.assertEqual(self._table_signature(first_output), self._table_signature(second_output))
        first_parquet = next(first_output.glob("*.parquet"))
        rows = pq.read_table(first_parquet).to_pylist()
        key_columns = ("source_dataset_id", "observation_unit_id", "hour_start_utc")
        keys = [tuple(row[column] for column in key_columns) for row in rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse(list(first_output.glob(".traffic-staging-*")))
        self.assertFalse(list(second_output.glob(".traffic-staging-*")))

    def test_workers_must_be_at_least_one(self) -> None:
        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
            label="invalid-workers",
        )
        completed, output_dir = self._run(inputs, "invalid-workers", workers=0)
        self._assert_failure(completed, r"workers.*at least 1")
        self.assertFalse(list(output_dir.rglob("*")))

    def test_parallel_archive_staging_matches_serial_artifact_and_hash(self) -> None:
        """Parallel phases preserve the serial canonical bytes and content stem."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
            label="parallel-base",
        )
        ta_a = self.work / "parallel-ta-a.zip"
        ta_b = self.work / "parallel-ta-b.zip"
        _write_zip_csv(
            ta_a,
            "TransportActivityCount_2026-1.csv",
            TA_COLUMNS,
            [_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
        )
        _write_zip_csv(
            ta_b,
            "TransportActivityCount_2026-1.csv",
            TA_COLUMNS,
            [_ta_row("Queen_Street_bn003", "2026-01-15T14:00:00Z", "truck", 2)],
        )
        scats_a = self.work / "parallel-scats-a.zip"
        scats_b = self.work / "parallel-scats-b.zip"
        _write_zip_csv(
            scats_a,
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, values={0: 1})],
        )
        _write_zip_csv(
            scats_b,
            "VSDATA_20260116.csv",
            SCATS_COLUMNS,
            [_scats_row(1001, 1, date_value="2026-01-16", values={0: 2})],
        )
        ta_archives = [ta_a, ta_b]
        scats_archives = [scats_a, scats_b]

        serial, serial_output = self._run(
            inputs,
            "parallel-serial",
            ta_archives=ta_archives,
            scats_archives=scats_archives,
            workers=1,
        )
        parallel, parallel_output = self._run(
            inputs,
            "parallel-processes",
            ta_archives=ta_archives,
            scats_archives=scats_archives,
            workers=2,
        )
        self._assert_success(serial)
        self._assert_success(parallel)
        serial_parquet = next(serial_output.glob("*.parquet"))
        parallel_parquet = next(parallel_output.glob("*.parquet"))
        self.assertEqual(serial_parquet.name, parallel_parquet.name)
        self.assertEqual(serial_parquet.read_bytes(), parallel_parquet.read_bytes())
        serial_preview = next(serial_output.glob("*_preview.csv"))
        parallel_preview = next(parallel_output.glob("*_preview.csv"))
        self.assertEqual(serial_preview.name, parallel_preview.name)
        self.assertEqual(serial_preview.read_bytes(), parallel_preview.read_bytes())

        _serial_manifest_path, serial_manifest = self._manifest(serial_output)
        _parallel_manifest_path, parallel_manifest = self._manifest(parallel_output)
        self.assertEqual(serial_manifest["content_hash"], parallel_manifest["content_hash"])
        self.assertEqual(
            serial_manifest["outputs"]["parquet"]["sha256"],
            parallel_manifest["outputs"]["parquet"]["sha256"],
        )
        self.assertEqual(
            [item["source_dataset_id"] for item in serial_manifest["sources"]],
            [item["source_dataset_id"] for item in parallel_manifest["sources"]],
        )
        self.assertEqual(serial_manifest["staging"]["requested_workers"], 1)
        self.assertEqual(parallel_manifest["staging"]["requested_workers"], 2)
        self.assertEqual(parallel_manifest["staging"]["effective_workers"], 2)
        self.assertEqual(parallel_manifest["staging"]["worker_strategy"], "shared_spawn_process_pool")
        self.assertEqual(parallel_manifest["staging"]["work_unit_count"], 4)
        self.assertEqual(
            parallel_manifest["staging"]["work_unit_counts"],
            {"transport_activity": 2, "scats": 2},
        )
        self.assertFalse(list(serial_output.glob(".traffic-staging-*")))
        self.assertFalse(list(parallel_output.glob(".traffic-staging-*")))
        self.assertIn("staged", f"{serial.stdout}\n{parallel.stdout}")

    def test_parallel_completion_order_does_not_reorder_parent_results(self) -> None:
        """Fast workers may finish first while stage records stay configured-order."""

        from ml.scripts import build_traffic_dataset as traffic_builder

        tasks = [
            (0, 0.8, "archive-a"),
            (1, 0.0, "archive-b"),
            (2, 0.1, "archive-c"),
        ]
        progress = io.StringIO()
        with contextlib.redirect_stdout(progress):
            results, effective, strategy = traffic_builder._run_archive_stage_tasks(
                tasks,
                _delayed_stage_task_worker,
                requested_workers=3,
            )
        self.assertEqual(effective, 3)
        self.assertEqual(strategy, "shared_spawn_process_pool")
        self.assertEqual(
            [result["source_dataset_id"] for result in results],
            ["archive-a", "archive-b", "archive-c"],
        )
        self.assertLess(progress.getvalue().index("archive-b"), progress.getvalue().index("archive-a"))

    def test_worker_failure_publishes_nothing_and_cleans_staging(self) -> None:
        """A failed archive aborts the phase before any final artifact exists."""

        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
            label="worker-failure-base",
        )
        good_ta = self.work / "worker-failure-good.zip"
        bad_ta = self.work / "worker-failure-bad.zip"
        _write_zip_csv(
            good_ta,
            "TransportActivityCount_2026-1.csv",
            TA_COLUMNS,
            [_ta_row("King_Street_bn003", "2026-01-15T13:00:00Z", "car", 1)],
        )
        _write_zip_csv(
            bad_ta,
            "TransportActivityCount_2026-1.csv",
            TA_COLUMNS,
            [_ta_row("Queen_Street_bn003", "2026-01-15T14:00:00Z", "car", "not-a-count")],
        )
        completed, output_dir = self._run(
            inputs,
            "worker-failure",
            ta_archives=[good_ta, bad_ta],
            workers=2,
        )
        self._assert_failure(completed, r"staging failed|count|numeric|invalid")
        self.assertFalse(list(output_dir.rglob("*")))
        self.assertFalse(list(output_dir.glob(".traffic-staging-*")))

    def test_transport_misleading_z_and_dst_fallback_wrap_are_localised(self) -> None:
        """Publisher Z labels are Melbourne wall time, including the repeated hour."""

        fallback = _ta_row(
            "King_Street_bn003",
            "2023-04-02T02:55:00.000Z",
            "car",
            4,
        )
        fallback["to"] = "2023-04-02T02:00:00.000Z"
        inputs = self._write_fixture_inputs(
            ta_rows=[fallback],
            scats_rows=[_scats_row(1001, 1, date_value="2026-04-02", values={0: 1})],
            label="dst-fallback",
        )
        completed, output_dir = self._run(inputs, "dst-fallback")
        self._assert_success(completed)
        rows = self._rows_with_any_text(
            self._all_rows(output_dir), "King_Street_bn003", "King Street"
        )
        self.assertTrue(rows)
        self._assert_row_local_and_utc(
            rows[0],
            utc_hour=(dt.date(2023, 4, 1), 16),
            local_hour=(dt.date(2023, 4, 2), 2),
        )
        self.assertRegex(_flatten_text(rows[0]).casefold(), r"dst|fallback|ambiguous")

    def test_null_2026_coordinates_are_flagged_or_excluded_from_mapped_status(self) -> None:
        ta_rows = [
            _ta_row(
                "Null_Coordinates_bn003",
                "2026-06-01T13:20:00Z",
                "car",
                4,
                latitude="",
                longitude="",
            ),
        ]
        inputs = self._write_fixture_inputs(
            ta_rows=ta_rows,
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
        )
        completed, output_dir = self._run(inputs, "null-coordinates")
        self._assert_success(completed)
        rows = self._rows_with_any_text(
            self._all_rows(output_dir), "Null_Coordinates_bn003", "Null Coordinates"
        )
        if rows:
            self._assert_not_mapped(rows[0])
            self.assertTrue(
                _is_missing(_field(rows[0], LAT_FIELDS))
                or _is_missing(_field(rows[0], LON_FIELDS)),
                f"2026 null coordinates were filled unexpectedly: {rows[0]}",
            )

    def test_ambiguous_registry_channel_is_excluded_or_strictly_rejected(self) -> None:
        inputs = self._write_fixture_inputs(
            ta_rows=[
                _ta_row("Ambiguous_Street_bn003", "2026-06-01T13:00:00Z", "car", 8),
            ],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
            registry_rows=_registry_rows(ambiguous=True),
        )
        completed, output_dir = self._run(inputs, "ambiguous-channel")
        if completed.returncode != 0:
            self.assertRegex(
                f"{completed.stdout}\n{completed.stderr}",
                r"ambiguous|duplicate|registry|channel",
            )
            return
        rows = self._rows_with_any_text(
            self._all_rows(output_dir), "Ambiguous_Street_bn003", "First Road", "Second Road"
        )
        self.assertFalse(rows, "ambiguous approved channel was silently treated as eligible")

    def test_negative_or_malformed_counts_fail(self) -> None:
        inputs = self._write_fixture_inputs(
            ta_rows=[
                _ta_row("King_Street_bn003", "2026-06-01T13:00:00Z", "car", -1),
                _ta_row("Queen_Street_bn003", "2026-06-01T13:05:00Z", "car", "not-a-count"),
            ],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
        )
        completed, _output_dir = self._run(inputs, "invalid-counts")
        self._assert_failure(completed, r"count|negative|numeric|malformed|invalid")

    def test_duplicate_natural_key_is_rejected_or_output_is_unique(self) -> None:
        duplicate = _ta_row("King_Street_bn003", "2026-06-01T13:00:00Z", "car", 2)
        duplicate_copy = dict(duplicate)
        inputs = self._write_fixture_inputs(
            ta_rows=[duplicate, duplicate_copy],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
        )
        completed, output_dir = self._run(inputs, "duplicate-key")
        if completed.returncode != 0:
            self.assertRegex(
                f"{completed.stdout}\n{completed.stderr}",
                r"duplicate|natural.?key|unique|same.?hour",
            )
            return
        rows = self._all_rows(output_dir)
        keys: list[tuple[Any, ...]] = []
        for row in rows:
            road = _field(row, ROAD_FIELDS)
            if road is None:
                continue
            try:
                local_date, local_hour = _date_hour(row, local=True)
            except AssertionError:
                continue
            detector = _field(row, DETECTOR_FIELDS, default="")
            site = _field(row, SITE_FIELDS, default="")
            keys.append((str(road), str(site), str(detector), local_date, local_hour))
        self.assertEqual(len(keys), len(set(keys)), f"duplicate natural keys in output: {keys}")

    def test_allow_partial_marks_output_partial_and_manifest_has_source_checksums(self) -> None:
        inputs = self._write_fixture_inputs(
            ta_rows=[_ta_row("King_Street_bn003", "2026-06-01T13:00:00Z", "car", 2)],
            scats_rows=[_scats_row(1001, 1, values={0: 1})],
        )
        completed, output_dir = self._run(
            inputs,
            "partial",
            allow_partial=True,
        )
        self._assert_success(completed)
        self._assert_manifest_sources(output_dir, inputs)
        manifest_path, payload = self._manifest(output_dir)
        manifest_text = json.dumps(payload, sort_keys=True, default=str).lower()
        partial_true = bool(
            re.search(r"\"(?:is_)?partial\"\s*:\s*(?:true|1)", manifest_text)
            or re.search(r"(?:status|coverage|completeness)\"\s*:\s*\"[^\"]*partial", manifest_text)
        )
        rows = self._all_rows(output_dir)
        row_partial = False
        for row in rows:
            for key, value in row.items():
                if _normalise(key) not in {
                    "partial",
                    "is_partial",
                    "partial_output",
                    "coverage_status",
                }:
                    continue
                value_text = str(value).casefold()
                if value_text in {"partial", "partial_output", "true", "1", "yes"}:
                    row_partial = True
                    break
            if row_partial:
                break
        self.assertTrue(
            partial_true or row_partial,
            f"--allow-partial output was not marked partial; manifest={manifest_path.read_text(encoding='utf-8')}",
        )

    def test_output_order_preview_limit_and_columns_are_deterministic(self) -> None:
        ta_rows = [
            _ta_row("King_Street_bn003", "2026-06-01T13:55:00Z", "truck", 3),
            _ta_row("King_Street_bn003", "2026-06-01T13:05:00Z", "car", 2),
            _ta_row("Queen_Street_bn003", "2026-06-01T13:15:00Z", "cyclist", 7),
        ]
        scats_rows = [
            _scats_row(1001, 2, values={0: 10, 15: 20, 30: 30, 45: 40}),
            _scats_row(1001, 1, values={0: 1, 15: 2, 30: 3, 45: 4}),
        ]
        inputs = self._write_fixture_inputs(
            ta_rows=ta_rows,
            scats_rows=scats_rows,
            label="stable",
        )
        first, first_output = self._run(
            inputs,
            "stable-a",
            preview_rows=500,
        )
        self._assert_success(first)
        first_signature = self._table_signature(first_output)
        first_preview = self._preview_signature(first_output)

        # Reversing each source's physical row order must not alter canonical
        # rows, natural keys, or the deterministic preview.
        _write_zip_csv(
            inputs["ta"],
            "TransportActivityCount_2026-1.csv",
            TA_COLUMNS,
            reversed(ta_rows),
        )
        _write_zip_csv(
            inputs["scats"],
            "VSDATA_20260115.csv",
            SCATS_COLUMNS,
            reversed(scats_rows),
        )
        second, second_output = self._run(
            inputs,
            "stable-b",
            preview_rows=500,
        )
        self._assert_success(second)
        self.assertEqual(first_signature, self._table_signature(second_output))
        self.assertEqual(first_preview, self._preview_signature(second_output))

    def _table_signature(self, output_dir: Path) -> tuple[Any, ...]:
        signature: list[Any] = []
        volatile_tokens = (
            "row_number",
            "input_order",
            "source_line",
            "source_checksum",
        )
        for path, rows, columns in self._artifacts(output_dir):
            keep_columns = sorted(
                column
                for column in columns
                if not any(token in _normalise(column) for token in volatile_tokens)
            )
            encoded_rows = []
            for row in rows:
                encoded_rows.append(
                    json.dumps(
                        {column: _jsonable(row.get(column)) for column in keep_columns},
                        sort_keys=True,
                        default=str,
                    )
                )
            # Content-addressed filenames legitimately change when source ZIP
            # bytes change, even when canonical rows are equivalent.
            signature.append((path.suffix.lower(), tuple(keep_columns), tuple(sorted(encoded_rows))))
        return tuple(signature)

    def _preview_signature(self, output_dir: Path) -> tuple[Any, ...]:
        previews = sorted(
            path
            for path in output_dir.rglob("*")
            if path.is_file() and "preview" in path.stem.lower()
        )
        self.assertTrue(previews, f"builder wrote no preview below {output_dir}")
        artifact_columns = [columns for _path, _rows, columns in self._artifacts(output_dir)]
        signature: list[Any] = []
        for path in previews:
            if path.suffix.lower() == ".csv":
                with path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    rows = [dict(row) for row in reader]
                    columns = tuple(reader.fieldnames or ())
            elif path.suffix.lower() == ".parquet":
                table = pq.read_table(path)
                rows = table.to_pylist()
                columns = tuple(table.column_names)
            elif path.suffix.lower() == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                rows = payload if isinstance(payload, list) else payload.get("rows", [])
                columns = tuple(sorted(rows[0])) if rows else ()
            else:
                continue
            self.assertLessEqual(len(rows), 500, path)
            self.assertTrue(
                any(set(columns) == set(output_columns) for output_columns in artifact_columns),
                f"preview columns differ from every canonical output: {path} {columns}",
            )
            signature.append(
                (
                    path.suffix.lower(),
                    columns,
                    tuple(
                        sorted(
                            json.dumps(_jsonable(row), sort_keys=True, default=str)
                            for row in rows
                        )
                    ),
                )
            )
        self.assertTrue(signature, f"no readable preview artifacts below {output_dir}")
        return tuple(signature)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
