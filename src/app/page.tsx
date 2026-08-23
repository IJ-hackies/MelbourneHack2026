import Link from "next/link";
import { headers } from "next/headers";
import { PendingLink } from "@/components/pending-link";
import { ConditionIcon } from "@/components/condition-icon";
import { DestinationSearch } from "@/components/destination-search";
import { SavedPlacesRow } from "@/components/saved-places-row";
import { MarketingPage } from "@/components/marketing/marketing-page";
import { formatDeparture } from "@/lib/routes";
import { APEX_HOSTS, getAppOrigin } from "@/lib/hosts";
import { conditionProvider } from "@/lib/providers/condition-provider";
import { routeProvider } from "@/lib/providers/route-provider";
import { listSavedPlaces } from "@/lib/actions/places";
import { listRecentSearches } from "@/lib/actions/searches";
import { createClient } from "@/lib/supabase/server";

type HomeSearchParams = { to?: string; address?: string; lat?: string; lon?: string };

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<HomeSearchParams>;
}) {
  const host = (await headers()).get("host");

  if (host && APEX_HOSTS.includes(host)) {
    const supabase = await createClient();
    const { data } = await supabase.rpc("community_impact").maybeSingle();
    const impactRow = data as { total_walks: number; total_emissions_kg: number } | null;
    const communityImpact = impactRow
      ? { totalWalks: Number(impactRow.total_walks), totalEmissionsKg: Number(impactRow.total_emissions_kg) }
      : null;
    return <MarketingPage appOrigin={getAppOrigin(host)} communityImpact={communityImpact} />;
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return <PlanScreen userId={user?.id ?? null} searchParams={searchParams} />;
}

async function PlanScreen({
  userId,
  searchParams,
}: {
  userId: string | null;
  searchParams: Promise<HomeSearchParams>;
}) {
  const { to, address, lat, lon } = await searchParams;
  const destinationLabel = to?.trim() || null;
  const resolvedLat = lat ? Number(lat) : undefined;
  const resolvedLon = lon ? Number(lon) : undefined;
  const hasCoordinates =
    typeof resolvedLat === "number" &&
    Number.isFinite(resolvedLat) &&
    typeof resolvedLon === "number" &&
    Number.isFinite(resolvedLon);
  // Providers require resolved coordinates; a destination label without them
  // (e.g. a geocode result missing lat/lon) can't be routed or scored yet.
  const destination = destinationLabel && hasCoordinates ? destinationLabel : null;
  const current = destinationLabel
    ? {
        label: destinationLabel,
        address,
        lat: resolvedLat,
        lon: resolvedLon,
      }
    : null;

  let preferences: Parameters<typeof routeProvider.listRoutes>[0]["preferences"];
  if (userId) {
    const supabase = await createClient();
    const { data: profile } = await supabase
      .from("profiles")
      .select(
        "heat_sensitivity, comfort_balance, pace, prefer_quieter_streets, prefer_lower_traffic"
      )
      .eq("id", userId)
      .single();

    if (profile) {
      preferences = {
        heatSensitivity: profile.heat_sensitivity,
        comfortBalance: profile.comfort_balance,
        pace: profile.pace,
        preferQuieterStreets: profile.prefer_quieter_streets,
        preferLowerTraffic: profile.prefer_lower_traffic,
      };
    }
  }

  const [conditions, routeResult, savedPlaces, recentSearches] =
    destination && hasCoordinates
      ? await Promise.all([
          conditionProvider.getConditions({
            label: destination,
            lat: resolvedLat!,
            lon: resolvedLon!,
          }),
          routeProvider.listRoutes({
            destination: { label: destination, lat: resolvedLat!, lon: resolvedLon! },
            departureTime: new Date(),
            preferences,
          }),
          listSavedPlaces(),
          listRecentSearches(),
        ])
      : await Promise.all([
          Promise.resolve([]),
          Promise.resolve({ routes: [], heatContext: null }),
          listSavedPlaces(),
          listRecentSearches(),
        ]);
  const { routes, heatContext } = routeResult;
  const top = routes.find((r) => r.recommended) ?? routes[0];
  const routeHref = (id: string) =>
    `/route/${id}?to=${encodeURIComponent(destination ?? "")}&lat=${resolvedLat}&lon=${resolvedLon}`;

  return (
    <main className="mx-auto grid max-w-xl grid-cols-1 gap-8 px-5 py-8 sm:px-8 lg:max-w-5xl lg:grid-cols-[360px_1fr] lg:items-start lg:gap-12 lg:py-12">
      {!userId && (
        <div className="flex flex-col gap-3 rounded-2xl border border-primary bg-primary-soft p-4 sm:flex-row sm:items-center sm:justify-between lg:col-span-2">
          <div className="flex items-center gap-2.5">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0 text-primary">
              <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
              <circle cx="12" cy="9.5" r="2.4" />
            </svg>
            <p className="text-sm text-text">
              Sign in to save places, track your walks, and personalise your routes.
            </p>
          </div>
          <PendingLink
            href="/login"
            className="shrink-0 rounded-full bg-primary px-4 py-2 text-center text-sm font-semibold text-surface transition-opacity hover:opacity-90"
          >
            Sign in
          </PendingLink>
        </div>
      )}

      <div className="flex flex-col gap-8 lg:sticky lg:top-24">
        <div>
          <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
            Where to?
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Sensible defaults, personalise anytime.
          </p>
        </div>

        <DestinationSearch initialValue={destination ?? ""} recentSearches={recentSearches} />

        <SavedPlacesRow places={savedPlaces} current={current} signedIn={Boolean(userId)} />

        {destination && (
          <div className="grid grid-cols-3 gap-2 lg:grid-cols-1 lg:gap-2.5">
            {conditions.map((c) => (
              <div
                key={c.label}
                className="rounded-2xl border border-border bg-surface px-2.5 py-3 text-center lg:flex lg:items-center lg:gap-3 lg:px-4 lg:py-3 lg:text-left"
              >
                <ConditionIcon
                  tone={c.tone}
                  className="mx-auto mb-1.5 h-[18px] w-[18px] lg:mx-0 lg:mb-0"
                />
                <div className="lg:flex-1">
                  <div className="lg:flex lg:items-baseline lg:justify-between">
                    <div className="font-display text-[1.05rem] font-semibold text-text">
                      {c.value}
                    </div>
                    <div className="mt-0.5 text-[0.68rem] tracking-wide text-text-tertiary uppercase lg:mt-0">
                      {c.label}
                    </div>
                  </div>
                  {c.detail && (
                    <div className="mt-0.5 text-[0.68rem] text-text-tertiary lg:text-right">{c.detail}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-8">
        {!destination ? (
          <div className="rounded-2xl border border-dashed border-border px-6 py-16 text-center">
            <p className="text-sm text-text-secondary">
              Search a street, building, or place to see route options.
            </p>
          </div>
        ) : (
          <>
            {heatContext?.advisory && (
              <div className="flex items-start gap-2.5 rounded-2xl border border-heat/30 bg-heat-soft px-4 py-3">
                <ConditionIcon tone="heat" className="mt-0.5 h-4 w-4 shrink-0" />
                <p className="text-[0.84rem] text-text">
                  {heatContext.extreme ? "Extreme heat" : "Heat advisory"} —{" "}
                  {Math.round(heatContext.temperatureC!)}°C right now, so shaded routing is
                  prioritising tree canopy more heavily than usual.
                </p>
              </div>
            )}

            <div>
              <h2 className="font-display text-lg font-semibold tracking-tight text-text lg:text-xl">
                {routes.length === 1 ? `Your route to ${destination}` : `${routes.length} ways to ${destination}`}
              </h2>
              <p className="mt-1 text-sm text-text-secondary">{formatDeparture(new Date())}</p>

              <div className="mt-4 flex flex-col gap-2.5 lg:grid lg:grid-cols-2 lg:items-start">
                {routes.map((route) => (
                  <Link
                    key={route.id}
                    href={routeHref(route.id)}
                    className={`block rounded-2xl border p-4 transition-colors ${
                      route.recommended
                        ? "border-primary bg-primary-soft"
                        : "border-border bg-surface hover:border-text-tertiary"
                    }`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="font-display text-[1.35rem] font-semibold tracking-tight text-text">
                        {route.minutes}
                        <span className="ml-0.5 font-sans text-[0.7rem] font-medium text-text-tertiary">
                          min
                        </span>
                      </div>
                      {route.recommended && routes.length > 1 && (
                        <span className="shrink-0 rounded-full bg-primary px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide whitespace-nowrap text-surface uppercase">
                          Recommended
                        </span>
                      )}
                    </div>
                    <p className="mt-1.5 text-[0.84rem] text-text-secondary">
                      {route.description}
                    </p>
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {route.tags.map((tag) => (
                        <span
                          key={tag.label}
                          className={`rounded-lg px-2 py-1 text-[0.72rem] ${
                            tag.tone === "warm"
                              ? "bg-heat-soft text-heat"
                              : "bg-surface-alt text-text-secondary"
                          }`}
                        >
                          {tag.label}
                        </span>
                      ))}
                    </div>
                  </Link>
                ))}
              </div>
            </div>

            {top && (
              <PendingLink
                href={routeHref(top.id)}
                className="rounded-2xl bg-primary py-3.5 text-center font-semibold text-surface shadow-[0_10px_22px_-12px_hsl(160_30%_15%/0.45)] lg:max-w-sm"
              >
                Start walking, {top.minutes} min pick
              </PendingLink>
            )}
          </>
        )}
      </div>
    </main>
  );
}
