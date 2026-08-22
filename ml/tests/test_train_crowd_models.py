"""Synthetic contract tests for the crowd-model training/evaluation command.

The feature-table builder is intentionally a separate boundary from model
training.  These tests therefore invoke the expected script as a subprocess
and pass tiny Parquet tables plus a manifest-shaped feature list.  The
expected command is::

    python ml/scripts/train_crowd_models.py \
        --all-history PATH \
        --recent-enhanced PATH \
        --manifest PATH \
        --output-dir PATH \
        --cpu --small-data --overwrite

Equivalent long option names are accepted by the test helpers so the command
can evolve without weakening the data contract.  The command must emit a JSON
evaluation report and prediction artifacts for the all-history candidate, the
recent-enhanced candidate, and a matched recent-window all-history ablation.
The report is expected to include the feature lists used, chronological split
metadata, overall/per-sensor/missingness-stratified metrics, and the common
test-key comparison.  Prediction rows must retain ``observation_key``,
``sensor_id``, ``split``, the observed target, and a non-negative prediction.

This file deliberately does not add a model implementation.  It is a small,
deterministic acceptance suite for the training boundary described by
``Context/Chunks/ml/crowd-training.md`` and ``ml/crowd/README.md``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # The training pipeline consumes the same Parquet contract as the builder.
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised only in minimal environments.
    pa = None
    pq = None


ROOT = Path(__file__).resolve().parents[2]
TRAINER = ROOT / "ml" / "scripts" / "train_crowd_models.py"
RECENT_START = date(2024, 1, 1)

# These are the only model features declared by the synthetic manifest.  The
# fixture also carries deliberately attractive leakage columns; the trainer
# must follow the manifest instead of taking every numeric Parquet column.
BASE_FEATURES = (
    "hour_sin",
    "hour_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "flow_lag_1h",
    "flow_lag_24h",
    "flow_rolling_past_24h_mean",
    "nasa_temperature_c",
)
ENHANCED_FEATURES = BASE_FEATURES + (
    "micro_temperature_c_lag_1h",
    "micro_relative_humidity_pct_lag_1h",
    "transport_pedestrian_count_lag_1h",
    "transport_observation_count_lag_1h",
)

LEAKAGE_NAMES = {
    "direction_1_count",
    "direction_2_count",
    "direction_1",
    "direction_2",
    "pedestrian_flow_same_hour",
    "same_hour_target",
    "target_copy",
    "transport_pedestrian_count",
    "micro_temperature_c",
}


def _normalise(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _write_parquet(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    assert pa is not None and pq is not None
    pq.write_table(pa.Table.from_pylist([dict(row) for row in rows]), path)
    return path


def _is_null(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text).replace(tzinfo=None)


def _as_number(value: Any) -> float:
    if value is None or _is_null(value):
        raise AssertionError(f"expected a numeric value, got {value!r}")
    return float(value)


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    """Yield every JSON node with its key path."""

    yield path, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, str(index)))


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str).lower()


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        assert pq is not None
        return [dict(row) for row in pq.read_table(path).to_pylist()]
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("predictions", "rows", "data", "records"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _key(row: Mapping[str, Any]) -> str:
    for name in ("observation_key", "key", "obs_key", "observation_id"):
        if name in row and row[name] is not None:
            return str(row[name])
    raise AssertionError(f"prediction row has no observation key: {row}")


def _sensor(row: Mapping[str, Any]) -> str:
    for name in ("sensor_id", "sensor", "location_id"):
        if name in row and row[name] is not None:
            return str(row[name])
    raise AssertionError(f"prediction row has no sensor id: {row}")


def _split(row: Mapping[str, Any]) -> str:
    for name in ("split", "dataset_split", "partition", "fold"):
        if name in row and row[name] is not None:
            return _normalise(row[name])
    raise AssertionError(f"prediction row has no split label: {row}")


def _timestamp(row: Mapping[str, Any]) -> datetime:
    for name in ("observed_at_local", "timestamp_local", "observed_at", "timestamp"):
        if name in row and row[name] is not None:
            return _as_datetime(row[name])
    if "local_date" in row and "local_hour" in row:
        local_date = row["local_date"]
        if not isinstance(local_date, date):
            local_date = date.fromisoformat(str(local_date))
        return datetime.combine(local_date, datetime.min.time()) + timedelta(
            hours=int(row["local_hour"])
        )
    raise AssertionError(f"prediction row has no timestamp: {row}")


def _actual(row: Mapping[str, Any]) -> float:
    for name in ("pedestrian_flow", "actual", "target", "y_true", "observed_flow"):
        if name in row and not _is_null(row[name]):
            return _as_number(row[name])
    raise AssertionError(f"prediction row has no actual target: {row}")


def _prediction(row: Mapping[str, Any]) -> float:
    for name in (
        "prediction",
        "predicted",
        "predicted_flow",
        "prediction_pedestrian_flow",
        "y_pred",
        "estimate",
    ):
        if name in row and not _is_null(row[name]):
            return _as_number(row[name])
    raise AssertionError(f"prediction row has no prediction value: {row}")


def _candidate(row: Mapping[str, Any], source_name: str = "") -> str:
    values = " ".join(
        str(row.get(name, ""))
        for name in ("model", "model_name", "candidate", "dataset", "feature_set")
    )
    values = _normalise(f"{values} {source_name}")
    if "recent" in values and ("ablation" in values or "baseline" in values):
        return "matched_recent_ablation"
    if "recent_enhanced" in values or ("enhanced" in values and "recent" in values):
        return "recent_enhanced"
    if "all_history" in values or "allhistory" in values or "history" in values:
        return "all_history"
    if "ablation" in values or "matched" in values:
        return "matched_recent_ablation"
    return "unknown"


def _mean_poisson_deviance(actual: Sequence[float], prediction: Sequence[float]) -> float:
    """Compute sklearn's mean Poisson deviance without requiring sklearn."""

    terms: list[float] = []
    for observed, estimate in zip(actual, prediction):
        if observed < 0 or estimate <= 0:
            raise AssertionError(
                f"Poisson deviance requires y >= 0 and prediction > 0; got {observed}, {estimate}"
            )
        # For y == 0, y * log(y / mu) is defined as zero.
        terms.append(2.0 * (estimate if observed == 0 else observed * math.log(observed / estimate) - (observed - estimate)))
    return sum(terms) / len(terms)


