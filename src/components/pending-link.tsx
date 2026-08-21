"use client";

import Link, { type LinkProps } from "next/link";
import { useLinkStatus } from "next/link";
import { Spinner } from "@/components/spinner";
import type { ReactNode } from "react";

function LinkContent({ children }: { children: ReactNode }) {
  const { pending } = useLinkStatus();
  return (
    <span className="inline-flex items-center justify-center gap-2">
      {pending && <Spinner className="h-4 w-4 text-current" />}
      <span className={pending ? "opacity-70" : undefined}>{children}</span>
    </span>
  );
}

export function PendingLink({
  children,
  className,
  ...props
}: LinkProps & { children: ReactNode; className?: string }) {
  return (
    <Link {...props} className={className}>
      <LinkContent>{children}</LinkContent>
    </Link>
  );
}
