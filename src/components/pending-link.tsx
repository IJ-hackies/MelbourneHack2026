"use client";

import Link, { type LinkProps } from "next/link";
import { useLinkStatus } from "next/link";
import { Spinner } from "@/components/spinner";
import { useSyncExternalStore, useState, type ReactNode } from "react";

function LinkContent({ children, forcePending }: { children: ReactNode; forcePending: boolean }) {
  const { pending } = useLinkStatus();
  const showPending = pending || forcePending;
  return (
    <span className="inline-flex items-center justify-center gap-2">
      {showPending && <Spinner className="h-4 w-4 text-current" />}
      <span className={showPending ? "opacity-70" : undefined}>{children}</span>
    </span>
  );
}

/** Matches the `md` Tailwind breakpoint used elsewhere for desktop nav. */
function subscribeIsDesktop(onChange: () => void) {
  const query = window.matchMedia("(min-width: 768px)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function useIsDesktop() {
  return useSyncExternalStore(
    subscribeIsDesktop,
    () => window.matchMedia("(min-width: 768px)").matches,
    () => false
  );
}

export function PendingLink({
  children,
  className,
  newTabOnDesktop,
  ...props
}: LinkProps & {
  children: ReactNode;
  className?: string;
  /** Opens in a new tab on desktop; on mobile, standard same-tab navigation. */
  newTabOnDesktop?: boolean;
}) {
  const isDesktop = useIsDesktop();
  const openInNewTab = newTabOnDesktop && isDesktop;

  // A target="_blank" click falls back to a plain anchor navigation, so
  // Next's own useLinkStatus never fires for it — without this, clicking
  // felt like nothing happened while the new tab loaded.
  const [opening, setOpening] = useState(false);

  return (
    <Link
      {...props}
      className={className}
      target={openInNewTab ? "_blank" : undefined}
      rel={openInNewTab ? "noopener noreferrer" : undefined}
      onClick={(e) => {
        props.onClick?.(e);
        if (openInNewTab) {
          setOpening(true);
          setTimeout(() => setOpening(false), 1500);
        }
      }}
    >
      <LinkContent forcePending={opening}>{children}</LinkContent>
    </Link>
  );
}
