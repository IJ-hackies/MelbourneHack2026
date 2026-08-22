import { callRoutePlanner } from "@/lib/routing/route-client";
import type { Coordinates, RouteOption, RouteQueryInput } from "./types";

export interface RouteProvider {
  listRoutes(input: RouteQueryInput): Promise<RouteOption[]>;
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

const ROUTE_ID = "walking-route";

class RealRouteProvider implements RouteProvider {
  async listRoutes(input: RouteQueryInput): Promise<RouteOption[]> {
    const origin = input.origin ?? DEFAULT_ORIGIN;
    const destination: Coordinates = { lat: input.destination.lat, lon: input.destination.lon };

    const planned = await callRoutePlanner(origin, destination);

    if (planned.qualityStatus === "ok" && planned.path && planned.distanceKm !== null) {
      return [
        {
          id: ROUTE_ID,
          minutes: planned.minutes ?? Math.round((planned.distanceKm * 1000) / 80),
          distanceKm: planned.distanceKm,
          recommended: true,
          description: "Real walking route along the City of Melbourne pedestrian network.",
          tags: [],
          geometry: { start: origin, end: destination, path: planned.path },
          segments: [],
          quality: "ok",
        },
      ];
    }

    // Routing unavailable (outside graph coverage, or the function failed) —
    // fall back to a straight-line estimate, but say so honestly rather than
    // presenting it identically to a real route.
    const straightLineKm = haversineKm(origin, destination);
    return [
      {
        id: ROUTE_ID,
        minutes: Math.round((straightLineKm * 1000) / FALLBACK_WALKING_SPEED_M_PER_MIN),
        distanceKm: Math.round(straightLineKm * 1000) / 1000,
        recommended: true,
        description:
          "Estimated straight-line distance — real street routing is unavailable for this destination.",
        tags: [{ label: "Estimated", tone: "warm" }],
        geometry: { start: origin, end: destination },
        segments: [],
        quality: "unavailable",
      },
    ];
  }

  async getRoute(id: string, input: RouteQueryInput): Promise<RouteOption | null> {
    const routes = await this.listRoutes(input);
    return routes.find((r) => r.id === id) ?? null;
  }
}

export const routeProvider: RouteProvider = new RealRouteProvider();
