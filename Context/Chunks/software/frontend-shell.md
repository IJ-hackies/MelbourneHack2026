---
id: software/frontend-shell
title: Next.js frontend shell
sources:
  - src/app/layout.tsx
  - src/app/page.tsx
  - src/app/globals.css
links: [heatroute, software/tooling]
verified: initial
---

## What this is

The current software surface is the Next.js App Router shell. The root layout
sets `lang="en"`, exposes HeatRoute metadata, imports global CSS, and renders
the route's `children` in `src/app/layout.tsx`. The home route in
`src/app/page.tsx` is a server component with only a placeholder `main` and
`Hello world!` text. (`src/app/layout.tsx`, `src/app/page.tsx`)

## Key files

- `src/app/layout.tsx` - root HTML/layout boundary and page metadata.
- `src/app/page.tsx` - `/` route entry point.
- `src/app/globals.css` - global stylesheet entry; currently only imports Tailwind CSS.

## Invariants

- The root layout must continue to render `children`; route content belongs in
  the App Router tree. (`src/app/layout.tsx`)
- Global styles are loaded through the root layout's `./globals.css` import.
  New global styling should enter through that file rather than bypassing the
  layout. (`src/app/layout.tsx`, `src/app/globals.css`)
- There is no client state, map provider, route API, or design system here yet.
  Do not infer an existing contract from the placeholder page.

## How to extend

Build the first software experience from `src/app/page.tsx`, then introduce
route folders and focused components as the UI grows. Use a client component
only where browser state or event handlers require it; keep data and model
integration behind an explicit contract with the ML workstream. Add a chunk when
a new module becomes load-bearing.

## Gotchas

- The page is deliberately minimal, so visual or behavioral changes are new
  product work rather than maintenance of an established UI.
- `globals.css` is not a complete theme; it is only the Tailwind entry point.
- The root context's MapLibre, FastAPI, Pydantic, route scoring, and history
  features are planned ideas, not imports or dependencies in this shell.
