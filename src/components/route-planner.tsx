"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ConditionIcon } from "@/components/condition-icon";
import { Spinner } from "@/components/spinner";
import { formatDeparture } from "@/lib/routes";
import { getCached, setCached } from "@/lib/client-cache";
import type { Coordinates, RouteOption } from "@/lib/providers/types";

// Same short-lived, correctness-conscious trade-off as ConditionsPanel —
// long enough to make re-picking a destination you just looked at instant,
// short enough that live heat-adaptive bias/crowd data never goes stale.
const ROUTES_CACHE_TTL_MS = 90_000;
type CachedRoutePlan = { routes: RouteOption[]; heatContext: HeatContext; quietestHour: QuietestHour };

type HeatContext = { temperatureC: number | null; advisory: boolean; extreme: boolean } | null;
type QuietestHour = { label: string; pedestrianFlowPerHour: number } | null;

// Same default the server-side provider falls back to (State Library of
// Victoria, central Melbourne CBD) — used only if a real location can't be
// obtained (denied permission, unsupported browser, or a timeout), never
// silently preferred over a real fix.
const DEFAULT_ORIGIN: Coordinates = { lat: -37.8098, lon: 144.9652 };
const GEOLOCATION_TIMEOUT_MS = 8000;

function resolveOrigin(): Promise<{ origin: Coordinates; isReal: boolean }> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return Promise.resolve({ origin: DEFAULT_ORIGIN, isReal: false });
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          origin: { lat: position.coords.latitude, lon: position.coords.longitude },
          isReal: true,
        }),
      () => resolve({ origin: DEFAULT_ORIGIN, isReal: false }),
      { enableHighAccuracy: true, timeout: GEOLOCATION_TIMEOUT_MS, maximumAge: 60_000 }
    );
  });
}

// Makes the actual trade-off a route candidate offers legible at a glance —
// e.g. "+3 min · 42% more shade" — instead of leaving the user to compare
// raw minutes/canopy numbers across cards themselves.
function tradeOffLabel(route: RouteOption, fastest: RouteOption): string {
  const parts: string[] = [];
  // Rounded explicitly — route.minutes/fastest.minutes are each already
  // rounded to 1dp server-side, but subtracting two such floats (e.g.
  // 30.2 - 21.6) can still land on a binary-float artefact like
  // 8.599999999999998 without this.
  const minuteDelta = Math.round((route.minutes - fastest.minutes) * 10) / 10;
  parts.push(minuteDelta > 0 ? `+${minuteDelta} min` : minuteDelta < 0 ? `${minuteDelta} min` : "Same time");

  if (
    route.id === "shaded" &&
    route.canopyDensityAvg !== null &&
    fastest.canopyDensityAvg !== null
  ) {
    const shadeDeltaPts = Math.round((route.canopyDensityAvg - fastest.canopyDensityAvg) * 100);
    if (shadeDeltaPts !== 0) parts.push(`${shadeDeltaPts > 0 ? "+" : ""}${shadeDeltaPts}pt canopy`);
  }
  if (
    route.id === "quieter" &&
    route.pedestrianFlowAvgPerHour !== null &&
    fastest.pedestrianFlowAvgPerHour !== null &&
    fastest.pedestrianFlowAvgPerHour > 0
  ) {
    const crowdDeltaPct = Math.round(
      ((route.pedestrianFlowAvgPerHour - fastest.pedestrianFlowAvgPerHour) / fastest.pedestrianFlowAvgPerHour) * 100
    );
    if (crowdDeltaPct !== 0) parts.push(`${crowdDeltaPct}% foot traffic`);
  }
  return `vs. fastest: ${parts.join(" · ")}`;
}

function RouteCardSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-border bg-surface p-4">
      <div className="h-6 w-16 rounded bg-surface-alt" />
      <div className="mt-2.5 h-3.5 w-4/5 rounded bg-surface-alt" />
      <div className="mt-2.5 flex gap-1.5">
        <div className="h-5 w-16 rounded-lg bg-surface-alt" />
      </div>
    </div>
  );
}

