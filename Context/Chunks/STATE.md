# Current State

- This repository has no Git commits yet. Initial chunks use `verified: initial`;
  commit-based freshness cannot be computed until the first commit exists.
- The only implementation is a Next.js/React/TypeScript/Tailwind scaffold. The
  home route renders `Hello world!`; there is no backend, API, map, pedestrian
  graph, persistence, external data client, model, or Python package yet.
- The project is intentionally split for two people: `software` is the active
  web-app lane, and `ml` is a planned forecasting/data-science lane with no
  source code. Do not move model work into the frontend package by assumption.
- Available commands are `npm run dev`, `npm run build`, `npm run start`,
  `npm run lint`, and `npm run context:drift`. There is no test or standalone
  type-check command yet.
- `npm run context:drift` validates normal chunk frontmatter, source paths,
  links, size, and Git freshness. Until the first commit, valid initial chunks
  report `UNVERIFIED` rather than `FRESH` because no commit baseline exists.

## Open threads

- Define the software/ML contract for time-indexed route-segment predictions,
  including missing data and confidence semantics.
- Decide where the future backend/data/model packages live and how they run.
- Choose initial Melbourne datasets, licences, coverage, and a narrow demo
  scenario before implementing the broad V1 brief.
- Decide the final client/deployment model and add tests once behavior exists.

## Landmines and deliberate deferrals

- `Context/Chunks/heatroute.md` is product intent as well as root context; its
  planned features are not implemented APIs or algorithms.
- MapLibre, FastAPI/Pydantic, routing, shade modelling, forecasting, history,
  and emissions calculations are deliberately deferred from the current shell.
- Calendar linking and automatic event-based trip suggestions are deferred.
  Manual route planning must not require calendar access; any future integration
  needs explicit consent, minimal permissions, and destination confirmation.
- There is no `backend`, `data`, or `infra` category yet because no real source
  area exists. Add one when implementation creates it; do not create empty
  architecture claims just to fill the index.
