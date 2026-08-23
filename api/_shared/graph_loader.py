"""Loads and verifies the promoted pedestrian routing graph.

Same trust contract as api/_shared/model_loader.py: verify the artifact's
SHA-256/byte count against its metadata before loading, and refuse to serve
an unverified graph rather than silently loading whatever's on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = "ml/routing/models/melbourne-inner-v1/metadata.json"

_graph_cache: dict[str, Any] | None = None
_metadata_cache: dict[str, Any] | None = None
_shade_grid_cache: dict[str, Any] | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_metadata() -> dict[str, Any]:
    global _metadata_cache
    if _metadata_cache is None:
        with (PROJECT_ROOT / METADATA_PATH).open() as f:
            _metadata_cache = json.load(f)
    return _metadata_cache


def load_graph() -> dict[str, Any]:
    """Returns {"node_coords": [[lon, lat], ...],
                "adjacency": [[[neighbor_id, weight_m, canopy_density], ...], ...]}.

    Cached at module scope so a warm Fluid Compute instance reuses it across
    invocations — checksum verification only happens once per cold start.
    """
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache

    metadata = load_metadata()
    graph_path = PROJECT_ROOT / "ml" / "routing" / "models" / "melbourne-inner-v1" / metadata["graph_file"]

    actual_bytes = graph_path.stat().st_size
    if actual_bytes != metadata["graph_bytes"]:
        raise RuntimeError(
            f"{graph_path}: expected {metadata['graph_bytes']} bytes, found {actual_bytes} "
            "(Git LFS pointer instead of the real file, or a stale build?)"
        )

    actual_sha256 = _sha256_file(graph_path)
    if actual_sha256 != metadata["graph_sha256"]:
        raise RuntimeError(
            f"{graph_path}: SHA-256 mismatch (expected {metadata['graph_sha256']}, got "
            f"{actual_sha256}) — refusing to load an unverified graph."
        )

    with graph_path.open() as f:
        _graph_cache = json.load(f)
    return _graph_cache


def load_shade_grid() -> dict[str, Any] | None:
    """Returns the canopy-density grid (see ml/routing/scripts/build_shade_grid.py),
    or None if the promoted graph predates it (metadata has no shade fields) —
    callers must treat that as an honest "no shade data" case, not an error."""
    global _shade_grid_cache
    if _shade_grid_cache is not None:
        return _shade_grid_cache

    metadata = load_metadata()
    if "shade_grid_file" not in metadata:
        return None

    grid_path = PROJECT_ROOT / "ml" / "routing" / "models" / "melbourne-inner-v1" / metadata["shade_grid_file"]

    actual_bytes = grid_path.stat().st_size
    if actual_bytes != metadata["shade_grid_bytes"]:
        raise RuntimeError(f"{grid_path}: expected {metadata['shade_grid_bytes']} bytes, found {actual_bytes}")

    actual_sha256 = _sha256_file(grid_path)
    if actual_sha256 != metadata["shade_grid_sha256"]:
        raise RuntimeError(f"{grid_path}: SHA-256 mismatch — refusing to load an unverified shade grid.")

    with grid_path.open() as f:
        _shade_grid_cache = json.load(f)
    return _shade_grid_cache
