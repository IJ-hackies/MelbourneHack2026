"""Fast contract tests for bounded traffic-recovery finalization."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ml" / "scripts" / "finalize_traffic_recovery.py"
FEATURE_BUILDER = ROOT / "ml" / "scripts" / "build_traffic_training_datasets.py"
SCRIPTS = ROOT / "ml" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from build_traffic_dataset import OUTPUT_SCHEMA  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row(
    source_dataset_id: str,
    label_source: str,
    unit_id: str,
    observed_date: dt.date,
    hour: int,
    vehicle_count: int,
    *,
    scats_source_date: dt.date | None = None,
) -> dict[str, object]:
    source_timezone = (
        dt.timezone(dt.timedelta(hours=10))
        if label_source == "scats"
        else ZoneInfo("Australia/Melbourne")
    )
    source_timestamp = dt.datetime(
        observed_date.year,
        observed_date.month,
        observed_date.day,
        hour,
        tzinfo=source_timezone,
    )
    timestamp_utc = source_timestamp.astimezone(dt.timezone.utc)
    local_timestamp = timestamp_utc.astimezone(ZoneInfo("Australia/Melbourne"))
    values: dict[str, object] = {}
    for field in OUTPUT_SCHEMA:
        if pa.types.is_string(field.type):
            values[field.name] = ""
        elif pa.types.is_boolean(field.type):
            values[field.name] = False
        elif pa.types.is_integer(field.type):
            values[field.name] = 0
        elif pa.types.is_floating(field.type):
            values[field.name] = 0.0
        elif pa.types.is_date(field.type):
            values[field.name] = observed_date
        elif pa.types.is_timestamp(field.type):
            values[field.name] = timestamp_utc
        else:  # pragma: no cover - protects the fixture if the public schema grows.
            values[field.name] = None

    values.update(
        {
            "source_dataset_id": source_dataset_id,
            "observation_unit_id": unit_id,
            "observation_id": f"{label_source}:{unit_id}",
            "hour_start_utc": timestamp_utc,
            "local_timestamp": local_timestamp,
            "local_date": local_timestamp.date(),
            "local_hour": local_timestamp.hour,
            "year": local_timestamp.year,
            "month": local_timestamp.month,
            "day": local_timestamp.day,
            "day_of_week": local_timestamp.weekday(),
            "is_weekend": local_timestamp.weekday() >= 5,
            "timezone_name": "Australia/Melbourne",
            "source_timezone_name": "UTC+10" if label_source == "scats" else "Australia/Melbourne",
            "source_timezone_offset_minutes": int(source_timestamp.utcoffset().total_seconds() // 60),
            "local_utc_offset_minutes": int(local_timestamp.utcoffset().total_seconds() // 60),
            "source_timestamp_utc": timestamp_utc if label_source == "scats" else None,
            "source_timestamp_first_utc": timestamp_utc,
            "source_timestamp_last_utc": timestamp_utc,
            "source_timestamp_lineage": "fixture",
            "source_timestamp_count": 1,
            "source_timestamp_semantics": "fixture",
            "source_archive_member": "fixture.csv",
            "source_date_local": observed_date if label_source == "scats" else None,
            "source_row_count": 1,
            "source_record_count": 1,
            "vehicle_count": vehicle_count,
            "intersection_total": vehicle_count if label_source == "scats" else None,
            "log1p_vehicle_count": math.log1p(vehicle_count),
            "label_quality": "observed",
            "quality_flag": "",
            "measurement_scope": "intersection" if label_source == "scats" else "countline",
            "label_source": label_source,
            "traffic_eligible": True if label_source == "transport_activity" else None,
            "review_status": "approved" if label_source == "transport_activity" else None,
            "coordinate_valid": True,
            "scats_source_date_local": scats_source_date,
        }
    )
    return values


def _write_partition(path: Path, rows: list[dict[str, object]]) -> None:
    table = pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA)
    pq.write_table(table, path, compression="zstd")


def _load_finalizer_module():
    spec = importlib.util.spec_from_file_location("traffic_recovery_finalizer_test_module", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib guard.
        raise AssertionError("unable to load recovery finalizer module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FinalizeTrafficRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="traffic-recovery-finalizer-test-")
        self.work = Path(self.tempdir.name)
        self.recovery = self.work / "recovery"
        self.recovery.mkdir()
        self.config = self.work / "expected_coverage.json"
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "transport_activity_sources": [
                        "com_transport_activity_2023",
                        "com_transport_activity_2024",
                    ],
                    "scats": {
                        "source_datasets": ["vic_scats_2023", "vic_scats_2024"],
                        "expected_start": "2024-01-01",
                        "expected_end": "2024-01-03",
                        "allowed_missing_dates": ["2024-01-02"],
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_partition(
            self.recovery / "part-ta-2023.parquet",
            [_row("com_transport_activity_2023", "transport_activity", "ta-2023", dt.date(2023, 1, 1), 0, 3)],
        )
        _write_partition(
            self.recovery / "part-scats-2023.parquet",
            [
                _row(
                    "vic_scats_2023",
                    "scats",
                    "site-2023",
                    dt.date(2023, 1, 1),
                    0,
                    4,
                    scats_source_date=dt.date(2023, 1, 1),
                )
            ],
        )
        # Deliberately write the selected partitions in an order that is not
        # the final natural-key order; the utility must sort globally.
        _write_partition(
            self.recovery / "part-ta-2024.parquet",
            [
                _row("com_transport_activity_2024", "transport_activity", "ta-1", dt.date(2024, 1, 1), 1, 11),
                _row("com_transport_activity_2024", "transport_activity", "ta-1", dt.date(2024, 1, 1), 0, 10),
            ],
        )
        _write_partition(
            self.recovery / "part-scats-2024.parquet",
            [
                _row(
                    "vic_scats_2024",
                    "scats",
                    "site-1",
                    dt.date(2024, 1, 3),
                    0,
                    30,
                    scats_source_date=dt.date(2024, 1, 3),
                ),
                _row(
                    "vic_scats_2024",
                    "scats",
                    "site-1",
                    dt.date(2024, 1, 1),
                    0,
                    20,
                    scats_source_date=dt.date(2024, 1, 1),
                ),
            ],
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, output_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONWARNINGS"] = "error"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--recovery-dir",
                str(self.recovery),
                "--output-dir",
                str(output_dir),
                "--expected-config",
                str(self.config),
                "--start-year",
                "2024",
                "--end-year",
                "2024",
                *extra,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

    def _outputs(self, output_dir: Path) -> tuple[Path, Path]:
        parquet = next(output_dir.glob("traffic_training_v1_complete_*.parquet"))
        manifest = output_dir / f"{parquet.stem}_manifest.json"
        self.assertTrue(manifest.is_file())
        return parquet, manifest

    def test_excludes_2023_preserves_recovery_and_publishes_complete_contract(self) -> None:
        before = {
            path.name: _sha256(path)
            for path in sorted(self.recovery.glob("*.parquet"))
        }
        output_dir = self.work / "output"
        completed = self._run(output_dir)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        parquet, manifest_path = self._outputs(output_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["artifact_status"], "complete")
        self.assertEqual(manifest["row_counts"]["selected_recovery_rows"], 4)
        self.assertEqual(manifest["row_counts"]["canonical_rows"], 4)
        self.assertEqual(manifest["outputs"]["parquet"]["rows"], 4)
        self.assertEqual(manifest["coverage"]["missing_scats_date_count"], 1)
        self.assertEqual(manifest["coverage"]["allowed_missing_scats_date_count"], 1)
        self.assertEqual(manifest["coverage"]["unexpected_missing_scats_date_count"], 0)
        self.assertTrue(manifest["assertions"]["all_2023_rows_excluded"])
        self.assertEqual(
            manifest["recovery"]["excluded_source_dataset_ids"],
            ["com_transport_activity_2023", "vic_scats_2023"],
        )
        self.assertEqual(
            manifest["feature_builder_compatibility"]["required_target_columns"],
            [
                "source_dataset_id",
                "observation_unit_id",
                "hour_start_utc",
                "vehicle_count",
                "measurement_scope",
                "label_source",
            ],
        )

        table = pq.read_table(parquet)
        self.assertEqual(table.schema.names, list(OUTPUT_SCHEMA.names))
        rows = table.to_pylist()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["year"] != 2023 for row in rows))
        keys = [
            (row["source_dataset_id"], row["observation_unit_id"], row["hour_start_utc"])
            for row in rows
        ]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            before,
            {path.name: _sha256(path) for path in sorted(self.recovery.glob("*.parquet"))},
        )
        self.assertFalse(list(output_dir.glob(".traffic-recovery-finalize-*")))

    def test_outputs_and_manifest_are_deterministic(self) -> None:
        first_dir = self.work / "first"
        second_dir = self.work / "second"
        first = self._run(first_dir)
        second = self._run(second_dir)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_parquet, first_manifest = self._outputs(first_dir)
        second_parquet, second_manifest = self._outputs(second_dir)
        self.assertEqual(_sha256(first_parquet), _sha256(second_parquet))
        self.assertEqual(
            first_manifest.read_bytes(),
            second_manifest.read_bytes(),
        )

    def test_final_manifest_is_accepted_by_feature_builder(self) -> None:
        output_dir = self.work / "output"
        completed = self._run(output_dir)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        parquet, manifest = self._outputs(output_dir)
        features = self.work / "features"
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONWARNINGS"] = "error"
        built = subprocess.run(
            [
                sys.executable,
                str(FEATURE_BUILDER),
                "--target",
                str(parquet),
                "--canonical-manifest",
                str(manifest),
                "--output-dir",
                str(features),
                "--threads",
                "1",
                "--memory-limit",
                "512MiB",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        training_manifest = json.loads(
            (features / "training_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            training_manifest["inputs"]["canonical_manifest"]["artifact_status"],
            "complete",
        )

    def test_schema_failure_is_fail_closed_and_cleans_staging(self) -> None:
        bad_path = self.recovery / "part-ta-2024.parquet"
        pq.write_table(pa.table({"not_canonical": [1]}), bad_path)
        output_dir = self.work / "output"
        completed = self._run(output_dir)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("schema", completed.stderr.lower())
        self.assertFalse(output_dir.exists())
        self.assertFalse(list(self.work.glob(".traffic-recovery-finalize-*")))

    def test_output_inside_recovery_is_rejected_before_mutation(self) -> None:
        nested_output = self.recovery / "nested-output"
        completed = self._run(nested_output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("outside the immutable recovery directory", completed.stderr)
        self.assertFalse(nested_output.exists())

    def test_selected_partition_with_2023_date_is_rejected(self) -> None:
        _write_partition(
            self.recovery / "part-ta-2024.parquet",
            [_row("com_transport_activity_2024", "transport_activity", "ta-1", dt.date(2023, 12, 31), 0, 10)],
        )
        output_dir = self.work / "output"
        completed = self._run(output_dir)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("outside the bounded contract", completed.stderr)
        self.assertFalse(output_dir.exists())

    def test_explicit_year_boundary_rejects_2023_selection(self) -> None:
        completed = self._run(
            self.work / "outside-year-boundary",
            "--start-year",
            "2023",
            "--start-date",
            "2023-01-01",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("explicit 2024-2026 boundary", completed.stderr)

    def test_null_local_date_is_rejected(self) -> None:
        row = _row(
            "com_transport_activity_2024",
            "transport_activity",
            "ta-null-date",
            dt.date(2024, 1, 1),
            0,
            10,
        )
        row["local_date"] = None
        _write_partition(self.recovery / "part-ta-2024.parquet", [row])
        completed = self._run(self.work / "null-local-date")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("null_temporal", completed.stderr)

    def test_inconsistent_year_is_rejected(self) -> None:
        row = _row(
            "com_transport_activity_2024",
            "transport_activity",
            "ta-inconsistent-year",
            dt.date(2024, 1, 1),
            0,
            10,
        )
        row["year"] = 2025
        _write_partition(self.recovery / "part-ta-2024.parquet", [row])
        completed = self._run(self.work / "inconsistent-year")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("temporal_consistency_rows", completed.stderr)

    def test_excluded_transport_activity_row_is_rejected(self) -> None:
        row = _row(
            "com_transport_activity_2024",
            "transport_activity",
            "ta-excluded",
            dt.date(2024, 1, 1),
            0,
            10,
        )
        row["traffic_eligible"] = False
        row["review_status"] = "excluded"
        _write_partition(self.recovery / "part-ta-2024.parquet", [row])
        completed = self._run(self.work / "excluded-ta")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("source_semantics", completed.stderr)

    def test_mutation_after_duckdb_consumption_is_rejected(self) -> None:
        finalizer = _load_finalizer_module()
        output_dir = self.work / "mutation-detected"
        target = self.recovery / "part-scats-2024.parquet"
        original_bytes = target.read_bytes()
        original_hash = _sha256(target)
        args = finalizer.parse_args(
            [
                "--recovery-dir",
                str(self.recovery),
                "--output-dir",
                str(output_dir),
                "--expected-config",
                str(self.config),
                "--start-year",
                "2024",
                "--end-year",
                "2024",
            ]
        )
        original_writer = finalizer._write_atomic_parquet_batches

        def write_then_mutate(path: Path, batches) -> int:
            written = original_writer(path, batches)
            with target.open("ab") as handle:
                handle.write(b"concurrent mutation")
            return written

        try:
            with mock.patch.object(
                finalizer,
                "_write_atomic_parquet_batches",
                side_effect=write_then_mutate,
            ):
                with self.assertRaises(finalizer.FinalizationError) as raised:
                    finalizer.finalize_recovery(args)
            self.assertIn("mutation detected", str(raised.exception))
            self.assertFalse(output_dir.exists())
        finally:
            target.write_bytes(original_bytes)
        self.assertEqual(_sha256(target), original_hash)

    def test_existing_outputs_are_never_overwritten(self) -> None:
        output_dir = self.work / "no-overwrite"
        first = self._run(output_dir)
        self.assertEqual(first.returncode, 0, first.stderr)
        parquet, manifest = self._outputs(output_dir)
        before = (parquet.read_bytes(), manifest.read_bytes())

        second = self._run(output_dir)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("refusing to overwrite", second.stderr)
        self.assertEqual(before, (parquet.read_bytes(), manifest.read_bytes()))

    def test_zero_eligible_date_requires_explicit_separate_allowance(self) -> None:
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        payload["scats"]["allowed_missing_dates"] = []
        self.config.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected = self._run(self.work / "rejected")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unexpected missing dates", rejected.stderr)

        accepted_dir = self.work / "accepted"
        accepted = self._run(
            accepted_dir,
            "--allow-zero-eligible-scats-date",
            "2024-01-02",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        _, manifest_path = self._outputs(accepted_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["coverage"]["zero_eligible_scats_dates"],
            ["2024-01-02"],
        )
        self.assertEqual(
            manifest["coverage"]["publisher_missing_scats_date_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
