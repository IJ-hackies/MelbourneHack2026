import { PendingLink } from "@/components/pending-link";
import { ConditionIcon } from "@/components/condition-icon";
import { MarketingHeader } from "@/components/marketing/marketing-header";
import { ContactForm } from "@/components/marketing/contact-form";

const FEATURES = [
  {
    tone: "heat" as const,
    title: "Adapts to real heat, live",
    body: "When it's genuinely hot out, LeafRoute leans harder into shade using real tree canopy data, rather than a fixed rule that ignores the forecast.",
  },
  {
    tone: "crowd" as const,
    title: "Reads the crowd",
    body: "Live pedestrian sensor data can steer you around streets that are busy right now, not just around distance.",
  },
  {
    tone: "traffic" as const,
    title: "Quieter, safer streets",
    body: "Tell it to weight traffic lower whenever you'd rather not walk beside six lanes of it.",
  },
  {
    tone: "primary" as const,
    title: "Every walk, counted honestly",
    body: "We measure your real distance and turn it into a transparent emissions estimate that's never inflated and never guessed.",
  },
];

const STEPS = [
  {
    number: "01",
    title: "Search where you're headed",
    body: "Type a street, building, or place anywhere in Melbourne and get real addresses back, not guesswork.",
  },
  {
    number: "02",
    title: "Compare the trade-off",
    body: "See a handful of honest options, from fastest to shadiest to quietest, along with exactly what each one costs you in minutes.",
  },
  {
    number: "03",
    title: "Walk it, track it",
    body: "Start walking with one tap, and your history and estimated avoided emissions build up automatically as you go.",
  },
];

