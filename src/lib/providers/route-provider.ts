import { callRoutePlanner, type HeatContext } from "@/lib/routing/route-client";
import type { Coordinates, RouteOption, RouteQueryInput } from "./types";

export type RouteListResult = { routes: RouteOption[]; heatContext: HeatContext | null };

export interface RouteProvider {
  listRoutes(input: RouteQueryInput): Promise<RouteListResult>;
  getRoute(id: string, input: RouteQueryInput): Promise<RouteOption | null>;
}

// Used as the walk's starting point when the caller doesn't supply an
// `origin` (State Library of Victoria, central Melbourne CBD) — there is no
// "current location" concept for the server-rendered route list yet (live
// geolocation, once added, only affects the client-side map/tracking view).
const DEFAULT_ORIGIN: Coordinates = { lat: -37.8098, lon: 144.9652 };

// Fallback walking speed for the straight-line estimate shown when the real
// routing function reports the query is outside its graph coverage — kept
// in one place so the "estimated" label and the number stay consistent.
const FALLBACK_WALKING_SPEED_M_PER_MIN = 80;

function haversineKm(a: Coordinates, b: Coordinates): number {
  const r = 6371;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h));
}

// Picks which already-generated candidate to recommend, based purely on
// saved preferences — never which candidates exist in the first place (see
// route-client.ts's callRoutePlanner, which no longer sends preferences to
// the routing function at all). A preference set to its maximum or minimum
// must always still return the same set of real route options; it only
// changes which one is suggested first. Explicit "prefer quieter streets"
// wins over heat sensitivity when both would apply, since it's a direct
// toggle rather than a threshold on a continuous slider.
function chooseRecommendedId(candidateIds: string[], preferences?: RouteQueryInput["preferences"]): string {
  if (preferences?.preferQuieterStreets && candidateIds.includes("quieter")) {
    return "quieter";
  }
  if ((preferences?.heatSensitivity ?? 0) >= 60 && candidateIds.includes("shaded")) {
    return "shaded";
  }
  return candidateIds.includes("fastest") ? "fastest" : candidateIds[0];
}

// Maps the route-planner's real, data-derived tag ids to display labels.
// Only ever attached server-side when the backend actually found two
// candidates that differ on that metric — never applied client-side as
// decoration.
const TAG_LABELS: Record<string, { label: string; tone: "default" | "warm" }> = {
  fastest: { label: "Fastest", tone: "default" },
  most_shaded: { label: "Most shaded", tone: "warm" },
  least_crowded: { label: "Least crowded", tone: "default" },
};

class RealRouteProvider implements RouteProvider {
  async listRoutes(input: RouteQueryInput): Promise<RouteListResult> {
    const origin = input.origin ?? DEFAULT_ORIGIN;
    const destination: Coordinates = { lat: input.destination.lat, lon: input.destination.lon };

    const planned = await callRoutePlanner(origin, destination);
    const ok = planned.routes.filter((r) => r.qualityStatus === "ok" && r.path && r.distanceKm !== null);
    const recommendedId = chooseRecommendedId(ok.map((r) => r.id), input.preferences);

    if (ok.length > 0) {
      return {
        heatContext: planned.heatContext,
        routes: ok.map((route) => ({
          id: route.id,
          minutes: route.minutes ?? Math.round((route.distanceKm! * 1000) / FALLBACK_WALKING_SPEED_M_PER_MIN),
          distanceKm: route.distanceKm!,
          recommended: route.id === recommendedId,
          description:
            route.id === "shaded"
              ? "A real walking route that leans toward streets with denser tree canopy."
              : route.id === "quieter"
                ? "A real walking route that steers away from streets that are busy right now."
                : "A real walking route along Melbourne's pedestrian network.",
          tags: route.tags.map((t) => TAG_LABELS[t]).filter(Boolean),
          geometry: { start: origin, end: destination, path: route.path! },
          segments: [],
          quality: "ok",
          canopyDensityAvg: route.canopyDensityAvg,
          pedestrianFlowAvgPerHour: route.pedestrianFlowAvgPerHour,
        })),
      };
    }

    // Routing unavailable (outside graph coverage, or the function failed) —
    // fall back to a straight-line estimate, but say so honestly rather than
    // presenting it identically to a real route.
    const straightLineKm = haversineKm(origin, destination);
    return {
      heatContext: planned.heatContext,
      routes: [
        {
          id: "fastest",
          minutes: Math.round((straightLineKm * 1000) / FALLBACK_WALKING_SPEED_M_PER_MIN),
          distanceKm: Math.round(straightLineKm * 1000) / 1000,
          recommended: true,
          description:
            "This destination is outside our mapped walking area, so we can't plot real streets yet. What you're seeing is a straight-line distance estimate, not turn-by-turn directions.",
          tags: [{ label: "Estimated", tone: "warm" }],
          geometry: { start: origin, end: destination },
          segments: [],
          quality: "unavailable",
          canopyDensityAvg: null,
          pedestrianFlowAvgPerHour: null,
        },
      ],
    };
  }

  async getRoute(id: string, input: RouteQueryInput): Promise<RouteOption | null> {
    const { routes } = await this.listRoutes(input);
    return routes.find((r) => r.id === id) ?? null;
  }
}

export const routeProvider: RouteProvider = new RealRouteProvider();
