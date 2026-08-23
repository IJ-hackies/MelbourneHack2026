import type { Coordinates, QualityStatus } from "@/lib/providers/types";

type RouteCandidate = {
  id: string;
  path: { lat: number; lon: number }[] | null;
  distance_km: number | null;
  minutes: number | null;
  quality: { status: QualityStatus; warnings: string[] };
};

type RoutePlannerResponse = { routes: RouteCandidate[] };

export type PlannedRoute = {
  path: Coordinates[] | null;
  distanceKm: number | null;
  minutes: number | null;
  qualityStatus: QualityStatus;
  warnings: string[];
};

const UNAVAILABLE: PlannedRoute = {
  path: null,
  distanceKm: null,
  minutes: null,
  qualityStatus: "unavailable",
  warnings: ["route_planner_unreachable"],
};

// Client Components (e.g. RouteMap re-routing from live location): the
// browser resolves a relative URL against the current origin on its own,
// so this doesn't need getBaseUrl()/next-headers at all — kept in its own
// module so bundling it for the browser never pulls in next/headers via
// route-client.ts's server-only sibling function.
export async function callRoutePlannerFromBrowser(
  origin: Coordinates,
  destination: Coordinates
): Promise<PlannedRoute> {
  try {
    const res = await fetch("/api/route-planner", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin, destination }),
      cache: "no-store",
    });
    const data: RoutePlannerResponse = await res.json();
    // Live re-routing while walking always tracks the fastest candidate —
    // the plan-time "most shaded"/"least crowded" choice is a one-time
    // pick, not something to keep re-optimising for on every GPS tick.
    const route = data.routes.find((r) => r.id === "fastest") ?? data.routes[0];
    if (!route) return UNAVAILABLE;
    return {
      path: route.path,
      distanceKm: route.distance_km,
      minutes: route.minutes,
      qualityStatus: route.quality.status,
      warnings: route.quality.warnings,
    };
  } catch {
    return UNAVAILABLE;
  }
}
