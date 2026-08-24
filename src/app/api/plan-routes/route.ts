import { NextResponse } from "next/server";
import { routeProvider } from "@/lib/providers/route-provider";
import { getQuietestHourToday } from "@/lib/providers/quietest-hour";
import { loadSignedInPreferences } from "@/lib/providers/signed-in-preferences";
import { isRateLimited, requestIp } from "@/lib/rate-limit";

// Client-callable wrapper around routeProvider.listRoutes — the plan page's
// route list is fetched from the browser (not server-rendered) specifically
// so it can carry the user's real live-location origin instead of always
// falling back to the static default origin, which previously made every
// route look like it started from a fixed point in the CBD rather than
// wherever the user actually was.
export async function GET(request: Request) {
  // The heaviest of these routes: fans out to the routing graph (its own
  // Python-side limiter still applies) plus several quietest-hour crowd
  // samples per call, so this caps the fan-out itself, not just the
  // downstream calls individually.
  if (isRateLimited(`plan-routes:${requestIp(request)}`, { windowMs: 60_000, maxPerWindow: 15 })) {
    return NextResponse.json(
      { routes: [], heatContext: null, quietestHour: null, error: "Too many requests." },
      { status: 429 }
    );
  }

  const { searchParams } = new URL(request.url);
  const destLat = Number(searchParams.get("destLat"));
  const destLon = Number(searchParams.get("destLon"));
  const destLabel = searchParams.get("destLabel") ?? "";
  const originLat = searchParams.get("originLat");
  const originLon = searchParams.get("originLon");

  if (!Number.isFinite(destLat) || !Number.isFinite(destLon) || !destLabel) {
    return NextResponse.json(
      { routes: [], heatContext: null, error: "destLat, destLon, and destLabel are required." },
      { status: 400 }
    );
  }

  const origin =
    originLat && originLon && Number.isFinite(Number(originLat)) && Number.isFinite(Number(originLon))
      ? { lat: Number(originLat), lon: Number(originLon) }
      : undefined;

  const preferences = await loadSignedInPreferences();

  const [result, quietestHour] = await Promise.all([
    routeProvider.listRoutes({
      destination: { label: destLabel, lat: destLat, lon: destLon },
      origin,
      departureTime: new Date(),
      preferences,
    }),
    getQuietestHourToday({ lat: destLat, lon: destLon }),
  ]);

  return NextResponse.json({ ...result, quietestHour });
}
