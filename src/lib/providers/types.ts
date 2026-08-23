// Shared shapes for anything that will eventually be answered by the
// pedestrian-routing / ML backend. Keep this file provider-agnostic — it
// should not import from route-provider.ts or condition-provider.ts.

export type RouteTag = { label: string; tone: "default" | "warm" };

export type Coordinates = { lat: number; lon: number };

// A place once geocoding has actually resolved coordinates for it. Distinct
// from PlaceQuery, which also covers the loose "user typed something but we
// don't have coordinates yet" case.
export type ResolvedPlace = PlaceQuery & Coordinates;

// V1 has no real pedestrian routing graph, so `path` is optional: absent
// means the map draws a straight line between start/end rather than
// inventing fake route geometry.
export type RouteGeometry = {
  start: Coordinates;
  end: Coordinates;
  path?: Coordinates[];
};

export type RouteOption = {
  id: string;
  minutes: number;
  distanceKm: number;
  recommended: boolean;
  description: string;
  tags: RouteTag[];
  geometry: RouteGeometry;
  segments: {
    label: string;
    share: number;
    tone: "primary" | "heat" | "crowd" | "traffic";
  }[];
  // "ok" when geometry.path is a real routed path from the pedestrian graph;
  // "unavailable" when it fell back to a straight line (query outside graph
  // coverage, or the routing function failed) — never silently identical.
  quality: QualityStatus;
  // Real, route-planner-derived metrics backing the "most shaded"/"least
  // crowded" tags — null when the graph/model couldn't produce one for this
  // route rather than being omitted, so callers can tell "no data" apart
  // from "not asked for". canopyDensityAvg is a tree-canopy-centroid density
  // proxy (0..1), not a precise solar-shade percentage — see
  // ml/routing/scripts/build_shade_grid.py.
  canopyDensityAvg: number | null;
  pedestrianFlowAvgPerHour: number | null;
};

export type Condition = {
  label: string;
  value: string;
  tone: "primary" | "heat" | "crowd" | "traffic";
};

// Raw ML model outputs. Neither release has calibrated uncertainty, so there
// is deliberately no confidence field here — never fabricate one.
export type QualityStatus = "ok" | "degraded" | "unavailable";

export type CrowdSignal = {
  pedestrianFlowPerHour: number;
  qualityStatus: QualityStatus;
  warnings: string[];
};

export type TrafficSignal = {
  vehicleCountPerHour: number;
  qualityStatus: QualityStatus;
  warnings: string[];
};

export type UserPreferences = {
  heatSensitivity: number;
  comfortBalance: number;
  pace: number;
  preferQuieterStreets: boolean;
  preferLowerTraffic: boolean;
};

export type PlaceQuery = {
  label: string;
  lat?: number;
  lon?: number;
};

export type RouteQueryInput = {
  destination: ResolvedPlace;
  origin?: Coordinates;
  departureTime?: Date;
  preferences?: Partial<UserPreferences>;
};

export type ConditionQueryInput = ResolvedPlace;
