"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useLiveLocation } from "@/lib/use-live-location";
import { useLiveProgress } from "@/lib/live-progress-context";
import { callRoutePlannerFromBrowser } from "@/lib/routing/route-client-browser";
import type { Coordinates, RouteGeometry, RouteOption } from "@/lib/providers/types";

// OpenFreeMap's vector styles (keyless, no account) — picked after
// comparing every free keyless option side by side. "Bright" is the
// default: real road/POI labels and icons, the closest free match to an
// actual Google-Maps-style app rather than a plain basemap. "Liberty"
// (used only when the 3D buildings toggle is on) includes building
// extrusions Bright doesn't, at the cost of a slightly flatter color
// palette — hence keeping Bright as the default look.
//
// The earlier "vector tiles never render" bug (worked around at one point
// by switching to raster tiles) turned out to be a MapLibre Web Worker
// resolution bug under Next.js/Turbopack bundling, not anything specific
// to a vector provider — see the setWorkerUrl fix below. With that fixed,
// vector styles (including these) work correctly again.
const BRIGHT_STYLE_URL = "https://tiles.openfreemap.org/styles/bright";
const LIBERTY_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const WALKING_SPEED_M_PER_MIN = 80;
// Below this, a fresh GPS fix isn't worth re-routing for — avoids hitting
// the routing API on every few-metre GPS jitter while stationary.
const REROUTE_THRESHOLD_M = 30;

// MapLibre resolves its internal Web Worker (used to process every GeoJSON
// source — the route line included) relative to its own `import.meta.url`.
// Under Next.js/Turbopack bundling that resolves to the bundled chunk's own
// hashed URL, not the real package location, so the worker request 404s.
// The failure is silent: no thrown error, the worker just never responds,
// so GeoJSON sources never finish processing (`querySourceFeatures()` stays
// empty forever, `map.once("idle", ...)` never fires) while raster tiles —
// which don't need the worker — keep rendering fine. This is what actually
// caused the route line to never appear. scripts/copy-maplibre-worker.mjs
// copies the real worker script to public/ so this points MapLibre at a
// real, reachable URL instead.
if (typeof window !== "undefined") {
  maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");
}

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
  const [show3D, setShow3D] = useState(false);
  // The path actually used for remaining-distance/ETA math and the drawn
  // line — starts as the server-computed static route, then gets replaced
  // by a real re-routed path from the user's live position as they walk,
  // the same way Google Maps keeps the route line anchored to where you
  // actually are rather than the original starting point.
  const currentPathRef = useRef<Coordinates[] | null>(null);
  const lastRoutedFromRef = useRef<Coordinates | null>(null);
  const reroutingRef = useRef(false);

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
    currentPathRef.current = geometry.path?.length ? geometry.path : null;
    lastRoutedFromRef.current = null;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: show3D ? LIBERTY_STYLE_URL : BRIGHT_STYLE_URL,
      // Set explicitly so the camera starts over the actual route even if
      // fitBounds below never runs.
      center: [geometry.start.lon, geometry.start.lat],
      zoom: 14,
      // A top-down view can't show building extrusions at all — pitch the
      // camera when 3D buildings are on so they're actually visible.
      pitch: show3D ? 45 : 0,
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
        paint: { "line-color": "#0e6e64", "line-width": 5, "line-opacity": 0.9 },
      });
      new maplibregl.Marker({ color: "#e8703a" })
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
    // show3D is included so toggling it swaps style+pitch via a clean
    // remount rather than trying to hot-swap style/source state in place.
  }, [geometry, segments, show3D]);

  // Live position updates: a separate, lighter effect that only moves the
  // live marker, keeps both the live position and destination in view, and
  // (throttled) fetches a fresh real route from wherever the user actually
  // is — never touches the map/route setup above.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || liveLocation.status !== "tracking") return;

    const { lat, lon } = liveLocation;
    const here: Coordinates = { lat, lon };

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

    const effectivePath = currentPathRef.current ?? geometry.path ?? null;
    const remainingM = effectivePath
      ? remainingMetres(here, { ...geometry, path: effectivePath })
      : haversineM(here, geometry.end);
    setProgress({
      distanceRemainingKm: Math.round((remainingM / 1000) * 100) / 100,
      etaMinutes: Math.round(remainingM / WALKING_SPEED_M_PER_MIN),
    });

    const lastRoutedFrom = lastRoutedFromRef.current;
    const movedEnoughToReroute =
      !lastRoutedFrom || haversineM(here, lastRoutedFrom) >= REROUTE_THRESHOLD_M;
    if (!movedEnoughToReroute || reroutingRef.current) return;

    reroutingRef.current = true;
    lastRoutedFromRef.current = here;
    callRoutePlannerFromBrowser(here, geometry.end)
      .then((planned) => {
        if (planned.qualityStatus !== "ok" || !planned.path?.length) return;
        currentPathRef.current = planned.path;
        const source = map.getSource("route-line") as maplibregl.GeoJSONSource | undefined;
        source?.setData({
          type: "Feature",
          properties: {},
          geometry: {
            type: "LineString",
            coordinates: planned.path.map((p) => [p.lon, p.lat]),
          },
        });
      })
      .finally(() => {
        reroutingRef.current = false;
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
      <div className="absolute top-3 right-3 flex gap-2">
        <button
          type="button"
          onClick={() => setShow3D((v) => !v)}
          aria-pressed={show3D}
          aria-label="Toggle 3D buildings"
          className={`flex h-8 items-center justify-center rounded-full px-3 text-xs font-semibold shadow-md ${
            show3D ? "bg-primary text-surface" : "bg-surface text-text"
          }`}
        >
          3D
        </button>
        <button
          type="button"
          onClick={() => setIsFullscreen((v) => !v)}
          aria-label={isFullscreen ? "Exit fullscreen map" : "Expand map to fullscreen"}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-surface shadow-md"
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
      </div>
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
