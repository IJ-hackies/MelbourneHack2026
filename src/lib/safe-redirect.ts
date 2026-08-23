// Only ever treat a `next` param as a same-site path — a bare "/evil.com"
// or "https://evil.com" would otherwise let an attacker-crafted login/reset
// link, or a "Continue as guest" click, land the user on their own site.
export function safeLocalPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return "/";
  if (next.includes("://") || next.includes("\\")) return "/";
  return next;
}
