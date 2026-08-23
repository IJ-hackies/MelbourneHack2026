import type { Metadata } from "next";
import { headers } from "next/headers";
import { Inter, Space_Grotesk, Roboto_Mono } from "next/font/google";
import { NavTabs } from "@/components/nav-tabs";
import { UserMenu } from "@/components/user-menu";
import { ToastProvider } from "@/components/toast-provider";
import { ClaimPendingWalk } from "@/components/claim-pending-walk";
import { APEX_HOSTS } from "@/lib/hosts";
import { createClient } from "@/lib/supabase/server";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-space-grotesk",
  display: "swap",
});

const robotoMono = Roboto_Mono({
  subsets: ["latin"],
  weight: ["500"],
  variable: "--font-roboto-mono",
  display: "swap",
});

// Resolves a real deployment env var into a valid absolute URL for
// metadataBase — defensively, since a misconfigured value here (empty
// string, a bare domain with no scheme, stray whitespace) must never be
// able to crash the entire production build over an OG-image nicety.
function resolveSiteUrl(): URL {
  const candidates = [
    process.env.NEXT_PUBLIC_SITE_URL,
    process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : undefined,
  ];
  for (const candidate of candidates) {
    const trimmed = candidate?.trim();
    if (!trimmed) continue;
    // A bare domain (e.g. "leafroute.org", no scheme) is invalid as an
    // absolute URL on its own — retry it with https:// prepended before
    // giving up on the candidate entirely.
    for (const attempt of [trimmed, `https://${trimmed}`]) {
      try {
        return new URL(attempt);
      } catch {
        // try the next attempt/candidate
      }
    }
  }
  return new URL("http://localhost:3000");
}

const siteUrl = resolveSiteUrl();

export const metadata: Metadata = {
  metadataBase: siteUrl,
  title: {
    default: "LeafRoute",
    template: "%s · LeafRoute",
  },
  description:
    "Every walk instead of a drive is climate action. LeafRoute routes you through shade and quiet streets around Melbourne, and tracks the emissions each walk avoids.",
  openGraph: {
    title: "LeafRoute",
    description:
      "Every walk instead of a drive is climate action. LeafRoute routes you through shade and quiet streets around Melbourne, and tracks the emissions each walk avoids.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "LeafRoute",
    description:
      "Every walk instead of a drive is climate action. LeafRoute routes you through shade and quiet streets around Melbourne.",
  },
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const host = (await headers()).get("host");
  const isMarketing = Boolean(host && APEX_HOSTS.includes(host));

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  let onboarded = false;
  if (user) {
    const { data: profile } = await supabase
      .from("profiles")
      .select("onboarded")
      .eq("id", user.id)
      .single();
    onboarded = profile?.onboarded ?? false;
  }

  return (
    <html
      lang="en"
      className={`${inter.variable} ${spaceGrotesk.variable} ${robotoMono.variable}`}
    >
      <body>
        <ToastProvider>
          {user && <ClaimPendingWalk />}
          {!isMarketing && user && (
            <div className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur-md">
              <div className="mx-auto flex max-w-5xl items-center justify-between px-5 py-4 sm:px-8 lg:px-12">
                <div className="flex items-center gap-2 font-display text-[1.05rem] font-semibold tracking-tight text-text">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-[22px] w-[22px] text-primary">
                    <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
                    <circle cx="12" cy="9.5" r="2.4" />
                  </svg>
                  LeafRoute
                </div>
                <div className="flex items-center gap-3">
                  {user.email && <UserMenu email={user.email} />}
                </div>
              </div>
            </div>
          )}
          {!isMarketing && user && onboarded && <NavTabs />}
          {children}
        </ToastProvider>
      </body>
    </html>
  );
}
