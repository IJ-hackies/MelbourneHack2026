import type { Coordinates, QualityStatus } from "@/lib/providers/types";

type RoutePlannerResponse = {
  path: { lat: number; lon: number }[] | null;
  distance_km: number | null;
  minutes: number | null;
  quality: { status: QualityStatus; warnings: string[] };
};

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
    return {
      path: data.path,
      distanceKm: data.distance_km,
      minutes: data.minutes,
      qualityStatus: data.quality.status,
      warnings: data.quality.warnings,
    };
  } catch {
    return UNAVAILABLE;
  }
}