def _metric_key_matches(name: str, aliases: Sequence[str]) -> bool:
    normalised = _normalise(name)
    return any(normalised == alias or alias in normalised for alias in aliases)


def _metric_value(section: Any, aliases: Sequence[str], *, split: str = "test") -> float | None:
    """Find a split metric in a model section while accepting naming aliases."""

    split_nodes: list[Any] = []
    fallback_nodes: list[Any] = []
    for path, node in _walk(section):
        path_text = "_".join(_normalise(part) for part in path)
        if isinstance(node, Mapping):
            if split in path_text or any(
                _normalise(key) in {split, f"{split}_metrics", f"metrics_{split}"}
                for key in node
            ):
                split_nodes.append(node)
            fallback_nodes.append(node)
    for nodes in (split_nodes, fallback_nodes):
        for node in nodes:
            for path, value in _walk(node):
                if path and _metric_key_matches(path[-1], aliases) and isinstance(value, (int, float)):
                    return float(value)
    return None


def _field_value(section: Any, aliases: Sequence[str]) -> Any:
    for path, value in _walk(section):
        if path and _normalise(path[-1]) in aliases:
            return value
    return None


def _model_section(report: Mapping[str, Any], candidate: str) -> Mapping[str, Any]:
    aliases = {
        "all_history": {"all_history", "allhistory", "history", "baseline"},
        "recent_enhanced": {"recent_enhanced", "recentenhanced", "enhanced"},
    }[candidate]
    containers: list[Any] = []
    for path, node in _walk(report):
        if isinstance(node, Mapping) and path and _normalise(path[-1]) in {
            "models",
            "candidates",
            "results",
            "model_results",
        }:
            containers.append(node)
    containers.append(report)
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key, value in container.items():
            normalised = _normalise(key)
            if normalised in aliases or any(alias in normalised for alias in aliases):
                if isinstance(value, Mapping):
                    return value
    # A list of records with a ``candidate``/``model`` field is also a clean
    # representation; return the first matching record.
    for path, node in _walk(report):
        if isinstance(node, Mapping):
            label = _candidate(node)
            if label == candidate:
                return node
    raise AssertionError(f"evaluation report has no {candidate} model section: {_json_text(report)[:2000]}")


