"use client";

import { useActionState, useEffect, useRef } from "react";
import { sendContactMessage } from "@/lib/actions/contact";
import { Spinner } from "@/components/spinner";

export function ContactForm() {
  const [state, formAction, pending] = useActionState(sendContactMessage, undefined);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.success) formRef.current?.reset();
  }, [state]);

  if (state?.success) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-2xl border border-primary bg-primary-soft p-6">
        <h3 className="font-display text-base font-semibold text-text">Message sent</h3>
        <p className="text-sm text-text-secondary">
          Thanks, we&apos;ll get back to you soon.
        </p>
      </div>
    );
  }

  return (
    <form ref={formRef} action={formAction} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-text">Name</span>
          <input
            name="name"
            type="text"
            required
            disabled={pending}
            className="rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[0.95rem] text-text placeholder:text-text-tertiary focus:border-primary focus:outline-none disabled:opacity-60"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-text">Email</span>
          <input
            name="email"
            type="email"
            required
            disabled={pending}
            className="rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[0.95rem] text-text placeholder:text-text-tertiary focus:border-primary focus:outline-none disabled:opacity-60"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-text">Message</span>
        <textarea
          name="message"
          required
          rows={5}
          disabled={pending}
          className="resize-none rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[0.95rem] text-text placeholder:text-text-tertiary focus:border-primary focus:outline-none disabled:opacity-60"
        />
      </label>

      {state?.error && (
        <p className="rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">{state.error}</p>
      )}

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-semibold text-surface transition-opacity hover:opacity-90 disabled:opacity-60 sm:self-start"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-surface" />}
        {pending ? "Sending…" : "Send message"}
      </button>
    </form>
  );
}
