# T05: Weather forcing for 2026-06-28 and the Landsat surface-heat anomaly

**Spec:** §3, §6.4, §13 · **Depends on:** nothing · **Consumed by:** T06, T07

## Goal

Prepare the two non-geometric inputs of the thermal model:

1. **Forcing table** for the 33 time steps of 2026-06-28 (06:00–22:00 CEST, 30 min). Variables:
   - air temperature
   - relative humidity
   - wind speed at 10 m
   - direct and diffuse solar radiation
   - global radiation
   - solar elevation and azimuth
   - optionally incoming longwave and cloud cover
   Include provenance per value.
2. **`pmwc` map:** the urban surface-heat anomaly per location from Landsat, on the shared 1 m grid (or a coarser grid that resamples cleanly onto it). It is the `pmwc` argument of `compute_inputs_for_utci` (spec §13) and the context layer for SOLWEIG comparisons.

## Why it matters

The forcing drives every UTCI value. `pmwc` is the only spatial temperature information we have from observation, so its definition (anomaly relative to what reference, at which scale) has to be explicit.

## Data (verified 2026-10-17)

- **IMGW public data** (`danepubliczne.imgw.pl`):
  - Hourly synop for Warszawa-Okęcie (station 12375 / 375).
  - Daily climate data for Warszawa-Filtry, which runs warmer: Tmax 39.7 °C vs Okęcie 38.1 °C on 06-28.
  - Files are CP1250. Status code 8 means missing.
  - Check which humidity field is usable; an earlier pass found one of the humidity-related columns unusable.
- **ERA5 via Open-Meteo archive:** `https://archive-api.open-meteo.com/v1/archive?latitude=52.23&longitude=21.01&start_date=2026-06-28&end_date=2026-06-28&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,direct_normal_irradiance,diffuse_radiation,shortwave_radiation,cloud_cover&timezone=Europe%2FWarsaw&wind_speed_unit=ms`
  - The grid point is NW Warsaw (52.27 N, 20.96 E).
  - Radiation values are means over the **hour ending** at the timestamp.
  - Peaks on 06-28: DNI ≈ 870 W/m² and DHI ≈ 100 W/m² around 12–13 h.
  - Ta 24.5 °C at 06 h, 38.2 °C at 16 h, 34.2 °C at 22 h.
  - Cloud 30–60% from 17 h.
- **Landsat Collection 2 Level-2** (Microsoft Planetary Computer STAC `https://planetarycomputer.microsoft.com/api/stac/v1`, collection `landsat-c2-l2`; sign assets with `planetary-computer`):
  - **No scene on 06-28.**
  - Best: `LC09_L2SP_187024_20260625_02_T1` (2026-06-25 09:25 UTC = 11:25 CEST, scene cloud 4.96%). Śródmieście lies inside its footprint.
  - Backup: `LC08_L2SP_188024_20260624_02_T1` (06-24, 19.8% scene cloud).
  - Surface temperature is asset `lwir11` (K = DN × 0.00341802 + 149.0); clouds are in `qa_pixel`.
  - Thermal resolution is 100 m native, delivered at 30 m.
- **Optional for checking the anomaly:**
  - City air-quality sensor network with temperature (new portal, live only).
  - ZOM road-weather stations (live only).
  - ECOSTRESS night pass 2026-06-29 01:44 UTC.

## Expected output

- **Forcing table:** a Parquet/BigQuery table with one row per time step (UTC timestamp + local label) and the variables above, each with units and source, plus a short decision note.
  - The note covers: which station is the reference for air temperature, whether and how an urban offset (e.g. Filtry vs Okęcie) is applied, and how hourly values are aligned to the 30-min steps.
- **`pmwc`:** a raster with a README. It must state:
  - the reference the anomaly is relative to (e.g. rural/Okęcie surrounding, district median)
  - the cloud masking
  - the date and time of the scene
  - the units (K)
  - that it is a surface, not air, anomaly
- **Report:** plots of the forcing over the day; a map of `pmwc` over Śródmieście.

## Constraints and things to handle

- The Landsat scene is three days before the heat day and at one time of day only. Document that `pmwc` is a static spatial pattern, not an hourly temperature.
- Landsat sees roofs and tree tops, not pavement under trees.
- UTCI expects wind at 10 m and is valid for wind ≥ 0.5 m/s. Provide 10 m wind; how it is reduced in street canyons is decided later.

## Done when

- The forcing table has no gaps for all 33 steps and a stated source for every variable.
- `pmwc` exists on a grid aligned with the shared grid, cloud-masked, with a clear definition.

## Out of scope

Computing Tmrt or UTCI (T06/T07).
