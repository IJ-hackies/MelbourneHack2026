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
  - api/route-planner.py
  - api/shade.py
  - api/_shared/model_loader.py
  - api/_shared/feature_lookup.py
  - supabase/migrations/20260823210000_community_impact_rpc.sql
links: [heatroute, software/frontend-shell, software/auth-persistence, ml/model-handoff]
verified: 7cdd997
---

## What this is

The private `leafroute` package uses Next.js 16.3.1, React 19.2.8, TypeScript,
Tailwind CSS 4, Supabase SSR/JS clients, Resend, ESLint 9, Playwright, and
`maplibre-gl`. GitHub Actions lint/build pull requests and run browser smoke
tests against a local Supabase stack; pushes to `main` deploy to Vercel.
(`package.json`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`)

The repo also has root-level Python Vercel Functions under `/api`: crowd and
traffic ML inference, real pedestrian routing (`route-planner.py`), and a
canopy-density point lookup (`shade.py`) — see `ml/model-handoff` and
`software/routing-boundary`. All four are declared in `vercel.json` with
per-function `excludeFiles` bundle-size hygiene. Dependencies come from a root
`requirements.txt` separate from `ml/requirements.txt` (CUDA training-only,
unusable at serving time). `requirements.txt` pins `xgboost-cpu` to the
**exact** version the promoted crowd model was trained with
(currently `3.4.1`) rather than the newest manylinux2014-wheel version — a
version mismatch previously produced silently-wrong (not failed) predictions;
see `software/routing-boundary`'s Invariants. Vercel's from-source build for
`xgboost-cpu` (it only ships `manylinux_2_28` wheels) now fits comfortably
under Vercel's current 5GB per-function package limit.

Schema changes go through `supabase/migrations/*.sql`, applied directly
against the production Postgres connection (`POSTGRES_URL_NON_POOLING` in
`.env.production.local`, provisioned by the Supabase Vercel integration) since
the CLI's authenticated account doesn't have this project linked — there is no
`supabase db push` step in CI for this repo.

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
