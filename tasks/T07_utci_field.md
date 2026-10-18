# T07: UTCI field

**Spec:** §6, §7, §13 · **Depends on:** T01, T02, T03, T05, T06 (all can be used now except T02's district shares, see below) · **Consumed by:** T08, T10, T11

## Goal

Compute local air temperature, mean radiant temperature (Tmrt) and **UTCI for every walkable pixel at each of the 33 time steps** of 2026-06-28. Use the simple closed-form model of spec §13, with coefficients tuned against SOLWEIG in T06.

## Why it matters

This is the heat field that every later product reads: segment and stop doses (T08), the router (T09) and the map (T10). It decides whether the simple model is good enough, or whether district SOLWEIG has to replace it.

## Data

| Input | Where | Notes |
|---|---|---|
| Shade-zone shares | `gs://hackaton-honory-heat/T02/`: `f_sun.tif`, `f_bldg.tif`, `f_tree.tif`, `f_both.tif`, `svf.tif` | uint8, 33 bands (band description = local time, UTC, sun azimuth/elevation). Share = value / 250; the four sum to 250. **255 = sun below the horizon** (21:00–22:00). `svf.tif` float32, band 1 buildings + terrain, band 2 also with crowns. Format: [`shade/README.md`](../shade/README.md). The production run is finishing on 2026-10-18; until it lands, build on the T06 pilot |
| Forcing | BigQuery `hackaton-honory.warsaw_heat.t05_forcing_20260628`, or `gs://hackaton-honory-heat/T05/forcing/` | `ta_c`, `rh_pct`, `wind_10m_utci_ms`, `ghi/dni/dhi_wm2`, `sun_elev_deg`. `ta_filtry_adj_c` = warmer built-up sensitivity variant. [T05 report](../reports/T05/README.md) |
| `pmwc` | `gs://hackaton-honory-heat/T05/pmwc/pmwc_10m.tif` | 10 m, each cell is exactly 10 × 10 grid cells. Surface anomaly, not air |
| Walkable mask | `gs://hackaton-honory-heat/T03/walkable.tif` | 0 = not walkable; categories 1–8. Indoor segments are not in the mask ([T03](../reports/T03/README.md)) |
| Tuned coefficients and pilot results | `gs://hackaton-honory-heat/T06/results/metrics.json` (`fits`), `T06/cog/` | Pilot = E 637300–638500, N 485100–485900. Code: [`solweig_ref/`](../solweig_ref/README.md) |
| District SOLWEIG | `gs://hackaton-honory-heat/T06/district/tiles/` (27 × 1 km tiles, running 2026-10-18; plan in `T06/district/plan.json`) | Reference to check the district field against |

## What T06 found (UTCI RMSE against SOLWEIG, eastern-half hold-out)

| Variant | Day (east hold-out) | Night (all 3 steps) |
|---|---|---|
| §13 as written | 0.98 K | 4.2 K (sun clipped to 2° at night) |
| §13, sun-driven terms off below the horizon | 0.98 K | 0.6 K |
| Refitted per zone with ERA5 radiation (`tuned_era5`) | 0.49 K | 1.4 K |
| `tuned_era5` + SVF term (`tuned_era5_svf`) | 0.46 K | 1.4 K |

- `pmwc` explains little of the remaining gap: fitted coefficient ≈ 0.10, correlation with the midday residual 0.13.
- **The fits used SOLWEIG's own 0/1 shadows,** not T02's fractional shares. In T02, light through crowns counts as `f_sun`, which is the double-count question in spec §13 ("points to settle"). Check what the tuned coefficients give on T02 shares over the pilot before applying them to the district.

## Expected output

- **Rasters:** UTCI and Tmrt as COGs on the shared grid, one band per time step (33), masked to walkable pixels (nodata elsewhere). Also local air temperature if it varies in space. Each with a README (model variant, coefficients, units, nodata).
- **Hand-over format for T08:** UTCI as int16 in 0.01 °C (or float32), nodata outside the mask. Band order and descriptions as in T02.
- **Report:**
  - The chosen model variant and why.
  - The comparison with district SOLWEIG where tiles exist.
  - Sanity checks from spec §12: no invalid UTCI inputs (Tmrt − Ta outside −30…+70 K, wind outside 0.5–17 m/s); plausible sun–shade differences; evening values close to air temperature.
  - A few maps (13:00, 18:00, 20:00).

## Constraints and things to handle

- **Night steps:** T02 marks them with 255. Drop direct shortwave and any sun-driven surface term there.
- **Receiver height:** T02 shares are at the surface (0 m); UTCI is at body height. State whether this matters (T02: small except next to walls).
- **Wind:** Okęcie 10 m, with no street-canyon reduction. Keep the convention and note it.
- **Indoor and underground** segments have no pixels. Leave them to T08, and say so.
- **Compute:** 2.2 M walkable pixels × 33 steps fits one Cloud Run task. Heavy work runs in the cloud, not on laptops.

## Done when

- UTCI and Tmrt for every walkable pixel and all 33 steps are in `gs://hackaton-honory-heat/T07/`.
- The pilot result on T02 shares is compared with the T06 numbers, and the district result with SOLWEIG tiles available at that time.
- The report is in `reports/T07/`.

## Out of scope

Aggregation to segments and stops (T08), thermal memory and evening heat release from pavement, wind modelling.
