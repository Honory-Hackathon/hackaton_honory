# Śródmieście 2076: planning a trip in space and time to minimise UTCI

**Specification v1.0** · 2026-10-17 · Scope: Śródmieście (TERYT 146510_8, 15.5 km²) · Heat replay day: Sun 2026-06-28 · Transit: recorded operations Sat 2026-10-17 (BigQuery)

What to build, from which data and with which equations: a LiDAR-based 1 m shade model, a simple time-varying surface and radiant temperature model, a UTCI field every 30 min from 06:00 to 22:00, and a walk + air-conditioned tram/bus router that times the walk so the pensioner barely waits at the stop. Rendered version: [specification-v1.0.html](specification-v1.0.html).

Badges: `[V]` verified (query, HEAD, source code) · `[L]` literature/code checked · `[D]` derived arithmetic · `[A]` assumption to confirm.

## 1. TL;DR

Build our own *1 m LiDAR shade + UTCI field* for Śródmieście at *33 time slots*, and route on a heat dose, not on travel time.

Leaf-on LiDAR from 2023-09-05 gives canopy; LiDAR from 2025-04-27 gives buildings and ground. A SOLWEIG-style parametric model turns shade, sky view factor and surface class into Tmrt, and Tmrt into UTCI. The router minimises a convex UTCI dose over walking and waiting. It boards only trams and buses that actually had AC in the recorded BigQuery operations, and times the walk so the pensioner arrives just before the vehicle.

**Time slots:** 33 — 06:00–22:00 every 30 min; 30 have sun above the horizon

**Raster:** 37 Mpx — district + 1 km halo for shadow casters, 1 m, EPSG:2180

**LiDAR download:** ~24 GB — 2023 leaf-on ≈ 9 GB, 2025 ≈ 15 GB (with halo)

**Compute:** 1–3 vCPU-h — ≈ 10–25 min on one 12-vCPU VM, \< €2; full 30-min resolution is affordable

### Your ten points, resolved

