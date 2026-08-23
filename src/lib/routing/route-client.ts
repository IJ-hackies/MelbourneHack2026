import { getBaseUrl } from "@/lib/base-url";
import type { Coordinates, QualityStatus } from "@/lib/providers/types";

type RouteCandidateResponse = {
  id: string;
  path: { lat: number; lon: number }[] | null;
  distance_km: number | null;
  minutes: number | null;
  canopy_density_avg: number | null;
  pedestrian_flow_avg_per_hour: number | null;
  tags: string[];
  quality: { status: QualityStatus; warnings: string[] };
};

type HeatContextResponse = {
  temperature_c: number | null;
  advisory: boolean;
  extreme: boolean;
  shade_bias_multiplier: number;
} | null;

type RoutePlannerResponse = { routes: RouteCandidateResponse[]; heat_context: HeatContextResponse };

export type PlannedRoute = {
  id: string;
  path: Coordinates[] | null;
  distanceKm: number | null;
  minutes: number | null;
  canopyDensityAvg: number | null;
  pedestrianFlowAvgPerHour: number | null;
  tags: string[];
  qualityStatus: QualityStatus;
  warnings: string[];
};

// Real current temperature driving how hard "shaded" routing biases toward
// canopy right now — the app's actual climate-adaptation behaviour (see
// api/route-planner.py's _heat_context), surfaced so the UI can explain why
// rather than silently changing route geometry.
export type HeatContext = {
  temperatureC: number | null;
  advisory: boolean;
  extreme: boolean;
};

export type PlannedRoutes = { routes: PlannedRoute[]; heatContext: HeatContext | null };

const UNAVAILABLE: PlannedRoutes = {
  routes: [
    {
      id: "fastest",
      path: null,
      distanceKm: null,
      minutes: null,
      canopyDensityAvg: null,
      pedestrianFlowAvgPerHour: null,
      tags: [],
      qualityStatus: "unavailable",
      warnings: ["route_planner_unreachable"],
    },
  ],
  heatContext: null,
};

// Server Components/Route Handlers only: Node's fetch needs an absolute
// URL, and next/headers (inside getBaseUrl) is only callable server-side.
// For Client Components, use route-client-browser.ts instead — it's kept
// in a separate module so bundling it for the browser never pulls in
// next/headers via this file.
export async function callRoutePlanner(
  origin: Coordinates,
  destination: Coordinates
): Promise<PlannedRoutes> {
  try {
    const res = await fetch(new URL("/api/route-planner", await getBaseUrl()), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin, destination }),
      cache: "no-store",
    });
    const data: RoutePlannerResponse = await res.json();
    if (!data.routes?.length) return UNAVAILABLE;
    return {
      routes: data.routes.map((r) => ({
        id: r.id,
        path: r.path,
        distanceKm: r.distance_km,
        minutes: r.minutes,
        canopyDensityAvg: r.canopy_density_avg,
        pedestrianFlowAvgPerHour: r.pedestrian_flow_avg_per_hour,
        tags: r.tags,
        qualityStatus: r.quality.status,
        warnings: r.quality.warnings,
      })),
      heatContext: data.heat_context
        ? {
            temperatureC: data.heat_context.temperature_c,
            advisory: data.heat_context.advisory,
            extreme: data.heat_context.extreme,
          }
        : null,
    };
  } catch {
    return UNAVAILABLE;
  }
}
