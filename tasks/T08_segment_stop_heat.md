# T08: Heat per walk segment and per stop

**Spec:** §7, §8, §9, §11 · **Depends on:** T03, T07 (develop on the T06 pilot UTCI now) · **Consumed by:** T09, T10

## Goal

Turn the UTCI field into the two tables the router prices:
- **Segments:** UTCI and heat-dose rate per walk segment and time step.
- **Stops:** UTCI and dose rate a person waiting at each tram/bus stop is exposed to, per time step.

## Why it matters

The router (T09) works on segments and stops, not pixels. How pixel values are summarised decides how the router sees a street: a sidewalk with one shady side has to come out cooler than its sunny neighbour.

## Data

| Input | Where | Notes |
|---|---|---|
| UTCI, Tmrt | `gs://hackaton-honory-heat/T07/` (format in [T07](T07_utci_field.md)) | Until T07 lands: `gs://hackaton-honory-heat/T06/cog/utci_tuned_era5.tif` and `utci_solweig.tif` on the pilot (E 637300–638500, N 485100–485900) |
| Segments, pixels | BigQuery `hackaton-honory.warsaw_heat.t03_walk_segments`, `t03_segment_pixels`; or `gs://hackaton-honory-heat/T03/network/` | `segment_pixels`: `segment_id`, `row`, `col` (row 0 = northern edge y = 492000). A pixel can belong to several segments. `indoor` segments have no pixels. Schema: [`walk/README.md`](../walk/README.md) |
| Stop waiting areas | `t03_stop_areas`, `gs://hackaton-honory-heat/T03/stops/stop_areas.parquet` | 360 stops, `shelter` flag (OSM), fixed-size rectangles where no platform polygon is mapped |
| Shade shares | `gs://hackaton-honory-heat/T02/` | For "share walking in shade" per segment |

**Dose rate:** the working definition is g(U) = max(0, U − 26) + max(0, U − 32) + 2·max(0, U − 38), in K. Dose = ∫ g dt. It is used in T06 (`solweig_ref/analyze.py`); the weights are still open (spec §7) and should stay a parameter.

## Expected output

BigQuery tables in `hackaton-honory.warsaw_heat`, plus Parquet in `gs://hackaton-honory-heat/T08/`:
- **`t08_segment_heat`:** `segment_id`, time step (UTC + local label), summary UTCI (e.g. mean and a high percentile), dose rate g, shade share. Joinable to `t03_walk_segments` for `length_m`, so the router computes traversal dose = g × length / speed.
- **`t08_stop_heat`:** `stop_id`, time step, UTCI and g of the waiting spot.
- **README and report** in `reports/T08/`: the summaries chosen and why, maps of a few streets at 13:00 and 18:00, and the rules for special cases.

## Constraints and things to handle

- **Waiting spot:** spec §8 charges waiting at "the best spot near the stop (shade, shelter)". Decide what "near" means and how shelters count. OSM `shelter=yes` is known, but small bus shelters are probably not in the LiDAR buildings.
- **Indoor and underground segments** (`indoor = true`, no pixels) need a rule, e.g. a constant indoor UTCI.
- **Covered segments** (arcades, passages) are in the DSM as roof, so they are already shaded. Check that they come out shaded.
- **Night steps** (21:00–22:00) must still have values.
- Keep `segment_id` and `stop_id` exactly as in T03 and T04; the router joins on them.

## Done when

- Every outdoor segment on component 0 and every stop has a value for all 33 steps.
- The tables are in BigQuery and in the bucket.
- The report shows that shady and sunny sides of a few known streets differ as expected (e.g. Marszałkowska, Aleje Ujazdowskie).

## Out of scope

Routing (T09), time interpolation between steps (the router's job), changes to the UTCI model (T07).
