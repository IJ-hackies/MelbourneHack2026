import { redirect } from "next/navigation";
import { WalkRow } from "@/components/walk-row";
import { createClient } from "@/lib/supabase/server";
import { co2Comparison } from "@/lib/co2-comparisons";

const levelClass = [
  "bg-surface-sunk",
  "bg-primary-soft",
  "bg-primary/55",
  "bg-primary",
];

type Walk = {
  id: string;
  destination: string;
  minutes: number;
  distance_km: number;
  emissions_kg: number;
  completed_at: string;
};

function dayKey(iso: string) {
  return iso.slice(0, 10);
}

function formatWalkDate(iso: string) {
  const date = new Date(iso);
  const now = new Date();
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round(
    (startOfDay(now).getTime() - startOfDay(date).getTime()) / 86_400_000
  );

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString("en-AU", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export default async function History() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data } = await supabase
    .from("walks")
    .select("id, destination, minutes, distance_km, emissions_kg, completed_at")
    .eq("user_id", user.id)
    .order("completed_at", { ascending: false })
    .limit(200);

  const walks: Walk[] = data ?? [];

  const now = new Date();
  const walksThisMonth = walks.filter((w) => {
    const d = new Date(w.completed_at);
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  }).length;

  const totalEmissions = walks.reduce((sum, w) => sum + Number(w.emissions_kg), 0);
  // Recomputed on every render of this server page, so it's a different,
  // still-accurate comparison each time someone checks their total rather
  // than the same fixed "km not driven" line every visit.
  const comparison = co2Comparison(totalEmissions);

  const countByDay = new Map<string, number>();
  for (const w of walks) {
    const key = dayKey(w.completed_at);
    countByDay.set(key, (countByDay.get(key) ?? 0) + 1);
  }

  const days: { key: string; level: number; dateLabel: string }[] = [];
  for (let i = 27; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const count = countByDay.get(key) ?? 0;
    const dateLabel = d.toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short" });
    days.push({ key, level: Math.min(count, 3), dateLabel });
  }

  const recentWalks = walks.slice(0, 8);

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-8 px-5 py-8 sm:px-8 lg:max-w-5xl lg:py-12">
      <div>
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
          Your walking history
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {walksThisMonth} walk{walksThisMonth === 1 ? "" : "s"} this month
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr] lg:items-start">
        <div className="rounded-2xl bg-primary p-[18px] text-surface">
          <div className="text-[0.76rem] tracking-wide text-surface/85 uppercase">
            Estimated avoided emissions
          </div>
          <div className="mt-1 font-display text-[2rem] font-semibold tracking-tight">
            {totalEmissions.toFixed(1)} kg CO₂e
          </div>
          <p className="mt-1.5 text-[0.75rem] text-surface/80">
            Compared to an equivalent car trip. It&apos;s an estimate, not a
            guarantee you would have driven.
          </p>
          {comparison && (
            <p className="mt-1.5 text-[0.75rem] font-medium text-surface/95">
              That&apos;s about the same as {comparison}.
            </p>
          )}
        </div>

        <div className="rounded-2xl border border-border bg-surface p-[18px]">
          <h2 className="font-display text-base font-semibold tracking-tight text-text">
            Activity
          </h2>
          <div
            role="img"
            aria-label={`Walk activity over the last 28 days: ${days.filter((d) => d.level > 0).length} days with at least one walk.`}
            className="mt-3 grid grid-cols-[repeat(14,minmax(0,1fr))] gap-1"
          >
            {days.map((day) => {
              const label =
                day.level === 0
                  ? `${day.dateLabel}: no walk`
                  : `${day.dateLabel}: ${day.level} walk${day.level > 1 ? "s" : ""}`;
              return (
                <div
                  key={day.key}
                  className={`aspect-square rounded-[3px] ${levelClass[day.level]}`}
                  title={label}
                  aria-hidden="true"
                />
              );
            })}
          </div>
          {/* A hover-only title isn't reachable on touch or reliably
              announced by screen readers — the grid's own aria-label above
              covers that; this legend makes the shade scale itself legible
              without relying on color perception alone. */}
          <div className="mt-3 flex items-center justify-end gap-1.5 text-[0.68rem] text-text-tertiary">
            <span>Less</span>
            {levelClass.map((cls, i) => (
              <span key={i} className={`h-2.5 w-2.5 rounded-[2px] ${cls}`} aria-hidden="true" />
            ))}
            <span>More</span>
          </div>
        </div>
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-text">
          Recent walks
        </h2>
        {recentWalks.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-text-tertiary">
            No walks yet. Finish a route from the Plan tab and it&apos;ll show up here.
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-2.5 lg:grid-cols-2">
            {recentWalks.map((walk) => (
              <WalkRow
                key={walk.id}
                id={walk.id}
                destination={walk.destination}
                minutes={walk.minutes}
                distanceKm={walk.distance_km}
                dateLabel={formatWalkDate(walk.completed_at)}
              />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
