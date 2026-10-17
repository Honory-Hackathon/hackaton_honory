# T02: Shade engine

**Spec:** §5.3 · **Depends on:** nothing to start (develop on test tiles) · **Consumed by:** T07

## Goal

Build a reusable engine that, given 1 m elevation rasters and a date-time, says for every pixel whether direct sun reaches it, distinguishing shade by **buildings** from shade by **trees**. Run it for the 33 time steps of 2026-06-28. The output is the four shade zone shares used by the thermal model (spec §13):
- `f_sun`: in direct sun
- `f_bldg`: shaded by buildings only
- `f_tree`: shaded by trees only
- `f_both`: shaded by both

Also compute a sky view factor once, because the thermal model may need it.

## Why it matters

Shade is what changes the felt temperature most between two sides of a street: mean radiant temperature differs by roughly 25–35 K between sun and shade. The engine must be correct at low sun (06:00–08:00 and 19:00–20:30), when shadows are longest and the router's choices matter most.

## Inputs

- **For development, anything at 1 m (or 0.5 m) over a small area:**
  - one or two GUGiK NMPT/NMT sheets (index `https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelPokryciaTerenuEVRF2007/WFS/Skorowidze`, direct `.asc` download URLs, no login)
  - or a single LAZ sheet (see T01)
  - Synthetic test scenes (a box, a tower, a cylinder tree) are strongly recommended for checking geometry.
- **For the production run:** T01's rasters (ground, buildings, canopy top, crown base, crown light transmission) on the shared grid.
- **Time steps:** the 33 time steps of the shared conventions, with sun positions computed for Śródmieście (≈52.23 N, 21.01 E).
  - The sun is above the horizon from 06:00 to 20:30 CEST (elevation 12.5° at 06:00, about 61° at 12:30–13:00, 2.9° at 20:30).
  - From 21:00 on the sun is below the horizon.

## Expected output

- **Shade engine:** a module with a clear interface `(elevation rasters, sun position) → per-pixel shade by buildings / by trees`. Tested on the synthetic scenes and documented.
- **Production run:** `f_sun`, `f_bldg`, `f_tree`, `f_both` for all 33 time steps on the shared grid (COG, one band per time step or one file per time step), plus sky view factor.
  - With crown transmission, shares can be fractional; say how transmission enters the shares.
- **Report:** runtime and memory per time step; previews of 07:00, 13:00 and 19:00.

## Constraints and things to handle

- Casters can be up to ~1 km outside the AOI (towers around Rondo Daszyńskiego at low evening sun); the halo exists for this. PKiN's shadow is about 530 m long at 18:00.
- Light passing under crowns at low sun (crown base) should be possible to represent.
- Must scale to ~37 Mpx × 30 sunlit time steps in reasonable time on a cloud VM. Pick the algorithm and parallelism accordingly.
- Pure Python preferred (numpy/numba-level performance is fine).

## Done when

- Synthetic scenes give analytically correct shadow lengths and directions.
- Real-data shadows match visible shadows at a few known spots: compare a city orthophoto with known acquisition time, or check on site.
- The production run completes on the full grid once T01 delivers.

## Out of scope

Producing the LiDAR geometry (T01), thermal effects of shade (T06/T07).
