import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ActiveWalk } from "@/components/active-walk";
import { ConditionIcon } from "@/components/condition-icon";
import { RouteMap } from "@/components/route-map-lazy";
import { ShareRouteButton } from "@/components/share-route-button";
import { routeProvider } from "@/lib/providers/route-provider";
import { getQuietestHourToday } from "@/lib/providers/quietest-hour";
import { LiveProgressProvider } from "@/lib/live-progress-context";
import { createClient } from "@/lib/supabase/server";

function buildRouteHref(
  routeId: string,
  {
    destination,
    resolvedLat,
    resolvedLon,
    origin,
  }: { destination: string; resolvedLat: number; resolvedLon: number; origin?: { lat: number; lon: number } }
) {
  const params = new URLSearchParams({
    to: destination,
    lat: String(resolvedLat),
    lon: String(resolvedLon),
  });
  if (origin) {
    params.set("originLat", String(origin.lat));
    params.set("originLon", String(origin.lon));
  }
  return `/route/${routeId}?${params.toString()}`;
}

export default async function RouteDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ to?: string; lat?: string; lon?: string; originLat?: string; originLon?: string }>;
}) {
  const { id } = await params;
  const { to, lat, lon, originLat, originLon } = await searchParams;
  const destination = to?.trim();
  const resolvedLat = lat ? Number(lat) : NaN;
  const resolvedLon = lon ? Number(lon) : NaN;
  if (!destination || !Number.isFinite(resolvedLat) || !Number.isFinite(resolvedLon)) {
    redirect("/");
  }
  // Real live-location origin, carried over from the plan page's own
  // geolocation fix (RoutePlanner) — falls back to the provider's static
  // default only when this page was reached without going through the
  // planner (e.g. a bookmarked/shared link), never re-guessed here.
  const resolvedOriginLat = originLat ? Number(originLat) : NaN;
  const resolvedOriginLon = originLon ? Number(originLon) : NaN;
  const origin =
    Number.isFinite(resolvedOriginLat) && Number.isFinite(resolvedOriginLon)
      ? { lat: resolvedOriginLat, lon: resolvedOriginLon }
      : undefined;
  const [{ routes }, quietestHour] = await Promise.all([
    routeProvider.listRoutes({
      destination: { label: destination, lat: resolvedLat, lon: resolvedLon },
      origin,
    }),
    getQuietestHourToday({ lat: resolvedLat, lon: resolvedLon }),
  ]);
  const route = routes.find((r) => r.id === id);
  if (!route) notFound();
  const otherRoutes = routes.filter((r) => r.id !== id);

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <LiveProgressProvider>
    <main className="mx-auto grid max-w-xl grid-cols-1 gap-6 px-5 py-8 sm:px-8 lg:max-w-5xl lg:grid-cols-[1fr_340px] lg:items-start lg:gap-12 lg:py-12">
      <div className="flex flex-col gap-6 lg:col-span-2">
        <div className="flex items-center justify-between gap-4">
          <Link
            href={`/?to=${encodeURIComponent(destination)}&lat=${resolvedLat}&lon=${resolvedLon}`}
            className="flex items-center gap-1.5 text-sm text-text-secondary hover:text-text"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="m15 18-6-6 6-6" />
            </svg>
            All routes to {destination}
          </Link>
          <ShareRouteButton destination={destination} minutes={route.minutes} />
        </div>

        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
              {route.minutes} min
            </h1>
            {route.quality === "unavailable" && (
              <span className="rounded-full bg-heat-soft px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide text-heat uppercase">
                Estimated
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-text-secondary">
            {route.distanceKm} km to {destination}
          </p>
        </div>

        {otherRoutes.length > 0 && (
          <div className="flex gap-2.5 overflow-x-auto pb-1">
            {otherRoutes.map((other) => (
              <Link
                key={other.id}
                href={buildRouteHref(other.id, { destination, resolvedLat, resolvedLon, origin })}
                className="flex shrink-0 items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-2.5 transition-colors hover:border-text-tertiary"
              >
                <div className="font-display text-base font-semibold text-text">
                  {other.minutes}
                  <span className="ml-0.5 font-sans text-[0.68rem] font-medium text-text-tertiary">
                    min
                  </span>
                </div>
                <div className="text-left">
                  <div className="text-[0.78rem] font-medium text-text-secondary">
                    {other.tags[0]?.label ?? "Alternative"}
                  </div>
                  <div className="text-[0.7rem] text-text-tertiary">Switch to this route</div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-6">
        {route.quality === "unavailable" && (
          <div className="flex items-start gap-2.5 rounded-2xl border border-heat/30 bg-heat-soft px-4 py-3">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mt-0.5 h-4 w-4 shrink-0 text-heat">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v5M12 16h.01" />
            </svg>
            <p className="text-[0.84rem] text-text">
              <strong className="font-semibold">No street-level directions here yet.</strong>{" "}
              {destination} is outside the area we currently have real walking-path data for.
              The line below is just a straight-line estimate to help you gauge distance, not a
              route you should actually follow.
            </p>
          </div>
        )}

        <div className="h-72 overflow-hidden rounded-2xl border border-border bg-surface-alt sm:h-96 lg:h-[32rem]">
          <RouteMap
            geometry={route.geometry}
            segments={route.segments}
            routeId={route.id}
            quality={route.quality}
          />
        </div>

        <p className="text-sm text-text-secondary">{route.description}</p>
        {(route.canopyDensityAvg !== null || route.pedestrianFlowAvgPerHour !== null) && (
          <div className="flex flex-wrap gap-2 text-[0.76rem] text-text-tertiary">
            {route.canopyDensityAvg !== null && (
              <span>Canopy density along route: {Math.round(route.canopyDensityAvg * 100)}%</span>
            )}
            {route.pedestrianFlowAvgPerHour !== null && (
              <span>Avg. nearby foot traffic: {Math.round(route.pedestrianFlowAvgPerHour)}/hr</span>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-6 lg:sticky lg:top-24">
        {quietestHour && (
          <div className="flex items-start gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3">
            <ConditionIcon tone="crowd" className="mt-0.5 h-4 w-4 shrink-0" />
            <p className="text-[0.84rem] text-text">
              Foot traffic near {destination} looks quietest around{" "}
              <span className="font-semibold">{quietestHour.label}</span> today, if you&apos;d
              rather wait.
            </p>
          </div>
        )}

        <ActiveWalk
          routeId={route.id}
          destination={destination}
          minutes={route.minutes}
          distanceKm={route.distanceKm}
          signedIn={Boolean(user)}
        />
      </div>
    </main>
    </LiveProgressProvider>
  );
}
