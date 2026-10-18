# Śródmieście 2076: planning a trip in space and time to minimise UTCI

**Specification** · updated 2026-10-18 · Scope: Śródmieście · Heat day: Sun 2026-06-28 · Transit: recorded operations Sat 2026-10-17 (BigQuery)

This document states what the project builds and the decisions it rests on. Task briefs are in [`tasks/`](../tasks/README.md); what each task produced and learned is in [`reports/`](../reports/).

## 1. Story

> In 2076, as a particularly heat-vulnerable pensioner, I want to go from X to Y. The sun is scorching and I want to minimise my UTCI on the way so that I don't get sick or die. We recreate the conditions of 2026-06-28, as they will likely match many summer days 50 years from now. I use the app to plan my route not only in space but in time.

The product is a route planner that answers both *which way* and *when to leave*. It minimises the felt heat (UTCI) along the whole trip: walking, waiting at stops and riding.

## 2. Decisions

| Topic | Decision |
|---|---|
| Area | Śródmieście only (PRG unit 146510_8, 15.56 km²). Surroundings are included only as shadow casters. |
| Modes | Walking, trams and buses. No bikes, metro or suburban rail. Only vehicles with air conditioning are boarded. |
| Geometry | GUGiK LiDAR at 1 m: 2025-04-27 flight for ground and buildings, 2023-09-05 leaf-on flight for trees. Cranes, scaffolding and arcades count as buildings; no special handling. |
| Thermal model | **SOLWEIG, run district-wide** (§6). It gives shadow and mean radiant temperature (Tmrt) per pixel and time step. UTCI follows from Tmrt, air temperature, humidity and wind. |
| Time | 2026-06-28, 06:00–22:00 CEST every 30 min: 33 steps. Values between steps are interpolated. |
| Transit | Recorded vehicle positions of Sat 2026-10-17, not a timetable. Clock time maps onto clock time of the heat day. |
| Metric | Heat dose along the trip (§7). Waiting at home before departure is free; an AC vehicle counts as comfortable. |
| Persona | Elderly pedestrian, walks about 1.0 m/s, stands while waiting. |
| Stack | Python; heavy work runs on GCP, not on laptops. |

## 3. Grid and conventions

| Item | Value |
|---|---|
| CRS | EPSG:2180 (PL-1992), heights EVRF2007. Web outputs also in EPSG:4326/3857. |
| Grid | 1 m cells, E 634,000–642,000, N 483,000–492,000 (8000 × 9000 = 72 Mpx). The AOI buffered by 1 km, snapped outward to whole kilometres. |
| Caster extent | E 630,000–643,000, N 483,000–494,000 (13 × 11 km): how far a 310 m tower's shadow reaches at the lowest sunlit step (≈5.7 km at 20:30, sun 3.1°). |
| Time stamps | Stored in UTC (04:00Z–20:00Z), labelled with local time. Steps 21:00–22:00 have the sun below the horizon. |
| Storage | `gs://hackaton-honory-heat/<task>/`, BigQuery `hackaton-honory.warsaw_heat`. Rasters are COGs; tables are Parquet or BigQuery. |

## 4. Data

| Source | Used for | Task |
|---|---|---|
| GUGiK LiDAR LAZ, 538 sheets (two flights, 44 GiB), WFS sheet index `DanePomiaroweLidarEVRF2007/WFS/Skorowidze` | Ground, buildings, canopy height, crown base, crown gap fraction, water | T01 |
| Sentinel-2 L2A 2026-06-27 (tile 34UDC) | NDVI for grass and NDWI for water where LiDAR is weak | T01 |
| Tree inventory (city open data, 2010–13) | Check of LiDAR tree heights | T01 |
| OpenStreetMap (Geofabrik Mazowieckie 2026-10-16) | Walk network, crossings, covered and indoor ways, stop platforms and shelters | T03 |
| BigQuery `hackaton-honory.warsaw_transport` | Vehicle positions, vehicle register with AC flag, GTFS (mkuran.pl feed 2026-10-16) | T04 |
| IMGW: Okęcie hourly synop, Obserwatorium II and Filtry climate stations | Air temperature, humidity, wind | T05 |
| ERA5 (Open-Meteo) | Global, direct and diffuse radiation; ground-temperature spin-up | T05, T06 |
| Landsat C2 L2: LC09 2026-06-25, gaps filled from LC08 2026-06-17 | Surface-temperature anomaly `pmwc`, for validation and visualisation | T05 |

