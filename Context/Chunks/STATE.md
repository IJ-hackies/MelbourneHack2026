# Current State

- Git source baseline `8c14561` merges cached `origin/main` into the preserved
  ML work branch. The application is now LeafRoute: a Next.js/React/TypeScript/
  Tailwind app with Supabase auth, onboarding, preferences, saved places,
  recent searches, walk history, account controls, Nominatim geocoding, and
  Playwright smoke tests. There is still no map, pedestrian graph, real route
  scorer, prediction service, or fixed-site-to-route calibration.
- `src/lib/providers/route-provider.ts` and `condition-provider.ts` expose the
  software integration seam but return fixed fixtures and ignore query details.
  `src/app/page.tsx` parses geocoded coordinates but currently passes only the
  destination label into those providers.
- The `ml` lane owns `ml/data/catalog.json` and `ml/scripts/fetch_datasets.py`.
  Crowd snapshots live under `ml/crowd/datasets/`, traffic snapshots under
  `ml/traffic/datasets/`, and shared inputs under `ml/data/raw/`. All are local,
  Git-ignored inputs rather than application or prediction behavior.
- The crowd mirror includes the 12 January 2025 Internet Archive capture of the
  official City hourly CSV. It has 2,093,193 raw rows from 1 July 2021 to
  11 January 2025; its 1,116,223-row gap slice covers every day from
  1 November 2022 through 20 August 2024. Use only that slice when harmonising.
- `ml/scripts/build_crowd_dataset.py` now produces an ignored, validated
  7,295,962-row canonical hourly pedestrian-flow table plus 118 sensor coverage
  rows and a quality manifest. Its natural key is unique, all 659 gap days are
  present, and no missing hours are zero-imputed.
- `ml/scripts/build_crowd_training_datasets.py` produces ignored all-history
  (7,295,962 rows) and recent-enhanced (2,642,497 rows from 2023) feature tables,
  two 250-row CSV previews, and a manifest. Both use the same hourly-flow label.
- `ml/scripts/train_crowd_models.py` trains CUDA/CPU XGBoost Poisson models and
  a matched recent-window ablation. The 22 August 2026 CUDA run scored 296,087
  shared test keys: all history led at MAE 47.2055/RMSE 110.0230/deviance
  14.8839; matched recent scored 49.6585/118.0417/16.4785; recent enhanced
  scored 56.1154/127.0281/20.2527. Full evaluation artifacts remain ignored
  under `ml/crowd/training/evaluation/`; the byte-identical 65.8 MiB winner is
  tracked with Git LFS at `ml/crowd/models/all-history-v1/model.ubj` alongside
  its portable metadata and model card.
- NASA POWER regional weather is locally mirrored as 151,704 hourly rows from
  May 2009 through 20 August 2026. Provider fill values are null. Transport
  Activity contributes 22,347,087 positive-only five-minute rows through
  11 May 2026; its `Z` suffix is misleading and the feature builder treats its
  timestamps as Melbourne wall time.
- `ml/scripts/finalize_traffic_recovery.py` reused 23 immutable recovery
  partitions and excluded the complete 2023 input without a raw rebuild. The
  complete 2024–July 2026 target has 17,744,407 unique-key rows: 17,526,830
  SCATS and 217,577 reviewed Transport Activity rows, with no null/negative
  targets. All 24 recovery artifacts remain preserved.
- `ml/scripts/build_traffic_training_datasets.py` produced base and lag-enhanced
  tables with all 17,744,407 rows. The split is 6,813,016 train (2024),
  6,826,496 validation (2025), and 4,104,895 test (2026 through July); candidate
  keys/labels match. The corrected manifest allows 27 base and 36 lag predictors
  and excludes all same-hour diagnostics. The verified full feature run uses
  DuckDB with four threads and a 12 GiB memory limit.
- `ml/scripts/train_traffic_models.py` completed an uncapped CUDA XGBoost run for
  base/lag candidates across both source groups. Validation selected
  lag-enhanced for both. Held-out SCATS scores are MAE 81.4622/RMSE
  150.7749/deviance 13.7267 over 3,988,344 rows; Transport Activity scores are
  48.9760/97.0135/19.5501 over 116,551 rows. The promoted release is the
  source-stratified bundle under `ml/traffic/models/source-stratified-v1/`. It
  predates the corrected predictor allow-list: audited same-hour diagnostics
  account for about 0.37% of SCATS gain and about 28% of Transport Activity gain.
  Use SCATS as the primary hackathon signal and mark Transport Activity degraded.
- Application commands are `npm run dev`, `build`, `start`, `lint`, `test`,
  `context:drift`, and `supabase:start|stop|reset|status`. Crowd processing runs with
  `python ml/scripts/build_crowd_dataset.py`; its contract tests run with
  `python -m unittest ml.tests.test_build_crowd_dataset -v`. Feature processing
  runs with `python ml/scripts/build_crowd_training_datasets.py`; both test
  suites run via `python -m unittest ml.tests.test_build_crowd_dataset
  ml.tests.test_build_crowd_training_datasets -v`. There is no standalone
  TypeScript type-check command yet.
- Crowd fitting runs with `python ml/scripts/train_crowd_models.py`; its
  synthetic end-to-end contract is `python -m unittest
  ml.tests.test_train_crowd_models -v`.
- Traffic cleaning runs with `python ml/scripts/build_traffic_dataset.py`.
  Recovery finalization runs with `python ml/scripts/finalize_traffic_recovery.py
  --allow-zero-eligible-scats-date 2025-05-27`; feature construction uses
  `python ml/scripts/build_traffic_training_datasets.py`; CUDA fitting uses
  `python ml/scripts/train_traffic_models.py --device cuda`; promotion uses
  `python ml/scripts/promote_traffic_models.py`. Their focused tests live under
  the matching `ml.tests.test_*traffic*` modules.
