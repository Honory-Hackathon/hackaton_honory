# hackaton_honory

Heat-aware trip planner for Śródmieście on the heat day 2026-06-28: shade from LiDAR → UTCI per walkable pixel and time step → walk + air-conditioned transit routes with the lowest heat dose.

- **Specification:** [`docs/specification.md`](docs/specification.md)
- **Task briefs and shared conventions** (grid, time steps, naming): [`tasks/README.md`](tasks/README.md)
- **Data:** bucket `gs://hackaton-honory-heat/<task-id>/`, BigQuery dataset `hackaton-honory.warsaw_heat` (GCP project `hackaton-honory`)

## Tasks

| Task | Code | Report | Status |
|---|---|---|---|
| T01 LiDAR → 1 m geometry | [`lidar/`](lidar/README.md) | [`reports/T01`](reports/T01/README.md) | done |
| T02 Shade engine → shade-zone shares, SVF | [`shade/`](shade/README.md) | [`reports/T02`](reports/T02/README.md) | done |
| T03 Walk network, walkable mask, stop areas | [`walk/`](walk/README.md) | [`reports/T03`](reports/T03/README.md) | done |
| T04 Recorded tram/bus stop events + AC | [`transit/`](transit/README.md) | [`reports/T04`](reports/T04/README.md) | done |
| T05 Weather forcing + Landsat `pmwc` | [`meteo/`](meteo/README.md) | [`reports/T05`](reports/T05/README.md) | done |
| T06 SOLWEIG: pilot, then the district run (the production thermal model) | [`solweig_ref/`](solweig_ref/README.md) | [`reports/T06`](reports/T06/README.md) | done: 27/27 district tiles + mosaic (report not yet committed by the T06 session) |
| T07 UTCI and Tmrt on walkable space (from district SOLWEIG) | [`utci/`](utci/) | [`reports/T07`](reports/T07/README.md) | done |
| T08 Heat per walk segment and per stop | [`heat/`](heat/) | [`reports/T08`](reports/T08/README.md) | done |
| T09 Heat-aware router (walk + AC trams/buses) | [`router/`](router/) | [`reports/T09`](reports/T09/README.md) | done |
| T10 App: time-slider map + planner ([live](https://heat-app-269831067246.europe-central2.run.app)) | [`app/`](app/README.md) | [`reports/T10`](reports/T10/README.md) | done |
| T11 Demo trips, baselines, dose-weight comparison | [`demo/`](demo/) | [`reports/T11`](reports/T11/README.md) | done |
| T12 Data layers for the visualisations | [`layers/`](layers/) | [`reports/T12`](reports/T12/README.md) | done |
| T13 Visualisations ([routes](https://heat-views-269831067246.europe-central2.run.app/routes/), [3D shadows](https://heat-views-269831067246.europe-central2.run.app/shadows3d/), [temperature](https://heat-views-269831067246.europe-central2.run.app/temperature/)) | [`views/`](views/) | [`reports/T13`](reports/T13/README.md) | done |

Integration and the final live check: [`reports/INTEGRATION`](reports/INTEGRATION/README.md), [`reports/FINAL`](reports/FINAL/README.md).
