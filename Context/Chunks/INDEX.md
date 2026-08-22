# Chunk Index

> `/recontext` always loads `INDEX.md`, `heatroute.md`, and `STATE.md`, then
> reads the sub-index for the matched category and pulls topic chunks on
> demand. See `CONVENTIONS.md` for the format and drift procedure.

This is a two-person project. The categories mirror the agreed split:

- `heatroute` - product vision, V1 scope, execution model, and the boundary between the two workstreams
- `software/` - the current Next.js/React/TypeScript application and its tooling -> `software/INDEX.md`
- `ml/` - dataset acquisition, crowd processing, model training/evaluation, and planned forecasting integration -> `ml/INDEX.md`

The software lane now has a Next.js application, Supabase authentication and
user-owned persistence, a Melbourne-bounded geocoding endpoint, and explicit
stub route/condition provider interfaces. The ML lane has canonical crowd and
traffic targets, feature tables, CUDA evaluations, and Git-LFS-backed promoted
models. The fixed-site software handoffs are documented, but inference adapters,
route-edge mapping, and a real pedestrian routing engine are not implemented.
Keep category names data-driven so future context skills do not assume a fixed
architecture.
