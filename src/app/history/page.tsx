const activityLevels = [
  0, 1, 0, 2, 0, 1, 3, 2, 0, 0, 1, 1, 0, 2, 0, 3, 1, 0, 2, 0, 1, 1, 0, 2, 3, 0,
  0, 1,
];

const levelClass = [
  "bg-surface-sunk",
  "bg-primary-soft",
  "bg-primary/55",
  "bg-primary",
];

const recentWalks = [
  { date: "Today", to: "Fitzroy Gardens", minutes: 17, km: 2.1 },
  { date: "Yesterday", to: "Queen Victoria Market", minutes: 22, km: 2.8 },
  { date: "Mon 17 Aug", to: "State Library", minutes: 13, km: 1.6 },
  { date: "Fri 14 Aug", to: "Royal Botanic Gardens", minutes: 31, km: 3.9 },
];

export default function History() {
  const totalKm = recentWalks.reduce((sum, w) => sum + w.km, 0);
  const emissionsKg = (totalKm * 0.19).toFixed(1);

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-8 px-5 py-8 sm:px-8 lg:max-w-5xl lg:py-12">
      <div>
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
          Your walking history
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {recentWalks.length} walks this month
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr] lg:items-start">
        <div className="rounded-2xl bg-primary p-[18px] text-surface">
          <div className="text-[0.76rem] tracking-wide text-surface/85 uppercase">
            Estimated avoided emissions
          </div>
          <div className="mt-1 font-display text-[2rem] font-semibold tracking-tight">
            {emissionsKg} kg CO₂e
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
            {activityLevels.map((level, i) => (
              <div
                key={i}
                className={`aspect-square rounded-[3px] ${levelClass[level]}`}
                title={level === 0 ? "No walk" : `${level} walk${level > 1 ? "s" : ""}`}
              />
            ))}
          </div>
        </div>
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-text">
          Recent walks
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-2.5 lg:grid-cols-2">
          {recentWalks.map((walk) => (
            <div
              key={`${walk.date}-${walk.to}`}
              className="flex items-center justify-between rounded-2xl border border-border bg-surface px-4 py-3.5"
            >
              <div>
                <div className="text-sm font-medium text-text">{walk.to}</div>
                <div className="mt-0.5 text-[0.78rem] text-text-tertiary">
                  {walk.date} · {walk.km} km
                </div>
              </div>
              <div className="font-display text-sm font-semibold text-text">
                {walk.minutes} min
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
