import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

// Only ever redirect to a same-site path after auth — a bare "/evil.com" or
// "https://evil.com" passed through `next` would otherwise let an attacker
// send a real login/reset link that lands the user on their own site post-auth.
function safeLocalPath(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return "/";
  if (next.includes("://") || next.includes("\\")) return "/";
  return next;
}

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = safeLocalPath(searchParams.get("next"));

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/login?error=oauth`);
}
