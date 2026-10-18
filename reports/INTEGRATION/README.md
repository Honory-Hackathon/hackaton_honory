# Integration report (T07–T13 on the final field)

**Status (2026-10-18 03:45 UTC):** done. Every user-facing piece runs on the SOLWEIG district field: T07 UTCI →
T08 `t08_*` → T09 router → T10 `heat-app` and T13 `heat-views`. T11's comparison is on the same field.

| What | Where |
|---|---|
| App (map + trip planner) | https://heat-app-269831067246.europe-central2.run.app |
| Route heat map | https://heat-views-269831067246.europe-central2.run.app/routes/ |
| 3D shadows | https://heat-views-269831067246.europe-central2.run.app/shadows3d/ |
| Temperature and albedo | https://heat-views-269831067246.europe-central2.run.app/temperature/ |
| Router reference API | https://t09-router-269831067246.europe-central2.run.app/api/health |

## Demo claim

Plac Zbawiciela → Uniwersytet (Krakowskie Przedmieście), 2.5 km, at midday on 2026-06-28 (window 12:30–14:30):

- **Coolest:** leave 13:21, tram 4 + AC bus 111, 30.6 min, **8,283 K·s**, 77 % of walking in shade.
- **Fastest:** leave 14:01, tram 18 + bus 175, 21.7 min, 15,916 K·s.
- **Walking the whole way:** 43.8 min, 55,983 K·s, peak UTCI 42 °C (very strong heat stress).

So the coolest trip takes **48 % less heat dose than the fastest for 8.9 more minutes**, and 85 % less than walking.
With an open 08:00–18:00 window (Plac Zamkowy → Plac Zbawiciela), leaving at 08:08 on tram 4 costs 87 % less dose
than the fastest trip at 10:17, for 3.9 more minutes. All five demo trips are in
[reports/T10](../T10/README.md#on-the-final-field) (live answers) and [reports/T11](../T11/README.md) (against ORS
Shaded and building-only shade).

## Steps

| Step | State | What happened |
|---|---|---|
| 1. T07 / T08 final | already done | `T07/_SUCCESS` 02:46 UTC, `T08/_SUCCESS` 03:10 UTC (source t07). `t08_segment_heat` 1,937,595 rows = 58,715 segments × 33, 0 NULL; `t08_stop_heat` 11,880 rows = 360 stops × 33. |
| 2. T09 on final | already done by T09 | `reports/T09` final-field table (4 test pairs × 8 windows); `t09-router` serves `HEAT_SOURCE=final`. Integration fixed `price_path` (below) and redeployed `t09-router`. |
| 3. T10 | **done here** | Frames `t07_utci` / `t07_shade` were already rendered from the final T07 rasters (all 33 step stats written after the rasters' upload). heat-app was still on `HEAT_SOURCE=placeholder`: rebuilt with the current `router/` (final image `heat-app:int3`, also tagged `:latest`) and redeployed with `HEAT_SOURCE=final`, 4 GiB, CPU always allocated (router warm-up 60–100 s → 3.8 s). T13 links were already set. `/api/plan` smoke test on all five demo trips: router `final`, no notes, 0.6–0.9 s (3.8 s for 10 h). |
| 4. T13 | already done | heat-views rebuilt by T13 at 03:14 UTC on the final T08 tables (badge "Dane: T08 (SOLWEIG, końcowe)"). All three pages answer 200. |
| 5. T11 | already done | `bash demo/run_final.sh` on the final field at 03:15 UTC; headline in `reports/T11/headline_final.md`. Not rerun: the `price_path` fix leaves the dense ORS lines T11 prices unchanged (checked on three cached ORS answers). |
| 6. Verify gaps | partly | See below. |

## Gaps from the verify steps

| Gap | Action |
|---|---|
| T09 `price_path` inflates coarse lines (verify: 3-vertex line 71.5 min vs 44 min walk-only) | **Fixed** in `router/core.py`: a chord > 150 m whose snapped samples take > 1.3× the shortest walk between its ends is replaced by that walk; Dijkstra limit scales with the gap. Zbawiciela → Uniwersytet as a 2-point line 62.3 → 43.8 min (= walk-only plan), as a 3-point line 62.5 → 48.0 min; dense lines unchanged. Deployed to heat-app and t09-router. |
| T10 router on placeholder heat | **Fixed** (heat-app revision 00014, `HEAT_SOURCE=final`). |
| T08 `stub/t08_segment_heat.parquet` has the same md5 as the final | Not a defect: T08 rewrote the stub at 02:40 from all 27 T06 tiles, and T07 copies those tile values unchanged (T07 spot check: max \|Δ\| = 0). T09's stub and final runs on segments are therefore the same field. |
| T07: 909 walkable pixels outside the tiles (outside the AOI); 20:00–20:30 far shadows beyond 1 km; T01 DTM step at E 638000 | Left as is: user decisions (T07 `needs_user`). T08 fills the 15 affected segments from graph neighbours. |
| T08 special-case rules (indoor 26 °C, shelter = local shade, under-roof cap) | Left for the user to review against spec §8. |
| Router: custom dose weights recompute g from mean UTCI, although T08 carries `exc26/32/38/46` that would give the pixel-mean g exactly | **Fixed** in `router/heat.py`: thresholds among 26/32/38/46 use Σ w·exc (spec weights passed explicitly now give the default answer, 8,282.8 vs 8,282.9 K·s; before 8,254.4). Other thresholds still use mean UTCI, with a note. T11's weight study uses its own pricer and is unaffected. |
| T12: city water points need an API token | Not done. |

## Cloud changes

- Cloud Build images `heat-app:int1`, `:int2` (superseded), `heat-app:int3` (= `:latest`), `t09-router:latest` (rebuilt twice).
- Cloud Run `heat-app` revision `heat-app-00016-99b`: image `:int3`, `HEAT_SOURCE=final`, memory 2 → 4 GiB, `--no-cpu-throttling`.
- Cloud Run `t09-router` revision `t09-router-00008-r2c`: both router fixes, `--no-cpu-throttling` (load 3.7 s).
