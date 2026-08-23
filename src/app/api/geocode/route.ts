import { NextResponse } from "next/server";
import { isRateLimited, requestIp } from "@/lib/rate-limit";

// Melbourne CBD-centred bounding box, generous enough to cover greater Melbourne.
const BBOX = "144.5,-38.05,145.35,-37.55";

// Photon (Komoot's OSM-based geocoder) rather than Nominatim's /search:
// Nominatim's tokenizer requires the query's last word to already be a real
// token, so "Royal exhi" (still typing "Exhibition") and "Abeckett" (real
// street is "A'Beckett") both returned zero results — verified directly
// against both APIs. Photon indexes n-grams, so it prefix-matches a partial
// word and its house-number/street parsing tolerates the missing apostrophe.
const PHOTON_URL = "https://photon.komoot.io/api";

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return Math.round(2 * r * Math.asin(Math.sqrt(h)) * 100) / 100;
}

type PhotonProperties = {
  name?: string;
  housenumber?: string;
  street?: string;
  locality?: string;
  district?: string;
  city?: string;
  state?: string;
  postcode?: string;
  country?: string;
  osm_key?: string;
  osm_value?: string;
};

// A coarse, honest place category derived from OSM's own tagging — used
// client-side to pick a recognisable icon per suggestion (restaurant, park,
// transit, shop, ...), the way Google/Apple Maps visually distinguish
// result types instead of a single generic pin for everything.
export type PlaceCategory =
  | "food"
  | "park"
  | "transit"
  | "shop"
  | "lodging"
  | "landmark"
  | "address"
  | "place";

function categoryFor(p: PhotonProperties): PlaceCategory {
  const key = p.osm_key;
  const value = p.osm_value;
  if (key === "amenity" && (value === "restaurant" || value === "cafe" || value === "fast_food" || value === "bar" || value === "pub")) {
    return "food";
  }
  if (key === "leisure" || key === "natural" || (key === "landuse" && value === "recreation_ground")) return "park";
  if (key === "railway" || key === "highway" && value === "bus_stop" || key === "public_transport") return "transit";
  if (key === "shop") return "shop";
  if (key === "tourism" && value === "hotel") return "lodging";
  if (key === "tourism" || key === "historic" || key === "amenity") return "landmark";
  if (p.housenumber || p.street) return "address";
  return "place";
}

type PhotonFeature = {
  properties: PhotonProperties;
  geometry: { coordinates: [number, number] };
};

export async function GET(request: Request) {
  // A caller typing normally fires well under this per keystroke-debounced
  // request; this only catches scripted abuse hammering Photon's free,
  // unauthenticated endpoint.
  if (isRateLimited(`geocode:${requestIp(request)}`, { windowMs: 60_000, maxPerWindow: 30 })) {
    return NextResponse.json({ results: [], error: "Too many requests." }, { status: 429 });
  }

  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q")?.trim();

  if (!q || q.length < 2) {
    return NextResponse.json({ results: [] });
  }

  // A real live location (when the browser has one) biases ranking toward
  // where the user actually is, same as typing into Google Maps while
  // location is on — falls back to the Melbourne CBD centroid otherwise,
  // never a hard restriction either way.
  const userLat = Number(searchParams.get("lat"));
  const userLon = Number(searchParams.get("lon"));
  const hasUserLocation = Number.isFinite(userLat) && Number.isFinite(userLon);

  const url = new URL(PHOTON_URL);
  url.searchParams.set("q", q);
  // More candidates than are shown by default — the client only renders a
  // handful inline but keeps the rest for the "show all matches" expansion
  // (Enter with nothing highlighted) so there's a real list to pick from,
  // not just the top few.
  url.searchParams.set("limit", "12");
  url.searchParams.set("bbox", BBOX);
  url.searchParams.set("lat", hasUserLocation ? String(userLat) : "-37.8136");
  url.searchParams.set("lon", hasUserLocation ? String(userLon) : "144.9631");

  let res: Response;
  try {
    res = await fetch(url, {
      headers: { "User-Agent": "LeafRoute/0.1 (Melbourne walking-route planner, hackathon project)" },
      // Identical queries are served from Next's fetch cache for a minute —
      // eases load on Photon's free public endpoint and its rate limit.
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return NextResponse.json(
      { results: [], error: "Search is temporarily unavailable." },
      { status: 502 }
    );
  }

  if (!res.ok) {
    return NextResponse.json(
      { results: [], error: "Search is temporarily unavailable." },
      { status: 502 }
    );
  }

  let data: { features?: PhotonFeature[] };
  try {
    data = await res.json();
  } catch {
    return NextResponse.json(
      { results: [], error: "Search is temporarily unavailable." },
      { status: 502 }
    );
  }
  // A 200 response body isn't guaranteed to have the expected shape (e.g.
  // Photon returning an error/rate-limit payload with no `features`) --
  // fall back to the same graceful "unavailable" response used above
  // instead of throwing on `.filter` and surfacing a raw 500.
  if (!Array.isArray(data.features)) {
    return NextResponse.json(
      { results: [], error: "Search is temporarily unavailable." },
      { status: 502 }
    );
  }

  const results = data.features
    .filter((f) => f.geometry?.coordinates)
    .map((feature) => {
      const p = feature.properties;
      const streetLine = [p.housenumber, p.street].filter(Boolean).join(" ");
      const primary = p.name || streetLine || p.locality || p.city || "Unnamed location";

      const secondaryParts = [
        primary === streetLine ? null : streetLine,
        p.locality,
        p.district,
        p.city,
        p.state,
      ].filter((part, i, arr) => part && arr.indexOf(part) === i);

      const [lon, lat] = feature.geometry.coordinates;

      return {
        label: primary,
        address: secondaryParts.join(", ") || "Melbourne, Victoria",
        lat,
        lon,
        category: categoryFor(p),
        distanceKm: hasUserLocation ? haversineKm(userLat, userLon, lat, lon) : null,
      };
    });

  return NextResponse.json({ results });
}
