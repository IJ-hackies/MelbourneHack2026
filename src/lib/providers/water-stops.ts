import type { Coordinates } from "./types";

export type WaterStop = { lat: number; lon: number; distanceFromRouteM: number };

// City of Melbourne's real public-asset register for drinking fountains
// (Explore v2.1 dataset "drinking-fountains") — same portal and query
// pattern as feature_lookup.py's pedestrian/sensor datasets. Only covers the
// City of Melbourne LGA, same real coverage gap as the rest of this app's
// City-of-Melbourne-sourced data, so this can honestly come back empty for a
// route mostly outside it rather than guessing.
const FOUNTAINS_URL =
  "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/drinking-fountains/records";

const MAX_STOPS = 3;

function haversineM(a: Coordinates, b: Coordinates): number {
  const r = 6_371_000;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h));
}

// ~332 fountains total across the whole dataset (well under a handful of
// paginated 100-row requests) -- the dataset's own latitude/longitude
// fields are published as text, not numbers, so ODSQL can't range-filter on
// them server-side; fetching everything and filtering by real distance here
// is simpler than fighting that and still cheap given the dataset's size.
async function fetchAllFountains(): Promise<{ lat: number; lon: number }[]> {
  const fountains: { lat: number; lon: number }[] = [];
  let offset = 0;
  const pageSize = 100;
  for (let page = 0; page < 5; page++) {
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    const res = await fetch(`${FOUNTAINS_URL}?${params.toString()}`, {
      signal: AbortSignal.timeout(5000),
      next: { revalidate: 3600 },
    });
    if (!res.ok) break;
    const data = await res.json();
    const results: { geo_point_2d?: { lat: number; lon: number } }[] = data.results ?? [];
    for (const rec of results) {
      if (rec.geo_point_2d) fountains.push({ lat: rec.geo_point_2d.lat, lon: rec.geo_point_2d.lon });
    }
    if (results.length < pageSize) break;
    offset += pageSize;
  }
  return fountains;
}

// Nearest real fountains to any point along the route's own path, not just
// the destination -- a walker wants a stop they'll actually pass, not one
// that happens to be close to where they end up.
// Caps how many path points the O(fountains × points) nearest-distance scan
// below actually checks. Consecutive routed-graph vertices sit only a few
// metres apart, far denser than the 350m detour radius cares about, so
// evenly thinning a long path down to this many points barely changes which
// fountains qualify while keeping the scan bounded regardless of how many
// vertices the real route happens to have.
const MAX_PATH_SAMPLES = 150;

function sampleAlongPath(path: Coordinates[]): Coordinates[] {
  if (path.length <= MAX_PATH_SAMPLES) return path;
  const stride = path.length / MAX_PATH_SAMPLES;
  const sampled: Coordinates[] = [];
  for (let i = 0; i < MAX_PATH_SAMPLES; i++) sampled.push(path[Math.floor(i * stride)]);
  if (sampled[sampled.length - 1] !== path[path.length - 1]) sampled.push(path[path.length - 1]);
  return sampled;
}

export async function getWaterStopsNearRoute(path: Coordinates[]): Promise<WaterStop[]> {
  if (path.length === 0) return [];
  const samples = sampleAlongPath(path);

  let fountains: { lat: number; lon: number }[];
  try {
    fountains = await fetchAllFountains();
  } catch {
    return [];
  }

  const stops: WaterStop[] = [];
  for (const fountain of fountains) {
    let nearestM = Infinity;
    for (const point of samples) {
      const d = haversineM(fountain, point);
      if (d < nearestM) nearestM = d;
    }
    if (nearestM <= 350) stops.push({ ...fountain, distanceFromRouteM: Math.round(nearestM) });
  }

  return stops.sort((a, b) => a.distanceFromRouteM - b.distanceFromRouteM).slice(0, MAX_STOPS);
}
