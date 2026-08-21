import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HeatRoute",
  description: "Personalised walking routes for Melbourne.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
