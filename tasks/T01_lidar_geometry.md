# T01: LiDAR → 1 m urban geometry

**Spec:** §5.1–5.2 · **Depends on:** nothing · **Consumed by:** T02 (as real input), T07

## Goal

Turn GUGiK LiDAR point clouds into the 1 m geometry the shade and thermal models need, covering the AOI plus halo (see the [shared conventions](README.md)). The geometry is:
- ground elevation
- building surfaces
- tree canopy, including **how high crowns start** and **how much light passes through them**
- a basic surface-type layer

## Why it matters

Shade from trees is the main reason to build our own model rather than use building-height-only shade (the MIT data). The canopy has to come from LiDAR (spec decision 4).

## Data (verified 2026-10-17)

- **Sheet index (WFS 2.0, no login):** `https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze`.
  - One layer per year: `gugik:SkorowidzDanychPomiarowychLIDAR2018` … `2026`.
  - Fields include `godlo`, `akt_data` (flight date), `uklad_h` and `url_do_pobrania` (direct LAZ URL).
  - In `BBOX=…,urn:ogc:def:crs:EPSG::2180` the axis order is **northing first**.
- **Flights over Śródmieście:**

  | Flight | Leaf state | Density | Sheets / size (district) | Use |
  |---|---|---|---|---|
  | **2023-09-05** (job GI-FOTO.6201.22.2023) | full leaf-on | ~33 pts/m² | 74 sheets / 7.0 GB | Canopy |
  | **2025-04-27** (job GK-FOTO.6201.5.2025) | early leaf-out | ~17 pts/m² | 74 sheets / 6.7 GB | Newest buildings and ground |

  - Both are EPSG:2180 / EVRF2007, LAZ (LAS 1.2, point format 3), sheets about 550 × 595 m. The halo adds roughly another 1–2× of sheets.
  - A 2022 flight exists but is in PL-2000 zone 7 (EPSG:2178) and 20 GB. Older 2012/2018 flights are leaf-off.
- **Classes per GUGiK:**

  | Class | Meaning |
  |---|---|
  | 2 | ground |
  | 3 / 4 / 5 | vegetation <0.4 / 0.4–2 / >2 m |
  | 6 | buildings and structures |
  | 7 | noise |
  | 8 | water (not the usual ASPRS 9) |
  | 12 | overlap |

  There is no bridge class. Verify on a sample.
- **Download quirk:** `opendata.geoportal.gov.pl` **ignores HTTP Range**, so every request downloads the whole file. Download in the cloud, not on a laptop.
- **Supporting data (optional):**
  - GUGiK NMT 1 m and NMPT 0.5 m (same index service family, `…/NumerycznyModelPokryciaTerenuEVRF2007/WFS/Skorowidze`), for cross-checks.
  - BDOT10k building footprints.
  - Sentinel-2 L2A from 2026-06-27 for vegetation and albedo hints.
  - City tree inventory (old API, 2010–13 points with species and crown diameter; local copy exists in the team workspace), for validation.
- **Not available:** the city's crown map (Mapa Koron Drzew) is view-only and cannot be downloaded.

## Expected output

COG rasters on the shared 1 m grid, each with a README:
- **Ground and surface models:** ground elevation (DTM) and building surface (DSM, buildings + ground).
- **Canopy:** canopy top height and crown base height above ground, plus an estimate of crown light transmission (a value in 0–1 per canopy pixel, with a note on how it was derived).
- **Masks:** building mask and water mask.
- **Far casters:** a ground + buildings surface model on the 1 m cells over the [caster extent](README.md) (13 × 11 km), for the low-sun shadows of tall buildings beyond the halo. Only objects above ~55 m matter there, so a simpler or coarser source resampled to the 1 m cells is acceptable.
- **Surface types:** a simple surface-type raster (e.g. building / water / grass / paved / other).
- **Report:** sheet list, processing notes, previews.

## Constraints and things to handle

- Canopy comes from the 2023 leaf-on flight and buildings/ground from 2025. The two must be reconciled, e.g. trees felled between the flights or buildings built in between.
- Expect artefacts and decide how each is handled:
  - cranes and scaffolding (April 2025)
  - vehicles
  - bridges and flyovers
  - arcades and covered walkways
  - glass roofs
  - Vistula embankments
- LiDAR gap fraction is not the same thing as shortwave transmissivity; document what the estimate represents. Literature reference values for leaf-on urban trees are roughly 3–10% (Lindberg & Grimmond 2011; Konarska et al. 2014).

## Done when

- All rasters exist for the full grid and are aligned to it.
- Spot checks pass for: tall towers (PKiN ≈ 237 m, Varso ≈ 310 m incl. spire), a tree-lined street (e.g. Aleje Ujazdowskie), a park (Ogród Saski) and a plaza (Plac Defilad).
- Crown heights agree with inventory trees where both exist.

## Out of scope

Shadow casting (T02), walkable masks (T03), thermal modelling.
