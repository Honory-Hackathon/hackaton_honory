# Heat App · Team 4 · Honory

> **Heat App** by **Team 4 — Honory** (Tadeusz Furykiewicz, Stanisław Sender, Adam Zienkowicz)
>
> 🌡️ **Try it:** https://heat-app-269831067246.europe-central2.run.app · 📊 **Pitch:** [`pitch-presentation.pptx`](pitch-presentation.pptx)

**Heat App** is a route planner for a heat-vulnerable pedestrian in central Warsaw that answers two questions: **which way** and
**when to leave**. It minimises the felt heat (UTCI) over the whole trip, walking, waiting at stops and riding
air-conditioned trams and buses. The heat day is **Sunday 2026-06-28** (air temperature up to 37.2 °C, clear sky);
the transit is the **recorded** operation of Saturday 2026-10-17.

| Live | URL |
|---|---|
| Planner + time-slider UTCI map | https://heat-app-269831067246.europe-central2.run.app |
| Route heat map with cool and water points | https://heat-views-269831067246.europe-central2.run.app/routes/ |
| 3D shadows through the day | https://heat-views-269831067246.europe-central2.run.app/shadows3d/ |
| Temperature and albedo through the day | https://heat-views-269831067246.europe-central2.run.app/temperature/ |
| Router API | https://t09-router-269831067246.europe-central2.run.app/api/health (`POST /api/plan`, `POST /api/price`) |

## Pipeline at a glance

```
GUGiK LiDAR ─► lidar/  1 m geometry ──┬─► shade/        own shade engine (validation, far casters, SVF, layers)
Sentinel-2                            └─► solweig_ref/  SOLWEIG district run: shadow, Tmrt, UTCI per pixel × 33 steps
IMGW + ERA5 + Landsat ─► meteo/ forcing ──┘                        │
OSM ─► walk/  walkable mask, walk network, stop areas ─────────────┤
                                                                   ▼
                                       utci/   UTCI + Tmrt on walkable pixels
                                                                   ▼
BigQuery vehicle positions ─► transit/ AC stop events     heat/   UTCI + dose rate per segment and stop
                                          │                        │
                                          └──────► router/ ◄───────┘   walk + AC transit, best departure
                                                      │
                          layers/ ─► views/ (3 pages)   app/ (map + planner)
```

| Folder | Stage | Main outputs |
|---|---|---|
| [`lidar/`](lidar/README.md) | LiDAR → 1 m geometry | `gs://hackaton-honory-heat/T01/output/*.tif` |
| [`shade/`](shade/README.md) | Shade engine, sky view factor, far-caster surface | `T02/f_{sun,bldg,tree,both}.tif`, `svf.tif`, `far_casters/dsm_far.tif` |
| [`walk/`](walk/README.md) | Walkable mask, walk network, stop waiting areas | `T03/`, `warsaw_heat.t03_*` |
| [`transit/`](transit/README.md) | Recorded stop events with AC | `T04/`, `warsaw_heat.stop_events`, `runs`; `transit/line_delays.csv` |
| [`meteo/`](meteo/README.md) | Weather forcing, Landsat surface-temperature anomaly | `T05/`, `warsaw_heat.t05_forcing_20260628` |
| [`solweig_ref/`](solweig_ref/README.md) | SOLWEIG thermal model (district tiles + mosaic) | `T06/district/` |
| [`utci/`](utci/BUCKET_README.md) | UTCI and Tmrt masked to walkable space | `T07/{utci,tmrt,sun_solweig}.tif` |
| [`heat/`](heat/README.md) | Heat per walk segment and per stop | `T08/`, `warsaw_heat.t08_segment_heat`, `t08_stop_heat` |
| [`router/`](router/README.md) | Heat-aware multimodal router | Cloud Run `t09-router`, bundle in `T09/bundle/` |
| [`app/`](app/README.md) | Web app: UTCI map + trip planner | Cloud Run `heat-app`, frames in `T10/frames/` |
| [`layers/`](layers/README.md) | Web layers for the visualisations | `T12/` (start from `T12/manifest.json`) |
| [`views/`](views/README.md) | The three visualisation pages | Cloud Run `heat-views` |

