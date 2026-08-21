"use client";

export function Toggle({
  name,
  description,
  checked,
  onChange,
}: {
  name: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-border py-4 first:border-t-0">
      <div className="pr-4">
        <div className="text-sm font-medium text-text">{name}</div>
        <div className="mt-0.5 max-w-[34ch] text-[0.78rem] text-text-tertiary">
          {description}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
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
