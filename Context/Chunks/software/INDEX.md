# Software Chunks

This category covers the current web application and the configuration needed
to run, lint, and build it. It is the active workstream for the software half of
the two-person project.

- `software/frontend-shell` - LeafRoute layout, marketing/home experiences, route pages, and styling -> `frontend-shell.md`
- `software/routing-boundary` - geocoding, real routing/ML-adapter providers, and live conditions -> `routing-boundary.md`
- `software/auth-persistence` - Supabase auth, onboarding, user-owned data, server actions, and proxy guards -> `auth-persistence.md`
- `software/tooling` - package scripts, framework/CI configuration, Playwright, and local Supabase workflow -> `tooling.md`

Real pedestrian routing (`ml/routing/` + `api/route-planner.py`), a MapLibre
map, and live weather/crowd/shade conditions are all implemented — see
`software/routing-boundary`. Traffic remains unwired into any provider.
