"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ConditionIcon } from "@/components/condition-icon";
import { Spinner } from "@/components/spinner";
import { formatDeparture } from "@/lib/routes";
import type { Coordinates, RouteOption } from "@/lib/providers/types";

type HeatContext = { temperatureC: number | null; advisory: boolean; extreme: boolean } | null;

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
}: {
  destination: { label: string; lat: number; lon: number };
}) {
  const router = useRouter();
  const [routes, setRoutes] = useState<RouteOption[] | null>(null);
  const [heatContext, setHeatContext] = useState<HeatContext>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [navigating, setNavigating] = useState(false);
  const originRef = useRef<{ origin: Coordinates; isReal: boolean } | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setRoutes(null);
      setSelectedId(null);

      // Geolocation permission is requested once per mount of the plan
      // screen and cached in a ref — switching destinations re-fetches
      // routes but must not re-prompt for location every time.
      if (!originRef.current) {
        originRef.current = await resolveOrigin();
      }
      if (cancelled) return;

      const { origin } = originRef.current;
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
        setRoutes(data.routes ?? []);
        setHeatContext(data.heatContext ?? null);
        setSelectedId(data.routes?.find((r: RouteOption) => r.recommended)?.id ?? data.routes?.[0]?.id ?? null);
      } catch {
        if (!cancelled) setRoutes([]);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [destination.lat, destination.lon, destination.label]);

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

  return (
    <>
      {heatContext?.advisory && (
        <div className="mb-6 flex items-start gap-2.5 rounded-2xl border border-heat/30 bg-heat-soft px-4 py-3">
          <ConditionIcon tone="heat" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="text-[0.84rem] text-text">
            {heatContext.extreme ? "Extreme heat" : "Heat advisory"} —{" "}
            {Math.round(heatContext.temperatureC!)}°C right now, so shaded routing is prioritising
            tree canopy more heavily than usual.
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
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {route.tags.map((tag) => (
                  <span
                    key={tag.label}
                    className={`rounded-lg px-2 py-1 text-[0.72rem] ${
                      tag.tone === "warm" ? "bg-heat-soft text-heat" : "bg-surface-alt text-text-secondary"
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
