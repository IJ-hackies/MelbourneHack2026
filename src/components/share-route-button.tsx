"use client";

import { useState } from "react";
import { useToast } from "@/components/toast-provider";

// The route detail URL already carries destination + origin as real query
// params (see route/[id]/page.tsx) — it's shareable as-is, this just gives
// it an explicit affordance instead of relying on someone knowing to copy
// the address bar.
export function ShareRouteButton({ destination, minutes }: { destination: string; minutes: number }) {
  const [copied, setCopied] = useState(false);
  const showToast = useToast();

  async function handleShare() {
    const url = window.location.href;
    const shareData = { title: "LeafRoute", text: `${minutes} min walk to ${destination}`, url };
    if (navigator.share) {
      try {
        await navigator.share(shareData);
      } catch {
        // User cancelled the share sheet — not an error worth surfacing.
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      showToast("Route link copied");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      showToast("Couldn't copy the link", "error");
    }
  }

  return (
    <button
      type="button"
      onClick={handleShare}
      className="flex items-center gap-1.5 text-sm text-text-secondary hover:text-text"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <circle cx="18" cy="5" r="3" />
        <circle cx="6" cy="12" r="3" />
        <circle cx="18" cy="19" r="3" />
        <path d="m8.6 10.5 6.8-3.9M8.6 13.5l6.8 3.9" />
      </svg>
      {copied ? "Copied" : "Share"}
    </button>
  );
}
