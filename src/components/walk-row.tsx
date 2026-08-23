"use client";

import { useTransition } from "react";
import Link from "next/link";
import { deleteWalk } from "@/lib/actions/walks";
import { useToast } from "@/components/toast-provider";

export function WalkRow({
  id,
  destination,
  minutes,
  distanceKm,
  dateLabel,
}: {
  id: string;
  destination: string;
  minutes: number;
  distanceKm: number;
  dateLabel: string;
}) {
  const [isPending, startTransition] = useTransition();
  const showToast = useToast();

  return (
    <div className="group flex items-center justify-between rounded-2xl border border-border bg-surface px-4 py-3.5">
      <div>
        <div className="text-sm font-medium text-text">{destination}</div>
        <div className="mt-0.5 text-[0.78rem] text-text-tertiary">
          {dateLabel} · {distanceKm} km
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Link
          href={`/?to=${encodeURIComponent(destination)}`}
          className="hidden text-[0.78rem] font-medium text-primary sm:inline hover:underline"
        >
          Walk again
        </Link>
        <div className="font-display text-sm font-semibold text-text">{minutes} min</div>
        {/* Always visible, not hover-gated — a hover-only reveal never
            appears at all on touch devices, which made this effectively
            unreachable on mobile. */}
        <button
          type="button"
          onClick={() =>
            startTransition(async () => {
              const result = await deleteWalk(id);
              if (result.error) {
                showToast(result.error, "error");
              } else {
                showToast(`Removed ${destination} from your history`);
              }
            })
          }
          disabled={isPending}
          aria-label={`Remove ${destination} from your history`}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-tertiary transition-colors hover:bg-surface-alt hover:text-heat disabled:opacity-50"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z" />
          </svg>
        </button>
      </div>
    </div>
  );
}
