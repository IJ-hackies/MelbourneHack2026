import { NextResponse } from "next/server";
import { routeProvider } from "@/lib/providers/route-provider";

// Client-callable wrapper around routeProvider.listRoutes — the plan page's
// route list is fetched from the browser (not server-rendered) specifically
// so it can carry the user's real live-location origin instead of always
// falling back to the static default origin, which previously made every
// route look like it started from a fixed point in the CBD rather than
// wherever the user actually was.
export async function GET(request: Request) {
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

  const result = await routeProvider.listRoutes({
    destination: { label: destLabel, lat: destLat, lon: destLon },
    origin,
    departureTime: new Date(),
  });

  return NextResponse.json(result);
}
