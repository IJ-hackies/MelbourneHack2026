import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// A light-mint recolor of the real brand mark (public/brand/leafroute-mark.png)
// -- its teal fill would nearly disappear against this dark gradient, so this
// variant swaps the leaf silhouette to a lighter tone while leaving the
// route/pin detail as the transparent cutout it already is in the source
// artwork, letting the gradient show through it directly.
async function loadLogoDataUri(): Promise<string> {
  const bytes = await readFile(join(process.cwd(), "public", "brand", "leafroute-mark-light.png"));
  return `data:image/png;base64,${bytes.toString("base64")}`;
}

export default async function OpengraphImage() {
  const logoSrc = await loadLogoDataUri();
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #0e2a1c 0%, #123524 55%, #163f2b 100%)",
          color: "#f4f7f2",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <img src={logoSrc} width={44} height={44} alt="" />
          <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: "-0.01em" }}>LeafRoute</div>
        </div>
        <div
          style={{
            display: "flex",
            marginTop: 48,
            fontSize: 64,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: "-0.02em",
            maxWidth: 920,
          }}
        >
          Every walk instead of a drive is climate action.
        </div>
        <div style={{ display: "flex", marginTop: 28, fontSize: 28, color: "#c7d6cb", maxWidth: 820 }}>
          Shade- and crowd-aware walking routes for Melbourne.
        </div>
      </div>
    ),
    { ...size }
  );
}
