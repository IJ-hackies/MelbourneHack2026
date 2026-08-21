import Link from "next/link";
import { ConditionIcon } from "@/components/condition-icon";
import { conditions, departure, destination, routeOptions } from "@/lib/routes";

export default function Home() {
  const top = routeOptions.find((r) => r.recommended) ?? routeOptions[0];

  return (
    <main className="mx-auto grid max-w-xl grid-cols-1 gap-8 px-5 py-8 sm:px-8 lg:max-w-5xl lg:grid-cols-[360px_1fr] lg:items-start lg:gap-12 lg:py-12">
      <div className="flex flex-col gap-8 lg:sticky lg:top-24">
        <div>
          <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
            Where to?
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Sensible defaults, personalise anytime.
          </p>
        </div>

        <label className="flex items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-3.5 text-text-tertiary">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            className="h-[17px] w-[17px] shrink-0"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" />
          </svg>
          <input
            type="text"
            defaultValue={destination}
            className="w-full bg-transparent text-[0.95rem] text-text placeholder:text-text-tertiary focus:outline-none"
          />
        </label>

        <div className="-mx-5 flex gap-2 overflow-x-auto px-5 sm:-mx-8 sm:px-8 lg:mx-0 lg:flex-wrap lg:px-0">
          {["Home", "Work", "Saved"].map((label) => (
            <button
              key={label}
              type="button"
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface-alt px-3.5 py-2 text-[0.82rem] text-text-secondary"
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-2 lg:grid-cols-1 lg:gap-2.5">
          {conditions.map((c) => (
            <div
              key={c.label}
              className="rounded-2xl border border-border bg-surface px-2.5 py-3 text-center lg:flex lg:items-center lg:gap-3 lg:px-4 lg:py-3 lg:text-left"
            >
              <ConditionIcon
                tone={c.tone}
                className="mx-auto mb-1.5 h-[18px] w-[18px] lg:mx-0 lg:mb-0"
              />
              <div className="lg:flex lg:flex-1 lg:items-baseline lg:justify-between">
                <div className="font-display text-[1.05rem] font-semibold text-text">
                  {c.value}
                </div>
                <div className="mt-0.5 text-[0.68rem] tracking-wide text-text-tertiary uppercase lg:mt-0">
                  {c.label}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-8">
        <div>
          <h2 className="font-display text-lg font-semibold tracking-tight text-text lg:text-xl">
            3 ways to {destination}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">{departure}</p>

          <div className="mt-4 flex flex-col gap-2.5 lg:grid lg:grid-cols-2 lg:items-start">
            {routeOptions.map((route) => (
              <Link
                key={route.id}
                href={`/route/${route.id}`}
                className={`block rounded-2xl border p-4 transition-colors ${
                  route.recommended
                    ? "border-primary bg-primary-soft"
                    : "border-border bg-surface hover:border-text-tertiary"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="font-display text-[1.35rem] font-semibold tracking-tight text-text">
                    {route.minutes}
                    <span className="ml-0.5 font-sans text-[0.7rem] font-medium text-text-tertiary">
                      min
                    </span>
                  </div>
                  {route.recommended && (
                    <span className="shrink-0 rounded-full bg-primary px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide whitespace-nowrap text-surface uppercase">
                      Comfort pick
                    </span>
                  )}
                </div>
                <p className="mt-1.5 text-[0.84rem] text-text-secondary">
                  {route.description}
                </p>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {route.tags.map((tag) => (
                    <span
                      key={tag.label}
                      className={`rounded-lg px-2 py-1 text-[0.72rem] ${
                        tag.tone === "warm"
                          ? "bg-heat-soft text-heat"
                          : "bg-surface-alt text-text-secondary"
                      }`}
                    >
                      {tag.label}
                    </span>
                  ))}
                </div>
              </Link>
            ))}
          </div>
        </div>

        <Link
          href={`/route/${top.id}`}
          className="rounded-2xl bg-primary py-3.5 text-center font-semibold text-surface shadow-[0_10px_22px_-12px_hsl(160_30%_15%/0.45)] lg:max-w-sm"
        >
          Start walking — {top.minutes} min pick
        </Link>
      </div>
    </main>
  );
}
