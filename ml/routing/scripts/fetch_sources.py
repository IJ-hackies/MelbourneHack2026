#!/usr/bin/env python3
"""Fetches the City of Melbourne Pedestrian Network export.

Per ml/data/catalog.json's `com_pedestrian_network` entry: "Primary hackathon
graph; static and limited to the municipality." This is the sole input to
build_graph.py for V1 — it's a plain GeoJSON LineString network covering
exactly the City of Melbourne bbox, so no OSM/Vicmap supplement or heavy
geospatial parsing (osmnx/geopandas/pyrosm) is needed to produce a real,
walkable routing graph. A wider-coverage OSM merge remains a documented
fast-follow for routes outside the municipality (see README.md).
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
DATASET_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "pedestrian-network/alternative_exports/pedestrian_network_zip"
)
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "routing" / "datasets" / "com_pedestrian_network"


def fetch(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "pedestrian_network.zip"

    req = Request(DATASET_URL, headers={"User-Agent": "LeafRoute/0.1 (hackathon project)"})
    with urlopen(req, timeout=30) as resp, zip_path.open("wb") as f:
        f.write(resp.read())

    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("Pedestrian_network.json", output_dir)

    return output_dir / "Pedestrian_network.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    path = fetch(args.output_dir)
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)")
