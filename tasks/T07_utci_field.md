# T07: UTCI field from district SOLWEIG

**Spec:** §6, §7, §13 · **Depends on:** T06 district run (finishing 2026-10-18), T03 · **Consumed by:** T08, T10, T11

## Goal

Deliver the production heat field: **Tmrt and UTCI for every walkable pixel at each of the 33 time steps** of 2026-06-28, taken from the district-wide SOLWEIG run. SOLWEIG is the thermal model (spec §2, §6).

## Why it matters

This is the heat field every later product reads: segment and stop doses (T08), the router (T09) and the map (T10). Its known gaps have to be stated before anyone prices routes on it.

## Data

| Input | Where | Notes |
|---|---|---|
| SOLWEIG tiles | `gs://hackaton-honory-heat/T06/district/tiles/{e}_{n}/{tmrt,utci,shadow}.tif`, `timing.json` | 27 × 1 km core tiles covering Śródmieście. float32, 33 bands (band description = local time), all pixels of the core, roofs included. UTCI already computed with T05 `ta_c`, RH and `wind_10m_utci_ms`. Each tile ran with a 500 m halo, or 1000 m where a tower > 100 m stands within 1 km (`plan.json`) |
| SOLWEIG mosaic | `T06/district/{tmrt,shadow,utci}_20260628.tif` | Produced by the T06 job with `MODE=mosaic` once all tiles exist. Code: [`solweig_ref/run.py`](../solweig_ref/run.py) |
| Walkable mask | `gs://hackaton-honory-heat/T03/walkable.tif` | 0 = not walkable; categories 1–8. Extends up to 50 m outside the AOI |
| Forcing | BigQuery `hackaton-honory.warsaw_heat.t05_forcing_20260628` | `ta_filtry_adj_c` = warmer built-up variant, for a sensitivity check if wanted ([T05](../reports/T05/README.md)) |
| Shade shares | `gs://hackaton-honory-heat/T02/` | Building shade `B = f_bldg + f_both` (uint8 / 250, 255 = night), computed with casters over the full 13 × 11 km [caster extent](README.md) |
| Pilot | `gs://hackaton-honory-heat/T06/cog/`, `T06/results/` | Same SOLWEIG setup on E 637300–638500, N 485100–485900 with a 500 m halo |

## Expected output

- **Rasters in `gs://hackaton-honory-heat/T07/`:** UTCI and Tmrt as COGs on the shared 1 m grid, 33 bands, masked to walkable pixels (nodata elsewhere). Each has a README: source, units, nodata, band order.
- **Hand-over format for T08:** UTCI float32 (or int16 in 0.01 °C), band order and descriptions as in T02.
- **Report in `reports/T07/`:** the checks below, plus maps at 13:00, 18:00 and 20:00.

## Checks and things to handle

- **Coverage:** every walkable pixel must fall inside a SOLWEIG tile. Report any that don't, e.g. the mask's 50 m fringe outside the AOI.
- **Tile seams:** neighbouring tiles ran with different halos. Look for jumps in Tmrt and shadow along tile edges.
- **Far casters at low sun:** a halo of 0.5–1 km misses long evening shadows. At 20:30 a 310 m tower casts ≈5.7 km. Compare SOLWEIG `shadow` with T02's building shade per step, and report where and when T02 has shade that SOLWEIG lacks.
- **Pilot consistency:** the pilot and the district tiles overlap. They should agree, apart from halo effects.
- **Sanity (spec §13):**
  - UTCI inputs inside the valid range (Tmrt − Ta between −30 and +70 K, wind 0.5–17 m/s).
  - Plausible sun–shade differences.
  - Evening values close to air temperature.
- **Indoor and underground** segments have no pixels. Leave them to T08, and say so.

## Done when

- UTCI and Tmrt for every walkable pixel and all 33 steps are in `gs://hackaton-honory-heat/T07/`.
- The report covers the checks above and states the known gaps.

## Out of scope

Rerunning SOLWEIG (T06), aggregation to segments and stops (T08).
