"use client";

import { useActionState, useEffect } from "react";
import type { AccountState } from "@/lib/actions/account";
import { useToast } from "@/components/toast-provider";
import { Spinner } from "@/components/spinner";

export function ResetLinkButton({
  action,
  label,
}: {
  action: (state: AccountState, formData: FormData) => Promise<AccountState>;
  label: string;
}) {
  const [state, formAction, pending] = useActionState(action, undefined);
  const showToast = useToast();

  useEffect(() => {
    if (state?.success) showToast(state.success);
  }, [state, showToast]);

  return (
    <form action={formAction}>
      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-surface disabled:opacity-60"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-surface" />}
        {pending ? "Sending…" : label}
      </button>

      {state?.error && (
        <p className="mt-3 rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">
          {state.error}
        </p>
      )}
    </form>
  );
}
