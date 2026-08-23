"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { listRecentSearches, logSearch, type RecentSearch } from "@/lib/actions/searches";
import type { PlaceCategory } from "@/app/api/geocode/route";

type Suggestion = {
  label: string;
  address: string;
  lat?: number;
  lon?: number;
  category?: PlaceCategory;
  distanceKm?: number | null;
};

// Shared across every mount within the session — retyping something you
// already searched for shouldn't re-hit the network (Photon's public
// endpoint is rate-limited and has no SLA).
const searchCache = new Map<string, Suggestion[]>();

const GEOLOCATION_TIMEOUT_MS = 4000;

// A recognisable icon per result type (restaurant, park, transit, ...) —
// the same at-a-glance scanability Google/Apple Maps give autocomplete
// results, instead of every suggestion looking identical regardless of
// what it actually is.
function CategoryIcon({ category }: { category?: PlaceCategory }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: "h-4 w-4",
  };
  switch (category) {
    case "food":
      return (
        <svg {...common}>
          <path d="M7 3v6a2 2 0 0 0 4 0V3M9 9v12M17 3c-1.5 0-3 1.5-3 4s1.5 4 3 4v9" />
        </svg>
      );
    case "park":
      return (
        <svg {...common}>
          <path d="M12 3 7 11h3l-4 6h5v4M12 3l5 8h-3l4 6h-5" />
        </svg>
      );
    case "transit":
      return (
        <svg {...common}>
          <rect x="5" y="4" width="14" height="14" rx="3" />
          <path d="M8 21l1.5-3M16 21l-1.5-3M8 12h8" />
          <circle cx="8.5" cy="16" r="0.5" fill="currentColor" />
          <circle cx="15.5" cy="16" r="0.5" fill="currentColor" />
        </svg>
      );
    case "shop":
      return (
        <svg {...common}>
          <path d="M4 8 5.5 4h13L20 8M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8M4 8h16M9 12a3 3 0 0 0 6 0" />
        </svg>
      );
    case "lodging":
      return (
        <svg {...common}>
          <path d="M3 21V6M3 12h17a1 1 0 0 1 1 1v8M3 8h9a2 2 0 0 1 2 2v2" />
        </svg>
      );
    case "landmark":
      return (
        <svg {...common}>
          <path d="M12 3 3 8v2h18V8ZM5 10v9M9 10v9M15 10v9M19 10v9M3 21h18" />
        </svg>
      );
    case "address":
      return (
        <svg {...common}>
          <path d="M4 21V9l8-6 8 6v12M9 21v-6h6v6" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
          <circle cx="12" cy="9.5" r="2.4" />
        </svg>
      );
  }
}

function resolveUserLocation(): Promise<{ lat: number; lon: number } | null> {
  if (typeof navigator === "undefined" || !navigator.geolocation) return Promise.resolve(null);
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({ lat: position.coords.latitude, lon: position.coords.longitude }),
      () => resolve(null),
      { timeout: GEOLOCATION_TIMEOUT_MS, maximumAge: 300_000 }
    );
  });
}