- Promoted UBJSON files require Git LFS. Run `git lfs install` and `git lfs
  pull` after cloning; metadata is the feature/encoder authority. Crowd and
  traffic follow `crowd-inference/v1` and `traffic-inference/v1` in their
  `SOFTWARE_HANDOFF.md` files; `ml/model-handoff` records compute and publication.
- `npm run context:drift` validates normal chunk frontmatter, source paths,
  links, size, and Git freshness. Valid chunks whose current sources have no
  committed verification baseline report `UNVERIFIED` rather than `FRESH`.

## Open threads

- Define the software/ML contract for time-indexed route-segment predictions,
  including missing data, freshness, and confidence semantics.
- Carry geocoded coordinates through `RouteQueryInput`; the current plan and
  route-detail calls reduce destinations to labels before provider invocation.
- Replace the fixed route/condition providers and ephemeral active-walk timer;
  document the emissions factor before treating walk history as more than an
  illustrative estimate.
- Implement the documented crowd/traffic Python adapters and separately define
  route-edge calibration; do not pool intersection and countline scales.
- Investigate why recent-enhanced is 13.0% worse in MAE than the matched recent
  ablation; all history also reached the 2,500-round ceiling and may benefit
  from targeted tuning rather than a broad search.
- Define inference/retraining, uncertainty, freshness, and missing-data
  contracts around the promoted model; effective-dated sensor locations and
  route-edge mappings remain unresolved.
- Confirm publication/redistribution terms for the City hourly pedestrian data
  and obtain registered BOM access if BOM becomes a production dependency.
- Reconcile local Supabase auth URLs (currently port 3001) with Next/Playwright
  ports (3000/3100), add the referenced `supabase/seed.sql`, and align the smoke
  test's onboarding skip label with the rendered punctuation.
- Diagnose the default Turbopack production build's failure to resolve the
  installed `lightningcss-linux-x64-gnu` optional package; the same source
  currently builds successfully with `next build --webpack`.
- Validate login `next` redirects as local paths and validate preference ranges
  server-side before relying on form inputs.

## Landmines and deliberate deferrals

- `Context/Chunks/heatroute.md` is product intent as well as root context; its
  planned features are not implemented APIs or algorithms.
- MapLibre, FastAPI/Pydantic, a real pedestrian routing engine, shade modelling,
  traffic serving/route calibration, and environmental forecasting are still
  deferred. History exists, but emissions use a hard-coded illustrative
  `distanceKm * 0.19` factor. Offline fixed-site models do not imply route-level
  prediction.
- Transport Activity timestamps are Melbourne wall time despite a trailing
  `Z`; annual `02:55 -> 02:00` records occur exactly on the DST rollback date.
  Do not reinterpret them as UTC or traffic patterns shift by 10-11 hours.
- SCATS uses fixed AEST and `-1` means missing while `0` is real. The traffic
  cleaner omits all-missing site-hours and never turns source silence into zero.
- The traffic training release deliberately excludes all 2023 data. SCATS dates
  2024-09-30, 2025-02-28, and 2025-12-31 are publisher gaps; 2025-05-27 has
  source observations but no sites inside the configured City bbox.
- The promoted traffic v1 is a time-constrained prototype with known target-hour
  diagnostic leakage, especially for Transport Activity. Do not claim its
  metrics as leakage-safe or production performance; publish a corrected retrain
  under a new release version.
- The City pedestrian network is municipal and dated; Greater Melbourne needs
  the catalogued OSM/Vicmap supplements and explicit topology validation.
- The archived City hourly export overlaps the historical attachment and live
  slice, and overlapping revisions differ. The crowd builder applies strict
  date precedence; do not replace it with raw concatenation.
- The historical source mislabels all 12,240 September 2010 readings as hour 0.
  The builder reconstructs hours only under the verified 30-day × 24-block ×
  17-sensor ordering and flags every repaired row. The current source also has
  66,547 rows beyond its distinct publisher-id count, so source ids are not
  natural keys.
- Historical observation coordinates remain null, and current sensor metadata
  is not an effective-dated relocation history. Do not backfill it across time
  or treat point-flow counts as area crowd density.
- The enhanced table uses citywide, not street-level, microclimate and transport
  aggregates. All such measurements are shifted one hour; absent source rows
  stay null because Transport Activity publishes no explicit zeros.
- Seven 2026 test sensors do not appear in the training split. The trainer maps
  their categorical identity to the missing branch and reports their metrics
  separately; do not hide this cold-start result inside only an overall score.
- The fair comparison test period ends 11 May 2026 with Transport Activity
  coverage. Rows through 20 August remain `post_test`; do not claim them as
  enhanced holdout results. NASA POWER is regional/grid-scale weather, and its
  solar field is missing after 31 May 2026.
- The 12.98 GB City surface model remains manual. BOM public endpoints rejected
  the automated client with HTTP 403 and must not be bypassed silently.
- Calendar linking and automatic event-based trip suggestions are deferred.
  Manual route planning must not require calendar access; any future integration
  needs explicit consent, minimal permissions, and destination confirmation.
- ESLint currently traverses ignored `.venv` JavaScript and reports three
  scikit-learn generated-file warnings; exclude the environment from lint scope.
- There is no `backend` or `infra` category. Dataset acquisition remains owned
  by `ml/data-acquisition`; add a new category only for a real independent
  source area.
