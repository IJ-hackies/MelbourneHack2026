"""Contract tests for the two-model crowd training-data builder.

These tests deliberately use tiny, source-shaped Parquet fixtures.  The
canonical hourly crowd target is the contract boundary: training outputs must
retain its observation identity and target, while adding only features that
were available strictly before the prediction timestamp.  The builder is
invoked as a subprocess, matching ``test_build_crowd_dataset.py`` and keeping
the tests useful while the ML workstream remains a script rather than a
package.
"""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # The training builder and the canonical target both use PyArrow.
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised only in minimal environments.
    pa = None
    pq = None


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "ml" / "scripts" / "build_crowd_training_datasets.py"
RECENT_START = date(2024, 1, 2)

TARGET_COLUMNS = {
    "observation_key",
    "sensor_id",
    "local_date",
    "local_hour",
    "observed_at_local",
    "pedestrian_flow",
}


def _write_parquet(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """Write a small Parquet fixture without introducing a pandas dependency."""

    assert pa is not None and pq is not None
    table = pa.Table.from_pylist([dict(row) for row in rows])
    pq.write_table(table, path)
    return path


def _target_row(sensor_id: int, observed_at: datetime, flow: int) -> dict[str, Any]:
    """Return one row with the canonical target schema used by the harmoniser."""

    observed_date = observed_at.date()
    return {
        "observation_key": f"{sensor_id}:{observed_at:%Y%m%d%H}",
        "source_dataset_id": "fixture-canonical-target",
        "source_record_id": f"record-{sensor_id}-{observed_at:%Y%m%d%H}",
        "source_row_number": flow,
        "sensor_id": sensor_id,
        "sensor_name": f"Fixture sensor {sensor_id}",
        "local_date": observed_date,
        "local_hour": observed_at.hour,
        "observed_at_local": observed_at,
        "timezone_name": "Australia/Melbourne",
        "utc_offset_known": True,
        "is_dst": True,
        "dst_ambiguous_local_time": False,
        "dst_nonexistent_local_time": False,
        "year": observed_date.year,
        "month": observed_date.month,
        "day_of_month": observed_date.day,
        "day_of_week": observed_date.weekday(),
        "is_weekend": observed_date.weekday() >= 5,
        "pedestrian_flow": flow,
        # These are intentionally populated in the fixture.  They are target
        # validation fields, not permissible same-hour model inputs.
        "direction_1_count": flow // 2,
        "direction_2_count": flow - flow // 2,
        "direction_counts_valid": True,
        "direction_semantics": "fixture-only",
        "latitude": -37.81,
        "longitude": 144.96,
        "coordinate_valid": True,
        "sensor_in_current_metadata": True,
        "sensor_name_missing": False,
        "coordinate_missing": False,
        "hour_was_reconstructed": False,
    }


def _transport_rows(observed_at: datetime, count: int) -> list[dict[str, Any]]:
    """Return a source-shaped Transport Activity pedestrian observation."""

    end = observed_at + timedelta(minutes=5)
    # Keep the native field names (``from``, ``to``, ``class``, ``count``) and
    # add a local timestamp alias so fixtures remain unambiguous and timezone
    # conversion cannot hide an as-of/leakage bug.
    return [
        {
            "countLocationId": 9001,
            "countlineName": "Fixture transport line",
            "countlineDirection": "BOTH",
            "CountLocationLat": -37.81,
            "CountLocationLong": 144.96,
            "from": observed_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timestamp_local": observed_at,
            "class": "pedestrian",
            "count": count,
            "year": observed_at.year,
            "quarter": 1,
        }
    ]


def _microclimate_row(observed_at: datetime, temperature: float) -> dict[str, Any]:
    """Return one source-shaped microclimate reading."""

    return {
        "device_id": "fixture-micro-1",
        "received_at": observed_at,
        "timestamp_local": observed_at,
        "sensorlocation": "Fixture microclimate site",
        "airtemperature": temperature,
        "relativehumidity": 55.0,
        "atmosphericpressure": 1015.0,
        "averagewindspeed": 1.0,
        "gustwindspeed": 2.0,
        "pm25": 4.0,
        "pm10": 8.0,
        "noise": 50.0,
    }


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None)


