import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

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

  const countByDay = new Map<string, number>();
  for (const w of walks) {
    const key = dayKey(w.completed_at);
    countByDay.set(key, (countByDay.get(key) ?? 0) + 1);
  }

  const days: { key: string; level: number }[] = [];
  for (let i = 27; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const count = countByDay.get(key) ?? 0;
    days.push({ key, level: Math.min(count, 3) });
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
            Vs. an equivalent car trip — illustrative, not a guarantee you would
            have driven.
          </p>
        </div>

        <div className="rounded-2xl border border-border bg-surface p-[18px]">
          <h2 className="font-display text-base font-semibold tracking-tight text-text">
            Activity
          </h2>
          <div className="mt-3 grid grid-cols-[repeat(14,minmax(0,1fr))] gap-1">
            {days.map((day) => (
              <div
                key={day.key}
                className={`aspect-square rounded-[3px] ${levelClass[day.level]}`}
                title={
                  day.level === 0 ? "No walk" : `${day.level} walk${day.level > 1 ? "s" : ""}`
                }
              />
            ))}
          </div>
        </div>
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-text">
          Recent walks
        </h2>
        {recentWalks.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-text-tertiary">
            No walks yet — finish a route from the Plan tab and it&apos;ll show up here.
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-2.5 lg:grid-cols-2">
            {recentWalks.map((walk) => (
              <div
                key={walk.id}
                className="flex items-center justify-between rounded-2xl border border-border bg-surface px-4 py-3.5"
              >
                <div>
                  <div className="text-sm font-medium text-text">{walk.destination}</div>
                  <div className="mt-0.5 text-[0.78rem] text-text-tertiary">
                    {formatWalkDate(walk.completed_at)} · {walk.distance_km} km
                  </div>
                </div>
                <div className="font-display text-sm font-semibold text-text">
                  {walk.minutes} min
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
