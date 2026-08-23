import { ImageResponse } from "next/og";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
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
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#7fd99a" strokeWidth="1.8">
            <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21Z" />
            <circle cx="12" cy="9.5" r="2.4" fill="#7fd99a" stroke="none" />
          </svg>
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
