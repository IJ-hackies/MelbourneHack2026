# Chunk Index

> `/recontext` always loads `INDEX.md`, `heatroute.md`, and `STATE.md`, then
> reads the sub-index for the matched category and pulls topic chunks on
> demand. See `CONVENTIONS.md` for the format and drift procedure.

This is a two-person project. The categories mirror the agreed split:

- `heatroute` - product vision, V1 scope, execution model, and the boundary between the two workstreams
- `software/` - the current Next.js/React/TypeScript application and its tooling -> `software/INDEX.md`
- `ml/` - planned forecasting and data-science work; no ML source exists yet -> `ml/INDEX.md`

There is no backend, data, routing, map, or model implementation in the current
repository. Add a category only when a real source area appears; keep category
names data-driven so future context skills do not assume a fixed architecture.