export function RoutePlanner({
  destination,
  signedIn,
}: {
  destination: { label: string; lat: number; lon: number };
  signedIn: boolean;
}) {
  const router = useRouter();
  const [routes, setRoutes] = useState<RouteOption[] | null>(null);
  const [heatContext, setHeatContext] = useState<HeatContext>(null);
  const [quietestHour, setQuietestHour] = useState<QuietestHour>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [navigating, setNavigating] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const originRef = useRef<{ origin: Coordinates; isReal: boolean } | null>(null);
  const [usingFallbackOrigin, setUsingFallbackOrigin] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setRoutes(null);
      setSelectedId(null);
      setLoadError(false);

      // Geolocation permission is requested once per mount of the plan
      // screen and cached in a ref — switching destinations re-fetches
      // routes but must not re-prompt for location every time.
      if (!originRef.current) {
        originRef.current = await resolveOrigin();
        setUsingFallbackOrigin(!originRef.current.isReal);
      }
      if (cancelled) return;

      const { origin } = originRef.current;
      // Rounded to ~11m precision so trivial GPS jitter between two quick
      // searches from roughly the same spot still hits the cache.
      const cacheKey = `routes:${origin.lat.toFixed(4)},${origin.lon.toFixed(4)}->${destination.lat.toFixed(4)},${destination.lon.toFixed(4)}`;
      const cached = getCached<CachedRoutePlan>(cacheKey);
      if (cached) {
        setRoutes(cached.routes);
        setHeatContext(cached.heatContext);
        setQuietestHour(cached.quietestHour);
        setSelectedId(cached.routes.find((r) => r.recommended)?.id ?? cached.routes[0]?.id ?? null);
        if (cached.routes.length === 0) setLoadError(true);
        return;
      }

      const params = new URLSearchParams({
        destLat: String(destination.lat),
        destLon: String(destination.lon),
        destLabel: destination.label,
        originLat: String(origin.lat),
        originLon: String(origin.lon),
      });
      try {
        const res = await fetch(`/api/plan-routes?${params.toString()}`, { cache: "no-store" });
        const data = await res.json();
        if (cancelled) return;
        const nextRoutes: RouteOption[] = data.routes ?? [];
        const nextHeatContext: HeatContext = data.heatContext ?? null;
        const nextQuietestHour: QuietestHour = data.quietestHour ?? null;
        setRoutes(nextRoutes);
        setHeatContext(nextHeatContext);
        setQuietestHour(nextQuietestHour);
        setSelectedId(nextRoutes.find((r) => r.recommended)?.id ?? nextRoutes[0]?.id ?? null);
        if (nextRoutes.length === 0) {
          setLoadError(true);
        } else {
          setCached(cacheKey, { routes: nextRoutes, heatContext: nextHeatContext, quietestHour: nextQuietestHour }, ROUTES_CACHE_TTL_MS);
        }
      } catch {
        if (!cancelled) {
          setRoutes([]);
          setLoadError(true);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [destination.lat, destination.lon, destination.label, retryCount]);

  function startWalking() {
    const selected = routes?.find((r) => r.id === selectedId);
    if (!selected || navigating) return;
    setNavigating(true);
    const origin = originRef.current?.origin ?? DEFAULT_ORIGIN;
    const params = new URLSearchParams({
      to: destination.label,
      lat: String(destination.lat),
      lon: String(destination.lon),
      originLat: String(origin.lat),
      originLon: String(origin.lon),
    });
    router.push(`/route/${selected.id}?${params.toString()}`);
  }

  const selected = routes?.find((r) => r.id === selectedId) ?? null;

  if (routes === null) {
    return (
      <div>
        <div className="h-6 w-40 animate-pulse rounded bg-surface-alt" />
        <div className="mt-4 flex flex-col gap-2.5 lg:grid lg:grid-cols-2 lg:items-start">
          <RouteCardSkeleton />
          <RouteCardSkeleton />
        </div>
        <p className="mt-4 flex items-center gap-2 text-sm text-text-tertiary">
          <Spinner className="h-3.5 w-3.5" />
          Finding routes to {destination.label}…
        </p>
      </div>
    );
  }

  if (loadError && routes.length === 0) {
    return (
      <div className="rounded-2xl border border-border bg-surface p-5 text-center">
        <p className="text-sm text-text-secondary">
          Couldn&apos;t find a route to {destination.label} right now.
        </p>
        <button
          type="button"
          onClick={() => setRetryCount((n) => n + 1)}
          className="mt-3 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-surface"
        >
          Try again
        </button>
      </div>
    );
  }

  const fastest = routes.find((r) => r.id === "fastest") ?? routes[0] ?? null;

  return (
    <>
      {heatContext?.advisory && (
        <div className="mb-6 flex items-start gap-2.5 rounded-2xl border border-heat/30 bg-heat-soft px-4 py-3">
          <ConditionIcon tone="heat" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="text-[0.84rem] text-text">
            <strong className="font-semibold">
              {heatContext.extreme ? "Extreme heat" : "Heat advisory"}:
            </strong>{" "}
            it&apos;s {Math.round(heatContext.temperatureC!)}°C right now, so shaded routing is
            leaning on tree canopy more heavily than usual.
          </p>
        </div>
      )}

      {usingFallbackOrigin && (
        <div className="mb-6 flex items-start gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3">
          <ConditionIcon tone="primary" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="text-[0.84rem] text-text">
            Couldn&apos;t get your location, these routes start from central Melbourne
            instead.{" "}
            <button
              type="button"
              onClick={() => {
                // Clearing the ref (not just bumping retryCount) makes the
                // load effect re-request geolocation instead of reusing the
                // cached fallback — a fresh permission prompt, not just a
                // page refresh the user would otherwise have to do by hand.
                originRef.current = null;
                setRetryCount((n) => n + 1);
              }}
              className="font-medium underline hover:no-underline"
            >
              Try again
            </button>
          </p>
        </div>
      )}

      {quietestHour && (
        <div className="mb-6 flex items-start gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3">
          <ConditionIcon tone="crowd" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="text-[0.84rem] text-text">
            Foot traffic near {destination.label} looks quietest around{" "}
            <span className="font-semibold">{quietestHour.label}</span> today, based on live
            crowd-sensor predictions.
          </p>
        </div>
      )}

      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight text-text lg:text-xl">
          {routes.length === 1 ? `Your route to ${destination.label}` : `${routes.length} ways to ${destination.label}`}
        </h2>
        <p className="mt-1 text-sm text-text-secondary">{formatDeparture(new Date())}</p>

        <div className="mt-4 flex flex-col gap-2.5 lg:grid lg:grid-cols-2 lg:items-start">
          {routes.map((route) => (
            <button
              key={route.id}
              type="button"
              onClick={() => setSelectedId(route.id)}
              aria-pressed={route.id === selectedId}
              className={`block rounded-2xl border p-4 text-left transition-colors ${
                route.id === selectedId
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
                {route.id === selectedId && routes.length > 1 && (
                  <span className="shrink-0 rounded-full bg-primary px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide whitespace-nowrap text-surface uppercase">
                    Selected
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-[0.84rem] text-text-secondary">{route.description}</p>
              {fastest && route.id !== "fastest" && (
                <p className="mt-1 text-[0.78rem] text-text-tertiary">
                  {tradeOffLabel(route, fastest)}
                </p>
              )}
              {signedIn && route.recommended && routes.length > 1 && (
                <p className="mt-1.5 flex items-center gap-1 text-[0.76rem] font-medium text-primary-strong">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 shrink-0">
                    <path d="M12 2l2.9 6.3 6.9.9-5 4.8 1.3 6.8L12 17.5 5.9 20.8l1.3-6.8-5-4.8 6.9-.9L12 2z" />
                  </svg>
                  Recommended for you, based on your preferences
                </p>
              )}
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {route.tags.map((tag) => (
                  <span
                    key={tag.label}
                    className={`rounded-lg px-2 py-1 text-[0.76rem] font-medium ${
                      tag.tone === "warm" ? "bg-heat-soft text-heat" : "bg-surface-alt text-text"
                    }`}
                  >
                    {tag.label}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </div>

      {selected && (
        <button
          type="button"
          onClick={startWalking}
          disabled={navigating}
          className="mt-8 flex items-center justify-center gap-2 rounded-2xl bg-primary py-3.5 text-center font-semibold text-surface shadow-[0_10px_22px_-12px_hsl(160_30%_15%/0.45)] disabled:opacity-70 lg:max-w-sm"
        >
          {navigating && <Spinner className="h-4 w-4 text-current" />}
          Start walking, {selected.minutes} min
        </button>
      )}
    </>
  );
}
