---
id: software/frontend-shell
title: LeafRoute application experience
sources:
  - src/app/layout.tsx
  - src/app/page.tsx
  - src/app/route/[id]/page.tsx
  - src/app/history/page.tsx
  - src/app/preferences/page.tsx
  - src/components/marketing/marketing-page.tsx
  - src/components/destination-search.tsx
  - src/components/route-planner.tsx
  - src/components/route-map.tsx
  - src/components/active-walk.tsx
links: [heatroute, software/tooling, software/routing-boundary, software/auth-persistence]
verified: 7cdd997
---

## What this is

LeafRoute is a responsive Next.js App Router experience. The apex host serves
a climate-action-framed marketing page; app/preview/local hosts serve the
planner for guests and signed-in users. Signed-in users additionally get
history, preferences, saved places, and account areas. (`src/app/layout.tsx`,
`src/app/page.tsx`)

Route listing is now client-driven, not server-rendered: `page.tsx` only
server-fetches conditions/saved-places/recent-searches, and hands the
destination to `<RoutePlanner>` (`src/components/route-planner.tsx`), a client
component that resolves a real one-shot `getCurrentPosition` origin (falling
back to a static CBD default only on denial/timeout/unsupported) before
calling the new `/api/plan-routes` route handler, which wraps
`routeProvider.listRoutes` server-side. This replaced an earlier
server-rendered list that always used the static default origin regardless of
where the user actually was — every route (and the detail page) looked like
it started from a fixed CBD point until a page refresh happened to resolve
geolocation faster the second time. Clicking a route card only
selects/highlights it; a persistent "Start walking, N min" button reflects
the selected route and is the only thing that navigates, carrying the
resolved origin to `/route/[id]` via `originLat`/`originLon` query params so
it isn't re-guessed there.

`route/[id]/page.tsx` reads those `originLat`/`originLon` params (falling
back to the provider's default only for a direct/bookmarked link that skipped
the planner) and renders `RouteMap` (real MapLibre GL, `route-map.tsx`) plus
`ActiveWalk`. Since real 2-3-candidate routing now exists (see
`software/routing-boundary`), `geometry.path` is usually one of several real
routed paths; the straight-line case now only means routing was unavailable
for that query.

## Key files

- `src/app/layout.tsx` - fonts, metadata, toast boundary, signed-in header, and
  onboarding-aware navigation.
- `src/app/page.tsx` - host-sensitive marketing/planning split; server-fetches
  conditions/saved-places/recent-searches only, delegates routing to
  `<RoutePlanner>`.
- `src/components/route-planner.tsx` - client route list: geolocation
  resolution, loading skeleton, card selection state, and the "Start walking"
  navigation trigger.
- `src/app/route/[id]/page.tsx` - selected-route detail and walk-start surface;
  threads `originLat`/`originLon` through to the provider.
- `src/components/route-map.tsx` - MapLibre GL map with live-location tracking
  and mid-walk re-routing (see `software/routing-boundary`).
- `src/components/active-walk.tsx` - auto-completes on arrival (15m radius via
  live progress), no manual timer.
- `src/app/history/page.tsx`, `src/app/preferences/page.tsx` - persisted
  walking history (real cumulative avoided-emissions stat) and personalisation.
- `src/components/marketing/marketing-page.tsx` - public marketing page,
  explicitly framed as climate action (reduce emissions + adapt to heat), with
  a real cross-user community-impact counter (see `software/routing-boundary`
  for its data source).

## Invariants

- `src/app/page.tsx` is host-sensitive: apex domains always render marketing;
  app/preview/local hosts render planning for guests and signed-in users.
- Route UI must call the provider interfaces (via `/api/plan-routes`) rather
  than embed a routing backend contract directly.
- Guests and new users remain useful through defaults; there is currently no
  wiring from `preferences` into route queries (the field exists on
  `RouteQueryInput` but neither provider implementation reads it).
- Global styles and font variables enter through the root layout.

## How to extend

Replace/extend provider implementations behind `software/routing-boundary`;
keep page components dependent on provider-agnostic types. Changes to
authenticated pages must preserve the proxy/onboarding and
row-level-security boundaries in `software/auth-persistence`.

## Gotchas

- `RoutePlanner` requests geolocation once per mount (cached in a ref) and
  re-fetches `/api/plan-routes` whenever the destination changes — it does not
  re-prompt for permission on every destination change.
- The route id space is now `"fastest" | "shaded" | "quieter"`, not a fixed
  single constant — bookmarked old `/route/walking-route` links 404.
- The marketing/app host split has production-domain assumptions in
  `src/lib/hosts.ts`; preview and localhost behavior intentionally differ.
