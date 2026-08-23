import { callCrowdInference } from "@/lib/ml/ml-client";
import type { Coordinates } from "./types";

export type QuietestHour = { label: string; pedestrianFlowPerHour: number } | null;

// Samples the crowd model (which already supports predicting an arbitrary
// target_hour, not just "now" — see ml/crowd/SOFTWARE_HANDOFF.md) in 2-hour
// steps and reports the quietest one near the destination. Real per-hour
// model output, not a fabricated forecast — the smallest honest step toward
// "when should I leave" without needing new forecasting infrastructure.
//
// Samples are capped to a same-day walking window (now through 9pm
// Melbourne time) — earlier versions spaced samples by raw elapsed time
// from "now" with no such cap, so a late-evening search could genuinely
// recommend "quietest around 5:42am", which isn't an actionable suggestion
// for someone deciding whether to walk right now. If there's no useful
// window left today, this returns null rather than suggesting a time
// nobody would act on.
const SAMPLE_COUNT = 6;
const SAMPLE_STEP_HOURS = 2;
const WALKING_WINDOW_END_HOUR = 21; // 9pm Melbourne time

function melbourneHourFraction(date: Date): number {
  const parts = new Intl.DateTimeFormat("en-AU", {
    timeZone: "Australia/Melbourne",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  }).formatToParts(date);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return hour + minute / 60;
}

export async function getQuietestHourToday(destination: Coordinates): Promise<QuietestHour> {
  const now = new Date();
  const hoursLeftInWindow = WALKING_WINDOW_END_HOUR - melbourneHourFraction(now);
  // Less than one real step of daytime left (e.g. already past 9pm) — no
  // useful "quieter later" suggestion exists today.
  if (hoursLeftInWindow < SAMPLE_STEP_HOURS) return null;

  const sampleCount = Math.min(SAMPLE_COUNT, Math.floor(hoursLeftInWindow / SAMPLE_STEP_HOURS) + 1);
  const samples = Array.from({ length: sampleCount }, (_, i) => {
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
