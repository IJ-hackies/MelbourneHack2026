import type { MetadataRoute } from "next";

// Every route requires a signed-in session, so there's nothing here for a
// crawler to usefully index — disallow everything rather than leaving a
// default robots.txt that implies the opposite.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
