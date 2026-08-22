"""Loads and verifies the promoted XGBoost releases.

Per ml/crowd/SOFTWARE_HANDOFF.md and ml/traffic/SOFTWARE_HANDOFF.md: the
software adapter must verify the model artifact's SHA-256 against the value
recorded in its metadata before loading, and refuse to start rather than
serve an unverified binary.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import xgboost as xgb

# Vercel Python functions use the project root as the working directory, so
# relative paths from there resolve the same locally (via `vercel dev`) and
# in production.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_booster_cache: dict[str, xgb.Booster] = {}
_metadata_cache: dict[str, dict[str, Any]] = {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_metadata(metadata_path: str) -> dict[str, Any]:
    if metadata_path not in _metadata_cache:
        with (PROJECT_ROOT / metadata_path).open() as f:
            _metadata_cache[metadata_path] = json.load(f)
    return _metadata_cache[metadata_path]


def load_model(model_path: str, model_bytes: int, model_sha256: str) -> xgb.Booster:
    """Verify and load a promoted model.ubj, caching the booster at module
    scope so a warm Fluid Compute instance reuses it across invocations."""
    if model_path in _booster_cache:
        return _booster_cache[model_path]

    full_path = PROJECT_ROOT / model_path
    actual_bytes = full_path.stat().st_size
    if actual_bytes != model_bytes:
        raise RuntimeError(
            f"{model_path}: expected {model_bytes} bytes, found {actual_bytes} "
            "(Git LFS pointer instead of the real file? confirm 'lfs: true' "
            "ran during checkout/deploy)"
        )

    actual_sha256 = _sha256_file(full_path)
    if actual_sha256 != model_sha256:
        raise RuntimeError(
            f"{model_path}: SHA-256 mismatch (expected {model_sha256}, got "
            f"{actual_sha256}) — refusing to load an unverified model."
        )

    booster = xgb.Booster()
    booster.load_model(str(full_path))
    _booster_cache[model_path] = booster
    return booster
