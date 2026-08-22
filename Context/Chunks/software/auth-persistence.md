---
id: software/auth-persistence
title: Supabase authentication and user persistence
sources:
  - src/proxy.ts
  - src/lib/hosts.ts
  - src/lib/supabase/server.ts
  - src/lib/supabase/client.ts
  - src/lib/supabase/admin.ts
  - src/lib/actions/auth.ts
  - src/lib/actions/profile.ts
  - src/lib/actions/places.ts
  - src/lib/actions/searches.ts
  - src/lib/actions/walks.ts
  - src/lib/actions/account.ts
  - src/app/auth/callback/route.ts
  - src/app/forgot-password/page.tsx
  - src/app/reset-password/page.tsx
  - supabase/config.toml
  - supabase/migrations/20260821120231_init_profiles.sql
  - supabase/migrations/20260821120949_google_display_name_fallback.sql
  - supabase/migrations/20260821124453_create_walks.sql
  - supabase/migrations/20260821132136_create_saved_places.sql
  - supabase/migrations/20260821132728_create_recent_searches.sql
  - supabase/migrations/20260821133026_walks_allow_delete.sql
  - supabase/migrations/20260822010000_saved_places_atomic_slot_replace.sql
links: [heatroute, software/frontend-shell, software/tooling]
verified: a85a787
---

## What this is

Supabase supplies cookie-based auth and password reset plus per-user profiles,
saved places, recent searches, and completed walks. Server actions authenticate the caller
before mutations; migrations enable row-level security and owner-scoped
policies. (`src/lib/actions/`, `supabase/migrations/`)

## Key files

- `src/proxy.ts` - session refresh, host routing, account-only guards, auth-page
  redirects, and onboarding enforcement.
- `src/lib/supabase/{server,client}.ts` - cookie-scoped server and browser clients.
- `src/lib/supabase/admin.ts` - service-role client used for authenticated
  self-account deletion.
- `src/lib/actions/` - auth/account/profile/place/search/walk operations.
- `supabase/migrations/` - schema, trigger, grants, indexes, and RLS policy history.

## Invariants

- Never expose `SUPABASE_SECRET_KEY` to client code; admin operations must first
  establish the cookie-authenticated user and act only on that identity.
- User-owned tables keep RLS enabled and scope policies to `auth.uid()`.
- New accounts receive a profile through `handle_new_user`; the proxy redirects
  incomplete profiles to onboarding.
- Home and work are single slots replaced atomically through the
  `replace_saved_slot` RPC; favorites are not slot-limited.
- Walk emissions are computed by the server action from an illustrative
  `distanceKm * 0.19` factor, not a documented model. (`src/lib/actions/walks.ts`)

## How to extend

Add schema changes as forward migrations, then keep server actions, RLS grants,
and browser flows aligned. Add service-role operations only when a normal
owner-scoped client cannot perform the task and re-authenticate the caller first.

## Gotchas

- Guests may plan and open route details; only history, preferences, account,
  and onboarding require authentication. Unknown paths reach the application 404.
- The marketing/apex and app-subdomain behavior depends on exact hosts in
  `src/lib/hosts.ts`.
- Calendar suggestions are stored as a preference but no calendar integration
  exists.
- Supabase clients are not generated/typed against the database schema.
- Login and auth-callback `next` values are not constrained to safe local paths.