class TrainCrowdModelsTests(unittest.TestCase):
    """End-to-end contract checks on deterministic, CPU-sized fixtures."""

    @classmethod
    def setUpClass(cls) -> None:
        if pa is None or pq is None:
            raise unittest.SkipTest("pyarrow is required for synthetic Parquet fixtures")
        if not TRAINER.exists():
            raise AssertionError(
                f"expected crowd model trainer at {TRAINER}; implement the CLI contract in the module docstring"
            )
        cls.tempdir = tempfile.TemporaryDirectory(prefix="crowd-model-training-")
        cls.work = Path(cls.tempdir.name)
        cls.all_path, cls.recent_path, cls.manifest_path, cls.expected = cls._write_fixture()
        cls.output_dir = cls.work / "artifacts"
        cls.output_dir.mkdir()
        cls.first_run = cls._run_trainer(overwrite=True)
        cls.report_path, cls.report = cls._load_report()
        cls.predictions = cls._load_predictions()

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    @classmethod
    def _write_fixture(cls) -> tuple[Path, Path, Path, dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        expected: dict[str, Any] = {"rows": {}, "test_keys": set(), "test_sensors": {"1", "2", "99"}}
        periods = (
            (date(2023, 1, 1), 3, "train", (1, 2)),  # history excluded by the matched ablation
            (date(2024, 1, 1), 6, "train", (1, 2)),
            (date(2025, 1, 1), 4, "validation", (1, 2, 99)),
            (date(2026, 1, 1), 4, "test", (1, 2, 99)),
            (date(2026, 6, 1), 2, "post_test", (1, 2)),  # must never enter claimed scores
        )
        for period_index, (start_date, hours, split, sensors) in enumerate(periods):
            for offset in range(hours):
                observed_at = datetime.combine(start_date, datetime.min.time()) + timedelta(hours=offset)
                for sensor_id in sensors:
                    flow = 35 + period_index * 11 + offset * 3 + sensor_id
                    optional_missing = split == "test" and (
                        sensor_id == 99 or (sensor_id == 1 and offset == 1)
                    )
                    row = {
                        "observation_key": f"{sensor_id}:{observed_at:%Y%m%d%H}",
                        "source_dataset_id": "synthetic-canonical-target",
                        "source_record_id": f"synthetic-{sensor_id}-{observed_at:%Y%m%d%H}",
                        "sensor_id": sensor_id,
                        "sensor_name": f"Synthetic sensor {sensor_id}",
                        "local_date": observed_at.date(),
                        "local_hour": observed_at.hour,
                        "observed_at_local": observed_at,
                        "feature_asof_local": observed_at - timedelta(hours=1),
                        "prediction_horizon_hours": 1,
                        "pedestrian_flow": flow,
                        "split": split,
                        "is_dst": False,
                        "dst_ambiguous_local_time": False,
                        "dst_nonexistent_local_time": False,
                        "hour_was_reconstructed": False,
                        "hour_sin": math.sin(2 * math.pi * observed_at.hour / 24),
                        "hour_cos": math.cos(2 * math.pi * observed_at.hour / 24),
                        "day_of_week_sin": math.sin(2 * math.pi * observed_at.weekday() / 7),
                        "day_of_week_cos": math.cos(2 * math.pi * observed_at.weekday() / 7),
                        "flow_lag_1h": flow - 2,
                        "flow_lag_24h": flow - 4,
                        "flow_rolling_past_24h_mean": float(flow - 1),
                        "nasa_temperature_c": None if optional_missing and sensor_id == 1 else 20.0 + offset,
                        "micro_temperature_c_lag_1h": None if optional_missing else 18.0 + offset,
                        "micro_relative_humidity_pct_lag_1h": None if optional_missing else 55.0,
                        "transport_pedestrian_count_lag_1h": None if optional_missing else 100.0 + offset,
                        "transport_observation_count_lag_1h": None if optional_missing else 4.0,
                        # Deliberately huge same-hour/leakage values.  A model
                        # that ignores the manifest can appear unrealistically
                        # accurate, so this also tests feature-list adherence.
                        "direction_1_count": flow * 1000,
                        "direction_2_count": flow * 1000,
                        "pedestrian_flow_same_hour": flow * 1000,
                        "same_hour_target": flow * 1000,
                        "target_copy": flow * 1000,
                        "transport_pedestrian_count": flow * 1000,
                        "micro_temperature_c": flow * 1000,
                    }
                    all_rows.append(row)
                    expected["rows"][row["observation_key"]] = row
                    if split == "test":
                        expected["test_keys"].add(row["observation_key"])

        all_path = cls.work / "crowd_training_all_history.parquet"
        recent_path = cls.work / "crowd_training_recent_enhanced.parquet"
        _write_parquet(all_path, all_rows)
        recent_rows = [row for row in all_rows if row["local_date"] >= RECENT_START]
        _write_parquet(recent_path, recent_rows)
        manifest_path = cls.work / "training_manifest.json"
        manifest = {
            "schema_version": 1,
            "target": {
                "column": "pedestrian_flow",
                "unit": "people per sensor-hour",
                "meaning": "pedestrian flow past a fixed counter; not area crowd density",
            },
            "split_contract": {
                "strategy": "chronological",
                "train_end": "2024-12-31",
                "validation_end": "2025-12-31",
                "test_end": "2026-05-11",
                "post_test_policy": "retained but excluded from the common comparison",
            },
            "fair_comparison": {
                "test_period": "2026-01-01 through 2026-05-11",
                "matched_recent_ablation": "filter all_history to local_date >= 2024-01-01",
                "note": "Score candidates on identical observation_key values.",
            },
            "datasets": {
                "all_history": {
                    "path": str(all_path),
                    "feature_columns": list(BASE_FEATURES),
                    "recent_start": str(RECENT_START),
                },
                "recent_enhanced": {
                    "path": str(recent_path),
                    "feature_columns": list(ENHANCED_FEATURES),
                    "recent_start": str(RECENT_START),
                },
            },
            "excluded_leakage": sorted(LEAKAGE_NAMES),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return all_path, recent_path, manifest_path, expected

    @classmethod
    def _help_text(cls) -> str:
        result = subprocess.run(
            [sys.executable, str(TRAINER), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return f"{result.stdout}\n{result.stderr}"

    @classmethod
    def _choose_flag(cls, help_text: str, options: Sequence[str], *, required: bool = True) -> str | None:
        for option in options:
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(option)}(?:[ =\[,]|$)", help_text):
                return option
        if required:
            raise AssertionError(f"trainer help has none of {options!r}:\n{help_text}")
        return None

    @classmethod
    def _run_trainer(cls, *, overwrite: bool) -> subprocess.CompletedProcess[str]:
        help_text = cls._help_text()
        command = [sys.executable, str(TRAINER)]
        command += [
            cls._choose_flag(help_text, ("--all-history", "--all-history-table", "--all-history-path")) or "--all-history",
            str(cls.all_path),
            cls._choose_flag(help_text, ("--recent-enhanced", "--recent-enhanced-table", "--enhanced-table")) or "--recent-enhanced",
            str(cls.recent_path),
            cls._choose_flag(help_text, ("--manifest", "--training-manifest", "--feature-manifest")) or "--manifest",
            str(cls.manifest_path),
            cls._choose_flag(help_text, ("--output-dir", "--output", "--artifacts-dir")) or "--output-dir",
            str(cls.output_dir),
        ]
        cpu_flag = cls._choose_flag(help_text, ("--cpu", "--cpu-only"), required=False)
        if cpu_flag:
            command.append(cpu_flag)
        else:
            device_flag = cls._choose_flag(help_text, ("--device",), required=False)
            if device_flag:
                command += [device_flag, "cpu"]
            else:
                raise AssertionError("trainer must expose an explicit CPU-only mode")
        small_flag = cls._choose_flag(help_text, ("--small-data", "--ci", "--fast"), required=False)
        if small_flag:
            command.append(small_flag)
        else:
            max_rows = cls._choose_flag(help_text, ("--max-rows", "--max-train-rows"), required=False)
            if max_rows:
                command += [max_rows, "96"]
            else:
                raise AssertionError("trainer must expose --small-data or an equivalent bounded-data mode")
        jobs_flag = cls._choose_flag(help_text, ("--n-jobs", "--threads", "--workers"), required=False)
        if jobs_flag:
            command += [jobs_flag, "1"]
        seed_flag = cls._choose_flag(help_text, ("--random-state", "--seed"), required=False)
        if seed_flag:
            command += [seed_flag, "7"]
        if overwrite:
            overwrite_flag = cls._choose_flag(help_text, ("--overwrite", "--force"), required=False)
            if overwrite_flag:
                command.append(overwrite_flag)
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    @classmethod
    def _load_report(cls) -> tuple[Path, Mapping[str, Any]]:
        json_files = sorted(cls.output_dir.rglob("*.json"))
        candidates: list[tuple[int, Path, Mapping[str, Any]]] = []
        for path in json_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            text = _json_text(payload)
            score = sum(token in text for token in ("poisson", "mae", "rmse", "all_history", "recent_enhanced"))
            if score >= 4:
                candidates.append((score, path, payload))
        if not candidates:
            files = [str(path) for path in json_files]
            raise AssertionError(f"trainer emitted no evaluation JSON report; JSON files={files}")
        candidates.sort(key=lambda item: (-item[0], str(item[1])))
        _, path, payload = candidates[0]
        return path, payload

    @classmethod
    def _prediction_paths_from_section(cls, section: Any) -> list[Path]:
        paths: list[Path] = []
        for path, value in _walk(section):
            if not isinstance(value, str):
                continue
            key_text = "_".join(_normalise(part) for part in path)
            if not any(token in key_text for token in ("prediction", "predictions", "artifact", "output")):
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = cls.output_dir / candidate
            if candidate.exists() and candidate.suffix.lower() in {".parquet", ".csv", ".json"}:
                paths.append(candidate)
        return list(dict.fromkeys(paths))

    @classmethod
    def _load_predictions(cls) -> dict[str, list[dict[str, Any]]]:
        paths: list[Path] = []
        for _, section in ((name, _model_section(cls.report, name)) for name in ("all_history", "recent_enhanced")):
            paths.extend(cls._prediction_paths_from_section(section))
        if not paths:
            paths = [
                path
                for path in sorted(cls.output_dir.rglob("*"))
                if path.is_file()
                and path.suffix.lower() in {".parquet", ".csv", ".json"}
                and "manifest" not in path.name.lower()
                and "metric" not in path.name.lower()
                and "report" not in path.name.lower()
                and "summary" not in path.name.lower()
            ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for path in dict.fromkeys(paths):
            rows = _read_rows(path)
            for row in rows:
                label = _candidate(row, path.name)
                if label != "unknown":
                    grouped.setdefault(label, []).append(row)
        if "all_history" not in grouped or "recent_enhanced" not in grouped:
            raise AssertionError(
                f"trainer emitted no predictions for both candidates; files={paths}; groups={grouped.keys()}"
            )
        return grouped

    def _assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            result.returncode,
            0,
            f"crowd model trainer failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_cpu_small_data_run_emits_both_candidates_and_report(self) -> None:
        self._assert_success(self.first_run)
        report_text = _json_text(self.report)
        self.assertIn("all_history", report_text)
        self.assertIn("recent_enhanced", report_text)
        self.assertRegex(report_text, r"cpu|n.?jobs|thread")
        self.assertRegex(report_text, r"small|bounded|max.?rows|ci")
        for candidate in ("all_history", "recent_enhanced"):
            section = _model_section(self.report, candidate)
            features = _field_value(section, ("feature_columns", "features", "feature_list", "predictors", "used_features"))
            self.assertIsInstance(features, list, f"{candidate} report omitted used feature list")
            self.assertTrue(features, f"{candidate} feature list is empty")

    def test_splits_are_chronological_and_post_test_is_not_scored(self) -> None:
        report_text = _json_text(self.report)
        self.assertRegex(report_text, r"chronolog|time.?ordered|as.?of")
        self.assertIn("post_test", report_text)
        for candidate, rows in self.predictions.items():
            if candidate not in ("all_history", "recent_enhanced"):
                continue
            by_split: dict[str, list[datetime]] = {}
            for row in rows:
                by_split.setdefault(_split(row), []).append(_timestamp(row))
            for split in ("train", "validation", "test"):
                self.assertIn(split, by_split, f"{candidate} predictions omitted {split} split")
            self.assertLess(max(by_split["train"]), min(by_split["validation"]))
            self.assertLess(max(by_split["validation"]), min(by_split["test"]))
            self.assertNotIn("post_test", by_split, f"{candidate} post-test rows must not enter evaluation predictions")

    def test_declared_feature_lists_are_used_and_leakage_is_excluded(self) -> None:
        expected_features = {
            "all_history": set(BASE_FEATURES),
            "recent_enhanced": set(ENHANCED_FEATURES),
        }
        for candidate, expected in expected_features.items():
            section = _model_section(self.report, candidate)
            actual = _field_value(section, ("feature_columns", "features", "feature_list", "predictors", "used_features"))
            self.assertIsInstance(actual, list)
            self.assertEqual(set(actual), expected, f"{candidate} ignored the manifest feature list")
            for feature in actual:
                normalised = _normalise(feature)
                self.assertNotIn(normalised, LEAKAGE_NAMES, f"same-hour leakage feature selected: {feature}")
                self.assertFalse(
                    ("direction" in normalised and "count" in normalised)
                    or ("same_hour" in normalised and "target" in normalised)
                    or normalised in {"pedestrian_flow", "target", "target_copy"},
                    f"target leakage feature selected: {feature}",
                )

    def test_candidates_score_identical_test_keys_and_matched_recent_ablation(self) -> None:
        expected_test_keys = set(self.expected["test_keys"])
        candidate_keys: dict[str, set[str]] = {}
        for candidate in ("all_history", "recent_enhanced"):
            rows = [row for row in self.predictions[candidate] if _split(row) == "test"]
            candidate_keys[candidate] = {_key(row) for row in rows}
            self.assertEqual(candidate_keys[candidate], expected_test_keys, f"{candidate} did not score the shared test keys")
        self.assertEqual(candidate_keys["all_history"], candidate_keys["recent_enhanced"])
        report_text = _json_text(self.report)
        self.assertRegex(report_text, r"matched.?recent|recent.?ablation")
        self.assertRegex(report_text, r"identical|shared.*test|common.*test|observation.?key")
        ablation_text = " ".join(
            _json_text(node)
            for path, node in _walk(self.report)
            if path and ("ablation" in "_".join(_normalise(part) for part in path) or "matched_recent" in "_".join(_normalise(part) for part in path))
        )
        self.assertRegex(ablation_text or report_text, r"test|metric|score")

    def test_predictions_are_nonnegative_and_reported_metrics_are_correct(self) -> None:
        for candidate in ("all_history", "recent_enhanced"):
            rows = [row for row in self.predictions[candidate] if _split(row) == "test"]
            actual = [_actual(row) for row in rows]
            prediction = [_prediction(row) for row in rows]
            self.assertTrue(rows)
            self.assertTrue(all(value >= 0 for value in prediction), f"{candidate} emitted a negative crowd prediction")
            mae = sum(abs(y - yhat) for y, yhat in zip(actual, prediction)) / len(rows)
            rmse = math.sqrt(sum((y - yhat) ** 2 for y, yhat in zip(actual, prediction)) / len(rows))
            poisson = _mean_poisson_deviance(actual, prediction)
            section = _model_section(self.report, candidate)
            reported_mae = _metric_value(section, ("mae", "mean_absolute_error"))
            reported_rmse = _metric_value(section, ("rmse", "root_mean_squared_error"))
            reported_poisson = _metric_value(
                section,
                ("poisson_deviance", "mean_poisson_deviance", "poisson_loss", "deviance"),
            )
            self.assertIsNotNone(reported_mae, f"{candidate} omitted test MAE")
            self.assertIsNotNone(reported_rmse, f"{candidate} omitted test RMSE")
            self.assertIsNotNone(reported_poisson, f"{candidate} omitted test Poisson deviance")
            self.assertAlmostEqual(float(reported_mae), mae, places=6)
            self.assertAlmostEqual(float(reported_rmse), rmse, places=6)
            self.assertAlmostEqual(float(reported_poisson), poisson, places=6)

    def test_per_sensor_and_missingness_stratified_metrics_are_exposed(self) -> None:
        report_text = _json_text(self.report)
        self.assertRegex(report_text, r"per.?sensor|sensor.?metrics")
        for sensor_id in self.expected["test_sensors"]:
            self.assertIn(sensor_id, report_text, f"report omitted sensor {sensor_id}")
        self.assertRegex(report_text, r"missingness|missing_features|feature_missing")
        self.assertRegex(report_text, r"complete|present|observed")
        self.assertRegex(report_text, r"missing|null|incomplete")
        # The fixture guarantees both groups on the common test rows.  A
        # prediction artifact may carry the explicit group; otherwise the
        # report must carry it as a stratified metric key.
        for candidate in ("all_history", "recent_enhanced"):
            rows = [row for row in self.predictions[candidate] if _split(row) == "test"]
            groups = {
                str(row.get(name))
                for row in rows
                for name in ("missingness_group", "feature_missingness", "missing_features")
                if name in row and not _is_null(row[name])
            }
            if groups:
                self.assertGreaterEqual(len(groups), 2, f"{candidate} predictions collapsed missingness strata")

    def test_validation_test_only_sensor_is_predicted_and_seen_unseen_is_reported(self) -> None:
        for candidate in ("all_history", "recent_enhanced"):
            test_rows = [row for row in self.predictions[candidate] if _split(row) == "test"]
            self.assertIn("99", {_sensor(row) for row in test_rows}, f"{candidate} failed on unseen sensor 99")
        report_text = _json_text(self.report)
        self.assertRegex(report_text, r"seen.?sensor|sensor.?seen")
        self.assertRegex(report_text, r"unseen.?sensor|sensor.?unseen|cold.?start")
        # The training fixture contains sensors 1/2 only and 99 first appears
        # in validation.  A report that labels every test sensor as seen is a
        # leakage-prone evaluation summary.
        self.assertRegex(report_text, r"99.{0,160}(unseen|cold)|(?:unseen|cold).{0,160}99")

    def test_existing_outputs_are_protected_without_overwrite(self) -> None:
        tracked = sorted(path for path in self.output_dir.rglob("*") if path.is_file())
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
        second = self._run_trainer(overwrite=False)
        self.assertNotEqual(second.returncode, 0, "a second run must require --overwrite")
        self.assertRegex(f"{second.stdout}\n{second.stderr}", r"overwrite|exist|force")
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
        self.assertEqual(before, after, "overwrite protection must not mutate existing artifacts")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
