"use client";

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}

function getSnapshot(): Theme {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "dark" || explicit === "light") return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function getServerSnapshot(): Theme {
  return "light";
}

function apply(next: Theme) {
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("hr-theme", next);
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  return (
    <div
      role="group"
      aria-label="Theme"
      className="flex items-center gap-1 rounded-full border border-border bg-surface-alt p-1"
      suppressHydrationWarning
    >
      <button
        type="button"
        onClick={() => apply("light")}
        aria-pressed={theme === "light"}
        title="Light"
        suppressHydrationWarning
        className="flex h-8 w-8 items-center justify-center rounded-full text-text-tertiary transition-colors aria-pressed:bg-surface aria-pressed:text-primary aria-pressed:shadow-sm"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      </button>
      <button
        type="button"
        onClick={() => apply("dark")}
        aria-pressed={theme === "dark"}
        title="Dark"
        suppressHydrationWarning
        className="flex h-8 w-8 items-center justify-center rounded-full text-text-tertiary transition-colors aria-pressed:bg-surface aria-pressed:text-primary aria-pressed:shadow-sm"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
      </button>
    </div>
  );
}