def _row_timestamp(row: Mapping[str, Any]) -> datetime:
    for name in ("observed_at_local", "timestamp_local", "timestamp", "local_timestamp"):
        if name in row and row[name] is not None:
            return _as_datetime(row[name])
    if "local_date" in row and "local_hour" in row:
        return datetime.combine(
            row["local_date"] if isinstance(row["local_date"], date) else date.fromisoformat(str(row["local_date"])),
            datetime.min.time(),
        ) + timedelta(hours=int(row["local_hour"]))
    raise AssertionError(f"output is missing a timestamp field; columns={sorted(row)}")


def _is_null(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _find_column(columns: Iterable[str], *patterns: str) -> str | None:
    names = list(columns)
    normalised = {_normalise(name): name for name in names}
    for pattern in patterns:
        wanted = _normalise(pattern)
        if wanted in normalised:
            return normalised[wanted]
    for name in names:
        lowered = _normalise(name)
        if all(part in lowered for part in patterns):
            return name
    return None


def _lag_column(columns: Iterable[str], hours: int = 1) -> str:
    names = list(columns)
    preferred = f"flow_lag_{hours}h"
    for name in names:
        if _normalise(name) == preferred:
            return name
    patterns = (
        rf"(?:^|_)lag_?{hours}h(?:_|$)",
        rf"(?:^|_)lag_?{hours}_hour(?:_|$)",
        rf"(?:^|_)lag_?{hours}(?:_|$)",
    )
    for name in names:
        lowered = _normalise(name)
        if any(re.search(pattern, lowered) for pattern in patterns):
            return name
    raise AssertionError(f"output has no {hours}-hour lag feature; columns={sorted(names)}")


def _optional_feature_columns(columns: Iterable[str]) -> list[str]:
    """Find feature columns while excluding canonical target/identity fields."""

    ignored = {
        "observation_key",
        "source_dataset_id",
        "source_record_id",
        "source_row_number",
        "sensor_id",
        "sensor_name",
        "local_date",
        "local_hour",
        "observed_at_local",
        "timestamp_local",
        "pedestrian_flow",
        "year",
        "month",
        "day_of_month",
        "day_of_week",
        "is_weekend",
        "is_dst",
    }
    return [name for name in columns if name not in ignored]


def _split_column(columns: Iterable[str]) -> str | None:
    for name in columns:
        if _normalise(name) in {"split", "dataset_split", "fold", "partition"}:
            return name
    return None


def _read_table(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    assert pq is not None
    table = pq.read_table(path)
    return table.to_pylist(), set(table.column_names)


class BuildCrowdTrainingDatasetsTests(unittest.TestCase):
    """Small fixtures for the all-history and recent-enhanced builders."""

    def setUp(self) -> None:
        if pa is None or pq is None:
            self.skipTest("pyarrow is required for Parquet fixtures")
        self.assertTrue(BUILDER.exists(), f"expected training builder at {BUILDER}")
        self.tempdir = tempfile.TemporaryDirectory(prefix="crowd-training-builder-")
        self.work = Path(self.tempdir.name)

    def tearDown(self) -> None:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def _write_target(self, rows: Iterable[Mapping[str, Any]]) -> Path:
        return _write_parquet(self.work / "pedestrian_flow_hourly.parquet", rows)

    def _builder_help(self) -> str:
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
            f"training builder --help failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return f"{completed.stdout}\n{completed.stderr}"

    def _choose_option(self, help_text: str, options: tuple[str, ...], *, required: bool = True) -> str | None:
        for option in options:
            if re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", help_text):
                return option
        if required:
            self.fail(f"builder help does not expose any of {options!r}\n{help_text}")
        return None

    def _run_builder(
        self,
        target: Path,
        *,
        transport: Path | None = None,
        microclimate: Path | None = None,
        recent_start: date = RECENT_START,
        preview_rows: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        output_dir = self.work / f"output-{len(list(self.work.iterdir()))}"
        output_dir.mkdir(parents=True, exist_ok=True)
        help_text = self._builder_help()
        command = [sys.executable, str(BUILDER)]
        command += [self._choose_option(help_text, ("--target", "--target-parquet", "--input-target")) or "--target", str(target)]
        if transport is not None:
            transport_flag = self._choose_option(
                help_text,
                ("--transport", "--transport-parquet", "--transport-path", "--transport-activity"),
            )
            command += [transport_flag or "--transport", str(transport)]
        if microclimate is not None:
            micro_flag = self._choose_option(
                help_text,
                ("--microclimate", "--microclimate-parquet", "--microclimate-path", "--microclimate-readings"),
            )
            command += [micro_flag or "--microclimate", str(microclimate)]
        command += ["--output-dir", str(output_dir)]
        recent_flag = self._choose_option(
            help_text,
            ("--recent-start", "--recent-start-date", "--enhanced-start"),
            required=False,
        )
        if recent_flag is not None:
            command += [recent_flag, recent_start.isoformat()]
        if preview_rows is not None:
            preview_flag = self._choose_option(
                help_text,
                ("--preview-rows", "--preview"),
            )
            command += [preview_flag or "--preview-rows", str(preview_rows)]
        completed = subprocess.run(
            command,
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
            f"training builder failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def _dataset_path(self, output_dir: Path, *words: str) -> Path:
        files = [
            path
            for path in output_dir.rglob("*.parquet")
            if "preview" not in path.stem.lower()
        ]
        scored = []
        for path in files:
            lowered = path.stem.lower()
            if all(word in lowered for word in words):
                # Prefer a combined dataset over train/validation/test pieces.
                split_penalty = int(any(token in lowered for token in ("train", "valid", "val", "test")))
                scored.append((split_penalty, len(lowered), path))
        self.assertTrue(scored, f"no Parquet output matching {words!r} below {output_dir}; files={files}")
        scored.sort(key=lambda item: (item[0], item[1], str(item[2])))
        return scored[0][2]

    def _split_paths(self, output_dir: Path, *words: str) -> dict[str, Path]:
        """Locate split-specific artifacts when the builder writes one file per split."""

        paths: dict[str, Path] = {}
        for path in output_dir.rglob("*.parquet"):
            lowered = path.stem.lower()
            if "preview" in lowered or not all(word in lowered for word in words):
                continue
            if "train" in lowered and "validation" not in lowered and "test" not in lowered:
                paths.setdefault("train", path)
            elif any(token in lowered for token in ("validation", "valid", "val")):
                paths.setdefault("validation", path)
            elif "test" in lowered or "holdout" in lowered:
                paths.setdefault("test", path)
        return paths

    def _rows_for(self, output_dir: Path, *words: str) -> tuple[list[dict[str, Any]], set[str]]:
        return _read_table(self._dataset_path(output_dir, *words))

    def _base_rows(self, count: int = 48) -> list[dict[str, Any]]:
        start = datetime(2024, 1, 1, 0)
        return [_target_row(1, start + timedelta(hours=offset), 100 + offset) for offset in range(count)]

    def test_target_contract_and_all_history_vs_recent_enhanced_columns(self) -> None:
        target = self._write_target(self._base_rows())
        transport = self.work / "transport.parquet"
        microclimate = self.work / "microclimate.parquet"
        anchor = datetime(2024, 1, 2, 12)
        _write_parquet(transport, _transport_rows(anchor - timedelta(hours=1), 111) + _transport_rows(anchor, 999))
        _write_parquet(microclimate, [_microclimate_row(anchor, 25.0)])

        completed, output_dir = self._run_builder(
            target,
            transport=transport,
            microclimate=microclimate,
        )
        self._assert_success(completed)
        all_rows, all_columns = self._rows_for(output_dir, "all", "history")
        recent_rows, recent_columns = self._rows_for(output_dir, "recent", "enhanced")

        self.assertEqual(len(all_rows), 48, "all-history output must retain every canonical target row")
        self.assertTrue(len(recent_rows) < len(all_rows), "recent-enhanced output must be a recent subset")
        self.assertTrue(TARGET_COLUMNS <= all_columns)
        self.assertTrue(TARGET_COLUMNS <= recent_columns)
        self.assertTrue(
            any("transport" in _normalise(name) or "traffic" in _normalise(name) for name in recent_columns - TARGET_COLUMNS),
            f"enhanced output has no transport/traffic feature columns: {sorted(recent_columns)}",
        )
        self.assertTrue(
            any("micro" in _normalise(name) or "temperature" in _normalise(name) for name in recent_columns - TARGET_COLUMNS),
            f"enhanced output has no microclimate feature columns: {sorted(recent_columns)}",
        )
        self.assertFalse(
            any("transport" in _normalise(name) or "traffic" in _normalise(name) for name in all_columns - TARGET_COLUMNS),
            "all-history model must not silently acquire recent-only transport features",
        )

        # Canonical direction counts are valid for target validation, but are
        # direct same-hour leakage for a total-flow model.
        for name in all_columns | recent_columns:
            lowered = _normalise(name)
            self.assertNotIn(lowered, {"direction_1_count", "direction_2_count"})
            self.assertFalse(
                "direction" in lowered and "count" in lowered and ("1" in lowered or "2" in lowered),
                f"same-hour directional target leakage appeared as {name!r}",
            )

    def test_lags_use_exact_timestamp_past_only_and_sensor_partition(self) -> None:
        start = datetime(2024, 1, 2, 10)
        target_rows = [
            _target_row(1, start, 100),
            # There is no sensor-1 11:00 row: a lag at 12:00 must be null,
            # rather than a nearest-neighbour value from 10:00.
            _target_row(1, start + timedelta(hours=2), 300),
            _target_row(1, start + timedelta(hours=3), 400),
            _target_row(2, start + timedelta(hours=2), 900),
            _target_row(2, start + timedelta(hours=3), 1000),
        ]
        target = self._write_target(target_rows)
        completed, output_dir = self._run_builder(target)
        self._assert_success(completed)
        rows, columns = self._rows_for(output_dir, "all", "history")
        lag = _lag_column(columns, 1)
        by_key = {(int(row["sensor_id"]), _row_timestamp(row)): row for row in rows}

        self.assertTrue(_is_null(by_key[(1, start + timedelta(hours=2))][lag]))
        self.assertEqual(by_key[(1, start + timedelta(hours=3))][lag], 300)
        self.assertEqual(
            by_key[(2, start + timedelta(hours=3))][lag],
            900,
            "lag features must be partitioned by sensor, not taken from another sensor",
        )
        # No feature may use a future observation to fill the first row.
        self.assertTrue(_is_null(by_key[(1, start)][lag]))

    def test_enhanced_transport_is_lagged_and_never_same_hour(self) -> None:
        start = datetime(2024, 1, 2, 10)
        target = self._write_target(
            [_target_row(1, start + timedelta(hours=offset), 100 + offset) for offset in range(5)]
        )
        transport = self.work / "transport.parquet"
        anchor = start + timedelta(hours=2)
        _write_parquet(
            transport,
            _transport_rows(anchor - timedelta(hours=1), 111)
            + _transport_rows(anchor, 999),
        )

        completed, output_dir = self._run_builder(target, transport=transport)
        self._assert_success(completed)
        rows, columns = self._rows_for(output_dir, "recent", "enhanced")
        transport_features = [
            name
            for name in columns
            if ("transport" in _normalise(name) or "traffic" in _normalise(name))
            and name not in {"sensor_id", "sensor_name"}
        ]
        self.assertTrue(transport_features, f"enhanced output has no traffic predictor: {sorted(columns)}")
        for name in transport_features:
            lowered = _normalise(name)
            self.assertTrue(
                "lag" in lowered or "past" in lowered or "previous" in lowered,
                f"traffic predictor {name!r} is not explicitly availability-lagged",
            )
            self.assertFalse(
                lowered in {"transport_pedestrian_count", "traffic_pedestrian_count", "transport_count"},
                f"same-hour transport predictor leaked through as {name!r}",
            )
        anchor_row = next(row for row in rows if _row_timestamp(row) == anchor)
        values = [anchor_row[name] for name in transport_features]
        self.assertIn(111, values, "anchor hour must use the prior-hour transport observation")
        self.assertNotIn(999, values, "same-hour transport pedestrian count leaked into features")

    def test_missing_transport_and_microclimate_values_remain_null(self) -> None:
        start = datetime(2024, 1, 2, 10)
        target = self._write_target(
            [_target_row(1, start + timedelta(hours=offset), 100 + offset) for offset in range(4)]
        )
        transport = self.work / "transport.parquet"
        microclimate = self.work / "microclimate.parquet"
        # Both sources are deliberately outside the target timestamps.  The
        # builder must retain target rows and leave optional values missing,
        # rather than converting missing joins into zero/false placeholders.
        _write_parquet(transport, _transport_rows(start - timedelta(days=2), 123))
        _write_parquet(microclimate, [_microclimate_row(start - timedelta(days=2), 18.0)])

        completed, output_dir = self._run_builder(
            target,
            transport=transport,
            microclimate=microclimate,
        )
        self._assert_success(completed)
        rows, columns = self._rows_for(output_dir, "recent", "enhanced")
        optional = [
            name
            for name in _optional_feature_columns(columns)
            if any(
                token in _normalise(name)
                for token in ("transport", "traffic", "micro", "temperature", "humidity", "pressure", "wind", "pm25", "pm10", "noise")
            )
        ]
        self.assertTrue(optional, f"no optional feature columns found: {sorted(columns)}")
        self.assertTrue(rows, "missing optional features must not remove target rows")
        for row in rows:
            for name in optional:
                self.assertTrue(
                    _is_null(row[name]),
                    f"missing feature {name!r} must stay null, got {row[name]!r}",
                )

    def test_splits_are_chronological_preview_is_capped_and_manifest_is_emitted(self) -> None:
        target = self._write_target(self._base_rows(72))
        completed, output_dir = self._run_builder(target, preview_rows=3)
        self._assert_success(completed)

        rows, columns = self._rows_for(output_dir, "all", "history")
        split_name = _split_column(columns)
        resolved: dict[str, list[datetime]] = {}
        if split_name is not None:
            groups: dict[str, list[datetime]] = {}
            for row in rows:
                groups.setdefault(str(row[split_name]).lower(), []).append(_row_timestamp(row))
            aliases = {
                "train": ("train",),
                "validation": ("validation", "valid", "val"),
                "test": ("test", "holdout"),
            }
            for canonical, names in aliases.items():
                for name in names:
                    if name in groups:
                        resolved[canonical] = groups[name]
                        break
                self.assertIn(canonical, resolved, f"missing {canonical} split in {sorted(groups)}")
        else:
            split_paths = self._split_paths(output_dir, "all", "history")
            self.assertEqual(
                set(split_paths),
                {"train", "validation", "test"},
                f"training output has no split field or complete split artifacts: {sorted(columns)}; {split_paths}",
            )
            for canonical, path in split_paths.items():
                split_rows, _ = _read_table(path)
                resolved[canonical] = [_row_timestamp(row) for row in split_rows]
        self.assertLess(max(resolved["train"]), min(resolved["validation"]))
        self.assertLess(max(resolved["validation"]), min(resolved["test"]))

        preview_files = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and "preview" in path.stem.lower()
        ]
        self.assertTrue(preview_files, f"--preview-rows must emit a preview artifact below {output_dir}")
        for path in preview_files:
            if path.suffix.lower() == ".parquet":
                preview_rows, _ = _read_table(path)
                self.assertLessEqual(len(preview_rows), 3, path)
            elif path.suffix.lower() == ".csv":
                with path.open(newline="", encoding="utf-8") as handle:
                    self.assertLessEqual(sum(1 for _ in csv.DictReader(handle)), 3, path)
            elif path.suffix.lower() == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    self.assertLessEqual(len(payload), 3, path)

        manifests = sorted(output_dir.rglob("*manifest*.json"))
        self.assertTrue(manifests, f"training builder must emit a manifest below {output_dir}")
        payload = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        manifest_text = json.dumps(payload, sort_keys=True).lower()
        for token in ("all_history", "recent_enhanced", "train", "validation", "test"):
            self.assertIn(token, manifest_text, f"manifest omitted {token!r}: {payload}")
        self.assertRegex(manifest_text, r"(row.?count|rows|records)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
