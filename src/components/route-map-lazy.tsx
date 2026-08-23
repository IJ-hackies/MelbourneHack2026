"use client";

import dynamic from "next/dynamic";

// maplibre-gl alone is ~550KB unminified — loading it eagerly blocks
// parsing/executing the rest of this page's client JS (ActiveWalk, the
// share button, turn-by-turn state) behind a library that isn't needed
// until the map itself paints. Splitting it into its own lazily-loaded
// chunk lets everything else on the page hydrate first; `ssr: false` is
// required anyway since MapLibre touches `window` at import time.
export const RouteMap = dynamic(() => import("@/components/route-map").then((m) => m.RouteMap), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-surface-alt" />,
});
