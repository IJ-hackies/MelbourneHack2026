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

export const destination = "Fitzroy Gardens";
export const departure = "Leaving now · 3:40pm";

export const conditions = [
  { label: "Feels hot", value: "31°C", tone: "heat" as const },
  { label: "Crowds", value: "Med", tone: "crowd" as const },
  { label: "Shade", value: "62%", tone: "primary" as const },
];

export const routeOptions: RouteOption[] = [
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

export function getRoute(id: string) {
  return routeOptions.find((r) => r.id === id);
}
