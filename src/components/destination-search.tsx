"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

type Suggestion = {
  label: string;
  address: string;
  lat: number;
  lon: number;
};

export function DestinationSearch({ initialValue }: { initialValue: string }) {
  const router = useRouter();
  const [query, setQuery] = useState(initialValue);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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

    if (debounceRef.current) clearTimeout(debounceRef.current);
    abortRef.current?.abort();

    if (value.trim().length < 3) {
      setSuggestions([]);
      setOpen(false);
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
        setSuggestions(data.results ?? []);
        setOpen(true);
      } catch (err) {
        if ((err as Error).name !== "AbortError") setSuggestions([]);
      } finally {
        setLoading(false);
      }
    }, 300);
  }

  function select(s: Suggestion) {
    setQuery(s.label);
    setOpen(false);
    setSuggestions([]);
    router.push(`/?to=${encodeURIComponent(s.label)}`);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      select(suggestions[activeIndex]);
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
          onFocus={() => suggestions.length > 0 && setOpen(true)}
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

      {open && suggestions.length > 0 && (
        <ul
          id="destination-suggestions"
          role="listbox"
          className="absolute z-30 mt-2 w-full overflow-hidden rounded-2xl border border-border bg-surface shadow-lg"
        >
          {suggestions.map((s, i) => (
            <li key={`${s.lat}-${s.lon}`}>
              <button
                type="button"
                onClick={() => select(s)}
                onMouseEnter={() => setActiveIndex(i)}
                className={`flex w-full flex-col items-start gap-0.5 px-4 py-3 text-left transition-colors ${
                  i === activeIndex ? "bg-surface-alt" : ""
                }`}
              >
                <span className="text-sm font-medium text-text">{s.label}</span>
                <span className="text-xs text-text-tertiary">{s.address}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
