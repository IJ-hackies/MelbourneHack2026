"use client";

import { useEffect, useRef, useTransition, useState } from "react";
import Link from "next/link";
import { logWalk } from "@/lib/actions/walks";
import { useLiveProgress } from "@/lib/live-progress-context";

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
    if (!done || savedRef.current || !signedIn) return;
    savedRef.current = true;
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
              to save this to your history.
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
