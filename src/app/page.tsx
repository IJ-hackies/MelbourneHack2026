import { headers } from "next/headers";
import { PendingLink } from "@/components/pending-link";
import { ConditionsPanel } from "@/components/conditions-panel";
import { DestinationSearch } from "@/components/destination-search";
import { SavedPlacesRow } from "@/components/saved-places-row";
import { MarketingPage } from "@/components/marketing/marketing-page";
import { RoutePlanner } from "@/components/route-planner";
import { APEX_HOSTS, getAppOrigin } from "@/lib/hosts";
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

  // Conditions and routes both load client-side (ConditionsPanel,
  // RoutePlanner) so they can start fetching in parallel the moment this
  // page paints, each with its own skeleton, rather than one blocking the
  // page render and the other only starting once that finished.
  const [savedPlaces, recentSearches] = await Promise.all([listSavedPlaces(), listRecentSearches()]);

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

        <DestinationSearch initialValue={destinationLabel ?? ""} recentSearches={recentSearches} />

        <SavedPlacesRow places={savedPlaces} current={current} signedIn={Boolean(userId)} />

        {destination && (
          <ConditionsPanel destination={{ label: destination, lat: resolvedLat!, lon: resolvedLon! }} />
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
          <RoutePlanner destination={{ label: destination, lat: resolvedLat!, lon: resolvedLon! }} />
        )}
      </div>
    </main>
  );
}
