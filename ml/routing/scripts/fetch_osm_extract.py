#!/usr/bin/env python3
"""Fetches a walkable-street OSM extract for the inner+middle-ring Melbourne
bbox via the public Overpass API — a single bbox query, not the full-state
Geofabrik PBF (`osm_victoria_pbf` in ml/data/catalog.json), so no new heavy
geospatial dependency (osmium/pyrosm/osmnx) is needed to parse it. See
ml/README.md and the melbourne-metro-v1 coverage note for why.

Filters to standard foot-routing `highway` tags (same set OSRM/GraphHopper's
foot profiles use), excluding anything explicitly closed to pedestrians.
Reshapes Overpass's `out geom;` response into the same GeoJSON
FeatureCollection-of-LineStrings shape build_graph.py already consumes from
the City of Melbourne dataset — no new input format for that script to learn.

Requires ODbL attribution ("(c) OpenStreetMap contributors") wherever this
data is displayed — see src/components/marketing/marketing-page.tsx's
footer / an equivalent attribution surface.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "routing" / "datasets" / "osm_inner_metro"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Inner + middle-ring Melbourne: ~15km radius from the CBD (-37.8136, 144.9631).
# Covers Port Phillip, Yarra, Stonnington, inner Boroondara, Moreland/Merri-bek,
# Maribyrnong, inner Moonee Valley, inner Glen Eira — not full Greater Melbourne.
DEFAULT_BBOX = (-37.9483, 144.7925, -37.6789, 145.1337)  # (south, west, north, east)

# Same tag set OSRM's/GraphHopper's foot-routing profiles use — real streets
# and paths a pedestrian can walk, not motorway/trunk-only infrastructure.
WALKABLE_HIGHWAY_TAGS = [
    "footway", "path", "pedestrian", "steps", "living_street",
    "residential", "service", "unclassified", "tertiary", "secondary", "primary",
]


def build_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    tag_filter = "|".join(WALKABLE_HIGHWAY_TAGS)
    # Excludes ways explicitly closed to pedestrians (foot=no / access=private
    # /access=no) rather than assuming every matched highway tag is walkable.
    return f"""
[out:json][timeout:180][bbox:{south},{west},{north},{east}];
way["highway"~"^({tag_filter})$"]["foot"!="no"]["access"!="private"]["access"!="no"];
out geom;
""".strip()


def fetch_overpass(bbox: tuple[float, float, float, float], timeout: float = 200.0) -> dict:
    query = build_query(bbox)
    data = urllib.parse.urlencode({"data": query}).encode()
    req = Request(OVERPASS_URL, data=data, headers={"User-Agent": "LeafRoute/0.1 (hackathon project)"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def to_feature_collection(overpass_response: dict) -> dict:
    """Reshapes Overpass `out geom;` way elements into the same
    {"type": "FeatureCollection", "features": [{"geometry": {"type":
    "LineString", "coordinates": [[lon, lat], ...]}}, ...]} shape
    build_graph.py already reads from the City of Melbourne dataset."""
    features = []
    for element in overpass_response.get("elements", []):
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry")
        if not geometry:
            continue
        coordinates = [[pt["lon"], pt["lat"]] for pt in geometry]
        if len(coordinates) < 2:
            continue
        features.append({
            "type": "Feature",
            "properties": {"osm_id": element.get("id"), "highway": element.get("tags", {}).get("highway")},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        })
    return {"type": "FeatureCollection", "features": features}


def fetch(output_dir: Path, bbox: tuple[float, float, float, float]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    overpass_response = fetch_overpass(bbox)
    feature_collection = to_feature_collection(overpass_response)

    output_path = output_dir / "osm_extract.json"
    with output_path.open("w") as f:
        json.dump(feature_collection, f)
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--bbox", type=float, nargs=4, default=DEFAULT_BBOX,
        metavar=("SOUTH", "WEST", "NORTH", "EAST"),
    )
    args = parser.parse_args()
    path = fetch(args.output_dir, tuple(args.bbox))
    with path.open() as f:
        feature_count = len(json.load(f)["features"])
    print(f"Wrote {path} ({feature_count:,} ways, {path.stat().st_size:,} bytes)")
