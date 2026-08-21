---
name: reupdate
description: "Synchronize HeatRoute's Context/Chunks with current source: re-verify drifted chunks, repair indexes and coverage, and prune obsolete state. Use after code or architecture changes."
---

# HeatRoute context synchronizer

Keep `Context/Chunks/` aligned with HeatRoute's actual code. This is targeted
context maintenance, not a full audit. The Next.js 16/React 19 software
scaffold currently centers on `src/app/`; the ML workstream may be added later,
so only document source areas that exist. Code and configuration are ground
truth.

## 1. Detect

Run `npm run context:drift`, then read `Context/Chunks/CONVENTIONS.md` if it is
not already loaded. Collect `STALE`, `INVALID`, and `OVERSIZE` chunks plus any
source-coverage gaps found while working. Even when all chunks are fresh,
perform cheap index and coverage checks. Do not turn `STATE.md` into a
changelog.

## 2. Re-verify stale chunks

For each stale chunk, read the chunk and inspect git history/diffs for every
listed source. Decide whether behavior, contracts, invariants, routes, UI
behavior, configuration, or extension guidance changed. Update the body when
it did; otherwise preserve the body. In both cases set `verified:` to the
current short `HEAD` commit. If the repository still has no usable git history,
use `verified: initial` and record that limitation in `STATE.md`.

Never bump `verified` without checking the relevant source diff or current
source behavior.

## 3. Repair invalid chunks

- Replace missing source paths when the documented subject moved.
- Delete a chunk and its index entry when its subject no longer exists.
- Re-check malformed/missing `verified` values before fixing them.
- Make each frontmatter `id` match its path under `Context/Chunks/`.

## 4. Maintain coverage and indexes

Use frontmatter `sources` as the ownership map. Cover load-bearing files and
boundaries such as `src/app/**`, root Next/TypeScript/ESLint/Tailwind config,
package scripts, and any real ML or service source once introduced. Do not list
every file merely to silence a gap. If an important area is intentionally
uncovered, record why in `STATE.md` or its category index.

Parse `Context/Chunks/INDEX.md` dynamically. For a new or moved category,
create/update its sub-index and top-level line. Add or remove one sub-index line
for every chunk addition or deletion, and repair links after moves. Never
assume a fixed category list; the two-person software/ML split may evolve.

## 5. Prune state and verify

Keep `STATE.md` limited to actionable landmines, open follow-ups, deliberate
deferrals, and environment blockers. Remove closed debug history and stale
paths. Re-run `npm run context:drift` at the end. If source code changed, also
run the smallest relevant checks (`npm run lint` and `npm run build`; run a test
command only if the repository has one). Documentation-only maintenance needs
the drift check.

## Report

Report chunks updated or verification-bumped, chunks/categories added/moved/
deleted, state entries pruned/added, coverage gaps fixed or remaining, commands
and results, and uncertainties needing user judgment. Do not change app
behavior as part of context maintenance.

