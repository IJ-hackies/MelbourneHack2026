import type { RouteOption, RouteQueryInput } from "./types";

export interface RouteProvider {
  listRoutes(input: RouteQueryInput): Promise<RouteOption[]>;
  getRoute(id: string, input: RouteQueryInput): Promise<RouteOption | null>;
}

// Same three options for every destination, ignoring preferences and
// departure time, until the pedestrian routing graph and ML-side condition
// scoring exist. Swap `routeProvider` below for a real implementation (e.g.
// one that calls the backend routing API) — every caller goes through this
// interface, so nothing else needs to change.
const STUB_ROUTES: RouteOption[] = [
  {
    id: "comfort",
    minutes: 17,
    distanceKm: 2.1,
    recommended: true,
    description:
      "Under tree canopy for 80% of the walk, quieter side streets past Carlton Gardens.",
    tags: [
      { label: "High shade", tone: "default" },
      { label: "Low crowd", tone: "default" },
    ],
    segments: [
      { label: "Shaded canopy", share: 80, tone: "primary" },
      { label: "Direct sun", share: 20, tone: "heat" },
      { label: "Quiet streets", share: 65, tone: "crowd" },
    ],
  },
  {
    id: "direct",
    minutes: 14,
    distanceKm: 1.9,
    recommended: false,
    description:
      "Direct along Nicholson Street. Fastest, but exposed for most of the route.",
    tags: [{ label: "Full sun", tone: "warm" }],
    segments: [
      { label: "Shaded canopy", share: 25, tone: "primary" },
      { label: "Direct sun", share: 75, tone: "heat" },
      { label: "Quiet streets", share: 30, tone: "crowd" },
    ],
  },
  {
    id: "quiet",
    minutes: 19,
    distanceKm: 2.4,
    recommended: false,
    description: "Longest, but lowest vehicle traffic the entire way.",
    tags: [{ label: "Low traffic", tone: "default" }],
    segments: [
      { label: "Shaded canopy", share: 45, tone: "primary" },
      { label: "Direct sun", share: 55, tone: "heat" },
      { label: "Low traffic", share: 90, tone: "traffic" },
    ],
  },
];

class StubRouteProvider implements RouteProvider {
  async listRoutes(input: RouteQueryInput): Promise<RouteOption[]> {
    void input; // stub ignores the query; a real provider would use it
    return STUB_ROUTES;
  }

  async getRoute(id: string, input: RouteQueryInput): Promise<RouteOption | null> {
    const routes = await this.listRoutes(input);
    return routes.find((r) => r.id === id) ?? null;
  }
}

export const routeProvider: RouteProvider = new StubRouteProvider();
