"""Real canopy-density lookup for a single point (the destination's plan-page
condition tile), backed by the same grid baked into the routing graph's
per-edge weights (ml/routing/scripts/build_shade_grid.py).

GET /api/shade?lat=...&lon=...
Response: { "canopy_density": number | null, "status": "ok"|"unavailable" }

Reports a tree-canopy-centroid density proxy, not a precise solar-shade
percentage — never fabricates a value when the point falls outside the
grid's coverage or the grid isn't available.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from _shared import shade_lookup


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            lat = float(query["lat"][0])
            lon = float(query["lon"][0])
        except (KeyError, ValueError, IndexError):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "lat and lon are required"}).encode())
            return

        try:
            density = shade_lookup.point_density(lat, lon)
        except Exception:
            density = None

        result = {
            "canopy_density": density,
            "status": "ok" if density is not None else "unavailable",
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
