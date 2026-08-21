# Current State

- Git `HEAD` is `afd0526`. The committed frontend/tooling chunks use that
  verification baseline. Root and ML chunks remain `verified: initial` because
  their new acquisition sources and context edits are not committed yet.
- The application is still a Next.js/React/TypeScript/Tailwind scaffold whose
  home route renders `Hello world!`; there is no backend, API, map integration,
  persistence, feature pipeline, model, or Python package.
- The `ml` lane now owns `ml/data/catalog.json` and
  `ml/scripts/fetch_datasets.py`. Core and extended raw snapshots are local and
  Git-ignored; they are inputs, not application or prediction behavior.
- Available commands are `npm run dev`, `npm run build`, `npm run start`,
  `npm run lint`, and `npm run context:drift`. There is no test or standalone
  type-check command yet.
- `npm run context:drift` validates normal chunk frontmatter, source paths,
  links, size, and Git freshness. Valid chunks whose current sources have no
  committed verification baseline report `UNVERIFIED` rather than `FRESH`.

## Open threads

- Define the software/ML contract for time-indexed route-segment predictions,
  including missing data, freshness, and confidence semantics.
- Design schema harmonisation and temporal/spatial joins, then choose the first
  narrow target (crowd, traffic, or microclimate) and evaluation split.
- Decide where future feature/model/backend packages live and how they run.
- Confirm publication/redistribution terms for the City hourly pedestrian data
  and obtain registered BOM access if BOM becomes a production dependency.
- Decide the final client/deployment model and add tests once behavior exists.

## Landmines and deliberate deferrals

- `Context/Chunks/heatroute.md` is product intent as well as root context; its
  planned features are not implemented APIs or algorithms.
- MapLibre, FastAPI/Pydantic, application routing, shade modelling,
  forecasting, history, and emissions calculations are still deferred. Raw
  source availability is not implementation evidence for any of them.
- The City pedestrian network is municipal and dated; Greater Melbourne needs
  the catalogued OSM/Vicmap supplements and explicit topology validation.
- The 12.98 GB City surface model remains manual. BOM public endpoints rejected
  the automated client with HTTP 403 and must not be bypassed silently.
- Calendar linking and automatic event-based trip suggestions are deferred.
  Manual route planning must not require calendar access; any future integration
  needs explicit consent, minimal permissions, and destination confirmation.
- There is no `backend` or `infra` category. Dataset acquisition remains owned
  by `ml/data-acquisition`; add a new category only for a real independent
  source area.
