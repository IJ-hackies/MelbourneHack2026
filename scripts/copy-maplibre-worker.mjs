#!/usr/bin/env node
// MapLibre GL JS resolves its internal Web Worker script relative to its own
// `import.meta.url` at runtime — under Next.js/Turbopack bundling, that
// resolves to the bundled chunk's hashed URL, not the real package location,
// so the worker request 404s. The worker fails silently (no thrown error,
// just a request that never resolves), which breaks every GeoJSON source:
// features never get processed, `map.once("idle", ...)` never fires, and
// `querySourceFeatures()` stays empty forever — while raster tiles, which
// don't need the worker, keep rendering fine. This copies the real worker
// script to public/ so route-map.tsx can point MapLibre at a real,
// reachable URL via `maplibregl.setWorkerUrl(...)`.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const distDir = join(ROOT, "node_modules", "maplibre-gl", "dist");
const destDir = join(ROOT, "public");
mkdirSync(destDir, { recursive: true });

// The worker script itself imports a sibling "./maplibre-gl-shared.mjs" —
// both must be served from the same directory for that relative import to
// resolve, so both get copied here, not just the worker entry point.
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  const src = join(distDir, file);
  const dest = join(destDir, file);
  copyFileSync(src, dest);
  console.log(`Copied ${src} -> ${dest}`);
}