export function DestinationSearch({
  initialValue,
  signedIn = false,
}: {
  initialValue: string;
  signedIn?: boolean;
}) {
  const router = useRouter();
  const [query, setQuery] = useState(initialValue);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [recentSearches, setRecentSearches] = useState<RecentSearch[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Enter with no suggestion highlighted re-sorts the current results by
  // real distance and surfaces that ordering explicitly, rather than the
  // plain relevance order search normally shows — "closest things near me
  // matching this" the way pressing enter in Google Maps' search box does.
  const [closestMode, setClosestMode] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const userLocationRef = useRef<{ lat: number; lon: number } | null>(null);

  // Fetched client-side (a guest always gets none, server-side, so this is
  // skipped entirely for guests) rather than passed down from an awaited
  // server call — recent searches previously blocked the whole plan page's
  // render on every navigation.
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    listRecentSearches().then((result) => {
      if (!cancelled) setRecentSearches(result);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  // Best-effort and silent — a denied/unavailable permission just means
  // suggestions stay in plain relevance order instead of location-biased,
  // never a blocking prompt or visible error.
  useEffect(() => {
    resolveUserLocation().then((loc) => {
      userLocationRef.current = loc;
    });
  }, []);

  // Matches the geocode API's own minimum (2 chars) — short real queries
  // like "MCG" or "GPO" shouldn't need a third character before searching.
  const showingRecents = query.trim().length < 2;
  const baseItems: Suggestion[] = showingRecents
    ? recentSearches.map((r) => ({
        label: r.label,
        address: r.address ?? "",
        lat: r.lat ?? undefined,
        lon: r.lon ?? undefined,
      }))
    : suggestions;
  const items =
    closestMode && !showingRecents
      ? [...baseItems].sort((a, b) => (a.distanceKm ?? Infinity) - (b.distanceKm ?? Infinity))
      : baseItems;

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  function handleChange(value: string) {
    setQuery(value);
    setActiveIndex(-1);
    setClosestMode(false);
    setError(null);

    if (debounceRef.current) clearTimeout(debounceRef.current);
    abortRef.current?.abort();

    if (value.trim().length < 2) {
      setSuggestions([]);
      setLoading(false);
      setOpen(recentSearches.length > 0);
      return;
    }

    const loc = userLocationRef.current;
    const cacheKey = `${value.trim().toLowerCase()}@${loc ? `${loc.lat.toFixed(3)},${loc.lon.toFixed(3)}` : "default"}`;
    const cached = searchCache.get(cacheKey);
    if (cached) {
      setSuggestions(cached);
      setOpen(true);
      setLoading(false);
      return;
    }

    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const params = new URLSearchParams({ q: value });
        if (loc) {
          params.set("lat", String(loc.lat));
          params.set("lon", String(loc.lon));
        }
        const res = await fetch(`/api/geocode?${params.toString()}`, {
          signal: controller.signal,
        });
        const data = await res.json();
        if (!res.ok) {
          setError(data.error ?? "Search is temporarily unavailable.");
          setSuggestions([]);
        } else {
          searchCache.set(cacheKey, data.results ?? []);
          setSuggestions(data.results ?? []);
        }
        setOpen(true);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError("Search is temporarily unavailable.");
          setSuggestions([]);
          setOpen(true);
        }
      } finally {
        setLoading(false);
      }
    }, 300);
  }

  function select(s: Suggestion) {
    setQuery(s.label);
    setOpen(false);
    setClosestMode(false);
    setSuggestions([]);
    logSearch(s).catch(() => {});
    const params = new URLSearchParams({ to: s.label, address: s.address });
    if (s.lat != null) params.set("lat", String(s.lat));
    if (s.lon != null) params.set("lon", String(s.lon));
    router.push(`/?${params.toString()}`);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      select(items[activeIndex]);
    } else if (e.key === "Enter" && activeIndex === -1) {
      e.preventDefault();
      // First Enter with nothing highlighted: reveal the closest-first
      // ordering and highlight the top (closest) match rather than
      // guessing which of several similarly-named results was meant.
      if (!closestMode && !showingRecents && items.some((i) => i.distanceKm != null)) {
        setClosestMode(true);
        setActiveIndex(0);
      } else {
        select(items[0]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <label className="flex items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3.5 text-text-tertiary has-[:focus]:border-primary">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="h-[17px] w-[17px] shrink-0"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => (items.length > 0 ? setOpen(true) : undefined)}
          onKeyDown={handleKeyDown}
          placeholder="Search a street, building, or place in Melbourne"
          role="combobox"
          aria-expanded={open}
          aria-controls="destination-suggestions"
          aria-autocomplete="list"
          className="w-full bg-transparent text-[0.95rem] text-text placeholder:text-text-tertiary focus:outline-none"
        />
        {loading && (
          <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-border border-t-primary" />
        )}
      </label>

      {open && error && (
        <div className="absolute z-30 mt-2 w-full rounded-2xl border border-border bg-surface px-4 py-3 text-sm text-text-tertiary shadow-lg">
          {error}
        </div>
      )}

      {open && !error && items.length > 0 && (
        <ul
          id="destination-suggestions"
          role="listbox"
          className="absolute z-30 mt-2 max-h-80 w-full overflow-y-auto rounded-2xl border border-border bg-surface shadow-lg"
        >
          {(showingRecents || closestMode) && (
            <ListHeading>
              {showingRecents ? (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 7v5l3 3" />
                  </svg>
                  Recent
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                    <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
                    <circle cx="12" cy="9.5" r="2.4" />
                  </svg>
                  Closest matches
                </>
              )}
            </ListHeading>
          )}
          {items.map((s, i) => (
            <li key={`${s.label}-${s.lat}-${s.lon}`}>
              <button
                type="button"
                onClick={() => select(s)}
                onMouseEnter={() => setActiveIndex(i)}
                className={`flex w-full items-start gap-2.5 px-4 py-3 text-left transition-colors ${
                  i === activeIndex ? "bg-surface-alt" : ""
                }`}
              >
                <span className="mt-0.5 shrink-0 text-text-tertiary">
                  <CategoryIcon category={s.category} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-text">{s.label}</span>
                  {s.address && (
                    <span className="block truncate text-xs text-text-tertiary">{s.address}</span>
                  )}
                </span>
                {s.distanceKm != null && (
                  <span className="shrink-0 text-[0.72rem] text-text-tertiary">
                    {s.distanceKm < 1 ? `${Math.round(s.distanceKm * 1000)} m` : `${s.distanceKm.toFixed(1)} km`}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ListHeading({ children }: { children: ReactNode }) {
  return (
    <div className="sticky top-0 flex items-center gap-1.5 border-b border-border bg-surface px-4 py-2 text-[0.7rem] tracking-wide text-text-tertiary uppercase">
      {children}
    </div>
  );
}
