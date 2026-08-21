# Chunk Conventions

The chunk system is the working context for this project: one Markdown file per
module-level concept, hierarchically organized and selectively loaded. The code
is ground truth; chunks hold intent, contracts, invariants, extension points,
and traps that code cannot communicate quickly.

## Layout

`Context/Chunks/INDEX.md` is the top-level index and the source of truth for
dynamic categories. `Context/Chunks/heatroute.md` is the root product vision,
V1 scope, identity, and execution model. `Context/Chunks/STATE.md` is always loaded and holds
landmines, open threads, and deliberate deferrals. Each
`Context/Chunks/<category>/INDEX.md` lists that category's chunks.

## Chunk file format

Every normal chunk, including the root chunk, uses frontmatter:

```yaml
---
id: category/chunk-name
title: Human-readable title
sources:
  - path/from/repo/root.ts
links: [other/chunk]
verified: initial
---
```

The `id` must match the path under `Context/Chunks/` without `.md`.
`sources` are repo-root-relative, existing, load-bearing files. `links` point
to related chunk ids. `verified` records the commit at which the chunk was last
checked against its sources. Use `initial` only while a chunk's current sources
do not yet have a usable committed verification baseline; record that limitation
in `STATE.md`.

## Body and style

Use these sections in order, omitting empty sections:

- `## What this is`
- `## Key files`
- `## Invariants`
- `## How to extend`
- `## Gotchas`

Keep each chunk to about 150 lines or fewer. Prefer intent, contracts, data
flow, lifecycle, and traps over restating code. Every concrete implementation
claim should name a source path. Prefer fewer useful chunks over one chunk per
file.

## Two-person ownership

`software/` is owned by the current web-application workstream. `ml/` is owned
by the planned forecasting/data-science workstream. A source that crosses the
boundary should be listed in both relevant chunks, with the integration
contract described from each side. New categories are allowed only when real
source areas appear.

## Drift check

Run `npm run context:drift` from the repository root. The checker reads every
normal chunk while excluding navigation indexes, `STATE.md`, and
`CONVENTIONS.md`. It validates `id`, `title`, `sources`, `links`, and
`verified`; checks that sources and linked chunks exist; flags chunks over 150
lines; and compares source changes with the `verified` commit when Git history
is available.

`INVALID`, `STALE`, or `OVERSIZE` results exit non-zero. With no Git `HEAD`, a
valid `verified: initial` chunk reports `UNVERIFIED` and the command remains
successful because structural validation still completed. After the first
commit, `/reupdate` should inspect the sources and replace `initial` with the
current short commit hash.

## Definition of done

When a code change touches files in a chunk's `sources`, re-read the affected
code and update or re-verify that chunk in the same session. Repair category
indexes and links when chunks move. Run `npm run context:drift` before
finishing.
