"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-md flex-col items-center justify-center px-5 py-8 text-center sm:px-8">
      <h1 className="font-display text-[1.4rem] font-semibold tracking-tight text-text">
        Something went wrong
      </h1>
      <p className="mt-2 text-sm text-text-secondary">
        That&apos;s on us, not you. Try again, and if it keeps happening let us know.
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-6 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-surface"
      >
        Try again
      </button>
    </main>
  );
}
