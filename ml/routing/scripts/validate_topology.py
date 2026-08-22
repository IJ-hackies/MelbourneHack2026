#!/usr/bin/env python3
"""Validates the built graph's connectivity before it's eligible for promotion.

STATE.md calls out "explicit topology validation" as required before treating
municipal pedestrian network data as production-ready. This fails loudly
(non-zero exit) if too much of the graph is disconnected from the dominant
component, rather than silently promoting a broken graph.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_graph import largest_component_ratio  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "ml" / "routing" / "processed" / "graph_raw.json"
MIN_CONNECTED_RATIO = 0.99


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--min-ratio", type=float, default=MIN_CONNECTED_RATIO)
    args = parser.parse_args()

    with args.input.open() as f:
        graph = json.load(f)

    ratio, largest_size = largest_component_ratio(graph["adjacency"])
    total = len(graph["node_coords"])
    print(f"Largest connected component: {largest_size:,}/{total:,} nodes ({ratio:.4%})")

    if ratio < args.min_ratio:
        print(
            f"FAIL: connectivity {ratio:.4%} is below the required {args.min_ratio:.2%} "
            "threshold — refusing to promote this graph.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("OK: connectivity meets threshold.")


if __name__ == "__main__":
    main()
