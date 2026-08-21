import { type NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { APEX_HOSTS, APP_HOST } from "@/lib/hosts";

const PUBLIC_PATHS = ["/login", "/signup", "/forgot-password"];
const ONBOARDING_PATH = "/onboarding";

export async function proxy(request: NextRequest) {
  const host = request.headers.get("host") ?? "";

  if (APEX_HOSTS.includes(host)) {
    // The marketing page is the only thing this host ever serves — anything
    // else (a stale bookmark to /history, an old link, etc.) goes to the app.
    if (request.nextUrl.pathname !== "/") {
      const url = new URL(request.nextUrl.pathname + request.nextUrl.search, `https://${APP_HOST}`);
      return NextResponse.redirect(url);
    }
    return NextResponse.next({ request });
  }

  if (request.nextUrl.pathname === "/auth/callback" || request.nextUrl.pathname === "/api/health") {
    return NextResponse.next({ request });
  }

  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value)
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub as string | undefined;
  const isAuthed = Boolean(userId);
  const { pathname } = request.nextUrl;
  const isPublicPath = PUBLIC_PATHS.includes(pathname);
  const isAppHost = host === APP_HOST;

  // "/" is the public marketing page for signed-out visitors and the Plan
  // screen for signed-in ones — page.tsx branches on auth state itself.
  // Once the app subdomain is live it never shows marketing, so this
  // shortcut only applies on hosts that still combine both (localhost,
  // *.vercel.app, previews).
  if (!isAuthed && pathname === "/" && !isAppHost) {
    return supabaseResponse;
  }

  if (!isAuthed && !isPublicPath) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (isAuthed && isPublicPath) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (isAuthed && userId) {
    const { data: profile } = await supabase
      .from("profiles")
      .select("onboarded")
      .eq("id", userId)
      .single();

    const onboarded = profile?.onboarded ?? false;

    if (!onboarded && pathname !== ONBOARDING_PATH) {
      const url = request.nextUrl.clone();
      url.pathname = ONBOARDING_PATH;
      url.search = "";
      return NextResponse.redirect(url);
    }

    if (onboarded && pathname === ONBOARDING_PATH) {
      const url = request.nextUrl.clone();
      url.pathname = "/";
      url.search = "";
      return NextResponse.redirect(url);
    }
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
