"use client";

import { createContext, useContext, useMemo, useState } from "react";

type LiveProgress = { distanceRemainingKm: number; etaMinutes: number } | null;

const LiveProgressContext = createContext<{
  progress: LiveProgress;
  setProgress: (p: LiveProgress) => void;
} | null>(null);

// Shares live distance/ETA between RouteMap (producer, via onLiveProgress)
// and ActiveWalk (consumer) even though they render in different parts of
// the page layout under the same server-rendered route-detail page.
export function LiveProgressProvider({ children }: { children: React.ReactNode }) {
  const [progress, setProgress] = useState<LiveProgress>(null);
  const value = useMemo(() => ({ progress, setProgress }), [progress]);
  return <LiveProgressContext.Provider value={value}>{children}</LiveProgressContext.Provider>;
}

export function useLiveProgress() {
  const ctx = useContext(LiveProgressContext);
  if (!ctx) throw new Error("useLiveProgress must be used within a LiveProgressProvider");
  return ctx;
}
