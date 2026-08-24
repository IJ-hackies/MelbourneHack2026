"use client";

import { useEffect, useMemo, useRef, useTransition, useState } from "react";
import Link from "next/link";
import { logWalk } from "@/lib/actions/walks";
import { useLiveProgress } from "@/lib/live-progress-context";
import { PENDING_WALK_STORAGE_KEY, type PendingWalk } from "@/lib/pending-walk";
import { co2Comparison } from "@/lib/co2-comparisons";

// Close enough to the destination to count as arrived — real GPS accuracy
// on a phone is commonly 5-15m in open air, so this has to be a radius,
// not an exact match.
const ARRIVAL_RADIUS_KM = 0.015;
// A single GPS fix landing inside the radius isn't reliable enough to
// permanently mark the walk done — urban-canyon multipath near tall
// buildings can produce one wildly wrong reading. Require it to hold for a
// few consecutive live updates before it's treated as real arrival.
const ARRIVAL_CONFIRM_TICKS = 3;

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
  const [collapsed, setCollapsed] = useState(false);
  const { progress } = useLiveProgress();
  const savedRef = useRef(false);
  const arrivalStreakRef = useRef(0);
  const [confirmedArrival, setConfirmedArrival] = useState(false);
  const [pendingStashFailed, setPendingStashFailed] = useState(false);

  const withinArrivalRadius = progress ? progress.distanceRemainingKm <= ARRIVAL_RADIUS_KM : false;
  useEffect(() => {
    if (!progress) return;
    arrivalStreakRef.current = withinArrivalRadius ? arrivalStreakRef.current + 1 : 0;
    if (arrivalStreakRef.current >= ARRIVAL_CONFIRM_TICKS) setConfirmedArrival(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress?.distanceRemainingKm]);

  // Auto-completes once live location has held the user inside the arrival
  // radius for several consecutive updates (see ARRIVAL_CONFIRM_TICKS) — the
  // manual "Finish walk" button stays as a fallback for when GPS is
  // denied/unavailable and this can never become true on its own.
  const done = manuallyFinished || confirmedArrival;

  const emissionsKg = (distanceKm * 0.19).toFixed(2);
  // Memoized on the number itself (not recomputed on every re-render, e.g.
  // when saveError changes) so the comparison shown for this walk stays put
  // once it's picked, rather than jumping to a different one mid-view.
  const comparison = useMemo(() => co2Comparison(distanceKm * 0.19), [distanceKm]);
  const percentComplete = progress
    ? Math.min(100, Math.max(0, Math.round((1 - progress.distanceRemainingKm / Math.max(distanceKm, 0.001)) * 100)))
    : 0;

  function saveWalk() {
    startSaving(async () => {
      setSaveError(null);
      const result = await logWalk({ routeId, destination, minutes, distanceKm });
      if (result.error) setSaveError(result.error);
    });
  }

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
        // completes, it just can't be claimed after signing in, so say so
        // instead of making a promise this browser can't keep. Deferred a
        // tick so this effect doesn't call setState synchronously within
        // itself (react-hooks/set-state-in-effect).
        queueMicrotask(() => setPendingStashFailed(true));
      }
      return;
    }

    saveWalk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done]);

  if (done) {
    return (
      <div className="leafroute-arrive rounded-2xl bg-primary p-5 text-surface">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface/20">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          </span>
          <div className="text-[0.76rem] tracking-wide text-surface/85 uppercase">
            Walk complete
          </div>
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
        {comparison && (
          <p className="mt-2.5 text-[0.82rem] font-medium text-surface/95">
            That&apos;s about the same as {comparison}.
          </p>
        )}
        <p className="mt-3 text-xs text-surface/80">
          {!signedIn && pendingStashFailed ? (
            <>
              Estimated avoided emissions vs. an equivalent car trip. This browser
              couldn&apos;t hold onto this walk to add it to your history later (private
              browsing or storage is blocked here) — it still counts, just not recorded.
            </>
          ) : !signedIn ? (
            <>
              Estimated avoided emissions vs. an equivalent car trip.{" "}
              <Link href="/login" className="underline hover:no-underline">
                Sign in
              </Link>{" "}
              and this walk will be added to your history automatically.
            </>
          ) : saveError ? (
            <>
              Couldn&apos;t save this walk to your history. It still counts, just not recorded.{" "}
              <button
                type="button"
                onClick={saveWalk}
                disabled={isSaving}
                className="underline hover:no-underline disabled:opacity-60"
              >
                Try again
              </button>
            </>
          ) : isSaving ? (
            "Saving to your history…"
          ) : (
            "Estimated avoided emissions vs. an equivalent car trip. Saved to your history."
          )}
        </p>
      </div>
    );
  }

  // Collapsing only makes sense once there's something live to summarise —
  // maximises map space during active navigation, the way a real nav app
  // shrinks its trip card to a strip once you're underway, while keeping
  // the map itself (and its own controls) untouched.
  if (collapsed && progress) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label="Expand walk details"
        className="flex w-full items-center gap-3 rounded-2xl border border-primary bg-primary-soft px-4 py-3 text-left"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-surface">
          {progress.nextTurn ? (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-4 w-4 ${progress.nextTurn.direction === "left" ? "-scale-x-100" : ""}`}
            >
              <path d="M8 6 3 11l5 5" />
              <path d="M3 11h11a5 5 0 0 1 5 5v3" />
            </svg>
          ) : (
            <span className="h-2.5 w-2.5 rounded-full bg-surface" aria-hidden="true" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-display text-sm font-semibold text-text">
            {progress.nextTurn
              ? `Turn ${progress.nextTurn.direction} in ${progress.nextTurn.distanceMetres} m`
              : `${progress.etaMinutes} min to ${destination}`}
          </span>
          <span className="block text-[0.72rem] text-text-secondary">
            {progress.distanceRemainingKm} km remaining
          </span>
        </span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0 text-text-tertiary">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
    );
  }

  // No manual "start" step and no elapsed-time stopwatch — walking progress
  // is automatic, driven by live location (RouteMap) the same way Google
  // Maps updates a live ETA, not a clock the user has to start themselves.
  return (
    <div className={`relative rounded-2xl border border-primary bg-primary-soft p-5 ${progress ? "pr-12" : ""}`}>
      {progress && (
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="Collapse walk details"
          className="absolute top-3 right-3 flex h-8 w-8 items-center justify-center rounded-full text-text-tertiary transition-colors hover:bg-surface/40 hover:text-text"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="m18 15-6-6-6 6" />
          </svg>
        </button>
      )}
      {/* Next turn is the dominant element once GPS tracking is live — real
          nav apps lead with "what do I do next", not the ETA. */}
      {progress?.nextTurn ? (
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary text-surface">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-5 w-5 ${progress.nextTurn.direction === "left" ? "-scale-x-100" : ""}`}
            >
              <path d="M8 6 3 11l5 5" />
              <path d="M3 11h11a5 5 0 0 1 5 5v3" />
            </svg>
          </span>
          <div>
            <div className="font-display text-lg font-semibold tracking-tight text-text capitalize">
              Turn {progress.nextTurn.direction} in {progress.nextTurn.distanceMetres} m
            </div>
            <div className="text-[0.76rem] text-text-secondary">
              then continue toward {destination}
            </div>
          </div>
        </div>
      ) : (
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
      )}

      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-sunk">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${percentComplete}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-text-secondary">
        {progress
          ? `${progress.distanceRemainingKm} km remaining · ${progress.etaMinutes} min, based on average walking pace`
          : `${distanceKm} km · waiting for your live location to start updating`}
      </p>

      {progress && progress.distanceWalkedKm !== undefined && progress.distanceWalkedKm > 0 && (
        <div className="mt-3 flex gap-5 border-t border-primary/25 pt-3 text-sm">
          <div>
            <div className="font-display text-base font-semibold text-text">
              {/* Defensively rounded here too, not just at the producer
                  (route-map.tsx) — a display value should never trust an
                  upstream rounding step it can't see. */}
              {(Math.round(progress.distanceWalkedKm * 100) / 100).toFixed(2)} km
            </div>
            <div className="text-xs text-text-tertiary">Walked so far</div>
          </div>
          <div>
            <div className="font-display text-base font-semibold text-text">
              {(Math.round((progress.emissionsSavedKg ?? 0) * 100) / 100).toFixed(2)} kg
            </div>
            <div className="text-xs text-text-tertiary">CO₂e saved so far</div>
          </div>
        </div>
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
