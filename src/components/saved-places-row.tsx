"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { deleteSavedPlace, listSavedPlaces, savePlace, type SavedPlace } from "@/lib/actions/places";
import { useToast } from "@/components/toast-provider";

type Current = {
  label: string;
  address?: string;
  lat?: number;
  lon?: number;
} | null;

function placeHref(p: { label: string; address?: string | null; lat?: number | null; lon?: number | null }) {
  const params = new URLSearchParams({ to: p.label });
  if (p.address) params.set("address", p.address);
  if (p.lat != null) params.set("lat", String(p.lat));
  if (p.lon != null) params.set("lon", String(p.lon));
  return `/?${params.toString()}`;
}

function SlotChip({
  label,
  place,
  kind,
  current,
  isPending,
  onNavigate,
  onSave,
  onRemove,
}: {
  label: string;
  place?: SavedPlace;
  kind: "home" | "work";
  current: Current;
  isPending: boolean;
  onNavigate: (href: string) => void;
  onSave: (kind: "home" | "work") => void;
  onRemove: (id: string, label: string) => void;
}) {
  if (place) {
    return (
      <span className="group flex shrink-0 items-center whitespace-nowrap rounded-full border border-border bg-surface-alt text-[0.82rem] text-text-secondary">
        <button
          type="button"
          onClick={() => onNavigate(placeHref(place))}
          disabled={isPending}
          title={place.label}
          className="py-2 pl-3.5 pr-1 disabled:opacity-60"
        >
          {label}
        </button>
        <button
          type="button"
          onClick={() => onRemove(place.id, label)}
          disabled={isPending}
          aria-label={`Remove ${label}`}
          className="px-2 py-2 text-text-tertiary opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-3 w-3">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSave(kind)}
      disabled={!current || isPending}
      title={current ? `Save ${current.label} as ${label}` : `Search a place first`}
      className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-border px-3.5 py-2 text-[0.82rem] text-text-tertiary disabled:opacity-50"
    >
      + {label}
    </button>
  );
}

// Fetched client-side (rather than passed down from an awaited server
// call) so a fresh destination search never has to wait on this — it used
// to block the whole plan page's render, which combined with the app's
// generic route-level loading.tsx meant the entire page (search box
// included) flashed to a bare spinner on every search instead of each
// section showing its own loading state independently.
export function SavedPlacesRow({
  current,
  signedIn,
}: {
  current: Current;
  signedIn: boolean;
}) {
  const router = useRouter();
  const showToast = useToast();
  const [isPending, startTransition] = useTransition();
  const [places, setPlaces] = useState<SavedPlace[] | null>(null);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    listSavedPlaces().then((result) => {
      if (!cancelled) setPlaces(result);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  function refreshPlaces() {
    listSavedPlaces().then(setPlaces);
  }

  if (!signedIn) {
    return (
      <Link
        href="/login"
        className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-border px-3.5 py-2 text-[0.82rem] text-text-tertiary transition-colors hover:border-text-tertiary hover:text-text-secondary"
      >
        Sign in to save home, work, and favourite places
      </Link>
    );
  }

  if (places === null) {
    return (
      <div className="flex gap-2">
        <div className="h-9 w-20 animate-pulse rounded-full bg-surface-alt" />
        <div className="h-9 w-20 animate-pulse rounded-full bg-surface-alt" />
      </div>
    );
  }

  const home = places.find((p) => p.kind === "home");
  const work = places.find((p) => p.kind === "work");
  const favorites = places.filter((p) => p.kind === "favorite");

  // Rounded to ~11m precision rather than requiring exact equality --
  // re-searching the same address can legitimately return marginally
  // different coordinates from the geocoder, which previously made
  // "+ Save this place" reappear for an address already saved and invited a
  // near-duplicate favorite.
  const currentAlreadySaved =
    current &&
    current.lat != null &&
    current.lon != null &&
    places.some(
      (p) =>
        p.lat != null &&
        p.lon != null &&
        p.lat.toFixed(4) === current.lat!.toFixed(4) &&
        p.lon.toFixed(4) === current.lon!.toFixed(4) &&
        p.label === current.label
    );

  const kindLabel = { home: "Home", work: "Work", favorite: "favorites" } as const;

  function save(kind: "home" | "work" | "favorite") {
    if (!current) return;
    startTransition(async () => {
      const result = await savePlace({
        kind,
        label: current.label,
        address: current.address,
        lat: current.lat,
        lon: current.lon,
      });
      if (result.error) {
        showToast(result.error, "error");
      } else {
        showToast(
          kind === "favorite"
            ? `Added ${current.label} to favorites`
            : `Saved ${current.label} as ${kindLabel[kind]}`
        );
        refreshPlaces();
      }
    });
  }

  function remove(id: string, label: string) {
    startTransition(async () => {
      const result = await deleteSavedPlace(id);
      if (result.error) {
        showToast(result.error, "error");
      } else {
        showToast(`Removed ${label} from saved places`);
        refreshPlaces();
      }
    });
  }

  return (
    <div className="-mx-5 flex gap-2 overflow-x-auto px-5 sm:-mx-8 sm:px-8 lg:mx-0 lg:flex-wrap lg:px-0">
      <SlotChip
        label="Home"
        place={home}
        kind="home"
        current={current}
        isPending={isPending}
        onNavigate={router.push}
        onSave={save}
        onRemove={remove}
      />
      <SlotChip
        label="Work"
        place={work}
        kind="work"
        current={current}
        isPending={isPending}
        onNavigate={router.push}
        onSave={save}
        onRemove={remove}
      />

      {favorites.map((f) => (
        <span
          key={f.id}
          className="group flex shrink-0 items-center whitespace-nowrap rounded-full border border-border bg-surface-alt text-[0.82rem] text-text-secondary"
        >
          <button
            type="button"
            onClick={() => router.push(placeHref(f))}
            disabled={isPending}
            className="py-2 pl-3.5 pr-1 disabled:opacity-60"
          >
            {f.label}
          </button>
          <button
            type="button"
            onClick={() => remove(f.id, f.label)}
            disabled={isPending}
            aria-label={`Remove ${f.label} from saved places`}
            className="px-2 py-2 text-text-tertiary opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-3 w-3">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </span>
      ))}

      {current && !currentAlreadySaved && (
        <button
          type="button"
          onClick={() => save("favorite")}
          disabled={isPending}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-border px-3.5 py-2 text-[0.82rem] text-text-tertiary disabled:opacity-50"
        >
          + Save this place
        </button>
      )}
    </div>
  );
}
