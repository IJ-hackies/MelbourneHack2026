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
links: [heatroute, software/tooling, software/routing-boundary, software/auth-persistence]
verified: a85a787
---

## What this is

LeafRoute is a responsive Next.js App Router experience. The apex host serves
marketing, while the application planner and route detail/start-walk flow work
for guests. Signed-in users additionally receive history, preferences, saved
places, and account areas.
The root layout reads Supabase auth/profile state to render authenticated
navigation. (`src/app/layout.tsx`, `src/app/page.tsx`)

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

- The three route options and condition cards look functional but are fixed
  fixtures; destination, preferences, and departure time do not affect them.
- There is no map renderer or live route geometry.
- The marketing/app host split has production-domain assumptions in
  `src/lib/hosts.ts`; preview and localhost behavior intentionally differ.
