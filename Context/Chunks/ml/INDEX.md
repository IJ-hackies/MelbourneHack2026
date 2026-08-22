# ML Chunks

This category is the ML/data-science half of the two-person project. It now owns
reproducible source acquisition, a canonical crowd-target transformation, two
crowd training feature tables, CUDA-capable crowd model training/evaluation,
a promoted Git-LFS-backed crowd model, a canonical traffic-target cleaner,
two traffic feature tables, CUDA traffic training/evaluation, a promoted
source-stratified traffic bundle, separate domain workspaces, and the
forecasting/software boundary.

- `ml/planned-forecasting` - intended crowd, traffic, and future-environment forecasting boundary -> `planned-forecasting.md`
- `ml/data-acquisition` - official dataset catalog, local raw mirror, licences, and fetch workflow -> `data-acquisition.md`
- `ml/crowd-processing` - canonical pedestrian-flow target, source precedence, repair, validation, and quality contract -> `crowd-processing.md`
- `ml/crowd-training` - two leakage-aware feature tables, previews, and their chronological comparison contract -> `crowd-training.md`
- `ml/crowd-modeling` - XGBoost training, matched ablation, metrics, models, and evaluation artifacts -> `crowd-modeling.md`
- `ml/model-handoff` - ready crowd/vehicle releases, software contracts, compute envelope, and publication boundary -> `model-handoff.md`
- `ml/traffic-processing` - reviewed road-countline and SCATS cleaning, hourly target schema, quality, coverage, and artifact contract -> `traffic-processing.md`
- `ml/traffic-training` - leakage-safe base/lag feature tables, bounded DuckDB build, and chronological split -> `traffic-training.md`
- `ml/traffic-modeling` - source-stratified CUDA XGBoost fitting, validation selection, evaluation, and release -> `traffic-modeling.md`

The repository has `ml/crowd/` and `ml/traffic/` domain workspaces plus shared
snapshots under `ml/data/raw/`. Crowd processing now produces an ignored,
validated 7,295,962-row hourly target and two ignored training feature tables.
The local CUDA run produced three variants and a shared-test evaluation. The
winning all-history model is promoted at
`ml/crowd/models/all-history-v1/model.ubj`; other run artifacts stay ignored.
Traffic recovery finalization produces a complete ignored 17,744,407-row
2024–2026 target while preserving and excluding all 2023 recovery data. Both
traffic feature candidates retain every row; the full CUDA run selected
lag-enhanced models for SCATS intersections and Transport Activity countlines,
promoted together under `ml/traffic/models/source-stratified-v1/`. Versioned
fixed-site crowd and traffic handoffs plus a measured serving-compute envelope
are documented, but there is still no backend adapter or route-edge mapping.
