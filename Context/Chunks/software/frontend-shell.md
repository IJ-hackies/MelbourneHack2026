---
id: software/frontend-shell
title: LeafRoute application experience
sources:
  - src/app/layout.tsx
  - src/app/page.tsx
  - src/app/globals.css
  - src/app/route/[id]/page.tsx
  - src/app/history/page.tsx
  - src/app/preferences/page.tsx
  - src/components/marketing/marketing-page.tsx
  - src/components/destination-search.tsx
  - src/components/nav-tabs.tsx
  - src/components/route-map.tsx
links: [heatroute, software/tooling, software/routing-boundary, software/auth-persistence]
verified: edcfab5
---

## What this is

LeafRoute is a responsive Next.js App Router experience. The apex host serves
marketing, while the application planner and route detail/start-walk flow work
for guests. Signed-in users additionally receive history, preferences, saved
places, and account areas.
The root layout reads Supabase auth/profile state to render authenticated
navigation. (`src/app/layout.tsx`, `src/app/page.tsx`)

The route detail page renders a real MapLibre GL map (`src/components/route-map.tsx`)
in place of the former placeholder SVG. Tile style is MapTiler Streets when
`NEXT_PUBLIC_MAPTILER_KEY` is set, falling back to keyless OpenFreeMap
otherwise (both plugged into `MapLibre`, not a library swap). Since
`ml/routing/` + `api/route-planner.py` now exist (see `software/routing-boundary`),
`geometry.path` is usually a real routed path, not just a straight line
between `start`/`end` — the straight-line case now only means routing
was unavailable for that query.
Destination coordinates now flow end-to-end from `DestinationSearch` through
`page.tsx` and `/route/[id]` into the provider calls; a destination label
without resolved coordinates is treated as "no destination yet" rather than
being passed through. (`src/app/page.tsx`, `src/app/route/[id]/page.tsx`)

## Key files

- `src/app/layout.tsx` - fonts, metadata, toast boundary, signed-in header, and
  onboarding-aware navigation.
- `src/app/page.tsx` - auth-sensitive marketing/planning split and composition
  of search, saved places, conditions, and route choices.
- `src/app/route/[id]/page.tsx` - selected-route detail and walk-start surface.
- `src/app/history/page.tsx`, `src/app/preferences/page.tsx` - persisted walking
  history and personalisation surfaces.
- `src/components/marketing/marketing-page.tsx` - public product/marketing page.
- `src/app/globals.css` - LeafRoute colour and typography tokens plus focus rules.

## Invariants

- `src/app/page.tsx` is host-sensitive: apex domains always render marketing;
  app/preview/local hosts render planning for guests and signed-in users.
- Route and condition UI must call the provider interfaces rather than embed a
  future routing or ML backend contract directly. (`src/app/page.tsx`)
- Guests and new users remain useful through defaults, while persisted preferences may be
  passed into route queries. (`src/app/page.tsx`, `src/app/preferences/page.tsx`)
- Global styles and font variables enter through the root layout.

## How to extend

Replace provider implementations behind `software/routing-boundary`; keep page
components dependent on provider-agnostic types. Add route/map state only when
a real routing contract exists. Changes to authenticated pages must preserve
the proxy/onboarding and row-level-security boundaries in
`software/auth-persistence`.

## Gotchas

- The three route options are still fixed fixtures (minutes/distance/segments
  unaffected by destination/preferences/departure time), but `geometry` now
  reflects the real resolved origin/destination coordinates.
- The "Feels hot" condition is now live (Open-Meteo via `/api/weather`, see
  `software/routing-boundary`); crowds/shade conditions remain fixed
  placeholders — no shade-geometry service exists, and crowd ML signals are
  not wired into the condition list yet (see `ml/model-handoff` for why).
- The marketing/app host split has production-domain assumptions in
  `src/lib/hosts.ts`; preview and localhost behavior intentionally differ.
