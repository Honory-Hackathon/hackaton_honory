# Śródmieście 2076: planning a trip in space and time to minimise UTCI

**Specification v1.0** · 2026-10-17 · Scope: Śródmieście · Heat day: Sun 2026-06-28 · Transit: recorded operations Sat 2026-10-17 (BigQuery)

This specification sets out the goal, scope, data, modelling approach and route metric of the project. It is deliberately general. The detailed models (shade algorithm, thermal model, dose weights, router) are to be worked out later. Values given below are starting points for those discussions, not fixed definitions.

## 1. Story

> In 2076, as a particularly heat-vulnerable pensioner, I want to go from X to Y. The sun is scorching and I want to minimise my UTCI on the way so that I don't get sick or die. We recreate the conditions of 2026-06-28, as they will likely match many summer days 50 years from now. I use the app to plan my route not only in space but in time.

The product is a route planner that answers both *which way* and *when to leave*. It minimises the felt heat (UTCI) along the whole trip: walking, waiting at stops and riding.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Shade | Our own shade model from LiDAR, covering buildings **and trees**. The MIT data shades from building heights only. Shade is computed on the 2D map of public walkable space and transit stops, every 30 min from 06:00 to 22:00. If that is too heavy, use fewer time steps. |
| 2 | Area | Śródmieście only (PRG unit 146510_8, ≈15.5 km²). A surrounding halo is included as shadow casters only. |
| 3 | Modes | Walking and public transport. Public transport means **trams and buses only**: no bikes, no metro, no suburban rail (SKM, KM, WKD). Only vehicles with air conditioning are boarded. The walk is timed so the wait at the stop is short, and the stop itself is part of the exposure. |
| 4 | Canopy | From **LiDAR**; see §5.2. |
| 5 | Stack | Pure Python, unless a component clearly needs something else. |
| 6 | Metric | UTCI over time along the route is what we minimise; see §7. |
| 7 | Sources | LiDAR: GUGiK geoportal ([dane pomiarowe LiDAR](https://www.geoportal.gov.pl/pl/dane/dane-pomiarowe-lidar-lidar/)). Temperature: Landsat C2 L2 ([Planetary Computer](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2)) plus station data. |
| 8 | Scenario | Weather of 2026-06-28 replayed as a typical 2076 summer day. City geometry as surveyed (2023–2025). |
| 9 | Resolution | 1 m grid. |
| 10 | Physics | Simple, time- and space-varying. Two levels: a simple proposed model (§13) and SOLWEIG in its simplest form as the reference (§6.3). |
| – | Transit data | Real-time vehicle data already stored in BigQuery, from a recorded day. No timetable replay. |
| – | Home | Waiting at home before departure is **free**: it does not count towards the heat dose. |
| – | Infrastructure | Any infrastructure that best fits the goals may be created (GCP free tier; cost is not a constraint). |

## 3. Scope and time

- **Area:** Śródmieście district boundary. Origins and destinations lie inside it, and heat is evaluated only inside it. Shadow casters come from a ~1 km halo, because tall buildings just outside the district (e.g. around Rondo Daszyńskiego) shade it at low sun.
- **Grid:** 1 m, EPSG:2180 (PL-1992), heights EVRF2007.
- **Heat day:** Sunday 2026-06-28.
  - Warszawa-Okęcie Tmax 38.1 °C; Filtry 39.7 °C.
  - Clear sky most of the day; sunrise 04:17, sunset 21:01 CEST.
- **Time steps:** 06:00–22:00 every 30 min (33 steps). The sun is up for about 30 of them; after sunset only longwave radiation matters. Values between steps are interpolated in time. Hourly steps are the fallback.
- **Transit day:** Saturday 2026-10-17, 06:00–22:00, recorded in BigQuery. It is mapped clock time to clock time onto the heat day.
- **Persona:** an elderly pedestrian walking about 1.0 m/s and standing while waiting.

## 4. Data

| Source | Role | Notes |
|---|---|---|
| GUGiK LiDAR point clouds (LAZ) | Buildings, ground, trees | WFS sheet index `mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze` gives direct download URLs, no login. The **2023-09-05** flight is leaf-on and used for canopy (~7 GB for the district). The **2025-04-27** flight is the newest and used for buildings and ground (~7 GB). |
| GUGiK NMT / NMPT (optional) | Cross-check of ground and surface models | Derived from the same LiDAR. |
| Landsat C2 L2 surface temperature | Surface heat pattern, urban surface-heat anomaly, validation | No scene on 06-28. Nearest clear scene: `LC09_L2SP_187024_20260625_02_T1` (06-25, 11:25 CEST, ~5% cloud). Backup: `LC08_L2SP_188024_20260624`. |
| IMGW stations (Okęcie hourly synop, Filtry) | Air temperature, humidity, wind | Reference meteorology for the heat day. |
| ERA5 (via Open-Meteo) | Direct and diffuse solar radiation, gap filling | Hourly. |
| Sentinel-2 L2A (06-27), BDOT10k, OSM | Surface types, albedo, building footprints, walkable network, crossings, covered ways, stop platforms, stop shelters | |
| City tree inventory | Species, validation of LiDAR trees | 2010–13 survey; does not cover large parks. |
| BigQuery `hackaton-honory.warsaw_transport` | Recorded vehicle positions (line, brigade, vehicle number), vehicle register with air-conditioning flag, GTFS stops and stop sequences | Sat 2026-10-17 06:00–22:00 is fully covered: every minute, ~1,100 vehicles per hour. |

## 5. Shade model (LiDAR)

### 5.1 Geometry

From the LiDAR point classes (ground, vegetation, buildings), build these 1 m rasters:
- **Ground and buildings:** a ground model and a building surface model.
- **Canopy:** a canopy height model including crown base height.
- **Light through crowns:** a crown-transmissivity estimate.
- **Surface types:** surface type per pixel.
- **Walkable mask:** public walkable space plus stop waiting areas, from OSM/BDOT10k.

Known artefacts need handling: cranes and temporary structures, arcades and covered walkways, underground passages, bridges.

### 5.2 Canopy: LiDAR or city data?

**LiDAR.** The 2023-09-05 flight is full leaf-on and gives three things the city's canopy data cannot:
- **Real crown heights.**
- **Crown base height:** low morning and evening sun passes under crowns.
- **Measured light transmission per pixel:** the share of laser pulses reaching the ground, so we don't rely on one constant.

The city's tree inventory is old and incomplete. The city's crown map (Mapa Koron Drzew) is view-only and not downloadable. Both remain useful for species information and validation.

Caveat: canopy is as of September 2023. Trees felled since then can be detected with the 2025 flight; trees planted since then are missing.

### 5.3 Sun and shadows

For each time step:
- **Sun position:** computed for the district (e.g. with pvlib).
- **Shadows:** cast from buildings and from trees, including low-sun shadows of tall buildings in the halo.
- **Sky view factor:** computed once.

Per walkable pixel and time step the shade model yields the **shade zone shares** used by the thermal model (§13):

| Share | Meaning |
|---|---|
| `f_sun` | in direct sun |
| `f_bldg` | shaded by buildings only |
| `f_tree` | shaded by trees only |
| `f_both` | shaded by both |

The four shares sum to 1 per pixel, or per path segment when aggregated.

## 6. Thermal model

### 6.1 Principle

Within a district the air temperature varies by about 1–2 K. Mean radiant temperature (Tmrt) differs by about 25–35 K between sun and shade, which means several degrees of UTCI. The model therefore keeps air temperature simple and puts its detail into radiation and surfaces:

```text
station meteorology (Ta, RH, wind)  ─┐
shade zone shares f_* (§5)          ─┼─►  local Ta, Tmrt  ─►  UTCI(Ta, Tmrt, wind, RH)
Landsat surface-heat anomaly        ─┤
sun elevation, radiation            ─┘
```

UTCI is computed with an existing implementation, e.g. `pythermalcomfort.models.utci` (wind at 10 m; valid for Tmrt − Ta between −30 and +70 K and wind 0.5–17 m/s).

### 6.2 Simple model: the team proposal

The default thermal model is the proposed closed-form function in **§13**. In outline, it takes:
- **Local air temperature:** the station temperature plus a share of the Landsat surface-heat anomaly.
- **Shortwave term:** solar load on a standing person, scaled by how much each shade zone transmits.
- **Longwave term:** surface temperature offsets per shade zone, plus background wall emission.

It is fast enough to evaluate on every walkable pixel and every time step.

### 6.3 SOLWEIG, in its simplest form

SOLWEIG (Lindberg et al. 2008, UMEP) is the reference physical model. In its simplest configuration it needs only:

- **Rasters:**
  - building + ground surface model (DSM)
  - ground model (DEM)
  - vegetation canopy model (CDSM; trunk zone left at its default)
  - optionally a land-cover raster
- **Meteorology per time step:** air temperature, relative humidity, wind and global radiation. Direct and diffuse radiation are optional.
- **Defaults:** standing person, isotropic sky, default tree transmissivity, default surface parameters.

It returns, per time step and pixel, shadow and **Tmrt**. UTCI then follows from the same UTCI function as above.

It can run in a pure-Python setting through the SOLWEIG code from UMEP-processing or SOLWEIG-GPU (pip package, GPU recommended).

Role in this project:
- **Benchmark:** a reference to check and tune the simple model (§13) on the same inputs, on the full district or a sample of time steps.
- **Upgrade path:** if the simple model is not convincing, run SOLWEIG for the whole district. That is a few GPU-hours at 1 m.

### 6.4 Landsat

Landsat sees surface temperature (roofs, canopy tops, open pavement) once, at about 11:25, and not on the heat day itself. It is therefore used for two things:
- **Static anomaly map:** the urban surface-heat anomaly per location (the `pmwc` input in §13).
- **Validation:** checking the spatial pattern of the modelled surface heat.

It is not used directly as hourly temperature.

## 7. Metric: UTCI in time

- **Exposure along a trip:** the trip is a path through space and time. At every moment the traveller is walking, waiting at a stop or riding. Exposure is U(t) = UTCI at the traveller's location and time, interpolated between time steps.
- **In an air-conditioned vehicle:** the cabin is assumed comfortable (UTCI ≈ 26 °C).
- **At home:** waiting before departure is free.
- **Heat dose:** the quantity to minimise is the time-integrated heat stress above a threshold:

```text
H = ∫ g(UTCI(t)) dt        g = 0 below the threshold, growing faster in higher UTCI stress categories
```

  Initial proposal for g, to be discussed: start counting at 26 °C (moderate heat stress) and weight higher at 32 °C (strong) and 38 °C (very strong). This follows the UTCI categories and Polish heat-mortality findings for the elderly.

- **Trade-off:** the dose is balanced against total travel time. The planner shows a few route options, e.g. *coolest*, *balanced* and *fastest* (still AC-only), each with its best departure time.
- **Reported per option:** dose, total time, peak UTCI, minutes per UTCI category, share of walking in shade.

## 8. Public transport

- **Data:** recorded operations from BigQuery (Saturday 2026-10-17), not a timetable.
  - Vehicle positions are matched to stops to get, for every run, the real arrival and departure time at each stop.
  - Each run also gets the vehicle that actually drove it.
- **Air conditioning:** taken from the vehicle register for the actual vehicle. Runs without AC, or with an unknown vehicle, are not boarded.
- **Modes:** trams and buses only, boarding and alighting at stops inside Śródmieście.
- **Timing the walk:**
  - The traveller leaves home so as to reach the stop shortly before the recorded departure, with a small safety margin.
  - Waiting at the stop, including transfers, is charged at the UTCI of the best spot near the stop (shade, shelter).
  - Waiting at home is free.

## 9. Router

- **Walk network:** from OSM, clipped to the district, split into short segments. Each segment carries its UTCI or dose per time step, computed from the walkable pixels it covers.
- **Time-dependent costs:** each segment is costed at the time the traveller enters it. Signalised crossings add a short wait.
- **Search:** combines walking with transit runs and searches over departure times. It always includes a walk-only option and a fastest baseline for comparison.
- **Baselines for comparison:** ORS Shaded and building-only shade (the MIT data).

## 10. Infrastructure

Any infrastructure that best fits the goals may be created, on the GCP free tier in project `hackaton-honory` (europe-central2). Indicatively:
- **Compute:** a compute VM for LiDAR processing and the shade model, and a GPU VM if SOLWEIG is run for the district (needs a GPU quota increase).
- **Storage:** a GCS bucket for LiDAR files and rasters, and a BigQuery dataset for the segment, stop and route tables next to `warsaw_transport`.
- **App:** a small web service, e.g. Cloud Run, for the map and planner.

Heavy computation runs in the cloud, not on laptops.

## 11. Outputs

- **Rasters (1 m):** shade zone shares, Tmrt and UTCI per time step on walkable space, plus a time-slider map.
- **Segment table:** UTCI and dose per path segment and time step.
- **Stop table:** UTCI per stop and time step.
- **Transit tables:** recorded stop events with AC flag.
- **Route results:** legs, times, dose and exposure summary.

## 12. Validation

- **Shadows:** visual checks of shadows at known places (arcades, tree-lined streets, plazas, tall towers).
- **Trees:** LiDAR tree heights against the tree inventory.
- **Surface heat:** modelled surface-heat pattern against Landsat.
- **Simple model vs SOLWEIG:** compare Tmrt and UTCI on a sample.
- **UTCI sanity:** no invalid values; plausible sun–shade differences; evening values close to air temperature.
- **Router:** dose reduction against extra travel time on a few demo origin–destination pairs.

Topics for the in-depth discussions:
- Shade algorithm details and crown transmissivity.
- Surface temperature in time (thermal memory, evening heat release).
- Wind in street canyons.
- The dose function and its weights.
- The safety margin before departure.
- Router algorithm.

## 13. Closing: proposed simple model for UTCI inputs

The proposed closed-form model returns the local air temperature and the mean radiant temperature for a location and time. They are ready to pass into an external `UTCI(t_air, mrt, wind, rh)` function.

**Inputs:**
- `t_air_meteo`: station air temperature for the time step.
- `alt_deg`: solar elevation.
- `f_sun`, `f_bldg`, `f_tree`, `f_both`: shade zone shares from §5.3.
- `pmwc`: the Landsat surface-heat anomaly at the location (§6.4).

SOLWEIG in its simplest form (§6.3) serves as the reference for checking and tuning its coefficients.

```python
import numpy as np


def compute_inputs_for_utci(
    t_air_meteo: float,
    alt_deg: float,
    f_sun: float,
    f_bldg: float,
    f_tree: float,
    f_both: float,
    pmwc: float = 0.0,
) -> tuple[float, float]:
  """Returns (t_air_local, mrt_celsius) ready to pass into an external UTCI(t_air, mrt, wind, rh) function."""
  # 1. Local air temperature adjusted for Surface UHI anomaly
  # (Empirical ratio: Canopy Layer air UHI is ~15% of Landsat Surface UHI anomaly)
  t_air_local = t_air_meteo + 0.15 * pmwc

  alt_rad = np.radians(np.clip(alt_deg, 2.0, 90.0))
  sin_a = np.sin(alt_rad)
  cos_a = np.cos(alt_rad)

  # 2. Linearized radiative heat transfer coefficient h_r = 4 * eps_p * sigma * T_k^3
  h_r = 4.0 * 0.97 * 5.67e-8 * ((t_air_local + 273.15) ** 3)

  # 3. Shortwave solar radiation load on a standing human (VDI 3787 projection factor f_p)
  f_p = 0.308 * cos_a + 0.04
  i_direct = 880.0 * (sin_a**0.25)
  i_diffuse = 110.0 * sin_a

  # Max shortwave equivalent temperature rise in full sun (absorptivity = 0.70)
  delta_t_sw_max = (0.70 * (f_p * i_direct + 0.5 * i_diffuse)) / h_r

  # Apply zone shortwave transmissivities
  delta_t_sw = delta_t_sw_max * (
      1.00 * f_sun + 0.12 * f_tree + 0.07 * f_bldg + 0.03 * f_both
  )

  # 4. Longwave surface temperature offset (T_surf - T_air_local) * Ground View Factor (0.40)
  # Unshaded asphalt heats up by ~15-20°C at high sun; background walls add a small PMWC term
  dt_surf_sun = (15.0 + 0.30 * max(0.0, t_air_local - 25.0)) * sin_a
  dt_surf_bldg = 2.0
  dt_surf_tree = -0.5  # Leaf transpiration keeps canopy near/below air temp
  dt_surf_both = -1.5

  delta_t_lw = (
      0.40
      * (
          dt_surf_sun * f_sun
          + dt_surf_bldg * f_bldg
          + dt_surf_tree * f_tree
          + dt_surf_both * f_both
      )
      + 0.20 * pmwc
  )  # Background thermal wall emission in hot urban quarters

  mrt_celsius = float(t_air_local + delta_t_sw + delta_t_lw)
  return t_air_local, mrt_celsius
```

**Points to settle when this model is discussed in depth:**
- **Radiation source:** the model uses a parametric clear-sky irradiance (`880·sin^0.25`, `110·sin`). The measured ERA5 direct/diffuse radiation of 2026-06-28 could be used instead.
- **Projection factor:** `f_p = 0.308·cos(alt) + 0.04` is a simplified form of the VDI 3787 factor.
- **Missing effects:** there is no sky-view-factor or thermal-memory term. Surface heating follows the current sun elevation only, so evening pavement heat release is not represented.
- **Landsat anomaly:** `pmwc` enters both air temperature (×0.15) and longwave (×0.20). It is a static map from one Landsat scene.
- **Coefficients:** zone transmissivities (1.00 / 0.12 / 0.07 / 0.03), surface offsets and the 0.40 ground view factor are the natural parameters to tune against SOLWEIG.
