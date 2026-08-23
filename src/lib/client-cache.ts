// A small in-memory, per-session TTL cache for client components — used to
// avoid re-fetching conditions/routes when a user re-searches or re-selects
// a place they just looked at (e.g. going back from a route detail page, or
// re-picking the same saved place). Deliberately short-lived: conditions
// and routing both reflect real live data (weather, crowd model, heat bias)
// that should never go stale for long, so this only smooths out quick
// repeat lookups within the same session, never masks a real change.
const store = new Map<string, { data: unknown; expiresAt: number }>();

export function getCached<T>(key: string): T | undefined {
  const entry = store.get(key);
  if (!entry) return undefined;
  if (Date.now() > entry.expiresAt) {
    store.delete(key);
    return undefined;
  }
  return entry.data as T;
}

export function setCached<T>(key: string, data: T, ttlMs: number): void {
  // Opportunistic prune so a long session doesn't accumulate unbounded
  // stale entries across many distinct searches.
  if (store.size > 200) {
    const now = Date.now();
    for (const [k, v] of store) {
      if (now > v.expiresAt) store.delete(k);
    }
  }
  store.set(key, { data, expiresAt: Date.now() + ttlMs });
}
