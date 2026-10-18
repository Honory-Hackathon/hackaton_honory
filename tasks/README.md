# Tasks

These tasks implement the [specification](../docs/specification.md). Each file is a self-contained brief for one person or agent session. They describe the goal, the data with its known quirks, the constraints and the expected output. They do not prescribe methods: exploring the data and choosing the approach is part of each task.

## First wave (done or finishing, 2026-10-18)

The six tasks below were chosen because together they cover every input the UTCI field and the router need, and **none depends on another's output**. Where one later consumes another's result, the hand-over format is fixed in the shared conventions below, so each task can be built and tested on its own.

| Task | Aspect | Why it starts first |
|---|---|---|
| [T01](T01_lidar_geometry.md) | LiDAR → 1 m geometry (ground, buildings, canopy, crown light transmission) | Largest download and longest processing; everything about shade depends on it |
| [T02](T02_shade_engine.md) | Shade engine: sun position + shadow casting → shade zone shares per time step | Core of decision 1 in the spec; the algorithm can be built and tested on any small elevation-model tile |
| [T03](T03_walk_network_stops.md) | Walkable space, walk network and stop waiting areas | Defines *where* UTCI is evaluated and *what* the router walks on |
| [T04](T04_transit_ac_events.md) | Recorded tram and bus operations from BigQuery → stop events + AC per run | Independent data engineering; the transit half of the router |
| [T05](T05_meteo_landsat.md) | Weather forcing for 2026-06-28 + Landsat surface-heat anomaly (`pmwc`) | Inputs of the thermal model; small and quick |
| [T06](T06_solweig_reference.md) | SOLWEIG (simplest setup) on a pilot area vs the proposed simple model | De-risks the physics early; tunes the proposal's coefficients |

## Second wave: start now, in parallel

First-wave results are in `reports/T01`–`T06`. Each second-wave task can start now: where an upstream output is not final yet, the brief names a stand-in with the same format (the T06 pilot rasters, placeholder heat costs, a stub router).

**Thermal model (decision 2026-10-18):** SOLWEIG, run district-wide in T06, is the production model. The spec §13 simple model is not used.

| Task | Aspect | Final run needs |
|---|---|---|
| [T07](T07_utci_field.md) | UTCI field from district SOLWEIG → Tmrt and UTCI per walkable pixel and time step, with seam, coverage and far-caster checks | T06 district run |
| [T08](T08_segment_stop_heat.md) | UTCI and dose rate per walk segment and per stop and time step | T07 |
| [T09](T09_router.md) | Router: walk + AC trams/buses, lowest heat dose, best departure time | T08 (heat costs) |
| [T10](T10_app.md) | App: time-slider map and route cards on Cloud Run | T07, T09 |
| [T11](T11_validation_demo.md) | Demo trips, baselines (ORS Shaded, building-only shade), dose-weight comparison | T09 |
| [T12](T12_visual_layers.md) | Data layers for the visualisations: cool and water points, 3D scene, temperature and albedo through the day | T06 district run |
| [T13](T13_visualisations.md) | Visualisations: route heat map with cool points, 3D shadows over the day, temperature and albedo | T08, T12 |

## Shared conventions (the hand-over contract)

| Item | Convention |
|---|---|
| Area of interest | Śródmieście, PRG unit `146510_8`, from WFS `https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries`, layer `A05_Granice_jednostek_ewidencyjnych`. ≈15.5 km². |
| Halo | AOI buffered by 1,000 m. Used only for shadow casters. |
| Caster extent | Shadow length is not capped at the halo. Buildings and terrain cast from as far as real shadows reach: AOI shifted towards the sun by 310 m / tan(sun elevation) for every sunlit step, i.e. ≈5.7 km to the west-north-west at 20:30 (3.1°). Bounding box E 630,000–643,000, N 483,000–494,000 (13 × 11 km). Outside the grid only a ground + buildings surface model is needed (same 1 m cells); beyond the halo only objects above ~55 m matter. |
| CRS | EPSG:2180 (PL-1992), heights EVRF2007. Web outputs may additionally be in EPSG:4326. |
| Grid | 1 m cells. Extent = halo bounding box snapped outward to multiples of 1,000 m. Cell edges on integer metres. Every raster product uses exactly this grid, cropped only by masking. |
| Time steps | 2026-06-28, 06:00–22:00 CEST, every 30 min: 33 steps. Timestamps stored in UTC (04:00Z–20:00Z), labelled with local time where useful. |
| Transit day | Saturday 2026-10-17, 06:00–22:00 local, mapped clock time to clock time onto the heat day. |
| Shade zone shares | T02: `f_sun`, `f_bldg`, `f_tree`, `f_both` in [0, 1], uint8 / 250, 255 = night. The thermal model is SOLWEIG (spec §6), which computes its own shadow. |
| Cloud | GCP project `hackaton-honory`, region `europe-central2`. Any infrastructure that fits the goal may be created (free tier, cost is not a concern). Proposed shared names: bucket `gs://hackaton-honory-heat/<task-id>/…`, BigQuery dataset `hackaton-honory.warsaw_heat`. Heavy processing runs in the cloud, not on laptops. |
| Compute | Cloud Run Jobs: 5 regions per project (in use: europe-central2, -west1, -west3, -west4, -north1), each 20 vCPU / 40 GiB, at most 8 vCPU / 32 GiB per task. Memory is usually the binding limit (one 32 GiB task per region). Compute Engine: 12 vCPU globally, no GPU, no spot. Split big jobs into tiles across regions. |
| Formats | Rasters: Cloud-Optimised GeoTIFF. Vectors and tables: GeoParquet/Parquet in GCS and/or BigQuery tables. Each output has a short README (source, vintage, units, nodata). |
| Stack | Python. Prefer pip-installable libraries; anything else needs a one-line justification. |

Each task ends with a short report in `reports/<task-id>/` of this repo: what was produced, where it is, what was learned about the data, and open issues. Include a few PNG previews.
