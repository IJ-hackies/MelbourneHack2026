import type { Metadata } from "next";
import { Inter, Space_Grotesk, Roboto_Mono } from "next/font/google";
import { NavTabs } from "@/components/nav-tabs";
import { UserMenu } from "@/components/user-menu";
import { ToastProvider } from "@/components/toast-provider";
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

export const metadata: Metadata = {
  title: {
    default: "HeatRoute",
    template: "%s · HeatRoute",
  },
  description: "Personalised walking routes for Melbourne.",
  openGraph: {
    title: "HeatRoute",
    description: "Personalised walking routes for Melbourne.",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "HeatRoute",
    description: "Personalised walking routes for Melbourne.",
  },
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
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
          {user && (
            <div className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur-md">
              <div className="mx-auto flex max-w-5xl items-center justify-between px-5 py-4 sm:px-8 lg:px-12">
                <div className="flex items-center gap-2 font-display text-[1.05rem] font-semibold tracking-tight text-text">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-[22px] w-[22px] text-primary">
                    <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
                    <circle cx="12" cy="9.5" r="2.4" />
                  </svg>
                  HeatRoute
                </div>
                <div className="flex items-center gap-3">
                  {user.email && <UserMenu email={user.email} />}
                </div>
              </div>
            </div>
          )}
          {user && onboarded && <NavTabs />}
          {children}
        </ToastProvider>
      </body>
    </html>
  );
}
