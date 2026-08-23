// Best-effort per-IP rate limiting for Server Actions and Next.js API
// routes, on top of Supabase's own auth.rate_limit config — this catches
// obvious scripted abuse at the app layer before it even reaches Supabase
// or a third-party API (Photon geocoding, Open-Meteo weather) whose free
// quota this app doesn't control. Resets on a cold server restart and
// isn't shared across instances, so it's a mitigation, not a hard boundary
// (see api/_shared/rate_limit.py for the same trade-off on the Python
// inference functions, which already have this).
const DEFAULT_WINDOW_MS = 5 * 60_000;
const DEFAULT_MAX_PER_WINDOW = 10;

const buckets = new Map<string, { windowStart: number; count: number }>();

export function isRateLimited(
  key: string,
  options?: { windowMs?: number; maxPerWindow?: number }
): boolean {
  const windowMs = options?.windowMs ?? DEFAULT_WINDOW_MS;
  const maxPerWindow = options?.maxPerWindow ?? DEFAULT_MAX_PER_WINDOW;
  const now = Date.now();
  if (buckets.size > 5000) buckets.clear();

  const bucket = buckets.get(key);
  if (!bucket || now - bucket.windowStart >= windowMs) {
    buckets.set(key, { windowStart: now, count: 1 });
    return false;
  }
  bucket.count += 1;
  return bucket.count > maxPerWindow;
}

// Real client IP from Vercel's forwarded headers — same extraction used by
// api/_shared/rate_limit.py's client_ip() for the Python endpoints.
export function requestIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "unknown";
}
