"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useLiveLocation } from "@/lib/use-live-location";
import { useLiveProgress } from "@/lib/live-progress-context";
import { callRoutePlannerFromBrowser } from "@/lib/routing/route-client-browser";
import { computeTurns, bearingDeg, type Turn } from "@/lib/routing/turn-by-turn";
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
// Below this, a fresh GPS fix is treated as noise, not real movement — real
// phone GPS commonly jitters 3-8m even standing still, so counting every
// tick toward "distance walked" would inflate the live counter while idle.
const MIN_MOVEMENT_M = 4;
// Same factor used everywhere else this figure appears (walks.ts, the
// history page, the marketing community counter) — kept in sync manually
// since there's no shared constants module yet.
const EMISSIONS_KG_PER_KM = 0.19;

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

// Remaining distance from a live position to the destination, plus the
// nearest-vertex index it snapped to: nearest-vertex onto the real path if
// one exists, otherwise a straight line to the end point. An approximation
// (nearest vertex, not nearest point-on-segment) but the real path already
// has dense vertices, so it's a reasonable one.
function progressAlongPath(
  position: Coordinates,
  geometry: RouteGeometry
): { remainingM: number; nearestIndex: number | null } {
  const path = geometry.path;
  if (!path || path.length === 0) {
    return { remainingM: haversineM(position, geometry.end), nearestIndex: null };
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
  return { remainingM: remaining, nearestIndex };
}

// Distance in metres from a path vertex to a later vertex, walked along the
// path itself (not a straight line) — used to say how far until the next
// real turn.
function distanceAlongPath(path: Coordinates[], fromIndex: number, toIndex: number): number {
  let total = 0;
  for (let i = fromIndex; i < toIndex; i++) total += haversineM(path[i], path[i + 1]);
  return total;
}

export function RouteMap({
  geometry,
  segments,
  routeId,
  quality = "ok",
}: {
  geometry: RouteGeometry;
  segments?: RouteOption["segments"];
  routeId?: string;
  // "unavailable" means geometry.path is a fabricated-looking straight line
  // (no real street data for this destination), not an actual walkable
  // route — drawn dashed instead of solid so it never reads as a real
  // turn-by-turn path, and the 3D-buildings toggle (irrelevant to a straight
  // line, and misleading pitched over one) is hidden.
  quality?: RouteOption["quality"];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const liveMarkerRef = useRef<maplibregl.Marker | null>(null);
  const followingRef = useRef(true);
  const recenterControlRef = useRef<HTMLButtonElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [show3D, setShow3D] = useState(false);
  const [isOffline, setIsOffline] = useState(
    typeof navigator !== "undefined" ? !navigator.onLine : false
  );
  // The path actually used for remaining-distance/ETA math and the drawn
  // line — starts as the server-computed static route, then gets replaced
  // by a real re-routed path from the user's live position as they walk,
  // the same way Google Maps keeps the route line anchored to where you
  // actually are rather than the original starting point.
  const currentPathRef = useRef<Coordinates[] | null>(null);
  const lastRoutedFromRef = useRef<Coordinates | null>(null);
  const reroutingRef = useRef(false);
  // Guards against a slow reroute response (real network variance) applying
  // stale turns/geometry after a newer one already landed — only the most
  // recently *issued* request's result is ever applied.
  const rerouteRequestIdRef = useRef(0);
  // Recomputed whenever the tracked path changes (initial geometry, or a
  // fresh path from a live reroute) — turn instructions must follow whichever
  // path line is actually on screen, not go stale after a reroute.
  const turnsRef = useRef<Turn[]>(computeTurns(geometry.path ?? []));
  // Real cumulative distance from consecutive GPS fixes, not derived from
  // "total minus remaining" — a live, on-map answer to "how far have I
  // actually walked", the same way a fitness-tracker app would show it.
  const walkedMetresRef = useRef(0);
  const lastPositionRef = useRef<Coordinates | null>(null);
  const headingDegRef = useRef<number | null>(null);

  const liveLocation = useLiveLocation(true);
  const { progress, setProgress } = useLiveProgress();

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
    turnsRef.current = computeTurns(currentPathRef.current ?? []);
    lastRoutedFromRef.current = null;
    walkedMetresRef.current = 0;
    lastPositionRef.current = null;
    headingDegRef.current = null;

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
      if (recenterControlRef.current) recenterControlRef.current.style.display = "flex";
    });
    map.on("zoomstart", (e) => {
      if (e.originalEvent) {
        followingRef.current = false;
        if (recenterControlRef.current) recenterControlRef.current.style.display = "flex";
      }
    });

    const drawRoute = () => {
      // "Walked" (behind current position, faded grey) starts as a
      // zero-length line at the start point — there's nothing walked yet
      // until the first live GPS fix arrives and splits the path.
      map.addSource("route-line-walked", {
        type: "geojson",
        data: {
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: [lineCoordinates[0], lineCoordinates[0]] },
        },
      });
      map.addLayer({
        id: "route-line-walked",
        type: "line",
        source: "route-line-walked",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#9aa3a0", "line-width": 5, "line-opacity": 0.7 },
      });

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
        paint: {
          "line-color": "#0e6e64",
          "line-width": 5,
          "line-opacity": 0.9,
          ...(quality === "unavailable" ? { "line-dasharray": [0.01, 2] } : {}),
        },
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
  }, [geometry, segments, show3D, quality]);

  // Live position updates: a separate, lighter effect that only moves the
  // live marker, keeps both the live position and destination in view, and
  // (throttled) fetches a fresh real route from wherever the user actually
  // is — never touches the map/route setup above.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || liveLocation.status !== "tracking") return;

    const { lat, lon } = liveLocation;
    const here: Coordinates = { lat, lon };

    // Real movement (above GPS jitter) updates heading and the walked-so-far
    // counter; a fix that hasn't actually moved keeps the last known heading
    // rather than snapping to a meaningless bearing between two near-identical
    // points.
    const previous = lastPositionRef.current;
    const movedM = previous ? haversineM(previous, here) : Infinity;
    if (previous && movedM >= MIN_MOVEMENT_M) {
      headingDegRef.current = bearingDeg(previous, here);
      walkedMetresRef.current += movedM;
    }
    lastPositionRef.current = here;

    if (!liveMarkerRef.current) {
      const el = document.createElement("div");
      el.className = "leafroute-live-marker";
      el.innerHTML = `
        <div class="leafroute-live-marker-pulse"></div>
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" fill="#2563eb" stroke="white" stroke-width="2.5" />
          <path d="M12 6 L15.5 14 L12 12 L8.5 14 Z" fill="white" stroke="none" />
        </svg>`;
      liveMarkerRef.current = new maplibregl.Marker({ element: el, rotationAlignment: "map" })
        .setLngLat([lon, lat])
        .addTo(map);
    } else {
      liveMarkerRef.current.setLngLat([lon, lat]);
    }
    // Rotates the arrow to point in the direction of travel — direction is
    // meaningless while stationary, so it only updates once real movement
    // has established a heading.
    if (headingDegRef.current !== null) {
      liveMarkerRef.current.setRotation(headingDegRef.current);
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
    const { remainingM, nearestIndex } = effectivePath
      ? progressAlongPath(here, { ...geometry, path: effectivePath })
      : { remainingM: haversineM(here, geometry.end), nearestIndex: null };

    let nextTurn: { direction: "left" | "right"; distanceMetres: number } | null = null;
    if (effectivePath && nearestIndex !== null) {
      const upcoming = turnsRef.current.find((t) => t.pathIndex > nearestIndex);
      if (upcoming) {
        nextTurn = {
          direction: upcoming.direction,
          distanceMetres: Math.round(distanceAlongPath(effectivePath, nearestIndex, upcoming.pathIndex)),
        };
      }
    }

    // Splits the drawn line into "already walked" (behind, faded) and
    // "ahead" (in front, full colour) at the live position — the same
    // at-a-glance cue Google Maps gives during turn-by-turn navigation.
    if (effectivePath && nearestIndex !== null) {
      const walkedCoords = effectivePath.slice(0, nearestIndex + 1).map((p) => [p.lon, p.lat] as [number, number]);
      const aheadCoords = effectivePath.slice(nearestIndex).map((p) => [p.lon, p.lat] as [number, number]);
      const walkedSource = map.getSource("route-line-walked") as maplibregl.GeoJSONSource | undefined;
      const aheadSource = map.getSource("route-line") as maplibregl.GeoJSONSource | undefined;
      if (walkedCoords.length >= 2) {
        walkedSource?.setData({
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: walkedCoords },
        });
      }
      if (aheadCoords.length >= 2) {
        aheadSource?.setData({
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: aheadCoords },
        });
      }
    }

    const distanceWalkedKm = Math.round((walkedMetresRef.current / 1000) * 100) / 100;
    setProgress({
      distanceRemainingKm: Math.round((remainingM / 1000) * 100) / 100,
      etaMinutes: Math.round(remainingM / WALKING_SPEED_M_PER_MIN),
      nextTurn,
      distanceWalkedKm,
      emissionsSavedKg: Math.round(distanceWalkedKm * EMISSIONS_KG_PER_KM * 100) / 100,
    });

    const lastRoutedFrom = lastRoutedFromRef.current;
    const movedEnoughToReroute =
      !lastRoutedFrom || haversineM(here, lastRoutedFrom) >= REROUTE_THRESHOLD_M;
    if (!movedEnoughToReroute || reroutingRef.current) return;

    reroutingRef.current = true;
    lastRoutedFromRef.current = here;
    const requestId = ++rerouteRequestIdRef.current;
    callRoutePlannerFromBrowser(here, geometry.end, routeId)
      .then((planned) => {
        // A newer reroute request (issued from a more recent GPS fix) may
        // have already resolved and applied its result — never let a
        // slower, now-stale response overwrite it.
        if (requestId !== rerouteRequestIdRef.current) return;
        if (planned.qualityStatus !== "ok" || !planned.path?.length) return;
        currentPathRef.current = planned.path;
        turnsRef.current = computeTurns(planned.path);
        const source = map.getSource("route-line") as maplibregl.GeoJSONSource | undefined;
        source?.setData({
          type: "Feature",
          properties: {},
          geometry: {
            type: "LineString",
            coordinates: planned.path.map((p) => [p.lon, p.lat]),
          },
        });
        // The new path starts at "here", so nothing on it is "walked" yet.
        const walkedSource = map.getSource("route-line-walked") as maplibregl.GeoJSONSource | undefined;
        walkedSource?.setData({
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: [[lon, lat], [lon, lat]] },
        });
      })
      .finally(() => {
        if (requestId === rerouteRequestIdRef.current) reroutingRef.current = false;
      });
  }, [liveLocation, geometry, setProgress, routeId]);

  // Re-routing while walking depends on network access — surface it plainly
  // instead of letting fetch failures fail silently (callRoutePlannerFromBrowser
  // already degrades gracefully, but the user has no way to know why the
  // route line stopped updating without this).
  useEffect(() => {
    const goOnline = () => setIsOffline(false);
    const goOffline = () => setIsOffline(true);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  // Custom fullscreen toggle (CSS-based, not the native Fullscreen API) —
  // the Fullscreen API is unreliable on iOS Safari, so this instead expands
  // the map to a fixed full-viewport overlay within the app's own UI, which
  // works consistently on both desktop and mobile.
  useEffect(() => {
    mapRef.current?.resize();
  }, [isFullscreen]);

  const handleRecenter = () => {
    followingRef.current = true;
    if (recenterControlRef.current) recenterControlRef.current.style.display = "none";
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
        {quality !== "unavailable" && (
        <button
          type="button"
          onClick={() => setShow3D((v) => !v)}
          aria-pressed={show3D}
          aria-label="Toggle 3D buildings"
          className={`flex h-11 items-center justify-center rounded-full px-4 text-xs font-semibold shadow-md transition-colors ${
            show3D ? "bg-primary text-surface" : "bg-surface text-text hover:bg-surface-alt"
          }`}
        >
          3D
        </button>
        )}
        <button
          type="button"
          onClick={() => setIsFullscreen((v) => !v)}
          aria-label={isFullscreen ? "Exit fullscreen map" : "Expand map to fullscreen"}
          className="flex h-11 w-11 items-center justify-center rounded-full bg-surface text-text shadow-md transition-colors hover:bg-surface-alt"
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
        style={{ display: "none" }}
        className="absolute bottom-3 left-3 items-center gap-1.5 rounded-full bg-surface px-4 py-2.5 text-xs font-semibold text-text shadow-md transition-colors hover:bg-surface-alt"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M22 12h-3M5 12H2" />
        </svg>
        Recenter
      </button>
      <div className="absolute top-3 left-3 flex flex-col items-start gap-1.5">
        {/* Always rendered, starting at 0 the moment this page mounts, not
            only once a live GPS fix arrives — a walker checking "has this
            started tracking me yet" should see it counting from zero
            immediately, the same way active-walk.tsx's own copy of these
            numbers does once progress exists. */}
        <div className="flex items-center gap-3 rounded-xl border border-border bg-surface/95 px-3 py-2 shadow-sm backdrop-blur-sm">
          <div className="text-center">
            <div className="font-display text-sm font-semibold tabular-nums text-text">
              {(progress?.distanceWalkedKm ?? 0).toFixed(2)} km
            </div>
            <div className="text-[0.62rem] tracking-wide text-text-tertiary uppercase">Walked</div>
          </div>
          <div className="h-6 w-px bg-border" aria-hidden="true" />
          <div className="text-center">
            <div className="font-display text-sm font-semibold tabular-nums text-primary">
              {(progress?.emissionsSavedKg ?? 0).toFixed(2)} kg
            </div>
            <div className="text-[0.62rem] tracking-wide text-text-tertiary uppercase">CO₂e saved</div>
          </div>
        </div>

        {isOffline && (
          <span className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[0.72rem] font-medium text-text shadow-sm">
            Offline, so the route won&apos;t update until you&apos;re back online
          </span>
        )}
        {!isOffline && liveLocation.status === "denied" && (
          <span className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[0.72rem] font-medium text-text shadow-sm">
            Location permission denied
          </span>
        )}
        {!isOffline && liveLocation.status === "unavailable" && (
          <span className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[0.72rem] font-medium text-text shadow-sm">
            Location unavailable
          </span>
        )}
      </div>
    </div>
  );
}
