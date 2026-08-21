import type { MetadataRoute } from "next";

// "/" is the public marketing page; everything else requires a signed-in
// session and has nothing useful for a crawler to index.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/history",
        "/preferences",
        "/account",
        "/onboarding",
        "/route/",
        "/api/",
      ],
    },
    sitemap: `${process.env.NEXT_PUBLIC_SITE_URL ?? "https://leafroute.org"}/sitemap.xml`,
  };
}
