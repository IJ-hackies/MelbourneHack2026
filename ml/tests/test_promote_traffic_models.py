"""Focused contract tests for traffic model promotion."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ml" / "scripts" / "promote_traffic_models.py"
SPEC = importlib.util.spec_from_file_location("promote_traffic_models_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - test collection failure.
    raise AssertionError(f"unable to import promotion utility: {SCRIPT}")
PROMOTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROMOTER
SPEC.loader.exec_module(PROMOTER)


GROUPS = (
    ("scats|intersection", "scats", "intersection"),
    ("transport_activity|countline", "transport_activity", "countline"),
)


class FakeBooster:
    def __init__(self, *, feature_count: int, boosted_rounds: int, best_iteration: int) -> None:
        self._feature_count = feature_count
        self._boosted_rounds = boosted_rounds
        self._best_iteration = best_iteration

    def num_features(self) -> int:
        return self._feature_count

    def num_boosted_rounds(self) -> int:
        return self._boosted_rounds

    def attributes(self) -> dict[str, str]:
        return {"best_iteration": str(self._best_iteration)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, object]:
    return {
        "bytes": path.stat().st_size,
        "path": str(path),
        "relative_path": path.name,
        "sha256": _sha256(path),
    }


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class PromotionFixture:
    """Small checksum-complete evaluation fixture with fake UBJSON bytes."""

    def __init__(self, root: Path, *, scats_tie: bool = False) -> None:
        self.root = root
        self.evaluation = root / "evaluation"
        self.models = self.evaluation / "models"
        self.models.mkdir(parents=True)
        self.output = root / "models" / "source-stratified-v1"
        self.model_paths: dict[tuple[str, str], Path] = {}
        self.metadata_paths: dict[tuple[str, str], Path] = {}
        model_records: list[dict[str, str]] = []
        scores: list[dict[str, object]] = []
        actual_devices: dict[str, str] = {}
        self.booster_specs: dict[tuple[str, str], dict[str, int]] = {}

        values = {
            ("base", "scats|intersection"): {
                "validation": (1.0, 2.0, 3.0),
                "test": (100.0, 100.0, 100.0),
            },
            ("lag_enhanced", "scats|intersection"): {
                "validation": (1.0, 2.0, 3.0)
                if scats_tie
                else (2.0, 2.0, 2.0),
                "test": (1.0, 1.0, 1.0),
            },
            ("base", "transport_activity|countline"): {
                "validation": (5.0, 5.0, 5.0),
                "test": (1.0, 1.0, 1.0),
            },
            ("lag_enhanced", "transport_activity|countline"): {
                "validation": (3.0, 3.0, 3.0),
                "test": (100.0, 100.0, 100.0),
            },
        }
        for source_group, label_source, measurement_scope in GROUPS:
            for candidate in PROMOTER.EXPECTED_CANDIDATES:
                key = (candidate, source_group)
                model_path = self.models / f"{candidate}__{label_source}.ubj"
                model_path.write_bytes(f"fake-{candidate}-{source_group}".encode("utf-8"))
                metadata_path = self.models / f"{candidate}__{label_source}.metadata.json"
                model_feature_count = 2 if candidate == "base" else 3
                self.booster_specs[key] = {
                    "feature_count": model_feature_count,
                    "boosted_rounds": 3,
                    "best_iteration": 2,
                }
                metadata = {
                    "schema_version": 1,
                    "candidate": candidate,
                    "dataset": candidate,
                    "source_group": source_group,
                    "label_source": label_source,
                    "measurement_scope": measurement_scope,
                    "target_column": "vehicle_count",
                    "prediction_horizon_hours": 1,
                    "model_sha256": _sha256(model_path),
                    "train_rows": 10,
                    "validation_rows": 20,
                    "test_rows": 30,
                    "boosted_rounds": 3,
                    "best_iteration": 2,
                    "cuda_attempted": True,
                    "cuda_used": True,
                    "device": "cuda",
                    "xgboost_parameters": {"device": "cuda"},
                    "feature_columns": ["feature"],
                    "model_feature_types": ["float"] * model_feature_count,
                    "dataset_path": str(self.evaluation / "training" / f"{candidate}.parquet"),
                    "checksums_file": str(self.evaluation / "checksums.json"),
                    "cache_policy": {
                        "backend": "test",
                        "cache_run_dir": str(self.evaluation / ".xgb-cache" / candidate),
                        "requested_cache_dir": str(self.evaluation / ".xgb-cache"),
                    },
                    "encoder": {
                        "feature_columns": ["feature"],
                        "model_feature_columns": [
                            "feature",
                            *(["feature__extra"] if model_feature_count == 2 else [
                                "feature__extra",
                                "feature__lag",
                            ]),
                        ],
                        "model_feature_types": ["float"] * model_feature_count,
                    },
                }
                _write_json(metadata_path, metadata)
                self.model_paths[key] = model_path
                self.metadata_paths[key] = metadata_path
                model_records.append(
                    {
                        "candidate": candidate,
                        "source_group": source_group,
                        "label_source": label_source,
                        "measurement_scope": measurement_scope,
                        "model_path": str(model_path),
                        "metadata_path": str(metadata_path),
                    }
                )
                actual_devices[f"{candidate}::{source_group}"] = "cuda"
                for split in ("validation", "test"):
                    poisson_deviance, mae, rmse = values[key][split]
                    scores.append(
                        {
                            "candidate": candidate,
                            "dataset": candidate,
                            "group_id": source_group,
                            "label_source": label_source,
                            "measurement_scope": measurement_scope,
                            "scope": "overall",
                            "split": split,
                            "n": 20 if split == "validation" else 30,
                            "poisson_deviance": poisson_deviance,
                            "mae": mae,
                            "rmse": rmse,
                            "target_mean": 12.5,
                        }
                    )

        self.metrics_path = self.evaluation / "metrics.json"
        # This deliberately disagrees with validation for SCATS. The utility
        # must ignore this release block and recompute the winner.
        _write_json(
            self.metrics_path,
            {
                "schema_version": 1,
                "requested_device": "cuda",
                "cuda_used": True,
                "actual_devices": actual_devices,
                "source_groups": [group[0] for group in GROUPS],
                "model_records": model_records,
                "scores": scores,
                "release": {
                    "winner_by_source_group": {
                        "scats|intersection": {"candidate": "lag_enhanced"},
                        "transport_activity|countline": {"candidate": "base"},
                    }
                },
            },
        )

        artifact_paths = [self.metrics_path]
        artifact_paths.extend(self.model_paths.values())
        artifact_paths.extend(self.metadata_paths.values())
        artifacts = [_artifact(path) for path in sorted(artifact_paths)]
        pairs = [
            {
                "model_path": str(self.model_paths[key]),
                "metadata_path": str(self.metadata_paths[key]),
                "model_sha256": _sha256(self.model_paths[key]),
                "metadata_sha256": _sha256(self.metadata_paths[key]),
            }
            for key in sorted(self.model_paths)
        ]
        self.checksums_path = self.evaluation / "checksums.json"
        _write_json(
            self.checksums_path,
            {
                "schema_version": 1,
                "artifacts": artifacts,
                "model_metadata_pairs": pairs,
            },
        )
        self.evaluation_manifest_path = self.evaluation / "evaluation_manifest.json"
        _write_json(
            self.evaluation_manifest_path,
            {
                "schema_version": 1,
                "checksums": str(self.checksums_path),
                "model_paths": [str(path) for path in sorted(self.model_paths.values())],
                "artifacts": artifacts + [_artifact(self.checksums_path)],
            },
        )


class PromoteTrafficModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix=".traffic-promotion-", dir=ROOT)
        self.fixture = PromotionFixture(Path(self.tempdir.name))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _loader(fixture: PromotionFixture, path: Path) -> FakeBooster:
        key = next(key for key, model_path in fixture.model_paths.items() if model_path == path)
        spec = fixture.booster_specs[key]
        return FakeBooster(**spec)

    def _promote(
        self,
        *,
        overwrite: bool = False,
        fixture: PromotionFixture | None = None,
    ) -> dict[str, object]:
        fixture = fixture or self.fixture
        with mock.patch.object(
            PROMOTER,
            "_load_xgboost_model",
            side_effect=lambda path: self._loader(fixture, path),
        ) as load_model:
            manifest = PROMOTER.promote_evaluation(
                fixture.evaluation,
                fixture.output,
                overwrite=overwrite,
            )
        self.assertEqual(load_model.call_count, 4)
        return manifest

    @staticmethod
    def _refresh_metadata_integrity(fixture: PromotionFixture, key: tuple[str, str]) -> None:
        metadata_path = fixture.metadata_paths[key]
        checksums = json.loads(fixture.checksums_path.read_text())
        for artifact in checksums["artifacts"]:
            if Path(artifact["path"]).resolve() == metadata_path.resolve():
                artifact.update(bytes=metadata_path.stat().st_size, sha256=_sha256(metadata_path))
        for pair in checksums["model_metadata_pairs"]:
            if Path(pair["metadata_path"]).resolve() == metadata_path.resolve():
                pair["metadata_sha256"] = _sha256(metadata_path)
        _write_json(fixture.checksums_path, checksums)

        evaluation_manifest = json.loads(fixture.evaluation_manifest_path.read_text())
        for artifact in evaluation_manifest["artifacts"]:
            if Path(artifact["path"]).resolve() == fixture.checksums_path.resolve():
                artifact.update(
                    bytes=fixture.checksums_path.stat().st_size,
                    sha256=_sha256(fixture.checksums_path),
                )
            if Path(artifact["path"]).resolve() == metadata_path.resolve():
                artifact.update(bytes=metadata_path.stat().st_size, sha256=_sha256(metadata_path))
        _write_json(fixture.evaluation_manifest_path, evaluation_manifest)

    def test_validation_wins_and_success_is_complete(self) -> None:
        before = _tree_hashes(self.fixture.evaluation)
        manifest = self._promote()
        after = _tree_hashes(self.fixture.evaluation)

        self.assertEqual(before, after)
        self.assertEqual(manifest["selection_split"], "validation")
        winners = {row["source_group"]: row for row in manifest["source_groups"]}
        self.assertEqual(winners["scats|intersection"]["candidate"], "base")
        self.assertEqual(winners["transport_activity|countline"]["candidate"], "lag_enhanced")
        self.assertEqual(winners["scats|intersection"]["validation_metrics"]["n"], 20)
        self.assertEqual(winners["scats|intersection"]["test_metrics"]["n"], 30)
        self.assertEqual(
            winners["scats|intersection"]["selection_key"],
            [1.0, 2.0, 3.0, 0],
        )
        self.assertEqual(
            (self.fixture.output / "scats-intersection" / "model.ubj").read_bytes(),
            self.fixture.model_paths[("base", "scats|intersection")].read_bytes(),
        )
        self.assertTrue((self.fixture.output / "README.md").is_file())
        self.assertTrue((self.fixture.output / "release_manifest.json").is_file())
        release_readme = (self.fixture.output / "README.md").read_text()
        self.assertIn("validation-only", release_readme)
        self.assertIn(r"scats\|intersection", release_readme)
        self.assertIn(
            "[the traffic software handoff](../../SOFTWARE_HANDOFF.md)",
            release_readme,
        )

        copied_metadata = json.loads(
            (self.fixture.output / "scats-intersection" / "metadata.json").read_text()
        )
        copied_metadata_text = json.dumps(copied_metadata)
        self.assertNotIn(str(ROOT), copied_metadata_text)
        self.assertNotIn("cache_run_dir", copied_metadata["cache_policy"])
        self.assertNotIn("requested_cache_dir", copied_metadata["cache_policy"])
        self.assertEqual(
            copied_metadata["dataset_path"],
            (self.fixture.evaluation / "training" / "base.parquet")
            .relative_to(ROOT)
            .as_posix(),
        )

        checksums = json.loads((self.fixture.output / "checksums.json").read_text())
        self.assertEqual(checksums["selection_split"], "validation")
        for artifact in checksums["artifacts"]:
            path = self.fixture.output / artifact["path"]
            self.assertEqual(path.stat().st_size, artifact["bytes"])
            self.assertEqual(_sha256(path), artifact["sha256"])
            self.assertTrue((self.fixture.output / artifact["release_path"]).is_file())
            self.assertNotIn(".staging-", artifact.get("repository_path", ""))
            if artifact.get("repository_path"):
                self.assertEqual(
                    ROOT / artifact["repository_path"],
                    self.fixture.output / artifact["release_path"],
                )

        scats_winner = next(
            row for row in manifest["source_groups"] if row["source_group"] == "scats|intersection"
        )
        original_metadata_path = self.fixture.metadata_paths[("base", "scats|intersection")]
        self.assertEqual(
            scats_winner["provenance"]["original_artifacts"]["metadata"]["sha256"],
            _sha256(original_metadata_path),
        )

    def test_base_wins_complete_validation_tie_before_lag_enhanced(self) -> None:
        tie_fixture = PromotionFixture(Path(self.tempdir.name) / "tie", scats_tie=True)
        manifest = self._promote(fixture=tie_fixture)
        winners = {row["source_group"]: row for row in manifest["source_groups"]}
        self.assertEqual(winners["scats|intersection"]["candidate"], "base")

    def test_rejects_semantic_metadata_mismatch(self) -> None:
        cases = (
            (
                "target",
                lambda metadata: metadata.update(target_column="flow"),
                "metadata target_column",
            ),
            (
                "horizon",
                lambda metadata: metadata.update(prediction_horizon_hours=2),
                "prediction_horizon_hours",
            ),
            (
                "encoder feature order",
                lambda metadata: metadata["encoder"].update(feature_columns=["other"]),
                "do not equal encoder.feature_columns",
            ),
            (
                "feature type length",
                lambda metadata: metadata["encoder"].update(model_feature_types=["float"]),
                "model_feature_types",
            ),
            (
                "loaded feature count",
                lambda metadata: metadata["encoder"].update(
                    model_feature_columns=["feature", "feature__extra", "feature__wrong"],
                    model_feature_types=["float", "float", "float"],
                )
                or metadata.update(model_feature_types=["float", "float", "float"]),
                "loaded Booster feature count mismatch",
            ),
            (
                "best iteration",
                lambda metadata: metadata.update(best_iteration=3),
                "best_iteration is outside boosted rounds",
            ),
        )
        for name, mutate, expected_error in cases:
            with self.subTest(name=name):
                fixture = PromotionFixture(Path(self.tempdir.name) / name.replace(" ", "-"))
                metadata_path = fixture.metadata_paths[("base", "scats|intersection")]
                metadata = json.loads(metadata_path.read_text())
                mutate(metadata)
                _write_json(metadata_path, metadata)
                self._refresh_metadata_integrity(
                    fixture,
                    ("base", "scats|intersection"),
                )
                with mock.patch.object(
                    PROMOTER,
                    "_load_xgboost_model",
                    side_effect=lambda path, f=fixture: self._loader(f, path),
                ):
                    with self.assertRaisesRegex(PROMOTER.PromotionError, expected_error):
                        PROMOTER.promote_evaluation(fixture.evaluation, fixture.output)
                self.assertFalse(fixture.output.exists())

    def test_checksums_record_final_release_paths(self) -> None:
        self._promote()
        checksums = json.loads((self.fixture.output / "checksums.json").read_text())
        for artifact in checksums["artifacts"]:
            self.assertEqual(artifact["path"], artifact["release_path"])
            self.assertNotIn(".staging-", artifact.get("repository_path", ""))
            self.assertTrue((self.fixture.output / artifact["release_path"]).exists())
            self.assertEqual(
                ROOT / artifact["repository_path"],
                self.fixture.output / artifact["release_path"],
            )

    def test_rejects_model_metadata_integrity_mismatch(self) -> None:
        metadata_path = self.fixture.metadata_paths[("base", "scats|intersection")]
        metadata = json.loads(metadata_path.read_text())
        metadata["model_sha256"] = "0" * 64
        _write_json(metadata_path, metadata)

        checksums = json.loads(self.fixture.checksums_path.read_text())
        for artifact in checksums["artifacts"]:
            if Path(artifact["path"]).resolve() == metadata_path.resolve():
                artifact.update(bytes=metadata_path.stat().st_size, sha256=_sha256(metadata_path))
        for pair in checksums["model_metadata_pairs"]:
            if Path(pair["metadata_path"]).resolve() == metadata_path.resolve():
                pair["metadata_sha256"] = _sha256(metadata_path)
        _write_json(self.fixture.checksums_path, checksums)
        evaluation_manifest = json.loads(self.fixture.evaluation_manifest_path.read_text())
        for artifact in evaluation_manifest["artifacts"]:
            if Path(artifact["path"]).resolve() == self.fixture.checksums_path.resolve():
                artifact.update(
                    bytes=self.fixture.checksums_path.stat().st_size,
                    sha256=_sha256(self.fixture.checksums_path),
                )
            if Path(artifact["path"]).resolve() == metadata_path.resolve():
                artifact.update(bytes=metadata_path.stat().st_size, sha256=_sha256(metadata_path))
        _write_json(self.fixture.evaluation_manifest_path, evaluation_manifest)

        with self.assertRaisesRegex(PROMOTER.PromotionError, "metadata model_sha256"):
            PROMOTER.promote_evaluation(self.fixture.evaluation, self.fixture.output)
        self.assertFalse(self.fixture.output.exists())

    def test_refuses_overwrite_without_flag_and_preserves_release(self) -> None:
        self._promote()
        before = _tree_hashes(self.fixture.output)
        with self.assertRaisesRegex(PROMOTER.PromotionError, "--overwrite"):
            PROMOTER.promote_evaluation(self.fixture.evaluation, self.fixture.output)
        self.assertEqual(before, _tree_hashes(self.fixture.output))

    def test_failed_overwrite_keeps_existing_release_atomically(self) -> None:
        self._promote()
        before = _tree_hashes(self.fixture.output)
        with mock.patch.object(
            PROMOTER,
            "_load_xgboost_model",
            side_effect=PROMOTER.PromotionError("synthetic load failure"),
        ):
            with self.assertRaisesRegex(PROMOTER.PromotionError, "synthetic load failure"):
                PROMOTER.promote_evaluation(
                    self.fixture.evaluation,
                    self.fixture.output,
                    overwrite=True,
                )
        self.assertEqual(before, _tree_hashes(self.fixture.output))


if __name__ == "__main__":
    unittest.main()
