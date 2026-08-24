// Turns a raw "X kg CO2e avoided" figure into something a person can
// actually picture. A bare kg number means little to most people on its
// own — these are commonly-cited everyday equivalencies (same style as the
// US EPA's greenhouse gas equivalencies calculator), not a live measurement,
// so they're presented the same honest way the rest of this app treats an
// estimate: a comparison, not a guarantee.
//
// kgPerUnit values are approximate, average-case figures:
// - smartphone charge: ~8.4g CO2e per full charge
// - video streaming: ~36g CO2e per hour of HD streaming
// - LED bulb: ~4g CO2e per hour (10W bulb, average grid intensity)
// - coffee: ~21g CO2e per cup brewed
// - plastic bottle: ~80g CO2e per 500ml bottle produced
// - tree: a mature tree absorbs ~21kg CO2 a year, ~0.0575kg/day
//
// Deliberately doesn't include a "km not driven" comparison -- that factor
// (0.19 kg/km) is the same one the emissions estimate itself is built from,
// so it's circular rather than a genuinely different way to picture the
// number.
type Comparison = {
  kgPerUnit: number;
  describe: (count: number) => string;
};

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return count === 1 ? singular : pluralForm;
}

const COMPARISONS: Comparison[] = [
  {
    kgPerUnit: 0.08,
    describe: (n) => `not producing ${Math.round(n)} plastic ${plural(Math.round(n), "bottle")}`,
  },
  {
    kgPerUnit: 0.0575,
    describe: (n) => `a tree absorbing CO2 for ${Math.round(n)} ${plural(Math.round(n), "day")}`,
  },
  {
    kgPerUnit: 0.036,
    describe: (n) => `${Math.round(n)} ${plural(Math.round(n), "hour")} less video streaming`,
  },
  {
    kgPerUnit: 0.021,
    describe: (n) => `${Math.round(n)} fewer ${plural(Math.round(n), "cup")} of coffee brewed`,
  },
  {
    kgPerUnit: 0.0084,
    describe: (n) => `${Math.round(n)} fewer smartphone ${plural(Math.round(n), "charge")}`,
  },
  {
    kgPerUnit: 0.004,
    describe: (n) => `an LED bulb switched off for ${Math.round(n)} ${plural(Math.round(n), "hour")}`,
  },
];

// Only a comparison whose count actually rounds to something worth saying
// (>= 1 unit) is eligible — a tiny walk shouldn't produce "0 plastic
// bottles". Comparisons are ordered above from the largest per-unit factor
// to the smallest, so a very small kg figure still has a real chance to
// land on a comparison with a sensible-looking count.
export function co2Comparison(kg: number): string | null {
  if (!(kg > 0)) return null;

  const eligible = COMPARISONS.filter((c) => Math.round(kg / c.kgPerUnit) >= 1);
  if (eligible.length === 0) return null;

  const pick = eligible[Math.floor(Math.random() * eligible.length)];
  return pick.describe(kg / pick.kgPerUnit);
}
