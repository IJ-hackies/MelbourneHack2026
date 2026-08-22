import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-md flex-col items-center justify-center px-5 py-8 text-center sm:px-8">
      <h1 className="font-display text-[1.4rem] font-semibold tracking-tight text-text">
        Page not found
      </h1>
      <p className="mt-2 text-sm text-text-secondary">
        That route doesn&apos;t exist, or the walk you&apos;re looking for has wandered off.
      </p>
      <Link
        href="/"
        className="mt-6 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-surface"
      >
        Back to Plan
      </Link>
    </main>
  );
}
