"use client";

import { useId } from "react";

export function Toggle({
  name,
  description,
  checked,
  onChange,
  comingSoon = false,
}: {
  name: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  // True for a control that's saved but doesn't affect routing yet -- the
  // description already says so, but that's easy to skim past when every
  // control on the form otherwise looks equally "live".
  comingSoon?: boolean;
}) {
  const labelId = useId();
  const descriptionId = useId();
  return (
    <div className="flex items-center justify-between border-t border-border py-4 first:border-t-0">
      <div className="pr-4">
        <div id={labelId} className="flex items-center gap-2 text-sm font-medium text-text">
          {name}
          {comingSoon && (
            <span className="rounded-full bg-surface-sunk px-2 py-0.5 text-[0.62rem] font-semibold tracking-wide text-text-tertiary uppercase">
              Coming soon
            </span>
          )}
        </div>
        <div id={descriptionId} className="mt-0.5 max-w-[34ch] text-[0.78rem] text-text-tertiary">
          {description}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-10 shrink-0 rounded-full transition-colors ${
          checked ? "bg-primary" : "bg-surface-sunk"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-surface shadow transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>
    </div>
  );
}
