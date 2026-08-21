import { PendingLink } from "@/components/pending-link";
import { ConditionIcon } from "@/components/condition-icon";
import { MarketingHeader } from "@/components/marketing/marketing-header";
import { ContactForm } from "@/components/marketing/contact-form";

const FEATURES = [
  {
    tone: "heat" as const,
    title: "Heat & shade aware",
    body: "Routes weigh tree canopy, building shade, and solar position against sun exposure, not just distance.",
  },
  {
    tone: "crowd" as const,
    title: "Reads the crowd",
    body: "Forecast pedestrian density along the way, so a busy shortcut doesn't ruin a quiet walk.",
  },
  {
    tone: "traffic" as const,
    title: "Quieter, safer streets",
    body: "Weight vehicle traffic lower when you'd rather not walk beside six lanes of it.",
  },
  {
    tone: "primary" as const,
    title: "Tuned to you",
    body: "Set your pace and your comfort-versus-speed balance once, and every route respects it after that.",
  },
];

const STEPS = [
  {
    number: "01",
    title: "Search where you're headed",
    body: "Type a street, building, or place anywhere in Melbourne, real addresses, not guesswork.",
  },
  {
    number: "02",
    title: "Compare the trade-off",
    body: "See a few honest options, the fastest, the shadiest, the quietest, and exactly what each one costs you in minutes.",
  },
  {
    number: "03",
    title: "Walk it, track it",
    body: "Start walking with one tap. Your history and estimated avoided emissions build up automatically.",
  },
];

export function MarketingPage() {
  return (
    <div>
      <MarketingHeader />

      <main>
        {/* Hero */}
        <section className="relative overflow-hidden px-5 pt-20 pb-24 sm:px-8 sm:pt-28 sm:pb-32 lg:px-12">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] bg-[radial-gradient(60%_50%_at_50%_0%,color-mix(in_srgb,var(--primary)_22%,transparent),transparent)]"
          />
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="font-display text-[2.6rem] leading-[1.05] font-semibold tracking-tight text-text sm:text-[3.4rem] lg:text-[4rem]">
              Walk Melbourne smarter,
              <br />
              not just shorter.
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-[1.05rem] leading-relaxed text-text-secondary sm:text-lg">
              HeatRoute finds the shadiest, quietest, most comfortable way to get
              there, trading a few minutes for a walk you&apos;ll actually enjoy.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <PendingLink
                href="/signup"
                className="w-full rounded-full bg-primary px-7 py-3.5 text-center text-[0.95rem] font-semibold text-surface shadow-[0_16px_36px_-16px_color-mix(in_srgb,var(--primary)_70%,transparent)] transition-opacity hover:opacity-90 sm:w-auto"
              >
                Get started free
              </PendingLink>
              <PendingLink
                href="/login"
                className="w-full rounded-full border border-border px-7 py-3.5 text-center text-[0.95rem] font-medium text-text transition-colors hover:bg-surface-alt sm:w-auto"
              >
                Log in
              </PendingLink>
            </div>
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
                Every route weighs more than distance.
              </h2>
              <p className="mt-3 text-text-secondary">
                Melbourne summers make the shortest path the wrong one more often
                than you&apos;d think. HeatRoute accounts for what actually makes a walk
                bearable.
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

        {/* Emissions callout */}
        <section className="border-t border-border px-5 py-20 sm:px-8 lg:px-12">
          <div className="mx-auto flex max-w-5xl flex-col items-start gap-8 rounded-3xl border border-primary bg-primary-soft p-8 sm:flex-row sm:items-center sm:justify-between sm:p-12">
            <div className="max-w-md">
              <h2 className="font-display text-xl font-semibold tracking-tight text-text sm:text-2xl">
                Every walk is one less car trip.
              </h2>
              <p className="mt-2 text-[0.92rem] text-text-secondary">
                HeatRoute tracks your walking history and gives you a transparent,
                honest estimate of the emissions you avoided by choosing to walk.
              </p>
            </div>
            <PendingLink
              href="/signup"
              className="shrink-0 rounded-full bg-primary px-7 py-3.5 text-center text-[0.95rem] font-semibold text-surface transition-opacity hover:opacity-90"
            >
              Start tracking
            </PendingLink>
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
                We&apos;re building HeatRoute for Melbourne walkers, tell us what would
                make it better, or what&apos;s not working.
              </p>
            </div>
            <ContactForm />
          </div>
        </section>
      </main>

      <footer className="border-t border-border px-5 py-10 sm:px-8 lg:px-12">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between gap-4 sm:flex-row">
          <div className="flex items-center gap-2 font-display text-sm font-semibold text-text">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary">
              <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
              <circle cx="12" cy="9.5" r="2.4" />
            </svg>
            HeatRoute
          </div>
          <p className="text-xs text-text-tertiary">
            © {new Date().getFullYear()} HeatRoute · Melbourne, Australia
          </p>
        </div>
      </footer>
    </div>
  );
}
