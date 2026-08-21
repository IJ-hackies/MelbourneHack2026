import { NextResponse } from "next/server";

// Melbourne CBD-centred bounding box, generous enough to cover greater Melbourne.
const VIEWBOX = "144.5,-38.05,145.35,-37.55";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q")?.trim();

  if (!q || q.length < 3) {
    return NextResponse.json({ results: [] });
  }

  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.searchParams.set("q", q);
  url.searchParams.set("format", "jsonv2");
  url.searchParams.set("addressdetails", "1");
  url.searchParams.set("limit", "6");
  url.searchParams.set("countrycodes", "au");
  url.searchParams.set("viewbox", VIEWBOX);
  url.searchParams.set("bounded", "1");

  let res: Response;
  try {
    res = await fetch(url, {
      headers: {
        "User-Agent": "HeatRoute/0.1 (Melbourne walking-route planner, hackathon project)",
        "Accept-Language": "en-AU",
      },
      // Identical queries are served from Next's fetch cache for a minute —
      // eases load on Nominatim's free public endpoint and its rate limit.
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

  const data: Array<{
    display_name: string;
    lat: string;
    lon: string;
    address?: Record<string, string>;
    type?: string;
  }> = await res.json();

  const results = data.map((place) => {
    const address = place.address ?? {};
    const primary =
      address.amenity ||
      address.building ||
      address.shop ||
      address.leisure ||
      address.tourism ||
      [address.house_number, address.road].filter(Boolean).join(" ") ||
      place.display_name.split(",")[0];

    const secondary = place.display_name
      .split(",")
      .slice(1)
      .join(",")
      .trim()
      .replace(/^,\s*/, "");

    return {
      label: primary,
      address: secondary || place.display_name,
      lat: Number(place.lat),
      lon: Number(place.lon),
    };
  });

  return NextResponse.json({ results });
}