Storage: bucket `gs://hackaton-honory-heat/<prefix>/` and BigQuery dataset `hackaton-honory.warsaw_heat` (GCP project
`hackaton-honory`). Stage outputs live under the numbered prefixes listed in the table above. Every raster is a Cloud-Optimised GeoTIFF;
tables are Parquet and BigQuery. Each bucket prefix has its own README (source, units, nodata, band order).

## 1. Scope, grid and time

| Item | Value |
|---|---|
| Area | Śródmieście, PRG unit `146510_8` (15.56 km²), from the PRG WFS layer `A05_Granice_jednostek_ewidencyjnych`. Surroundings count only as shadow casters. |
| Modes | Walking, trams and buses. Only vehicles with air conditioning are boarded. No metro, rail or bikes. |
| Persona | Elderly pedestrian, 1.0 m/s, standing while waiting. |
| CRS | EPSG:2180 (PL-1992), heights EVRF2007. Web outputs in EPSG:3857/4326. |
| Grid | 1 m cells, E 634,000–642,000, N 483,000–492,000 (8000 × 9000 = 72 Mpx): the AOI buffered by 1 km and snapped outward to whole kilometres. Every raster product uses exactly this grid. |
| Caster extent | E 630,000–643,000, N 483,000–494,000 (13 × 11 km): how far a 310 m tower's shadow reaches at the lowest sunlit step (≈ 5.7 km at 20:30, sun 3.1°). |
| Time | 2026-06-28, 06:00–22:00 CEST every 30 min = **33 steps**, stored in UTC (04:00Z–20:00Z). The sun sets between 20:30 and 21:00, so the last three steps are night. Values between steps are interpolated linearly. |
| Transit day | Saturday 2026-10-17, 06:00–22:00, mapped clock time to clock time onto the heat day (both CEST). |

## 2. Geometry from LiDAR ([`lidar/`](lidar/README.md))

**Data:** GUGiK LiDAR, 538 LAZ sheets (44 GiB): the **2025-04-27** flight for ground and buildings, the **2023-09-05
leaf-on** flight for trees. Sheets cover the whole grid, not only the halo.

**Layers** (all on the shared grid):

| Layer | How |
|---|---|
| `dtm` | 2025 class 2 ground, then water surface, then 2023 ground; pyramid inpainting under buildings |
| `dsm` | Ground + buildings, no vegetation: 2025 class 6 max inside the building mask, DTM elsewhere |
| `building_mask` | 2025 class 6 ≥ 2 m above ground; 3×3 closing, holes ≤ 25 m² filled, parts < 10 m² dropped |
| `water_mask` | LiDAR water (class 9) grown into connected Sentinel-2 NDWI > 0 areas |
| `canopy_height` | 2023 class 5 max − DTM where ≥ 2.5 m |
| `crown_base_height` | 10th percentile of class-5 point heights per cell, 3×3 mean |
| `crown_gap_fraction` | Share of 2023 first returns within 1 m of the ground over the crown (3×3 window): a near-nadir laser gap fraction, median 0.04 over canopy |
| `surface_type` | Building, water, grass, shrubs, paved/bare, other; Sentinel-2 NDVI relabels grass (≥ 0.45) and cars (< 0.25) outside canopy |

**Reconciling the two flights:** canopy is removed where a 2025 building stands or where 2025 has no vegetation
returns in a 5×5 window (0.24 km² felled, 1.1 % of the 2023 canopy). Trees planted after 2023 are not added.
Cranes, scaffolding, glass roofs, arcades and bridges are whatever GUGiK classified as building (class 6), and count
as buildings.

**Data quirks found:**
- Water is class 9, not 8 (class 8 does not occur). The Vistula surface often gives no returns at all; it is filled
  from Sentinel-2 NDWI (NDVI cannot separate the turbid river from asphalt).
- Class 12 (overlap) holds 27–36 % of all points. They are valid points, just without a semantic class.
- Class 0 (never classified, mostly vehicles) exists; GUGiK rarely uses class 3 (low vegetation).
- Bridges and flyovers are class 6, so they are in the building mask and the DSM (correct for shade).

**Checks:** Palace of Culture 235.6 m above ground (reference ≈ 237 m); Varso roof 233.6 m (the 310 m spire is not
in class 6, and its thin shadow is negligible). Against the city tree inventory (39,091 trees with height): 93 % have
LiDAR canopy within the crown radius, median LiDAR − inventory +2.9 m, which fits 10–13 years of growth.

