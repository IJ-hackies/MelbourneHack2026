"use client";

import { useEffect, useRef, useTransition, useState } from "react";
import Link from "next/link";
import { logWalk } from "@/lib/actions/walks";
import { useLiveProgress } from "@/lib/live-progress-context";
import { PENDING_WALK_STORAGE_KEY, type PendingWalk } from "@/lib/pending-walk";

// Close enough to the destination to count as arrived — real GPS accuracy
// on a phone is commonly 5-15m in open air, so this has to be a radius,
// not an exact match.
const ARRIVAL_RADIUS_KM = 0.015;

export function ActiveWalk({
  routeId,
  destination,
  minutes,
  distanceKm,
  signedIn,
}: {
  routeId: string;
  destination: string;
  minutes: number;
  distanceKm: number;
  signedIn: boolean;
}) {
  const [manuallyFinished, setManuallyFinished] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, startSaving] = useTransition();
  const { progress } = useLiveProgress();
  const savedRef = useRef(false);

  // Auto-completes once live location puts the user within arrival radius —
  // derived directly from progress rather than mirrored into its own state,
  // so there's nothing to keep in sync. The manual "Finish walk" button
  // stays as a fallback for when GPS is denied/unavailable and this can
  // never become true on its own.
  const arrived = progress ? progress.distanceRemainingKm <= ARRIVAL_RADIUS_KM : false;
  const done = manuallyFinished || arrived;

  const emissionsKg = (distanceKm * 0.19).toFixed(2);

  useEffect(() => {
    if (!done || savedRef.current) return;
    savedRef.current = true;

    if (!signedIn) {
      // Stashed so claim-pending-walk.tsx can save it the moment this
      // browser has a real session — otherwise a guest who signs up right
      // after finishing a walk loses it, with no way to log it retroactively.
      const pending: PendingWalk = { routeId, destination, minutes, distanceKm };
      try {
        localStorage.setItem(PENDING_WALK_STORAGE_KEY, JSON.stringify(pending));
      } catch {
        // Storage unavailable (private browsing, quota) — the walk still
        // completes, it just can't be claimed after signing in.
      }
      return;
    }

    startSaving(async () => {
      const result = await logWalk({ routeId, destination, minutes, distanceKm });
      if (result.error) setSaveError(result.error);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done]);

  if (done) {
    return (
      <div className="rounded-2xl bg-primary p-5 text-surface">
        <div className="text-[0.76rem] tracking-wide text-surface/85 uppercase">
          Walk complete
        </div>
        <div className="mt-3 flex gap-5 text-sm">
          <div>
            <div className="font-display text-base font-semibold">{distanceKm} km</div>
            <div className="text-xs text-surface/80">Distance</div>
          </div>
          <div>
            <div className="font-display text-base font-semibold">{emissionsKg} kg</div>
            <div className="text-xs text-surface/80">Est. CO₂e avoided</div>
          </div>
        </div>
        <p className="mt-3 text-xs text-surface/80">
          {!signedIn ? (
            <>
              Estimated avoided emissions vs. an equivalent car trip.{" "}
              <Link href="/login" className="underline hover:no-underline">
                Sign in
              </Link>{" "}
              and this walk will be added to your history automatically.
            </>
          ) : saveError ? (
            "Couldn't save this walk to your history. It still counts, just not recorded."
          ) : isSaving ? (
            "Saving to your history…"
          ) : (
            "Estimated avoided emissions vs. an equivalent car trip. Saved to your history."
          )}
        </p>
      </div>
    );
  }

  // No manual "start" step and no elapsed-time stopwatch — walking progress
  // is automatic, driven by live location (RouteMap) the same way Google
  // Maps updates a live ETA, not a clock the user has to start themselves.
  return (
    <div className="rounded-2xl border border-primary bg-primary-soft p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[0.76rem] tracking-wide text-text-secondary uppercase">
            Walking to {destination}
          </div>
          <div className="mt-1 font-display text-[1.7rem] font-semibold tracking-tight text-text">
            {progress ? `${progress.etaMinutes} min` : `${minutes} min`}
          </div>
        </div>
        <span className="flex h-2.5 w-2.5 rounded-full bg-primary" aria-hidden="true" />
      </div>
      <p className="mt-2 text-xs text-text-secondary">
        {progress
          ? `${progress.distanceRemainingKm} km remaining, based on your live location`
          : `${distanceKm} km · waiting for your live location to start updating`}
      </p>
      {progress?.nextTurn && (
        <p className="mt-1.5 flex items-center gap-1.5 text-sm font-medium text-text">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`h-4 w-4 shrink-0 ${progress.nextTurn.direction === "left" ? "-scale-x-100" : ""}`}
          >
            <path d="M8 6 3 11l5 5" />
            <path d="M3 11h11a5 5 0 0 1 5 5v3" />
          </svg>
          Turn {progress.nextTurn.direction} in {progress.nextTurn.distanceMetres} m
        </p>
      )}
      <button
        type="button"
        onClick={() => setManuallyFinished(true)}
        className="mt-4 w-full rounded-xl border border-primary py-3 text-sm font-semibold text-primary-strong"
      >
        Finish walk
      </button>
    </div>
  );
}
