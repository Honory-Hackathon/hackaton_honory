# hackaton_honory

Heat-aware trip planner for Śródmieście on the heat day 2026-06-28: shade from LiDAR → UTCI per walkable pixel and time step → walk + air-conditioned transit routes with the lowest heat dose.

- **Specification:** [`docs/specification-v1.0.md`](docs/specification-v1.0.md)
- **Task briefs and shared conventions** (grid, time steps, naming): [`tasks/README.md`](tasks/README.md)
- **Data:** bucket `gs://hackaton-honory-heat/<task-id>/`, BigQuery dataset `hackaton-honory.warsaw_heat` (GCP project `hackaton-honory`)

## Tasks

| Task | Code | Report | Status |
|---|---|---|---|
| T01 LiDAR → 1 m geometry | [`lidar/`](lidar/README.md) | [`reports/T01`](reports/T01/README.md) | done |
| T02 Shade engine → shade-zone shares, SVF | [`shade/`](shade/README.md) | [`reports/T02`](reports/T02/README.md) | production run in progress |
| T03 Walk network, walkable mask, stop areas | [`walk/`](walk/README.md) | [`reports/T03`](reports/T03/README.md) | done |
| T04 Recorded tram/bus stop events + AC | [`transit/`](transit/README.md) | [`reports/T04`](reports/T04/README.md) | done |
| T05 Weather forcing + Landsat `pmwc` | [`meteo/`](meteo/README.md) | [`reports/T05`](reports/T05/README.md) | done |
| T06 SOLWEIG pilot vs simple model | [`solweig_ref/`](solweig_ref/README.md) | [`reports/T06`](reports/T06/README.md) | report in progress |
