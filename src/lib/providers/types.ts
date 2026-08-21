// Shared shapes for anything that will eventually be answered by the
// pedestrian-routing / ML backend. Keep this file provider-agnostic — it
// should not import from route-provider.ts or condition-provider.ts.

export type RouteTag = { label: string; tone: "default" | "warm" };

export type RouteOption = {
  id: string;
  minutes: number;
  distanceKm: number;
  recommended: boolean;
  description: string;
  tags: RouteTag[];
  segments: {
    label: string;
    share: number;
    tone: "primary" | "heat" | "crowd" | "traffic";
  }[];
};

export type Condition = {
  label: string;
  value: string;
  tone: "primary" | "heat" | "crowd" | "traffic";
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

export type RouteQueryInput = PlaceQuery & {
  departureTime?: Date;
  preferences?: Partial<UserPreferences>;
};

export type ConditionQueryInput = PlaceQuery;
