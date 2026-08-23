"""Point queries against the promoted canopy-density grid.

Same grid that's baked into the routing graph's per-edge weights
(ml/routing/scripts/build_shade_grid.py) — a count of real tree canopy
centroids per ~40m cell, normalised 0..1. This is a real, data-derived
"how leafy is this area" proxy, not a solar-shade calculation, and is
labelled "canopy density" rather than "shade %" wherever it's surfaced.
"""

from __future__ import annotations

from . import graph_loader


def point_density(lat: float, lon: float) -> float | None:
    """Returns the canopy-density score (0..1) for the grid cell containing
    (lat, lon), or None if the shade grid isn't available or the point falls
    outside its coverage — never fabricates a value for either case."""
    grid = graph_loader.load_shade_grid()
    if grid is None:
        return None

    col = int((lon - grid["min_lon"]) / grid["cell_deg_lon"])
    row = int((lat - grid["min_lat"]) / grid["cell_deg_lat"])
    if not (0 <= row < grid["rows"] and 0 <= col < grid["cols"]):
        return None
    return grid["density"][row][col]
