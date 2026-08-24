import { NextResponse } from "next/server";
import { isRateLimited, requestIp } from "@/lib/rate-limit";

export async function GET(request: Request) {
  if (isRateLimited(`air-quality:${requestIp(request)}`, { windowMs: 60_000, maxPerWindow: 30 })) {
    return NextResponse.json({ conditions: null, error: "Too many requests." }, { status: 429 });
  }

  const { searchParams } = new URL(request.url);
  const lat = Number(searchParams.get("lat"));
  const lon = Number(searchParams.get("lon"));

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return NextResponse.json(
      { conditions: null, error: "lat and lon query params are required." },
      { status: 400 }
    );
  }

  const url = new URL("https://air-quality-api.open-meteo.com/v1/air-quality");
  url.searchParams.set("latitude", String(lat));
  url.searchParams.set("longitude", String(lon));
  url.searchParams.set("current", "us_aqi,pm2_5");
  url.searchParams.set("timezone", "Australia/Melbourne");

  let res: Response;
  try {
    res = await fetch(url, {
      // Air quality changes slower than crowd/weather, similar cadence to
      // the weather route's own cache window.
      next: { revalidate: 600 },
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return NextResponse.json(
      { conditions: null, error: "Air quality is temporarily unavailable." },
      { status: 502 }
    );
  }

  if (!res.ok) {
    return NextResponse.json(
      { conditions: null, error: "Air quality is temporarily unavailable." },
      { status: 502 }
    );
  }

  const data: { current?: { us_aqi?: number; pm2_5?: number } } = await res.json();
  const current = data.current;
  if (!current || typeof current.us_aqi !== "number" || typeof current.pm2_5 !== "number") {
    return NextResponse.json(
      { conditions: null, error: "Air quality is temporarily unavailable." },
      { status: 502 }
    );
  }

  return NextResponse.json({
    conditions: { usAqi: current.us_aqi, pm25: current.pm2_5 },
  });
}
