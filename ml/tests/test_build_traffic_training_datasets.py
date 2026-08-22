"""Source-shaped contract tests for traffic feature construction.

The fixture mirrors the canonical traffic schema rather than inventing a
separate model table.  Tests invoke the script as a CLI so manifest validation,
DuckDB SQL, atomic publication, and deterministic output are covered together.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - dependency error is reported by setup.
    pa = None
    pq = None


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "ml" / "scripts" / "build_traffic_training_datasets.py"
UTC = dt.timezone.utc
MELBOURNE = ZoneInfo("Australia/Melbourne")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    assert pa is not None and pq is not None
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def _canonical_row(
    unit_id: str,
    observed_at_utc: dt.datetime,
    vehicle_count: int,
    *,
    source_dataset_id: str = "fixture-traffic",
    label_source: str = "transport_activity",
    measurement_scope: str = "countline",
    include_observation_key: bool = True,
    observation_id: str | None = None,
) -> dict[str, object]:
    observed_at_utc = observed_at_utc.astimezone(UTC)
    observed_local = observed_at_utc.astimezone(MELBOURNE)
    local_date = observed_local.date()
    row = {
        "source_dataset_id": source_dataset_id,
        "observation_unit_id": unit_id,
        "observation_id": observation_id or f"row-{unit_id}",
        "hour_start_utc": observed_at_utc,
        "local_timestamp": observed_local,
        "local_date": local_date,
        "local_hour": observed_local.hour,
        "year": local_date.year,
        "month": local_date.month,
        "day": local_date.day,
        "day_of_week": (local_date.weekday() + 1) % 7,
        "is_weekend": local_date.weekday() >= 5,
        "is_dst": bool(observed_local.dst()),
        "timezone_name": "Australia/Melbourne",
        "source_timezone_name": "UTC+10",
        "source_timezone_offset_minutes": 600,
        "local_utc_offset_minutes": int(observed_local.utcoffset().total_seconds() / 60),
        "source_timestamp_utc": observed_at_utc,
        "source_timestamp_first_utc": observed_at_utc,
        "source_timestamp_last_utc": observed_at_utc,
        "source_timestamp_lineage": "fixture-lineage",
        "source_timestamp_count": 1,
        "source_timestamp_semantics": "fixture hourly source",
        "source_archive_member": "fixture.csv",
        "source_date_local": local_date,
        "source_row_count": 12,
        "source_record_count": 12,
        "count_location_id": unit_id if label_source == "transport_activity" else None,
        "countline_name": f"Fixture {unit_id}" if label_source == "transport_activity" else None,
        "channel_type": "road" if label_source == "transport_activity" else None,
        "traffic_eligible": True,
        "review_status": "approved",
        "scats_site": unit_id if label_source == "scats" else None,
        "physical_site_id": f"coord:{unit_id}",
        "latitude": -37.81,
        "longitude": 144.96,
        "coordinate_valid": True,
        "coordinate_missing": False,
        "coordinate_drift_flag": False,
        "vehicle_count": vehicle_count,
        "intersection_total": vehicle_count if label_source == "scats" else None,
        "log1p_vehicle_count": float(vehicle_count),
        "label_quality": "observed",
        "quality_flag": "none",
        "quality_partial_flag": False,
        "quality_alarm_flag": False,
        "quality_missing_interval_count": 0,
        "measurement_scope": measurement_scope,
        "label_source": label_source,
        "ta_motor_class_rows": 12 if label_source == "transport_activity" else None,
        "ta_non_motor_class_rows": 0 if label_source == "transport_activity" else None,
        "ta_reported_class_rows": 12 if label_source == "transport_activity" else None,
        "ta_derived_zero": False,
        "ta_dst_ambiguous_flag": False,
        "ta_dst_fallback_wrap_flag": False,
        "scats_detector_count": 4 if label_source == "scats" else None,
        "scats_detector_row_count": 4 if label_source == "scats" else None,
        "scats_ct_records_min": 96 if label_source == "scats" else None,
        "scats_ct_records_max": 96 if label_source == "scats" else None,
        "scats_qt_volume_24hour_sum": float(vehicle_count * 24) if label_source == "scats" else None,
        "scats_alarm_24hour_count": 0 if label_source == "scats" else None,
        "scats_source_date_local": local_date if label_source == "scats" else None,
    }
    if include_observation_key:
        row = {
            "observation_key": f"{source_dataset_id}:{unit_id}:{observed_at_utc:%Y%m%d%H}",
            **row,
        }
    return row


def _derived_observation_key(
    source_dataset_id: str,
    observation_unit_id: str,
    observed_at_utc: dt.datetime,
) -> str:
    observed_at_utc = observed_at_utc.astimezone(UTC)
    epoch_microseconds = int(observed_at_utc.timestamp() * 1_000_000)
    return (
        f"v1:{len(source_dataset_id)}:{source_dataset_id}:"
        f"{len(observation_unit_id)}:{observation_unit_id}:{epoch_microseconds}"
    )


def _write_canonical_fixture(
    directory: Path,
    rows: list[dict[str, object]],
    *,
    manifest_status: str = "complete",
) -> tuple[Path, Path]:
    target = directory / "canonical_traffic.parquet"
    _write_parquet(target, rows)
    manifest = {
        "schema_version": 1,
        "artifact_status": manifest_status,
        "outputs": {
            "parquet": {
                "path": target.name,
                "rows": len(rows),
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        },
        "coverage": {"partial": manifest_status != "complete"},
    }
    manifest_path = directory / "canonical_traffic_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target, manifest_path


def _read_rows(path: Path) -> list[dict[str, object]]:
    assert pq is not None
    return pq.read_table(path).to_pylist()


def _run_builder(
    target: Path,
    manifest: Path,
    output_dir: Path,
    *,
    preview_rows: int = 250,
    memory_limit: str = "8GiB",
    threads: int = 1,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--target",
            str(target),
            "--canonical-manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--preview-rows",
            str(preview_rows),
            "--memory-limit",
            memory_limit,
            "--threads",
            str(threads),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


class BuildTrafficTrainingDatasetsTests(unittest.TestCase):
    def setUp(self) -> None:
        if pa is None or pq is None:
            self.skipTest("pyarrow is required for Parquet fixtures")
        self.assertTrue(BUILDER.exists(), f"expected builder at {BUILDER}")
        self.tempdir = tempfile.TemporaryDirectory(prefix="traffic-training-builder-")
        self.work = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _rows(self) -> list[dict[str, object]]:
        start = dt.datetime(2024, 1, 1, tzinfo=UTC)
        rows = [
            _canonical_row("ta-1", start, 100, source_dataset_id="fixture-traffic-2024"),
            _canonical_row("ta-1", start + dt.timedelta(hours=1), 101, source_dataset_id="fixture-traffic-2024"),
            # Deliberate gaps at t+2 through t+23 and t+25 through t+167.
            _canonical_row("ta-1", start + dt.timedelta(hours=24), 124, source_dataset_id="fixture-traffic-2025"),
            _canonical_row("ta-1", start + dt.timedelta(hours=168), 268, source_dataset_id="fixture-traffic-2025"),
            _canonical_row("ta-1", start + dt.timedelta(hours=169), 269, source_dataset_id="fixture-traffic-2026"),
            # A different natural unit at the same timestamp catches
            # accidental cross-unit lag joins.
            _canonical_row("scats-1", start + dt.timedelta(hours=169), 900, label_source="scats", measurement_scope="intersection"),
            _canonical_row("ta-1", dt.datetime(2024, 12, 31, 12, tzinfo=UTC), 2024),
            _canonical_row("ta-1", dt.datetime(2024, 12, 31, 13, tzinfo=UTC), 2025),
            _canonical_row("ta-1", dt.datetime(2025, 12, 31, 12, tzinfo=UTC), 2026),
            _canonical_row("ta-1", dt.datetime(2025, 12, 31, 13, tzinfo=UTC), 2027),
            _canonical_row("ta-1", dt.datetime(2026, 7, 31, 13, tzinfo=UTC), 2028),
        ]
        return rows

    def _build(self, *, output_name: str = "output", preview_rows: int = 250):
        target, manifest = _write_canonical_fixture(self.work, self._rows())
        output = self.work / output_name
        completed = _run_builder(target, manifest, output, preview_rows=preview_rows)
        self.assertEqual(
            completed.returncode,
            0,
            f"builder failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return target, manifest, output

    def test_exact_lags_are_natural_unit_partitioned_and_gaps_stay_null(self) -> None:
        _, _, output = self._build()
        rows = _read_rows(output / "traffic_training_lag_enhanced.parquet")
        by_identity = {
            (row["observation_unit_id"], row["hour_start_utc"]): row for row in rows
        }

        target_at_24 = by_identity[("ta-1", dt.datetime(2024, 1, 2, 0, tzinfo=UTC))]
        self.assertIsNone(target_at_24["vehicle_count_lag_1h"])
        self.assertEqual(target_at_24["vehicle_count_lag_24h"], 100)
        self.assertIsNone(target_at_24["vehicle_count_lag_168h"])

        target_at_169 = by_identity[("ta-1", dt.datetime(2024, 1, 8, 1, tzinfo=UTC))]
        self.assertEqual(target_at_169["vehicle_count_lag_1h"], 268)
        self.assertIsNone(
            target_at_169["vehicle_count_lag_24h"],
            "an absent exact t-24 hour must not be filled by a nearby observation",
        )
        self.assertEqual(target_at_169["vehicle_count_lag_168h"], 101)
        self.assertEqual(
            target_at_169["vehicle_count_lag_1h"],
            268,
            "the same unit must carry its lag across source archive IDs",
        )
        self.assertIsNone(
            next(
                row for row in rows
                if row["observation_unit_id"] == "scats-1"
                and row["hour_start_utc"] == dt.datetime(2024, 1, 8, 1, tzinfo=UTC)
            )["vehicle_count_lag_1h"],
            "target lags must not cross natural units",
        )

    def test_cleaner_schema_without_observation_key_derives_stable_natural_key(self) -> None:
        start = dt.datetime(2024, 1, 1, tzinfo=UTC)
        source_dataset_id = "cleaner:transport_activity:2024"
        rows = [
            _canonical_row(
                "ta:unit-1",
                start,
                100,
                source_dataset_id=source_dataset_id,
                include_observation_key=False,
                observation_id="ta:unit-1",
            ),
            _canonical_row(
                "ta:unit-1",
                start + dt.timedelta(hours=1),
                101,
                source_dataset_id=source_dataset_id,
                include_observation_key=False,
                observation_id="ta:unit-1",
            ),
        ]
        target, manifest = _write_canonical_fixture(self.work, rows)
        output = self.work / "cleaner-schema-output"
        completed = _run_builder(target, manifest, output, threads=2)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        expected_keys = {
            _derived_observation_key(source_dataset_id, "ta:unit-1", start),
            _derived_observation_key(
                source_dataset_id, "ta:unit-1", start + dt.timedelta(hours=1)
            ),
        }
        base = _read_rows(output / "traffic_training_base.parquet")
        enhanced = _read_rows(output / "traffic_training_lag_enhanced.parquet")
        self.assertEqual({row["observation_key"] for row in base}, expected_keys)
        self.assertEqual(
            {row["observation_key"] for row in enhanced}, expected_keys
        )
        self.assertEqual(len({row["observation_key"] for row in base}), len(rows))
        self.assertEqual(
            pq.read_schema(output / "traffic_training_base.parquet").names[0],
            "observation_key",
        )
        training_manifest = json.loads(
            (output / "training_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            training_manifest["observation_key"]["mode"], "derived_natural_key"
        )
        self.assertFalse(training_manifest["observation_key"]["observation_id_used"])

        second_output = self.work / "cleaner-schema-output-two"
        second_completed = _run_builder(
            target, manifest, second_output, threads=2
        )
        self.assertEqual(second_completed.returncode, 0, second_completed.stderr)
        for name in (
            "traffic_training_base.parquet",
            "traffic_training_lag_enhanced.parquet",
            "traffic_training_base_preview.csv",
            "traffic_training_lag_enhanced_preview.csv",
            "training_manifest.json",
        ):
            self.assertEqual(
                (output / name).read_bytes(),
                (second_output / name).read_bytes(),
                f"derived-key artifact is not deterministic: {name}",
            )

    def test_rolling_windows_are_strictly_past_only(self) -> None:
        _, _, output = self._build()
        rows = _read_rows(output / "traffic_training_lag_enhanced.parquet")
        by_hour = {
            row["hour_start_utc"]: row
            for row in rows
            if row["observation_unit_id"] == "ta-1"
        }
        target = by_hour[dt.datetime(2024, 1, 8, 1, tzinfo=UTC)]

        # At t+169, the 24-hour window contains only t+168 (40 in the
        # smaller equivalent example; 268 here), while the 168-hour window
        # contains t+1 and t+168.  The target value 269 is excluded.
        self.assertEqual(target["vehicle_count_rolling_past_24h_count"], 1)
        self.assertAlmostEqual(target["vehicle_count_rolling_past_24h_mean"], 268.0)
        self.assertEqual(target["vehicle_count_rolling_past_168h_count"], 3)
        self.assertAlmostEqual(target["vehicle_count_rolling_past_168h_mean"], (101 + 124 + 268) / 3)
        self.assertNotEqual(target["vehicle_count_rolling_past_168h_mean"], (101 + 124 + 268 + 269) / 4)

        enhanced_schema = pq.read_schema(
            output / "traffic_training_lag_enhanced.parquet"
        )
        self.assertEqual(
            len(enhanced_schema.names),
            len(set(enhanced_schema.names)),
            "lag-enhanced output must not duplicate rolling feature columns",
        )

    def test_base_contract_splits_metadata_and_no_target_features(self) -> None:
        _, _, output = self._build(preview_rows=3)
        base = _read_rows(output / "traffic_training_base.parquet")
        enhanced = _read_rows(output / "traffic_training_lag_enhanced.parquet")
        manifest = json.loads((output / "training_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(len(base), len(self._rows()))
        self.assertEqual(
            {row["observation_key"] for row in base},
            {row["observation_key"] for row in enhanced},
        )
        for row in base + enhanced:
            self.assertEqual(row["prediction_horizon_hours"], 1)
            self.assertEqual(
                row["feature_asof"], row["hour_start_utc"] - dt.timedelta(hours=1)
            )

        self.assertEqual(
            {row["split"] for row in base},
            {"train", "validation", "test"},
        )
        split_by_key = {row["observation_key"]: row["split"] for row in base}
        # Split boundaries use the canonical Melbourne local date.  These UTC
        # labels are the corresponding AEST local boundary rows.
        self.assertEqual(split_by_key["fixture-traffic:ta-1:2024123112"], "train")
        self.assertEqual(split_by_key["fixture-traffic:ta-1:2024123113"], "validation")
        self.assertEqual(split_by_key["fixture-traffic:ta-1:2025123112"], "validation")
        self.assertEqual(split_by_key["fixture-traffic:ta-1:2025123113"], "test")
        self.assertEqual(split_by_key["fixture-traffic:ta-1:2026073113"], "test")

        contract = manifest["training_contract"]
        self.assertEqual(contract["target_column"], "vehicle_count")
        self.assertIn("source_dataset_id", contract["identity_features"]["base"])
        self.assertIn("measurement_scope", contract["identity_features"]["base"])
        self.assertIn("source_dataset_id", contract["train_available_features"]["base"])
        self.assertIn("measurement_scope", contract["train_available_features"]["base"])
        self.assertIn("observation_unit_id", contract["categorical_features"]["base"])
        self.assertIn("latitude", contract["numeric_features"]["base"])
        self.assertIn("hour_sin", contract["numeric_features"]["base"])
        self.assertIn("is_public_holiday", contract["numeric_features"]["base"])
        self.assertIn("quality_partial_flag", contract["quality_features"]["base"])
        self.assertIn("quality_alarm_flag", contract["quality_features"]["base"])
        self.assertIn("public_holiday_name", contract["categorical_features"]["base"])
        self.assertIn("label_source", contract["identity_features"]["base"])
        self.assertNotIn("vehicle_count", contract["feature_columns"]["base"])
        self.assertNotIn("log1p_vehicle_count", contract["feature_columns"]["base"])
        self.assertNotIn("intersection_total", contract["feature_columns"]["base"])
        self.assertNotIn("vehicle_count_lag_1h", contract["feature_columns"]["base"])
        self.assertIn("vehicle_count_lag_1h", contract["numeric_features"]["lag_enhanced"])
        self.assertEqual(manifest["prediction_contract"]["prediction_horizon_hours"], 1)
        self.assertEqual(manifest["split_contract"]["train"]["start"], "2024-01-01")
        self.assertEqual(manifest["split_contract"]["test"]["end"], "2026-07-31")
        self.assertTrue(manifest["assertions"]["candidate_keys_identical"])
        self.assertIn("schema", manifest["datasets"]["base"])
        self.assertIn("sha256", manifest["outputs"]["lag_enhanced"])
        self.assertIn("columns", manifest["leakage_exclusions"])
        self.assertEqual(manifest["observation_key"]["mode"], "existing_valid")
        self.assertEqual(manifest["resource_budget"]["duckdb_memory_limit"], "8GiB")
        self.assertEqual(
            manifest["resource_budget"]["duckdb_memory_limit_bytes"], 8 * 1024**3
        )
        self.assertEqual(manifest["resource_budget"]["duckdb_threads"], 1)
        self.assertFalse(manifest["determinism"]["duckdb_preserve_insertion_order"])

    def test_target_hour_quality_and_lineage_fields_are_diagnostic_only(self) -> None:
        _, _, output = self._build(preview_rows=2)
        manifest = json.loads(
            (output / "training_manifest.json").read_text(encoding="utf-8")
        )
        contract = manifest["training_contract"]
        target_hour_fields = {
            "label_quality",
            "quality_flag",
            "quality_partial_flag",
            "quality_alarm_flag",
            "quality_missing_interval_count",
            "source_timestamp_count",
            "source_row_count",
            "source_record_count",
            "ta_motor_class_rows",
            "ta_non_motor_class_rows",
            "ta_reported_class_rows",
            "ta_derived_zero",
            "scats_detector_count",
            "scats_detector_row_count",
            "scats_ct_records_min",
            "scats_ct_records_max",
            "scats_qt_volume_24hour_sum",
            "scats_alarm_24hour_count",
            "is_dst",
            "source_timezone_offset_minutes",
            "local_utc_offset_minutes",
        }

        excluded = set(manifest["leakage_exclusions"]["same_hour_observation_fields"])
        self.assertTrue(target_hour_fields <= excluded)
        for candidate in ("base", "lag_enhanced"):
            schema_names = set(
                pq.read_schema(output / f"traffic_training_{candidate}.parquet").names
            )
            self.assertTrue(target_hour_fields <= schema_names)
            predictors = (
                set(contract["categorical_features"][candidate])
                | set(contract["numeric_features"][candidate])
                | set(contract["feature_columns"][candidate])
            )
            self.assertTrue(
                predictors.isdisjoint(target_hour_fields),
                f"target-hour fields leaked into {candidate}: {sorted(predictors & target_hour_fields)}",
            )
            self.assertTrue(
                target_hour_fields <= set(contract["quality_features"][candidate])
            )
            self.assertTrue(
                target_hour_fields <= set(contract["train_available_features"][candidate])
            )

    def test_rows_outside_release_date_contract_are_rejected(self) -> None:
        cases = (
            ("before-2024", dt.datetime(2023, 12, 31, 12, tzinfo=UTC), "before_release=1"),
            ("after-july-2026", dt.datetime(2026, 8, 1, 0, tzinfo=UTC), "after_test_end=1"),
        )
        for name, observed_at, expected_error in cases:
            with self.subTest(name=name):
                case_dir = self.work / name
                case_dir.mkdir()
                rows = [
                    *self._rows(),
                    _canonical_row(
                        "out-of-contract",
                        observed_at,
                        1,
                        source_dataset_id=f"fixture-{name}",
                    ),
                ]
                target, manifest = _write_canonical_fixture(case_dir, rows)
                output = case_dir / "rejected-output"
                completed = _run_builder(target, manifest, output)

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("exact release date contract", completed.stderr.lower())
                self.assertIn(expected_error, completed.stderr)
                self.assertFalse(
                    any(
                        (output / artifact).exists()
                        for artifact in (
                            "traffic_training_base.parquet",
                            "traffic_training_lag_enhanced.parquet",
                            "training_manifest.json",
                        )
                    )
                )

    def test_publication_rolls_back_the_existing_artifact_set_on_failure(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "traffic_training_builder_for_publication_test", BUILDER
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        builder = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(builder)

        output = self.work / "publication-output"
        staging = self.work / "staging"
        temporary = self.work / "transaction-temp"
        output.mkdir()
        staging.mkdir()
        temporary.mkdir()
        keys = ("base", "lag_enhanced", "base_preview", "lag_enhanced_preview", "manifest")
        destinations = {
            key: output / f"{key}.artifact"
            for key in keys
        }
        staged = {
            key: staging / f"{key}.artifact"
            for key in keys
        }
        for key in keys:
            destinations[key].write_text(f"old-{key}", encoding="utf-8")
            staged[key].write_text(f"new-{key}", encoding="utf-8")

        real_replace = builder.os.replace
        replace_calls = 0

        def fail_on_lag_publish(source: Path, destination: Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            # base backup, base publish, lag backup, then fail before the lag
            # staged file can replace its destination.
            if replace_calls == 4:
                raise OSError("synthetic publication failure")
            real_replace(source, destination)

        with mock.patch.object(builder.os, "replace", side_effect=fail_on_lag_publish):
            with self.assertRaises(OSError):
                builder._publish_staged_artifacts(staged, destinations, temporary)

        for key in keys:
            self.assertEqual(
                destinations[key].read_text(encoding="utf-8"),
                f"old-{key}",
                f"publication did not restore {key}",
            )

    def test_memory_limit_is_configurable_and_invalid_values_fail_closed(self) -> None:
        _, _, output = self._build(
            output_name="budgeted-output", preview_rows=2
        )
        # Re-run into a different directory so the configured budget is
        # visible in the published manifest without touching prior outputs.
        target = self.work / "canonical_traffic.parquet"
        manifest = self.work / "canonical_traffic_manifest.json"
        budgeted = self.work / "budgeted-output-16g"
        completed = _run_builder(
            target,
            manifest,
            budgeted,
            preview_rows=2,
            memory_limit="512MiB",
            threads=2,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(
            (budgeted / "training_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["resource_budget"]["duckdb_memory_limit"], "512MiB")
        self.assertEqual(
            payload["resource_budget"]["duckdb_memory_limit_bytes"], 512 * 1024**2
        )
        self.assertEqual(payload["resource_budget"]["duckdb_threads"], 2)

        invalid = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--target",
                str(target),
                "--canonical-manifest",
                str(manifest),
                "--output-dir",
                str(self.work / "invalid-budget"),
                "--memory-limit",
                "12bananas",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("memory limit", invalid.stderr.lower())
        self.assertFalse((self.work / "invalid-budget").exists())

    def test_partial_canonical_manifest_is_rejected_before_publication(self) -> None:
        target, manifest = _write_canonical_fixture(
            self.work, self._rows(), manifest_status="partial"
        )
        output = self.work / "rejected-output"
        completed = _run_builder(target, manifest, output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("partial canonical manifest", completed.stderr.lower())
        self.assertFalse(output.exists() and any(output.iterdir()))

    def test_allowlisted_publisher_gaps_do_not_make_canonical_input_partial(self) -> None:
        target, manifest = _write_canonical_fixture(self.work, self._rows())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["coverage"].update(
            {
                "missing_scats_date_count": 4,
                "allowed_missing_scats_date_count": 4,
                "unexpected_missing_scats_date_count": 0,
            }
        )
        manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        completed = _run_builder(target, manifest, self.work / "allowed-gaps")

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_previews_and_manifest_are_deterministic(self) -> None:
        _, _, first = self._build(output_name="output-one", preview_rows=3)
        # Reuse the exact same canonical input and manifest in a second output
        # directory, exercising deterministic ordering and metadata.
        target = self.work / "canonical_traffic.parquet"
        manifest = self.work / "canonical_traffic_manifest.json"
        second = self.work / "output-two"
        completed = _run_builder(target, manifest, second, preview_rows=3)
        self.assertEqual(
            completed.returncode,
            0,
            f"second deterministic build failed\n{completed.stdout}\n{completed.stderr}",
        )
        for name in (
            "traffic_training_base.parquet",
            "traffic_training_lag_enhanced.parquet",
            "traffic_training_base_preview.csv",
            "traffic_training_lag_enhanced_preview.csv",
            "training_manifest.json",
        ):
            self.assertEqual(
                (first / name).read_bytes(),
                (second / name).read_bytes(),
                f"artifact is not deterministic: {name}",
            )
        manifest_payload = json.loads((first / "training_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_payload["datasets"]["base"]["preview_rows"], 3)
        self.assertEqual(manifest_payload["datasets"]["lag_enhanced"]["preview_rows"], 3)
        self.assertIn("sha256", manifest_payload["datasets"]["base"])
        self.assertIn("schema", manifest_payload["datasets"]["lag_enhanced"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