**Grid-wide shares:** canopy 30 %, buildings 20 %, water 3 %; surfaces paved/bare 50 %, grass 17 %, shrubs 8 %.

**Compute:** 72 Cloud Run tasks (2 vCPU / 8 GiB, one per 1 km tile with a 100 m buffer) over four regions, ~15 min,
then one mosaic task.

## 3. Shade engine ([`shade/`](shade/README.md))

Our own engine (Python + numba) runs on the same geometry. SOLWEIG computes the shade that feeds UTCI (§5). The
engine is used to:
- check SOLWEIG's shadows, including the long evening shadows of towers outside SOLWEIG's tile halos;
- provide shade shares split into building and tree shade, and the sky view factor (SVF), for the visualisations
  and for the building-only-shade baseline (§10).

**Method:**
- Sun positions with pvlib SPA for the 33 steps.
- Building and terrain shade: a sweep along the sun direction over the ground + buildings surface, with far casters
  from a 13 × 11 km surface (`far_dsm.py`, 305 sheets of the 2025 flight). Shadow length is not capped.
- Tree shade: a ray march through the crowns, with the LiDAR crown gap fraction (§2) as vertical transmission. The sun can
  pass under a crown, between the trunk zone and the crown base.
- Outputs: per pixel and step, the shares `f_sun`, `f_bldg`, `f_tree`, `f_both` (uint8 / 250, summing to exactly 250;
  255 = night). SVF over 32 azimuths × 8 elevation bands, with and without crown transmission.

**Validation:**
- Synthetic scenes: shadow lengths and bearings within 1–2 cells of the analytic values; a 100 m tower at 3° sun
  casts 1.9 km within 3 cells; canyon SVF 0.447 vs analytic 0.447.
- Palace of Culture spire against analytic tip positions, 07:00–19:00: within 2 m, bearing within 0.2°.
- 2023-09-05 orthophoto (5 cm, same job as the LiDAR), photo time fitted as the only free parameter: all five test
  windows chose 09:33–09:51 UTC, inside the LiDAR flight window. **Precision 0.86–0.89** (engine shade is dark in the
  photo); recall is lower where dark asphalt, tram tracks and cars are not shadow.

**Runtime:** 33 steps in 9.5 min and SVF in 65 min on one 8-vCPU VM (8 GB peak). Low sun is the slow case for trees.

## 4. Weather forcing and surface heat ([`meteo/`](meteo/README.md))

| Variable | Source |
|---|---|
| Air temperature `ta_c` | IMGW hourly synop Warszawa-Okęcie, shifted to the climate station Warszawa-Obserwatorium II (Al. Ujazdowskie, inside the district): −2.3 / 0.0 / −1.6 K at 06/12/18 UTC, linear in between |
| Warm variant `ta_filtry_adj_c` | Okęcie + the Filtry station offset (+0.9…+2.1 K), for sensitivity |
| Humidity | Okęcie vapour pressure, RH recomputed at `ta_c` |
| Wind | Okęcie 10 m; UTCI input max(0.5, wind). No street-canyon reduction |
| Radiation | ERA5 (Open-Meteo) global, direct and diffuse, interpolated with a clear-sky index; GHI = DHI + DNI·sin(elevation) |

Peak `ta_c` 37.2 °C (station Tmax 37.7 °C). The sky was clear all day.

No public hourly air-temperature series exists inside Śródmieście for June 2026 (the IMGW 10-min archive is gone;
city sensors are live-only). Obserwatorium II sits in a park, so `ta_c` is a **cool-side** estimate.

**Landsat surface-temperature anomaly `pmwc`:** LC09 2026-06-25 11:25 CEST minus the district median (38.8 °C), at
10 m, with 32 % cloud-masked area filled from LC08 2026-06-17 (fit r = 0.94). It is used for validation and the
temperature view, **not** as a model input.

## 5. Thermal model: SOLWEIG ([`solweig_ref/`](solweig_ref/README.md))