What the data turned out to be (water is LiDAR class 9, not 8; bridges are in class 6; no hourly air temperature exists inside the district for June 2026; …) is recorded in the task reports.

## 5. Geometry and shade

- **Geometry (T01):** COGs on the grid: `dtm`, `dsm` (buildings + ground), `building_mask`, `water_mask`, `canopy_height`, `crown_base_height`, `crown_gap_fraction`, `surface_type`. Plus a ground + buildings surface over the caster extent for far shadows (built in T02).
- **SOLWEIG shadow (T06):** building and vegetation shadow per pixel and step, part of the thermal run (§6). This is the shade used for UTCI and in the app.
- **Shade engine (T02, [`shade/`](../shade/README.md)):** our own engine on the same geometry:
  - It sweeps the full caster extent, so it also catches the long evening shadows of towers outside the SOLWEIG tile halos.
  - It splits shade into building and tree parts, with light transmission through crowns.
  - Uses: far-shadow check of the SOLWEIG field (T07), the building-only-shade baseline (T11), shade shares and sky view factor for visualisations.

## 6. Thermal model: SOLWEIG

- **Implementation:** [`solweig`](https://github.com/UMEP-dev/solweig) 0.1.0b96, the UMEP-dev Rust re-implementation of UMEP SOLWEIG, on CPU. Code: [`solweig_ref/`](../solweig_ref/README.md).
- **Configuration:**
  - Defaults throughout: standing person, isotropic sky, tree transmissivity 0.03, trunk zone 25 % of canopy height, default surfaces (no land-cover raster).
  - Inputs: DSM = T01 buildings on ground, DEM = T01 ground, CDSM = T01 canopy height.
- **Forcing:** T05 table `t05_forcing_20260628`:
  - Air temperature: Okęcie, with an offset to Obserwatorium II (Śródmieście).
  - Humidity: Okęcie, recomputed at that temperature. Wind: Okęcie at 10 m.
  - Radiation: ERA5 global, direct and diffuse.
  - Three spin-up steps before 06:00 are run and dropped.
- **Domain:** 27 core tiles of 1 km covering the district, each run with a 500 m halo of shadow casters, or 1000 m where a tower above 100 m stands within 1 km. Cloud Run Jobs spread over five regions.
- **UTCI:** `pythermalcomfort` from SOLWEIG Tmrt, air temperature, humidity and wind (max(0.5, wind)). Valid for Tmrt − Ta between −30 and +70 K and wind 0.5–17 m/s; checked on every pixel.
- **Known limits:**
  - Shadows from towers beyond a tile's halo are missing at low sun; T07 compares with T02.
  - Air temperature comes from a park station, so it is a cool-side estimate; the Filtry-adjusted series is the warm sensitivity case.
  - No street-canyon wind reduction.
  - Surfaces have no thermal memory beyond what SOLWEIG models.
  - Landsat `pmwc` is not a model input.

## 7. Metric: heat dose

- **Exposure:** the trip is a path through space and time. U(t) is UTCI at the traveller's position and time, interpolated between steps. Walking and waiting are charged; riding an AC vehicle counts as U ≈ 26 °C; waiting at home is free.
- **Dose:** H = ∫ g(U(t)) dt, with the working weights g(U) = max(0, U − 26) + max(0, U − 32) + 2·max(0, U − 38) (K·s). The weights follow the UTCI stress categories and stay a parameter.
- **Options per query:** *coolest*, *balanced*, *fastest* (all AC-only), plus walk-only. Each option has its best departure time.
- **Reported per option:** dose, total time, peak UTCI, minutes per UTCI category, share of walking in shade.

## 8. Public transport

- **Stop events (T04):** for 2026-10-17 06:00–22:00, vehicle positions are matched to GTFS trips. Output: 7,555 runs and 56,625 stop events at Śródmieście stops, with recorded arrival and departure. Coverage is about 96 % of scheduled stop calls.
- **AC:** taken from the register entry of the vehicle that drove the run. Unknown AC means not boardable. 97.3 % of tram and 99.0 % of bus departures are boardable; tram lines 7 and 28 are mostly non-AC.
- **Timing:**
  - The traveller reaches the stop shortly before the recorded departure, with a safety margin from the per-line early-departure spread (`reports/T04/line_delays.csv`).
  - Waiting at the stop, including transfers, is charged at the best spot of the stop's waiting area (shade, shelter).

## 9. Router

- **Walk network (T03):** OSM segments ≤ 25 m on the main component (99 % of length). Each segment carries UTCI and dose rate per step from the pixels it covers (T08).
- **Missing sidewalks:** where a street has none mapped, synthetic sidewalk edges represent each side.
- **Indoor and underground segments:** walkable, with a separate rule because outdoor UTCI does not apply.
- **Time-dependent costs:** a segment is priced at the time the traveller enters it. Signalised crossings add a short wait.
- **Search:** combines walking with AC runs and searches over departure times.
- **Baselines:** fastest walk-only; ORS Shaded; routing on building-only shade.

## 10. App and visualisations

- **App (T10):** a public web app on Cloud Run with:
  - a time-slider map of UTCI and shade;
  - the trip planner with route cards.
- **Visualisations (T12 data, T13 views):**

| Visualisation | Data |
|---|---|
| Heat map of walking routes with cool and water points | Segment heat per step (T08); points for drinking water, fountains, water curtains, shaded rest spots and other cool places (T12) |
| Planner demo with the transit component | App (T10) with router answers (T09) and demo trips (T11) |
| 3D view of shadows moving with the sun, with clouds | Building and tree 3D geometry from T01, shadow per step (SOLWEIG, T02), sun position and cloud cover per step (T05) |
| Temperature and albedo through the day | Air temperature (T05); Tmrt, UTCI and surface radiation terms per step (SOLWEIG); albedo map (Sentinel-2) and reflected/incoming shortwave from SOLWEIG; Landsat surface temperature at 11:25 (T05) |

## 11. Infrastructure

- **GCP project:** `hackaton-honory`, home region europe-central2.
- **Cloud Run Jobs:** the main compute.
  - At most 5 regions per project; in use: europe-central2, -west1, -west3, -west4, -north1.
  - Each region: 20 vCPU / 40 GiB. Each task: at most 8 vCPU / 32 GiB.
- **Compute Engine:** 12 vCPU in total, no GPU, no spot.
- **Storage:** bucket `gs://hackaton-honory-heat`; BigQuery datasets `warsaw_heat` (products) and `warsaw_transport` (collected transit data).
- **App:** Cloud Run service in europe-central2.

## 12. Outputs

| Product | Where | Task |
|---|---|---|
| Geometry COGs | `T01/output/` | T01 |
| Shade shares (`f_sun`, `f_bldg`, `f_tree`, `f_both`), sky view factor, far-caster surface | `T02/` | T02 |
| Walkable mask, walk network, segment → pixel table, stop waiting areas | `T03/`, `warsaw_heat.t03_*` | T03 |
| Stop events and runs with AC | `T04/`, `warsaw_heat.stop_events`, `runs` | T04 |
| Forcing, `pmwc` | `T05/`, `warsaw_heat.t05_forcing_20260628` | T05 |
| SOLWEIG Tmrt, shadow, UTCI (district) | `T06/district/` | T06 |
| UTCI and Tmrt on walkable space | `T07/` | T07 |
| Heat per segment and per stop | `T08/`, `warsaw_heat.t08_*` | T08 |
| Router, route results | repo, `T09/` | T09 |
| App | Cloud Run | T10 |
| Demo trips and baseline comparison | `T11/` | T11 |
| Visualisation layers and views | `T12/`, app | T12, T13 |

## 13. Validation

Done:
- Shadows against analytic cases and the 2023-09-05 orthophoto (precision 0.86–0.89; T02).
- LiDAR tree heights against the tree inventory (T01).
- Landsat gap fill (r = 0.94; T05).
- SOLWEIG pilot runtimes and UTCI validity (T06).

Remaining:
- **SOLWEIG field (T07):** tile seams, far shadows at low sun, sun–shade differences, evening values close to air temperature.
- **Router (T11):** dose saved against extra travel time on demo trips, against the baselines.

Open questions:
- the dose weights;
- the departure safety margin;
- the rule for waiting spots and shelters;
- evening heat release from pavement.
