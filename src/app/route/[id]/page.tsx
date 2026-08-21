import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ActiveWalk } from "@/components/active-walk";
import { ConditionIcon } from "@/components/condition-icon";
import { routeProvider } from "@/lib/providers/route-provider";

export default async function RouteDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ to?: string }>;
}) {
  const { id } = await params;
  const { to } = await searchParams;
  const destination = to?.trim();
  if (!destination) redirect("/");
  const route = await routeProvider.getRoute(id, { label: destination });
  if (!route) notFound();

  return (
    <main className="mx-auto grid max-w-xl grid-cols-1 gap-6 px-5 py-8 sm:px-8 lg:max-w-5xl lg:grid-cols-[1fr_340px] lg:items-start lg:gap-12 lg:py-12">
      <div className="flex flex-col gap-6 lg:col-span-2">
        <Link
          href={`/?to=${encodeURIComponent(destination)}`}
          className="flex items-center gap-1.5 text-sm text-text-secondary hover:text-text"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="m15 18-6-6 6-6" />
          </svg>
          All routes to {destination}
        </Link>

        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
              {route.minutes} min
            </h1>
            {route.recommended && (
              <span className="rounded-full bg-primary px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide text-surface uppercase">
                Comfort pick
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-text-secondary">
            {route.distanceKm} km to {destination}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-6">
        <div
          className="relative flex h-48 items-center justify-center overflow-hidden rounded-2xl border border-border bg-surface-alt lg:h-80"
          aria-hidden="true"
        >
          <svg viewBox="0 0 320 160" className="h-full w-full">
            <path
              d="M20 130 C 80 130, 70 40, 140 40 S 220 110, 260 90 S 300 40, 300 30"
              fill="none"
              stroke="var(--primary)"
              strokeWidth="3.5"
              strokeLinecap="round"
              strokeDasharray="1 12"
            />
            <circle cx="20" cy="130" r="6" fill="var(--surface)" stroke="var(--primary)" strokeWidth="3" />
            <circle cx="300" cy="30" r="6" fill="var(--primary)" />
          </svg>
          <span className="absolute bottom-3 left-3 rounded-lg bg-surface/90 px-2.5 py-1 text-[0.72rem] text-text-tertiary">
            Map preview, routing data not yet wired up
          </span>
        </div>

        <p className="text-sm text-text-secondary">{route.description}</p>
      </div>

      <div className="flex flex-col gap-6 lg:sticky lg:top-24">
        <div>
          <h2 className="font-display text-base font-semibold tracking-tight text-text">
            Conditions along this route
          </h2>
          <div className="mt-3 flex flex-col gap-3">
            {route.segments.map((seg) => (
              <div key={seg.label}>
                <div className="mb-1.5 flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5 text-text-secondary">
                    <ConditionIcon tone={seg.tone} className="h-3.5 w-3.5" />
                    {seg.label}
                  </span>
                  <span className="font-mono text-[0.76rem] text-text-tertiary">
                    {seg.share}%
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-surface-sunk">
                  <div
                    className={`h-full rounded-full ${
                      seg.tone === "primary"
                        ? "bg-primary"
                        : seg.tone === "heat"
                          ? "bg-heat"
                          : seg.tone === "crowd"
                            ? "bg-crowd"
                            : "bg-traffic"
                    }`}
                    style={{ width: `${seg.share}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <ActiveWalk
          routeId={route.id}
          destination={destination}
          minutes={route.minutes}
          distanceKm={route.distanceKm}
        />
      </div>
    </main>
  );
}
