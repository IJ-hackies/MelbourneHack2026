"use client";

import { useState, useTransition } from "react";
import { signOutOtherSessions } from "@/lib/actions/account";
import { useToast } from "@/components/toast-provider";
import { Spinner } from "@/components/spinner";

export function SignOutOthersButton() {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const showToast = useToast();

  function handleClick() {
    setError(null);
    startTransition(async () => {
      const result = await signOutOtherSessions();
      if (result.error) {
        setError(result.error);
        return;
      }
      showToast(result.success ?? "Signed out everywhere else");
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 self-start rounded-xl border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-text disabled:opacity-60"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-current" />}
        {pending ? "Signing out other sessions…" : "Sign out of other devices"}
      </button>
      {error && <p className="text-[0.82rem] text-heat">{error}</p>}
    </div>
  );
}
