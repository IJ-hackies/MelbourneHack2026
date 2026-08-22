"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useLiveLocation } from "@/lib/use-live-location";
import { useLiveProgress } from "@/lib/live-progress-context";
import type { Coordinates, RouteGeometry, RouteOption } from "@/lib/providers/types";

// Free, keyless vector tile style — no account or API key required, in
// keeping with the app's existing no-vendor-key pattern (Nominatim, Open-Meteo).
const OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const WALKING_SPEED_M_PER_MIN = 80;

function haversineM(a: Coordinates, b: Coordinates): number {
  const r = 6371000;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h));
}

// Remaining distance from a live position to the destination: nearest-vertex
// snap onto the real path if one exists, otherwise a straight line to the
// end point. An approximation (nearest vertex, not nearest point-on-segment)
// but the real path already has dense vertices, so it's a reasonable one.
function remainingMetres(position: Coordinates, geometry: RouteGeometry): number {
  const path = geometry.path;
  if (!path || path.length === 0) {
    return haversineM(position, geometry.end);
  }
  let nearestIndex = 0;
  let nearestDist = Infinity;
  for (let i = 0; i < path.length; i++) {
    const d = haversineM(position, path[i]);
    if (d < nearestDist) {
      nearestDist = d;
      nearestIndex = i;
    }
  }
  let remaining = nearestDist;
  for (let i = nearestIndex; i < path.length - 1; i++) {
    remaining += haversineM(path[i], path[i + 1]);
  }
  return remaining;
}

export function RouteMap({
  geometry,
  segments,
}: {
  geometry: RouteGeometry;
  segments?: RouteOption["segments"];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const startMarkerRef = useRef<maplibregl.Marker | null>(null);
  const liveMarkerRef = useRef<maplibregl.Marker | null>(null);
  const followingRef = useRef(true);
  const recenterControlRef = useRef<HTMLButtonElement | null>(null);

  const liveLocation = useLiveLocation(true);
  const { setProgress } = useLiveProgress();

  // Mounts the map and draws the static route once per geometry/segments
  // change — deliberately does NOT depend on live location, so a GPS tick
  // never tears down and rebuilds the whole map.
  useEffect(() => {
    if (!containerRef.current) return;

    const waypoints = geometry.path?.length ? geometry.path : [geometry.start, geometry.end];
    const lineCoordinates = waypoints.map((c) => [c.lon, c.lat] as [number, number]);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OPENFREEMAP_STYLE_URL,
      // Set explicitly so the camera starts over the actual route even if
      // fitBounds below never runs (a silent style/network failure
      // previously left the map on its style's built-in default view — the
      // whole world at zoom 0 — instead of failing loudly or falling back
      // to something sane).
      center: [geometry.start.lon, geometry.start.lat],
      zoom: 14,
    });
    mapRef.current = map;
    followingRef.current = true;

    // Manual pan/zoom suspends camera-follow until the user asks to resume.
    map.on("dragstart", () => {
      followingRef.current = false;
      recenterControlRef.current?.classList.remove("hidden");
    });
    map.on("zoomstart", (e) => {
      if (e.originalEvent) {
        followingRef.current = false;
        recenterControlRef.current?.classList.remove("hidden");
      }
    });

    const fitToRoute = () => {
      try {
        map.resize(); // guards against a 0-size container at construction time
        const bounds = lineCoordinates.reduce(
          (b, coord) => b.extend(coord),
          new maplibregl.LngLatBounds(lineCoordinates[0], lineCoordinates[0])
        );
        map.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 0 });

        if (!map.getSource("route-line")) {
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

          startMarkerRef.current = new maplibregl.Marker({ color: "#0e6e64" })
            .setLngLat([geometry.start.lon, geometry.start.lat])
            .addTo(map);
          new maplibregl.Marker({ color: "#e8703a" })
            .setLngLat([geometry.end.lon, geometry.end.lat])
            .addTo(map);
        }
      } catch (err) {
        // Surface failures in devtools instead of silently sitting on the
        // default view with no indication anything went wrong.
        console.error("RouteMap: failed to render route", err);
      }
    };

    // isStyleLoaded() covers the case where the style finished loading
    // before this listener was attached (a real MapLibre race condition —
    // "load" only fires once, and is missed entirely if registered late).
    if (map.isStyleLoaded()) {
      fitToRoute();
    } else {
      map.on("load", fitToRoute);
    }

    return () => {
      map.remove();
      mapRef.current = null;
      startMarkerRef.current = null;
      liveMarkerRef.current = null;
    };
    // geometry/segments intentionally re-mount the map on change rather than
    // diffing sources in place — route details are read once per navigation.
  }, [geometry, segments]);

  // Live position updates: a separate, lighter effect that only moves an
  // existing marker and (optionally) recenters the camera — never touches
  // the map/route setup above.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || liveLocation.status !== "tracking") return;

    const { lat, lon } = liveLocation;

    // Replaces the static start marker the first time a fix arrives, per
    // the confirmed decision — avoids two overlapping markers at the
    // common case where the user starts at the route origin.
    if (startMarkerRef.current) {
      startMarkerRef.current.remove();
      startMarkerRef.current = null;
    }

    if (!liveMarkerRef.current) {
      const el = document.createElement("div");
      el.className = "h-4 w-4 rounded-full border-2 border-white bg-blue-500 shadow-md";
      liveMarkerRef.current = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    } else {
      liveMarkerRef.current.setLngLat([lon, lat]);
    }

    if (followingRef.current) {
      map.easeTo({ center: [lon, lat], duration: 500 });
    }

    const remainingM = remainingMetres({ lat, lon }, geometry);
    setProgress({
      distanceRemainingKm: Math.round((remainingM / 1000) * 100) / 100,
      etaMinutes: Math.round(remainingM / WALKING_SPEED_M_PER_MIN),
    });
  }, [liveLocation, geometry, setProgress]);

  const handleRecenter = () => {
    followingRef.current = true;
    recenterControlRef.current?.classList.add("hidden");
    if (mapRef.current && liveLocation.status === "tracking") {
      mapRef.current.easeTo({ center: [liveLocation.lon, liveLocation.lat], duration: 500 });
    }
  };

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      <button
        ref={recenterControlRef}
        type="button"
        onClick={handleRecenter}
        className="absolute right-3 bottom-3 hidden rounded-full bg-surface px-3 py-1.5 text-xs font-semibold text-text shadow-md"
      >
        Recenter
      </button>
      {liveLocation.status === "denied" && (
        <span className="absolute top-3 left-3 rounded-lg bg-surface/90 px-2.5 py-1 text-[0.72rem] text-text-tertiary">
          Location permission denied
        </span>
      )}
      {liveLocation.status === "unavailable" && (
        <span className="absolute top-3 left-3 rounded-lg bg-surface/90 px-2.5 py-1 text-[0.72rem] text-text-tertiary">
          Location unavailable
        </span>
      )}
    </div>
  );
}
