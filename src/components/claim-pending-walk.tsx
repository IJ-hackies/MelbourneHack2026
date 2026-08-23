"use client";

import { useEffect, useRef } from "react";
import { logWalk } from "@/lib/actions/walks";
import { useToast } from "@/components/toast-provider";
import { PENDING_WALK_STORAGE_KEY, type PendingWalk } from "@/lib/pending-walk";

// Mounted only when a real session exists (see layout.tsx) — claims a walk
// a guest finished and stashed locally before signing in (active-walk.tsx),
// so it doesn't just disappear the moment they create an account.
export function ClaimPendingWalk() {
  const showToast = useToast();
  const claimedRef = useRef(false);

  useEffect(() => {
    if (claimedRef.current) return;
    claimedRef.current = true;

    let raw: string | null = null;
    try {
      raw = localStorage.getItem(PENDING_WALK_STORAGE_KEY);
    } catch {
      return;
    }
    if (!raw) return;

    try {
      localStorage.removeItem(PENDING_WALK_STORAGE_KEY);
    } catch {
      // best effort — worst case this runs again next mount and logWalk
      // just inserts a duplicate row, not a crash.
    }

    let pending: PendingWalk;
    try {
      pending = JSON.parse(raw);
    } catch {
      return;
    }

    logWalk(pending).then((result) => {
      if (!result.error) {
        showToast(`Saved your walk to ${pending.destination} from before you signed in`);
      }
    });
    // showToast is stable from context; only run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
