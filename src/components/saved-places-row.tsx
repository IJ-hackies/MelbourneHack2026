"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { deleteSavedPlace, savePlace, type SavedPlace } from "@/lib/actions/places";
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
}: {
  label: string;
  place?: SavedPlace;
  kind: "home" | "work";
  current: Current;
  isPending: boolean;
  onNavigate: (href: string) => void;
  onSave: (kind: "home" | "work") => void;
}) {
  if (place) {
    return (
      <button
        type="button"
        onClick={() => onNavigate(placeHref(place))}
        disabled={isPending}
        className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface-alt px-3.5 py-2 text-[0.82rem] text-text-secondary disabled:opacity-60"
      >
        {label}
      </button>
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

export function SavedPlacesRow({
  places,
  current,
}: {
  places: SavedPlace[];
  current: Current;
}) {
  const router = useRouter();
  const showToast = useToast();
  const [isPending, startTransition] = useTransition();

  const home = places.find((p) => p.kind === "home");
  const work = places.find((p) => p.kind === "work");
  const favorites = places.filter((p) => p.kind === "favorite");

  const currentAlreadySaved =
    current &&
    places.some(
      (p) => p.lat === current.lat && p.lon === current.lon && p.label === current.label
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
      }
      router.refresh();
    });
  }

  function remove(id: string, label: string) {
    startTransition(async () => {
      const result = await deleteSavedPlace(id);
      if (result.error) {
        showToast(result.error, "error");
      } else {
        showToast(`Removed ${label} from saved places`);
      }
      router.refresh();
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
      />
      <SlotChip
        label="Work"
        place={work}
        kind="work"
        current={current}
        isPending={isPending}
        onNavigate={router.push}
        onSave={save}
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