- **1 Shade:** Own model from GUGiK LiDAR at 1 m, every 30 min, 06:00–22:00 (33 slots). Buildings and trees, with per-pixel crown transmissivity. We need our own model because the MIT data shades from building heights only. Trees are the main source of shade on walkable space in Śródmieście, and they are missing there. [§5](#shade)
- **2 Area:** Śródmieście only, PRG boundary 146510_8 (15.5 km²). Shadow casters are read from a 1 km halo, so towers on Rondo Daszyńskiego still shade the district in the evening. [§2](#scope)
- **3 Modes:** Walk + tram + bus. No bikes. Metro is out of scope. Transit is **real operations recorded in BigQuery** (Sat 2026-10-17, 06:00–22:00), replayed on the 06-28 heat clock. A run may be boarded only if the vehicle that actually drove it has AC. The walk is timed to arrive a fixed margin before the vehicle's recorded departure, and the stop wait is costed with the UTCI at the stop. [§9](#transit)
- **4 Canopy:** **LiDAR, not city canopy data.** The reasons are below; city data is used for species and validation only. [§4](#geom)
- **5 Stack:** Pure Python, all pip wheels: laspy\[lazrs\], numpy, numba, scipy, rasterio, shapely/geopandas, pvlib, pythermalcomfort, pystac-client. No PDAL, QGIS, UMEP or Java. [§12](#compute)
- **6 Metric:** UTCI field U(x, t) at 1 m × 30 min. The route cost is heat dose H = ∫ g(U) dt, with g convex above 26 / 32 / 38 °C, traded against total time. [§8](#metric)
- **7 Sources:** LiDAR: GUGiK WFS index → direct LAZ URLs, no auth (verified). Landsat C2 L2 on Planetary Computer has **no scene on 06-28**. LC09 06-25 (5% cloud) calibrates the surface model; air temperature comes from IMGW. [§3](#data)
- **8 Story:** Replay the 2026-06-28 forcing unchanged as a typical 2076 summer day, with the city geometry as of 2025. The pensioner persona walks at 1.0 m/s and uses elderly-shifted dose weights. [§2](#scope)
- **9 Grid:** 1 m everywhere: shade, SVF, surface class, Tmrt and UTCI. Physics is evaluated only on walkable and stop pixels. [§4](#geom)
- **10 Physics:** Ta: uniform from IMGW plus an urban offset. Ts: SOLWEIG sine-wave ground temperature with 30-min thermal memory, calibrated to Landsat. Tmrt: six-direction SOLWEIG formulation. A force-restore model is the upgrade if calibration fails. [§6](#temp)

&nbsp;

- `[V]`verified today (query, HEAD, source code)
- `[L]`literature / code, checked
- `[D]`derived arithmetic
- `[A]`assumption, to confirm

## 2. Story, scope and time grid

“In 2076, as a particularly heat-vulnerable pensioner, I want to go from X to Y. The sun is scorching and I want to minimise my UTCI on the way so I don't get sick or die. We recreate the conditions of 2026-06-28, which will likely match many summer days 50 years from now. I use the app to plan my route not only in space but in time.”

| Item | Specification |  |
|----|----|----|
| Area of interest | Śródmieście, PRG unit 146510_8 (WFS `A05_Granice_jednostek_ewidencyjnych`), 15.5 km², perimeter 19.2 km, bbox 5.6 × 6.0 km. Origins and destinations must lie inside; heat is computed only inside. | `[V]` |
| Caster halo | District + 1 km buffer = 37.2 km². Buildings come from 2025 LiDAR in the whole halo; canopy (2023) within 200 m of the district (19.5 km²), because tree shadows are short. | `[D]` |
| CRS / grid | EPSG:2180 (PL-1992), heights EVRF2007, 1 m cells aligned to integer metres. Outputs are also published in WGS84 for the map. |  |
| Replay day | Sunday 2026-06-28: Okęcie Tx 38.1 °C, Filtry 39.7 °C, clear 05–16 h. Sunrise 04:17, sunset 21:01 CEST. | `[V]` |
| Time grid | t_k = 06:00 + 30 min·k, k = 0…32 (33 slots, local CEST). The sun is above 2.9° from 06:00 to 20:30 (30 slots). 21:00–22:00 is longwave only. Values between slots are interpolated linearly in time. Fallback if compute were short (not expected): hourly slots, 17. | `[D]` |
| 2076 framing | No ΔT is added: the forcing is replayed as observed, and the buildings and trees are those of 2025, not a 2076 city. Both points belong on one caveat slide. | `[A]` |
| Transit day | Real operations of **Saturday 2026-10-17**, recorded in BigQuery, mapped clock-time to clock-time onto the 06-28 heat day (10:00 on 10-17 ↔ 10:00 on 06-28). The caveat slide notes that this is an October Saturday's service on a June Sunday's weather. | `[A]` |
| Persona | Walking 1.0 m/s; standing while waiting; arrival window and origin/destination chosen by the user. Waiting at home before leaving is indoors and is **not** counted in the dose. | `[A]` |

## 3. Data sources

| Source | Use | Vintage | Access | Size (AOI) |  |
|----|----|----|----|----|----|
| GUGiK LiDAR LAZ, 2023 (GI-FOTO.6201.22.2023) | Canopy height, crown base, transmissivity | 2023-09-05, **leaf-on**, ~33 pts/m² | WFS `gugik:SkorowidzDanychPomiarowychLIDAR2023` → `url_do_pobrania`, no auth | 74 sheets in district = 7.0 GB; ~9 GB with 200 m halo | `[V]` |
| GUGiK LiDAR LAZ, 2025 (GK-FOTO.6201.5.2025) | Buildings, ground (DTM), tree-removal check | 2025-04-27, early leaf-out, ~17 pts/m² | same, layer `…LIDAR2025` | 74 sheets = 6.7 GB; ~15 GB with 1 km halo | `[V]` |
| `warsaw_transport.vehicle_positions` (BigQuery, hackaton-honory) | **The transit data:** realised trams and buses (position, line, brigade, vehicle number) → stop events and departures | Sat 2026-10-17 06:00–22:00 local: all 60 min of every hour present, ~1,100 fresh vehicles and ~0.5M fresh pings per hour | existing | ~8M fresh rows that day | `[V]` |
| `warsaw_transport.ztm_vehicles` + `ztm_vehicle_models` | AC of the vehicle that actually ran (`air_conditioning` BOOL), joined on `(mode, vehicle_number)` → `model_id` | register snapshots since 10-16 | existing | 31k / 3.7k rows | `[V]` |
| `warsaw_transport.gtfs_*` (mkuran feed 2026-10-16) | Stop locations and stop sequences only (service `2026-10-17:SbS`), to snap positions to stops | valid 10-16…10-24 | existing | 7.85M stop_times | `[V]` |
| IMGW synop Okęcie (12375) hourly + klimat Filtry | Ta, RH, wind, cloud; urban Ta offset | 2026-06-28 | danepubliczne.imgw.pl zips | \<1 MB | `[V]` |
| ERA5 via Open-Meteo archive | DNI, DHI, GHI; gap-fill; optional sky longwave | hourly, preceding-hour means | open API | KB | `[V]` |
| Landsat C2 L2 ST, `LC09_L2SP_187024_20260625_02_T1` | Calibration of the surface-temperature model (not a model input on 06-28) | 2026-06-25 09:25 UTC (11:25 CEST), 4.96% scene cloud | Planetary Computer STAC `landsat-c2-l2`, asset `lwir11` + `qa_pixel` | ~2 MB clipped | `[V]` |
| Landsat backup `LC08_L2SP_188024_20260624` | Second calibration view | 06-24, 19.8% cloud | same | ~2 MB | `[V]` |
| Sentinel-2 L2A 06-27 (34UDC/34UEC) | Albedo and NDVI per surface class (grass vs paving) | 2026-06-27, 0–8% cloud | Earth Search / PC STAC | ~100 MB clipped | `[L]` |
| BDOT10k Warszawa (GPKG) | Building footprints (mask clean-up), surface material, water, land cover | 2025-06 | GUGiK, open | 88 MB | `[L]` |
| OSM extract (Geofabrik/BBBike PBF) | Walk graph, sidewalks, crossings, covered/tunnel ways, stop platforms, `shelter=yes`, surface tags | 2026-10 | PBF, pyosmium | 300 MB (Mazowieckie) | `[L]` |
| City tree inventory (old API) | Species/genus for τ prior; validation of LiDAR crown heights | survey 2010–13; 8,142 trees in Śródmieście | local copy `downloads/trees/` | 63 MB | `[V]` |
| Not used: Mapa Koron Drzew, Meta canopy, ORS Shaded / MIT shade | See §4; baselines for the related-work slide only |  |  |  |  |

### Forcing for 2026-06-28 (ERA5 grid point 52.27 N 20.96 E, local time)

Ta below is ERA5. The spec takes Ta, RH and wind from IMGW Okęcie hourly and uses ERA5 only for radiation. Radiation values are means over the hour *ending* at the stamp, so a value stamped hh:00 belongs to hh−1:30. `[V]`

| h   | Ta °C | RH % | v10 m/s | DNI W/m² | DHI W/m² | Cloud % |
|-----|-------|------|---------|----------|----------|---------|
| 06  | 24.5  | 55   | 2.0     | 379      | 37       | 3       |
| 08  | 28.5  | 45   | 1.1     | 690      | 75       | 0       |
| 10  | 33.5  | 34   | 1.2     | 811      | 96       | 9       |
| 12  | 36.3  | 27   | 1.8     | 865      | 101      | 0       |
| 13  | 37.1  | 25   | 1.8     | 871      | 101      | 1       |
| 15  | 38.1  | 24   | 2.7     | 827      | 105      | 9       |
| 17  | 38.1  | 22   | 2.5     | 743      | 95       | 30      |
| 19  | 37.1  | 22   | 2.6     | 459      | 78       | 49      |
| 21  | 35.3  | 29   | 0.9     | 84       | 14       | 14      |
| 22  | 34.2  | 31   | 0.9     | 0        | 0        | 9       |

The table shows selected hours; ingest all 06–22 h. The cloud in the evening (30–63% from 17 h) is real ERA5 cloud. Use ERA5 DNI/DHI as they are, not a clear-sky model.

## 4. Geometry from LiDAR

**Answer to question 4: canopy from LiDAR or from city data?**

**LiDAR.** The 2023-09-05 scan is full leaf-on and LiDAR-derived (the earlier “image-matched” suspicion was wrong) `[V]`. It gives the canopy top *and* crown base: low sun at 06–08 and 19–21 h passes under crowns, which no 2D crown map can represent. It also gives a measured per-pixel gap fraction, so τ is not a single constant.

The city data can't do this:

- **Tree inventory:** 2010–13 points, crown diameter but no crown shape, large parks missing. Śródmieście holds 8,142 trees and 15.8 ha of crowns.
- **Mapa Koron Drzew:** 7.1M crown polygons, but view-only. Getting the file needs a request to the city environment office (BOŚ). `[V]`

Use the city data for genus and health (a prior on τ) and as validation of crown heights.

### 4.1 Ingest

1.  Query the WFS indexes for the district + halo bbox. The URL is `https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze`; the bbox order is **northing, easting**. Keep `godlo`, `akt_data` and `url_do_pobrania`.
2.  Download VM → GCS with 8 parallel streams. The server ignores HTTP Range, so always fetch whole files.
3.  Read with `laspy` + `lazrs` in chunks and bin to the 1 m grid with `numpy`/`numba`. Classes per GUGiK: 2 ground, 3/4/5 vegetation (\<0.4 / 0.4–2 / \>2 m), 6 buildings, 7 noise, 8 water. Drop 7 and 12 (overlap).

### 4.2 Rasters (1 m, float32 COG)

| Raster | Rule | Epoch |
|----|----|----|
| `dtm` | Mean z of class 2 per cell. Fill holes (under buildings) by linear interpolation (`scipy.interpolate` on the hole boundary). Cross-check against GUGiK NMT 1 m, \|Δ\| \< 0.3 m. | 2025 (leaf-off → best ground) |
| `bldg_dsm` | Max z of class 6 per cell; 3×3 morphological close; elsewhere `dtm`. Bridges and footbridges come from OSM `bridge=yes` ways: they are walkable surfaces, not casters over the deck. | 2025 |
| `chm_top` | p95 z of class 5 − dtm, minus building cells. Remove cells where the 2025 scan has no class 3–5 return within 2 m, i.e. trees felled since 2023. | 2023, 2025 check |
| `chm_base` | p10 z of class 5 − dtm, smoothed (median 5×5 inside each connected crown). Fallback: 0.25·chm_top (SOLWEIG trunk-zone default). | 2023 |
| `gap_frac` | Within canopy cells: first returns reaching the ground or below 1.5 m (classes 2/3) ÷ all first returns, aggregated 3×3 (~300 pts). τ_v = clip(gap_frac, 0.02, 0.5). | 2023 |
| `surface_class` | Priority order: building (class 6 / BDOT) → water (class 8 / BDOT) → canopy-covered ground (keep underlying class) → grass (S2 NDVI \> 0.4, class 2/3) → asphalt / granite-concrete paving / cobble from OSM `surface=*` and BDOT material → default paving. | mixed |
| `walk_mask` | Not building, not water, and one of: inside OSM pedestrian/plaza areas; within w/2 of a footway/sidewalk/path/crossing (w from OSM `width`, else 2.5 m sidewalk, 4 m pedestrian street, 3 m crossing); within 15 m of a stop platform. Expect roughly 3–5 km² (20–30% of the district). | OSM + BDOT |

Known artefacts to check in a visual QA of 5 spots: cranes and scaffolding in 2025 (they will cast false shadows); arcades on Marszałkowska and MDM (covered walkways → OSM `covered=yes` overrides to full shade); underground passages at Centralna and Rondo Dmowskiego (OSM `tunnel`/`indoor` → indoor rule, §7).

## 5. Sun and shade

### 5.1 Sun positions

Use `pvlib.solarposition.get_solarposition` at the district centroid (52.230 N, 21.012 E) for each slot; the 6 km extent changes elevation by \<0.05°. Spot values `[D]`:

| CEST   | 06:00 | 08:00 | 10:00 | 12:30 | 15:00 | 18:00 | 20:00 | 20:30 | 21:00 |
|--------|-------|-------|-------|-------|-------|-------|-------|-------|-------|
| elev ° | 12.5  | 30.4  | 48.1  | 61.0  | 50.5  | 24.1  | 6.8   | 2.9   | −0.7  |
| azim ° | 68    | 91    | 118   | 176   | 236   | 277   | 300   | 305   | 311   |

### 5.2 Shadow algorithm (per slot, numba)

Use the SOLWEIG / Ratti & Richens shift-and-compare, written in numba. For each slot, march the DSM toward the sun in 1 m steps. A pixel p is shaded if some cell at horizontal distance d along the sun azimuth has height \> z(p) + d·tan β.

    bldg_sunlit[p,k]  ∈ {0,1}      # casters: bldg_dsm only
    veg_hit[p,k]      ∈ {0,1}      # ray intersects the canopy slab chm_base…chm_top
    tau_hit[p,k]      = τ_v(first crown cell hit) ** (1 / sin β)    # slant path (Beer–Lambert)
    s[p,k] = bldg_sunlit · (1 − veg_hit · (1 − tau_hit))          # sunlit fraction of the direct beam, 0…1

- **Reach cap:** min(ΔH_max/tan β, 2 km), with ΔH_max the tallest relative height in the halo (Varso ≈ 310 m, PKiN 237 m). Early exit when the ray rises above the halo maximum. PKiN at 18:00 casts 237/tan 24.1° ≈ 530 m.
- **Trunk zone:** below chm_base the ray passes, so low sun lights the pavement under crowns.
- **τ prior:** where gap_frac has \<100 first returns, use τ by genus from the inventory, else 0.03 (Lindberg & Grimmond 2011; Konarska 2014) `[L]`.
- **τ sensitivity:** the LiDAR gap fraction may run above radiometric τ (it includes gaps at crown edges). Run once with τ ≡ 0.03 and report the change in dose. Pick the default at checkpoint 2.
- **Storage:** s as uint8 (0–255) per slot, 30 slots × 37 Mpx ≈ 1.1 GB raw, compressed COG.

### 5.3 Sky view factors (once)

Compute 32 azimuths with horizon angles up to 200 m:

- `svf_b`: buildings only.
- `svf_bv`: buildings + canopy as obstacles, transmitting τ_v.
- Effective sky view: ψ_s = svf_b − (svf_b − svf_bv)(1 − τ_v).
- Wall view ≈ 1 − svf_b; canopy view ≈ svf_b − ψ_s.

## 6. Temperature model (question 10)

Three temperatures matter, and they vary very differently.

- **Air temperature Ta** varies by ~1–2 K within the district. At Ta = 37 °C that moves UTCI by ~1–2 K.
- **Ground and wall temperatures Ts and Tw** differ by 10–25 K between sun and shade.
- **Tmrt** differs by 25–35 K, which gives a 6–9 K UTCI difference. UTCI rises ~0.25 K per K of Tmrt at these conditions (official polynomial) `[L]`.

So keep Ta simple, and spend the modelling effort on surface temperature and radiation.

### 6.1 Options considered

| Option | What it does | Verdict |
|----|----|----|
| **A. SOLWEIG ground-temperature wave + memory** | Ts − Ta = class amplitude × sine of time since sunrise × sunlit fraction, with a 30-min half-life memory. Per-class parameters are from UMEP's land-cover table and calibrated to Landsat. | **Chosen.** Simple and published (Lindberg 2008/2016). Pixel-local, so it vectorises trivially. |
| B. Force-restore surface energy balance | dTs/dt from absorbed shortwave/longwave, convection and ground storage (Deardorff 1978), with class thermal inertia. | Upgrade path, if A misses Landsat by \> 3 K on open paving or under-predicts the evening pavement. |
| C. Landsat LST used directly | Rescale the 06-25 11:25 LST map to every slot. | Rejected. One instant, 100 m native, sees roofs and canopy tops (not pavement under trees), wrong day. |
| D. Ta from LST anomaly | Ta(x) = Ta_ref + β·ΔLST. | Rejected for v1: it double-counts surface heat. At most a damped β ≈ 0.1–0.2 as a sensitivity run. |

### 6.2 Air temperature, humidity, wind (uniform over the district)

    Ta(t)  = Ta_Okęcie(t) + δ(t)          # IMGW hourly, linear to :30 slots
    δ(t)   = +1.6 K (08–20 h, from Filtry−Okęcie Tx 39.7−38.1), ramping to
             +2.1 K at night (night Tn 26.8 vs 24.7); linear between 06–08 and 20–22
    e(t)   = vapour pressure from Okęcie RH and Ta_Okęcie → held uniform; RH(x,t) recomputed at Ta(t)
    v10(t) = Okęcie 10 m wind;  v_utci(p,t) = max(0.5, v10 · (0.4 + 0.4·svf_b(p)))   # canyon damping

δ is anchored on one day's daily extremes `[A]`. The canyon factor is a heuristic `[A]`, but between 0.5 and 2 m/s UTCI changes by ≤0.5 K on this day `[L]`, so wind is second-order. Most street pixels will sit on the 0.5 m/s floor.

### 6.3 Ground and wall surface temperature (option A)

    β_max   = 61.0°   (solar noon elevation, 06-28)
    Tg_amp  = k_c · (TgK_c · β_max + Tstart_c)                      # k_c = calibration multiplier (init 1)
    φ(t)    = (t − t_sunrise) / (TmaxLST_c − t_sunrise) · π/2       # TmaxLST in solar time (15:00 solar ≈ 15:37 CEST)
    CI(t)   = clear-sky index G/G0 corrected as in SOLWEIG, capped at 1
    ΔTg(t)  = max(0, Tg_amp · sin φ(t) · CI(t))
    s_mem(p,t_k) = 0.5·s(p,t_k) + 0.5·s_mem(p,t_{k−1})             # exp(−33.27·Δt[d]) = 0.5 at 30 min
    Ts(p,t) = Ta(t) + ΔTg(t)·s_mem(p,t) + ΔT_night_c·[t ≥ 19:00]·(1 − s_mem)   # evening release floor
    Tw(t)   = Ta(t) + 0.5 · ΔTg_wall(t)                              # ≈ half of the walls in view are sunlit

| Class | albedo | ε | TgK | Tstart | TmaxLST | Tg_amp at 61° | ΔT_night `[A]` |
|----|----|----|----|----|----|----|----|
| granite / concrete paving | 0.20 | 0.95 | 0.37 | −3.41 | 15 | 19.2 K | +3 K |
| asphalt | 0.18 | 0.95 | 0.58 | −9.78 | 15 | 25.6 K | +4 K |
| grass | 0.16 | 0.94 | 0.21 | −3.38 | 14 | 9.4 K | 0 |
| bare soil / gravel | 0.25 | 0.94 | 0.33 | −3.01 | 14 | 17.1 K | +1 K |
| water | 0.05 | 0.98 | 0 | 0 | 12 | 0 | 0 |
| walls (all) | 0.20 | 0.90 | 0.37 | −3.41 | 15 | 19.2 K | +2 K |

The first five columns are from UMEP `parametersforsolweig.json` / `landcoverclasses_2016a.txt` `[L]`. The ΔT_night floor is our addition, because SOLWEIG drops shaded and evening ground to Ta, while real Śródmieście paving releases stored heat. If time allows, calibrate it on the ECOSTRESS night pass of 06-29 01:44 UTC.

### 6.4 Calibration with Landsat (06-25, 11:25 CEST)

1.  Read `lwir11` (ST_B10: K = DN·0.00341802 + 149.0) and `qa_pixel` for the district window. Mask clouds and shadow. The district is inside the path-187 footprint (east of 20.91 °E).
2.  Run §5–6.3 for one extra slot: sun at 06-25 11:25, IMGW Ta of 06-25. Build the *top-of-scene* field: roofs (class wave), canopy tops (Ta + 2 K), visible ground (model Ts).
3.  Aggregate in radiance space (εσT⁴) with a Gaussian PSF of σ ≈ 40 m (TIRS is 100 m native, delivered at 30 m), then sample at the 30 m pixels.
4.  Fit k_c for paving, asphalt, roof and grass by least squares on (LST − Ta). Use only pixels whose footprint is ≥ 60% one class; large open plazas (Plac Defilad, Pl. Piłsudskiego) and the rail yard constrain paving. Target RMSE \< 3 K and \|bias\| \< 1 K.

## 7. Mean radiant temperature and UTCI

Per walkable/stop pixel p and slot k, use the SOLWEIG six-direction standing-person formulation, isotropic simplification. Code verified against UMEP `Solweig_2025a_calc` `[L]`.

    I  = DNI(t),  D = DHI(t),  G = I·sin β + D,  σ = 5.670e-8
    ε_sky = 1 − (1 + w)·exp(−√(1.2 + 3w)),  w = 46.5·e[hPa]/Ta[K]          # Prata 1996 (or ERA5 strd)
    L_sky = ε_sky·σ·Ta⁴

    K↓  = s·I·sin β + ψ_s·D + α_w(1 − svf_b)·[G·(1 − F_sh) + D·F_sh]       # F_sh ≈ 0.5
    K↑  = α_g·[ s̄·I·sin β + ψ_s·D ]                                         # s̄ = s smoothed 5×5 (ground in view)
    L↓  = ψ_s·L_sky + (1 − svf_b)·ε_w·σ·Tw⁴ + (svf_b − ψ_s)·σ·(Ta + 1)⁴      # canopy ≈ Ta + 1 K
    L↑  = ε_g·σ·Ts⁴
    K_side = ½[ψ_s·D + α_w(1 − svf_b)·G] + ½·K↑
    L_side = ½·L↓ + ½·L↑

    S_str = a_K·[ s·I·cos β·F_cyl + (K↓ + K↑)·F_up + 4·K_side·F_side ]
          + a_L·[ (L↓ + L↑)·F_up + 4·L_side·F_side ]
    Tmrt  = (S_str / (a_L·σ))^0.25 − 273.15
           a_K = 0.70, a_L = 0.97, F_up = 0.06, F_side = 0.22, F_cyl = 0.28 (standing)

- **Night (β ≤ 0):** all K = 0.
- **Covered, arcade and tunnel walkways:** s = 0, ψ_s = 0, Tw = Ta.
- **Indoor/underground passages:** Tmrt = Ta_in = 28 °C `[A]`.
- **Stop shelters** (OSM `shelter=yes`): inside a 3 × 1.5 m footprint at the platform node, s = 0 and ψ_s × 0.3.

### UTCI

    from pythermalcomfort.models import utci
    U = utci(tdb=Ta, tr=Tmrt, v=v_utci, rh=RH, units="SI", limit_inputs=True, round_output=False).utci
    assert finite(U) on walk_mask      # NaN if Ta∉[−50,50], Tmrt−Ta∉[−30,70], v∉[0.5,17]

- **Inputs:** Ta and Tmrt in °C, wind at 10 m in m/s (UTCI convention), RH in %. The Bröde 2012 polynomial (pythermalcomfort 4.6.2) holds within Ta −50…50 °C, Tmrt−Ta −30…+70 K and v 0.5…17 m/s `[L]`.
- **Expected ranges on 06-28:** sunlit asphalt at 13:00 ≈ 44–46 °C; deep tree shade ≈ 37–39 °C; at 22:00 ≈ Ta ± 2 ≈ 34–36 °C.
- **Reference person:** UTCI assumes a person walking 4 km/h (1.1 m/s) with adaptive clothing. The pensioner at 1.0 m/s is close. Standing at a stop slightly overstates UTCI (less metabolic heat). We accept this and keep one UTCI field for walking and waiting.
- **In an AC vehicle:** Ta_in = Tmrt = 26 °C, v = 0.5, RH = 45%, so UTCI ≈ 26 and g = 0. Based on the EN 14750 interior-temperature rule of thumb; the standard text was not retrieved `[A]`.

## 8. The route metric: UTCI in time

A trip is a schedule x(t) for t ∈ \[t_dep, t_arr\]. Each instant is in one state: *walking* on a pixel, *waiting* at a stop pixel, or *riding* an AC vehicle. Waiting at home before t_dep is indoors and free. The felt temperature along the trip is U(t) = UTCI(x(t), t), interpolated linearly between the 30-min slots.

### 8.1 Dose function

    g(U) = max(0, U − 26) + max(0, U − 32) + 2·max(0, U − 38)     # K, per minute of exposure
    H    = Σ_legs ∫ g(U(t)) dt   [K·min]                           # heat dose, the quantity to minimise

| UTCI band | UTCI category | slope of g | Why |
|----|----|----|----|
| \< 26 | no heat stress | 0 |  |
| 26–32 | moderate | 1 | Counting starts one category early for the elderly: there is no official age-adjusted UTCI, and heat mortality in Europe already rises above 26 (Di Napoli 2018) `[L]` |
| 32–38 | strong | 2 | Mortality RR +10–20% above 32 in 8 Polish cities incl. Warsaw (Kuchcik 2020) `[L]` |
| ≥ 38 | very strong / extreme | 4 | RR +25–30% above 38 (Kuchcik 2020) `[L]` |

g is continuous and convex, so one minute at 44 °C costs more than two minutes at 35 °C. Example for 13:00 `[D]`: sunlit sidewalk U ≈ 44.5 → g = 44 K per minute; tree-shaded U ≈ 38.5 → g = 20. A 10-minute walk costs 440 vs 200 K·min.

### 8.2 Objective and outputs

    J(route, t_dep) = H + μ · T_total                     # T_total = t_arr − t_dep [min]
    minimise J over routes and over t_dep ∈ [t_earliest, t_latest_arrival − T]
    subject to:  boarded runs had AC (recorded vehicle);  t_arr ≤ deadline (if given);  single walk leg ≤ 1.5 km

- **Route cards:** three cards from one search: *coolest* (μ = 0.5), *balanced* (μ = 2) and *fastest* (μ → ∞, still AC-only). Each card has its own best departure time.
- **Per card, report:** H; T_total; peak U; minutes in each UTCI category; minutes ≥ 38; shade share of walking.
- **Departure-time curve:** H(t_dep) over the day, for the “when should I go?” chart.
- **Rejected metrics:**
  - Mean UTCI: rewards long cool detours and hides peaks.
  - Max UTCI alone: ignores duration. It is kept as a reported value.
  - Full physiology (`two_nodes_gagge_ji`, an elderly-tuned bioheat model): reserved for validating the 3 cards, not for routing.
- **Edge evaluation:**
  - Per walk edge (≤ 20 m) and slot, store Ū = mean U over the edge's walk-mask pixels within 1 m of its centreline (a 2 m strip).
  - Store g(Ū) as well. Using g of the mean rather than the mean of g is a deliberate simplification, because a pedestrian walks a line, not an area.
  - Traversal cost = g(Ū(t_enter))·len/v.
  - A signalised crossing adds a 30 s wait at the crossing pixel's U.

## 9. Public transport: AC only, timed walks

### 9.1 Source: real operations from BigQuery, not a timetable

The router does not use a timetable. It uses what trams and buses actually did on **Saturday 2026-10-17, 06:00–22:00**, as recorded in `hackaton-honory.warsaw_transport.vehicle_positions`. Every minute of that window is present, with ~1,100 fresh vehicles per hour `[V]`. These events are replayed on the 06-28 heat clock.

- **Modes:** trams and buses only. Metro and rail are dropped.
- **Stops:** boarding and alighting only at stops inside the district: 250 bus, 76 tram, 4 bus+tram `[V]`. Runs may leave the district between two inside stops.
- **Stop locations:** the OSM platform node via `ref` (81% match), else a 30 m spatial fallback. Shelter flags: 297 `yes`, 79 `no`, 29 unknown `[V]`.

### 9.2 From positions to stop events (BigQuery SQL, one-off)

1.  **Clean:** keep `report_age_s < 120`, de-duplicate the old/new API copies on `(vehicle_type, vehicle_number, reported_at)`, and drop vehicles frozen \> 10 min or outside Warsaw.
2.  **Runs:** split each vehicle's track into runs on `(vehicle_type, vehicle_number, line, brigade)`, with a new run when the line/brigade changes or there is a gap \> 15 min.
3.  **Candidate stops:** match runs to `gtfs_trips` of service `2026-10-17:SbS` on `(route_id = line, LTRIM(block_short_name,'0') = LTRIM(brigade,'0'))`, which gives the stop sequence. Fallback: all stops of that line within 30 m.
4.  **Stop events:** for each stop in the sequence, take the pings within 30 m of the stop.
    - arr = first ping, dep = last ping.
    - With no ping inside 30 m (a fast pass), interpolate the time of closest approach between the two bracketing pings.
    - Keep only monotonically increasing events along the sequence.
5.  **Connections:** consecutive stop events of the same run (dep at stop i → arr at stop i+1) form the connections for the router.

The earlier pass matched 21.8k tram and 31.3k bus stop passes on this exact day with this method `[V]`. Gaps: a run with \< 50% of its sequence matched is dropped, not repaired.

### 9.3 AC: the actual vehicle, not a prediction

    ac(run) = ztm_vehicle_models.air_conditioning
              via ztm_vehicles ON (mode, vehicle_number) → model_id   # always join on mode: fleet numbers collide
    board only if ac(run) = TRUE        # NULL / unregistered vehicle → treated as no AC

On this Saturday, 96.4% of tram and 99.1% of bus vehicle-minutes in Śródmieście were AC. Tram line 7 (36% AC) and line 28 (70%) are the mixed ones `[V]`. Non-AC trams are mainly the 105N/105Na and 116N types.

### 9.4 Arriving just in time

The recorded departure plays the role of a 2076 real-time prediction. The traveller aims to reach the stop a fixed margin b before it:

    t_at_stop = dep_recorded − b,          b = 60 s (safety margin for prediction error)   [A]
    wait      = b  (+ transfer: dep_next − arr_prev, as recorded)
    dose_wait = ∫ g(U_stop(t)) dt over the wait
    U_stop(t) = min( U over walk_mask pixels within 15 m of the platform, U_shelter )   # stand in the best spot

- **Sensitivity:** b ∈ {30, 60, 120} s.
- **Transfers:** the wait between alighting and the next recorded departure is charged in full at the transfer stop's UTCI, so the router prefers transfers with short waits in shade.
- **Walk legs:** costed by the walk graph at their real entry time.

## 10. Router

1.  **Walk graph:** OSM ways clipped to the district, split at intersections, resegmented to ≤ 20 m. Edge attributes: `len`, `Ū[33]`, `g[33]`, `covered`, `crossing_signals`. Mapped sidewalks are used as they are. Where a street has no mapped sidewalk, sample both 2 m strips and keep the cooler one per slot (flagged “side assumed”).
2.  **Access/egress:** a time-dependent Dijkstra (FIFO, cost evaluated at entry time) from the origin to every stop within 1.5 km, for each candidate departure time. The same runs backwards from the destination.
3.  **Transit:** McRAPTOR with two criteria (arrival time, H) and ≤ 2 transfers, over the recorded AC runs only (stop events from §9.2 used as the trips). Śródmieście has ~330 stops, so it is small enough for Python + numba.
4.  **Walk-only:** the walk-only route is always a candidate.
5.  **Departure sweep:** run t_dep every 5 min across the user's window, up to 193 runs for 06–22 h. Pick the J-minimal (route, t_dep) per μ.
6.  **Fallback** if McRAPTOR slips: generate K = 10 itineraries by plain earliest-arrival CSA with walking penalties, then re-rank by J.

ORS Shaded and the MIT building-only shade are not engines here, only “related work” baselines. Running our walk-only route on the same O–D pairs makes that comparison fair.

## 11. Outputs and data contract

| Artifact | Shape | Format |
|----|----|----|
| Static rasters | `dtm, bldg_dsm, chm_top, chm_base, tau_v, svf_b, svf_bv, surface_class, walk_mask` at 1 m | COG, EPSG:2180 |
| Slot rasters | `s` (uint8), `Ts`, `Tmrt`, `UTCI` (int16 ×10), 33 bands, masked to walk_mask | COG per variable |
| `walk_edges` | edge_id, u, v, geom, len, covered, crossing_signals, U\[33\], g\[33\], shade_frac\[33\] | GeoParquet |
| `stop_heat` | stop_id, shelter, U_stop\[33\], U_pole\[33\] | Parquet |
| `stop_events` | run_id, mode, line, brigade, vehicle_number, ac, seq, stop_id, arr, dep, method (ping / interpolated) | BigQuery table (needs OK) or Parquet |
| `route_results` | od_id, μ, t_dep, legs\[mode, line, from, to, dep, arr, H_leg, peak_U\], H, T, minutes per category | Parquet |
| Map | UTCI tiles per slot with a time slider; route cards; H(t_dep) curve | PMTiles / PNG tiles + small web page |

Storage is GCS Parquet/COG. A BigQuery dataset is optional and needs your OK. OSM-derived tables are ODbL. Attribute GUGiK, IMGW, USGS/Landsat, Copernicus and Mobility Database/ZTM.

## 12. Compute and Python stack

| Stage | Volume | vCPU-h | Basis |
|----|----|----|----|
| LiDAR download → GCS | ~24 GB, ~260 sheets | 0.2 | I/O bound; 8 streams |
| LAZ decode + gridding | ~0.65 G pts (2023) + ~0.63 G pts (2025) | 0.2–0.4 | lazrs ~10–20 M pts/s/core `[A]` |
| Shade, 30 sunlit slots, 2 passes | 37 Mpx × Σ steps (4.4k at 1 m, 100 m caster, 300 m cap) | 0.2–0.4 | 2.2 ns/px-step `[D]` |
| Tall-caster pass (\> 80 m, reach ≤ 2 km) | ~10 towers | 0.2–0.5 | earlier estimate `[D]` |
| SVF ×2 (32 az, 200 m) | 37 Mpx | 0.3 | `[D]` |
| Ts / Tmrt / UTCI | ~4 Mpx × 34 slots (incl. calibration slot) | 0.1–0.3 | ~2 µs/px `[A]` |
| Edge aggregation, router sweep | ~40–60k edges; ≤ 193 departures × 3 μ | \< 0.2 |  |
| Total |  | 1.2–2.5 | ≈ 10–25 min on n2-custom-12-49152, \< €2 + ~€0.5/month storage |

**Stack** (all pip wheels): `laspy[lazrs]`, `numpy`, `numba`, `scipy`, `rasterio`, `shapely`, `geopandas`, `pyproj`, `pvlib`, `pythermalcomfort`, `pystac-client` + `planetary-computer`, `osmium`, `pandas`/`pyarrow`, `igraph` (optional, for Dijkstra), `requests`.

- **Avoided:** PDAL, QGIS/UMEP, OTP/Java. SOLWEIG equations are re-implemented and cited, not copied.
- **Where it runs:** all heavy stages run on GCP (hackaton-honory, europe-central2), in line with “no local analysis”. Creating the VM and bucket needs your OK.

Module layout: `ingest_lidar.py · rasters.py · sun.py · shade.py · svf.py · surface.py · tmrt_utci.py · calibrate_landsat.py · walkgraph.py · transit.py · router.py · app/`.

## 13. Validation and acceptance

| Check | Pass criterion |
|----|----|
| DTM vs GUGiK NMT 1 m | median \|Δ\| \< 0.15 m on open ground |
| CHM vs inventory tree heights (Śródmieście, 8,142 trees) | median \|Δ\| \< 2 m (excluding trees felled since 2013) |
| PKiN shadow at 18:00 | tip ≈ 530 m east of the base, ±5% |
| Visual QA, 5 spots (Marszałkowska arcades, Nowy Świat trees, Pl. Defilad, Centralna, Varso) | no crane / scaffold shadows; arcades fully shaded; tree shade has trunk gaps at 07:00 |
| Ts vs Landsat 06-25 (radiance-aggregated) | RMSE \< 3 K, \|bias\| \< 1 K on class-pure pixels after calibration |
| UTCI sanity | 0 NaN on walk_mask; sun–shade ΔUTCI at 13:00 of 5–10 K; 22:00 field within Ta ± 3 K |
| Router | For 5 demo O–D pairs: dose reduction % vs time increase % against fastest AC; walk-only baseline; ORS Shaded screenshot |
| Sensitivity | τ ≡ 0.03 vs LiDAR τ; wind factor ±50%; δ ±1 K; report how the ranking of the 3 route cards changes |

## 14. Open decisions and risks

### Decisions for you

1.  **Rail:** is SKM/KM rail (Śródmieście, Centralna, Powiśle) out together with metro? The spec assumes yes: trams and buses only.
2.  **Transit day:** Saturday 10-17 is the only full day recorded before the deadline. Sunday 10-18 is recorded only after submission. Keep Saturday?
3.  **Dose function:** the threshold of 26 and slopes 1/2/4, and the μ values for the three cards.
4.  **Waiting before departure:** waiting at home costs nothing (assumed cool indoors). In 2076 the pensioner's flat may itself be hot. Keep it free?
5.  **GCP:** OK to create one VM (n2-custom-12-49152, on-demand) and one bucket in hackaton-honory, europe-central2?

### Risks

| Risk | Mitigation |
|----|----|
| Canopy is from 2023 and buildings from 2025, a 9–20-month gap. New street trees planted 2024–26 are missing; felled trees are removed by the 2025 check. | Say “canopy as of 2023-09”. Spot-check 3 recent planting sites. |
| LiDAR gap fraction ≠ shortwave τ | Sensitivity run with τ = 0.03; choose at checkpoint 2. |
| Landsat is 3 days before the replay day | Calibrate the anomaly Ts − Ta, not absolute Ts; use 06-24 as a second view. |
| Evening pavement heat release is not physical in option A | ΔT_night floor; force-restore (option B) as the upgrade. |
| Transit is an October Saturday replayed on a June Sunday | State it; clock times are kept, and the service level is a Saturday's. |
| Stop-event extraction misses fast passes or mis-snaps at shared stops | Closest-approach interpolation; check sequence monotonicity; drop runs with \< 50% matched; compare event counts with the GTFS stop_times for 10-17. |
| GUGiK server: no HTTP Range, throughput unknown from GCP | Download from the VM, whole files, 8 streams; start first. |
| DSM artefacts (cranes, temporary structures in April 2025) | Visual QA; mask class-6 cells with no BDOT footprint and area \< 20 m² as non-buildings. |

Method: synthesis of the earlier heat_routing, heat_compute and prior_art memos plus three new research passes on 2026-10-17: the LiDAR/canopy index, UTCI/SOLWEIG physics from source code, and GTFS/AC/positions from BigQuery. A Mobility Database feed valid on 06-28 exists (mdb-2092-202606280122) but is not used, because transit comes from recorded operations. Literature flagged `[L]` was checked via DOI metadata or abstracts, not full texts. The EN 14750 in-vehicle temperature and the Filtry offset method are unverified assumptions. No GCP resources were created. While reading LAS headers, one research pass downloaded about 400 MB of LAZ to the laptop (the server ignores Range requests); it was deleted immediately.
