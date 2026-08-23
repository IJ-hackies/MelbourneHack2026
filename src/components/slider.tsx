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
}: {
  label: string;
  valueLabel: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (v: number) => void;
  hint?: string;
}) {
  const id = useId();
  const pct = ((value - min) / (max - min)) * 100;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <label htmlFor={id} className="font-medium text-text">
          {label}
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
