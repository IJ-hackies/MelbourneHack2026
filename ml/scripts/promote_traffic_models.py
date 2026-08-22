"""Promote validated traffic model winners into a stable release directory.

The evaluation directory is treated as immutable input.  Winners are selected
from the evaluation report's overall chronological validation rows, never
from the report's release block or held-out test rows.  A release is built in
a sibling staging directory and swapped into place only after every selected
model has passed an XGBoost load check.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVALUATION_DIR = ROOT / "ml" / "traffic" / "training" / "evaluation"
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "traffic" / "models" / "source-stratified-v1"
EXPECTED_CANDIDATES = ("base", "lag_enhanced")
CANDIDATE_ORDER = {candidate: index for index, candidate in enumerate(EXPECTED_CANDIDATES)}
SELECTION_METRICS = ("poisson_deviance", "mae", "rmse")
SCORE_FIELDS = ("n", "poisson_deviance", "mae", "rmse", "target_mean")
EXPECTED_TARGET_COLUMN = "vehicle_count"
EXPECTED_HORIZON_HOURS = 1
EPHEMERAL_CACHE_PATH_FIELDS = ("cache_run_dir", "requested_cache_dir")


class PromotionError(RuntimeError):
    """Raised when an evaluation cannot be promoted safely."""


@dataclass(frozen=True)
class Candidate:
    """A checksum-verified candidate and its split metrics."""

    candidate: str
    source_group: str
    label_source: str
    measurement_scope: str
    model_path: Path
    metadata_path: Path
    model_sha256: str
    metadata_sha256: str
    metadata: Mapping[str, Any]
    model_feature_count: int
    validation_metrics: Mapping[str, Any]
    test_metrics: Mapping[str, Any]

    @property
    def selection_key(self) -> tuple[float, float, float, int]:
        return (
            float(self.validation_metrics["poisson_deviance"]),
            float(self.validation_metrics["mae"]),
            float(self.validation_metrics["rmse"]),
            CANDIDATE_ORDER[self.candidate],
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"unable to read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"JSON file must contain an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, payload + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _resolve_inside(path_value: Any, root: Path, *, field: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise PromotionError(f"{field} must be a non-empty path string")
    raw_path = Path(path_value)
    if not raw_path.is_absolute():
        raw_path = root / raw_path
    try:
        resolved = raw_path.resolve(strict=True)
    except OSError as exc:
        raise PromotionError(f"{field} does not exist: {raw_path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PromotionError(f"{field} escapes evaluation directory: {resolved}") from exc
    return resolved


def _resolve_output(path_value: Path) -> Path:
    return path_value.expanduser().resolve(strict=False)


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PromotionError(f"{field} must be an object")
    return value


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PromotionError(f"{field} must be a non-empty string")
    return value


def _require_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PromotionError(f"{field} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PromotionError(f"{field} must be a non-negative integer")
    return value


def _require_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise PromotionError(f"{field} must be a non-empty list of strings")
    return value


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise PromotionError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise PromotionError(f"{field} must be a finite number")
    return number


def _score_snapshot(row: Mapping[str, Any], *, field_prefix: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "n": _require_positive_int(row.get("n"), field=f"{field_prefix}.n"),
    }
    for metric in SELECTION_METRICS:
        result[metric] = _finite_number(row.get(metric), field=f"{field_prefix}.{metric}")
    if row.get("target_mean") is not None:
        result["target_mean"] = _finite_number(
            row["target_mean"],
            field=f"{field_prefix}.target_mean",
        )
    return result


def _relative_to_root(path: Path) -> str | None:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _portable_path(path: Path) -> str:
    repository_path = _relative_to_root(path.resolve(strict=False))
    if repository_path is not None:
        return repository_path
    return path.name


def _path_info(
    path: Path,
    *,
    relative_to: Path | None = None,
    final_path: Path | None = None,
    portable: bool = False,
) -> dict[str, Any]:
    display_path = str(path)
    if portable:
        display_path = _portable_path(path)
    info: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "path": display_path,
        "sha256": _sha256(path),
    }
    if relative_to is not None:
        try:
            display_path = path.relative_to(relative_to).as_posix()
            info["path"] = display_path
            info["relative_path"] = display_path
        except ValueError:
            pass
    root_relative = _relative_to_root((final_path or path).resolve(strict=False))
    if root_relative is not None:
        info["repository_path"] = root_relative
    elif final_path is not None:
        info.pop("repository_path", None)
    return info


def _release_path_info(
    staging_path: Path,
    *,
    staging_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    relative_path = staging_path.relative_to(staging_dir)
    info = _path_info(
        staging_path,
        relative_to=staging_dir,
        final_path=output_dir / relative_path,
    )
    info["release_path"] = relative_path.as_posix()
    return info


def _validate_checksums(
    *,
    evaluation_dir: Path,
    checksums_path: Path,
    checksums: Mapping[str, Any],
    evaluation_manifest: Mapping[str, Any],
) -> tuple[dict[Path, Mapping[str, Any]], dict[tuple[Path, Path], Mapping[str, Any]]]:
    artifacts = checksums.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PromotionError("checksums.json must contain a non-empty artifacts list")

    artifact_by_path: dict[Path, Mapping[str, Any]] = {}
    for index, raw_artifact in enumerate(artifacts):
        artifact = _require_mapping(raw_artifact, field=f"checksums.artifacts[{index}]")
        path = _resolve_inside(
            artifact.get("path"),
            evaluation_dir,
            field=f"checksums.artifacts[{index}].path",
        )
        if path in artifact_by_path:
            raise PromotionError(f"duplicate checksum artifact path: {path}")
        expected_bytes = _require_positive_int(
            artifact.get("bytes"),
            field=f"checksums.artifacts[{index}].bytes",
        )
        expected_sha = _require_string(
            artifact.get("sha256"),
            field=f"checksums.artifacts[{index}].sha256",
        ).lower()
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            raise PromotionError(
                f"checksum byte count mismatch for {path}: "
                f"expected {expected_bytes}, got {actual_bytes}"
            )
        actual_sha = _sha256(path)
        if actual_sha != expected_sha:
            raise PromotionError(
                f"checksum mismatch for {path}: expected {expected_sha}, got {actual_sha}"
            )
        artifact_by_path[path] = artifact

    manifest_artifacts = evaluation_manifest.get("artifacts")
    if not isinstance(manifest_artifacts, list) or not manifest_artifacts:
        raise PromotionError("evaluation_manifest.json must contain a non-empty artifacts list")
    for index, raw_artifact in enumerate(manifest_artifacts):
        artifact = _require_mapping(
            raw_artifact,
            field=f"evaluation_manifest.artifacts[{index}]",
        )
        path = _resolve_inside(
            artifact.get("path"),
            evaluation_dir,
            field=f"evaluation_manifest.artifacts[{index}].path",
        )
        expected_sha = _require_string(
            artifact.get("sha256"),
            field=f"evaluation_manifest.artifacts[{index}].sha256",
        ).lower()
        expected_bytes = _require_positive_int(
            artifact.get("bytes"),
            field=f"evaluation_manifest.artifacts[{index}].bytes",
        )
        checksum_artifact = artifact_by_path.get(path)
        if checksum_artifact is not None:
            if checksum_artifact.get("sha256", "").lower() != expected_sha:
                raise PromotionError(f"manifest/checksum SHA mismatch for {path}")
            if checksum_artifact.get("bytes") != expected_bytes:
                raise PromotionError(f"manifest/checksum byte count mismatch for {path}")
        elif path != checksums_path:
            raise PromotionError(
                f"evaluation manifest artifact is absent from checksums.json: {path}"
            )
        actual_sha = _sha256(path)
        if actual_sha != expected_sha or path.stat().st_size != expected_bytes:
            raise PromotionError(f"evaluation manifest integrity mismatch for {path}")

    manifest_checksums = evaluation_manifest.get("checksums")
    if manifest_checksums is not None:
        manifest_checksums_path = _resolve_inside(
            manifest_checksums,
            evaluation_dir,
            field="evaluation_manifest.checksums",
        )
        if manifest_checksums_path != checksums_path:
            raise PromotionError(
                "evaluation_manifest.checksums does not point to evaluation/checksums.json"
            )

    pairs = checksums.get("model_metadata_pairs")
    if not isinstance(pairs, list) or not pairs:
        raise PromotionError("checksums.json must contain model_metadata_pairs")
    pair_by_paths: dict[tuple[Path, Path], Mapping[str, Any]] = {}
    for index, raw_pair in enumerate(pairs):
        pair = _require_mapping(raw_pair, field=f"checksums.model_metadata_pairs[{index}]")
        model_path = _resolve_inside(
            pair.get("model_path"),
            evaluation_dir,
            field=f"checksums.model_metadata_pairs[{index}].model_path",
        )
        metadata_path = _resolve_inside(
            pair.get("metadata_path"),
            evaluation_dir,
            field=f"checksums.model_metadata_pairs[{index}].metadata_path",
        )
        key = (model_path, metadata_path)
        if key in pair_by_paths:
            raise PromotionError(f"duplicate model/metadata checksum pair: {model_path}")
        model_artifact = artifact_by_path.get(model_path)
        metadata_artifact = artifact_by_path.get(metadata_path)
        if model_artifact is None or metadata_artifact is None:
            raise PromotionError(f"model/metadata pair is absent from checksums artifacts: {key}")
        model_sha = _require_string(
            pair.get("model_sha256"),
            field=f"checksums.model_metadata_pairs[{index}].model_sha256",
        ).lower()
        metadata_sha = _require_string(
            pair.get("metadata_sha256"),
            field=f"checksums.model_metadata_pairs[{index}].metadata_sha256",
        ).lower()
        if model_sha != str(model_artifact.get("sha256", "")).lower():
            raise PromotionError(f"model pair SHA disagrees with artifact checksum: {model_path}")
        if metadata_sha != str(metadata_artifact.get("sha256", "")).lower():
            raise PromotionError(
                f"metadata pair SHA disagrees with artifact checksum: {metadata_path}"
            )
        metadata = _json(metadata_path)
        metadata_model_sha = _require_string(
            metadata.get("model_sha256"),
            field=f"{metadata_path}.model_sha256",
        ).lower()
        if metadata_model_sha != model_sha:
            raise PromotionError(
                f"metadata model_sha256 disagrees with model bytes: {metadata_path}"
            )
        pair_by_paths[key] = pair

    return artifact_by_path, pair_by_paths


def _source_group_parts(source_group: str) -> tuple[str, str]:
    parts = source_group.split("|")
    if len(parts) != 2 or not all(parts):
        raise PromotionError(
            f"source group must have the form label_source|measurement_scope: {source_group}"
        )
    return parts[0], parts[1]


def _validate_metadata_contract(
    metadata: Mapping[str, Any],
    *,
    metadata_path: Path,
    candidate: str,
    source_group: str,
    label_source: str,
    measurement_scope: str,
) -> int:
    """Validate the inference schema recorded beside one candidate model."""

    expected_identity = {
        "candidate": candidate,
        "source_group": source_group,
        "label_source": label_source,
        "measurement_scope": measurement_scope,
    }
    for field, expected in expected_identity.items():
        if metadata.get(field) != expected:
            raise PromotionError(f"metadata {field} mismatch: {metadata_path}")
    if "dataset" in metadata and metadata.get("dataset") != candidate:
        raise PromotionError(f"metadata dataset/candidate mismatch: {metadata_path}")
    if metadata.get("target_column") != EXPECTED_TARGET_COLUMN:
        raise PromotionError(
            f"metadata target_column must be {EXPECTED_TARGET_COLUMN}: {metadata_path}"
        )
    horizon = metadata.get("prediction_horizon_hours")
    if isinstance(horizon, bool) or horizon != EXPECTED_HORIZON_HOURS:
        raise PromotionError(
            f"metadata prediction_horizon_hours must be {EXPECTED_HORIZON_HOURS}: "
            f"{metadata_path}"
        )

    feature_columns = _require_string_list(
        metadata.get("feature_columns"),
        field=f"{metadata_path}.feature_columns",
    )
    encoder = _require_mapping(
        metadata.get("encoder"),
        field=f"{metadata_path}.encoder",
    )
    encoder_feature_columns = _require_string_list(
        encoder.get("feature_columns"),
        field=f"{metadata_path}.encoder.feature_columns",
    )
    if feature_columns != encoder_feature_columns:
        raise PromotionError(
            f"metadata feature_columns do not equal encoder.feature_columns: {metadata_path}"
        )

    encoder_model_feature_columns = _require_string_list(
        encoder.get("model_feature_columns"),
        field=f"{metadata_path}.encoder.model_feature_columns",
    )
    if "model_feature_columns" in metadata:
        top_level_model_columns = _require_string_list(
            metadata.get("model_feature_columns"),
            field=f"{metadata_path}.model_feature_columns",
        )
        if top_level_model_columns != encoder_model_feature_columns:
            raise PromotionError(
                "metadata model_feature_columns do not equal "
                f"encoder.model_feature_columns: {metadata_path}"
            )

    model_feature_types = _require_string_list(
        metadata.get("model_feature_types", encoder.get("model_feature_types")),
        field=f"{metadata_path}.model_feature_types",
    )
    if "model_feature_types" in encoder:
        encoder_model_feature_types = _require_string_list(
            encoder.get("model_feature_types"),
            field=f"{metadata_path}.encoder.model_feature_types",
        )
        if model_feature_types != encoder_model_feature_types:
            raise PromotionError(
                "metadata model_feature_types do not equal "
                f"encoder.model_feature_types: {metadata_path}"
            )
    if len(encoder_model_feature_columns) != len(model_feature_types):
        raise PromotionError(
            "model_feature_columns/model_feature_types length mismatch: "
            f"{metadata_path}"
        )
    return len(encoder_model_feature_columns)


def _validate_cuda_evidence(
    metrics: Mapping[str, Any],
    candidate: Candidate,
) -> None:
    """Require internally consistent CUDA evidence for this release."""

    if metrics.get("requested_device") != "cuda":
        raise PromotionError("CUDA release requires metrics.requested_device=cuda")
    if metrics.get("cuda_used") is not True:
        raise PromotionError("CUDA release requires metrics.cuda_used=true")
    actual_devices = metrics.get("actual_devices")
    if not isinstance(actual_devices, Mapping):
        raise PromotionError("CUDA release requires metrics.actual_devices")
    device_key = f"{candidate.candidate}::{candidate.source_group}"
    if actual_devices.get(device_key) != "cuda":
        raise PromotionError(f"CUDA device evidence mismatch for {device_key}")
    if candidate.metadata.get("cuda_attempted") is not True:
        raise PromotionError(f"metadata cuda_attempted is not true: {candidate.metadata_path}")
    if candidate.metadata.get("cuda_used") is not True:
        raise PromotionError(f"metadata cuda_used is not true: {candidate.metadata_path}")
    if candidate.metadata.get("device") != "cuda":
        raise PromotionError(f"metadata device is not cuda: {candidate.metadata_path}")
    parameters = _require_mapping(
        candidate.metadata.get("xgboost_parameters"),
        field=f"{candidate.metadata_path}.xgboost_parameters",
    )
    if parameters.get("device") != "cuda":
        raise PromotionError(
            f"metadata xgboost_parameters.device is not cuda: {candidate.metadata_path}"
        )


def _extract_scores(metrics: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    scores = metrics.get("scores")
    if not isinstance(scores, list):
        raise PromotionError("metrics.json must contain a scores list")
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, raw_score in enumerate(scores):
        score = _require_mapping(raw_score, field=f"metrics.scores[{index}]")
        if score.get("scope") != "overall" or score.get("split") not in {
            "validation",
            "test",
        }:
            continue
        candidate = _require_string(
            score.get("candidate"),
            field=f"metrics.scores[{index}].candidate",
        )
        if candidate not in EXPECTED_CANDIDATES:
            raise PromotionError(f"unsupported candidate in overall score: {candidate}")
        source_group = _require_string(
            score.get("group_id"),
            field=f"metrics.scores[{index}].group_id",
        )
        split = str(score["split"])
        key = (candidate, source_group, split)
        if key in result:
            raise PromotionError(f"duplicate overall {split} score: {key}")
        result[key] = _score_snapshot(
            score,
            field_prefix=f"metrics.scores[{index}]",
        )
    if not result:
        raise PromotionError("metrics.json has no overall validation/test scores")
    return result


def _load_candidates(
    *,
    evaluation_dir: Path,
    metrics: Mapping[str, Any],
    artifact_by_path: Mapping[Path, Mapping[str, Any]],
    pair_by_paths: Mapping[tuple[Path, Path], Mapping[str, Any]],
) -> dict[str, tuple[Candidate, ...]]:
    scores = _extract_scores(metrics)
    records = metrics.get("model_records")
    if not isinstance(records, list) or not records:
        raise PromotionError("metrics.json must contain model_records")

    records_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw_record in enumerate(records):
        record = _require_mapping(raw_record, field=f"metrics.model_records[{index}]")
        candidate = _require_string(
            record.get("candidate"),
            field=f"metrics.model_records[{index}].candidate",
        )
        source_group = _require_string(
            record.get("source_group"),
            field=f"metrics.model_records[{index}].source_group",
        )
        if candidate not in EXPECTED_CANDIDATES:
            raise PromotionError(f"unsupported candidate in model_records: {candidate}")
        key = (candidate, source_group)
        if key in records_by_key:
            raise PromotionError(f"duplicate model record: {key}")
        records_by_key[key] = record

    source_groups_value = metrics.get("source_groups")
    if source_groups_value is None:
        source_groups = sorted({source_group for _, source_group in records_by_key})
    elif isinstance(source_groups_value, list) and source_groups_value:
        source_groups = sorted(
            {_require_string(value, field="metrics.source_groups") for value in source_groups_value}
        )
    else:
        raise PromotionError("metrics.source_groups must be a non-empty list")

    selected_inputs: dict[str, tuple[Candidate, ...]] = {}
    for source_group in source_groups:
        label_source, measurement_scope = _source_group_parts(source_group)
        candidates: list[Candidate] = []
        for candidate_name in EXPECTED_CANDIDATES:
            record = records_by_key.get((candidate_name, source_group))
            if record is None:
                raise PromotionError(
                    f"source group {source_group} is missing {candidate_name} model record"
                )
            if (
                record.get("label_source") is not None
                and record.get("label_source") != label_source
            ):
                raise PromotionError(
                    f"model record label_source mismatch: {candidate_name}/{source_group}"
                )
            if (
                record.get("measurement_scope") is not None
                and record.get("measurement_scope") != measurement_scope
            ):
                raise PromotionError(
                    f"model record measurement_scope mismatch: {candidate_name}/{source_group}"
                )
            model_path = _resolve_inside(
                record.get("model_path"),
                evaluation_dir,
                field=f"model_records[{candidate_name},{source_group}].model_path",
            )
            metadata_path = _resolve_inside(
                record.get("metadata_path"),
                evaluation_dir,
                field=f"model_records[{candidate_name},{source_group}].metadata_path",
            )
            pair = pair_by_paths.get((model_path, metadata_path))
            if pair is None:
                raise PromotionError(
                    f"model record is absent from model_metadata_pairs: {model_path}"
                )
            model_artifact = artifact_by_path[model_path]
            metadata_artifact = artifact_by_path[metadata_path]
            metadata = _json(metadata_path)
            if metadata.get("candidate") != candidate_name:
                raise PromotionError(f"metadata candidate mismatch: {metadata_path}")
            model_feature_count = _validate_metadata_contract(
                metadata,
                metadata_path=metadata_path,
                candidate=candidate_name,
                source_group=source_group,
                label_source=label_source,
                measurement_scope=measurement_scope,
            )
            validation_metrics = scores.get((candidate_name, source_group, "validation"))
            test_metrics = scores.get((candidate_name, source_group, "test"))
            if validation_metrics is None or test_metrics is None:
                raise PromotionError(
                    f"{candidate_name}/{source_group} must have overall validation and test metrics"
                )
            metadata_validation_rows = _require_positive_int(
                metadata.get("validation_rows"),
                field=f"{metadata_path}.validation_rows",
            )
            metadata_test_rows = _require_positive_int(
                metadata.get("test_rows"),
                field=f"{metadata_path}.test_rows",
            )
            if metadata_validation_rows != validation_metrics["n"]:
                raise PromotionError(f"validation row count mismatch: {metadata_path}")
            if metadata_test_rows != test_metrics["n"]:
                raise PromotionError(f"test row count mismatch: {metadata_path}")
            candidates.append(
                Candidate(
                    candidate=candidate_name,
                    source_group=source_group,
                    label_source=label_source,
                    measurement_scope=measurement_scope,
                    model_path=model_path,
                    metadata_path=metadata_path,
                    model_sha256=str(pair["model_sha256"]).lower(),
                    metadata_sha256=str(pair["metadata_sha256"]).lower(),
                    metadata=metadata,
                    model_feature_count=model_feature_count,
                    validation_metrics=validation_metrics,
                    test_metrics=test_metrics,
                )
            )
            _validate_cuda_evidence(metrics, candidates[-1])
            if str(model_artifact.get("sha256", "")).lower() != candidates[-1].model_sha256:
                raise PromotionError(f"model artifact hash changed during validation: {model_path}")
            if str(metadata_artifact.get("sha256", "")).lower() != candidates[-1].metadata_sha256:
                raise PromotionError(
                    f"metadata artifact hash changed during validation: {metadata_path}"
                )
        selected_inputs[source_group] = tuple(candidates)
    return selected_inputs


def _load_xgboost_model(path: Path) -> Any:
    """Load a UBJSON model through XGBoost as a promotion gate."""

    try:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(path))
        return booster
    except Exception as exc:  # pragma: no cover - exact dependency errors vary by stack.
        raise PromotionError(f"XGBoost could not load {path}: {exc}") from exc


def _validate_loaded_model(candidate: Candidate, booster: Any) -> None:
    """Validate model dimensions and training-round metadata after loading."""

    try:
        model_feature_count = booster.num_features()
    except Exception as exc:  # pragma: no cover - dependency-specific failure.
        raise PromotionError(
            f"loaded Booster does not expose num_features: {candidate.model_path}"
        ) from exc
    if isinstance(model_feature_count, bool) or not isinstance(model_feature_count, int):
        raise PromotionError(f"loaded Booster num_features is invalid: {candidate.model_path}")
    if model_feature_count != candidate.model_feature_count:
        raise PromotionError(
            f"loaded Booster feature count mismatch for {candidate.model_path}: "
            f"metadata={candidate.model_feature_count}, model={model_feature_count}"
        )

    metadata_rounds = _require_positive_int(
        candidate.metadata.get("boosted_rounds"),
        field=f"{candidate.metadata_path}.boosted_rounds",
    )
    try:
        boosted_rounds = booster.num_boosted_rounds()
    except Exception as exc:  # pragma: no cover - dependency-specific failure.
        raise PromotionError(
            f"loaded Booster does not expose boosted rounds: {candidate.model_path}"
        ) from exc
    if isinstance(boosted_rounds, bool) or not isinstance(boosted_rounds, int):
        raise PromotionError(f"loaded Booster boosted rounds are invalid: {candidate.model_path}")
    if boosted_rounds != metadata_rounds:
        raise PromotionError(
            f"boosted round count mismatch for {candidate.model_path}: "
            f"metadata={metadata_rounds}, model={boosted_rounds}"
        )

    best_iteration = _require_nonnegative_int(
        candidate.metadata.get("best_iteration"),
        field=f"{candidate.metadata_path}.best_iteration",
    )
    if best_iteration >= boosted_rounds:
        raise PromotionError(
            f"best_iteration is outside boosted rounds for {candidate.metadata_path}"
        )
    try:
        attributes = booster.attributes()
    except Exception as exc:  # pragma: no cover - dependency-specific failure.
        raise PromotionError(
            f"loaded Booster does not expose attributes: {candidate.model_path}"
        ) from exc
    if not isinstance(attributes, Mapping):
        raise PromotionError(f"loaded Booster attributes are invalid: {candidate.model_path}")
    stored_best_iteration = attributes.get("best_iteration")
    if stored_best_iteration is not None:
        try:
            stored_best_iteration_int = int(stored_best_iteration)
        except (TypeError, ValueError) as exc:
            raise PromotionError(
                f"loaded Booster best_iteration is invalid: {candidate.model_path}"
            ) from exc
        if stored_best_iteration_int != best_iteration:
            raise PromotionError(
                f"best_iteration metadata mismatch for {candidate.model_path}"
            )


def _safe_directory_name(source_group: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "-", source_group).strip("-").lower()
    if not name:
        raise PromotionError(f"source group cannot produce an output directory: {source_group}")
    return name


def _cuda_record(metrics: Mapping[str, Any], candidate: Candidate) -> dict[str, Any]:
    actual_devices = metrics.get("actual_devices")
    if not isinstance(actual_devices, Mapping):
        actual_devices = {}
    device_key = f"{candidate.candidate}::{candidate.source_group}"
    return {
        "actual_device": actual_devices.get(device_key),
        "cuda_attempted": candidate.metadata.get("cuda_attempted"),
        "cuda_used": candidate.metadata.get("cuda_used"),
        "metadata_device": candidate.metadata.get("device"),
        "requested_device": metrics.get("requested_device"),
    }


def _portable_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Copy metadata while removing machine-local provenance from the bundle."""

    portable = copy.deepcopy(dict(metadata))
    for field in ("dataset_path", "checksums_file"):
        value = portable.get(field)
        if isinstance(value, str) and value:
            portable[field] = _portable_path(Path(value)) if Path(value).is_absolute() else value

    cache_policy = portable.get("cache_policy")
    if isinstance(cache_policy, Mapping):
        portable_cache_policy = dict(cache_policy)
        for field in EPHEMERAL_CACHE_PATH_FIELDS:
            portable_cache_policy.pop(field, None)
        portable["cache_policy"] = portable_cache_policy
    return portable