**Implementation:** [`solweig`](https://github.com/UMEP-dev/solweig) 0.1.0b96, the UMEP developers' Rust
re-implementation of UMEP SOLWEIG, run CPU-only. The project has no GPU quota, so GPU SOLWEIG was not an option, and
the reference Python code is single-threaded.

**Configuration:**
- Defaults throughout: standing person, isotropic sky, tree transmissivity 0.03, trunk zone 25 % of canopy height,
  default surfaces (no land-cover grid).
- DSM = buildings on ground, DEM = ground, CDSM = canopy height, all from §2.
- Forcing from §4, with three ERA5 spin-up steps before 06:00 to warm the ground scheme (dropped).
- UTCI with `pythermalcomfort` from Tmrt, `ta_c`, RH and max(0.5, 10 m wind). Validity (Tmrt − Ta within −30…+70 K,
  wind 0.5–17 m/s) holds on every pixel and step.

**District run:** one call over the whole grid would have taken ~6 h for SVF alone, because every pixel's rays are
sized for the tallest building in the domain. Instead, **27 core tiles of 1 km** cover the district, each with a
caster halo:
- 500 m halo for 9 tiles (4 Mpx, ~12 min each on 4 vCPU);
- 1000 m where a building over 100 m stands within 1 km (18 tiles, 9 Mpx, ~17 min on 8 vCPU / 32 GiB). 1000 m is
  SOLWEIG's own shadow-reach cap.

The tiles ran as Cloud Run Jobs over five regions in about 2 h 15 min of wall time (~55 vCPU-h). A mosaic job writes
the 33-band district COGs `T06/district/{tmrt,shadow,utci}_20260628.tif`.

**Comparison with a zone-based model:** on a 0.96 km² pilot, SOLWEIG was compared with a parametric model
(Tmrt = Ta + shortwave × zone transmissivity + surface offset):

| Model | Daytime Tmrt RMSE vs SOLWEIG | UTCI RMSE |
|---|---|---|
| Parametric model, default coefficients | 4.2 K (+16 K in sun at low sun, +17 K at night) | 1.1 K |
| Same structure, 12 coefficients fitted (spatial hold-out) | 2.0 K | 0.5 K |
| SOLWEIG itself, default vs land-cover surfaces | 1.6 K | 0.4 K |

Even fitted, a zone model ranks pixels poorly at low sun (Spearman of UTCI vs SOLWEIG 0.04 at 20:00 with default coefficients), and
ranking is what a router uses. In shade, Tmrt is driven by hot sunlit walls and pavement nearby, which a zone model
cannot see.

**Runtime facts:** SVF costs 63–86 s per Mpx once (geometry only, reusable for any day); each time step 1.5–2.1 s
per Mpx. At tile size, the 33 steps cost 0.6–1.2× the SVF.

## 6. UTCI field on walkable space ([`utci/`](utci/BUCKET_README.md))

The 27 tile cores are masked to the walkable pixels of §7 (values copied unchanged). Outputs on the shared grid,
33 bands: `utci.tif`, `tmrt.tif` (float32, NaN off the mask) and `sun_solweig.tif` (SOLWEIG shadow × 250, uint8,
255 = nodata or night). A 3-task Cloud Run Job does it in 13 min, holding only the walkable pixels as vectors.

**Checks:**

| Check | Result |
|---|---|
| Coverage | 2,230,894 of 2,231,803 walkable pixels (99.96 %) have values at all 33 steps; the 909 without lie in the mask's fringe outside the AOI. |
| Tile seams (41 edges) | No SOLWEIG seams: the Tmrt difference across an edge equals the normal pixel-to-pixel texture (1.22 vs 1.19 K), also where 500 m and 1000 m halos meet. The one visible seam (E 638,000, N 488,000–489,000, up to 4.7 K) is a 1–3 m step in the DTM at a LiDAR tile boundary, which casts a real-looking shadow in both SOLWEIG and the shade engine. |
| Far casters vs the shade engine | SOLWEIG and the engine (built independently) agree on all but 0.13–0.37 % of walkable pixels at every step, mostly 1–2 px shadow edges. Only at 20:00 and 20:30 are true far shadows missing: ≈ 900 and ≈ 1,700 pixels with a tower caster more than 1 km away, worth ≤ 1.8 K UTCI; < 0.01 K averaged over the area. |
| Ground-temperature sign | `solweig` uses Tstart = −3.41 K without land cover (the published value; UMEP's own no-land-cover branch uses +3.41 K, which would add 0.5–0.8 K UTCI in full sun). Confirmed in the source and with a 3-run synthetic test. |
| Pilot consistency | District tiles reproduce the pilot to ≤ 0.006 K. |
| Sanity | Sun vs building shade at 13:00: Tmrt 63.0 vs 40.7 °C, UTCI 41.8 vs 36.4 °C. The sun–shade gap is +5.2…+6.2 K UTCI from 09:00 to 17:00. Tree and building shade agree within 0.35 K. UTCI reaches Ta at 20:00. |

District UTCI on walkable pixels peaks at 40.9 °C (mean) at 15:00; in the sun it is 43.9 °C, in shade 38.0 °C.

## 7. Walk network and stops ([`walk/`](walk/README.md))

**Source:** OpenStreetMap (Geofabrik Mazowieckie, 2026-10-16). Śródmieście's sidewalks are mostly mapped as separate
ways, so the network knows which side of the street a walker is on.

- **Walkable mask** (1 m, 8 categories): sidewalk, plaza, shared street, park path, crossing, stop area, covered,
  steps; 2.23 M pixels. Plazas count only `highway=pedestrian|footway` areas and `area:highway` polygons
  (`place=square` includes the carriageway).
- **Network:** 58,715 segments of ≤ 25 m and 49,191 nodes. The main component holds 99.0 % of the length; routing
  uses only it.
  - **Synthetic sidewalks:** a street tagged with sidewalks but without separate sidewalk ways gets one 2 m edge per
    side, offset from the centreline (10.3 km, mostly narrow Old Town and Powiśle streets).
  - **Shared space:** service roads and living streets are walked on their centreline.
  - **Not walkable along their length:** streets with no sidewalk and road tunnels; they can still be crossed at
    mapped crossings.
  - **Gap closing:** 477 dead ends within 5 m of the network were joined with connectors.
- **Flags:** `crossing_signals` (486 signalised crossings), `steps`, `indoor` (15.2 km of underground passages and
  corridors, which have no pixels), `covered` (9.4 km of arcades and passages).
- **Segment → pixel table:** each segment's footprint on the mask (2.06 M rows); a pixel can belong to several
  segments.
- **Stop waiting areas:** 360 GTFS tram/bus stops. OSM platform polygons where mapped; elsewhere 3 × 15 m (bus) or
  3 × 30 m (tram) rectangles aligned with the sidewalk. Each area has a `shelter` flag (279 yes) and an access node
  on the network (95 % within 11 m).

## 8. Transit: recorded stop events with AC ([`transit/`](transit/README.md))

The planner uses what trams and buses **actually did**, not the timetable. Vehicle positions collected from the
city API on Saturday 2026-10-17 (BigQuery `hackaton-honory.warsaw_transport`, ~10 s reporting) are matched to GTFS
trips (mkuran.pl feed of 2026-10-16) along their shapes. The result is one recorded arrival and departure per run
and Śródmieście stop.

- **Output:** 7,555 runs and 56,625 stop events. 95.6 % of scheduled stop calls are recorded; 98 % of in-scope
  position reports are assigned to a trip.
- **Air conditioning:** from the fleet register entry of the vehicle that drove the run. Unknown AC means not
  boardable. 97.3 % of tram and 99.0 % of bus departures are boardable. Trams 7 and 28 run mostly older 116N cars
  without AC.
- **Timing safety margin:** per-line timetable deviation (`transit/line_delays.csv`): the traveller reaches the pole
  `clip(60 s + max(0, −p05), 60, 240)` s before the recorded departure (median line 103 s).
- **Known gaps:** a feed outage at 21:53–22:07 (missing runs, not cancelled service); 258 scheduled trips without a
  recorded run; operations outside the timetable blocks are not included.

## 9. Heat per segment and stop ([`heat/`](heat/README.md))

The router works on segments and stops, not pixels, so the UTCI field is summarised per segment and step
(58,715 × 33) and per stop and step (360 × 33).

**Dose rate** (the metric is defined in §10): g(U) = max(0, U − 26) + max(0, U − 32) + 2·max(0, U − 38), in K. The
thresholds are the UTCI stress categories (moderate 26, strong 32, very strong 38 °C).

**Segment summaries:**
- `utci_mean`: mean over the footprint pixels; `utci_p90` for the worst part.
- `g`: **pixel mean of g(U)**, not g of the mean, because g is convex and a half-sunny segment would be under-charged.
- `exc26`, `exc32`, `exc38`, `exc46`: pixel means of max(0, U − t). Any weights on these thresholds give g exactly as
  Σ wₜ·excₜ, so the weights can change without recomputing.
- `shade_share`: pixel mean of 1 − SOLWEIG shadow (tree shade counts 0.97).

**Rules for special cases:**

| Case | Rule |
|---|---|
| Roof pixels | SOLWEIG computes Tmrt on the DSM surface, so pixels on the building mask carry roof-top values. They are dropped (8 % of segment pixels). |
| Covered or under a roof (OSM `covered`, or ≥ 50 % roof pixels; 4,727 segments) | Daytime UTCI capped at the local shade value: the median of shaded open walkable pixels within 30 m. Before this rule, 65 % of covered segment-steps came out sunlit, because 91 % of their pixels are roof in the DSM. |
| Indoor / underground (1,546 segments) | Constant 26 °C (g = 0), the same value as an AC vehicle. |
| No valid pixels | Mean of graph neighbours, then the step median. |
| Stop waiting spot | Mean of the coolest 10 % of the stop area's pixels: the waiting person stands in the best shade (at 13:00 a median 4.5 K below the area mean). |
| Stop shelter (OSM, 279 of 360) | Shelters are too small for the LiDAR building class, so daytime UTCI is capped at the local shade value. |

**Side-of-street check** (north–south streets, length-weighted):

| Street | 09:00 west / east | 18:00 west / east |
|---|---|---|
| Marszałkowska | 32.3 / **29.4 °C** (shade 0.38 / 0.86) | **37.3** / 38.0 °C (shade 0.77 / 0.68) |
| Aleje Ujazdowskie | 31.5 / **29.6 °C** | **36.3** / 37.4 °C |

In the morning the east side is shaded by its own frontage and cooler; in the evening the west side is.

## 10. Heat dose and the router ([`router/`](router/README.md))

**Exposure:** the trip is a path through space and time. U(t) is UTCI at the traveller's position and time.
- Walking and waiting are charged; waiting at home before departure is free.
- Riding an AC vehicle counts as U = 26 °C (dose 0).
- **Dose H = ∫ g(U(t)) dt** (K·s). A segment's traversal dose is g × length / speed at the time it is entered, with g
  interpolated linearly between steps.

**Walking:** 1.0 m/s; steps take twice as long; each signalised crossing adds 20 s of standing, charged at that
segment's rate. Origin and destination snap to the nearest node.

**Transit:**
- Board and alight only `ac_boardable` runs at stops inside the district.
- Arrive at the pole by the safety margin of §8; the margin is spent waiting and charged at the stop's waiting-spot
  value.
- At most one transfer (transfer walk ≤ 400 s, transfer wait ≤ 20 min).

**Options** (each with its own best departure time in the requested window): one scalar objective
cost = dose + λ · duration each, duration measured from leaving home.

| Option | λ (K) | Reading |
|---|---|---|
| coolest | 0.3 | lowest dose; λ only breaks ties against absurd detours |
| balanced | 10 | one extra minute is worth it if it saves ≥ 600 K·s, about one minute of walking at UTCI 34 °C |
| fastest | 100 | shortest trip; dose decides only between near-equal durations |
| walk only | – | quickest walking path at the lowest-dose departure (baseline, always returned) |

**Search:**
1. For every 30-min step in the window, one Dijkstra from the origin and one to the destination (`scipy.sparse.csgraph`,
   ~4 ms each), with edge weight time × (g + λ).
2. Boarding potential per stop event. One running minimum along each run gives the best boarding for every later
   alighting, so every (board, alight) pair is covered in O(events).
3. One transfer: each alighting joins walks to nearby poles and their next 12 departures; step 2 then repeats.
4. The best candidates are **re-priced exactly**, each segment at its entry time with time interpolation, and the
   options are chosen on the exact numbers.

**Speed:** median 0.23 s per 1-hour window, 1.6 s for an 8-hour window; heat and the 6 MB data bundle are loaded
once at startup.

Each option reports dose, total time, peak UTCI, minutes per UTCI category, share of walking in shade, the legs
(walk / wait / ride with line and run) and GeoJSON geometry. `price_path` prices any foreign walking line (e.g. from
another router) on the same field.

## 11. Results

**Example trips** (live app, default weights; "vs fastest" = coolest compared with fastest):

| Trip, window (CEST) | Coolest | Fastest | Dose vs fastest | Extra time |
|---|---|---|---|---|
| Plac Zbawiciela → Uniwersytet, 12:30–14:30 | leave 13:21, tram 4 + bus 111, 30.6 min, 8,283 K·s | leave 14:01, 18 + 175, 21.7 min, 15,916 K·s | **−48 %** (−85 % vs walking: 43.8 min, 55,983 K·s, peak 42 °C) | +8.9 min |
| Plac Zamkowy → Plac Zbawiciela, 08:00–18:00 | leave 08:08, tram 4, 21.5 min, 1,294 K·s | leave 10:17, 13 + 16, 17.6 min, 9,874 K·s | **−87 %** | +3.9 min |
| Centrum Nauki Kopernik → Hala Mirowska, 15:00–16:30 | 118 + 106, 37.1 min, 16,725 K·s | 106, 27.2 min, 24,818 K·s | −33 % | +9.9 min |
| Dworzec Centralny → Łazienki gate, 14:00–15:30 | 520, 27.3 min, 13,696 K·s | 525, 24.0 min, 14,358 K·s | −5 % | +3.3 min |
| Rondo Dmowskiego → Plac Trzech Krzyży, 18:30–20:00 | bus 517 at 19:42, 8.9 min, 2,326 K·s | same | 0 % (−76 % vs walking) | 0 |

**Where the gain comes from:** most of it is the AC ride and the departure time. Compared with walking at the
requested time, the coolest trip cuts the dose by 79–94 % and is also faster. Shade routing alone saves 1–17 % at a
fixed departure.

**Baselines** (all priced on our field):
- **ORS Shaded** (openrouteservice's public shade-aware walking router, queried with its 21-June shade slot): our
  coolest walk has **6–10 % less dose**. On the noon trip the ORS Shaded route has more dose than ORS's own
  unweighted route on our field; its shade layer is a synthetic nDSM for four fixed hours.
- **Building-only shade** (what tools without LiDAR trees see; routing on the shade engine's `f_bldg + f_both`):
  walking routes get 0–7 % more dose. For multimodal trips the answer is unchanged, because the ride dominates.

**Dose weights:** five weight sets were compared (linear from 26 °C, from 32 °C only, from 38 °C only, the default
weights, and a steeper set). The best option stays the same under every set (Kendall τ 0.85–0.98 against the default
ranking). Threshold-only weights make the walk router detour by up to +3.8 km, because time spent below the threshold
is free. The default weights, with their baseline term from 26 °C, keep the coolest walk within 0.9 km of the fastest,
which suits an elderly persona.

**District picture at 15:00** (the worst step): 85 % of the 777 km walk network is at UTCI ≥ 38 °C (very strong heat
stress) and 2 % is below 32 °C. Sunlit ground averages 43.8 °C UTCI against 38.0 °C in shade (air 37.2 °C). Between
13:00 and 18:00 the sunlit share of paved ground falls from 77 % to 37 %, which is why the same street is bearable at
18:00 and not at 13:00. Albedo barely differs between surfaces (0.15–0.16 at 10 m): at street level, shade is the
whole story.

## 12. App and visualisations ([`app/`](app/README.md), [`layers/`](layers/README.md), [`views/`](views/README.md))

**heat-app** (FastAPI + Leaflet, Polish and English, no accounts):
- **Map:** UTCI over the 33 steps with a slider, a shade overlay, and a click-for-time-series point query.
- **Planner:** pick origin, destination and time window; option cards (dose, time, peak UTCI, minutes per category,
  shade share, departure) and the routes on the map. Selecting a card moves the slider to that departure.
- **Colours:** hard steps at the UTCI categories 26 / 32 / 38 / 46 °C.
- **Frames:** pre-rendered EPSG:3857 XYZ PNG tiles at zoom 12–17 (z17 ≈ 0.7 m) by `app/render/` as a Cloud Run Job,
  proxied through the app so the bucket stays private.
- **Router:** the `router/` package is vendored into the image.

**Visualisation layers** (`layers/`, Cloud Run Jobs → `T12/`, described by `T12/manifest.json`):
- **Cool and water points:** 5,779 OSM points (benches, shelters, parks, fountains, drinking water, toilets, cool
  indoor places, water), each with 33-step shade, UTCI and Tmrt. Benches with mean shade ≥ 0.5 at 12:00–15:00 are
  flagged as shaded rest spots.
- **3D scene:** 27,169 building blocks from LiDAR (3 m height steps, so towers keep their setbacks); 57,619 trees
  from canopy-height local maxima with watershed crowns.
- **Frames per step:** SOLWEIG shadow (2 m, 4 m for phones), UTCI and Tmrt (4 m, plus grey value frames for hover
  read-outs).
- **Static layers:** Sentinel-2 broadband albedo (2026-06-27) and the Landsat surface temperature at 11:25.
- **Per-surface series**, including estimated incoming and reflected shortwave (K_in = sun·DNI·sin(el) + DHI·SVF,
  K_up = albedo·K_in).

**heat-views** (nginx, data baked into the image; MapLibre + deck.gl + Chart.js from CDNs):
- **Route heat map:** segments coloured by UTCI per step (a compact binary format instead of a 20 MB GeoJSON), with
  stops and cool points.
- **3D shadows:** extruded buildings and trees with the SOLWEIG shadow frame on the ground and the sun and cloud of
  each step.
- **Temperature and albedo:** UTCI/Tmrt/albedo/LST maps and the per-surface charts.

## 13. Known limitations

- **No evening heat release from pavement.** SOLWEIG's default ground scheme sets the surface-temperature excess to
  zero in shade and at sunset. After sunset UTCI is 1–1.3 K *below* air temperature, so the 21:00–22:00 values are a
  lower bound. The app and pages say so. (SOLWEIG's experimental storage scheme needs a land-cover grid and refuses
  tiled runs.)
- **Air temperature** is one park-station series for the whole district (cool side); the warm variant was not run.
  There is no street-canyon wind reduction.
- **Far shadows beyond 1 km** are missing at 20:00–20:30 (§6): SOLWEIG caps shadow reach at 1000 m by default.
- **DTM step** at E 638,000, N 488,000–489,000 (a LiDAR tile boundary) creates a 1–3 m false shade strip in the
  afternoon.
- **Water** is treated as sunlit open ground (no land cover, no evaporative cooling), so the Vistula shows as the
  hottest UTCI class while Landsat shows it as the coolest surface.
- **Conventions, not measurements:** indoor = 26 °C; a shelter = the local shade value; under-roof segments capped at
  local shade.
- **Router:** the search is approximate (walk legs chosen on one 30-min step's tree, then re-priced exactly), with at
  most one transfer. Ride geometry is a straight line between stops. Because riding is dose-free, the coolest option
  sometimes rides past the destination and comes back.
- **Cool points** come from OSM only: the city's summer drinking fountains and water curtains need an API token.
- **Building-only baseline** reclassifies shade zones; it is not a SOLWEIG run without trees.
- Heat is loaded at startup: if the segment heat is regenerated, redeploy `heat-app` and `t09-router` and rebuild
  `heat-views`.

## 14. Rebuilding

Every stage runs on GCP (Cloud Run Jobs, images in
`europe-central2-docker.pkg.dev/hackaton-honory/hackathon/`). Each module README has the exact commands. In order:

1. `lidar/`: `t01-tiles` (four regions), then `t01-mosaic`.
2. `shade/`: `far_dsm.py`, then `run.py` with `--far-dsm` (one 8-vCPU VM, ~80 min).
3. `walk/`: `fetch.sh` → `python -m walk.run` → `python -m walk.routes` → `python -m walk.to_bq`.
4. `transit/`: `python -m transit.stop_events`, then `python -m transit.report` (writes `transit/line_delays.csv`).
5. `meteo/`: job `t05-meteo` with `SCRIPT=forcing.py`, then `pmwc.py`.
6. `solweig_ref/`: `MODE=tile` per tile (`plan.json` gives each tile's halo), then `MODE=mosaic`.
7. `utci/`: job `t07-final`.
8. `heat/`: job `t08-heat` with `SOURCE=t07,OUT=final`.
9. `router/`: `python -m router.build_bundle`, then build and deploy `t09-router`.
10. `app/`: render jobs `t10-render-t07-utci` and `t10-render-t07-shade`, then build and deploy `heat-app`.
11. `layers/`: jobs `t12-*` (points, scene, frames, manifest).
12. `views/`: `gcloud builds submit --config views/cloudbuild.yaml --region europe-central2 views`.

Compute limits of the project: Cloud Run Jobs in five regions (europe-central2, -west1, -west3, -west4, -north1),
20 vCPU / 40 GiB per region and at most 8 vCPU / 32 GiB per task (memory usually binds); Compute Engine 12 vCPU, no
GPU.
