// Best-effort per-IP rate limiting for auth Server Actions, on top of
// Supabase's own auth.rate_limit config — this catches obvious scripted
// abuse at the app layer before it even reaches Supabase. Resets on a cold
// server restart and isn't shared across instances, so it's a mitigation,
// not a hard boundary (see api/_shared/rate_limit.py for the same trade-off
// on the Python inference functions).
const WINDOW_MS = 5 * 60_000;
const MAX_PER_WINDOW = 10;

const buckets = new Map<string, { windowStart: number; count: number }>();

export function isRateLimited(key: string): boolean {
  const now = Date.now();
  if (buckets.size > 5000) buckets.clear();

  const bucket = buckets.get(key);
  if (!bucket || now - bucket.windowStart >= WINDOW_MS) {
    buckets.set(key, { windowStart: now, count: 1 });
    return false;
  }
  bucket.count += 1;
  return bucket.count > MAX_PER_WINDOW;
}
