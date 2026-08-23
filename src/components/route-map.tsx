"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useLiveLocation } from "@/lib/use-live-location";
import { useLiveProgress } from "@/lib/live-progress-context";
import type { Coordinates, RouteGeometry, RouteOption } from "@/lib/providers/types";

// Three vector-tile providers (Mapbox, MapTiler, OpenFreeMap) each hit
// failures that took real requests down to a blank canvas — an ad-blocker
// intercepting protobuf/XHR-style tile fetches was confirmed for two of
// them, and the third stayed unexplained even with the blocker off. Vector
// rendering has a lot of moving parts (protobuf parsing, WebGL layer
// compositing, sprite/glyph/style-spec resolution) for any one of those to
// silently break. Plain raster PNG tiles sidestep all of that — just
// <img>-style GET requests, the same technology most "just works" web maps
// have used for 15+ years. CARTO's free, keyless "Voyager" basemap (built
// on OSM data) is used instead of the default OSM Mapnik style — flatter,
// less visually busy, closer to the Google-Maps-style look originally asked
// for than Mapnik's more literal/"realistic" rendering.
const RASTER_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    basemap: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "https://d.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "basemap-tiles", type: "raster", source: "basemap", minzoom: 0, maxzoom: 20 }],
};
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
  const liveMarkerRef = useRef<maplibregl.Marker | null>(null);
  const followingRef = useRef(true);
  const recenterControlRef = useRef<HTMLButtonElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const liveLocation = useLiveLocation(true);
  const { setProgress } = useLiveProgress();

  // Mounts the map and draws the static route once per geometry/segments
  // change — deliberately does NOT depend on live location, so a GPS tick
  // never tears down and rebuilds the whole map. No start marker: per
  // product decision, live location (once available) is the only "current
  // position" indicator — there's no separate fixed "start" concept to show.
  useEffect(() => {
    if (!containerRef.current) return;

    const waypoints = geometry.path?.length ? geometry.path : [geometry.start, geometry.end];
    const lineCoordinates = waypoints.map((c) => [c.lon, c.lat] as [number, number]);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: RASTER_STYLE,
      // Set explicitly so the camera starts over the actual route even if
      // fitBounds below never runs.
      center: [geometry.start.lon, geometry.start.lat],
      zoom: 14,
    });
    mapRef.current = map;
    followingRef.current = false; // enabled once the initial route view has been shown

    map.on("error", (e) => console.error("RouteMap: MapLibre error", e.error ?? e));

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

    const drawRoute = () => {
      console.log("RouteMap: drawing route, point count:", lineCoordinates.length, "first:", lineCoordinates[0], "last:", lineCoordinates[lineCoordinates.length - 1]);
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
        paint: { "line-color": "#e8703a", "line-width": 8, "line-opacity": 1 },
      });
      console.log(
        "RouteMap: layer registered?",
        Boolean(map.getLayer("route-line")),
        "all layer ids:",
        map.getStyle()?.layers?.map((l) => l.id)
      );
      new maplibregl.Marker({ color: "#0e6e64" })
        .setLngLat([geometry.end.lon, geometry.end.lat])
        .addTo(map);
    };

    const fitToRoute = () => {
      map.resize(); // guards against a 0-size container at construction time
      const bounds = lineCoordinates.reduce(
        (b, coord) => b.extend(coord),
        new maplibregl.LngLatBounds(lineCoordinates[0], lineCoordinates[0])
      );
      map.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 0 });
      followingRef.current = true;
    };

    const onReady = () => {
      try {
        if (!map.getSource("route-line")) drawRoute();
        // Deferred one animation frame: raster tiles can finish loading fast
        // enough that "load" fires before the container has completed its
        // layout pass, so resize()/fitBounds would measure a stale size and
        // land far more zoomed out than the route actually spans.
        requestAnimationFrame(fitToRoute);
      } catch (err) {
        console.error("RouteMap: failed to render route", err);
      }
    };

    // isStyleLoaded() covers the case where the style finished loading
    // before this listener was attached (a real MapLibre race condition —
    // "load" only fires once, and is missed entirely if registered late).
    if (map.isStyleLoaded()) {
      onReady();
    } else {
      map.on("load", onReady);
    }

    return () => {
      map.remove();
      mapRef.current = null;
      liveMarkerRef.current = null;
    };
    // geometry/segments intentionally re-mount the map on change rather than
    // diffing sources in place — route details are read once per navigation.
  }, [geometry, segments]);

  // Live position updates: a separate, lighter effect that only moves the
  // live marker and (optionally) keeps both the live position and the
  // destination in view — never touches the map/route setup above.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || liveLocation.status !== "tracking") return;

    const { lat, lon } = liveLocation;

    if (!liveMarkerRef.current) {
      const el = document.createElement("div");
      el.className = "h-4 w-4 rounded-full border-2 border-white bg-blue-500 shadow-md";
      liveMarkerRef.current = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    } else {
      liveMarkerRef.current.setLngLat([lon, lat]);
    }

    if (followingRef.current) {
      // Keeps both the live position and the destination in frame while
      // following, rather than zooming in tight on only the live dot —
      // otherwise the route/destination can end up outside the visible
      // area entirely as soon as tracking starts.
      const bounds = new maplibregl.LngLatBounds([lon, lat], [lon, lat]).extend([
        geometry.end.lon,
        geometry.end.lat,
      ]);
      map.fitBounds(bounds, { padding: 64, maxZoom: 17, duration: 500 });
    }

    const remainingM = remainingMetres({ lat, lon }, geometry);
    setProgress({
      distanceRemainingKm: Math.round((remainingM / 1000) * 100) / 100,
      etaMinutes: Math.round(remainingM / WALKING_SPEED_M_PER_MIN),
    });
  }, [liveLocation, geometry, setProgress]);

  // Custom fullscreen toggle (CSS-based, not the native Fullscreen API) —
  // the Fullscreen API is unreliable on iOS Safari, so this instead expands
  // the map to a fixed full-viewport overlay within the app's own UI, which
  // works consistently on both desktop and mobile.
  useEffect(() => {
    mapRef.current?.resize();
  }, [isFullscreen]);

  const handleRecenter = () => {
    followingRef.current = true;
    recenterControlRef.current?.classList.add("hidden");
    if (mapRef.current && liveLocation.status === "tracking") {
      const bounds = new maplibregl.LngLatBounds(
        [liveLocation.lon, liveLocation.lat],
        [liveLocation.lon, liveLocation.lat]
      ).extend([geometry.end.lon, geometry.end.lat]);
      mapRef.current.fitBounds(bounds, { padding: 64, maxZoom: 17, duration: 500 });
    }
  };

  return (
    <div
      className={
        isFullscreen
          ? "fixed inset-0 z-50 bg-surface"
          : "relative h-full w-full"
      }
    >
      <div ref={containerRef} className="h-full w-full" />
      <button
        type="button"
        onClick={() => setIsFullscreen((v) => !v)}
        aria-label={isFullscreen ? "Exit fullscreen map" : "Expand map to fullscreen"}
        className="absolute top-3 right-3 flex h-8 w-8 items-center justify-center rounded-full bg-surface shadow-md"
      >
        {isFullscreen ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3M8 21v-3a2 2 0 0 0-2-2H3M16 21v-3a2 2 0 0 1 2-2h3" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" />
          </svg>
        )}
      </button>
      <button
        ref={recenterControlRef}
        type="button"
        onClick={handleRecenter}
        className="absolute bottom-3 left-3 hidden rounded-full bg-surface px-3 py-1.5 text-xs font-semibold text-text shadow-md"
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
