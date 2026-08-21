import type { ReactNode } from "react";

type Tone = "primary" | "heat" | "crowd" | "traffic";

const toneClass: Record<Tone, string> = {
  primary: "text-primary",
  heat: "text-heat",
  crowd: "text-crowd",
  traffic: "text-traffic",
};

const paths: Record<Tone, ReactNode> = {
  heat: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  crowd: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M2 20c0-3.3 3.1-6 7-6s7 2.7 7 6" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M16 14.1c2.7.5 5 2.6 5 5.4" />
    </>
  ),
  primary: (
    <>
      <path d="M12 3v2M5.6 5.6l1.4 1.4M3 12h2M5.6 18.4l1.4-1.4M12 19v2M17 17l1.4 1.4M19 12h2M17 7l1.4-1.4" />
      <path d="M8 21a4 4 0 0 1 8 0" />
    </>
  ),
  traffic: (
    <>
      <rect x="3" y="11" width="18" height="7" rx="1.5" />
      <circle cx="7.5" cy="18" r="1.5" />
      <circle cx="16.5" cy="18" r="1.5" />
    </>
  ),
};

export function ConditionIcon({
  tone,
  className = "h-[18px] w-[18px]",
}: {
  tone: Tone;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`${toneClass[tone]} ${className}`}
    >
      {paths[tone]}
    </svg>
  );
}
