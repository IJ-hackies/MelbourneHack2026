import { getBaseUrl } from "@/lib/base-url";
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

export async function callRoutePlanner(
  origin: Coordinates,
  destination: Coordinates
): Promise<PlannedRoute> {
  try {
    const res = await fetch(new URL("/api/route-planner", await getBaseUrl()), {
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
