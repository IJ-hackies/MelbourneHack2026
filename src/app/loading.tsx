export default function Loading() {
  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-md items-center justify-center px-5 py-8 sm:px-8">
      <span
        aria-label="Loading"
        className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-primary"
      />
    </main>
  );
}
