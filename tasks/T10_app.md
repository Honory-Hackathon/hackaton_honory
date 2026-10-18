# T10: App

**Spec:** §1, §7, §11 · **Depends on:** T07, T09 (build now on the T06 pilot rasters and a stub router) · **Consumed by:** T11 (demo)

## Goal

A small web app for the demo. It has:
- **A map with a time slider** showing UTCI (and shade) over Śródmieście for the 33 steps of 2026-06-28.
- **A trip planner:** pick origin, destination and time window; see the route options as cards (*coolest*, *balanced*, *fastest*, walk-only) and on the map.

## Why it matters

The hackathon is judged on what people see. The story is the pensioner who plans in space and time, and the time slider makes the moving shade visible.

## Data

| Input | Where | Notes |
|---|---|---|
| UTCI rasters | `gs://hackaton-honory-heat/T07/` | 1 m, EPSG:2180, 33 bands, walkable pixels only. Until T07 lands: `gs://hackaton-honory-heat/T06/cog/utci_*.tif` (pilot) |
| Shade shares | `gs://hackaton-honory-heat/T02/` | uint8 / 250, 255 = night |
| Router | T09 function or endpoint | Option fields listed in [T09](T09_router.md) |
| Segment and stop heat | `hackaton-honory.warsaw_heat.t08_*` | For hover details |

## Expected output

- **The app deployed** on Cloud Run in `hackaton-honory` (europe-central2), with a public URL for the demo.
- **Code** in the repo, with a README on how to build and deploy.
- **Short report** in `reports/T10/` with screenshots.

## Constraints and things to handle

- **Web maps need EPSG:3857/4326:** the rasters are EPSG:2180 at 1 m. Decide whether to pre-render tiles or serve COGs dynamically, given 72 Mpx × 33 steps.
- **Colour scale on the UTCI stress categories** (26 / 32 / 38 / 46 °C), with a legend.
- **Route cards:** dose, total time, peak UTCI, minutes per category, share in shade, departure time.
- **Phone width works:** demo viewers may open it on a phone.
- **Keep it a demo:** no accounts, no persistence beyond what the demo needs.

## Done when

- The public URL shows the time-slider map for the whole district and plans at least the demo trips (T11) with live router answers.

## Out of scope

New modelling, data processing beyond formatting for the web.
