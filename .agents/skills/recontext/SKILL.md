---
name: recontext
description: "Load HeatRoute's working context from Context/Chunks, check context drift, and route only the chunks relevant to a topic. Use before non-trivial changes or when joining an unfamiliar area."
---

# HeatRoute context loader

Load the smallest useful set of HeatRoute context before changing code. HeatRoute
is currently a Next.js 16 App Router frontend scaffold using React 19,
TypeScript, and Tailwind CSS 4. The project is intentionally split between a
software workstream and a future ML workstream; inspect the repository and the
chunk index to determine what is implemented before making claims about either.

The current software source root is `src/app/`. Root configuration and workflow
are defined by `package.json`, `next.config.ts`, `tsconfig.json`,
`eslint.config.mjs`, and `postcss.config.mjs`. The normal checks are:

- `npm run context:drift` - check chunk freshness and validity
- `npm run lint` - run ESLint
- `npm run build` - verify the production build

## Navigation

Treat categories as data, never as a fixed list. The top-level index is the
authority for which category directories exist.

1. Always read, in order:
   - `Context/Chunks/INDEX.md`
   - `Context/Chunks/heatroute.md`
   - `Context/Chunks/STATE.md`
2. Run `npm run context:drift`. Record `STALE`, `INVALID`, and `OVERSIZE`
   results. Stale context can guide exploration, but verify its claims against
   source and recommend `/reupdate` when anything is flagged.
3. If no topic was supplied in `$ARGUMENTS`, stop after the navigation set and
   drift check. Do not bulk-read every chunk.
4. If a topic was supplied, parse category names, summaries, chunk ids, and
   source paths from `Context/Chunks/INDEX.md` and the matching sub-indexes.
   Read only the relevant category sub-indexes and topic chunks. Follow a
   chunk's frontmatter `links` one hop when clearly useful. If index matching
   fails, search `Context/Chunks/` for the topic keywords and read the strongest
   hits.

Useful topic examples include a route such as `software`, `ml`, `frontend`, or
`infra`, but these are only routing hints: use the categories actually present
in the index. Do not invent an ML subsystem merely because the product plan
mentions one.

## Source ownership

Before editing an unfamiliar file, search all chunk frontmatter `sources` for
that path and load the owning chunk first. If it has no owner, state the context
gap and decide whether the change warrants adding or extending a chunk. Source
files remain the ground truth; chunks summarize contracts and traps.

## Report and handoff

Report the HeatRoute architecture in one paragraph, the chunks loaded and why,
topic-specific invariants, landmines/open threads from `STATE.md`, drift
results, and any context gaps. Pull more chunks on demand before touching an
unfamiliar area. If a change touches a listed source, update or re-verify its
chunk and bump `verified` in the same session.
