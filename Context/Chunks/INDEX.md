# Chunk Index

> `/recontext` always loads `INDEX.md`, `heatroute.md`, and `STATE.md`, then
> reads the sub-index for the matched category and pulls topic chunks on
> demand. See `CONVENTIONS.md` for the format and drift procedure.

This is a two-person project. The categories mirror the agreed split:

- `heatroute` - product vision, V1 scope, execution model, and the boundary between the two workstreams
- `software/` - the current Next.js/React/TypeScript application and its tooling -> `software/INDEX.md`
- `ml/` - dataset acquisition plus planned forecasting and data-science work -> `ml/INDEX.md`

There is no backend, model, serving, or application routing implementation. The
ML lane now has a reproducible dataset catalog/downloader and a local raw-data
mirror. Keep category names data-driven so future context skills do not assume
a fixed architecture.
