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
  destination: Coordinates,
  preferredCandidateId?: string
): Promise<PlannedRoute> {
  try {
    const res = await fetch("/api/route-planner", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin, destination }),
      cache: "no-store",
    });
    const data: RoutePlannerResponse = await res.json();
    // Re-routing while walking keeps tracking the plan-time candidate type
    // (fastest/shaded/quieter) so a user who picked "shaded" on a hot day
    // isn't silently switched onto the unshaded fastest path the first time
    // they drift off the original line. Falls back to "fastest"/first when
    // the new origin no longer produces a meaningfully-different candidate
    // of that type (route-planner.py only returns one when it's real).
    const route =
      (preferredCandidateId && data.routes.find((r) => r.id === preferredCandidateId)) ??
      data.routes.find((r) => r.id === "fastest") ??
      data.routes[0];
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
