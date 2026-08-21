---
id: software/tooling
title: Software package and build tooling
sources:
  - package.json
  - package-lock.json
  - next.config.ts
  - tsconfig.json
  - eslint.config.mjs
  - postcss.config.mjs
  - README.md
  - scripts/context-drift.mjs
  - .agents/skills/recontext/SKILL.md
  - .agents/skills/reupdate/SKILL.md
  - .agents/skills/reaudit/SKILL.md
  - .claude/skills/recontext/SKILL.md
  - .claude/skills/reupdate/SKILL.md
  - .claude/skills/reaudit/SKILL.md
links: [heatroute, software/frontend-shell]
verified: afd0526
---

## What this is

The software package is a private npm project named `heatroute`. It uses
Next.js 16.3.1, React 19.2.8, TypeScript, Tailwind CSS 4, ESLint 9, and the
Next TypeScript/core-web-vitals configurations. (`package.json`,
`eslint.config.mjs`, `postcss.config.mjs`)

## Key files

- `package.json`, `package-lock.json` - runtime, build, quality, context-check
  scripts, and the reproducible npm dependency graph.
- `next.config.ts` - Next config; sets Turbopack's root to `process.cwd()`.
- `tsconfig.json` - strict TypeScript, bundler resolution, no emit, and `@/*` -> `./src/*`.
- `eslint.config.mjs` - Next core-web-vitals and TypeScript rules with generated-output ignores.
- `postcss.config.mjs` - Tailwind PostCSS plugin.
- `README.md` - local setup and command reference.
- `scripts/context-drift.mjs` - validates chunk metadata, links, sources, size, and Git freshness.
- `.agents/skills/*/SKILL.md` - canonical Codex definitions for loading,
  updating, and auditing the chunk system.
- `.claude/skills/*/SKILL.md` - identical Claude Code mirrors of those skills.

## Invariants

- Run npm commands from the repository root. (`next.config.ts`, `README.md`)
- The currently defined commands are `npm run dev`, `npm run build`,
  `npm run start`, `npm run lint`, and `npm run context:drift`; there is no test
  or standalone type-check script yet. (`package.json`, `README.md`)
- TypeScript is strict, emits no files, and supports the `@/*` source alias.
  New imports should respect that configuration. (`tsconfig.json`)
- Generated `.next`, `out`, `build`, and `next-env.d.ts` artifacts are ignored
  by the ESLint configuration. (`eslint.config.mjs`)

## How to extend

Add dependencies and scripts only when a concrete software feature needs them,
and keep the lockfile aligned with `package.json`. Add tests/type-checking as
the app gains behavior, then document the new commands in `README.md` and this
chunk. Keep future ML dependencies in the ML workstream rather than coupling
them to the browser package. Treat `.agents/skills/` as canonical; when context
tooling changes, mirror each `SKILL.md` into `.claude/skills/` and keep the
drift-check behavior aligned.

## Gotchas

- `next.config.ts` uses the current working directory as the Turbopack root;
  invoking commands from a parent directory can change resolution behavior.
- The current ESLint ignores generated files but does not establish a test
  strategy or runtime validation.
- MapLibre, FastAPI, Pydantic, weather/sensor clients, and model runtimes are
  not installed in this package. (`package.json`)
- `.agents/skills/*/agents/openai.yaml` files are skill presentation metadata,
  not HeatRoute runtime or context-contract sources; keep them outside module
  ownership unless their behavior becomes independently load-bearing.
