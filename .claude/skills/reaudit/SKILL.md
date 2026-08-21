---
name: reaudit
description: "Deep-audit HeatRoute code and its Context/Chunks for runtime, reliability, security, architecture, UI, ML-boundary, and drift issues. Use for reaudit, code audits, deep reviews, or quality sweeps."
---

# HeatRoute architecture audit

Perform a deep, evidence-backed audit of HeatRoute. This audit is read-only:
do not edit application code, chunks, `STATE.md`, package files, or git history.
Report findings for the user to choose and fix later.

HeatRoute is currently a Next.js 16 App Router/React 19 TypeScript frontend
scaffold with Tailwind CSS 4, centered on `src/app/`. It is a two-person
project with separate software and ML workstreams; distinguish implemented
code from planned ML integration and do not report an absent planned subsystem
as a defect. Current checks are `npm run context:drift`, `npm run lint`, and
`npm run build`; there is no test script unless the repository adds one.

## Phase 1: load context

1. Read `Context/Chunks/INDEX.md`, `Context/Chunks/heatroute.md`, and
   `Context/Chunks/STATE.md`.
2. Run `npm run context:drift` and record all `STALE`, `INVALID`, and
   `OVERSIZE` results.
3. Parse categories dynamically from `Context/Chunks/INDEX.md`, then read every
   existing category `INDEX.md`. Never assume a fixed frontend/backend/ML/etc.
   list.
4. Skim the fastest source inventory available, prioritizing `src/app/**`,
   `package.json`, `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`,
   `postcss.config.mjs`, and any actual ML/service roots.
5. Treat known `STATE.md` items as context, not findings, unless current code
   contradicts them.

## Phase 2: audit lanes

Partition work by the real category indexes and source ownership. If subagents
are available, give each lane its assigned chunk(s) before source files,
repository root, architecture summary, issue categories, and the instruction
that known `STATE.md` items are not findings. Otherwise audit each category
yourself.

Check for:

- chunk claims contradicted by code or important invariants missing from chunks
- missing imports and runtime-only failures beyond lint/type checks
- unsafe file/network I/O, TOCTOU races, and unhandled parse failures
- secret leaks, unsafe IDs/tokens/sessions, or auth/permission bypasses when
  those boundaries exist
- swallowed promise failures and stream, timer, watcher, socket, or subscription
  leaks
- schema or contract drift between any ML/service payloads and the UI
- server/client boundary mistakes, stale state, rendering, accessibility, and
  browser security bugs in `src/app/**`
- configuration, build/deployment, dependency, and external-integration risks
- undocumented dead branches, inconsistent parallel implementations, and
  source areas missing chunk ownership

Verify suspicious findings by reading the cited code. Do not report guesses.

Use this format for every finding:

- File: repository-relative path
- Lines: approximate line range
- Severity: CRITICAL / IMPORTANT / MINOR / NITPICK
- Category: invariant violation / security / race / leak / chunk drift / UI
  state / configuration / other
- Description: what is wrong and why it matters
- Suggested fix: concrete change (do not apply it during this audit)

## Phase 3: aggregate and report

Deduplicate overlapping findings, drop items already recorded in `STATE.md`
unless code contradicts them, and personally validate every CRITICAL or
IMPORTANT finding. Group the final report as:

## Audit Results

Summary: X findings across Y files
- CRITICAL: N
- IMPORTANT: N
- MINOR: N
- NITPICK: N

Then list findings with file, lines, category, issue, and fix. Include meaningful
test gaps. End with drift/check results, uncovered areas, and explicit
uncertainties. Keep the audit read-only until the user selects fixes.

