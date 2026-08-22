"""Synthetic CPU contract tests for the traffic training boundary."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - dependency-gated test environment.
    pa = None
    pq = None


ROOT = Path(__file__).resolve().parents[2]
TRAINER = ROOT / "ml" / "scripts" / "train_traffic_models.py"
GROUPS = {
    ("scats", "intersection"),
    ("transport_activity", "countline"),
}
BASE_FEATURES = (
    "observation_unit_id",
    "hour_sin",
    "hour_cos",
    "vehicle_count_lag_1h",
    "vehicle_count_lag_24h",
    "vehicle_count_lag_168h",
    "temperature_c",
)
LAG_FEATURES = BASE_FEATURES + ("pressure_lag_1h",)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_parquet(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    assert pa is not None and pq is not None
    pq.write_table(pa.Table.from_pylist([dict(row) for row in rows]), path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _cuda_stack_available() -> tuple[bool, str]:
    """Probe the exact CuPy/device/XGBoost stack used by the trainer."""

    try:
        import cupy as cupy
        import xgboost as xgboost
    except ImportError as exc:
        return False, f"dependency unavailable: {exc}"
    try:
        if not bool(xgboost.build_info().get("USE_CUDA")):
            return False, "XGBoost was built without CUDA"
        if cupy.cuda.runtime.getDeviceCount() < 1:
            return False, "CuPy reports no CUDA devices"
        probe = cupy.asarray([1.0], dtype=cupy.float32)
        if float(cupy.asnumpy(probe)[0]) != 1.0:
            return False, "CuPy device transfer probe returned an unexpected value"
        cupy.cuda.runtime.deviceSynchronize()
    except Exception as exc:  # Driver/runtime failures are genuine unavailability.
        return False, f"CUDA runtime unavailable: {type(exc).__name__}: {exc}"
    return True, "CUDA stack available"


class TrainTrafficModelsTests(unittest.TestCase):
    """Exercise the complete trainer contract with small deterministic data."""

    def test_winner_key_uses_validation_metrics_not_test_metrics(self) -> None:
        winner_key = runpy.run_path(str(TRAINER))["_winner_key"]
        candidate_order = {"base": 0, "lag_enhanced": 1}
        base = {
            "candidate": "base",
            "validation_metrics": {"poisson_deviance": 9.0, "mae": 9.0, "rmse": 9.0},
            "test_metrics": {"poisson_deviance": 1.0, "mae": 1.0, "rmse": 1.0},
        }
        lag_enhanced = {
            "candidate": "lag_enhanced",
            "validation_metrics": {"poisson_deviance": 2.0, "mae": 2.0, "rmse": 2.0},
            "test_metrics": {"poisson_deviance": 20.0, "mae": 20.0, "rmse": 20.0},
        }

        winner = min(
            (base, lag_enhanced),
            key=lambda result: winner_key(result, candidate_order),
        )
        self.assertEqual(winner["candidate"], "lag_enhanced")

    def test_release_selection_metadata_is_validation_only(self) -> None:
        for winner in self.report["release"]["winner_by_source_group"].values():
            self.assertEqual(winner["selection_split"], "validation")
            validation = winner["validation_metrics"]
            self.assertEqual(
                winner["selection_key"][:3],
                [
                    validation["poisson_deviance"],
                    validation["mae"],
                    validation["rmse"],
                ],
            )

    @classmethod
    def setUpClass(cls) -> None:
        if pa is None or pq is None:
            raise unittest.SkipTest("pyarrow is required for synthetic traffic Parquet fixtures")
        if not TRAINER.exists():
            raise AssertionError(f"expected traffic trainer at {TRAINER}")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="traffic-model-training-")
        cls.work = Path(cls.tempdir.name)
        cls.base_path, cls.lag_path, cls.manifest_path, cls.expected_test_keys = cls._write_fixture()
        cls.output_dir = cls.work / "evaluation"
        cls.first_run = cls._run(overwrite=True)
        if cls.first_run.returncode != 0:
            raise AssertionError(
                "traffic trainer failed\n"
                f"stdout:\n{cls.first_run.stdout}\n"
                f"stderr:\n{cls.first_run.stderr}"
            )
        cls.report = json.loads((cls.output_dir / "metrics.json").read_text(encoding="utf-8"))
        cls.evaluation = json.loads(
            (cls.output_dir / "evaluation_manifest.json").read_text(encoding="utf-8")
        )
        cls.predictions = _read_csv(cls.output_dir / "predictions.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    @classmethod
    def _write_fixture(cls) -> tuple[Path, Path, Path, set[str]]:
        rows: list[dict[str, Any]] = []
        expected_test_keys: set[str] = set()
        periods = (
            (date(2024, 1, 1), 8, "train"),
            (date(2025, 1, 1), 5, "validation"),
            (date(2026, 1, 1), 5, "test"),
            (date(2026, 8, 1), 2, "post_test"),
        )
        for period_index, (start, hours, split) in enumerate(periods):
            observed_units = {
                ("scats", "intersection"): ["scats:001"]
                if split == "train"
                else ["scats:001", "scats:999"],
                ("transport_activity", "countline"): ["ta:001"]
                if split == "train"
                else ["ta:001", "ta:999"],
            }
            for hour_offset in range(hours):
                observed_at = datetime.combine(start, datetime.min.time()) + timedelta(hours=hour_offset)
                for group_index, ((label_source, scope), units) in enumerate(observed_units.items()):
                    for unit_index, unit_id in enumerate(units):
                        key = f"{label_source}:{unit_id}:{observed_at:%Y%m%d%H}"
                        scale = 100 if label_source == "scats" else 18
                        target = scale + period_index * 7 + hour_offset * 2 + unit_index
                        missing_features = split == "test" and unit_id.endswith("999") and hour_offset % 2 == 0
                        row = {
                            "observation_key": key,
                            "observation_unit_id": unit_id,
                            "split": split,
                            "local_date": observed_at.date(),
                            "hour_sin": math.sin(2 * math.pi * observed_at.hour / 24),
                            "hour_cos": math.cos(2 * math.pi * observed_at.hour / 24),
                            "vehicle_count": target,
                            "vehicle_count_lag_1h": None if hour_offset == 0 else target - 2,
                            "vehicle_count_lag_24h": None if hour_offset % 3 == 0 else target - 4,
                            "vehicle_count_lag_168h": None if hour_offset % 4 == 0 else target - 5,
                            "temperature_c": None if missing_features else 20.0 + hour_offset,
                            "pressure_lag_1h": None if missing_features else 1000.0 + hour_offset,
                            "label_source": label_source,
                            "measurement_scope": scope,
                            "prediction_horizon_hours": 1,
                            "label_quality": "partial" if hour_offset == 0 else "complete",
                            "quality_flag": "alarm" if group_index == 0 and hour_offset == 1 else "ok",
                            "quality_partial_flag": hour_offset == 0,
                            "quality_alarm_flag": group_index == 0 and hour_offset == 1,
                            # This deliberately attractive current-hour target
                            # proxy must never be admitted by the feature list.
                            "vehicle_count_same_hour": target * 1000,
                        }
                        rows.append(row)
                        if split == "test":
                            expected_test_keys.add(key)

        base_path = cls.work / "traffic_base.parquet"
        lag_path = cls.work / "traffic_lag_enhanced.parquet"
        _write_parquet(base_path, rows)
        _write_parquet(lag_path, rows)
        manifest = {
            "schema_version": 1,
            "target_column": "vehicle_count",
            "prediction_horizon_hours": 1,
            "columns": {
                "observation_key": "observation_key",
                "split": "split",
                "observation_unit_id": "observation_unit_id",
                "label_source": "label_source",
                "measurement_scope": "measurement_scope",
            },
            "split_contract": {
                "strategy": "chronological",
                "train_end": "2024-12-31",
                "validation_end": "2025-12-31",
                "test_end": "2026-07-31",
                "post_test_policy": "retained but excluded from scores",
            },
            # The list representation is intentional: the feature builder is
            # allowed to emit either a mapping or a list.
            "datasets": [
                {
                    "name": "base",
                    "path": str(base_path),
                    "sha256": _sha256(base_path),
                    "features": list(BASE_FEATURES),
                },
                {
                    "name": "lag_enhanced",
                    "path": str(lag_path),
                    "hash": {"sha256": _sha256(lag_path)},
                    "feature_list": list(LAG_FEATURES),
                },
            ],
        }
        manifest_path = cls.work / "training_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return base_path, lag_path, manifest_path, expected_test_keys

    @classmethod
    def _run(cls, *, overwrite: bool) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(TRAINER),
            "--manifest",
            str(cls.manifest_path),
            "--output-dir",
            str(cls.output_dir),
            "--device",
            "cpu",
            "--small-data",
            "--seed",
            "7",
            "--n-jobs",
            "1",
        ]
        if overwrite:
            command.append("--overwrite")
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    @classmethod
    def _run_manifest_variant(
        cls, payload: Mapping[str, Any], name: str
    ) -> subprocess.CompletedProcess[str]:
        manifest_path = cls.work / f"{name}.json"
        output_dir = cls.work / f"{name}-evaluation"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(TRAINER),
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(output_dir),
                "--device",
                "cpu",
                "--small-data",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_source_stratified_bundle_and_device_are_reported(self) -> None:
        self.assertEqual(set(self.report["source_groups"]), {"scats|intersection", "transport_activity|countline"})
        self.assertEqual(set(self.report["models"]), {"base", "lag_enhanced"})
        for candidate in ("base", "lag_enhanced"):
            self.assertEqual(set(self.report["models"][candidate]), set(self.report["source_groups"]))
        self.assertTrue(self.report["release"]["type"].endswith("source_stratified_models"))
        self.assertEqual(self.report["requested_device"], "cpu")
        self.assertTrue(all(device == "cpu" for device in self.report["actual_devices"].values()))
        self.assertFalse(self.report["cuda_used"])
        self.assertTrue(self.report["memory_policy"]["full_run_uses_all_rows"] is False)

    def test_split_discipline_and_identical_candidate_test_keys(self) -> None:
        by_candidate: dict[str, list[dict[str, str]]] = {}
        for row in self.predictions:
            by_candidate.setdefault(row["candidate"], []).append(row)
            self.assertNotEqual(row["split"], "post_test")
        self.assertEqual(set(by_candidate), {"base", "lag_enhanced"})
        candidate_test_keys = {
            candidate: {row["observation_key"] for row in rows if row["split"] == "test"}
            for candidate, rows in by_candidate.items()
        }
        self.assertEqual(candidate_test_keys["base"], self.expected_test_keys)
        self.assertEqual(candidate_test_keys["base"], candidate_test_keys["lag_enhanced"])
        self.assertEqual(self.report["candidate_test_alignment"]["exact_match"], True)
        self.assertEqual(self.report["candidate_test_alignment"]["label_match"], True)
        for candidate, rows in by_candidate.items():
            dates = {
                split: [date.fromisoformat(row["local_date"]) for row in rows if row["split"] == split]
                for split in ("train", "validation", "test")
            }
            self.assertLess(max(dates["train"]), min(dates["validation"]))
            self.assertLess(max(dates["validation"]), min(dates["test"]))

    def test_metrics_cover_baselines_units_sources_and_quality(self) -> None:
        scores = self.report["scores"]
        baseline = [row for row in scores if row["scope"] == "baseline"]
        self.assertTrue(baseline)
        self.assertEqual({row["baseline"] for row in baseline}, {"lag_1h", "lag_24h", "lag_168h"})
        self.assertTrue(all(0.0 <= float(row["coverage"]) <= 1.0 for row in baseline))
        self.assertTrue(any(row["scope"] == "per_source" for row in scores))
        self.assertTrue(self.report["per_unit_scores"])
        self.assertTrue(any(row["scope"] == "unit_seen_group" and row["unit_id"] == "unseen" for row in scores))
        self.assertTrue(any(row["scope"] == "quality_stratum" for row in scores))
        self.assertTrue(any(row["scope"] == "missingness_stratum" for row in scores))
        self.assertTrue((self.output_dir / "per_unit_metrics.csv").exists())
        self.assertTrue((self.output_dir / "quality_metrics.csv").exists())

    def test_predictions_are_finite_nonnegative_and_unseen_units_are_explicit(self) -> None:
        self.assertTrue(self.predictions)
        for row in self.predictions:
            prediction = float(row["prediction"])
            self.assertTrue(math.isfinite(prediction))
            self.assertGreaterEqual(prediction, 0.0)
        unseen = [row for row in self.predictions if row["split"] == "test" and row["observation_unit_id"].endswith("999")]
        self.assertTrue(unseen)
        self.assertTrue(all(row["unit_seen_in_train"].lower() == "false" for row in unseen))
        for candidate in ("base", "lag_enhanced"):
            for group in self.report["source_groups"]:
                features = self.report["models"][candidate][group]["feature_columns"]
                self.assertNotIn("vehicle_count_same_hour", features)
                self.assertIn("vehicle_count_lag_1h", features)

    def test_hash_schema_horizon_split_and_leakage_contracts_fail_closed(self) -> None:
        cases: list[tuple[str, str, Any]] = []

        bad_hash = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        bad_hash["datasets"][0]["sha256"] = "0" * 64
        cases.append(("bad-hash", "hash mismatch", bad_hash))

        bad_schema = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        bad_schema["datasets"][0]["features"].append("missing_feature_column")
        cases.append(("bad-schema", "missing required columns", bad_schema))

        bad_horizon = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        bad_horizon["prediction_horizon_hours"] = 2
        cases.append(("bad-horizon", "horizon", bad_horizon))

        bad_split = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        bad_split["split_contract"]["train_end"] = "2024-01-31"
        cases.append(("bad-split", "date contract", bad_split))

        bad_leakage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        bad_leakage["datasets"][0]["features"].append("vehicle_count_same_hour")
        cases.append(("bad-leakage", "target-derived", bad_leakage))

        for name, expected_text, payload in cases:
            result = self._run_manifest_variant(payload, name)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertRegex(
                f"{result.stdout}\n{result.stderr}",
                expected_text,
                name,
            )

    def test_static_calendar_coordinate_and_prior_lag_features_remain_allowed(self) -> None:
        validate_features = runpy.run_path(str(TRAINER))["_validate_feature_columns"]
        validate_features(
            "valid-feature-contract",
            (
                "observation_unit_id",
                "channel_type",
                "year",
                "local_hour",
                "latitude",
                "longitude",
                "hour_sin",
                "vehicle_count_lag_1h",
                "vehicle_count_lag_24h",
                "vehicle_count_rolling_past_168h_mean",
            ),
        )

    def test_target_hour_quality_predictors_and_date_boundaries_fail_closed(self) -> None:
        forbidden_features = (
            "label_quality",
            "quality_flag",
            "source_row_count",
            "source_record_count",
            "source_timestamp_count",
            "scats_detector_count",
            "scats_detector_row_count",
            "ta_motor_class_rows",
            "ta_reported_class_rows",
            "scats_alarm_24hour_count",
            "ta_derived_zero",
            "ta_dst_ambiguous_flag",
            "ta_dst_fallback_wrap_flag",
            "vehicle_count_unexpected_summary",
        )
        for feature in forbidden_features:
            with self.subTest(feature=feature):
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                payload["datasets"][0]["features"].append(feature)
                result = self._run_manifest_variant(payload, f"forbidden-{feature}")
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(
                    f"{result.stdout}\n{result.stderr}",
                    r"target-derived|audit",
                )

        source_rows = pq.read_table(self.base_path).to_pylist()
        date_variants = (
            ("train-before-2024", "train", date(2023, 12, 31), "lower date bound"),
            ("validation-before-2025", "validation", date(2024, 12, 31), "lower date bound"),
            ("test-after-july-2026", "test", date(2026, 8, 1), "upper date bound"),
        )
        for name, split, replacement, expected_text in date_variants:
            with self.subTest(date_variant=name):
                rows = [dict(row) for row in source_rows]
                row_index = next(
                    index for index, row in enumerate(rows) if row["split"] == split
                )
                rows[row_index]["local_date"] = replacement
                parquet_path = self.work / f"{name}.parquet"
                _write_parquet(parquet_path, rows)
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                payload["datasets"][0]["path"] = str(parquet_path)
                payload["datasets"][0]["sha256"] = _sha256(parquet_path)
                result = self._run_manifest_variant(payload, name)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(
                    f"{result.stdout}\n{result.stderr}",
                    expected_text,
                )

    def test_models_metadata_and_checksums_are_paired(self) -> None:
        checksums = json.loads((self.output_dir / "checksums.json").read_text(encoding="utf-8"))
        pairs = checksums["model_metadata_pairs"]
        self.assertEqual(len(pairs), 4)
        for pair in pairs:
            model_path = Path(pair["model_path"])
            metadata_path = Path(pair["metadata_path"])
            self.assertTrue(model_path.suffix == ".ubj")
            self.assertTrue(model_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertEqual(_sha256(model_path), pair["model_sha256"])
            self.assertEqual(_sha256(metadata_path), pair["metadata_sha256"])
        artifact_paths = [Path(record["path"]) for record in self.evaluation["artifacts"]]
        self.assertTrue(all(path.exists() for path in artifact_paths))
        self.assertIn("actual_devices", self.evaluation)
        self.assertIn("cuda_used", self.evaluation)

    def test_small_run_is_deterministic_and_overwrite_is_required(self) -> None:
        before = {
            path.relative_to(self.output_dir): _sha256(path)
            for path in self.output_dir.rglob("*")
            if path.is_file()
        }
        second = self._run(overwrite=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        after = {
            path.relative_to(self.output_dir): _sha256(path)
            for path in self.output_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        before_protected = dict(after)
        blocked = self._run(overwrite=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertRegex(f"{blocked.stdout}\n{blocked.stderr}", r"overwrite|exist")
        after_blocked = {
            path.relative_to(self.output_dir): _sha256(path)
            for path in self.output_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_protected, after_blocked)

    def test_default_full_mode_does_not_apply_a_hidden_training_cap(self) -> None:
        output_dir = self.work / "full-evaluation"
        result = subprocess.run(
            [
                sys.executable,
                str(TRAINER),
                "--manifest",
                str(self.manifest_path),
                "--output-dir",
                str(output_dir),
                "--device",
                "cpu",
                "--n-estimators",
                "4",
                "--early-stopping-rounds",
                "2",
                "--n-jobs",
                "1",
                "--batch-size",
                "3",
                "--inspection-batch-size",
                "3",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(report["run_mode"], "full")
        self.assertTrue(report["memory_policy"]["full_run_uses_all_rows"])
        self.assertIsNone(report["memory_policy"]["configured_row_cap"])
        self.assertEqual(report["memory_policy"]["backend"], "xgboost_external_memory")
        self.assertTrue(report["memory_policy"]["candidate_group_split_pushdown"])
        self.assertEqual(report["memory_policy"]["whole_table_pandas_loads"], 0)
        self.assertFalse(report["memory_policy"]["whole_table_pandas_retention"])
        self.assertFalse(report["memory_policy"]["repeated_full_table_pandas_retention"])
        self.assertGreater(report["memory_policy"]["scanner_calls"], 0)
        self.assertGreater(report["memory_policy"]["materialized_cache_reads"], 0)
        self.assertLessEqual(report["memory_policy"]["observed_max_batch_rows"], 3)
        self.assertEqual(report["cache_policy"]["backend"], "xgboost_extmem_quantile_dmatrix")
        self.assertFalse(report["cache_policy"]["cache_on_host"])
        self.assertFalse(report["cache_policy"]["cuda_async_pool"])
        self.assertFalse(report["cache_policy"]["cache_retained_after_run"])
        prediction_rows = _read_csv(output_dir / "predictions.csv")
        self.assertTrue(prediction_rows)
        self.assertNotIn("train", {row["split"] for row in prediction_rows})

    def test_overwrite_is_staged_and_failed_rebuild_preserves_last_complete_release(self) -> None:
        output_dir = self.work / "atomic-evaluation"

        def run(extra: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    sys.executable,
                    str(TRAINER),
                    "--manifest",
                    str(self.manifest_path),
                    "--output-dir",
                    str(output_dir),
                    "--device",
                    "cpu",
                    "--n-jobs",
                    "1",
                    *extra,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )

        initial = run(["--small-data", "--n-estimators", "2", "--overwrite"])
        self.assertEqual(initial.returncode, 0, initial.stderr)
        stale_path = output_dir / "stale-metrics.json"
        stale_path.write_text('{"stale": true}\n', encoding="utf-8")

        replacement = run(["--small-data", "--n-estimators", "2", "--overwrite"])
        self.assertEqual(replacement.returncode, 0, replacement.stderr)
        self.assertFalse(stale_path.exists(), "successful staged publish must replace old files")
        self.assertNotIn(
            ".staging-",
            (output_dir / "metrics.json").read_text(encoding="utf-8"),
        )
        before_failure = {
            path.relative_to(output_dir): _sha256(path)
            for path in output_dir.rglob("*")
            if path.is_file()
        }

        invalid_cache_parent = self.work / "cache-parent-is-a-file"
        invalid_cache_parent.write_text("not a directory\n", encoding="utf-8")
        failed = run(
            [
                "--overwrite",
                "--n-estimators",
                "1",
                "--early-stopping-rounds",
                "0",
                "--batch-size",
                "3",
                "--inspection-batch-size",
                "3",
                "--cache-dir",
                str(invalid_cache_parent),
            ]
        )
        self.assertNotEqual(failed.returncode, 0)
        after_failure = {
            path.relative_to(output_dir): _sha256(path)
            for path in output_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_failure, after_failure)
        self.assertFalse(
            list(output_dir.parent.glob(f".{output_dir.name}.staging-*")),
            "failed rebuild must not leak a partially published staging directory",
        )

    def test_pushdown_dimensions_require_canonical_raw_values(self) -> None:
        source_rows = pq.read_table(self.base_path).to_pylist()
        variants = {
            "noncanonical-split": ("split", " train "),
            "noncanonical-source": ("label_source", " scats "),
            "noncanonical-scope": ("measurement_scope", " intersection "),
        }
        for name, (column, value) in variants.items():
            with self.subTest(column=column):
                rows = [dict(row) for row in source_rows]
                rows[0][column] = value
                parquet_path = self.work / f"{name}.parquet"
                _write_parquet(parquet_path, rows)
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                manifest["datasets"][0]["path"] = str(parquet_path)
                manifest["datasets"][0]["sha256"] = _sha256(parquet_path)
                result = self._run_manifest_variant(manifest, name)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(
                    f"{result.stdout}\n{result.stderr}",
                    r"canonical raw values|exact Parquet pushdown",
                )

    def test_streaming_full_and_all_row_sample_paths_have_prediction_metric_parity(self) -> None:
        full_output = self.work / "parity-full"
        sample_output = self.work / "parity-sampled"

        def run(output_dir: Path, *, sampled: bool) -> subprocess.CompletedProcess[str]:
            command = [
                sys.executable,
                str(TRAINER),
                "--manifest",
                str(self.manifest_path),
                "--output-dir",
                str(output_dir),
                "--device",
                "cpu",
                "--seed",
                "7",
                "--n-estimators",
                "4",
                "--early-stopping-rounds",
                "0",
                "--n-jobs",
                "1",
                "--batch-size",
                "3",
                "--inspection-batch-size",
                "3",
            ]
            if sampled:
                command.extend(["--sample", "10000"])
            return subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )

        full_result = run(full_output, sampled=False)
        self.assertEqual(full_result.returncode, 0, full_result.stderr)
        sample_result = run(sample_output, sampled=True)
        self.assertEqual(sample_result.returncode, 0, sample_result.stderr)
        full_report = json.loads((full_output / "metrics.json").read_text(encoding="utf-8"))
        sample_report = json.loads(
            (sample_output / "metrics.json").read_text(encoding="utf-8")
        )
        full_predictions = {
            (row["candidate"], row["group_id"], row["split"], row["observation_key"]): float(
                row["prediction"]
            )
            for row in _read_csv(full_output / "predictions.csv")
        }
        sample_predictions = {
            (row["candidate"], row["group_id"], row["split"], row["observation_key"]): float(
                row["prediction"]
            )
            for row in _read_csv(sample_output / "predictions.csv")
            if row["split"] in {"validation", "test"}
        }
        self.assertEqual(set(full_predictions), set(sample_predictions))
        for key, prediction in full_predictions.items():
            self.assertAlmostEqual(prediction, sample_predictions[key], delta=1e-5, msg=str(key))

        for candidate in ("base", "lag_enhanced"):
            for group_id in full_report["source_groups"]:
                for metric_set in ("validation_metrics", "test_metrics"):
                    full_metrics = full_report["models"][candidate][group_id][metric_set]
                    sample_metrics = sample_report["models"][candidate][group_id][metric_set]
                    self.assertEqual(full_metrics["n"], sample_metrics["n"])
                    for metric in ("target_mean", "mae", "rmse", "poisson_deviance"):
                        self.assertAlmostEqual(
                            float(full_metrics[metric]),
                            float(sample_metrics[metric]),
                            delta=1e-5,
                            msg=f"{candidate}/{group_id}/{metric_set}/{metric}",
                        )
        full_winners = full_report["release"]["winner_by_source_group"]
        sample_winners = sample_report["release"]["winner_by_source_group"]
        self.assertEqual(
            {group: winner["candidate"] for group, winner in full_winners.items()},
            {group: winner["candidate"] for group, winner in sample_winners.items()},
        )

    def test_full_cuda_request_never_silently_falls_back_to_cpu(self) -> None:
        output_dir = self.work / "cuda-evaluation"
        result = subprocess.run(
            [
                sys.executable,
                str(TRAINER),
                "--manifest",
                str(self.manifest_path),
                "--output-dir",
                str(output_dir),
                "--device",
                "cuda",
                "--n-estimators",
                "1",
                "--early-stopping-rounds",
                "0",
                "--n-jobs",
                "1",
                "--batch-size",
                "3",
                "--inspection-batch-size",
                "3",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        cuda_available, unavailable_reason = _cuda_stack_available()
        if cuda_available:
            self.assertEqual(
                result.returncode,
                0,
                "usable CuPy/CUDA/XGBoost stack must complete the trainer's actual "
                f"external-memory path\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            report = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
            self.assertTrue(report["cuda_used"])
            self.assertTrue(all(device == "cuda" for device in report["actual_devices"].values()))
            self.assertTrue(report["cache_policy"]["cuda_async_pool"])
            prediction_rows = _read_csv(output_dir / "predictions.csv")
            self.assertTrue(prediction_rows)
            for row in prediction_rows:
                prediction = float(row["prediction"])
                self.assertTrue(math.isfinite(prediction))
                self.assertGreaterEqual(prediction, 0.0)
        else:
            if result.returncode == 0:
                report = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
                self.assertTrue(report["cuda_used"])
                self.assertTrue(
                    all(device == "cuda" for device in report["actual_devices"].values())
                )
            else:
                combined = f"{result.stdout}\n{result.stderr}"
                self.assertRegex(combined, r"(?i)cuda|cupy|external.memory")
                self.assertNotIn("device=cpu", combined.lower())
                self.assertFalse((output_dir / "metrics.json").exists())
                self.assertTrue(unavailable_reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
