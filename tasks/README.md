# Tasks: first wave

These tasks implement [specification v1.0](../docs/specification-v1.0.md). Each file is a self-contained brief for one person or agent session. They describe the goal, the data with its known quirks, the constraints and the expected output. They do not prescribe methods: exploring the data and choosing the approach is part of each task.

## First wave: start now, in parallel

The six tasks below were chosen because together they cover every input the UTCI field and the router need, and **none depends on another's output**. Where one later consumes another's result, the hand-over format is fixed in the shared conventions below, so each task can be built and tested on its own.

| Task | Aspect | Why it starts first |
|---|---|---|
| [T01](T01_lidar_geometry.md) | LiDAR → 1 m geometry (ground, buildings, canopy, crown light transmission) | Largest download and longest processing; everything about shade depends on it |
| [T02](T02_shade_engine.md) | Shade engine: sun position + shadow casting → shade zone shares per time step | Core of decision 1 in the spec; the algorithm can be built and tested on any small elevation-model tile |
| [T03](T03_walk_network_stops.md) | Walkable space, walk network and stop waiting areas | Defines *where* UTCI is evaluated and *what* the router walks on |
| [T04](T04_transit_ac_events.md) | Recorded tram and bus operations from BigQuery → stop events + AC per run | Independent data engineering; the transit half of the router |
| [T05](T05_meteo_landsat.md) | Weather forcing for 2026-06-28 + Landsat surface-heat anomaly (`pmwc`) | Inputs of the thermal model; small and quick |
| [T06](T06_solweig_reference.md) | SOLWEIG (simplest setup) on a pilot area vs the proposed simple model | De-risks the physics early; tunes the proposal's coefficients |

## Later waves (depend on the first wave; do not start yet)

- **T07 UTCI field:** shade shares (T02 on T01) × forcing and `pmwc` (T05) × thermal model (spec §13, tuned in T06) → Tmrt and UTCI per walkable pixel and time step.
- **T08 Aggregation:** UTCI and dose per walk segment and per stop and time step (T07 + T03).
- **T09 Router:** time-dependent walk + transit search minimising heat dose, with best departure time (T08 + T04).
- **T10 App:** map with time slider and route cards.
- **T11 Validation and demo:** demo origin–destination pairs, baselines (ORS Shaded, building-only shade), heat-dose definition workshop.

## Shared conventions (the hand-over contract)

| Item | Convention |
|---|---|
| Area of interest | Śródmieście, PRG unit `146510_8`, from WFS `https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries`, layer `A05_Granice_jednostek_ewidencyjnych`. ≈15.5 km². |
| Halo | AOI buffered by 1,000 m. Used only for shadow casters. |
| CRS | EPSG:2180 (PL-1992), heights EVRF2007. Web outputs may additionally be in EPSG:4326. |
| Grid | 1 m cells. Extent = halo bounding box snapped outward to multiples of 1,000 m. Cell edges on integer metres. Every raster product uses exactly this grid, cropped only by masking. |
| Time steps | 2026-06-28, 06:00–22:00 CEST, every 30 min: 33 steps. Timestamps stored in UTC (04:00Z–20:00Z), labelled with local time where useful. |
| Transit day | Saturday 2026-10-17, 06:00–22:00 local, mapped clock time to clock time onto the heat day. |
| Shade zone shares | `f_sun`, `f_bldg`, `f_tree`, `f_both` in [0, 1], summing to 1. These are the inputs of `compute_inputs_for_utci` (spec §13). |
| Cloud | GCP project `hackaton-honory`, region `europe-central2`. Any infrastructure that fits the goal may be created (free tier, cost is not a concern). Proposed shared names: bucket `gs://hackaton-honory-heat/<task-id>/…`, BigQuery dataset `hackaton-honory.warsaw_heat`. Heavy processing runs in the cloud, not on laptops. |
| Formats | Rasters: Cloud-Optimised GeoTIFF. Vectors and tables: GeoParquet/Parquet in GCS and/or BigQuery tables. Each output has a short README (source, vintage, units, nodata). |
| Stack | Python. Prefer pip-installable libraries; anything else needs a one-line justification. |

Each task ends with a short report in `reports/<task-id>/` of this repo: what was produced, where it is, what was learned about the data, and open issues. Include a few PNG previews.
