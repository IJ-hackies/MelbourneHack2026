"use client";

import { useActionState, useState } from "react";
import { deleteAccount } from "@/lib/actions/account";
import { Spinner } from "@/components/spinner";

export function DeleteAccountForm() {
  const [state, formAction, pending] = useActionState(deleteAccount, undefined);
  const [confirmation, setConfirmation] = useState("");

  return (
    <form action={formAction} className="flex flex-col gap-3">
      <label className="flex flex-col gap-1.5">
        <span className="text-sm text-text-secondary">
          Type <strong className="text-text">DELETE</strong> to confirm. This removes your
          profile, preferences, saved places, and walking history permanently.
        </span>
        <input
          name="confirmation"
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          autoComplete="off"
          disabled={pending}
          className="rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[0.95rem] text-text focus:border-primary focus:outline-none disabled:opacity-60"
        />
      </label>

      {state?.error && (
        <p className="rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">{state.error}</p>
      )}

      <button
        type="submit"
        disabled={confirmation !== "DELETE" || pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 rounded-xl bg-heat px-5 py-2.5 text-sm font-semibold text-surface disabled:opacity-40"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-surface" />}
        {pending ? "Deleting…" : "Delete my account"}
      </button>
    </form>
  );
}
