import { callCrowdInference } from "@/lib/ml/ml-client";
import type { Coordinates } from "./types";

export type QuietestHour = { label: string; pedestrianFlowPerHour: number } | null;

// Samples the crowd model (which already supports predicting an arbitrary
// target_hour, not just "now" — see ml/crowd/SOFTWARE_HANDOFF.md) across the
// rest of today in 2-hour steps and reports the quietest one near the
// destination. Real per-hour model output, not a fabricated forecast — the
// smallest honest step toward "when should I leave" without needing new
// forecasting infrastructure. Samples are spaced by elapsed time from now
// rather than fixed clock hours (simpler, no DST edge cases), but each
// sample is labelled using its real Melbourne wall-clock hour.
const SAMPLE_COUNT = 6;
const SAMPLE_STEP_HOURS = 2;

export async function getQuietestHourToday(destination: Coordinates): Promise<QuietestHour> {
  const now = new Date();
  const samples = Array.from({ length: SAMPLE_COUNT }, (_, i) => {
    const targetHour = new Date(now.getTime() + i * SAMPLE_STEP_HOURS * 3600_000);
    return { targetHour };
  });

  const results = await Promise.all(
    samples.map(async ({ targetHour }) => {
      const signal = await callCrowdInference(destination, targetHour);
      return { targetHour, signal };
    })
  );

  const usable = results.filter((r) => r.signal.qualityStatus !== "unavailable");
  if (usable.length < 2) return null;

  const quietest = usable.reduce((min, r) =>
    r.signal.pedestrianFlowPerHour < min.signal.pedestrianFlowPerHour ? r : min
  );
  // Only worth surfacing if it's a meaningfully quieter window, not noise
  // between two samples that round to the same value.
  const busiest = usable.reduce((max, r) =>
    r.signal.pedestrianFlowPerHour > max.signal.pedestrianFlowPerHour ? r : max
  );
  if (busiest.signal.pedestrianFlowPerHour <= 0) return null;
  const spread =
    (busiest.signal.pedestrianFlowPerHour - quietest.signal.pedestrianFlowPerHour) /
    busiest.signal.pedestrianFlowPerHour;
  if (spread < 0.15 || quietest.targetHour.getTime() === now.getTime()) return null;

  const label = quietest.targetHour.toLocaleTimeString("en-AU", {
    timeZone: "Australia/Melbourne",
    hour: "numeric",
    minute: quietest.targetHour.getMinutes() === 0 ? undefined : "2-digit",
    hour12: true,
  });

  return { label, pedestrianFlowPerHour: quietest.signal.pedestrianFlowPerHour };
}
