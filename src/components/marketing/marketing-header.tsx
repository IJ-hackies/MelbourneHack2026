"use client";

import { useEffect, useState } from "react";
import { PendingLink } from "@/components/pending-link";

const NAV_LINKS = [
  { id: "features", label: "Features" },
  { id: "how-it-works", label: "How it works" },
  { id: "contact", label: "Contact" },
];

export function MarketingHeader({ appOrigin = "" }: { appOrigin?: string }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  function scrollTo(id: string) {
    setOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur-md relative">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8 lg:px-12">
        <div className="flex items-center gap-2 font-display text-[1.1rem] font-semibold tracking-tight text-text">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-[22px] w-[22px] text-primary">
            <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
            <circle cx="12" cy="9.5" r="2.4" />
          </svg>
          LeafRoute
        </div>

        <nav className="hidden items-center gap-8 md:flex" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <button
              key={link.id}
              type="button"
              onClick={() => scrollTo(link.id)}
              className="text-sm font-medium text-text-secondary transition-colors hover:text-text"
            >
              {link.label}
            </button>
          ))}
        </nav>

        <div className="hidden items-center gap-3 md:flex">
          <PendingLink
            href={`${appOrigin}/login`}
            newTabOnDesktop={Boolean(appOrigin)}
            className="text-sm font-medium text-text-secondary transition-colors hover:text-text"
          >
            Log in
          </PendingLink>
          <PendingLink
            href={`${appOrigin}/`}
            newTabOnDesktop={Boolean(appOrigin)}
            className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
          >
            Try it now
          </PendingLink>
        </div>

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="marketing-mobile-nav"
          aria-label={open ? "Close menu" : "Open menu"}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-text md:hidden"
        >
          {open ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          )}
        </button>
      </div>

      <nav
        id="marketing-mobile-nav"
        aria-label="Primary mobile"
        aria-hidden={!open}
        className={`absolute inset-x-0 top-full flex flex-col gap-1 border-t border-border bg-bg px-5 py-4 shadow-[0_20px_40px_-20px_rgba(0,0,0,0.3)] transition-[opacity,transform] duration-300 ease-out md:hidden ${
          open
            ? "pointer-events-auto translate-y-0 opacity-100"
            : "pointer-events-none -translate-y-2 opacity-0"
        }`}
      >
        {NAV_LINKS.map((link) => (
          <button
            key={link.id}
            type="button"
            tabIndex={open ? 0 : -1}
            onClick={() => scrollTo(link.id)}
            className="rounded-xl px-3 py-3 text-left text-[0.95rem] font-medium text-text-secondary transition-colors hover:bg-surface-alt hover:text-text"
          >
            {link.label}
          </button>
        ))}
      </nav>
    </header>
  );
}
