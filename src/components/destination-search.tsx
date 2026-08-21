"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { logSearch, type RecentSearch } from "@/lib/actions/searches";

type Suggestion = {
  label: string;
  address: string;
  lat: number;
  lon: number;
};

// Shared across every mount within the session — retyping something you
// already searched for shouldn't re-hit the network (Nominatim's public
// endpoint is rate-limited and has no SLA).
const searchCache = new Map<string, Suggestion[]>();

export function DestinationSearch({
  initialValue,
  recentSearches = [],
}: {
  initialValue: string;
  recentSearches?: RecentSearch[];
}) {
  const router = useRouter();
  const [query, setQuery] = useState(initialValue);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const showingRecents = query.trim().length < 3;
  const items: Suggestion[] = showingRecents
    ? recentSearches.map((r) => ({
        label: r.label,
        address: r.address ?? "",
        lat: r.lat ?? 0,
        lon: r.lon ?? 0,
      }))
    : suggestions;

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
    setError(null);

    if (debounceRef.current) clearTimeout(debounceRef.current);
    abortRef.current?.abort();

    if (value.trim().length < 3) {
      setSuggestions([]);
      setLoading(false);
      setOpen(recentSearches.length > 0);
      return;
    }

    const cacheKey = value.trim().toLowerCase();
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
        const res = await fetch(`/api/geocode?q=${encodeURIComponent(value)}`, {
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
    setSuggestions([]);
    logSearch(s).catch(() => {});
    const params = new URLSearchParams({
      to: s.label,
      address: s.address,
      lat: String(s.lat),
      lon: String(s.lon),
    });
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
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <label className="flex items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3.5 text-text-tertiary">
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
          className="absolute z-30 mt-2 w-full overflow-hidden rounded-2xl border border-border bg-surface shadow-lg"
        >
          {showingRecents && (
            <li className="flex items-center gap-1.5 px-4 pt-3 pb-1 text-[0.7rem] tracking-wide text-text-tertiary uppercase">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 3" />
              </svg>
              Recent
            </li>
          )}
          {items.map((s, i) => (
            <li key={`${s.label}-${s.lat}-${s.lon}`}>
              <button
                type="button"
                onClick={() => select(s)}
                onMouseEnter={() => setActiveIndex(i)}
                className={`flex w-full flex-col items-start gap-0.5 px-4 py-3 text-left transition-colors ${
                  i === activeIndex ? "bg-surface-alt" : ""
                }`}
              >
                <span className="text-sm font-medium text-text">{s.label}</span>
                {s.address && <span className="text-xs text-text-tertiary">{s.address}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
