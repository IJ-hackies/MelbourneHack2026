"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { RouteGeometry, RouteOption } from "@/lib/providers/types";

// Free, keyless vector tile style — no account or API key required, in
// keeping with the app's existing no-vendor-key pattern (Nominatim, Open-Meteo).
const OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

export function RouteMap({
  geometry,
  segments,
}: {
  geometry: RouteGeometry;
  segments?: RouteOption["segments"];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OPENFREEMAP_STYLE_URL,
    });
    mapRef.current = map;

    const waypoints = geometry.path?.length
      ? geometry.path
      : [geometry.start, geometry.end];
    const lineCoordinates = waypoints.map((c) => [c.lon, c.lat] as [number, number]);

    map.on("load", () => {
      map.addSource("route-line", {
        type: "geojson",
        data: {
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: lineCoordinates },
        },
      });
      // Segment tones are cosmetic labels only for V1 (no per-segment
      // geometry exists yet) — draw a single neutral line rather than
      // fabricating a segment-colored path the data doesn't support.
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route-line",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#0e6e64", "line-width": 4 },
      });

      new maplibregl.Marker({ color: "#0e6e64" })
        .setLngLat([geometry.start.lon, geometry.start.lat])
        .addTo(map);
      new maplibregl.Marker({ color: "#e8703a" })
        .setLngLat([geometry.end.lon, geometry.end.lat])
        .addTo(map);

      const bounds = lineCoordinates.reduce(
        (b, coord) => b.extend(coord),
        new maplibregl.LngLatBounds(lineCoordinates[0], lineCoordinates[0])
      );
      map.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 0 });
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // geometry/segments intentionally re-mount the map on change rather than
    // diffing sources in place — route details are read once per navigation.
  }, [geometry, segments]);

  return <div ref={containerRef} className="h-full w-full" />;
}
