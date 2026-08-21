"use client";

import { useFormStatus } from "react-dom";
import { Spinner } from "@/components/spinner";

export function FormStatusButton({
  children,
  pendingLabel,
  className,
}: {
  children: React.ReactNode;
  pendingLabel: string;
  className?: string;
}) {
  const { pending } = useFormStatus();

  return (
    <button
      type="submit"
      disabled={pending}
      aria-busy={pending}
      className={`inline-flex items-center justify-center gap-2 disabled:opacity-60 ${className ?? ""}`}
    >
      {pending && <Spinner className="h-3.5 w-3.5 text-current" />}
      {pending ? pendingLabel : children}
    </button>
  );
}
