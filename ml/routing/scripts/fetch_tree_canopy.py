#!/usr/bin/env python3
"""Fetches City of Melbourne tree canopy point locations.

Per ml/data/catalog.json's `com_tree_canopy_2021` entry (Tree Canopies 2021
Urban Forest, CC BY 4.0). Only `geo_point_2d` (each canopy polygon's
centroid) is fetched, not the full polygon geometry — a centroid-density
grid is enough to derive a real, if approximate, "how leafy is this street"
score without a geospatial (shapely) runtime dependency, and keeps the fetch
to one bulk CSV export instead of ~580 paginated polygon requests.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
EXPORT_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "tree-canopies-2021-urban-forest/exports/csv?select=geo_point_2d&use_labels=false"
)
DEFAULT_OUTPUT = ROOT / "ml" / "routing" / "datasets" / "tree_canopy_points.json"


def fetch(output_path: Path) -> Path:
    req = Request(EXPORT_URL, headers={"User-Agent": "LeafRoute/0.1 (hackathon project)"})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8-sig")

    points: list[list[float]] = []
    reader = csv.reader(io.StringIO(raw), delimiter=";")
    header = next(reader)
    if header != ["geo_point_2d"]:
        # Opendatasoft CSV exports use ';' by default; fall back to ',' if
        # the dataset ever changes its export delimiter.
        reader = csv.reader(io.StringIO(raw))
        header = next(reader)

    for row in reader:
        if not row or not row[0].strip():
            continue
        lat_str, lon_str = row[0].split(",")
        points.append([float(lon_str.strip()), float(lat_str.strip())])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump({"points": points}, f)

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    path = fetch(args.output)
    with path.open() as f:
        count = len(json.load(f)["points"])
    print(f"Wrote {path} ({count:,} canopy points)")
