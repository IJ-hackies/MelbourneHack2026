# Chunk Index

> `/recontext` always loads `INDEX.md`, `heatroute.md`, and `STATE.md`, then
> reads the sub-index for the matched category and pulls topic chunks on
> demand. See `CONVENTIONS.md` for the format and drift procedure.

This is a two-person project. The categories mirror the agreed split:

- `heatroute` - product vision, V1 scope, execution model, and the boundary between the two workstreams
- `software/` - the current Next.js/React/TypeScript application and its tooling -> `software/INDEX.md`
- `ml/` - dataset acquisition, crowd processing, model training/evaluation, and planned forecasting integration -> `ml/INDEX.md`

The software lane has a Next.js application, Supabase authentication and
user-owned persistence, a Melbourne-bounded geocoding endpoint, and real
route/condition provider implementations (`software/routing-boundary`): a
real pedestrian routing graph, live weather/crowd/shade conditions, and the
crowd ML model serving into both conditions and route scoring. The ML lane
has canonical crowd and traffic targets, feature tables, CUDA evaluations,
and Git-LFS-backed promoted models. Traffic inference is implemented but not
wired into any provider; route-edge crowd scoring is a pragmatic real-time
sampling approximation, not a validated model. Keep category names
data-driven so future context skills do not assume a fixed architecture.
