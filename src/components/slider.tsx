"use client";

import { useId } from "react";

export function Slider({
  label,
  valueLabel,
  value,
  min = 0,
  max = 100,
  onChange,
  hint,
  comingSoon = false,
}: {
  label: string;
  valueLabel: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (v: number) => void;
  hint?: string;
  // True for a control that's saved but doesn't affect routing yet -- the
  // hint text already says so, but that's easy to skim past when every
  // control on the form otherwise looks equally "live".
  comingSoon?: boolean;
}) {
  const id = useId();
  const pct = ((value - min) / (max - min)) * 100;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <label htmlFor={id} className="flex items-center gap-2 font-medium text-text">
          {label}
          {comingSoon && (
            <span className="rounded-full bg-surface-sunk px-2 py-0.5 text-[0.62rem] font-semibold tracking-wide text-text-tertiary uppercase">
              Coming soon
            </span>
          )}
        </label>
        <span className="font-mono text-[0.76rem] text-text-secondary">{valueLabel}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 w-full appearance-none rounded-full bg-surface-sunk accent-primary"
        style={{
          background: `linear-gradient(to right, var(--primary) ${pct}%, var(--surface-sunk) ${pct}%)`,
        }}
      />
      {hint && <p className="mt-1.5 text-[0.76rem] text-text-tertiary">{hint}</p>}
    </div>
  );
}
