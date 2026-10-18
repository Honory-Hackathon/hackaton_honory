# T13: Visualisations

**Spec:** §10 · **Depends on:** T08, T12 (build now on partial layers and the T06 pilot) · **Consumed by:** demo, T10

## Goal

Build the demo visualisations as web pages, served by the T10 app or next to it:
1. **Heat map of walking routes** through the day (time slider), with cool and water points.
2. **3D view of shadows** moving over buildings and trees from 06:00 to 22:00 as the sun moves, with the cloud cover of each step.
3. **Temperature and albedo through the day:** how air temperature, Tmrt/UTCI and surface reflectance and heat change, on maps and in a chart per surface type.

The planner demo with transit (spec §10, view 2) is the T10 app itself.

## Why it matters

The demo is judged on what people see. Each view answers one question:
- where is it bearable;
- why does shade move;
- what makes a surface hot.

## Data

| View | Input |
|---|---|
| Route heat map | `hackaton-honory.warsaw_heat.t08_segment_heat` + `t03_walk_segments` geometry ([T08](T08_segment_stop_heat.md)); cool and water points `T12/` |
| 3D shadows | `T12/` 3D scene assets |
| Temperature and albedo | `T12/` layers and series; forcing `warsaw_heat.t05_forcing_20260628` |

## Expected output

- **Pages:** three, each loading in a normal browser on a laptop and a phone, with:
  - a time slider over the 33 steps;
  - a legend on the UTCI stress categories (26 / 32 / 38 / 46 °C);
  - one-line captions in Polish and English.
- **Hosting:** deployed with or next to the T10 app on Cloud Run (`hackaton-honory`, europe-central2), with public URLs.
- **Code** in the repo; **screenshots and the URLs** in `reports/T13/`.

## Constraints

- Libraries from public CDNs or bundled.
- No backend computation at view time beyond serving files.

## Done when

All three pages are public and show the whole district for all 33 steps.

## Out of scope

New modelling; the planner (T09, T10).
