# T11: Validation, baselines and demo

**Spec:** §9, §13 · **Depends on:** T02 engine (baselines can start now), T09 (for the final comparison) · **Consumed by:** demo

## Goal

Show that heat-aware routing makes a difference, and that the shade behind it is believable:
- **Demo trips:** a handful of origin–destination pairs and times that tell the story.
- **Baselines:** compare our routes with the dose they would get from (a) ORS Shaded routes and (b) routing on building-only shade (no trees).
- **Checks:** the end-to-end validation points of spec §13 not covered by earlier tasks.

## Why it matters

The demo claim is "this route lowers your heat dose by X for Y extra minutes", and it needs a baseline to be credible. Building-only shade is what most existing tools (and the MIT data) use; it shows what LiDAR trees add.

## Data

| Input | Where | Notes |
|---|---|---|
| Shade engine | [`shade/`](../shade/README.md), T01 layers in `gs://hackaton-honory-heat/T01/output/` | Building-only shade = run the engine with canopy disabled (or read `B = f_bldg + f_both` from T02's shares) |
| ORS Shaded | shaded.openrouteservice.org (HeiGIT) | Walk-only shade-aware routing. Check what shade data it uses for Warsaw, access and rate limits early |
| Routes and heat | T09 router, `hackaton-honory.warsaw_heat.t08_*` | Dose of any path = sum over its segments at entry time |
| Prior art notes | `analysis/prior_art/report.html` (workspace, not in repo) | ORS Shaded, CoolPath, ShadeMe |

## Expected output

- **Demo set:** 3–6 trips with origin, destination and time, each with a one-line reason it is in the demo. Include a midday case and a low-sun evening case, and at least one with an AC transit leg.
- **Comparison table per trip:** our options vs ORS Shaded route vs building-only-shade route, all priced on the T07/T08 field: dose, time, peak UTCI, shade share.
- **Report** in `reports/T11/` with maps of the compared routes. It covers the dose-weight question (spec §7) with numbers: how the ranking changes if the weights change.

## Constraints and things to handle

- Price every baseline route on **our** heat field, so the comparison is like for like.
- ORS Shaded is an external service: cache its answers, don't hammer it.
- **Optional:** on-site photo check of low-sun shadows on a sunny evening (open issue in [T02](../reports/T02/README.md)).

## Done when

- The demo trips are fixed and each has a comparison table.
- The headline numbers (dose saved, time cost) are in the report and ready for the pitch.

## Out of scope

Changing the models; app work (T10).
