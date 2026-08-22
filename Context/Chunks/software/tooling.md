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
  - requirements.txt
  - vercel.json
  - .python-version
  - api/crowd-inference.py
  - api/traffic-inference.py
  - api/_shared/model_loader.py
  - api/_shared/feature_lookup.py
links: [heatroute, software/frontend-shell, software/auth-persistence, ml/model-handoff]
verified: edcfab5
---

## What this is

The private `leafroute` package uses Next.js 16.3.1, React 19.2.8, TypeScript,
Tailwind CSS 4, Supabase SSR/JS clients, Resend, ESLint 9, Playwright, and
`maplibre-gl`. GitHub Actions lint/build pull requests and run browser smoke
tests against a local Supabase stack; pushes to `main` deploy to Vercel.
(`package.json`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`)

The repo also has root-level Python Vercel Functions under `/api` (crowd and
traffic ML inference — see `ml/model-handoff`), deployed by the same
`vercel build`/`vercel deploy` pipeline as the Next.js app, with dependencies
declared in a root `requirements.txt` separate from `ml/requirements.txt`
(which pins CUDA training-only packages unusable at serving time).

## Key files

- `package.json`, `package-lock.json` - application dependencies and npm scripts.
- `next.config.ts` - Turbopack root, Codex agent-rules opt-out, and dev origins.
- `playwright.config.ts`, `tests/` - Chromium smoke flow using a local app and
  Supabase instance, covering guest planning, account guards, and signed-in flow.
- `.github/workflows/ci.yml` - Node 24 lint/build and local-Supabase E2E jobs,
  plus an additive `python-check` job that compiles the `/api` functions and
  sanity-imports `xgboost`/`pandas`/`numpy` (no Git LFS pull here — it doesn't
  need real model bytes, just import/syntax validity).
- `.github/workflows/deploy.yml` - Vercel production build/deploy on `main`;
  its checkout now has `lfs: true` so `model.ubj` files are real bytes before
  `vercel build` runs (required — the inference functions refuse to load a
  file whose SHA-256/byte count doesn't match its recorded checksum).
- `requirements.txt`, `.python-version`, `vercel.json` - root-level Python
  Vercel Function config (`api/**/*.py`, minimal `xgboost`/`pandas`/`numpy`
  deps, Python 3.12, and `excludeFiles` bundle-size hygiene excluding the
  offline `ml/*/datasets|processed|training` directories).
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
