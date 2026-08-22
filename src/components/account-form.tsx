"use client";

import { useActionState, useEffect, useRef } from "react";
import type { AccountState } from "@/lib/actions/account";
import { useToast } from "@/components/toast-provider";
import { Spinner } from "@/components/spinner";

export function AccountForm({
  action,
  field,
  submitLabel,
}: {
  action: (state: AccountState, formData: FormData) => Promise<AccountState>;
  field: { name: string; label: string; type: string; autoComplete?: string };
  submitLabel: string;
}) {
  const [state, formAction, pending] = useActionState(action, undefined);
  const showToast = useToast();
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.success) {
      showToast(state.success);
      formRef.current?.reset();
    }
  }, [state, showToast]);

  return (
    <form ref={formRef} action={formAction} className="flex flex-col gap-3 sm:flex-row sm:items-end">
      <label className="flex flex-1 flex-col gap-1.5">
        <span className="text-sm font-medium text-text">{field.label}</span>
        <input
          name={field.name}
          type={field.type}
          autoComplete={field.autoComplete}
          required
          disabled={pending}
          className="rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[0.95rem] text-text placeholder:text-text-tertiary focus:border-primary focus:outline-none disabled:opacity-60"
        />
      </label>

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-surface disabled:opacity-60"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-surface" />}
        {pending ? "Saving…" : submitLabel}
      </button>

      {state?.error && (
        <p className="w-full rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat sm:basis-full">
          {state.error}
        </p>
      )}
    </form>
  );
}
