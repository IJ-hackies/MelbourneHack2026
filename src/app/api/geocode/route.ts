import { NextResponse } from "next/server";

// Melbourne CBD-centred bounding box, generous enough to cover greater Melbourne.
const BBOX = "144.5,-38.05,145.35,-37.55";

// Photon (Komoot's OSM-based geocoder) rather than Nominatim's /search:
// Nominatim's tokenizer requires the query's last word to already be a real
// token, so "Royal exhi" (still typing "Exhibition") and "Abeckett" (real
// street is "A'Beckett") both returned zero results — verified directly
// against both APIs. Photon indexes n-grams, so it prefix-matches a partial
// word and its house-number/street parsing tolerates the missing apostrophe.
const PHOTON_URL = "https://photon.komoot.io/api";

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
};

type PhotonFeature = {
  properties: PhotonProperties;
  geometry: { coordinates: [number, number] };
};

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q")?.trim();

  if (!q || q.length < 2) {
    return NextResponse.json({ results: [] });
  }

  const url = new URL(PHOTON_URL);
  url.searchParams.set("q", q);
  url.searchParams.set("limit", "6");
  url.searchParams.set("bbox", BBOX);
  // Biases (doesn't restrict) ranking toward the Melbourne CBD, so a partial
  // query still favours local results without a hard bounding-box cutoff.
  url.searchParams.set("lat", "-37.8136");
  url.searchParams.set("lon", "144.9631");

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

  const data: { features: PhotonFeature[] } = await res.json();

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
      };
    });

  return NextResponse.json({ results });
}
