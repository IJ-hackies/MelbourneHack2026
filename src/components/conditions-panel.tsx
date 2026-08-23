"use client";

import { useEffect, useState } from "react";
import { ConditionIcon } from "@/components/condition-icon";
import { getCached, setCached } from "@/lib/client-cache";
import type { Condition } from "@/lib/providers/types";

// Short TTL — long enough that re-selecting the same destination a moment
// later (e.g. clicking back from a route, or re-picking a saved place)
// feels instant, short enough that live weather/crowd/shade never goes
// stale for long.
const CONDITIONS_CACHE_TTL_MS = 90_000;

function ConditionSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-border bg-surface px-2.5 py-3 lg:flex lg:items-center lg:gap-3 lg:px-4 lg:py-3">
      <div className="mx-auto h-[18px] w-[18px] rounded-full bg-surface-alt lg:mx-0" />
      <div className="mt-2 flex flex-col items-center gap-1.5 lg:mt-0 lg:flex-1 lg:items-start">
        <div className="h-2.5 w-16 rounded bg-surface-alt" />
        <div className="h-4 w-20 rounded bg-surface-alt" />
      </div>
    </div>
  );
}

// Loads on mount, in parallel with RoutePlanner's own route fetch — both
// start the moment the plan page renders instead of one blocking the other
// (this used to be fetched server-side before the page painted at all).
export function ConditionsPanel({
  destination,
}: {
  destination: { label: string; lat: number; lon: number };
}) {
  const [conditions, setConditions] = useState<Condition[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    const cacheKey = `conditions:${destination.lat.toFixed(5)},${destination.lon.toFixed(5)}`;

    async function load() {
      const cached = getCached<Condition[]>(cacheKey);
      if (cached) {
        setConditions(cached);
        return;
      }

      setConditions(null);
      const params = new URLSearchParams({
        label: destination.label,
        lat: String(destination.lat),
        lon: String(destination.lon),
      });
      try {
        const res = await fetch(`/api/conditions?${params.toString()}`, { cache: "no-store" });
        const data = await res.json();
        const result: Condition[] = data.conditions ?? [];
        if (!cancelled) setConditions(result);
        setCached(cacheKey, result, CONDITIONS_CACHE_TTL_MS);
      } catch {
        if (!cancelled) setConditions([]);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [destination.label, destination.lat, destination.lon]);

  return (
    <div className="grid grid-cols-3 gap-2 lg:grid-cols-1 lg:gap-2.5">
      {conditions === null
        ? Array.from({ length: 3 }, (_, i) => <ConditionSkeleton key={i} />)
        : conditions.map((c) => (
            <div
              key={c.label}
              className="rounded-2xl border border-border bg-surface px-2.5 py-3 text-center lg:flex lg:items-center lg:gap-3 lg:px-4 lg:py-3 lg:text-left"
            >
              <ConditionIcon tone={c.tone} className="mx-auto mb-1.5 h-[18px] w-[18px] lg:mx-0 lg:mb-0" />
              <div className="min-w-0 lg:flex-1">
                <div className="text-[0.66rem] font-medium tracking-wide text-text-tertiary uppercase">
                  {c.label}
                </div>
                <div className="mt-0.5 font-display text-[0.98rem] leading-tight font-semibold text-text">
                  {c.value}
                </div>
                {c.detail && (
                  <div className="mt-0.5 text-[0.68rem] text-text-tertiary">{c.detail}</div>
                )}
              </div>
            </div>
          ))}
    </div>
  );
}
