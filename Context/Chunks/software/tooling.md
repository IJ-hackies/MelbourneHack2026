---
id: software/tooling
title: Software package, test, and delivery tooling
sources:
  - package.json
  - package-lock.json
  - next.config.ts
  - tsconfig.json
  - eslint.config.mjs
  - postcss.config.mjs
  - README.md
  - playwright.config.ts
  - tests/global-setup.ts
  - tests/helpers.ts
  - tests/smoke.spec.ts
  - .env.example
  - .github/workflows/ci.yml
  - .github/workflows/deploy.yml
  - scripts/context-drift.mjs
  - .agents/skills/recontext/SKILL.md
  - .agents/skills/reupdate/SKILL.md
  - .agents/skills/reaudit/SKILL.md
  - .claude/skills/recontext/SKILL.md
  - .claude/skills/reupdate/SKILL.md
  - .claude/skills/reaudit/SKILL.md
links: [heatroute, software/frontend-shell, software/auth-persistence]
verified: a85a787
---

## What this is

The private `leafroute` package uses Next.js 16.3.1, React 19.2.8, TypeScript,
Tailwind CSS 4, Supabase SSR/JS clients, Resend, ESLint 9, and Playwright.
GitHub Actions lint/build pull requests and run browser smoke tests against a
local Supabase stack; pushes to `main` deploy to Vercel. (`package.json`,
`.github/workflows/ci.yml`, `.github/workflows/deploy.yml`)

## Key files

- `package.json`, `package-lock.json` - application dependencies and npm scripts.
- `next.config.ts` - Turbopack root, Codex agent-rules opt-out, and dev origins.
- `playwright.config.ts`, `tests/` - Chromium smoke flow using a local app and
  Supabase instance, covering guest planning, account guards, and signed-in flow.
- `.github/workflows/ci.yml` - Node 24 lint/build and local-Supabase E2E jobs.
- `.github/workflows/deploy.yml` - Vercel production build/deploy on `main`.
- `scripts/context-drift.mjs` and context skills - chunk validation/maintenance.

## Invariants

- Run commands from the repository root; Turbopack resolves from
  `process.cwd()`. (`next.config.ts`)
- Commands include `dev`, `build`, `start`, `lint`, `test`, `context:drift`, and
  local `supabase:start|stop|reset|status`. (`package.json`)
- TypeScript remains strict/no-emit and supports `@/*` -> `./src/*`.
- CI browser tests require the Supabase CLI/Docker stack and Chromium, while
  lint/build use placeholder public Supabase values. (`.github/workflows/ci.yml`)

## How to extend

Keep dependency and lockfile changes together. Add browser coverage to the
existing Playwright setup and schema behavior through ordered migrations.
Preserve the context skill mirrors when changing context tooling.

## Gotchas

- `README.md` still describes an empty scaffold and omits Supabase, Playwright,
  auth environment variables, and the expanded npm scripts.
- `package-lock.json` still records the root package name as `heatroute` while
  `package.json` is `leafroute`.
- Production deployment depends on repository secrets and Vercel project/team
  identifiers configured in the workflow.
- The smoke-test credentials are for the local Supabase instance only; never
  reuse them for a hosted environment.
