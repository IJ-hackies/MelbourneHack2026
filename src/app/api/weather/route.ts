import { NextResponse } from "next/server";
import { isRateLimited, requestIp } from "@/lib/rate-limit";

export async function GET(request: Request) {
  if (isRateLimited(`weather:${requestIp(request)}`, { windowMs: 60_000, maxPerWindow: 30 })) {
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

  const url = new URL("https://api.open-meteo.com/v1/forecast");
  url.searchParams.set("latitude", String(lat));
  url.searchParams.set("longitude", String(lon));
  url.searchParams.set(
    "current",
    "temperature_2m,relative_humidity_2m,wind_speed_10m,uv_index"
  );
  url.searchParams.set("timezone", "Australia/Melbourne");

  let res: Response;
  try {
    res = await fetch(url, {
      // Weather changes slower than place search, so cache longer than geocode.
      next: { revalidate: 600 },
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return NextResponse.json(
      { conditions: null, error: "Weather is temporarily unavailable." },
      { status: 502 }
    );
  }

  if (!res.ok) {
    return NextResponse.json(
      { conditions: null, error: "Weather is temporarily unavailable." },
      { status: 502 }
    );
  }

  const data: {
    current?: {
      temperature_2m?: number;
      relative_humidity_2m?: number;
      wind_speed_10m?: number;
      uv_index?: number;
    };
  } = await res.json();

  const current = data.current;
  if (
    !current ||
    typeof current.temperature_2m !== "number" ||
    typeof current.relative_humidity_2m !== "number" ||
    typeof current.wind_speed_10m !== "number" ||
    typeof current.uv_index !== "number"
  ) {
    return NextResponse.json(
      { conditions: null, error: "Weather is temporarily unavailable." },
      { status: 502 }
    );
  }

  return NextResponse.json({
    conditions: {
      temperatureC: current.temperature_2m,
      relativeHumidityPct: current.relative_humidity_2m,
      windSpeedMs: current.wind_speed_10m,
      uvIndex: current.uv_index,
    },
  });
}
