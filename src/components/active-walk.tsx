"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { logWalk } from "@/lib/actions/walks";
import { useLiveProgress } from "@/lib/live-progress-context";

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
  const [status, setStatus] = useState<"idle" | "walking" | "done">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [saveError, setSaveError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [isSaving, startSaving] = useTransition();
  const { progress } = useLiveProgress();

  useEffect(() => {
    if (status !== "walking") return;
    intervalRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [status]);

  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  const emissionsKg = (distanceKm * 0.19).toFixed(2);

  function endWalk() {
    setStatus("done");
    if (!signedIn) return;
    startSaving(async () => {
      const result = await logWalk({ routeId, destination, minutes, distanceKm });
      if (result.error) setSaveError(result.error);
    });
  }

  if (status === "done") {
    return (
      <div className="rounded-2xl bg-primary p-5 text-surface">
        <div className="text-[0.76rem] tracking-wide text-surface/85 uppercase">
          Walk complete
        </div>
        <div className="mt-1 font-display text-[1.7rem] font-semibold tracking-tight">
          {mm}:{ss}
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

  if (status === "walking") {
    return (
      <div className="rounded-2xl border border-primary bg-primary-soft p-5">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[0.76rem] tracking-wide text-text-secondary uppercase">
              Walking
            </div>
            <div className="mt-1 font-display text-[1.7rem] font-semibold tracking-tight text-text">
              {mm}:{ss}
            </div>
          </div>
          <span className="flex h-2.5 w-2.5 rounded-full bg-primary" aria-hidden="true" />
        </div>
        {progress && (
          <p className="mt-2 text-xs text-text-secondary">
            {progress.distanceRemainingKm} km remaining · ~{progress.etaMinutes} min, based on
            your live location
          </p>
        )}
        <button
          type="button"
          onClick={endWalk}
          className="mt-4 w-full rounded-xl border border-primary py-3 text-sm font-semibold text-primary-strong"
        >
          End walk
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setStatus("walking")}
      className="w-full rounded-2xl bg-primary py-3.5 text-center font-semibold text-surface shadow-[0_10px_22px_-12px_hsl(160_30%_15%/0.45)]"
    >
      Start walking, {minutes} min
    </button>
  );
}
