"use client";

import { useEffect } from "react";
import "./globals.css";

// Catches a crash in the root layout itself (e.g. the Supabase user lookup
// in layout.tsx throwing) — app/error.tsx only covers errors below the root
// layout, so without this a root-layout crash falls through to Next's own
// unstyled default error screen instead of the app's error UI.
export default function GlobalError({
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
    <html lang="en">
      <body>
        <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-5 py-8 text-center sm:px-8">
          <h1 className="text-[1.4rem] font-semibold tracking-tight text-text">
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
      </body>
    </html>
  );
}
