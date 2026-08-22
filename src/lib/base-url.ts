import { headers } from "next/headers";

// Resolves this deployment's own origin for server-side calls to sibling
// API routes/functions (weather, ML inference). Prefers the incoming
// request's own host header — correct in dev even when Next picks a
// non-default port (e.g. 3000 already in use) — falling back to
// VERCEL_URL, then localhost:3000, for contexts with no request headers.
export async function getBaseUrl(): Promise<string> {
  try {
    const h = await headers();
    const host = h.get("x-forwarded-host") ?? h.get("host");
    if (host) {
      const proto = h.get("x-forwarded-proto") ?? (host.includes("localhost") ? "http" : "https");
      return `${proto}://${host}`;
    }
  } catch {
    // headers() throws outside a request context (e.g. background jobs).
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}`;
  }
  return "http://localhost:3000";
}
