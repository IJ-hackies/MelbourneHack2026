import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { safeLocalPath } from "@/lib/safe-redirect";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = safeLocalPath(searchParams.get("next"));

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      // Doesn't need to special-case a first-time OAuth sign-up itself:
      // src/proxy.ts already redirects any authenticated request whose
      // profile has onboarded=false to /onboarding (and away from it once
      // onboarded), so a fresh Google sign-up lands there on its very next
      // request regardless of what `next` says here.
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/login?error=oauth`);
}
