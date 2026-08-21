"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const tabs = [
  { href: "/", label: "Plan" },
  { href: "/history", label: "History" },
  { href: "/preferences", label: "Preferences" },
];

export function NavTabs() {
  const pathname = usePathname();

  return (
    <nav className="border-b border-border" aria-label="Primary">
      <div className="mx-auto flex max-w-5xl gap-6 px-5 sm:px-8 lg:px-12">
        {tabs.map((tab) => {
          const active =
            tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active ? "page" : undefined}
              className={`-mb-px border-b-2 px-1 py-3 text-sm font-medium transition-colors ${
                active
                  ? "border-primary text-text"
                  : "border-transparent text-text-tertiary hover:text-text-secondary"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