export function MarketingPage({
  appOrigin = "",
  communityImpact,
}: {
  appOrigin?: string;
  communityImpact?: { totalWalks: number; totalEmissionsKg: number; totalDistanceKm: number } | null;
}) {
  return (
    <div>
      <MarketingHeader appOrigin={appOrigin} />

      <main>
        {/* Hero */}
        <section className="relative overflow-hidden px-5 pt-20 pb-24 sm:px-8 sm:pt-28 sm:pb-32 lg:px-12">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] bg-[radial-gradient(60%_50%_at_50%_0%,color-mix(in_srgb,var(--primary)_22%,transparent),transparent)]"
          />
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="font-display text-[2.6rem] leading-[1.05] font-semibold tracking-tight text-text sm:text-[3.4rem] lg:text-[4rem]">
              Every walk instead of a drive
              <br />
              is climate action.
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-[1.05rem] leading-relaxed text-text-secondary sm:text-lg">
              LeafRoute makes walking around Melbourne the easy choice. It cuts
              the emissions a car trip would&apos;ve cost, and routes you through
              shade and cooler streets so rising temperatures don&apos;t make that
              choice any harder than it has to be.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <PendingLink
                href={`${appOrigin}/`}
                newTabOnDesktop={Boolean(appOrigin)}
                className="w-full rounded-full bg-primary px-7 py-3.5 text-center text-[0.95rem] font-semibold text-surface shadow-[0_16px_36px_-16px_color-mix(in_srgb,var(--primary)_70%,transparent)] transition-opacity hover:opacity-90 sm:w-auto"
              >
                Try it now
              </PendingLink>
              <PendingLink
                href={`${appOrigin}/login`}
                newTabOnDesktop={Boolean(appOrigin)}
                className="w-full rounded-full border border-border px-7 py-3.5 text-center text-[0.95rem] font-medium text-text transition-colors hover:bg-surface-alt sm:w-auto"
              >
                Log in
              </PendingLink>
            </div>

            {communityImpact && communityImpact.totalWalks > 0 && (
              <div className="mx-auto mt-10 flex max-w-md flex-wrap items-center justify-center gap-x-8 gap-y-3 rounded-2xl border border-border bg-surface/70 px-6 py-4 backdrop-blur-sm">
                <div>
                  <div className="font-display text-xl font-semibold tracking-tight text-text">
                    {communityImpact.totalEmissionsKg.toFixed(1)}
                    <span className="ml-1 text-[0.8rem] font-medium text-text-secondary">kg CO₂e avoided</span>
                  </div>
                </div>
                <div className="h-8 w-px bg-border" aria-hidden="true" />
                <div>
                  <div className="font-display text-xl font-semibold tracking-tight text-text">
                    {communityImpact.totalDistanceKm.toFixed(1)}
                    <span className="ml-1 text-[0.8rem] font-medium text-text-secondary">km walked</span>
                  </div>
                </div>
                <div className="h-8 w-px bg-border" aria-hidden="true" />
                <div>
                  <div className="font-display text-xl font-semibold tracking-tight text-text">
                    {communityImpact.totalWalks}
                    <span className="ml-1 text-[0.8rem] font-medium text-text-secondary">
                      {communityImpact.totalWalks === 1 ? "walk" : "walks"} logged
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Product showcase */}
          <div className="relative mx-auto mt-20 flex max-w-lg justify-center">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -top-16 -left-10 h-64 w-64 rounded-full bg-primary opacity-[0.16] blur-[90px]"
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -right-6 -bottom-10 h-56 w-56 rounded-full bg-heat opacity-[0.14] blur-[90px]"
            />

            <div className="relative w-full max-w-[380px] -rotate-2 rounded-[2.25rem] border border-border bg-surface p-2 shadow-[0_50px_100px_-40px_rgba(0,0,0,0.7)]">
              <div className="overflow-hidden rounded-[1.75rem] border border-border bg-bg">
                <div className="flex items-center justify-between px-5 pt-4 pb-2 font-mono text-[0.68rem] text-text-tertiary">
                  <span>9:41</span>
                  <span>MELBOURNE, VIC</span>
                </div>

                <div className="relative mx-4 mb-4 flex h-32 items-center justify-center overflow-hidden rounded-2xl bg-surface-alt">
                  <svg viewBox="0 0 320 128" className="h-full w-full">
                    {/* Fastest — the straighter, unshaded option, shown faded
                        behind the chosen shaded route to make the actual
                        trade-off visible at a glance, not just described. */}
                    <path
                      d="M15 100 C 90 92, 170 55, 305 22"
                      fill="none"
                      stroke="var(--text-tertiary)"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeDasharray="1 8"
                      opacity="0.5"
                    />
                    <path
                      d="M15 100 C 70 100, 65 30, 130 30 S 205 90, 245 72 S 300 30, 305 22"
                      fill="none"
                      stroke="var(--primary)"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeDasharray="1 11"
                    />
                    <circle cx="15" cy="100" r="5" fill="var(--surface)" stroke="var(--primary)" strokeWidth="2.5" />
                    <circle cx="305" cy="22" r="5" fill="var(--primary)" />
                  </svg>
                </div>

                <div className="px-5 pb-5">
                  <div className="flex items-center justify-between text-xs text-text-tertiary">
                    <span>3 ways to Royal Botanic Gardens</span>
                    <span>Leaving now</span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-3 text-[0.7rem] text-text-tertiary">
                    <span className="flex items-center gap-1">
                      <span className="h-0.5 w-3 rounded-full bg-primary" /> Shaded, 17 min
                    </span>
                    <span className="flex items-center gap-1 opacity-70">
                      <span className="h-0.5 w-3 rounded-full bg-text-tertiary" /> Fastest, 13 min
                    </span>
                  </div>

                  <div className="mt-3 rounded-2xl border border-primary bg-primary-soft p-4">
                    <div className="flex items-start justify-between">
                      <div className="font-display text-2xl font-semibold tracking-tight text-text">
                        17<span className="ml-0.5 font-sans text-xs font-medium text-text-tertiary">min</span>
                      </div>
                      <span className="rounded-full bg-primary px-2.5 py-1 text-[0.65rem] font-semibold tracking-wide text-surface uppercase">
                        Comfort pick
                      </span>
                    </div>
                    <p className="mt-1.5 text-[0.83rem] text-text-secondary">
                      Under tree canopy for 80% of the walk, quieter side streets.
                    </p>
                  </div>

                  <div className="mt-4 rounded-full bg-primary py-3 text-center text-[0.9rem] font-semibold text-surface">
                    Start walking, 17 min
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="features" className="scroll-mt-20 border-t border-border px-5 py-20 sm:px-8 lg:px-12">
          <div className="mx-auto max-w-5xl">
            <div className="max-w-xl">
              <h2 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">
                Built for a warming city.
              </h2>
              <p className="mt-3 text-text-secondary">
                Melbourne is already seeing more extreme heat days, and on
                those days the shortest path is often the wrong one. LeafRoute
                weighs real, live conditions instead of just distance, so
                walking stays a genuine alternative to driving even as it
                gets hotter.
              </p>
            </div>

            <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2">
              {FEATURES.map((f) => (
                <div
                  key={f.title}
                  className="rounded-2xl border border-border bg-surface p-6"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-alt">
                    <ConditionIcon tone={f.tone} className="h-5 w-5" />
                  </div>
                  <h3 className="mt-4 font-display text-base font-semibold text-text">
                    {f.title}
                  </h3>
                  <p className="mt-1.5 text-[0.9rem] leading-relaxed text-text-secondary">
                    {f.body}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-10 flex justify-start">
              <PendingLink
                href={`${appOrigin}/signup`}
                newTabOnDesktop={Boolean(appOrigin)}
                className="rounded-full bg-primary px-7 py-3.5 text-center text-[0.95rem] font-semibold text-surface transition-opacity hover:opacity-90"
              >
                Start tracking your walks
              </PendingLink>
            </div>
          </div>
        </section>

        {/* How it works */}
        <section
          id="how-it-works"
          className="scroll-mt-20 border-t border-border bg-surface/40 px-5 py-20 sm:px-8 lg:px-12"
        >
          <div className="mx-auto max-w-5xl">
            <h2 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">
              How it works
            </h2>

            <div className="mt-12 grid grid-cols-1 gap-10 sm:grid-cols-3 sm:gap-6">
              {STEPS.map((step) => (
                <div key={step.number}>
                  <div className="font-display text-sm font-semibold text-primary">
                    {step.number}
                  </div>
                  <h3 className="mt-2 font-display text-lg font-semibold text-text">
                    {step.title}
                  </h3>
                  <p className="mt-2 text-[0.9rem] leading-relaxed text-text-secondary">
                    {step.body}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Video */}
        <section id="demo" className="scroll-mt-20 border-t border-border px-5 py-20 sm:px-8 lg:px-12">
          <div className="mx-auto max-w-5xl">
            <h2 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">
              See LeafRoute in action.
            </h2>
            <div className="mt-8 aspect-video w-full overflow-hidden rounded-2xl border border-border bg-surface-alt">
              <iframe
                className="h-full w-full"
                src="https://www.youtube.com/embed/v4EALF0QRII"
                title="LeafRoute walkthrough"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
              />
            </div>
          </div>
        </section>

        {/* Contact */}
        <section id="contact" className="scroll-mt-20 border-t border-border px-5 py-20 sm:px-8 lg:px-12">
          <div className="mx-auto grid max-w-5xl grid-cols-1 gap-12 lg:grid-cols-[1fr_1.1fr]">
            <div>
              <h2 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">
                Questions, feedback, ideas?
              </h2>
              <p className="mt-3 max-w-sm text-text-secondary">
                We&apos;re building LeafRoute for Melbourne walkers. Tell us what
                would make it better, or let us know if something&apos;s not
                working.
              </p>
            </div>
            <ContactForm />
          </div>
        </section>
      </main>

      <footer className="border-t border-border px-5 py-10 sm:px-8 lg:px-12">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between gap-4 sm:flex-row">
          <div className="flex items-center gap-2 font-display text-sm font-semibold text-text">
            {/* eslint-disable-next-line @next/next/no-img-element -- a
                static brand mark, not a page image worth next/image's
                machinery */}
            <img src="/brand/leafroute-mark.png" alt="" className="h-4 w-4" />
            LeafRoute
          </div>
          <p className="text-xs text-text-tertiary">
            © {new Date().getFullYear()} LeafRoute · Melbourne, Australia
          </p>
        </div>
        <p className="mx-auto mt-4 max-w-5xl text-[0.7rem] text-text-tertiary">
          Routing and tree-canopy data © City of Melbourne (CC BY 4.0) and{" "}
          <a
            href="https://www.openstreetmap.org/copyright"
            className="underline hover:no-underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            OpenStreetMap contributors
          </a>{" "}
          (ODbL).
        </p>
      </footer>
    </div>
  );
}
