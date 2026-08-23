import { NextResponse } from "next/server";
import { conditionProvider } from "@/lib/providers/condition-provider";
import { isRateLimited, requestIp } from "@/lib/rate-limit";

// Client-callable wrapper around conditionProvider.getConditions — pulled out
// of the server-rendered plan page (page.tsx used to await this before
// rendering anything) so the page can paint immediately and the condition
// tiles load in parallel with the route list instead of blocking navigation
// on a weather/crowd/shade round trip first.
export async function GET(request: Request) {
  // Fans out to weather + crowd-model + shade lookups per call, so this
  // gets a tighter window than the plain geocode/weather proxies.
  if (isRateLimited(`conditions:${requestIp(request)}`, { windowMs: 60_000, maxPerWindow: 20 })) {
    return NextResponse.json({ conditions: [], error: "Too many requests." }, { status: 429 });
  }

  const { searchParams } = new URL(request.url);
  const lat = Number(searchParams.get("lat"));
  const lon = Number(searchParams.get("lon"));
  const label = searchParams.get("label") ?? "";

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return NextResponse.json({ conditions: [], error: "lat and lon are required." }, { status: 400 });
  }

  const conditions = await conditionProvider.getConditions({ label, lat, lon });
  return NextResponse.json({ conditions });
}
