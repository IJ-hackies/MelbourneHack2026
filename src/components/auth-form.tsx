"use client";

import { useActionState } from "react";
import type { AuthState } from "@/lib/actions/auth";
import { Spinner } from "@/components/spinner";

type Field = {
  name: string;
  label: string;
  type: string;
  autoComplete?: string;
};

export function AuthForm({
  action,
  fields,
  submitLabel,
  hidden,
}: {
  action: (state: AuthState, formData: FormData) => Promise<AuthState>;
  fields: Field[];
  submitLabel: string;
  hidden?: Record<string, string>;
}) {
  const [state, formAction, pending] = useActionState(action, undefined);

  return (
    <form action={formAction} className="flex flex-col gap-4">
      {hidden &&
        Object.entries(hidden).map(([name, value]) => (
          <input key={name} type="hidden" name={name} value={value} />
        ))}

      {fields.map((field) => (
        <label key={field.name} className="flex flex-col gap-1.5">
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
      ))}

      {state?.error && (
        <p className="rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">
          {state.error}
        </p>
      )}

      {state?.success && (
        <p className="rounded-xl bg-primary-soft px-3.5 py-2.5 text-sm text-primary-strong">
          {state.success}
        </p>
      )}

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="mt-1 flex items-center justify-center gap-2 rounded-xl bg-primary py-3 text-center font-semibold text-surface disabled:opacity-60"
      >
        {pending && <Spinner className="h-4 w-4 text-surface" />}
        {pending ? "Please wait…" : submitLabel}
      </button>
    </form>
  );
}
