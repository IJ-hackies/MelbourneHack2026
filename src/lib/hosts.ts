// Shared with src/proxy.ts — marketing lives only at the bare domain, the
// app lives only at the "app." subdomain. Every other host (localhost,
// *.vercel.app, previews) keeps the combined single-domain behaviour.
export const APEX_HOSTS = ["leafroute.org", "www.leafroute.org"];
export const APP_HOST = "app.leafroute.org";

/** Absolute origin to send auth links to from the marketing page, or "" to
 * stay relative (combined-domain hosts, where the app lives on the same
 * origin as the marketing page). */
export function getAppOrigin(host: string | null) {
  return host && APEX_HOSTS.includes(host) ? `https://${APP_HOST}` : "";
}
