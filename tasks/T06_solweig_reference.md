# T06: SOLWEIG reference on a pilot area vs the proposed simple model

**Spec:** §6.2, §6.3, §13 · **Depends on:** nothing (builds its own pilot inputs) · **Consumed by:** T07 (tuned coefficients and model choice)

## Goal

Run SOLWEIG in its **simplest configuration** for a small pilot area of Śródmieście over the 33 time steps of 2026-06-28. Compare its mean radiant temperature (Tmrt) and UTCI with the proposed closed-form model `compute_inputs_for_utci` (spec §13) on the same pixels and times. Recommend:
- whether the simple model is good enough for routing
- adjusted coefficients for it (zone transmissivities, surface offsets, ground view factor), if needed
- what, if anything, would justify running SOLWEIG for the whole district instead

## Why it matters

The simple model is fast but its coefficients are assumptions. SOLWEIG is the established reference. Settling this early means the district-wide UTCI field (T07) is built on a tested model.

## Pilot

- **Size:** roughly 0.5–1 km² of the district.
- **Contents:**
  - a canyon street (e.g. Marszałkowska)
  - tree-lined streets (e.g. Mokotowska / Aleje Ujazdowskie)
  - an open plaza (Plac Zbawiciela or Plac Konstytucji)
  - a park edge
- **Inputs (built independently of T01 for speed; reuse T01 rasters once they exist):**
  - SOLWEIG's minimal rasters: building + ground surface (DSM), ground (DEM), vegetation canopy (CDSM), optional land cover. Sources:
    - GUGiK LAZ sheets: index `https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze`; 2023-09-05 leaf-on flight for canopy, 2025-04-27 for buildings; whole-file downloads only.
    - Or GUGiK NMPT/NMT sheets.
  - Meteorology for 2026-06-28: ERA5 via Open-Meteo, or the T05 table if it is ready. Variables are listed in T05.
  - `pmwc` for the simple model: compute a quick pilot version from the Landsat scene `LC09_L2SP_187024_20260625_02_T1` (see T05), or set 0 and note it.

## SOLWEIG options

Each needs a short justification in the report:
- **UMEP-processing code:** the SOLWEIG Python functions without QGIS.
- **SOLWEIG-GPU:** pip package; GPU recommended. A GPU VM may be created in `hackaton-honory`; a GPU quota increase may be needed.

Keep the configuration at defaults (standing person, isotropic sky, default tree transmissivity and surface parameters) unless the report explains a change.

## Expected output

- **SOLWEIG outputs:** shadow and Tmrt per time step for the pilot (COG), plus UTCI computed with `pythermalcomfort.models.utci`.
- **Simple-model outputs:** the same for `compute_inputs_for_utci`, with shade zone shares (`f_sun`, `f_bldg`, `f_tree`, `f_both`) derived for the pilot.
  - Any quick method is fine, e.g. SOLWEIG's own building and vegetation shadows.
- **Comparison report:**
  - Tmrt and UTCI differences by shade zone and time of day.
  - Where the simple model fails (low sun, evening pavement heat, narrow canyons, under trees).
  - Fitted coefficients.
  - A recommendation (simple model, tuned simple model, or SOLWEIG district-wide), with runtime numbers to scale to the full ~37 Mpx grid.

## Constraints

- UTCI from `pythermalcomfort` returns NaN outside its validity range (Tmrt − Ta outside −30…+70 K, wind outside 0.5–17 m/s at 10 m). Report, don't silently clip.
- Python; keep the pilot reproducible (scripted inputs, fixed bbox).

## Done when

- Both models have run for all 33 steps on the same pilot.
- The comparison and recommendation are written up with figures.

## Out of scope

District-wide production (T07), routing.