def _original_artifact_info(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    info = _path_info(path, portable=True)
    if info["sha256"].lower() != expected_sha256.lower():
        raise PromotionError(f"source artifact changed during promotion: {path}")
    return info


def _candidate_manifest(
    *,
    candidate: Candidate,
    output_dir: Path,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    directory = _safe_directory_name(candidate.source_group)
    promoted_model = output_dir / directory / "model.ubj"
    promoted_metadata = output_dir / directory / "metadata.json"
    source_model = _original_artifact_info(
        candidate.model_path,
        expected_sha256=candidate.model_sha256,
    )
    source_metadata = _original_artifact_info(
        candidate.metadata_path,
        expected_sha256=candidate.metadata_sha256,
    )
    return {
        "candidate": candidate.candidate,
        "cuda": _cuda_record(metrics, candidate),
        "measurement_scope": candidate.measurement_scope,
        "model": {
            "bytes": promoted_model.stat().st_size,
            "path": f"{directory}/model.ubj",
            "sha256": _sha256(promoted_model),
        },
        "metadata": {
            "bytes": promoted_metadata.stat().st_size,
            "path": f"{directory}/metadata.json",
            "sha256": _sha256(promoted_metadata),
        },
        "provenance": {
            "original_artifacts": {
                "metadata": source_metadata,
                "model": source_model,
            },
            "source_metadata": source_metadata,
            "source_model": source_model,
        },
        "row_counts": {
            "train": _require_positive_int(
                candidate.metadata.get("train_rows"),
                field=f"{candidate.metadata_path}.train_rows",
            ),
            "validation": candidate.validation_metrics["n"],
            "test": candidate.test_metrics["n"],
        },
        "selection_key": list(candidate.selection_key),
        "selection_split": "validation",
        "source_group": candidate.source_group,
        "label_source": candidate.label_source,
        "test_metrics": dict(candidate.test_metrics),
        "validation_metrics": dict(candidate.validation_metrics),
    }


def _markdown_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _readme(
    *,
    candidates: Sequence[Candidate],
) -> str:
    lines = [
        "# Traffic model bundle: source-stratified-v1",
        "",
        "## What this is",
        "",
        "This is HeatRoute's first promoted traffic release. It contains one XGBoost",
        "Poisson model for SCATS intersection totals and another for reviewed Transport",
        "Activity countline volumes. The scales are intentionally not pooled.",
        "",
        "Software integration instructions are in [the traffic software handoff](../../SOFTWARE_HANDOFF.md).",
        "",
        "Both models predict the next hour at fixed observation units. They do not predict",
        "travel time, route-edge congestion, or a pedestrian exposure score; those mappings",
        "and the production inference boundary are not implemented.",
        "",
        "## Artifacts and selection",
        "",
        "Each source directory contains `model.ubj` and its paired `metadata.json`.",
        "`release_manifest.json` records selection, provenance, CUDA evidence, and scores;",
        "`checksums.json` authenticates every release artifact.",
        "",
        "Winners used validation-only selection on the chronological validation split,",
        "using Poisson deviance,",
        "then MAE, then RMSE, with base before lag_enhanced as the deterministic final",
        "tie-break. Held-out test metrics were used only for final reporting.",
        "",
        "| Source group | Winner | Model bytes | SHA-256 |",
        "| --- | --- | ---: | --- |",
    ]
    for candidate in candidates:
        lines.append(
            "| {group} | {variant} | {model_bytes:,} | `{sha256}` |".format(
                group=_markdown_escape(candidate.source_group),
                variant=candidate.candidate,
                model_bytes=candidate.model_path.stat().st_size,
                sha256=candidate.model_sha256,
            )
        )
    lines.extend(
        [
            "",
            "## Training and evaluation",
            "",
            "The source evaluation uses chronological train, validation, and held-out test",
            "splits. CUDA/run evidence and exact split provenance are recorded in",
            "`release_manifest.json`; the selected winner for each group is shown below.",
            "",
            "| Source group | Split | Rows | MAE | RMSE | Poisson deviance |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for candidate in candidates:
        for split, scores in (
            ("Validation", candidate.validation_metrics),
            ("Held-out test", candidate.test_metrics),
        ):
            lines.append(
                "| {group} | {split} | {n:,} | {mae:.4f} | {rmse:.4f} | {deviance:.4f} |".format(
                    group=_markdown_escape(candidate.source_group),
                    split=split,
                    n=scores["n"],
                    mae=scores["mae"],
                    rmse=scores["rmse"],
                    deviance=scores["poisson_deviance"],
                )
            )
    lines.extend(
        [
            "",
            "## Input contract",
            "",
            "Use each `metadata.json` as the source of truth for feature order, train-only",
            "categorical encoder state, missing/unseen category behavior, parameters, best",
            "iteration, and source hashes. Do not pass raw SCATS or countline rows directly",
            "to a model. Serving must reproduce the lag-enhanced one-hour feature boundary.",
            "Missing lag values may remain missing; they must not be converted to zero.",
            "",
            "## Getting and checking the models",
            "",
            "Git LFS is required to materialize both UBJSON files after cloning:",
            "",
            "```bash",
            "git lfs install",
            "git lfs pull --include=\"ml/traffic/models/source-stratified-v1/**/*.ubj\"",
            "sha256sum ml/traffic/models/source-stratified-v1/*/model.ubj",
            "```",
            "",
            "A basic load check is:",
            "",
            "```python",
            "from pathlib import Path",
            "import xgboost as xgb",
            "",
            "for path in Path(\"ml/traffic/models/source-stratified-v1\").glob(\"*/model.ubj\"):",
            "    model = xgb.Booster()",
            "    model.load_model(path)",
            "```",
            "",
            "Loading a model is not the complete inference path; apply the exact paired",
            "metadata transformation and choose the model matching the source group.",
            "",
            "## Limitations",
            "",
            "- These are fixed-site count forecasts, not route-edge traffic or travel time.",
            "- Lag features require a freshness and missing-history policy in serving.",
            "- SCATS and countline outputs are not interchangeable calibration scales.",
            "- There is no calibrated uncertainty, retraining policy, adapter implementation,",
            "  effective-dated route mapping, or production fallback implementation yet.",
            "- The canonical training release deliberately excludes all 2023 data.",
            "- Evaluation and recovery directories remain immutable provenance inputs.",
            "",
        ]
    )
    return "\n".join(lines)


def _publish_atomically(staging_dir: Path, output_dir: Path, *, overwrite: bool) -> None:
    backup_dir: Path | None = None
    if output_dir.exists() or output_dir.is_symlink():
        if not overwrite:
            raise PromotionError(
                f"release directory already exists; pass --overwrite to replace it: {output_dir}"
            )
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise PromotionError(f"release path is not a directory: {output_dir}")
        backup_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.previous-", dir=output_dir.parent)
        )
        backup_dir.rmdir()
        os.replace(output_dir, backup_dir)
    try:
        os.replace(staging_dir, output_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not output_dir.exists():
            os.replace(backup_dir, output_dir)
        raise
    if backup_dir is not None:
        shutil.rmtree(backup_dir)


def promote_evaluation(
    evaluation_dir: Path = DEFAULT_EVALUATION_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate, select, and atomically publish a traffic model bundle."""

    evaluation_dir = evaluation_dir.expanduser().resolve(strict=True)
    output_dir = _resolve_output(output_dir)
    if not evaluation_dir.is_dir():
        raise PromotionError(f"evaluation path is not a directory: {evaluation_dir}")
    try:
        output_dir.relative_to(evaluation_dir)
    except ValueError:
        pass
    else:
        raise PromotionError("output directory must not be inside the evaluation directory")
    if output_dir.exists() or output_dir.is_symlink():
        if not overwrite:
            raise PromotionError(
                f"release directory already exists; pass --overwrite to replace it: {output_dir}"
            )
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise PromotionError(f"release path is not a directory: {output_dir}")

    metrics_path = evaluation_dir / "metrics.json"
    checksums_path = evaluation_dir / "checksums.json"
    evaluation_manifest_path = evaluation_dir / "evaluation_manifest.json"
    metrics = _json(metrics_path)
    checksums = _json(checksums_path)
    evaluation_manifest = _json(evaluation_manifest_path)
    artifact_by_path, pair_by_paths = _validate_checksums(
        evaluation_dir=evaluation_dir,
        checksums_path=checksums_path,
        checksums=checksums,
        evaluation_manifest=evaluation_manifest,
    )
    candidates_by_group = _load_candidates(
        evaluation_dir=evaluation_dir,
        metrics=metrics,
        artifact_by_path=artifact_by_path,
        pair_by_paths=pair_by_paths,
    )
    winners = [
        min(candidates, key=lambda candidate: candidate.selection_key)
        for _, candidates in sorted(candidates_by_group.items())
    ]
    output_directories = [_safe_directory_name(candidate.source_group) for candidate in winners]
    if len(output_directories) != len(set(output_directories)):
        raise PromotionError("source groups collide after output directory normalization")

    for _, candidates in sorted(candidates_by_group.items()):
        for candidate in candidates:
            loaded_model = _load_xgboost_model(candidate.model_path)
            _validate_loaded_model(candidate, loaded_model)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
    )
    try:
        for winner in winners:
            directory = _safe_directory_name(winner.source_group)
            destination = staging_dir / directory
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(winner.model_path, destination / "model.ubj")
            _write_json(
                destination / "metadata.json",
                _portable_metadata(winner.metadata),
            )

        provenance = {
            "checksums_json": _path_info(checksums_path, portable=True),
            "evaluation_dir": _portable_path(evaluation_dir),
            "evaluation_manifest": _path_info(evaluation_manifest_path, portable=True),
            "metrics_json": _path_info(metrics_path, portable=True),
        }
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "release": "source-stratified-v1",
            "selection_split": "validation",
            "selection_metric_order": list(SELECTION_METRICS),
            "candidate_tie_break_order": list(EXPECTED_CANDIDATES),
            "run_mode": metrics.get("run_mode"),
            "split_contract": metrics.get("split_contract"),
            "source_groups": [
                _candidate_manifest(candidate=winner, output_dir=staging_dir, metrics=metrics)
                for winner in winners
            ],
            "cuda_evidence": {
                "evaluation_cuda_used": metrics.get("cuda_used"),
                "evaluation_requested_device": metrics.get("requested_device"),
                "actual_devices": metrics.get("actual_devices", {}),
            },
            "provenance": provenance,
            "script": {
                "path": _portable_path(Path(__file__).resolve()),
                "sha256": _sha256(Path(__file__).resolve()),
            },
            "checksums_path": "checksums.json",
            "readme_path": "README.md",
        }
        _write_json(staging_dir / "release_manifest.json", manifest)
        _atomic_write_text(
            staging_dir / "README.md",
            _readme(candidates=winners),
        )
        release_artifacts = [
            _release_path_info(
                path,
                staging_dir=staging_dir,
                output_dir=output_dir,
            )
            for path in sorted(staging_dir.rglob("*"))
            if path.is_file()
        ]
        checksums_payload = {
            "schema_version": 1,
            "artifacts": release_artifacts,
            "selection_split": "validation",
        }
        _write_json(staging_dir / "checksums.json", checksums_payload)
        _publish_atomically(staging_dir, output_dir, overwrite=overwrite)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-dir",
        type=Path,
        default=DEFAULT_EVALUATION_DIR,
        help=f"completed evaluation directory (default: {DEFAULT_EVALUATION_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"release directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="atomically replace an existing release directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = promote_evaluation(
            args.evaluation_dir,
            args.output_dir,
            overwrite=args.overwrite,
        )
    except (OSError, PromotionError) as exc:
        print(f"traffic model promotion failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"promoted {len(manifest['source_groups'])} traffic model groups to "
        f"{Path(args.output_dir).expanduser().resolve()} using validation-only selection"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
